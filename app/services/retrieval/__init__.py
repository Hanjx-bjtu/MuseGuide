"""检索层。

P2 阶段只有离线知识直出（:mod:`~app.services.retrieval.offline`）；
P3 会加入 Dense / Sparse / Hybrid 与 Query 分解。
"""

from app.services.retrieval.offline import OfflineKnowledgeBase, default_kb, to_evidence

__all__ = ["OfflineKnowledgeBase", "default_kb", "to_evidence"]
