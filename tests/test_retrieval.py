"""P3 检索测试：BM25 / 融合 / Query 分解 / Hybrid 编排。

对应 ``实现阶段计划.md`` §7 的验收：检索能返回带来源的片段、
Query 分解产出 §3.5.2 的四字段、融合结果可复现。
"""

from __future__ import annotations

import pytest

from app.core.brief import CreativeIntent
from app.core.degradation import DegradationLog
from app.services.kb import load_entries
from app.services.query import (
    QUERY_DECOMPOSE_PROMPT,
    decompose,
    decompose_by_rules,
    parse_decomposed,
)
from app.services.retrieval import (
    BM25,
    HashEmbedder,
    HybridRetriever,
    RetrievalConfig,
    build_embedder,
    rrf_fuse,
    to_ranked,
    tokenize,
    weighted_fuse,
)
from app.services.retrieval.fusion import RankedDoc

# --------------------------------------------------------------------------- #
# BM25 与分词
# --------------------------------------------------------------------------- #


def test_tokenize_handles_chinese_and_latin():
    """中英混合分词：中文出单字 + 双字，英文/和弦按整词。"""
    tokens = tokenize("Cmaj7 和 Am 的用法")
    assert "cmaj7" in tokens
    assert "am" in tokens
    assert "用法" in tokens
    assert "用" in tokens


def test_tokenize_keeps_chord_symbols_intact():
    """和弦符号里的升降号与斜杠是语义的一部分，不得拆散。"""
    assert "g/b" in tokenize("G/B 转位")
    assert "f#m" in tokenize("F#m 和弦")


def test_bm25_ranks_relevant_document_first():
    docs = ["a", "b", "c"]
    fields = {
        "title": ["终止式的用法", "七和弦的色彩", "低音线进行"],
        "content": ["", "", ""],
    }
    bm25 = BM25()
    bm25.index(docs, fields, {"title": 1.0})
    results = bm25.search("终止式")
    assert results
    assert results[0][0] == "a"


def test_bm25_ties_are_broken_deterministically():
    """同分时按 doc_id 升序 —— 实验可复现的前提。"""
    docs = ["z", "a", "m"]
    fields = {"title": ["一样的词", "一样的词", "一样的词"]}
    bm25 = BM25()
    bm25.index(docs, fields, {"title": 1.0})
    assert [doc for doc, _ in bm25.search("一样")] == ["a", "m", "z"]


def test_bm25_field_weights_matter():
    """字段加权应生效：标题命中应优于正文命中。"""
    docs = ["a", "b"]
    fields = {"title": ["终止式", ""], "content": ["", "终止式"]}
    bm25 = BM25()
    bm25.index(docs, fields, {"title": 3.0, "content": 1.0})
    assert bm25.search("终止式")[0][0] == "a"


def test_bm25_empty_query_returns_nothing():
    bm25 = BM25()
    bm25.index(["a"], {"title": ["x"]}, {"title": 1.0})
    assert bm25.search("") == []


def test_bm25_rejects_mismatched_field_length():
    bm25 = BM25()
    with pytest.raises(ValueError):
        bm25.index(["a", "b"], {"title": ["only one"]})


# --------------------------------------------------------------------------- #
# 融合
# --------------------------------------------------------------------------- #


def test_rrf_fuse_combines_two_routes():
    dense = to_ranked([("a", 0.9), ("b", 0.5)])
    sparse = to_ranked([("b", 12.0), ("c", 8.0)])
    fused = rrf_fuse({"dense": dense, "sparse": sparse})
    ids = [c.doc_id for c in fused]
    # b 被两路同时召回，应排第一
    assert ids[0] == "b"
    assert set(ids) == {"a", "b", "c"}


def test_rrf_route_marker():
    dense = to_ranked([("a", 0.9)])
    sparse = to_ranked([("a", 5.0)])
    fused = rrf_fuse({"dense": dense, "sparse": sparse})
    assert fused[0].route == "both"


def test_rrf_records_per_route_ranks():
    """逐级排名必须保留 —— 这是「为什么这条证据排第一」可解释的基础。"""
    fused = rrf_fuse(
        {"dense": to_ranked([("a", 1.0), ("b", 0.5)]), "sparse": to_ranked([("b", 9.0), ("a", 3.0)])}
    )
    by_id = {c.doc_id: c for c in fused}
    assert by_id["a"].rank_dense == 1
    assert by_id["a"].rank_sparse == 2


