"""P1 意图映射测试 —— 阶段门 G1 的核心断言。

对应 ``实现阶段计划.md`` §5.3：

* §3.2.2 的九条规则样例**全部命中**
* 断网 / 无 Key 时链路自动回落规则层，结果仍可用
* 选择式输入优先于 LLM 推断
"""

from __future__ import annotations

import json

import pytest

from app.core.brief import CreativeIntent, SelectionInput
from app.core.degradation import DegradationLog
from app.providers.mock import MockLLMProvider
from app.services.intent import (
    INTENT_PROMPT_TEMPLATE,
    RULES_BY_NAME,
    RULE_MAP,
    concepts_for_rule,
    default_intent,
    intent_from_selections,
    map_by_rules,
    merge_intents,
    parse_llm_intent,
    resolve_intent,
)

# --------------------------------------------------------------------------- #
# §3.2.2 映射规则表：九条规则全部就位且可命中
# --------------------------------------------------------------------------- #

#: (规则名, 触发它的用户原话, 期望映射到的音乐概念)
RULE_CASES = [
    ("听起来很普通", "我的和弦听起来很普通", ("缺乏和声色彩", "常见进行")),
    ("感觉不对劲", "这段总感觉不对劲", ("可能存在不协和", "功能逻辑问题")),
    ("想要更温暖", "我想要更温暖一点", ("倾向大调和声", "纯五度关系")),
    ("想要更伤感", "想要更伤感一些", ("倾向小调", "借用和弦", "下行旋律")),
    ("想要高潮感", "副歌想要有高潮感", ("Tension build-up", "力度变化", "音域升高")),
    ("想要收尾的感觉", "结尾想要收尾的感觉", ("Cadence", "终止式")),
    ("有点伤感但最后释然", "有点伤感但最后释然", ("小调→大调转向", "下行→上行旋律", "张力→释放")),
    ("温柔", "我想要温柔一点的感觉", ("大调", "慢速", "柔和音色")),
    ("激昂", "想要激昂一点的歌", ("大调", "快速", "强力度", "高音域")),
]


def test_rule_map_matches_mvp_section_3_2_2():
    """§3.2.2 的映射表必须逐条落为规则 —— 一条都不能少。"""
    expected = {name for name, _text, _concepts in RULE_CASES}
    assert expected == set(RULES_BY_NAME), "规则表与 §3.2.2 不一致"


@pytest.mark.parametrize("name,text,concepts", RULE_CASES, ids=[c[0] for c in RULE_CASES])
def test_rule_hits_and_maps_concepts(name, text, concepts):
    """九条规则逐条：能命中，且映射到 §3.2.2 指定的音乐概念。"""
    intent = map_by_rules(text)
    assert name in intent.matched_rules, f"{text!r} 未命中规则 {name!r}"
    assert concepts_for_rule(name) == concepts


@pytest.mark.parametrize("name,text,concepts", RULE_CASES, ids=[c[0] for c in RULE_CASES])
def test_every_rule_has_trigger_keywords(name, text, concepts):
    """每条规则都必须有至少一个关键词，且该关键词出现在测试原话中。"""
    rule = RULES_BY_NAME[name]
    assert rule.keywords, f"规则 {name!r} 没有关键词，永远不会命中"
    assert any(kw in text for kw in rule.keywords)


def test_rule_layer_needs_no_llm():
    """规则层必须能完全脱离 LLM 工作（§8「规则兜底」）。"""
    intent = resolve_intent("我想写一首关于毕业的歌，有点伤感但最后释然", llm=None)
    assert intent.emotion, "规则层未能给出任何情绪方向"
    assert "伤感" in intent.emotion and "释然" in intent.emotion


# --------------------------------------------------------------------------- #
# 端到端：§3.1.2 的零基础示例
# --------------------------------------------------------------------------- #


