"""离线知识直出（P2 阶段的临时证据来源）。

**为什么需要它：** ``实现阶段计划.md`` 刻意把 M2（起步方案）排在 M1（检索）之前，
让零基础链路不被检索进度阻塞。因此 P2 阶段用一个**规则打分**的离线检索器
提供 ``[检索到的知识]`` 段，P3.10 再替换为真实的 Hybrid 检索。

它同时是检索不可用时的**降级路径**（``retrieval_unavailable``）——
即使 P3 完成，这条路径也会保留，因为向量库/嵌入可能加载失败。
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import PROJECT_ROOT, Settings, get_settings
from app.core.evidence import Candidate, Evidence, EvidenceSource
from app.services.kb import load_entries


def _score(entry: dict, terms: list[str], emotions: list[str]) -> float:
    """规则打分：情绪标签命中权重最高，其次标题，再次 tags 与正文。"""
    score = 0.0
    entry_emotions = entry.get("emotions") or []
    title = (entry.get("title") or "") + (entry.get("layman_title") or "")
    tags = " ".join(entry.get("tags") or [])
    body = (entry.get("content") or "") + (entry.get("layman_content") or "")

    for emo in emotions:
        if emo in entry_emotions:
            score += 3.0
    for term in terms:
        if not term:
            continue
        if term in title:
            score += 2.0
        if term in tags:
            score += 1.5
        if term in body:
            score += 0.5

    # 分类先验：起步阶段更需要起步与情绪类知识
    category = entry.get("category")
    if category in ("starter", "emotion"):
        score += 1.0
    elif category == "layman":
        score += 0.5

    # priority 作为轻微加权（§3.6.2 的 priority 字段用途之一）
    score += (entry.get("priority") or 3) * 0.1
    return score


def to_evidence(entry: dict, score: float, from_query: str | None = None) -> Evidence:
    """知识条目 → :class:`~app.core.evidence.Evidence`。"""
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
        score=round(score, 4),
        from_query=from_query,
        candidate=Candidate(entry_id=entry["id"], score=round(score, 4), route="none"),
    )


class OfflineKnowledgeBase:
    """基于关键词与情绪标签打分的离线知识检索器。

    零第三方依赖，不加载向量模型，因此**在任何环境下都能工作** ——
    这正是它作为降级路径的价值。
    """

    def __init__(self, kb_dir: Path | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._kb_dir = kb_dir or self._settings.kb_dir
        self._entries: list[dict] | None = None

    @property
    def entries(self) -> list[dict]:
        if self._entries is None:
            self._entries = load_entries(self._kb_dir)
        return self._entries

    def search(
        self,
        queries: list[str],
        *,
        emotions: list[str] | None = None,
        top_k: int = 5,
        categories: list[str] | None = None,
    ) -> list[Evidence]:
        """按 query 与情绪检索，返回排序后的证据。

        :param queries: 检索词列表（P2 阶段由意图直接生成，P3 由 Query 分解生成）
        :param emotions: 情绪标签，命中权重最高
        :param categories: 限定分类（metadata filter 的雏形）
        """
        emotions = emotions or []
        terms: list[str] = []
        for q in queries:
            if not q:
                continue
            terms.append(q.strip())
            # 中文长句拆成短词做包含匹配（零依赖的简易切分）
            terms.extend(w for w in q.replace("的", " ").split() if len(w) >= 2)

        scored: list[tuple[float, dict, str | None]] = []
        for entry in self.entries:
            if categories and entry.get("category") not in categories:
                continue
            best_score = 0.0
            best_query: str | None = None
            for query in queries or [""]:
                s = _score(entry, terms, emotions)
                if s > best_score:
                    best_score, best_query = s, query or None
            if best_score > 0:
                scored.append((best_score, entry, best_query))

        # 稳定排序：先按分数降序，再按 id 升序（保证可复现）
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        return [to_evidence(entry, score, query) for score, entry, query in scored[:top_k]]

    def search_for_intent(self, intent, raw_text: str = "", top_k: int = 5) -> list[Evidence]:
        """按 :class:`~app.core.brief.CreativeIntent` 生成检索词并查询。

        P2 阶段的检索词是**规则生成**的；P3 会替换为 LLM 驱动的 Query 分解，
        但本方法作为降级路径保留。
        """
        queries: list[str] = []
        if intent.style:
            queries.append(f"{intent.style}风格常见和声框架")
        if intent.emotion:
            queries.append("".join(intent.emotion) + "的情绪实现方式")
        if intent.harmony_needs:
            queries.extend(intent.harmony_needs)
        if intent.tempo_feel:
            queries.append(f"{intent.tempo_feel}速度的和声节奏")
        if not queries:
            queries.append(raw_text or "如何开始写一首歌")

        categories = ["starter", "emotion", "layman", "harmony", "examples"]
        return self.search(queries, emotions=intent.emotion, top_k=top_k, categories=categories)


def default_kb() -> OfflineKnowledgeBase:
    return OfflineKnowledgeBase()
