"""P2 测试：Prompt 组装、结构化输出解析、Creative Starter 编排（阶段门 G2）。

对应 ``实现阶段计划.md`` §6.3：

* §3.3.4 的示例输入端到端产出符合 §3.3.3 结构的方案
* 方案中 key_explanation / 每段 explanation 非空
* 零基础模式输出不含未解释的专业术语
* 无 API Key 时 Mock 后端可完整跑通同一条链路
"""

from __future__ import annotations

import json

import pytest

from app.core.brief import CreationInput, CreativeIntent, SelectionInput
from app.core.evidence import Evidence, EvidenceSource
from app.core.plan import StarterPlan, StarterSection
from app.providers.mock import MockLLMProvider
from app.services.generation import prompts
from app.services.generation.parser import (
    OutputParseError,
    extract_json,
    parse_advice,
    parse_starter_plan,
)
from app.services.starter import (
    build_fallback_plan,
    generate_starter_plan,
    validate_plan,
)

# --------------------------------------------------------------------------- #
# Prompt 组装（§3.3.4 / §3.9.2）
# --------------------------------------------------------------------------- #


@pytest.fixture
def sample_evidence() -> list[Evidence]:
    return [
        Evidence(
            entry_id="emotion.sad_to_hopeful.01",
            title="「伤感→释然」的情绪转折实现",
            layman_title="伤感怎么变成释然",
            content="技术本质是小调色彩 → 大调色彩的转向。",
            layman_content="伤感来自不太稳定的和弦，释然来自稳稳落回原地的和弦。",
            tags=["emotion", "transition"],
            source=EvidenceSource(
                title="MuseGuide 情绪映射表", url="https://example.org", license="CC BY-SA 4.0"
            ),
        )
    ]


def test_starter_prompt_has_four_required_sections(sample_evidence):
    """§3.3.4 的 Prompt 必须含四个段落标题。"""
    prompt = prompts.starter_prompt(
        raw_text="我想写一首关于毕业的歌",
        intent=CreativeIntent(emotion=["伤感", "释然"]),
        evidence=sample_evidence,
    )
    for header in ("[用户创作意图]", "[Intent Mapping 结果]", "[检索到的知识]", "[任务]"):
        assert header in prompt, f"Prompt 缺少段落 {header}"


def test_starter_prompt_contains_five_task_items(sample_evidence):
    """§3.3.4 的任务有 4 条，加上输出格式要求。"""
    prompt = prompts.starter_prompt(
        raw_text="x", intent=CreativeIntent(emotion=["伤感"]), evidence=sample_evidence
    )
    assert "建议一个合适的调性" in prompt
    assert "为主歌和副歌各设计一组基础和弦" in prompt
    assert "用通俗语言解释每个选择的原因" in prompt
    assert "情绪变化是如何实现的" in prompt


def test_starter_prompt_inlines_layman_evidence_for_zero_level(sample_evidence):
    """零基础模式下证据本身应是通俗版本 —— 模型才更可能输出通俗解释。"""
    prompt = prompts.starter_prompt(
        raw_text="x", intent=CreativeIntent(emotion=["伤感"]), evidence=sample_evidence, level="zero"
    )
    assert "伤感来自不太稳定的和弦" in prompt
    assert "技术本质是小调色彩" not in prompt


def test_advanced_level_uses_professional_evidence(sample_evidence):
    prompt = prompts.starter_prompt(
        raw_text="x",
        intent=CreativeIntent(emotion=["伤感"]),
        evidence=sample_evidence,
        level="advanced",
    )
    assert "技术本质是小调色彩" in prompt


def test_prompt_without_evidence_forbids_fabrication():
    """没有证据时必须明确禁止编造来源。"""
    prompt = prompts.starter_prompt(
        raw_text="x", intent=CreativeIntent(emotion=["伤感"]), evidence=[]
    )
    assert "不要编造来源" in prompt


def test_prompt_embeds_output_schema(sample_evidence):
    prompt = prompts.starter_prompt(
        raw_text="x", intent=CreativeIntent(), evidence=sample_evidence
    )
    assert "key_explanation" in prompt
    assert "sections" in prompt