def test_mvp_example_intent_is_fully_resolved():
    """§3.1.2 的示例意图应解析出情绪，并给出调性倾向。

    注意：示例原文**没有提到风格和速度**，因此这两项在规则层应为空 ——
    这正是 ``_needs_llm`` 要触发 LLM 补充的场景（见 test_style_and_tempo_gaps_trigger_llm）。
    """
    intent = resolve_intent("我想写一首关于毕业的歌，有点伤感但最后是释然的感觉。")
    assert set(intent.emotion) >= {"伤感", "释然"}
    assert intent.key_preference is not None
    assert intent.matched_rules, "必须记录命中了哪些规则"


def test_compound_emotion_rule_wins_key_preference():
    """回归：泛化规则不得覆盖具体规则给出的调性倾向。

    「有点伤感但最后释然」是复合意图，调性应落到「大调（释然感）」；
    若被「想要更伤感」抢先，就会错误地给出「小调（忧伤感）」。
    """
    intent = map_by_rules("我想写一首关于毕业的歌，有点伤感但最后释然。")
    assert intent.key_preference == "大调（释然感）"


def test_style_and_tempo_gaps_trigger_llm():
    """回归：规则层命中多条规则但 style/tempo 为空时，仍必须调用 LLM 补齐。

    曾经的 bug 是只看命中数：命中 2 条就跳过 LLM，
    结果 style 与 tempo_feel 永远为空，下游只能吃默认值。
    """
    from app.services.intent import _needs_llm

    rule_intent = map_by_rules("有点伤感但最后释然")
    assert len(rule_intent.matched_rules) >= 2, "前提：该输入命中多条规则"
    assert rule_intent.style is None, "前提：规则层无法给出风格"
    assert _needs_llm(rule_intent) is True, "留下了空白就必须调用 LLM"


def test_tempo_keyword_precedence():
    """回归：最具体的速度词优先。

    「中等偏慢」含有裸字「慢」，若按关键词表原顺序匹配会被误判为「很慢」。
    """
    assert map_by_rules("速度中等偏慢").tempo_feel == "中等偏慢"
    assert map_by_rules("速度偏慢一点").tempo_feel == "中等偏慢"
    assert map_by_rules("想要很慢的感觉").tempo_feel == "很慢"
    assert map_by_rules("速度中等").tempo_feel == "中等"


def test_graduation_example_matches_mvp_expectation():
    """§3.3.4 的 Prompt 展示了期望结果：emotion=伤感→释然、style=民谣、tempo=中等偏慢。"""
    intent = map_by_rules("我想写一首关于毕业的歌，有点伤感但最后释然。风格偏民谣，速度中等偏慢。")
    assert "伤感" in intent.emotion and "释然" in intent.emotion
    assert intent.style == "民谣"
    assert intent.tempo_feel == "中等偏慢"


def test_emotions_are_restricted_to_controlled_vocabulary():
    """自由文本里的情绪词若不在受控词表内，不得污染输出。"""
    intent = map_by_rules("我想要一种很玄妙很莫测的感觉")
    for emo in intent.emotion:
        from app.core.options import emotion_option

        assert emotion_option(emo), f"{emo!r} 不在受控词表内"


# --------------------------------------------------------------------------- #
# LLM 层：解析与严格字段约束（§3.2.3）
# --------------------------------------------------------------------------- #


def test_parse_llm_intent_extracts_five_fields():
    payload = json.dumps(
        {
            "emotion": ["伤感", "释然"],
            "style": "民谣",
            "tempo_feel": "中等偏慢",
            "key_preference": "大调（释然感）",
            "harmony_needs": ["需要情绪转折"],
        },
        ensure_ascii=False,
    )
    intent = parse_llm_intent(payload)
    assert intent.emotion == ["伤感", "释然"]
    assert intent.style == "民谣"
    assert intent.tempo_feel == "中等偏慢"
    assert intent.key_preference == "大调（释然感）"
    assert intent.harmony_needs == ["需要情绪转折"]
    assert intent.source == "llm"


def test_parse_llm_intent_strips_markdown_fence():
    """模型常把 JSON 包在 ```json 围栏里，必须能解析。"""
    payload = '```json\n{"emotion": ["温暖"], "style": "流行"}\n```'
    intent = parse_llm_intent(payload)
    assert intent.emotion == ["温暖"]
    assert intent.style == "流行"