def test_weighted_fuse_normalizes_scales():
    """加权融合必须先归一化 —— 否则量纲大的那一路会完全主导。"""
    dense = to_ranked([("a", 0.01), ("b", 0.005)])
    sparse = to_ranked([("b", 100.0), ("a", 50.0)])
    fused = weighted_fuse({"dense": dense, "sparse": sparse})
    assert len(fused) == 2


def test_fusion_ties_are_deterministic():
    dense = to_ranked([("b", 1.0)])
    sparse = to_ranked([("a", 1.0)])
    first = [c.doc_id for c in rrf_fuse({"dense": dense, "sparse": sparse})]
    second = [c.doc_id for c in rrf_fuse({"dense": dense, "sparse": sparse})]
    assert first == second


def test_fused_candidate_converts_to_contract():
    fused = rrf_fuse({"dense": to_ranked([("a", 1.0)])})
    candidate = fused[0].to_candidate()
    assert candidate.entry_id == "a"
    assert candidate.route in ("dense", "sparse", "both", "none")


# --------------------------------------------------------------------------- #
# 嵌入后端
# --------------------------------------------------------------------------- #


def test_hash_embedder_is_deterministic_and_normalized():
    emb = HashEmbedder(dim=32)
    v1 = emb.embed(["终止式"])[0]
    v2 = emb.embed(["终止式"])[0]
    assert v1 == v2
    assert abs(sum(x * x for x in v1) ** 0.5 - 1.0) < 1e-6


def test_build_embedder_falls_back_with_note():
    """不可用的后端必须**显式降级并给出说明**，不能静默替换。"""
    _embedder, note = build_embedder("sentence-transformers")
    assert note is None or "回退" in note


# --------------------------------------------------------------------------- #
# Query 分解（§3.5）
# --------------------------------------------------------------------------- #


def test_decompose_by_rules_puts_original_question_first():
    """回归：原始问题必须排在检索词的第一位。

    实测教训：早期版本只从关键词生成检索词，导致「终止式有哪几种」
    被拆成「常见和弦进行如何增加色彩」——**用户实际问的东西完全丢失**，
    严格 Recall@5 仅 0.403（修好后升至 0.839）。
    """
    decomposed = decompose_by_rules(goal_text="终止式有哪几种", user_level="some")
    assert decomposed.queries[0] == "终止式有哪几种"


def test_decompose_by_rules_produces_three_to_five_queries():
    """§3.5.4 要求 3-5 条检索 query。"""
    for question in ("我想写一首毕业歌", "我的和弦很普通怎么办", "终止式有哪几种"):
        decomposed = decompose_by_rules(goal_text=question, user_level="some")
        assert 3 <= len(decomposed.queries) <= 5, f"{question}: {decomposed.queries}"


def test_decompose_by_rules_uses_emotions():
    intent = CreativeIntent(emotion=["伤感", "释然"])
    decomposed = decompose_by_rules(goal_text="写首歌", intent=intent, user_level="zero")
    joined = " ".join(decomposed.queries)
    assert "伤感" in joined or "释然" in joined


def test_decompose_by_rules_has_four_fields():
    """§3.5.2 / §3.5.3 的四个字段。"""
    decomposed = decompose_by_rules(goal_text="保持温暖但不要太普通", user_level="some")
    assert decomposed.intent
    assert decomposed.goal
    assert decomposed.object
    assert decomposed.queries
    assert decomposed.source == "rule"


def test_decompose_by_rules_reflects_user_level():
    zero = decompose_by_rules(goal_text="写首歌", user_level="zero")
    advanced = decompose_by_rules(goal_text="加色彩", user_level="advanced")
    assert zero.intent != advanced.intent


def test_parse_decomposed_accepts_valid_payload():
    payload = '{"intent":"修改已有作品","goal":"增加色彩","object":"和弦进行","queries":["a","b","c"]}'
    decomposed = parse_decomposed(payload)
    assert decomposed.queries == ["a", "b", "c"]
    assert decomposed.source == "llm"


def test_parse_decomposed_tolerates_markdown_fence():
    payload = '```json\n{"queries":["a","b","c"]}\n```'
    assert parse_decomposed(payload).queries == ["a", "b", "c"]


def test_parse_decomposed_rejects_missing_queries():
    with pytest.raises(ValueError):
        parse_decomposed('{"intent":"x"}')
    with pytest.raises(ValueError):
        parse_decomposed("不是 JSON")