def test_tutor_prompt_has_four_sections_and_five_tasks(sample_evidence):
    """§3.9.2 的进阶 Prompt 结构。"""
    from app.core.plan import AnalysisResult

    analysis = AnalysisResult(key="C Major", raw=["C", "G"], roman=["I", "V"])
    prompt = prompts.tutor_prompt(
        goal_text="保持温暖，但不要太普通", analysis=analysis, evidence=sample_evidence
    )
    for header in ("[用户作品]", "[用户目标]", "[检索到的知识]", "[任务]"):
        assert header in prompt
    assert "给出 2-3 个修改方向" in prompt
    assert "引用检索到的知识" in prompt


def test_render_artifact_includes_key_and_roman():
    from app.core.plan import AnalysisResult

    text = prompts.render_artifact(
        AnalysisResult(key="C Major", raw=["C", "G", "Am", "F"], roman=["I", "V", "vi", "IV"])
    )
    assert "Key: C Major" in text
    assert "I → V → vi → IV" in text


# --------------------------------------------------------------------------- #
# 结构化输出解析
# --------------------------------------------------------------------------- #


VALID_PLAN = {
    "key": "C Major",
    "key_explanation": "听起来明亮温暖。",
    "tempo": 82,
    "tempo_explanation": "中等偏慢，适合抒情。",
    "sections": [
        {"name": "主歌", "chords": ["Am", "F", "C", "G"], "emotion": "偏伤感", "explanation": "柔和忧伤。"},
        {"name": "副歌", "chords": ["C", "G", "Am", "F"], "emotion": "释然", "explanation": "温暖开阔。"},
    ],
    "why": "小调转大调实现情绪转折。",
    "adjust_hints": ["把 F 换成 Fm"],
}


def test_parse_starter_plan_happy_path():
    plan = parse_starter_plan(json.dumps(VALID_PLAN, ensure_ascii=False))
    assert plan.key == "C Major"
    assert plan.tempo == 82
    assert len(plan.sections) == 2
    assert plan.sections[0].chords == ["Am", "F", "C", "G"]


def test_parse_starter_plan_tolerates_markdown_fence():
    text = f"```json\n{json.dumps(VALID_PLAN, ensure_ascii=False)}\n```"
    assert parse_starter_plan(text).key == "C Major"


def test_parse_starter_plan_tolerates_leading_prose():
    text = "好的，这是你的起步方案：\n" + json.dumps(VALID_PLAN, ensure_ascii=False)
    assert parse_starter_plan(text).key == "C Major"


def test_parse_starter_plan_accepts_pipe_separated_chords():
    """模型可能把和弦写成 ``"Am | F | C | G"`` 而不是数组。"""
    data = dict(VALID_PLAN)
    data["sections"] = [
        {"name": "主歌", "chords": "Am | F | C | G", "emotion": "伤感", "explanation": "柔和。"},
        {"name": "副歌", "chords": "C G Am F", "emotion": "释然", "explanation": "开阔。"},
    ]
    plan = parse_starter_plan(json.dumps(data, ensure_ascii=False))
    assert plan.sections[0].chords == ["Am", "F", "C", "G"]


def test_parse_starter_plan_rejects_single_section():
    """§3.3.3 要求主歌 + 副歌，只有一段即为不合规。"""
    data = dict(VALID_PLAN)
    data["sections"] = VALID_PLAN["sections"][:1]
    with pytest.raises(OutputParseError, match="至少两个段落"):
        parse_starter_plan(json.dumps(data, ensure_ascii=False))


def test_parse_starter_plan_rejects_blank_explanation():
    data = dict(VALID_PLAN)
    data["sections"] = [
        {"name": "主歌", "chords": ["C"], "emotion": "x", "explanation": "  "},
        VALID_PLAN["sections"][1],
    ]
    with pytest.raises(OutputParseError):
        parse_starter_plan(json.dumps(data, ensure_ascii=False))


