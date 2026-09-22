"""生成质量评测（P6 在接入真实 LLM 后才能做的部分）。

**这个脚本回答的问题是：** 「系统真的能生成可用内容吗？」
在此之前的全部测试都走 Mock 或知识库降级路径 —— 它们验证的是
**结构正确性与降级路径**，而不是**生成质量**。

**只做可自动判定的检查。** Usefulness / Comprehensibility 等主观维度
仍需人工评分（见 README 的诚实清单）；本脚本的作用是把
「结构合规」「接地性」「术语通俗度」「数值约束」「延迟」这些
原本也常被归入「人工看看」的维度自动化。

用法::

    python knowledge/eval/run_generation_eval.py                # 全量
    python knowledge/eval/run_generation_eval.py --limit 3      # 快速冒烟
    python knowledge/eval/run_generation_eval.py --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.brief import CreationInput, SelectionInput  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.providers.deepseek import DeepSeekProvider  # noqa: E402
from app.services.advisor import generate_advice  # noqa: E402
from app.services.analyzer import build_artifact  # noqa: E402
from app.services.generation.grounding import summarize  # noqa: E402
from app.services.layman import find_banned_terms  # noqa: E402
from app.services.starter import generate_starter_plan, validate_plan  # noqa: E402

DATASET = PROJECT_ROOT / "datasets" / "advice_eval" / "generation_v1.json"
REPORTS_DIR = PROJECT_ROOT / "reports"

#: MVP 验收：单次端到端 ≤ 60 s（``MVP计划.md`` §9 第 8 条）
LATENCY_BUDGET_S = 60.0


@dataclass
class CaseResult:
    """单题结果。"""

    case_id: str
    kind: str
    passed: bool
    checks: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    latency_s: float = 0.0
    degradation: list[str] = field(default_factory=list)
    sample_output: str = ""


def _check_starter(case: dict, result, latency: float) -> CaseResult:
    """零基础链路的结构与约束检查。"""
    plan = result.plan
    failures: list[str] = []
    checks: dict = {}

    # --- 结构合规（§3.3.3）---
    checks["has_key"] = bool(plan.key)
    checks["has_key_explanation"] = bool(plan.key_explanation.strip())
    checks["has_tempo_explanation"] = bool(plan.tempo_explanation.strip())
    checks["section_count"] = len(plan.sections)
    checks["all_sections_explained"] = all(s.explanation.strip() for s in plan.sections)

    if not checks["has_key"]:
        failures.append("缺少调性")
    if not checks["has_key_explanation"]:
        failures.append("缺少调性解释")
    if not checks["has_tempo_explanation"]:
        failures.append("缺少速度解释")
    expected_sections = case.get("expect_sections_at_least", 2)
    if checks["section_count"] < expected_sections:
        failures.append(f"段落数 {checks['section_count']} < {expected_sections}")
    if not checks["all_sections_explained"]:
        failures.append("存在段落缺少通俗解释")

    # --- 数值约束：用户选定的 BPM 必须被遵守 ---
    if "expect_tempo" in case:
        checks["tempo"] = plan.tempo
        if plan.tempo != case["expect_tempo"]:
            failures.append(f"速度 {plan.tempo} != 用户选定的 {case['expect_tempo']}")

    # --- 情绪识别 ---
    intent_emotions = result.trace.intent.emotion if result.trace.intent else []
    checks["emotions"] = intent_emotions
    if "expect_emotions" in case:
        missing = [e for e in case["expect_emotions"] if e not in intent_emotions]
        if missing:
            failures.append(f"缺少预期情绪：{missing}")
    if "expect_emotions_any" in case:
        if not any(e in intent_emotions for e in case["expect_emotions_any"]):
            failures.append(f"未命中任一预期情绪：{case['expect_emotions_any']}")

    # --- 风格识别 ---
    if "expect_style" in case:
        style = result.trace.intent.style if result.trace.intent else None
        checks["style"] = style
        if style != case["expect_style"]:
            failures.append(f"风格 {style} != 预期 {case['expect_style']}")

    # --- 术语通俗度（零基础档不得出现禁区术语）---
    level = "zero"
    texts = [
        plan.key_explanation,
        plan.tempo_explanation,
        plan.why,
        *[s.explanation for s in plan.sections],
    ]
    banned = [t for text in texts for t in find_banned_terms(text)]
    checks["banned_terms"] = banned
    if banned:
        failures.append(f"零基础输出含禁区术语：{banned}")

    # --- 证据接地 ---
    checks["evidence_count"] = len(plan.evidence)
    checks["evidence_with_source"] = all(e.source.title for e in plan.evidence)

    # --- 延迟 ---
    checks["latency_s"] = round(latency, 2)
    if latency > LATENCY_BUDGET_S:
        failures.append(f"延迟 {latency:.1f}s 超出 {LATENCY_BUDGET_S:.0f}s 预算")

    # --- 校验器（复用 P2 的实现）---
    validation = validate_plan(plan)
    checks["validation_issues"] = validation
    failures.extend(validation)

    sample = f"{plan.key} | {plan.tempo}BPM | " + " / ".join(
        f"{s.name}: {'→'.join(s.chords)}" for s in plan.sections
    )

    return CaseResult(
        case_id=case["id"],
        kind="starter",
        passed=not failures,
        checks=checks,
        failures=failures,
        latency_s=round(latency, 2),
        degradation=[d.kind for d in result.trace.degradation],
        sample_output=sample,
    )


def _check_advice(case: dict, result, latency: float) -> CaseResult:
    """进阶链路的结构、接地性与延迟检查。"""
    advice = result.advice
    failures: list[str] = []
    checks: dict = {}

    # --- 结构合规（§3.9.3）---
    checks["has_analysis"] = bool(advice.analysis.strip())
    checks["problem_count"] = len(advice.problems)
    checks["option_count"] = len(advice.options)
    checks["all_options_have_reason"] = all(o.reason.strip() for o in advice.options)
    checks["all_options_have_chords"] = all(o.chords for o in advice.options)
    checks["all_options_have_theory"] = all(o.theory for o in advice.options)

    minimum = case.get("expect_options_min", 2)
    maximum = case.get("expect_options_max", 3)
    if not checks["has_analysis"]:
        failures.append("缺少分析段")
    if not (minimum <= checks["option_count"] <= maximum):
        failures.append(f"方案数 {checks['option_count']} 不在 [{minimum}, {maximum}] 内")
    if not checks["all_options_have_reason"]:
        failures.append("存在方案缺少通俗解释")
    if not checks["all_options_have_chords"]:
        failures.append("存在方案缺少和弦")
    if not checks["all_options_have_theory"]:
        failures.append("存在方案缺少理论依据")

    # --- 接地性（Grounding，§7.2）---
    summary = summarize(result.trace.grounding)
    checks["grounding"] = summary
    checks["grounding_ok"] = result.trace.grounding.ok
    if summary.get("citations_fabricated", 0) > 0:
        failures.append(f"编造引用 {summary['citations_fabricated']} 条")
    if summary.get("chords_out_of_key", 0) > 0:
        failures.append(f"调外和弦 {summary['chords_out_of_key']} 个")

    # --- 零基础档的术语守卫 ---
    level = case.get("user_level", "some")
    checks["user_level"] = level
    if level == "zero":
        texts = [advice.analysis, *advice.problems]
        texts.extend(o.feature for o in advice.options)
        texts.extend(o.reason for o in advice.options)
        banned = [t for text in texts for t in find_banned_terms(text)]
        checks["banned_terms"] = banned
        if banned:
            failures.append(f"零基础输出含禁区术语：{banned}")

    # --- 延迟 ---
    checks["latency_s"] = round(latency, 2)
    if latency > LATENCY_BUDGET_S:
        failures.append(f"延迟 {latency:.1f}s 超出 {LATENCY_BUDGET_S:.0f}s 预算")

    sample = f"{advice.analysis[:40]}… | " + " ; ".join(
        f"{o.label}: {'→'.join(o.chords)}" for o in advice.options
    )

    return CaseResult(
        case_id=case["id"],
        kind="advice",
        passed=not failures,
        checks=checks,
        failures=failures,
        latency_s=round(latency, 2),
        degradation=[d.kind for d in result.trace.degradation],
        sample_output=sample,
    )


def run(*, limit: int | None = None, llm=None) -> list[CaseResult]:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    provider = llm if llm is not None else DeepSeekProvider()

    results: list[CaseResult] = []

    starter_cases = dataset["starter_cases"][: limit or None]
    for case in starter_cases:
        payload = CreationInput(
            mode="starter",
            raw_text=case.get("text", ""),
            selections=SelectionInput(
                emotion=case.get("emotion"),
                style=case.get("style"),
                tempo_label=case.get("tempo_label"),
            ),
            user_level=case.get("user_level", "zero"),
        )
        started = time.perf_counter()
        try:
            result = generate_starter_plan(payload, llm=provider)
            latency = time.perf_counter() - started
            results.append(_check_starter(case, result, latency))
        except Exception as exc:  # noqa: BLE001
            results.append(
                CaseResult(
                    case_id=case["id"],
                    kind="starter",
                    passed=False,
                    failures=[f"调用异常：{type(exc).__name__}: {exc}"],
                )
            )

    advice_cases = dataset["advice_cases"][: limit or None]
    for case in advice_cases:
        artifact = build_artifact(
            chord_text=case.get("chords", ""),
            melody_text=case.get("melody", ""),
            key=case.get("key"),
        )
        payload = CreationInput(
            mode="tutor",
            raw_text=case.get("goal", ""),
            artifact=artifact,
            user_level=case.get("user_level", "some"),
        )
        started = time.perf_counter()
        try:
            result = generate_advice(payload, llm=provider)
            latency = time.perf_counter() - started
            results.append(_check_advice(case, result, latency))
        except Exception as exc:  # noqa: BLE001
            results.append(
                CaseResult(
                    case_id=case["id"],
                    kind="advice",
                    passed=False,
                    failures=[f"调用异常：{type(exc).__name__}: {exc}"],
                )
            )

    return results


def render(results: list[CaseResult]) -> str:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    latencies = [r.latency_s for r in results if r.latency_s > 0]

    lines = [
        "# MuseGuide 生成质量评测",
        "",
        "> 由 `knowledge/eval/run_generation_eval.py` 自动生成，无手工誊写。",
        f"> 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "> **本次评测使用真实 LLM**，非 Mock 或知识库降级路径。",
        "",
        "## 总体结果",
        "",
        f"- 用例数：**{total}**",
        f"- 通过：**{passed}**（{passed / total:.1%}）"
        if total
        else "- 无用例",
    ]

    if latencies:
        lines.extend(
            [
                f"- 端到端延迟：中位数 **{statistics.median(latencies):.1f}s**、"
                f"最大 **{max(latencies):.1f}s**、最小 **{min(latencies):.1f}s**",
                f"- 延迟预算：{LATENCY_BUDGET_S:.0f}s（`MVP计划.md` §9 第 8 条）"
                f" → {'✅ 全部达标' if max(latencies) <= LATENCY_BUDGET_S else '❌ 存在超时'}",
            ]
        )

    lines.extend(["", "## 逐题结果", "", "| 用例 | 类型 | 结果 | 延迟 | 失败项 |", "|---|---|---|---|---|"])
    for r in results:
        mark = "✅" if r.passed else "❌"
        reasons = "；".join(r.failures) if r.failures else "—"
        lines.append(f"| `{r.case_id}` | {r.kind} | {mark} | {r.latency_s:.1f}s | {reasons} |")

    # 分维度统计
    degraded = [r for r in results if r.degradation]
    lines.extend(["", "## 降级情况", ""])
    if degraded:
        lines.append(f"- {len(degraded)} 个用例触发了降级：")
        for r in degraded:
            lines.append(f"  - `{r.case_id}`：{', '.join(r.degradation)}")
    else:
        lines.append("- **无降级**：所有用例都走通了真实 LLM 路径。")

    lines.extend(["", "## 样例输出", ""])
    for r in results[:6]:
        lines.append(f"- `{r.case_id}`：{r.sample_output}")

    lines.extend(
        [
            "",
            "## 局限",
            "",
            "- 本评测只覆盖**可机器判定的维度**（结构合规、接地性、术语通俗度、"
            "数值约束、延迟）。",
            "- `MVP计划.md` §7.2 的 Theory Validity / Usefulness / Comprehensibility / "
            "Actionability 等维度**仍需要人工评分**，本次未做。",
            "- 用例数有限，不能替代真实用户测试。",
            "",
            "---",
            "",
            "*本报告由 `knowledge/eval/run_generation_eval.py` 自动生成。*",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MuseGuide 生成质量评测")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题（冒烟）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.llm_available:
        print("❌ 未配置 DEEPSEEK_API_KEY，无法评测生成质量。")
        print("   Mock / 知识库降级路径的测试请运行 pytest。")
        return 2

    print(f"使用模型：{settings.llm_model}")
    print("开始评测（每题一次真实 API 调用，请耐心等待）…")
    print()

    results = run(limit=args.limit)

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "case_id": r.case_id,
                        "kind": r.kind,
                        "passed": r.passed,
                        "failures": r.failures,
                        "latency_s": r.latency_s,
                        "checks": r.checks,
                    }
                    for r in results
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    for r in results:
        mark = "✅" if r.passed else "❌"
        print(f"{mark} {r.case_id:20} {r.latency_s:6.1f}s  {r.sample_output[:60]}")
        for failure in r.failures:
            print(f"      ↳ {failure}")

    print()
    print(f"通过：{passed}/{total}（{passed / total:.1%}）" if total else "无用例")
    latencies = [r.latency_s for r in results if r.latency_s > 0]
    if latencies:
        print(
            f"延迟：中位数 {statistics.median(latencies):.1f}s，"
            f"最大 {max(latencies):.1f}s（预算 {LATENCY_BUDGET_S:.0f}s）"
        )

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / "GENERATION_EVAL.md"
    path.write_text(render(results), encoding="utf-8")
    print(f"报告：{path.relative_to(PROJECT_ROOT)}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
