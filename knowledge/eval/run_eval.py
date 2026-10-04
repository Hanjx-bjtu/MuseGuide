"""一键评测入口（P6.4 / P6.5）。

**为什么要有独立 CLI：** 计划 §10.3 要求「任何进入报告的数字都能通过
一条命令重跑」。这个脚本就是那条命令。

用法::

    python knowledge/eval/run_eval.py                 # 跑全部并写报告
    python knowledge/eval/run_eval.py --quick         # 只跑生产配置
    python knowledge/eval/run_eval.py --json          # 只输出 JSON
    python knowledge/eval/run_eval.py --analysis      # 附带分析层金标准评测
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.experiment.metrics import evaluate, has_discrimination  # noqa: E402
from app.experiment.report import render_report, write_report  # noqa: E402
from app.experiment.runner import (  # noqa: E402
    load_retrieval_dataset,
    run_matrix,
    run_retrieval_experiment,
    save_results,
)
from app.services.retrieval.hybrid import EXPERIMENT_MATRIX, PRODUCTION_CONFIG  # noqa: E402


def _print_table(results) -> None:
    print(f"{'配置':<30} {'宽松R@5':>9} {'严格R@5':>9} {'MRR':>7} {'nDCG@5':>8}")
    print("-" * 68)
    for result in results:
        m = result.metrics
        print(
            f"{result.name:<30} {m.loose_recall:9.3f} {m.strict_recall:9.3f} "
            f"{m.mrr:7.3f} {m.ndcg:8.3f}"
        )


def run_analysis_eval() -> dict:
    """分析层金标准评测（复用 P2 建立的数据集）。"""
    from app.services.analyzer import analyze, build_artifact
    from app.services.analyzer.key import detect_key_from_chords
    from app.services.parser.chords import parse_progression
    from app.services.parser.melody import parse_melody

    gold_path = PROJECT_ROOT / "datasets" / "analysis_gold" / "v1.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))

    key_correct = 0
    roman_total = roman_correct = 0
    func_total = func_correct = 0
    melody_correct = melody_total = 0

    for case in gold["cases"]:
        chords = parse_progression(case["chords"])
        detected = detect_key_from_chords(chords)
        acceptable = case.get("acceptable_keys") or [case["key"]]
        if detected and detected.key in acceptable:
            key_correct += 1

        artifact = build_artifact(chord_text=case["chords"], key=case["key"])
        analysis = analyze(artifact)
        for got, want in zip(analysis.roman, case["roman"]):
            roman_total += 1
            roman_correct += got == want
        for got, want in zip(analysis.functions, case["functions"]):
            func_total += 1
            func_correct += got == want

    for case in gold["melody_cases"]:
        melody_total += 1
        melody = parse_melody(case["melody"])
        if (
            melody.range == case["range"]
            and melody.contour == case["contour"]
            and melody.repetition == case["repetition"]
        ):
            melody_correct += 1

    return {
        "key_accuracy": round(key_correct / len(gold["cases"]), 4),
        "roman_accuracy": round(roman_correct / roman_total, 4) if roman_total else 0.0,
        "function_accuracy": round(func_correct / func_total, 4) if func_total else 0.0,
        "melody_accuracy": round(melody_correct / melody_total, 4) if melody_total else 0.0,
        "n_cases": len(gold["cases"]),
        "n_melody": len(gold["melody_cases"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MuseGuide 一键评测")
    parser.add_argument("--quick", action="store_true", help="只跑生产配置")
    parser.add_argument("--json", action="store_true", help="只输出 JSON，不写报告")
    parser.add_argument("--analysis", action="store_true", help="附带分析层金标准评测")
    parser.add_argument("--k", type=int, default=5, help="Recall@K 的 K")
    args = parser.parse_args(argv)

    cases = load_retrieval_dataset()
    print(f"评测集：{len(cases)} 题（冻结版 v1）")
    print()

    if args.quick:
        results = [run_retrieval_experiment("hybrid_rrf", PRODUCTION_CONFIG, cases=cases, k=args.k)]
    else:
        results = run_matrix(k=args.k)

    _print_table(results)
    print()
    print(f"评测集有区分度：{has_discrimination([r.metrics for r in results])}")

    analysis_metrics = run_analysis_eval() if args.analysis else None
    if analysis_metrics:
        print()
        print("=== 分析层金标准 ===")
        for key, value in analysis_metrics.items():
            print(f"  {key}: {value}")

    if args.json:
        print()
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
        return 0

    # 写产物与报告
    summary_path = save_results(results, tag="retrieval")
    print()
    print(f"实验产物：{summary_path.relative_to(PROJECT_ROOT)}")

    extra = ""
    if analysis_metrics:
        extra = "\n".join(
            [
                "## 附：分析层金标准评测",
                "",
                f"- 调性判断准确率：**{analysis_metrics['key_accuracy']:.3f}**"
                f"（{analysis_metrics['n_cases']} 个和弦片段）",
                f"- 罗马数字标注：**{analysis_metrics['roman_accuracy']:.3f}**",
                f"- 功能标注：**{analysis_metrics['function_accuracy']:.3f}**",
                f"- 旋律分析：**{analysis_metrics['melody_accuracy']:.3f}**"
                f"（{analysis_metrics['n_melody']} 个旋律片段）",
            ]
        )

    from app.experiment.report import _default_notes  # noqa: PLC0415

    report = render_report(results, k=args.k, extra_sections=extra, notes=_default_notes(results))
    report_path = write_report(report)
    print(f"评测报告：{report_path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