def test_parse_starter_plan_rejects_blank_key_explanation():
    data = dict(VALID_PLAN, key_explanation="")
    with pytest.raises(OutputParseError):
        parse_starter_plan(json.dumps(data, ensure_ascii=False))


def test_parse_starter_plan_rejects_non_json():
    with pytest.raises(OutputParseError):
        parse_starter_plan("我很乐意帮你创作一首歌！")
    with pytest.raises(OutputParseError):
        parse_starter_plan("")


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('说明文字 {"a": 1} 结尾') == {"a": 1}


def test_parse_advice_enforces_option_count():
    """§1.2 要求 2~3 个方向；1 个或 4 个都应失败。"""
    one = {
        "analysis": "a",
        "problems": ["p"],
        "options": [
            {"label": "A", "chords": ["C"], "feature": "f", "reason": "r", "theory": ["t"]}
        ],
    }
    with pytest.raises(OutputParseError):
        parse_advice(json.dumps(one, ensure_ascii=False))


def test_parse_advice_happy_path():
    two = {
        "analysis": "当前进行常见",
        "problems": ["色彩较少"],
        "options": [
            {"label": "A", "chords": ["Cmaj7"], "feature": "加色彩", "reason": "更梦幻", "theory": ["七和弦"]},
            {"label": "B", "chords": ["Fm"], "feature": "借用", "reason": "多一丝忧伤", "theory": ["调式借用"]},
        ],
    }
    advice = parse_advice(json.dumps(two, ensure_ascii=False))
    assert len(advice.options) == 2
    assert advice.options[1].chords == ["Fm"]


# --------------------------------------------------------------------------- #
# Creative Starter 编排（§3.3.2 流程）
# --------------------------------------------------------------------------- #


def test_starter_end_to_end_with_mock_llm():
    """G2：§3.3.4 的示例输入端到端产出 §3.3.3 结构的方案。"""
    payload = CreationInput(raw_text="我想写一首关于毕业的歌，有点伤感但最后是释然的感觉。")
    result = generate_starter_plan(payload, llm=MockLLMProvider())

    plan = result.plan
    assert plan.key
    assert plan.key_explanation
    assert plan.tempo_explanation
    assert len(plan.sections) >= 2
    for section in plan.sections:
        assert section.explanation, f"段落 {section.name} 缺少解释"
        assert section.chords


def test_starter_falls_back_without_llm():
    """G2：无 API Key 时仍能产出可用方案（§8 风险应对）。"""
    payload = CreationInput(raw_text="我想写一首关于毕业的歌，有点伤感但最后释然")
    result = generate_starter_plan(payload, llm=None)

    assert result.plan.key
    assert len(result.plan.sections) == 2
    assert result.plan.evidence, "降级路径同样必须带证据"


def test_starter_records_llm_unavailable_once():
    """回归：同一次请求中，llm_unavailable 不得重复留痕。

    Mock 曾出现两次记录 —— 因为 intent 层与 starter 层都会检测到 LLM 不可用。
    留痕重复会让「降级次数」这类统计指标失真。
    """
    from app.providers.deepseek import DeepSeekProvider
    from app.core.config import Settings

    payload = CreationInput(raw_text="随便写点什么")
    result = generate_starter_plan(payload, llm=DeepSeekProvider(Settings(llm_api_key="")))
    kinds = [d.kind for d in result.trace.degradation]
    assert kinds.count("llm_unavailable") <= 1, f"llm_unavailable 重复：{kinds}"


def test_starter_falls_back_on_parse_failure():
    class BadLLM:
        name = "bad"
        available = True

        def complete(self, prompt, *, system=None, json_mode=False):
            return "好的！我来帮你写一首歌～"

    payload = CreationInput(raw_text="我想写一首歌")
    result = generate_starter_plan(payload, llm=BadLLM())
    kinds = [d.kind for d in result.trace.degradation]
    assert "parse_failed" in kinds
    assert result.plan.key, "解析失败后仍必须有可用方案"


