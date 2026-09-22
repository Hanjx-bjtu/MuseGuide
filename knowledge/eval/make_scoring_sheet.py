"""导出人工评分材料（P6 的 45 个人工评分用例）。

**为什么需要这个脚本：**
自动评测只记录「通过/不通过」，但人工评分需要看到**完整输出内容** ——
评「这条建议有没有用」必须读到建议原文，而不是一个 ✅。

本脚本跑一遍真实链路，把每次生成的完整内容落成两类文件：

* ``samples.md`` —— 人读的：每题一屏，含输入与完整输出
* ``scores.csv`` —— 人填的：每行一题，评分列留空

用法::

    python knowledge/eval/make_scoring_sheet.py               # 全量
    python knowledge/eval/make_scoring_sheet.py --limit 3     # 冒烟
    python knowledge/eval/make_scoring_sheet.py --seed 42     # 固定随机性说明
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.brief import CreationInput, CreativeGoal, SelectionInput  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.providers.deepseek import DeepSeekProvider  # noqa: E402
from app.services.advisor import generate_advice  # noqa: E402
from app.services.analyzer import build_artifact  # noqa: E402
from app.services.generation.grounding import summarize  # noqa: E402
from app.services.starter import generate_starter_plan  # noqa: E402

DATASET = PROJECT_ROOT / "datasets" / "advice_eval" / "generation_v1.json"
OUT_DIR = PROJECT_ROOT / "reports" / "scoring"

#: 人工评分维度（严格对齐 ``MVP计划.md`` §7.2 与 §19 的八维表）
DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("theory_validity", "理论上是否合理"),
    ("relevance", "是否符合用户问题"),
    ("diversity", "建议之间是否有差异（仅多方案题）"),
    ("explainability", "是否解释了原因"),
    ("groundedness", "是否有知识依据"),
    ("usefulness", "对创作者是否有帮助"),
    ("comprehensibility", "零基础用户能否看懂"),
    ("actionability", "用户能否据此行动"),
)

#: 仅对「多方案」的用例评 diversity
_DIVERSITY_KINDS = {"advice"}


def _ensure_key() -> None:
    """确保 API Key 可见（Machine 级变量不会被已运行的进程继承）。"""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return
    try:
        result = subprocess.run(
            [
                "powershell",
                "-Command",
                "[Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','Machine')",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        key = result.stdout.strip()
        if key:
            os.environ["DEEPSEEK_API_KEY"] = key
    except Exception:  # noqa: BLE001 - 取不到就交给调用方报错
        pass


def _render_starter(case: dict, result, latency: float) -> dict:
    plan = result.plan
    lines = [
        f"### {case['id']} —— {case.get('note', '')}",
        "",
        "**用户输入**",
        "",
        "```text",
        f"意图：{case.get('text') or '（未填写，仅做了选择）'}",
    ]
    if case.get("emotion") or case.get("style") or case.get("tempo_label"):
        lines.append(
            f"选择：情绪={case.get('emotion', '—')} 风格={case.get('style', '—')} "
            f"速度={case.get('tempo_label', '—')}"
        )
    lines.extend(["```", "", "**系统输出**", ""])
    lines.append(f"- **调性**：{plan.key}")
    lines.append(f"  - {plan.key_explanation}")
    lines.append(f"- **速度**：{plan.tempo} BPM")
    lines.append(f"  - {plan.tempo_explanation}")
    for section in plan.sections:
        lines.append(f"- **{section.name}**（{section.emotion}）")
        lines.append(f"  - 和弦：{' → '.join(section.chords)}")
        lines.append(f"  - {section.explanation}")
    if plan.why:
        lines.extend(["", f"**为什么这样选**：{plan.why}"])
    if plan.adjust_hints:
        lines.extend(["", "**如果你想调整**："])
        lines.extend(f"- {hint}" for hint in plan.adjust_hints)
    if plan.evidence:
        lines.extend(["", "**理论依据**："])
        for item in plan.evidence:
            lines.append(f"- [{item.entry_id}] {item.layman_title or item.title}")

    return {
        "case_id": case["id"],
        "kind": "starter",
        "input": case.get("text") or "（仅选择式输入）",
        "latency_s": round(latency, 1),
        "markdown": "\n".join(lines),
        "degradation": [d.kind for d in result.trace.degradation],
    }


def _render_advice(case: dict, result, latency: float) -> dict:
    advice = result.advice
    lines = [
        f"### {case['id']} —— {case.get('note', '')}",
        "",
        "**用户输入**",
        "",
        "```text",
        f"创作目标：{case.get('goal', '')}",
        f"和弦进行：{case.get('chords', '')}",
    ]
    if case.get("melody"):
        lines.append(f"旋律：{case['melody']}")
    if case.get("constraints"):
        lines.append(f"要求保持：{'、'.join(case['constraints'])}")
    lines.append(f"用户水平：{case.get('user_level', 'some')}")
    lines.extend(["```", "", "**系统输出**", ""])
    lines.append(f"**分析**：{advice.analysis}")
    if advice.problems:
        lines.append("")
        lines.append("**问题**：")
        lines.extend(f"- {problem}" for problem in advice.problems)

    for option in advice.options:
        lines.extend(
            [
                "",
                f"**{option.label}**",
                "",
                f"- 和弦：`{' | '.join(option.chords)}`",
                f"- 特点：{option.feature}",
                f"- 解释：{option.reason}",
            ]
        )
        if option.theory:
            lines.append(f"- 依据：{'；'.join(option.theory)}")

    summary = summarize(result.trace.grounding)
    lines.extend(
        [
            "",
            "**机器校验**："
            f"编造引用 {summary['citations_fabricated']} 条、"
            f"调外和弦 {summary['chords_out_of_key']} 个",
        ]
    )

    return {
        "case_id": case["id"],
        "kind": "advice",
        "input": f"{case.get('goal', '')} / {case.get('chords', '')}",
        "latency_s": round(latency, 1),
        "markdown": "\n".join(lines),
        "degradation": [d.kind for d in result.trace.degradation],
    }


def run(*, limit: int | None = None) -> list[dict]:
    _ensure_key()
    settings = get_settings()
    if not settings.llm_available:
        raise SystemExit(
            "❌ 未配置 DEEPSEEK_API_KEY，无法生成评分材料。\n"
            "   （人工评分必须基于真实模型的输出）"
        )

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    llm = DeepSeekProvider()
    samples: list[dict] = []

    starters = dataset["starter_cases"][: limit or None]
    for case in starters:
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
        result = generate_starter_plan(payload, llm=llm)
        samples.append(_render_starter(case, result, time.perf_counter() - started))
        print(f"  已生成 {case['id']}")

    advices = dataset["advice_cases"][: limit or None]
    for case in advices:
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
        result = generate_advice(payload, llm=llm)
        samples.append(_render_advice(case, result, time.perf_counter() - started))
        print(f"  已生成 {case['id']}")

    return samples


def write_samples(samples: list[dict]) -> Path:
    """写出人读的评分材料。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    header = [
        "# MuseGuide 人工评分材料",
        "",
        f"> 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "> 模型：真实 DeepSeek（非 Mock / 降级路径）",
        f"> 共 {len(samples)} 题。**请对照 `scores.csv` 逐题打分。**",
        "",
        "## 评分方法",
        "",
        "每题按 1~5 分评分（判据见 `docs/SCORING_GUIDE.md`）：",
        "",
        f"| 维度 | 含义 | 适用 |",
        "|---|---|---|",
    ]
    for key, meaning in DIMENSIONS:
        applies = "全部" if key != "diversity" else "仅多方案题"
        header.append(f"| `{key}` | {meaning} | {applies} |")

    header.extend(
        [
            "",
            "> **不懂乐理也能评。** 大部分维度评的是「读起来是否清楚、是否回答了问题、",
            "> 你是否愿意照着做」，这些不需要专业知识。",
            "> 只有 `theory_validity`（理论上是否合理）需要乐理判断 —— 拿不准就留空，",
            "> **留空比乱填更有价值**。",
            "",
            "---",
            "",
        ]
    )

    body = "\n\n---\n\n".join(sample["markdown"] for sample in samples)
    path = OUT_DIR / "samples.md"
    path.write_text("\n".join(header) + body + "\n", encoding="utf-8")
    return path


