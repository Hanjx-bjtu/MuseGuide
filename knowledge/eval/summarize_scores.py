"""汇总人工评分（P6.4 的人工评估部分）。

读取 ``reports/scoring/scores.csv``，产出 ``reports/HUMAN_SCORING.md``。

**三条设计原则：**

1. **留空就是留空。** 未评的项不计入均值，报告里明确写出「N 题未评」——
   不把空白当 0 分，也不假装数据齐全。
2. **样本太小就说样本太小。** 评了 3 题时均值是噪声，
   报告会直接提示「不足以支撑结论」。
3. **一致性不够就不拿它当结论。** 有两人评分时计算 Cohen's κ，
   κ < 0.6 的维度会被标注为「不适合用于结论」（``MVP计划.md`` §7.2 的要求）。

用法::

    python knowledge/eval/summarize_scores.py
    python knowledge/eval/summarize_scores.py --scores reports/scoring/scores_b.csv  # 第二人
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCORES_PATH = PROJECT_ROOT / "reports" / "scoring" / "scores.csv"
REPORT_PATH = PROJECT_ROOT / "reports" / "HUMAN_SCORING.md"

#: 八个维度（与 ``docs/SCORING_GUIDE.md`` 和 ACCEPTANCE.md §3.3 一致）
DIMENSIONS: tuple[tuple[str, str, float], ...] = (
    ("theory_validity", "理论上是否合理", 4.0),
    ("relevance", "是否符合用户问题", 4.0),
    ("diversity", "建议之间是否有差异", 3.5),
    ("explainability", "是否解释了原因", 4.0),
    ("groundedness", "是否有知识依据", 4.0),
    ("usefulness", "对创作者是否有帮助", 3.5),
    ("comprehensibility", "零基础用户能否看懂", 4.0),
    ("actionability", "用户能否据此行动", 3.5),
)

#: 低于此样本量时提示「不足以支撑结论」
MIN_SAMPLE = 5

#: κ 低于此值时该维度不进入结论（``MVP_main`` §7.2）
KAPPA_THRESHOLD = 0.6


def _parse_score(value: str) -> int | None:
    """解析一个评分单元格；空白或非法返回 ``None``（= 未评）。"""
    text = (value or "").strip()
    if not text:
        return None
    try:
        number = int(float(text))
    except ValueError:
        return None
    return number if 1 <= number <= 5 else None


def load_scores(path: Path) -> list[dict]:
    """读取评分表。

    用 ``utf-8-sig`` 而非 ``utf-8``：**Excel 与 PowerShell 保存 CSV 时会写入 BOM**，
    若按 utf-8 读取，第一个列名会变成 ``\\ufeffcase_id``，
    导致后续按列名取值全部失败（实测踩过：报 ``KeyError: 'case_id'``）。

    :raises SystemExit: 文件不存在或缺少必需列
    """
    if not path.is_file():
        raise SystemExit(
            f"❌ 找不到评分表：{path}\n"
            f"   请先运行：python knowledge/eval/make_scoring_sheet.py"
        )

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise SystemExit(f"❌ 评分表为空：{path}")

    # 逐列清理 BOM 与空白，避免 Excel 另存后列名带不可见字符
    cleaned: list[dict] = []
    for row in rows:
        cleaned.append({(k or "").lstrip("\ufeff").strip(): v for k, v in row.items()})

    missing = {"case_id"} - set(cleaned[0].keys())
    if missing:
        raise SystemExit(
            f"❌ 评分表缺少必需列：{missing}\n"
            f"   实际列：{sorted(cleaned[0].keys())}\n"
            f"   请重新运行 make_scoring_sheet.py 生成评分表。"
        )
    return cleaned


def cohens_kappa(a: list[int], b: list[int]) -> float:
    """Cohen's κ（加权用简单版本，适用于 1~5 的有序评分）。

    ``κ = (Po - Pe) / (1 - Pe)``，``Po`` 为观察一致率，``Pe`` 为随机一致率。
    """
    if not a or len(a) != len(b):
        return 0.0
    n = len(a)
    observed = sum(1 for x, y in zip(a, b) if x == y) / n

    labels = sorted(set(a) | set(b))
    expected = 0.0
    for label in labels:
        p_a = sum(1 for x in a if x == label) / n
        p_b = sum(1 for x in b if x == label) / n
        expected += p_a * p_b

    if expected >= 1.0:
        return 1.0
    return (observed - expected) / (1 - expected)


def summarize(rows: list[dict], second: list[dict] | None = None) -> dict:
    """统计各维度。"""
    per_dimension: dict[str, dict] = {}

    for key, label, threshold in DIMENSIONS:
        values = [_parse_score(row.get(key)) for row in rows]
        scored = [v for v in values if v is not None]
        per_dimension[key] = {
            "label": label,
            "threshold": threshold,
            "scored": len(scored),
            "missing": len(values) - len(scored),
            "mean": round(statistics.mean(scored), 2) if scored else None,
            "median": statistics.median(scored) if scored else None,
            "min": min(scored) if scored else None,
            "max": max(scored) if scored else None,
        }

    # 一致性（有第二人时）
    if second:
        by_id_b = {row["case_id"]: row for row in second}
        for key, _, _ in DIMENSIONS:
            pairs = [
                (_parse_score(row.get(key)), _parse_score(by_id_b[row["case_id"]].get(key)))
                for row in rows
                if row["case_id"] in by_id_b
            ]
            usable = [(x, y) for x, y in pairs if x is not None and y is not None]
            if len(usable) >= MIN_SAMPLE:
                kappa = cohens_kappa([x for x, _ in usable], [y for _, y in usable])
                per_dimension[key]["kappa"] = round(kappa, 3)
                per_dimension[key]["kappa_ok"] = kappa >= KAPPA_THRESHOLD

    return {"dimensions": per_dimension, "n_cases": len(rows)}


def render(rows: list[dict], stats: dict, second_used: bool) -> str:
    lines = [
        "# MuseGuide 人工评分报告",
        "",
        "> 由 `knowledge/eval/summarize_scores.py` 从 `reports/scoring/scores.csv` 自动生成。",
        f"> 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## 1. 概览",
        "",
        f"- 评分的题数：**{stats['n_cases']}**",
        f"- 评分人数：**{2 if second_used else 1}**",
        f"- 评分维度：{len(DIMENSIONS)} 个（`MVP计划.md` §7.2）",
        "",
        "## 2. 各维度结果",
        "",
        "| 维度 | 含义 | 已评 | 未评 | 均值 | 中位数 | 最低 | 门槛 | 结论 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for key, _, _ in DIMENSIONS:
        item = stats["dimensions"][key]
        if item["mean"] is None:
            lines.append(
                f"| `{key}` | {item['label']} | 0 | {item['missing']} | — | — | — | "
                f"{item['threshold']:.1f} | ⬜ 无数据 |"
            )
            continue

        if item["scored"] < MIN_SAMPLE:
            mark = f"⚠️ 样本仅 {item['scored']}"
        else:
            mark = "✅" if item["mean"] >= item["threshold"] else "❌"

        lines.append(
            f"| `{key}` | {item['label']} | {item['scored']} | {item['missing']} | "
            f"**{item['mean']:.2f}** | {item['median']:.1f} | {item['min']} | "
            f"{item['threshold']:.1f} | {mark} |"
        )

    # 一致性
    if second_used:
        lines.extend(
            [
                "",
                "## 3. 评分者一致性（Cohen's κ）",
                "",
                "| 维度 | κ | 是否可用于结论 |",
                "|---|---|---|",
            ]
        )
        for key, _, _ in DIMENSIONS:
            item = stats["dimensions"][key]
            if "kappa" not in item:
                lines.append(f"| `{key}` | — | 样本不足，未计算 |")
            else:
                mark = "✅ 可用" if item["kappa_ok"] else f"❌ 不可用（< {KAPPA_THRESHOLD}）"
                lines.append(f"| `{key}` | {item['kappa']} | {mark} |")
        lines.extend(
            [
                "",
                f"> **κ < {KAPPA_THRESHOLD} 的维度不进入结论。**",
                "> 这是 `MVP计划.md` §7.2 的要求：宁可承认指标不可靠，也不拿它当证据。",
            ]
        )

    # 低分题
    low = []
    for row in rows:
        scored = [_parse_score(row.get(key)) for key, _, _ in DIMENSIONS]
        scored = [s for s in scored if s is not None]
        if scored and statistics.mean(scored) <= 3.0:
            low.append((statistics.mean(scored), row))

    lines.extend(["", "## 4. 值得复盘的题（均分 ≤ 3.0）", ""])
    if low:
        for mean, row in sorted(low, key=lambda item: item[0]):
            comment = (row.get("comment") or "").strip()
            lines.append(f"- **{row['case_id']}**（均分 {mean:.1f}）")
            lines.append(f"  - 输入：{row.get('input', '')}")
            if comment:
                lines.append(f"  - 评分备注：{comment}")
        lines.append("")
        lines.append("> 这些题的原始输出见 `reports/scoring/samples.md`。")
    else:
        lines.append("无（所有题均分都高于 3.0）。")
        lines.append("")
        lines.append("> ⚠️ 若全部题目的分数都很高，请复核是否存在「礼貌性高分」。")
        lines.append("> 评分的目的是发现边界，**一份挑不出问题的评分表本身就值得怀疑**。")

    # 诚实声明
    lines.extend(["", "## 5. 数据完整性", ""])
    total_scored = sum(stats["dimensions"][key]["scored"] for key, _, _ in DIMENSIONS)
    total_cells = stats["n_cases"] * len(DIMENSIONS)
    lines.append(f"- 已填评分格：**{total_scored} / {total_cells}**")
    if total_scored == 0:
        lines.append("- ⚠️ **尚未评分。** 请先填写 `reports/scoring/scores.csv`。")
    elif total_scored < total_cells * 0.5:
        lines.append(
            "- ⚠️ **有效评分数不足一半，本报告不足以支撑任何结论。** "
            "留空是可以的，但当前样本量下均值没有统计意义。"
        )

    if stats["n_cases"] < MIN_SAMPLE:
        lines.append(
            f"- ⚠️ **仅评了 {stats['n_cases']} 题**（建议 ≥ {MIN_SAMPLE} 题），"
            "当前结果仅供参考。"
        )

    lines.extend(
        [
            "",
            "## 6. 说明",
            "",
            "- **留空不算 0 分。** 未评项按「未评」统计，不参与均值计算。",
            "- **不懂乐理也能评。** 八个维度里只有 `theory_validity` 需要乐理判断，",
            "  其余评的是普通读者的直觉反应。判据见 `docs/SCORING_GUIDE.md`。",
            "- 主观维度存在评分者偏差，因此单次评分**不应作为绝对的结论**，",
            "  而应作为「系统在哪些地方明显不够好」的定位工具。",
            "",
            "---",
            "",
            "*本报告由 `knowledge/eval/summarize_scores.py` 自动生成。*",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="汇总人工评分")
    parser.add_argument("--scores", type=Path, default=SCORES_PATH, help="评分表路径")
    parser.add_argument("--second", type=Path, default=None, help="第二位评分者的评分表")
    args = parser.parse_args(argv)

    rows = load_scores(args.scores)
    second = load_scores(args.second) if args.second else None

    stats = summarize(rows, second)
    report = render(rows, stats, second_used=second is not None)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")

    print(f"✅ 报告：{REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print()
    for key, _, _ in DIMENSIONS:
        item = stats["dimensions"][key]
        mean = f"{item['mean']:.2f}" if item["mean"] is not None else "—"
        print(f"  {key:20} 均值={mean:>5}  已评={item['scored']:>2}  未评={item['missing']:>2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