def test_starter_falls_back_on_llm_error():
    class BrokenLLM:
        name = "broken"
        available = True

        def complete(self, prompt, *, system=None, json_mode=False):
            raise RuntimeError("connection refused")

    payload = CreationInput(raw_text="我想写一首歌")
    result = generate_starter_plan(payload, llm=BrokenLLM())
    kinds = [d.kind for d in result.trace.degradation]
    assert "llm_failed" in kinds
    assert result.plan.key


def test_starter_uses_selections_when_text_is_empty():
    """§8 兜底：零基础用户只做选择也必须能拿到方案。"""
    payload = CreationInput(
        raw_text="",
        selections=SelectionInput(emotion="温柔", style="民谣", tempo_label="很慢，像翻相册"),
    )
    result = generate_starter_plan(payload, llm=None)
    assert result.plan.key
    assert result.plan.tempo == 60, "应使用「很慢」对应的 60 BPM"


# --------------------------------------------------------------------------- #
# 用户显式选择不得被模型覆盖（回归）
# --------------------------------------------------------------------------- #


def test_user_tempo_selection_overrides_llm_output():
    """回归：用户选定的速度必须覆盖模型的返回值。

    实测教训（接入真实 DeepSeek 后才暴露）：用户选「很慢，像翻相册」= 60 BPM，
    但模型返回了 ``tempo: 72``（因为 72 也勉强算「慢」）。
    只把选择写进 Prompt 是**不够的** —— 模型会把它当建议。

    **用户的显式选择是输入，模型给的数字是输出；输入不应被输出覆盖。**
    这与 ADR-0009「选择层优先于 LLM 层」是同一条原则，
    只是发生在生成之后而不是之前。
    """

    class WrongTempoLLM:
        """故意返回错误速度的模型。"""

        name = "wrong-tempo"
        available = True

        def complete(self, prompt, *, system=None, json_mode=False):
            import json

            return json.dumps(
                {
                    "key": "C Major",
                    "key_explanation": "明亮温暖。",
                    "tempo": 72,  # 用户选的是 60
                    "tempo_explanation": "中慢速。",
                    "sections": [
                        {
                            "name": "主歌",
                            "chords": ["Am", "F"],
                            "emotion": "伤感",
                            "explanation": "柔和。",
                        },
                        {
                            "name": "副歌",
                            "chords": ["C", "G"],
                            "emotion": "释然",
                            "explanation": "开阔。",
                        },
                    ],
                    "why": "小调转大调。",
                    "adjust_hints": [],
                },
                ensure_ascii=False,
            )

    payload = CreationInput(
        raw_text="",
        selections=SelectionInput(emotion="温柔", tempo_label="很慢，像翻相册"),
    )
    result = generate_starter_plan(payload, llm=WrongTempoLLM())

    assert result.plan.tempo == 60, "用户的显式选择应覆盖模型给出的 72"
    assert any("覆盖" in w for w in result.warnings), (
        "覆盖模型输出这件事必须留痕，不能静默修改"
    )


def test_enforce_user_selections_is_noop_when_model_agrees():
    """模型本来就对时，不应产生多余的修正记录。"""
    from app.core.plan import StarterSection
    from app.services.starter import enforce_user_selections

    plan = StarterPlan(
        key="C Major",
        key_explanation="k",
        tempo=60,
        tempo_explanation="t",
        sections=[
            StarterSection(name="主歌", chords=["C"], explanation="a"),
            StarterSection(name="副歌", chords=["G"], explanation="b"),
        ],
    )
    fixed, adjustments = enforce_user_selections(
        plan, CreativeIntent(tempo_feel="很慢"), SelectionInput(tempo_label="很慢，像翻相册")
    )
    assert fixed.tempo == 60
    assert adjustments == [], "无需修正时不应产生记录"