def write_scores_csv(samples: list[dict]) -> Path:
    """写出待填的评分表。

    评分列一律留空 —— **不预填任何默认值**，否则会诱导评分者直接采用。
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "scores.csv"

    fieldnames = ["case_id", "kind", "input", "latency_s"]
    fieldnames += [key for key, _ in DIMENSIONS]
    fieldnames += ["comment"]

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for sample in samples:
            row = {
                "case_id": sample["case_id"],
                "kind": sample["kind"],
                "input": sample["input"],
                "latency_s": sample["latency_s"],
            }
            for key, _ in DIMENSIONS:
                # 不适用 diversity 的单方案题留空
                row[key] = ""
            row["comment"] = ""
            writer.writerow(row)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成人工评分材料")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题")
    args = parser.parse_args(argv)

    print("开始生成评分材料（每题一次真实 API 调用）…")
    samples = run(limit=args.limit)

    samples_path = write_samples(samples)
    scores_path = write_scores_csv(samples)

    print()
    print(f"✅ 评分材料：{samples_path.relative_to(PROJECT_ROOT)}")
    print(f"✅ 评分表：  {scores_path.relative_to(PROJECT_ROOT)}")
    print()
    print("下一步：")
    print("  1. 打开 reports/scoring/samples.md 通读全部输出")
    print("  2. 在 reports/scoring/scores.csv 里逐题打 1~5 分（留空表示无法判断）")
    print("  3. 运行 python knowledge/eval/summarize_scores.py 出报告")
    return 0


if __name__ == "__main__":
    sys.exit(main())
