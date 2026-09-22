"""融合与去重排序（``MVP计划.md`` §3.7.1 / §3.7.3）。

**可插拔策略**（计划 §5 的「决策 B：变量真正单一」）：
* ``rrf`` —— Reciprocal Rank Fusion，只看排名不看分数，对量纲不敏感
* ``weighted`` —— 归一化分数加权和，可调两路权重
* ``none`` —— 只用单路（用于消融实验）

**关键设计：所有排序都必须带次级键 doc_id。**
同分时若顺序随机，实验就无法重跑 —— 这是 P6 结论可信的前提。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.core.evidence import Candidate

FusionStrategy = Literal["none", "rrf", "weighted"]

#: RRF 的平滑常数（原论文取 60）
DEFAULT_RRF_K = 60


@dataclass
class RankedDoc:
    """一次召回中的单个结果。"""

    doc_id: str
    score: float
    rank: int


@dataclass
class FusedCandidate:
    """融合后的候选（保留逐级排名，供 Trace 展示可解释性）。"""

    doc_id: str
    score: float
    rank_dense: int | None = None
    rank_sparse: int | None = None
    rank_final: int | None = None
    from_query: str | None = None
    routes: list[str] = field(default_factory=list)

    @property
    def route(self) -> str:
        """召回路径标记（``dense`` / ``sparse`` / ``both``）。"""
        if len(self.routes) >= 2:
            return "both"
        return self.routes[0] if self.routes else "none"

    def to_candidate(self) -> Candidate:
        return Candidate(
            entry_id=self.doc_id,
            score=round(self.score, 6),
            route=self.route,  # type: ignore[arg-type]
            rank_dense=self.rank_dense,
            rank_sparse=self.rank_sparse,
            rank_final=self.rank_final,
            from_query=self.from_query,
        )


def rrf_fuse(
    ranked_lists: dict[str, list[RankedDoc]],
    *,
    k: int = DEFAULT_RRF_K,
    weights: dict[str, float] | None = None,
) -> list[FusedCandidate]:
    """Reciprocal Rank Fusion。

    每个文档的得分是 ``Σ weight / (k + rank)``。只用排名不用原始分数，
    因此不需要处理「BM25 分数与余弦相似度量纲不同」的问题。
    """
    weights = weights or {}
    merged: dict[str, FusedCandidate] = {}

    for route, ranked in ranked_lists.items():
        weight = weights.get(route, 1.0)
        for item in ranked:
            candidate = merged.setdefault(item.doc_id, FusedCandidate(doc_id=item.doc_id, score=0.0))
            candidate.score += weight / (k + item.rank)
            candidate.routes.append(route)
            if route == "dense":
                candidate.rank_dense = item.rank
            elif route == "sparse":
                candidate.rank_sparse = item.rank

    return _finalize(merged)


def weighted_fuse(
    ranked_lists: dict[str, list[RankedDoc]],
    *,
    weights: dict[str, float] | None = None,
) -> list[FusedCandidate]:
    """归一化分数加权和。

    每路分数先除以该路的最高分（归一化到 0~1），再加权求和 ——
    否则量纲大的那一路会完全主导结果。
    """
    weights = weights or {}
    merged: dict[str, FusedCandidate] = {}

    for route, ranked in ranked_lists.items():
        if not ranked:
            continue
        weight = weights.get(route, 1.0)
        top = max((item.score for item in ranked), default=0.0) or 1.0

        for item in ranked:
            candidate = merged.setdefault(item.doc_id, FusedCandidate(doc_id=item.doc_id, score=0.0))
            candidate.score += weight * (item.score / top)
            candidate.routes.append(route)
            if route == "dense":
                candidate.rank_dense = item.rank
            elif route == "sparse":
                candidate.rank_sparse = item.rank

    return _finalize(merged)


def _finalize(merged: dict[str, FusedCandidate]) -> list[FusedCandidate]:
    """排序并写入 ``rank_final``。次级键 doc_id 保证可复现。"""
    results = sorted(merged.values(), key=lambda c: (-c.score, c.doc_id))
    for index, candidate in enumerate(results, start=1):
        candidate.rank_final = index
    return results


def fuse(
    ranked_lists: dict[str, list[RankedDoc]],
    strategy: FusionStrategy = "rrf",
    *,
    weights: dict[str, float] | None = None,
    rrf_k: int = DEFAULT_RRF_K,
) -> list[FusedCandidate]:
    """统一入口。"""
    if strategy == "rrf":
        return rrf_fuse(ranked_lists, k=rrf_k, weights=weights)
    if strategy == "weighted":
        return weighted_fuse(ranked_lists, weights=weights)

    # strategy == "none"：只用第一路有结果的那条
    for route in ("dense", "sparse"):
        if ranked_lists.get(route):
            return _finalize(
                {
                    item.doc_id: FusedCandidate(
                        doc_id=item.doc_id,
                        score=item.score,
                        rank_dense=item.rank if route == "dense" else None,
                        rank_sparse=item.rank if route == "sparse" else None,
                        routes=[route],
                    )
                    for item in ranked_lists[route]
                }
            )
    return []


def to_ranked(hits: list[tuple[str, float]]) -> list[RankedDoc]:
    """``[(doc_id, score)]`` → ``[RankedDoc]``（名次从 1 开始）。"""
    return [
        RankedDoc(doc_id=doc_id, score=score, rank=index)
        for index, (doc_id, score) in enumerate(hits, start=1)
    ]