def test_enforce_user_selections_skips_when_no_selection():
    """用户没选速度时不做任何覆盖 —— 此时模型的判断才是唯一依据。"""
    from app.core.plan import StarterSection
    from app.services.starter import enforce_user_selections

    plan = StarterPlan(
        key="C Major",
        key_explanation="k",
        tempo=95,
        tempo_explanation="t",
        sections=[
            StarterSection(name="主歌", chords=["C"], explanation="a"),
            StarterSection(name="副歌", chords=["G"], explanation="b"),
        ],
    )
    fixed, adjustments = enforce_user_selections(plan, CreativeIntent(), SelectionInput())
    assert fixed.tempo == 95
    assert adjustments == []


def test_starter_prompt_locks_tempo_when_user_selected():
    """Prompt 中必须给出确切数值并声明为硬约束。"""
    from app.services.generation import prompts

    prompt = prompts.starter_prompt(
        raw_text="", intent=CreativeIntent(tempo_feel="很慢"), evidence=[], locked_tempo=60
    )
    assert "[必须遵守的数值]" in prompt
    assert "恰好是 60" in prompt


def test_starter_prompt_omits_lock_without_selection():
    from app.services.generation import prompts

    prompt = prompts.starter_prompt(
        raw_text="写首歌", intent=CreativeIntent(), evidence=[], locked_tempo=None
    )
    assert "[必须遵守的数值]" not in prompt


def test_starter_evidence_is_attached():
    """§3.9.3 要求展示理论依据，方案必须带 evidence。"""
    payload = CreationInput(raw_text="我想写一首关于毕业的歌，有点伤感但最后释然")
    result = generate_starter_plan(payload, llm=None)
    assert result.plan.evidence
    for item in result.plan.evidence:
        assert item.entry_id
        assert item.source.title, "证据必须可追溯"


def test_starter_trace_is_serializable():
    """Trace 要能存档回放 —— 这是 P6 评测的前提。"""
    payload = CreationInput(raw_text="我想写一首歌")
    result = generate_starter_plan(payload, llm=MockLLMProvider())
    line = result.trace.to_jsonl()
    assert "\n" not in line
    from app.core.trace import Trace

    assert Trace.from_jsonl(line) == result.trace


def test_starter_output_is_deterministic_without_llm():
    payload = CreationInput(raw_text="我想写一首关于毕业的歌，有点伤感但最后释然")
    assert generate_starter_plan(payload, llm=None).plan == generate_starter_plan(payload, llm=None).plan


# --------------------------------------------------------------------------- #
# 方案校验（G2：解释非空、无未解释术语）
# --------------------------------------------------------------------------- #


def test_fallback_plan_passes_validation():
    plan = build_fallback_plan(CreativeIntent(emotion=["伤感", "释然"]), [])
    assert validate_plan(plan) == []


def test_validate_plan_flags_unparseable_chords():
    plan = build_fallback_plan(CreativeIntent(emotion=["伤感"]), []).model_copy(
        update={
            "sections": [
                StarterSection(name="主歌", chords=["H", "X"], emotion="x", explanation="y"),
                StarterSection(name="副歌", chords=["C"], emotion="x", explanation="y"),
            ]
        }
    )
    issues = validate_plan(plan)
    assert any("无法识别" in i for i in issues)


def test_validate_plan_flags_banned_terms_in_zero_mode():
    plan = build_fallback_plan(CreativeIntent(emotion=["伤感"]), []).model_copy(
        update={"key_explanation": "这里涉及三度叠置与声部进行"}
    )
    issues = validate_plan(plan)
    assert any("禁区术语" in i for i in issues)


def test_validate_plan_flags_out_of_range_tempo():
    plan = build_fallback_plan(CreativeIntent(emotion=["伤感"]), []).model_copy(
        update={"tempo": 500}
    )
    assert any("速度" in i for i in validate_plan(plan))


def test_fallback_plan_is_complete_and_layman_friendly():
    plan = build_fallback_plan(CreativeIntent(emotion=["伤感", "释然"]), [])
    assert plan.key_explanation
    assert plan.why
    assert plan.adjust_hints
    from app.services.layman import find_banned_terms

    assert find_banned_terms(plan.key_explanation) == []
    assert find_banned_terms(plan.why) == []
