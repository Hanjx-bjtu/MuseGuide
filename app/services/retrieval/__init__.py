"""检索层。

P2 阶段的离线知识直出（:mod:`~app.services.retrieval.offline`）保留为
**降级通道**；P3 起的主路径是 Hybrid 检索：

* :mod:`~app.services.retrieval.sparse` —— BM25（自研，零依赖）
* :mod:`~app.services.retrieval.dense` —— 向量检索（嵌入后端可切换）
* :mod:`~app.services.retrieval.fusion` —— RRF / 加权融合
* :mod:`~app.services.retrieval.hybrid` —— 编排 + 元数据过滤 + 策略自适应
"""

from app.services.retrieval.dense import DenseIndex, Embedder, HashEmbedder, build_embedder
from app.services.retrieval.fusion import (
    FusedCandidate,
    FusionStrategy,
    RankedDoc,
    fuse,
    rrf_fuse,
    to_ranked,
    weighted_fuse,
)
from app.services.retrieval.hybrid import (
    EXPERIMENT_MATRIX,
    HybridRetriever,
    RetrievalConfig,
)
from app.services.retrieval.offline import OfflineKnowledgeBase, default_kb, to_evidence
from app.services.retrieval.service import RetrievalResult, retrieve
from app.services.retrieval.sparse import BM25, tokenize

__all__ = [
    # sparse
    "BM25",
    "tokenize",
    # dense
    "DenseIndex",
    "Embedder",
    "HashEmbedder",
    "build_embedder",
    # fusion
    "FusedCandidate",
    "FusionStrategy",
    "RankedDoc",
    "fuse",
    "rrf_fuse",
    "to_ranked",
    "weighted_fuse",
    # hybrid
    "EXPERIMENT_MATRIX",
    "HybridRetriever",
    "RetrievalConfig",
    # offline（降级通道）
    "OfflineKnowledgeBase",
    "default_kb",
    "to_evidence",
    # service 门面
    "RetrievalResult",
    "retrieve",
]