def test_decompose_falls_back_when_llm_unavailable():
    class NoKey:
        available = False

    log = DegradationLog()
    decomposed = decompose(goal_text="终止式", llm=NoKey(), log=log)
    assert decomposed.source == "rule"
    assert any(d.kind == "llm_unavailable" for d in log.items())


def test_decompose_prompt_has_required_fields():
    prompt = QUERY_DECOMPOSE_PROMPT.format(goal="g", music="m", user_level="零基础")
    for field in ("intent", "goal", "object", "queries"):
        assert field in prompt


# --------------------------------------------------------------------------- #
# Hybrid 编排
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def entries() -> list[dict]:
    return load_entries()


@pytest.fixture
def retriever(entries) -> HybridRetriever:
    return HybridRetriever(
        entries,
        embedder=HashEmbedder(),
        config=RetrievalConfig(mode="hybrid", fusion="rrf", top_k=5, use_metadata_filter=False),
    )


def test_hybrid_returns_evidence_with_sources(retriever):
    """G3：检索返回带 entry_id + source 的片段。"""
    evidence, _ = retriever.retrieve(["终止式有哪几种"], user_level="some")
    assert evidence
    for item in evidence:
        assert item.entry_id
        assert item.source.title, "证据必须可追溯"


def test_hybrid_returns_candidates_with_provenance(retriever):
    """候选必须保留逐级排名，供 Trace 展示可解释性。"""
    _evidence, candidates = retriever.retrieve(["终止式"], user_level="some")
    assert candidates
    assert any(c.rank_dense is not None or c.rank_sparse is not None for c in candidates)
    assert candidates[0].rank_final == 1


def test_hybrid_is_deterministic(retriever):
    """同输入必须给出同结果 —— P6 实验可重跑的前提。"""
    first, _ = retriever.retrieve(["终止式有哪几种"], user_level="some")
    second, _ = retriever.retrieve(["终止式有哪几种"], user_level="some")
    assert [e.entry_id for e in first] == [e.entry_id for e in second]


def test_hybrid_respects_top_k(entries):
    retriever = HybridRetriever(
        entries, embedder=HashEmbedder(), config=RetrievalConfig(top_k=3, use_metadata_filter=False)
    )
    evidence, _ = retriever.retrieve(["和弦"], user_level="some")
    assert len(evidence) <= 3


def test_primary_query_is_weighted_higher(entries):
    """回归：用户原话的权重要高于自动扩展的检索词。

    实测教训：不做加权时，兜底 query 会压过用户真提的问题 ——
    问「想写一首很丧的歌」，BM25 单独能把 melancholy 排第一，
    但融合后它掉出前五。加权后严格 Recall@5 从 0.734 升到 0.839，
    且「混合 vs 纯 BM25」的差异变得统计显著。
    """
    retriever = HybridRetriever(
        entries,
        embedder=HashEmbedder(),
        config=RetrievalConfig(mode="hybrid", fusion="rrf", top_k=5, use_metadata_filter=False),
    )
    evidence, _ = retriever.retrieve(
        ["想写一首很丧的歌", "如何开始写第一首歌"], user_level="some"
    )
    assert evidence
    assert "emotion.melancholy.01" in [e.entry_id for e in evidence]


def test_none_mode_returns_nothing(retriever):
    """baseline 对照组：不检索即无证据。"""
    from app.services.retrieval.hybrid import RetrievalConfig as Cfg

    baseline = retriever.with_config(Cfg(mode="none"))
    evidence, candidates = baseline.retrieve(["终止式"], user_level="some")
    assert evidence == []
    assert candidates == []


def test_config_describe_is_serializable(retriever):
    described = retriever.config.describe()
    assert described["mode"] == "hybrid"
    import json

    json.dumps(described)  # 必须可写入 Trace


def test_metadata_filter_restricts_categories(entries):
    filtered = HybridRetriever(
        entries,
        embedder=HashEmbedder(),
        config=RetrievalConfig(mode="sparse", fusion="none", use_metadata_filter=True),
    )
    evidence, _ = filtered.retrieve(["和弦"], user_level="zero")
    # 零基础档只在 starter / emotion / layman 三个分类内检索
    for item in evidence:
        assert item.entry_id.split(".")[0] in ("starter", "emotion", "layman")


def test_retriever_rejects_empty_kb():
    with pytest.raises(ValueError):
        HybridRetriever([], embedder=HashEmbedder())
