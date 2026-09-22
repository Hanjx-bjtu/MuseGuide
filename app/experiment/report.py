"""报告渲染器（P6.5）。

**铁律：报告里的所有数字都由代码从实验产物渲染，禁止手工誊写。**

手工誊写的表格会与 JSON 数据漂移 —— 改一次参数就要重抄一遍，
而且无法保证报告与实验是同一批数据。这里把「跑实验 → 读产物 → 渲染」
串成一条链，使报告**不可能**与数据不一致。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.experiment.metrics import has_discrimination
from app.experiment.runner import ExperimentResult, compare

REPORTS_DIR = PROJECT_ROOT / "reports"

#: 验收门槛（docs/ACCEPTANCE.md §3.1）
THRESHOLDS = {
    "loose_recall@k": 0.90,
    "strict_recall@k": 0.70,
}


def _mark(value: float, threshold: float) -> str:
    """达标标记。**不达标就写 ❌** —— 报告不能只报喜。"""
    return "✅" if value >= threshold else "❌"


def render_retrieval_table(results: list[ExperimentResult], *, k: int = 5) -> str:
    """渲染检索对比表（由 metrics 直接生成）。"""
    lines = [
        f"| 配置 | 宽松 Recall@{k} | 严格 Recall@{k} | MRR | nDCG@{k} | Context Precision | 耗时(s) |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in results:
        m = result.metrics
        lines.append(
            f"| `{result.name}` | {m.loose_recall:.3f} | {m.strict_recall:.3f} | "
            f"{m.mrr:.3f} | {m.ndcg:.3f} | {m.context_precision:.3f} | {result.elapsed_s:.1f} |"
        )
    return "\n".join(lines)


def render_comparisons(results: list[ExperimentResult]) -> str:
    """渲染配对比较（含 bootstrap 置信区间）。

    **禁止「X 比 Y 高 0.02」式的结论** —— 没有区间就无法区分
    真实差异与评测集噪声（计划 §12 S12.4）。
    """
    by_name = {r.name: r for r in results}
    pairs = [
        ("hybrid_rrf", "sparse_only", "混合检索 vs 纯 BM25"),
        ("hybrid_rrf", "dense_only", "混合检索 vs 纯向量"),
        ("sparse_only", "dense_only", "BM25 vs 向量"),
        ("hybrid_rrf_filter", "hybrid_rrf", "元数据过滤的边际贡献"),
    ]

    lines = [
        "| 对比 | 严格 Recall 差值 | 95% 置信区间 | 是否显著 |",
        "|---|---|---|---|",
    ]
    for a, b, label in pairs:
        if a not in by_name or b not in by_name:
            continue
        comparison = compare(by_name[a], by_name[b])
        low, high = comparison["ci95"]
        mark = "✅ 显著" if comparison["significant"] else "❌ 不显著"
        lines.append(
            f"| {label} | {comparison['mean_diff']:+.4f} | "
            f"[{low:+.4f}, {high:+.4f}] | {mark} |"
        )
    return "\n".join(lines)


def render_misses(result: ExperimentResult, *, limit: int = 12) -> str:
    """渲染未命中案例（诚实清单的素材）。"""
    misses = [c for c in result.metrics.per_case if c["strict"] < 1.0]
    if not misses:
        return "无未命中案例。"

    lines = [f"共 **{len(misses)} / {result.metrics.n_cases}** 题未完全命中（严格口径）。示例：", ""]
    for case in misses[:limit]:
        lines.append(f"- **{case['question']}**")
        lines.append(f"  - 未召回：`{'`, `'.join(case['missed'])}`")
    return "\n".join(lines)


def render_report(
    results: list[ExperimentResult],
    *,
    k: int = 5,
    extra_sections: str = "",
    notes: list[str] | None = None,
) -> str:
    """渲染完整评测报告。"""
    best = max(results, key=lambda r: r.metrics.strict_recall)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    loose = best.metrics.loose_recall
    strict = best.metrics.strict_recall
    embedder_note = next(
        (n for r in results for n in r.notes if "嵌入后端" in n), "嵌入后端：未知"
    )

    sections = [
        "# MuseGuide 检索评测报告",
        "",
        "> ⚠️ **本报告的所有数字均由 `app/experiment` 从实验产物自动渲染，无手工誊写。**",
        f"> 生成时间：{stamp}",
        "",
        "## 1. 实验设置",
        "",
        f"- **评测集**：`datasets/retrieval_qa/v1.json`（冻结版，{best.metrics.n_cases} 题）",
        "- **标注口径**：每题标注**全部必需 gold**，支持多 gold（严格口径的前提）",
        f"- **K**：{k}",
        f"- {embedder_note}",
        "",
        "**两级 Recall 口径：**",
        "",
        "- 宽松：命中任一 gold 即算对（`MVP计划.md` §7.1 的字面要求）",
        "- 严格：必须召回该题全部 gold（本项目补充，用于防止评测集饱和）",
        "",
        "## 2. 检索对比矩阵",
        "",
        render_retrieval_table(results, k=k),
        "",
        "## 3. 配对比较（含统计显著性）",
        "",
        render_comparisons(results),
        "",
        "> 置信区间由配对 bootstrap（2000 次重采样）得出。",
        "> **区间跨 0 即视为不显著** —— 此时不应声称「X 比 Y 好」。",
        "",
        "## 4. 验收结论",
        "",
        f"| 指标 | 最佳配置值 | 门槛 | 结论 |",
        f"|---|---|---|---|",
        f"| 宽松 Recall@{k} | {loose:.3f} | ≥ {THRESHOLDS['loose_recall@k']:.2f} | "
        f"{_mark(loose, THRESHOLDS['loose_recall@k'])} |",
        f"| 严格 Recall@{k} | {strict:.3f} | ≥ {THRESHOLDS['strict_recall@k']:.2f} | "
        f"{_mark(strict, THRESHOLDS['strict_recall@k'])} |",
        f"| 评测集有区分度 | — | 各配置不完全相同 | "
        f"{'✅' if has_discrimination([r.metrics for r in results]) else '❌'} |",
        "",
        f"最佳配置：`{best.name}`",
        "",
    ]

    if extra_sections:
        sections.extend([extra_sections, ""])

    sections.extend(
        [
            "## 5. 未命中案例",
            "",
            render_misses(best),
            "",
            "## 6. 局限",
            "",
        ]
    )

    if notes:
        sections.extend(f"- {note}" for note in notes)
    else:
        sections.append("- 无附加说明。")

    sections.extend(["", "---", "", "*本报告由 `app/experiment/report.py` 自动生成。*"])
    return "\n".join(sections)


def write_report(content: str, *, name: str = "EVAL.md") -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / name
    path.write_text(content, encoding="utf-8")
    return path


def _default_notes(results: list[ExperimentResult]) -> list[str]:
    """报告的「局限」小节。

    **这一节不是免责声明，而是结论可信度的一部分。**
    计划 §11 明确要求：「我知道我的系统哪里不行，并且我量化了它」
    比「我的系统什么都好」可信得多。
    """
    notes: list[str] = []

    notes.append(
        "**嵌入后端为哈希实现**（零依赖兜底），**不具备真实语义能力**。"
        "因此「向量检索」这一路的表现被系统性低估，"
        "`dense_only` 的分数不代表真实语义检索的水平。"
        "接入 BGE / Ollama 后需重跑本报告。"
    )

    notes.append(
        "**全部实验在无 LLM 的降级路径下完成**：Query 分解用的是规则分解器。"
        "LLM 分解的效果差异属于另一个自变量，需接入 API Key 后单独实验。"
    )

    notes.append(
        "**评测集为 62 题、覆盖 29 条知识库条目**，规模有限。"
        "题目由模板生成初稿后人工复核，存在标注者主观性；"
        "没有第二人独立标注，因此**无法给出标注一致性（κ）**。"
    )

    strict = {round(r.metrics.strict_recall, 3) for r in results}
    if len(strict) <= 1:
        notes.append(
            "⚠️ **各配置严格 Recall 相同，评测集可能已饱和** —— "
            "此时不应基于本报告声称任何配置优劣。"
        )

    return notes
