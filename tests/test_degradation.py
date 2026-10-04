"""降级协议测试。

``MVP计划.md`` §8 要求「LLM + 规则映射双保险，规则兜底」。
若降级本身不可测，这条要求就只是口号。
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.degradation import DegradationLog
from app.providers.mock import MockEmbeddingProvider, MockLLMProvider


def test_settings_without_api_key_does_not_raise():
    """ADR-0004：Key 缺失时必须能构造 Settings，只是标记为不可用。"""
    settings = Settings(llm_api_key="")
    assert settings.llm_available is False


def test_deepseek_provider_unavailable_without_key():
    from app.providers.base import LLMError
    from app.providers.deepseek import DeepSeekProvider

    provider = DeepSeekProvider(Settings(llm_api_key=""))
    assert provider.available is False
    with pytest.raises(LLMError):
        provider.complete("hi")


def test_mock_llm_is_deterministic():
    """Mock 必须确定性 —— 否则测试不可复现。"""
    llm = MockLLMProvider()
    prompt = "[用户创作意图]\n我想写一首关于毕业的歌，有点伤感但最后释然。\n[任务]\n设计一组基础和弦"
    assert llm.complete(prompt) == llm.complete(prompt)


def test_mock_llm_returns_parseable_starter_payload():
    import json

    llm = MockLLMProvider()
    out = llm.complete("[创作意图] 毕业歌，伤感但释然\n[任务] 生成起步方案")
    payload = json.loads(out)
    assert {"key", "key_explanation", "tempo", "tempo_explanation", "sections"} <= set(payload)


def test_mock_llm_starter_payload_satisfies_contract():
    """Mock 的产出必须能直接通过 StarterPlan 校验 —— 否则测试会假装通过。"""
    import json

    from app.core import StarterPlan

    llm = MockLLMProvider()
    payload = json.loads(llm.complete("[任务] 生成起步方案，中等偏慢"))
    plan = StarterPlan.model_validate(payload)
    assert len(plan.sections) >= 2
    assert plan.tempo == 82


def test_mock_llm_advice_payload_has_two_or_three_options():
    import json

    from app.core import Advice

    llm = MockLLMProvider()
    payload = json.loads(llm.complete("[任务] 给出 2-3 个修改方向"))
    advice = Advice.model_validate(payload)
    assert 2 <= len(advice.options) <= 3


def test_mock_embedding_is_normalized_and_stable():
    emb = MockEmbeddingProvider()
    v1 = emb.embed_one("小调到大调的情绪转向和弦进行")
    v2 = emb.embed_one("小调到大调的情绪转向和弦进行")
    assert v1 == v2
    assert len(v1) == emb.dim
    assert abs(sum(x * x for x in v1) ** 0.5 - 1.0) < 1e-6


def test_mock_embedding_distinguishes_related_texts():
    """哈希嵌入没有真实语义能力，但相同字符串必须一致、不同字符串必须不同。"""
    emb = MockEmbeddingProvider()
    a = emb.embed_one("和弦")
    b = emb.embed_one("爵士鼓")
    assert a != b


def test_degradation_log_records_events():
    log = DegradationLog()
    assert not log
    log.add("llm_unavailable", "未配置 API Key", fallback_to="rule_intent")
    assert log
    assert len(log) == 1
    assert log.items()[0].kind == "llm_unavailable"
    assert log.items()[0].fallback_to == "rule_intent"
