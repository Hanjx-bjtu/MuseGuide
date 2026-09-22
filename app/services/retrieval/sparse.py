"""BM25 稀疏检索（自研实现，零第三方依赖）。

**为什么不直接用 ``rank_bm25``：** ``MVP计划.md`` §3.7.4 建议用它，但本机
``pip`` 无法联网安装（见计划 R13），而 BM25 的公式本身很短（约 60 行）。
自研带来的额外好处是**可以控制分词与字段加权** —— 音乐领域有大量
精确术语（``ii–V–I``、``Modal Interchange``、``Fm``），通用分词器未必合适。

依据：``MVP计划.md`` §3.7.1（Sparse Retrieval）、§3.7.4（技术选型）
"""

from __future__ import annotations

import math
import re
from collections import Counter

#: BM25 自由参数。``k1`` 控制词频饱和，``b`` 控制长度归一化。
#: 取业界常用默认值（Robertson & Zaragoza）。
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75

#: 拉丁词与和弦符号（``Cmaj7`` / ``G/B`` / ``ii-V-I``）
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9#/+\-]*")
#: 中文字符
_HAN = re.compile(r"[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    """中英混合分词（零依赖）。

    **中文用「单字 + 相邻双字」的组合**，理由：
    * 单字保证召回（「借」「用」都能匹配到「借用和弦」）
    * 双字提供精度（「色彩」比「色」「彩」更有区分度）
    * 不需要外部词典，也不会因为分词错误漏掉音乐术语

    英文与和弦符号按整词处理，并额外保留小写形式以便大小写不敏感匹配。
    """
    if not text:
        return []

    tokens: list[str] = []
    for chunk in _HAN.findall(text):
        tokens.extend(chunk)  # 单字
        tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))  # 双字

    for word in _LATIN.findall(text):
        lowered = word.lower()
        tokens.append(lowered)
        # 和弦符号里的升降号/斜杠是语义的一部分，不做拆分
    return tokens


class BM25:
    """字段加权的 BM25 检索器。

    知识条目有多个字段（标题、标签、正文、通俗层），它们的检索价值不同：
    ``tags`` 命中通常比正文命中更有意义。因此本实现按字段分别建索引，
    再对得分加权求和 —— 这比把字段拼成一个大字符串更可控。
    """

    def __init__(self, k1: float = DEFAULT_K1, b: float = DEFAULT_B) -> None:
        self.k1 = k1
        self.b = b
        self._docs: list[str] = []
        self._field_tokens: dict[str, list[list[str]]] = {}
        self._field_weights: dict[str, float] = {}
        self._df: Counter[str] = Counter()
        self._avg_len: dict[str, float] = {}
        self._n_docs = 0

    def index(
        self,
        doc_ids: list[str],
        fields: dict[str, list[str]],
        field_weights: dict[str, float] | None = None,
    ) -> None:
        """建立索引。

        :param doc_ids: 文档 id 列表（顺序即文档索引）
        :param fields: ``{字段名: [该字段在每个文档中的文本]}``
        :param field_weights: ``{字段名: 权重}``，缺省为 1.0
        """
        self._docs = list(doc_ids)
        self._n_docs = len(doc_ids)
        self._field_weights = dict(field_weights or {})
        self._field_tokens = {}
        self._df = Counter()
        self._avg_len = {}

        for name, texts in fields.items():
            if len(texts) != self._n_docs:
                raise ValueError(f"字段 {name!r} 的文档数({len(texts)})与 doc_ids({self._n_docs})不符")
            token_lists = [tokenize(text) for text in texts]
            self._field_tokens[name] = token_lists
            self._avg_len[name] = (
                sum(len(t) for t in token_lists) / self._n_docs if self._n_docs else 0.0
            )
            # 文档频率按字段累计（用于 IDF）
            for tokens in token_lists:
                self._df.update(set(tokens))

    def _idf(self, term: str) -> float:
        """BM25 的 IDF（带 +0.5 平滑，避免负值）。"""
        df = self._df.get(term, 0)
        return math.log(1 + (self._n_docs - df + 0.5) / (df + 0.5))

    def score(self, query: str, doc_index: int) -> float:
        """单个文档对查询的得分（跨字段加权求和）。"""
        query_terms = tokenize(query)
        if not query_terms or doc_index >= self._n_docs:
            return 0.0

        total = 0.0
        for name, token_lists in self._field_tokens.items():
            weight = self._field_weights.get(name, 1.0)
            if weight == 0.0:
                continue

            tokens = token_lists[doc_index]
            if not tokens:
                continue
            length = len(tokens)
            avg_len = self._avg_len.get(name, 0.0) or 1.0
            freqs = Counter(tokens)

            field_score = 0.0
            for term in query_terms:
                tf = freqs.get(term, 0)
                if tf == 0:
                    continue
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * length / avg_len)
                field_score += self._idf(term) * numerator / denominator
            total += weight * field_score

        return total

    def search(self, query: str, k: int = 20) -> list[tuple[str, float]]:
        """返回 ``[(doc_id, score), ...]``，按得分降序。

        排序键包含 doc_id 作为次要键，保证**同分时结果稳定可复现**
        —— 这是 P6 实验结果可重跑的前提。
        """
        scored = [
            (self._docs[i], self.score(query, i))
            for i in range(self._n_docs)
        ]
        scored = [item for item in scored if item[1] > 0]
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:k]

    @property
    def size(self) -> int:
        return self._n_docs


#: 知识条目的字段权重（供检索层复用）。
#:
#: 依据：标题与标签最能代表条目主题，通俗层服务于零基础用户，
#: 正文信息量最大但噪声也最多。
DEFAULT_FIELD_WEIGHTS: dict[str, float] = {
    "title": 3.0,
    "layman_title": 2.5,
    "tags": 3.0,
    "roman_numerals": 2.0,
    "emotions": 1.5,
    "examples": 1.5,
    "layman_content": 1.0,
    "content": 1.0,
}
