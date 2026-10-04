"""实验运行器（P6.4 / P6.5）。

**核心原则：`Config × Dataset → Trace[] → Metrics → Report`，全程自动化。**

报告里的数字**必须由 Trace 渲染**，禁止手工誊写 —— 手工誊写的表格
会与 JSON 数据漂移（改一次参数就要重抄一遍），这是不可接受的科研工程实践。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT
from app.experiment.metrics import (
    RetrievalMetrics,
    evaluate,
    paired_bootstrap,
)
from app.services.kb import load_entries
from app.services.query import decompose_by_rules
from app.services.retrieval.dense import HashEmbedder, build_embedder
from app.services.retrieval.hybrid import EXPERIMENT_MATRIX, HybridRetriever, RetrievalConfig

DATASETS_DIR = PROJECT_ROOT / "datasets"
EXPERIMENTS_DIR = PROJECT_ROOT / "experiments"
REPORTS_DIR = PROJECT_ROOT / "reports"


@dataclass
class ExperimentResult:
    """一次实验的完整产出。"""

    name: str
    config: dict
    metrics: RetrievalMetrics
    cases: list[dict] = field(default_factory=list)
    elapsed_s: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "config": self.config,
            "metrics": self.metrics.to_dict(),
            "elapsed_s": round(self.elapsed_s, 2),
            "notes": self.notes,
        }


def load_retrieval_dataset(path: Path | None = None) -> list[dict]:
    """加载冻结的检索评测集。"""
    path = path or DATASETS_DIR / "retrieval_qa" / "v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["cases"]


def _queries_for(case: dict) -> list[str]:
    """把一道评测题变成检索词。

    **刻意用规则分解器**：它不依赖 LLM，因此实验可离线重跑。
    LLM 分解的效果差异属于另一个自变量，应作为单独的实验轴。
    """
    decomposed = decompose_by_rules(
        goal_text=case["q"],
        user_level=case.get("level", "zero"),
    )
    return decomposed.queries or [case["q"]]


def run_retrieval_experiment(
    name: str,
    config: RetrievalConfig,
    *,
    cases: list[dict] | None = None,
    k: int = 5,
    embedder=None,
) -> ExperimentResult:
    """跑一组配置，返回指标。"""
    started = time.perf_counter()
    cases = cases if cases is not None else load_retrieval_dataset()

    if embedder is None:
        embedder, note = build_embedder("hash")
        embedding_note = note
    else:
        embedding_note = None

    entries = load_entries()
    base = HybridRetriever(entries, embedder=embedder, config=config)
    retriever = base.with_config(config)

    results: list[dict] = []
    for case in cases:
        queries = _queries_for(case)
        evidence, _candidates = retriever.retrieve(
            queries,
            user_level=case.get("level", "zero"),
            emotions=[],
        )
        results.append(
            {
                "question": case["q"],
                "gold": case["gold"],
                "retrieved": [item.entry_id for item in evidence],
            }
        )

    metrics = evaluate(results, k=k)
    notes: list[str] = []
    if embedding_note:
        notes.append(embedding_note)
    notes.append(f"嵌入后端：{base.embedder_name}")

    return ExperimentResult(
        name=name,
        config=config.describe(),
        metrics=metrics,
        cases=results,
        elapsed_s=time.perf_counter() - started,
        notes=notes,
    )


def run_matrix(
    *,
    matrix: dict[str, RetrievalConfig] | None = None,
    k: int = 5,
) -> list[ExperimentResult]:
    """跑完整实验矩阵。"""
    matrix = matrix or EXPERIMENT_MATRIX
    cases = load_retrieval_dataset()
    embedder, _ = build_embedder("hash")

    results: list[ExperimentResult] = []
    for name, config in matrix.items():
        # baseline_no_retrieval 不检索，单独处理
        if config.mode == "none":
            results.append(
                ExperimentResult(
                    name=name,
                    config=config.describe(),
                    metrics=evaluate(
                        [
                            {"question": c["q"], "gold": c["gold"], "retrieved": []}
                            for c in cases
                        ],
                        k=k,
                    ),
                    cases=[],
                    notes=["对照组：不使用检索，全部指标为 0"],
                )
            )
            continue
        results.append(
            run_retrieval_experiment(name, config, cases=cases, k=k, embedder=embedder)
        )
    return results


def save_results(results: list[ExperimentResult], *, tag: str = "retrieval") -> Path:
    """把实验结果存档为 JSONL + 摘要 JSON（供报告渲染）。"""
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")

    detail_path = EXPERIMENTS_DIR / f"{tag}_{stamp}.jsonl"
    with detail_path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")

    summary_path = EXPERIMENTS_DIR / f"{tag}_{stamp}_summary.json"
    summary_path.write_text(
        json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary_path


def compare(a: ExperimentResult, b: ExperimentResult) -> dict[str, Any]:
    """两组配置的配对比较（含 bootstrap 置信区间）。

    **禁止「X 比 Y 高 0.02」式的结论** —— 必须给出区间，
    否则无法区分真实差异与评测集噪声（计划 §12 S12.4）。
    """
    a_strict = [c["strict"] for c in a.metrics.per_case]
    b_strict = [c["strict"] for c in b.metrics.per_case]

    mean_diff, low, high = paired_bootstrap(a_strict, b_strict)
    return {
        "a": a.name,
        "b": b.name,
        "strict_recall_a": round(a.metrics.strict_recall, 4),
        "strict_recall_b": round(b.metrics.strict_recall, 4),
        "mean_diff": mean_diff,
        "ci95": [low, high],
        "significant": (low > 0) or (high < 0),
        "elapsed_a_s": round(a.elapsed_s, 2),
        "elapsed_b_s": round(b.elapsed_s, 2),
    }
