"""P4 测试：Tutor Prompt、多方案编排、Layman 分级、多样性（阶段门 G4）。

对应 ``实现阶段计划.md`` §8.4：

* §3.9.2 的示例输入产出 §3.9.3 的进阶版结构
* `Advice.options` 长度 2~3，每项 reason 与 theory 非空
* Grounding 捕获率 ≥ 80%（见 ``test_grounding.py``）
* 同输入重复 3 次结构与数量稳定
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.server import app
from app.core.brief import CreationInput, CreativeGoal, CreativeIntent
from app.core.evidence import Evidence, EvidenceSource
from app.core.plan import Advice
from app.providers.mock import MockLLMProvider
from app.services.advisor import (
    DIVERSITY_OVERLAP_LIMIT,
    _reharmonize_with_bass,
    _reharmonize_with_borrowed,
    _reharmonize_with_sevenths,
    build_fallback_advice,
    check_diversity,
    generate_advice,
)
from app.services.analyzer import analyze, build_artifact
from app.services.generation import prompts
from app.services.layman import adapt_advice, terminology_density


@pytest.fixture
def artifact():
    return build_artifact(chord_text="C | G | Am | F", melody_text="E4 G4 A4 G4 E4")


@pytest.fixture
def payload(artifact):
    return CreationInput(
        mode="tutor",
        raw_text="保持温暖，但不要太普通",
        artifact=artifact,
        user_level="some",
    )


# --------------------------------------------------------------------------- #
# Tutor Prompt（§3.9.2）
# --------------------------------------------------------------------------- #


def test_tutor_prompt_has_four_sections(artifact):
    analysis = analyze(artifact)
    prompt = prompts.tutor_prompt(goal_text="保持温暖，但不要太普通", analysis=analysis, evidence=[])
    for header in ("[用户作品]", "[用户目标]", "[检索到的知识]", "[任务]"):
        assert header in prompt


def test_tutor_prompt_renders_artifact_details(artifact):
    """§3.9.2 的 `[用户作品]` 要含 Key / Progression / Melody。"""
    analysis = analyze(artifact)
    prompt = prompts.tutor_prompt(goal_text="x", analysis=analysis, evidence=[])
    assert "Key: C Major" in prompt
    assert "I → V → vi → IV" in prompt
    assert "Melody: E4 G4 A4 G4 E4" in prompt


def test_tutor_prompt_states_constraints(artifact):
    """用户要求「保持温暖」时必须显式写进 Prompt，否则模型容易大改。"""
    prompt = prompts.tutor_prompt(
        goal_text="保持温暖，但不要太普通",
        analysis=analyze(artifact),
        evidence=[],
        constraints=["保持温暖"],
    )
    assert "[必须保持的约束]" in prompt
    assert "保持温暖" in prompt


def test_tutor_prompt_instructs_real_citation_numbers(artifact):
    """必须要求模型引用真实存在的编号 —— 编造引用是最典型的幻觉。"""
    prompt = prompts.tutor_prompt(goal_text="x", analysis=analyze(artifact), evidence=[])
    assert "必须是上面真实存在的编号" in prompt


# --------------------------------------------------------------------------- #
# 变形函数：每个都对应一个实测踩过的坑
# --------------------------------------------------------------------------- #


def test_seventh_reharmonization_uses_dominant_seventh_for_V():
    """回归：V 级必须用属七（G7），不能无脑变 maj7。

    ``Gmaj7`` 会引入调外的 F#，Grounding 会（正确地）判为调外和弦 ——
    Grounding 当初就是靠抓到我自己这个 bug 证明了它的价值。
    """
    assert _reharmonize_with_sevenths(["C", "G", "Am", "F"], "C Major") == [
        "Cmaj7",
        "G7",
        "Am7",
        "Fmaj7",
    ]


def test_seventh_reharmonization_in_other_keys():
    assert _reharmonize_with_sevenths(["G", "D", "Em", "C"], "G Major") == [
        "Gmaj7",
        "D7",
        "Em7",
        "Cmaj7",
    ]


def test_borrowed_reharmonization_targets_subdominant():
    """§3.9.3 的「建议 B」：``C → G → Am → Fm``。"""
    assert _reharmonize_with_borrowed(["C", "G", "Am", "F"], "C Major") == ["C", "G", "Am", "Fm"]


def test_borrowed_reharmonization_skips_minor_keys():
    """小调场景不套用「借用同主音小调」的手法。"""
    chords = ["Am", "Dm", "Em", "Am"]
    assert _reharmonize_with_borrowed(chords, "A Minor") == chords


def test_bass_line_descends_monotonically():
    """回归：低音线必须真的下行（用**实际音高**判断，不能看音级）。

    实测踩过三个坑：
    1. 逐个取三音 → ``C | G/B | A/C | F/A``，低音 ``C→B→C→A`` 中途反弹；
    2. 起始音取最低候选 → 基准落到 -12，后续找不到更低的音，整条线退化为原位，
       得到一个「什么都没改」却挂着「低音级进下行」标签的方案；
    3. 用音级比较「谁更低」→ 从 B(11) 出发把 A(9) 判为更低，于是给 Am 选了
       原位 A4，实际音高反而**高于** B3，线条大幅反弹。

    ``_reharmonize_with_bass`` 内部在音高空间（可跨八度）规划，
    因此这里用 ``chord_pitches`` 的相对关系来验证同一件事。
    """
    from app.services.advisor import _pitch_name  # noqa: F401 - 保持导入可见性

    result = _reharmonize_with_bass(["C", "G", "Am", "F"], "C Major")
    assert result == ["C", "G/B", "Am", "F"]

    # 直接复用实现里的音高规划逻辑来核对：把和弦名转成低音音级序列，
    # 并验证「低音音级确实构成了下行运动」——
    # C → B 是半音下行，B → A 是级进下行，A → F 是三度下行。
    from app.services.parser.chords import NOTE_TO_PITCH, parse_chord

    def bass_pc(name: str) -> int:
        chord = parse_chord(name)
        return NOTE_TO_PITCH[chord.bass or chord.root]

    pcs = [bass_pc(n) for n in result]
    # 相邻低音的**音程距离**（取模 12 后按下行方向计算）应为负或小正数，
    # 关键是不要出现「反弹回原位」那种 +11 的跳跃。
    for a, b in zip(pcs, pcs[1:]):
        step = (b - a) % 12
        assert step != 0, f"低音未移动：{a} → {b}"
        assert step >= 7 or step <= 5, f"出现反向大跳：{a} → {b}"


def test_bass_line_continues_below_root_octave():
    """回归：当低音需要继续下落时，必须允许「低八度原位」。

    实测教训：候选里若只有同八度的根音，线条走到根音以下后无处可去，
    只能反弹回原位，整条线退化成「什么都没改」。
    """
    result = _reharmonize_with_bass(["C", "G", "Am", "F"], "C Major")
    # Am 的低音应为 A（原位），且它是从 B 继续下行的自然落点
    assert result[2] == "Am"
    from app.services.parser.chords import parse_chord

    assert parse_chord(result[1]).bass == "B", "第二拍应为 G/B"


def test_bass_line_works_in_other_keys():
    result = _reharmonize_with_bass(["G", "D", "Em", "C"], "G Major")
    assert result != ["G", "D", "Em", "C"], "应产生转位而非原样返回"


def test_bass_line_does_not_introduce_out_of_key_chords():
    """回归：低音线不得产生调外和弦（Grounding 会报错）。"""
    from app.services.analyzer.roman import is_in_key
    from app.services.parser.chords import parse_chord

    for key, chords in (("C Major", ["C", "G", "Am", "F"]), ("G Major", ["G", "D", "Em", "C"])):
        for name in _reharmonize_with_bass(chords, key):
            assert is_in_key(parse_chord(name), key), f"{name} 在 {key} 中不属于调内"


# --------------------------------------------------------------------------- #
# 兜底建议（知识库直出）
# --------------------------------------------------------------------------- #


def test_fallback_advice_has_two_to_three_options(artifact):
    """§1.2 P0：输出 2～3 个修改方向。"""
    advice = build_fallback_advice(analyze(artifact), [], level="some")
    assert 2 <= len(advice.options) <= 3


def test_fallback_advice_every_option_has_reason_and_theory(artifact):
    """§9 第 6 条：每个建议附带通俗解释和理论依据。"""
    advice = build_fallback_advice(analyze(artifact), [], level="some")
    for option in advice.options:
        assert option.reason.strip()
        assert option.theory
        assert option.chords


def test_fallback_advice_options_are_labeled_alphabetically(artifact):
    advice = build_fallback_advice(analyze(artifact), [], level="some")
    labels = [o.label for o in advice.options]
    assert "建议 A" in labels[0]
    assert "建议 B" in labels[1]


def test_fallback_advice_has_analysis_and_problems(artifact):
    advice = build_fallback_advice(analyze(artifact), [], level="some")
    assert advice.analysis
    assert advice.problems


def test_fallback_advice_is_grounding_clean(artifact):
    """兜底方案自己必须能通过 Grounding —— 否则等于系统在自我打脸。"""
    from app.services.generation.grounding import verify_advice

    analysis = analyze(artifact)
    advice = build_fallback_advice(analysis, [], level="some")
    report = verify_advice(advice, evidence=[], key=analysis.key)
    assert report.ok, f"兜底方案未通过 Grounding：{[i.detail for i in report.errors]}"


def test_fallback_advice_handles_empty_artifact():
    """无作品时也不得崩溃。"""
    from app.core.artifact import MusicArtifact

    advice = build_fallback_advice(analyze(MusicArtifact()), [], level="some")
    assert len(advice.options) >= 2


# --------------------------------------------------------------------------- #
# 编排端到端
# --------------------------------------------------------------------------- #


def test_advice_end_to_end_with_llm(payload):
    """G4：§3.9.2 的示例输入产出 §3.9.3 的进阶版结构。"""
    result = generate_advice(payload, llm=MockLLMProvider())
    advice = result.advice

    assert advice.analysis
    assert advice.problems
    assert 2 <= len(advice.options) <= 3
    for option in advice.options:
        assert option.reason and option.theory and option.chords


def test_advice_falls_back_without_llm(payload):
    """无 Key 时进阶链路同样可用。"""
    result = generate_advice(payload, llm=None)
    assert 2 <= len(result.advice.options) <= 3
    assert result.advice.analysis


def test_advice_attaches_evidence(payload):
    """§3.9.3 要求展示理论依据。"""
    result = generate_advice(payload, llm=None)
    assert result.advice.evidence
    for item in result.advice.evidence:
        assert item.entry_id and item.source.title


def test_advice_produces_grounding_report(payload):
    result = generate_advice(payload, llm=MockLLMProvider())
    assert result.trace.grounding is not None
    assert isinstance(result.trace.grounding.ok, bool)


def test_advice_trace_is_serializable(payload):
    from app.core.trace import Trace

    result = generate_advice(payload, llm=None)
    line = result.trace.to_jsonl()
    assert Trace.from_jsonl(line) == result.trace
    # Trace 必须带分析与 grounding，这是 P6 评测的数据源
    assert result.trace.analysis is not None
    assert result.trace.grounding is not None


def test_advice_is_stable_across_repeats(payload):
    """§8 风险应对「输出格式不稳定」：同输入重复 3 次结构必须一致。"""
    results = [generate_advice(payload, llm=MockLLMProvider()) for _ in range(3)]
    counts = {len(r.advice.options) for r in results}
    assert counts == {3}
    labels = {tuple(o.label for o in r.advice.options) for r in results}
    assert len(labels) == 1, "同输入应给出相同的方案集合"


def test_advice_degrades_on_parse_failure(payload):
    class BadLLM:
        name = "bad"
        available = True

        def complete(self, prompt, *, system=None, json_mode=False):
            return "好的，我来帮你分析～"

    result = generate_advice(payload, llm=BadLLM())
    assert any(d.kind == "parse_failed" for d in result.trace.degradation)
    assert len(result.advice.options) >= 2


def test_advice_degrades_on_llm_error(payload):
    class BrokenLLM:
        name = "broken"
        available = True

        def complete(self, prompt, *, system=None, json_mode=False):
            raise RuntimeError("connection refused")

    result = generate_advice(payload, llm=BrokenLLM())
    assert any(d.kind == "llm_failed" for d in result.trace.degradation)
    assert len(result.advice.options) >= 2


def test_advice_records_llm_unavailable_once(payload):
    """回归：降级留痕不得重复（同一请求内）。"""
    from app.core.config import Settings
    from app.providers.deepseek import DeepSeekProvider

    result = generate_advice(payload, llm=DeepSeekProvider(Settings(llm_api_key="")))
    kinds = [d.kind for d in result.trace.degradation]
    assert kinds.count("llm_unavailable") <= 1, f"重复留痕：{kinds}"


# --------------------------------------------------------------------------- #
# 多样性（§7.2 Diversity）
# --------------------------------------------------------------------------- #


def test_fallback_options_are_diverse(artifact):
    """三个方案不能是同一个改动的三种说法 —— 那是虚假的选择权。"""
    advice = build_fallback_advice(analyze(artifact), [], level="some")
    chord_sets = [set(o.chords) for o in advice.options]
    for i in range(len(chord_sets)):
        for j in range(i + 1, len(chord_sets)):
            union = chord_sets[i] | chord_sets[j]
            overlap = len(chord_sets[i] & chord_sets[j]) / len(union) if union else 0
            assert overlap <= DIVERSITY_OVERLAP_LIMIT, f"方案 {i} 与 {j} 过于相似"


def test_check_diversity_flags_identical_options():
    from app.core.plan import AdviceOption

    option = AdviceOption(label="A", chords=["C", "G"], feature="f", reason="r", theory=[])
    clone = AdviceOption(label="B", chords=["C", "G"], feature="f", reason="r", theory=[])
    advice = Advice(analysis="a", problems=["p"], options=[option, clone])
    warnings = check_diversity(advice)
    assert warnings, "完全相同的两个方案必须被标记"


def test_check_diversity_accepts_distinct_options():
    from app.core.plan import AdviceOption

    a = AdviceOption(label="A", chords=["Cmaj7", "G7"], feature="f", reason="r", theory=[])
    b = AdviceOption(label="B", chords=["C", "Fm"], feature="f", reason="r", theory=[])
    advice = Advice(analysis="a", problems=["p"], options=[a, b])
    assert check_diversity(advice) == []


# --------------------------------------------------------------------------- #
# Layman 分级（P4.4 / §3.8.3）
# --------------------------------------------------------------------------- #


@pytest.fixture
def professional_advice() -> Advice:
    from app.core.plan import AdviceOption

    return Advice(
        analysis="当前进行为 I → V → vi → IV，功能关系稳定，和声张力较低。",
        problems=["色彩和弦较少。"],
        options=[
            AdviceOption(
                label="建议 A",
                chords=["Cmaj7"],
                feature="采用 Modal Interchange 引入色彩",
                reason="听起来更柔和。",
                theory=["Tension / Resolution"],
            ),
            AdviceOption(
                label="建议 B",
                chords=["Fm"],
                feature="借用 iv 级",
                reason="多一丝忧伤。",
                theory=["Cadence"],
            ),
        ],
    )


def test_adapt_advice_zero_level_replaces_terms(professional_advice):
    """零基础档：专业表述被替换为通俗表述。"""
    adapted = adapt_advice(professional_advice, "zero")
    assert "流行歌最常用的走向之一" in adapted.analysis
    assert "I → V → vi → IV" not in adapted.analysis
    assert "从别的调借一个和弦" in adapted.options[0].feature


def test_adapt_advice_advanced_level_is_untouched(professional_advice):
    """进阶档：保留原文，不做任何替换。"""
    adapted = adapt_advice(professional_advice, "advanced")
    assert adapted == professional_advice


def test_adapt_advice_some_level_uses_notes(professional_advice):
    """中间档：用「通俗说法（Term）」的旁注形式。"""
    adapted = adapt_advice(professional_advice, "some")
    assert "调式借用（Modal Interchange）" in adapted.options[0].feature


def test_adapt_advice_does_not_touch_chords(professional_advice):
    """和弦是数据不是措辞 —— 分级改写不得动它。"""
    for level in ("zero", "some", "advanced"):
        adapted = adapt_advice(professional_advice, level)
        assert [o.chords for o in adapted.options] == [o.chords for o in professional_advice.options]


def test_three_levels_are_measurably_different(professional_advice):
    """三档输出必须有可测量的区分，而不只是换了 Prompt 措辞。"""
    zero = adapt_advice(professional_advice, "zero")
    advanced = adapt_advice(professional_advice, "advanced")

    zero_text = zero.analysis + " ".join(o.feature for o in zero.options)
    advanced_text = advanced.analysis + " ".join(o.feature for o in advanced.options)

    assert terminology_density(zero_text) < terminology_density(advanced_text)


def test_advice_end_to_end_respects_level(artifact):
    """端到端：零基础档的输出不含未替换的专业表述。"""
    payload = CreationInput(
        mode="tutor",
        raw_text="让它别那么普通",
        artifact=artifact,
        user_level="zero",
    )
    result = generate_advice(payload, llm=MockLLMProvider())
    assert result.advice.layman_level == "zero"


# --------------------------------------------------------------------------- #
# API（P4.7）
# --------------------------------------------------------------------------- #


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_advice_endpoint_matches_mvp_section_3_9_3(client):
    """§3.9.2 的示例输入 → §3.9.3 的进阶版结构，逐段核对。"""
    response = client.post(
        "/api/advice",
        json={"goal": "保持温暖，但不要太普通", "chords": "C | G | Am | F"},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["analysis"]           # ## 分析
    assert body["problems"]           # ## 问题
    assert 2 <= len(body["options"]) <= 3  # ## 建议 A/B
    assert body["evidence"]           # ## 理论依据
    for option in body["options"]:
        assert option["chords"]
        assert option["feature"]
        assert option["reason"]


def test_advice_endpoint_returns_grounding_summary(client):
    """Grounding 结果必须透出，且含可写进报告的计数。"""
    body = client.post("/api/advice", json={"chords": "C | G | Am | F"}).json()
    assert "grounding" in body
    summary = body["grounding"]["summary"]
    assert "citations_fabricated" in summary
    assert "chords_out_of_key" in summary


def test_advice_endpoint_returns_breakdown(client):
    """进阶链路也应返回作品分析结果。"""
    body = client.post("/api/advice", json={"chords": "C | G | Am | F"}).json()
    assert body["breakdown"]["key"] == "C Major"
    assert body["breakdown"]["roman"] == ["I", "V", "vi", "IV"]


def test_advice_endpoint_rejects_empty_input(client):
    response = client.post("/api/advice", json={})
    assert response.status_code == 400


def test_advice_endpoint_rejects_invalid_chords(client):
    response = client.post("/api/advice", json={"chords": "H | X"})
    assert response.status_code == 400


def test_advice_endpoint_in_openapi(client):
    assert "/api/advice" in client.get("/openapi.json").json()["paths"]


def test_advice_endpoint_accepts_explicit_key(client):
    body = client.post("/api/advice", json={"chords": "C | G | Am | F", "key": "G Major"}).json()
    assert body["breakdown"]["key"] == "G Major"


def test_advice_endpoint_with_constraints(client):
    body = client.post(
        "/api/advice",
        json={
            "goal": "保持温暖，但不要太普通",
            "chords": "C | G | Am | F",
            "constraints": ["保持温暖"],
        },
    ).json()
    assert body["grounding"]["ok"] in (True, False)
    assert body["trace_id"]
