"""Hybrid 检索编排（``MVP计划.md`` §3.7）。

```text
Query  ──┬─→ Dense（向量）  ──┐
         └─→ Sparse（BM25）  ──┼─→ 融合去重排序 ─→ Top-K Evidence
                 + Metadata Filter ─┘
```

**检索策略自适应（§3.7.2）：**
* 零基础用户 → 语义检索 + 情绪标签过滤 + **通俗内容优先**
* 进阶用户 → 关键词 + 向量 + **理论文档优先**

**组件全部可插拔**，使 P6 能按「单一变量」原则做消融对比（计划 §5 决策 B）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.core.brief import UserLevel
from app.core.evidence import Evidence, EvidenceSource
from app.services.retrieval.dense import DenseIndex, Embedder, HashEmbedder
from app.services.retrieval.fusion import (
    DEFAULT_RRF_K,
    FusionStrategy,
    FusedCandidate,
    fuse,
    to_ranked,
)
from app.services.retrieval.sparse import DEFAULT_FIELD_WEIGHTS, BM25

RetrievalMode = Literal["none", "dense", "sparse", "hybrid"]

#: 零基础用户偏好的分类（通俗解释与起步引导）
ZERO_LEVEL_CATEGORIES = ("starter", "emotion", "layman")
#: 进阶用户偏好的分类（理论文档与案例）
ADVANCED_CATEGORIES = ("harmony", "melody", "examples")


@dataclass
class RetrievalConfig:
    """一次检索的配置 —— P6 的消融实验就是改这里的一个字段。"""

    mode: RetrievalMode = "hybrid"
    fusion: FusionStrategy = "rrf"
    candidate_k: int = 20
    top_k: int = 5
    rrf_k: int = DEFAULT_RRF_K
    route_weights: dict[str, float] = field(default_factory=lambda: {"dense": 1.0, "sparse": 1.0})
    use_metadata_filter: bool = True
    prefer_layman: bool = True

    def describe(self) -> dict:
        """可写入 Trace 的配置摘要（实验溯源用）。"""
        return {
            "mode": self.mode,
            "fusion": self.fusion,
            "candidate_k": self.candidate_k,
            "top_k": self.top_k,
            "rrf_k": self.rrf_k,
            "route_weights": dict(self.route_weights),
            "use_metadata_filter": self.use_metadata_filter,
            "prefer_layman": self.prefer_layman,
        }


#: P6 消融实验的配置矩阵。
#:
#: 设计原则：**每次只改一个变量**，使差异可归因。
#: 名称中带 ``baseline`` 的是对照组（不检索）。
EXPERIMENT_MATRIX: dict[str, RetrievalConfig] = {
    "baseline_no_retrieval": RetrievalConfig(mode="none"),
    "dense_only": RetrievalConfig(mode="dense", fusion="none", use_metadata_filter=False),
    "sparse_only": RetrievalConfig(mode="sparse", fusion="none", use_metadata_filter=False),
    "hybrid_rrf": RetrievalConfig(mode="hybrid", fusion="rrf", use_metadata_filter=False),
    "hybrid_rrf_filter": RetrievalConfig(mode="hybrid", fusion="rrf", use_metadata_filter=True),
    "hybrid_weighted": RetrievalConfig(mode="hybrid", fusion="weighted", use_metadata_filter=True),
    "hybrid_rrf_layman_first": RetrievalConfig(
        mode="hybrid", fusion="rrf", use_metadata_filter=True, prefer_layman=True
    ),
}

#: **生产配置** —— 即 ``hybrid_rrf``，也就是 P6 实验中严格 Recall@5 达标的那个。
#:
#: 注意它**没有**启用 metadata filter：实测该组件的边际贡献为
#: −0.008 且不显著（CI 跨 0），因此不值得为它增加复杂度。
#: 这是「按数据取舍」而非「按直觉堆组件」的一个具体例子。
PRODUCTION_CONFIG = EXPERIMENT_MATRIX["hybrid_rrf"]


def _field_texts(entries: list[dict], field_name: str, key: str) -> list[str]:
    """把条目的某个字段抽成纯文本列表（列表字段用空格连接）。"""
    out: list[str] = []
    for entry in entries:
        value = entry.get(field_name)
        if value is None:
            out.append("")
        elif isinstance(value, list):
            out.append(" ".join(str(v) for v in value))
        else:
            out.append(str(value))
    return out


class HybridRetriever:
    """两路召回 + 可插拔融合 + 元数据过滤。"""

    def __init__(
        self,
        entries: list[dict],
        *,
        embedder: Embedder | None = None,
        config: RetrievalConfig | None = None,
    ) -> None:
        if not entries:
            raise ValueError("知识库为空，无法建立检索索引")

        self._entries = entries
        self._by_id = {entry["id"]: entry for entry in entries}
        self._config = config or RetrievalConfig()
        self._embedder: Embedder = embedder or HashEmbedder()

        self._ids = [entry["id"] for entry in entries]

        # --- 稀疏索引：按字段加权 ---
        fields = {
            name: _field_texts(entries, name, name)
            for name in DEFAULT_FIELD_WEIGHTS
        }
        self._bm25 = BM25()
        self._bm25.index(self._ids, fields, DEFAULT_FIELD_WEIGHTS)

        # --- 稠密索引：把多个字段拼成一条文档文本 ---
        self._dense = DenseIndex(self._embedder)
        self._dense.index(
            self._ids,
            [
                " ".join(
                    filter(
                        None,
                        [
                            entry.get("title", ""),
                            entry.get("layman_title", ""),
                            " ".join(entry.get("tags") or []),
                            entry.get("content", ""),
                            entry.get("layman_content", ""),
                        ],
                    )
                )
                for entry in entries
            ],
        )

    @property
    def embedder_name(self) -> str:
        return self._embedder.name

    @property
    def config(self) -> RetrievalConfig:
        return self._config

    def with_config(self, config: RetrievalConfig) -> "HybridRetriever":
        """换一组配置（复用已建好的索引，供消融实验快速切换）。"""
        clone = HybridRetriever.__new__(HybridRetriever)
        clone._entries = self._entries
        clone._by_id = self._by_id
        clone._embedder = self._embedder
        clone._ids = self._ids
        clone._bm25 = self._bm25
        clone._dense = self._dense
        clone._config = config
        return clone

    # ---- 过滤 ----

    def _allowed_ids(self, categories: list[str] | None, user_level: UserLevel) -> set[str] | None:
        """元数据过滤（§3.7.1 的 Metadata Filter 组件）。

        :return: 允许的 id 集合；``None`` 表示不过滤
        """
        config = self._config
        if not config.use_metadata_filter and not categories:
            return None

        allow = set(categories) if categories else None
        if allow is None and config.use_metadata_filter:
            # 按用户水平收窄分类（§3.7.2 的策略自适应）
            allow = set(ZERO_LEVEL_CATEGORIES if user_level == "zero" else ADVANCED_CATEGORIES)
            # 「some」介于两者之间：全分类但排序不同，因此不加限制
            if user_level == "some":
                allow = None

        if allow is None:
            return None
        return {entry["id"] for entry in self._entries if entry.get("category") in allow}

    # ---- 检索 ----

    def retrieve(
        self,
        queries: list[str],
        *,
        user_level: UserLevel = "zero",
        emotions: list[str] | None = None,
        categories: list[str] | None = None,
        primary_query_weight: float = 2.0,
    ) -> tuple[list[Evidence], list[FusedCandidate]]:
        """执行检索。

        :param queries: 检索词列表。**第一个被视为「用户原话」**，
            获得 ``primary_query_weight`` 倍权重 —— 用户真正问的东西
            应当比系统自动扩展的检索词更重要。
        :param primary_query_weight: 首条 query 的权重倍数（1.0 表示与其它等权）
        :return: ``(evidence, candidates)``；candidates 保留逐级排名，
            写入 ``Trace`` 后即可回答「这条证据由哪条 query、在第几级被召回」
        """
        config = self._config
        if config.mode == "none" or not queries:
            return [], []

        allowed = self._allowed_ids(categories, user_level)
        per_query: dict[str, list[FusedCandidate]] = {}

        for position, query in enumerate(queries):
            if not query or not query.strip():
                continue
            ranked_lists = {}

            if config.mode in ("dense", "hybrid"):
                hits = [(h.doc_id, h.score) for h in self._dense.search(query, config.candidate_k)]
                if allowed is not None:
                    hits = [h for h in hits if h[0] in allowed]
                ranked_lists["dense"] = to_ranked(hits)

            if config.mode in ("sparse", "hybrid"):
                hits = self._bm25.search(query, config.candidate_k)
                if allowed is not None:
                    hits = [h for h in hits if h[0] in allowed]
                ranked_lists["sparse"] = to_ranked(hits)

            fused = fuse(
                ranked_lists,
                config.fusion if config.mode == "hybrid" else "none",
                weights=config.route_weights,
                rrf_k=config.rrf_k,
            )
            # 用户原话的候选获得更高权重 —— 见 test_primary_query_dominates_fallbacks
            if position == 0 and primary_query_weight != 1.0:
                for candidate in fused:
                    candidate.score *= primary_query_weight
            for candidate in fused:
                candidate.from_query = query
            if fused:
                per_query[query] = fused

        return self._finalize(per_query, emotions, user_level)

    def _finalize(
        self,
        per_query: dict[str, list[FusedCandidate]],
        emotions: list[str] | None,
        user_level: UserLevel,
    ) -> tuple[list[Evidence], list[FusedCandidate]]:
        """合并多 query 的结果、去重、按策略重排、截断到 ``top_k``。

        跨 query 合并取**每篇文档在所有 query 中的最高 RRF 分数**。

        ⚠️ **这里试过三种方案，只有第一种达标（严格 Recall@5）：**

        =================== ============== ==========================================
        方案                 严格 Recall@5  问题
        =================== ============== ==========================================
        ``max(score)``      **0.734**      当前实现基准
        跨 query 再做 RRF    0.492          多而弱的 query 合起来压过最相关的那个
        按名次合并           0.548          名次信息丢失了「两路都命中」的加成
        =================== ============== ==========================================

        后两种方案都是「看起来更优雅但实测更差」的典型 ——
        保留这张表是为了避免后来者（包括我自己）再走一遍。
        """
        config = self._config
        best: dict[str, FusedCandidate] = {}

        for _query, candidates in per_query.items():
            for candidate in candidates:
                existing = best.get(candidate.doc_id)
                if existing is None or candidate.score > existing.score:
                    best[candidate.doc_id] = candidate

        ranking = list(best.values())

        # 情绪标签匹配加分（§3.7.2：零基础用户的情绪过滤）
        if emotions:
            for candidate in ranking:
                entry_emotions = set(self._by_id.get(candidate.doc_id, {}).get("emotions") or [])
                if entry_emotions & set(emotions):
                    candidate.score *= 1.15

        # 通俗内容优先（§3.7.2：零基础用户）
        if config.prefer_layman and user_level == "zero":
            for candidate in ranking:
                entry = self._by_id.get(candidate.doc_id, {})
                if entry.get("layman_content"):
                    candidate.score *= 1.10

        # 稳定排序：分数降序，doc_id 升序（实验可复现）
        ranking.sort(key=lambda c: (-c.score, c.doc_id))
        ranking = ranking[: config.top_k]
        for index, candidate in enumerate(ranking, start=1):
            candidate.rank_final = index

        evidence = [self._to_evidence(candidate) for candidate in ranking]
        return evidence, ranking

    def _to_evidence(self, candidate: FusedCandidate) -> Evidence:
        entry = self._by_id[candidate.doc_id]
        source = entry.get("source") or {}
        return Evidence(
            entry_id=entry["id"],
            title=entry.get("title", ""),
            layman_title=entry.get("layman_title"),
            content=entry.get("content", ""),
            layman_content=entry.get("layman_content"),
            tags=list(entry.get("tags") or []),
            source=EvidenceSource(
                title=source.get("title", ""),
                url=source.get("url", ""),
                license=source.get("license", ""),
            ),
            score=round(candidate.score, 6),
            from_query=candidate.from_query,
            candidate=candidate.to_candidate(),
        )

    @property
    def size(self) -> int:
        return len(self._entries)