def test_parse_llm_intent_ignores_unknown_keys():
    """模型自由发挥的字段不得进入契约。"""
    payload = json.dumps({"emotion": ["温暖"], "bpm": 999, "vibe": "chill"}, ensure_ascii=False)
    intent = parse_llm_intent(payload)
    assert not hasattr(intent, "bpm")
    assert not hasattr(intent, "vibe")


def test_parse_llm_intent_rejects_non_json():
    with pytest.raises(ValueError):
        parse_llm_intent("我很乐意帮你创作一首歌！")
    with pytest.raises(ValueError):
        parse_llm_intent("")


def test_prompt_template_contains_required_fields():
    """§3.2.3 的 prompt 必须要求五个字段。"""
    prompt = INTENT_PROMPT_TEMPLATE.format(user_input="测试")
    for field in ("emotion", "style", "tempo_feel", "key_preference", "harmony_needs"):
        assert field in prompt


# --------------------------------------------------------------------------- #
# 降级路径：G1 要求「断网自动回落规则层」
# --------------------------------------------------------------------------- #


class _BrokenLLM:
    """模拟 LLM 服务不可达。"""

    name = "broken"
    available = True

    def complete(self, prompt: str, *, system=None, json_mode=False) -> str:
        raise RuntimeError("connection refused")


class _BadJSONLLM:
    name = "bad-json"
    available = True

    def complete(self, prompt: str, *, system=None, json_mode=False) -> str:
        return "好的！我来帮你写一首歌～"


def test_llm_unavailable_falls_back_to_rules_and_logs():
    log = DegradationLog()
    intent = resolve_intent("我想写一首歌", llm=MockLLMProvider(), log=log)
    # Mock 可用，不应记录 llm_unavailable
    assert "llm_unavailable" not in [d.kind for d in log.items()]


def test_llm_failure_degrades_to_rule_layer():
    """LLM 调用抛异常 → 降级到规则层，链路不中断。"""
    log = DegradationLog()
    intent = resolve_intent("我想写一首关于毕业的歌，有点伤感但最后释然", llm=_BrokenLLM(), log=log)
    assert any(d.kind == "llm_failed" for d in log.items())
    assert set(intent.emotion) >= {"伤感", "释然"}, "降级后仍必须给出可用结果"


def test_llm_parse_failure_degrades_and_keeps_result():
    log = DegradationLog()
    intent = resolve_intent("我想写一首关于毕业的歌，有点伤感但最后释然", llm=_BadJSONLLM(), log=log)
    assert any(d.kind == "parse_failed" for d in log.items())
    assert intent.emotion, "解析失败后仍必须有结果"


def test_unavailable_llm_logs_llm_unavailable():
    """配置了 provider 但 available=False（无 Key）→ 记 llm_unavailable。"""

    class _NoKeyLLM:
        name = "no-key"
        available = False

        def complete(self, *a, **k):  # pragma: no cover - 不应被调用
            raise AssertionError("available=False 时不应调用 complete()")

    log = DegradationLog()
    intent = resolve_intent("随便写点什么", llm=_NoKeyLLM(), log=log)
    assert any(d.kind == "llm_unavailable" for d in log.items())
    assert intent.style is not None


# --------------------------------------------------------------------------- #
# 选择层与合并优先级（P1.4）
# --------------------------------------------------------------------------- #


def test_selection_overrides_llm_inference():
    """选择式输入优先于 LLM 推断 —— 用户能覆盖系统的猜测。"""
    llm_says = parse_llm_intent(json.dumps({"style": "电子", "tempo_feel": "中等"}, ensure_ascii=False))
    selection = SelectionInput(style="民谣", tempo_label="中等偏慢，像散步")
    merged = merge_intents(intent_from_selections(selection), llm_says)
    assert merged.style == "民谣", "选择应覆盖 LLM 推断"
    assert merged.tempo_feel == "中等偏慢"


def test_selection_parses_compound_emotion():
    """§3.1.2 的情绪选择写作「伤感→释然」。"""
    intent = intent_from_selections(SelectionInput(emotion="伤感→释然"))
    assert intent.emotion == ["伤感", "释然"]


