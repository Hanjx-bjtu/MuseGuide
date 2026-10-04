"""Dense 向量检索（可切换嵌入后端）。

**嵌入后端按 ADR-0008 设计为可替换**，顺序为
``ollama`` → ``sentence-transformers(BGE)`` → ``hash``：

* 本机 ``LongPathsEnabled = 0``，``sentence-transformers``（依赖 torch）
  安装可能因路径长度失败
* 未检测到本地 Ollama 服务
* ``hash`` 是确定性哈希嵌入，**仅用于让链路结构正确、可测试**，
  其检索指标不代表真实语义能力

**不引入 Chroma**：知识库只有 29 条，numpy 暴力检索足够
（计划 §11 R11「避免过度工程」）。这也省掉了一个重依赖。
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

#: 默认嵌入维度（哈希后端）
HASH_DIM = 128

_HAN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9#/+\-]*")


@runtime_checkable
class Embedder(Protocol):
    """嵌入后端协议。"""

    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


def _tokens(text: str) -> list[str]:
    """与稀疏检索一致的分词策略（单字 + 双字 + 拉丁词）。"""
    if not text:
        return []
    out: list[str] = []
    for chunk in _HAN.findall(text):
        out.extend(chunk)
        out.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    out.extend(w.lower() for w in _LATIN.findall(text))
    return out


class HashEmbedder:
    """确定性哈希嵌入（零依赖兜底）。

    ⚠️ **不具备真实语义能力**，仅保证：
    * 相同文本得到相同向量（可复现）
    * 有共同词元的文本相似度更高（比随机向量有意义）
    * 链路结构正确，测试无需网络

    使用它产出的检索指标**不得写进结论报告**（ADR-0008）。
    """

    name = "hash"

    def __init__(self, dim: int = HASH_DIM) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _tokens(text):
            digest = hashlib.md5(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class SentenceTransformerEmbedder:
    """本地 BGE 嵌入（需 ``sentence-transformers``）。"""

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5") -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self.name = f"st:{model_name}"
        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vectors]


class OllamaEmbedder:
    """Ollama 本地嵌入（HTTP 调用，无需 torch）。"""

    def __init__(self, model: str = "nomic-embed-text", base_url: str = "http://127.0.0.1:11434") -> None:
        self.name = f"ollama:{model}"
        self._model = model
        self._base_url = base_url.rstrip("/")
        self.dim = 0  # 首次调用后确定

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx  # noqa: PLC0415

        vectors: list[list[float]] = []
        for text in texts:
            response = httpx.post(
                f"{self._base_url}/api/embeddings",
                json={"model": self._model, "prompt": text},
                timeout=30.0,
            )
            response.raise_for_status()
            vector = response.json()["embedding"]
            self.dim = len(vector)
            vectors.append([float(v) for v in vector])
        return vectors


class _CachedEmbedder:
    """带内存缓存的嵌入包装。

    **为什么需要：** 建索引与查询都会调用嵌入，而知识库在运行期不变。
    缓存把「29 条文档 × 每次查询」的重复计算降为一次。
    """

    def __init__(self, inner: Embedder) -> None:
        self._inner = inner
        self._cache: dict[str, list[float]] = {}
        self.name = inner.name
        self.dim = inner.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in texts if t not in self._cache]
        if missing:
            for text, vector in zip(missing, self._inner.embed(missing)):
                self._cache[text] = vector
        self.dim = self._inner.dim or self.dim
        return [self._cache[t] for t in texts]


def build_embedder(backend: str = "auto", model: str = "BAAI/bge-small-zh-v1.5") -> tuple[Embedder, str | None]:
    """按可用性构造嵌入后端。

    :return: ``(embedder, degradation_note)``；``degradation_note`` 非空表示发生了降级，
        调用方应写入 ``Trace.degradation`` 并**在报告中标注**。
    """
    if backend in ("ollama", "auto"):
        try:
            embedder = OllamaEmbedder(model="nomic-embed-text")
            embedder.embed(["探测"])  # 探活
            return _CachedEmbedder(embedder), None if backend == "ollama" else None
        except Exception:  # noqa: BLE001 - Ollama 不可用时回退
            if backend == "ollama":
                return _CachedEmbedder(HashEmbedder()), "ollama 不可用，已回退哈希嵌入"

    if backend in ("sentence-transformers", "st", "auto"):
        try:
            return _CachedEmbedder(SentenceTransformerEmbedder(model)), None
        except Exception as exc:  # noqa: BLE001 - 未安装或缺模型
            if backend in ("sentence-transformers", "st"):
                return _CachedEmbedder(HashEmbedder()), f"sentence-transformers 不可用（{exc}），已回退哈希嵌入"

    return (
        _CachedEmbedder(HashEmbedder()),
        None if backend == "hash" else "未找到可用的语义嵌入后端，已回退哈希嵌入（检索指标不代表真实水平）",
    )


@dataclass
class DenseHit:
    doc_id: str
    score: float


class DenseIndex:
    """暴力向量检索。

    29 条知识库下暴力检索是**正确选择**：构建零成本、结果精确、
    无近似索引的调参负担。计划 §11 R11 明确「库 < 5000 条时 numpy 暴力检索足够」。
    """

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._ids: list[str] = []
        self._vectors: list[list[float]] = []

    def index(self, doc_ids: list[str], texts: list[str]) -> None:
        self._ids = list(doc_ids)
        self._vectors = self._embedder.embed(texts) if texts else []

    def search(self, query: str, k: int = 20) -> list[DenseHit]:
        if not self._ids:
            return []
        query_vector = self._embedder.embed([query])[0]

        scored: list[DenseHit] = []
        for doc_id, vector in zip(self._ids, self._vectors):
            scored.append(DenseHit(doc_id=doc_id, score=_cosine(query_vector, vector)))

        # 次级排序键 doc_id 保证同分时稳定（实验可复现）
        scored.sort(key=lambda hit: (-hit.score, hit.doc_id))
        return scored[:k]

    @property
    def size(self) -> int:
        return len(self._ids)


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (norm_a * norm_b)
