"""检索服务门面 —— 统一「主路径（Hybrid）+ 降级路径（离线直出）」。

**为什么需要这一层：** Starter 与 Advisor 都需要检索，但不应各自处理
「向量库没装怎么办」「知识库读不到怎么办」。把这些判断集中在这里，
调用方只拿到 ``(evidence, candidates, degradation)``。

降级路径**不是历史包袱**：向量库/嵌入加载失败、知识库损坏、
依赖缺失都会走到它，而那时系统仍应给出可用的建议（§8 风险应对）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from app.core.brief import CreativeIntent, UserLevel
from app.core.degradation import DegradationLog
from app.core.evidence import Candidate, DecomposedQuery, Evidence
from app.services.kb import load_entries
from app.services.retrieval.dense import Embedder, build_embedder
from app.services.retrieval.hybrid import HybridRetriever, RetrievalConfig
from app.services.retrieval.offline import OfflineKnowledgeBase
from app.services.query import decompose


@dataclass
class RetrievalResult:
    """一次检索的完整产出。"""

    evidence: list[Evidence] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    decomposed: DecomposedQuery | None = None
    strategy: str = "hybrid"


@lru_cache(maxsize=1)
def _default_embedder() -> Embedder:
    embedder, _note = build_embedder("hash")
    return embedder


@lru_cache(maxsize=1)
def _default_retriever() -> HybridRetriever:
    """进程内复用索引（建索引需要跑一次嵌入，不应每请求重做）。"""
    return HybridRetriever(load_entries(), embedder=_default_embedder())


def clear_cache() -> None:
    """清除索引缓存（知识库热更新或测试用）。"""
    _default_retriever.cache_clear()
    _default_embedder.cache_clear()


def retrieve(
    *,
    goal_text: str = "",
    intent: CreativeIntent | None = None,
    artifact_summary: str = "",
    user_level: UserLevel = "zero",
    queries: list[str] | None = None,
    emotions: list[str] | None = None,
    llm: object | None = None,
    config: RetrievalConfig | None = None,
    log: DegradationLog | None = None,
) -> RetrievalResult:
    """执行检索：Query 分解 → Hybrid 检索 → 失败则回落离线直出。

    :param queries: 显式指定检索词；留空则由 Query 分解生成
    :param config: 实验配置（P6 的消融实验通过它切换组件）
    """
    log = log if log is not None else DegradationLog()
    emotions = emotions if emotions is not None else (intent.emotion if intent else [])

    # --- Query 分解 ---
    decomposed = decompose(
        goal_text=goal_text,
        intent=intent,
        artifact_summary=artifact_summary,
        user_level=user_level,
        llm=llm,
        log=log,
    )
    effective_queries = queries or decomposed.queries
    if not effective_queries:
        effective_queries = [goal_text or "如何开始写一首歌"]

    # --- 主路径：Hybrid ---
    try:
        retriever = _default_retriever()
        if config is not None:
            retriever = retriever.with_config(config)
        evidence, candidates = retriever.retrieve(
            effective_queries, user_level=user_level, emotions=emotions
        )
        if evidence:
            return RetrievalResult(
                evidence=evidence,
                candidates=[c.to_candidate() for c in candidates],
                decomposed=decomposed,
                strategy=f"hybrid:{retriever.config.mode}/{retriever.config.fusion}",
            )
        log.add(
            "retrieval_unavailable",
            "Hybrid 检索未召回任何内容，已回落离线知识直出",
            fallback_to="offline_kb",
        )
    except Exception as exc:  # noqa: BLE001 - 索引构建失败不得中断链路
        log.add(
            "retrieval_unavailable",
            f"Hybrid 检索不可用（{type(exc).__name__}: {exc}），已回落离线知识直出",
            fallback_to="offline_kb",
        )

    # --- 降级路径：离线直出 ---
    try:
        kb = OfflineKnowledgeBase()
        if intent is not None:
            evidence = kb.search_for_intent(intent, goal_text, top_k=5)
        else:
            evidence = kb.search(effective_queries, emotions=emotions, top_k=5)
        return RetrievalResult(
            evidence=evidence,
            candidates=[
                Candidate(entry_id=e.entry_id, score=e.score, route="none", from_query=e.from_query)
                for e in evidence
            ],
            decomposed=decomposed,
            strategy="offline",
        )
    except Exception as exc:  # noqa: BLE001 - 知识库都读不到时返回空，不抛
        log.add("retrieval_unavailable", f"离线知识库也不可用：{exc}", fallback_to="no_evidence")
        return RetrievalResult(decomposed=decomposed, strategy="none")