def test_selection_accepts_ui_label_for_tempo():
    """用户传回的是界面文案（带「像散步」），必须能识别。"""
    intent = intent_from_selections(SelectionInput(tempo_label="中等偏慢，像散步"))
    assert intent.tempo_feel == "中等偏慢"


def test_selections_alone_are_sufficient():
    """零基础用户什么都不写、只做选择，也必须能得到方向（§8 兜底）。"""
    intent = resolve_intent(
        "",
        SelectionInput(emotion="温柔", style="民谣", tempo_label="很慢，像翻相册"),
    )
    assert intent.emotion == ["温柔"]
    assert intent.style == "民谣"
    assert intent.tempo_feel == "很慢"


def test_empty_input_falls_back_to_default():
    """完全空输入 → 默认层，且留痕。"""
    log = DegradationLog()
    intent = resolve_intent("", None, log=log)
    assert intent.source == "default"
    assert intent.emotion and intent.style and intent.tempo_feel
    assert any(d.kind == "intent_fallback" for d in log.items())


def test_default_intent_is_complete():
    d = default_intent("x")
    assert d.emotion and d.style and d.tempo_feel and d.key_preference and d.harmony_needs


def test_resolve_intent_is_deterministic():
    """无 LLM 时同一输入必须给出同一结果（否则测试不可复现）。"""
    text = "我想写一首关于毕业的歌，有点伤感但最后释然"
    assert resolve_intent(text) == resolve_intent(text)


def test_resolve_intent_end_to_end_with_mock_llm():
    """接入 Mock LLM 时链路仍完整且产出合法契约。"""
    intent = resolve_intent("我想要一种朦胧梦幻的感觉", llm=MockLLMProvider())
    assert intent.emotion, "即使 LLM 信息稀疏也必须有情绪方向"
    assert intent.model_dump_json()  # 可序列化，便于写入 Trace


# --------------------------------------------------------------------------- #
# 情绪收敛（回归）
# --------------------------------------------------------------------------- #


def test_emotion_list_is_capped():
    """回归：情绪不得无限累积。

    曾经的 bug：Mock 扫描整个 prompt（含 §3.2.3 模板里列举的「忧郁、温暖、激昂」），
    导致每次调用都返回 5 个情绪；合并层又照单全收。
    情绪超过 3 个后彼此抵消，无法指导创作，因此必须截断。
    """
    from app.services.intent import MAX_EMOTIONS, merge_intents

    many = CreativeIntent(emotion=["伤感", "释然", "温暖", "忧郁", "激昂"])
    merged = merge_intents(many)
    assert len(merged.emotion) == MAX_EMOTIONS


def test_mock_llm_does_not_leak_template_vocabulary():
    """回归：Mock 只能看用户原文，不能把 Prompt 模板里的示例词当用户情绪。"""
    llm = MockLLMProvider()
    prompt = INTENT_PROMPT_TEMPLATE.format(user_input="我想写一首毕业歌")
    intent = parse_llm_intent(llm.complete(prompt, json_mode=True))
    # 模板里列举了「忧郁、温暖、激昂」，它们都不得出现
    assert "忧郁" not in intent.emotion
    assert "激昂" not in intent.emotion
    assert intent.emotion == ["温暖"], "无情绪词时应回落到中性默认"


def test_mock_llm_reflects_actual_user_emotions():
    """Mock 必须真实反映用户输入的情绪，而不是恒定输出。"""
    llm = MockLLMProvider()
    prompt = INTENT_PROMPT_TEMPLATE.format(user_input="我想写一首很伤感的歌")
    intent = parse_llm_intent(llm.complete(prompt, json_mode=True))
    assert intent.emotion == ["伤感"]


def test_end_to_end_emotion_count_is_sane():
    """端到端：§3.1.2 的示例应得到 2~3 个情绪，而不是一长串。"""
    intent = resolve_intent(
        "我想写一首关于毕业的歌，有点伤感但最后是释然的感觉。", llm=MockLLMProvider()
    )
    assert 2 <= len(intent.emotion) <= 3, f"情绪数量不合理：{intent.emotion}"
