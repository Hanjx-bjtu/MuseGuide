"""契约往返测试 —— P0 阶段门 G0 的核心断言。

`Trace` 要能存档与回放，因此**每个契约都必须可序列化往返**。
任一契约新增字段而不更新这里，测试就会失败。
"""

from __future__ import annotations

import pytest

from app.core import (
    Advice,
    AdviceOption,
    AnalysisResult,
    Candidate,
    ChordSymbol,
    CreationInput,
    CreativeGoal,
    CreativeIntent,
    DecomposedQuery,
    Evidence,
    EvidenceSource,
    GroundingIssue,
    GroundingReport,
    LaymanNote,
    MelodyInfo,
    MusicArtifact,
    Section,
    SelectionInput,
    StarterPlan,
    StarterSection,
    Trace,
)

ALL_CONTRACTS = [
    ChordSymbol(raw="G/B", root="G", quality="major", bass="B", roman="V6", function="Dominant"),
    MelodyInfo(notes=["E4", "G4", "A4"], range=["E4", "A4"], contour="up-down", repetition=False),
    Section(name="主歌", chords=["Am", "F"], emotion="偏伤感", explanation="柔和忧伤"),
    MusicArtifact(source_format="chords_text", key="C Major", tempo=82, meter="4/4"),
    SelectionInput(emotion="伤感", style="民谣", tempo_label="中等偏慢，像散步"),
    CreativeIntent(emotion=["伤感", "释然"], style="民谣", tempo_feel="中等偏慢", raw_text="毕业"),
    CreativeGoal(text="保持温暖，但不要太普通", target_section="副歌", constraints=["保持温暖"]),
    CreationInput(mode="tutor", raw_text="保持温暖", user_level="some"),
    DecomposedQuery(intent="修改已有作品", goal="保持温暖", object="和弦进行", queries=["七和弦"]),
    Candidate(entry_id="harmony.seventh_chords.01", score=0.9, route="both", rank_final=1),
    EvidenceSource(title="Open Music Theory", url="https://example.org", license="CC BY-SA 4.0"),
    Evidence(entry_id="e1", title="七和弦", layman_title="给和弦加点料", content="..."),
    GroundingIssue(kind="out_of_key", detail="Ab 不在 C Major 内", severity="error"),
    GroundingReport(ok=False, issues=[GroundingIssue(kind="out_of_key")]),
    LaymanNote(key="听起来明亮温暖的调", progression="流行歌最常用的走向之一"),
    AnalysisResult(key="C Major", raw=["C", "G", "Am", "F"], roman=["I", "V", "vi", "IV"]),
    StarterSection(name="主歌", chords=["Am", "F", "C", "G"], emotion="偏伤感", explanation="柔和忧伤"),
    StarterPlan(
        key="C Major",
        key_explanation="明亮温暖",
        tempo=82,
        tempo_explanation="中等偏慢",
        sections=[
            StarterSection(name="主歌", chords=["Am"], emotion="伤感", explanation="柔和"),
            StarterSection(name="副歌", chords=["C"], emotion="释然", explanation="开阔"),
        ],
    ),
    AdviceOption(label="建议 A", chords=["Cmaj7"], feature="加色彩", reason="更梦幻", theory=["七和弦"]),
    Advice(
        analysis="当前进行常见",
        problems=["色彩较少"],
        options=[
            AdviceOption(label="A", chords=["Cmaj7"], feature="f", reason="r"),
            AdviceOption(label="B", chords=["Fm"], feature="f", reason="r"),
        ],
    ),
]


@pytest.mark.parametrize("obj", ALL_CONTRACTS, ids=lambda o: type(o).__name__)
def test_contract_roundtrip(obj):
    """序列化 → 反序列化 → 逐字段相等。"""
    payload = obj.model_dump_json()
    restored = type(obj).model_validate_json(payload)
    assert restored == obj


@pytest.mark.parametrize("obj", ALL_CONTRACTS, ids=lambda o: type(o).__name__)
def test_contract_has_json_schema(obj):
    """每个契约都必须能导出 JSON Schema（供文档与外部校验使用）。"""
    schema = type(obj).model_json_schema()
    assert isinstance(schema, dict) and schema


def test_trace_roundtrip_with_nested_payload():
    """Trace 是评测与回放的公共数据源，必须完整还原。"""
    trace = Trace(
        input=CreationInput(mode="starter", raw_text="我想写一首关于毕业的歌"),
        intent=CreativeIntent(emotion=["伤感", "释然"], source="rule", matched_rules=["有点伤感但最后释然"]),
        analysis=AnalysisResult(key="C Major", roman=["I"]),
        queries=[DecomposedQuery(queries=["小调到大调的情绪转向和弦进行"])],
        candidates=[Candidate(entry_id="e1", score=0.8, route="dense", rank_final=1)],
        evidence=[Evidence(entry_id="e1", title="t", source=EvidenceSource(title="s", url="u"))],
        prompt="[任务] ...",
        raw_output='{"key": "C Major"}',
        plan=StarterPlan(
            key="C Major",
            key_explanation="k",
            tempo=82,
            tempo_explanation="t",
            sections=[
                StarterSection(name="主歌", explanation="a"),
                StarterSection(name="副歌", explanation="b"),
            ],
        ),
        timings_ms={"intent": 120, "retrieve": 300, "generate": 1200},
    )
    line = trace.to_jsonl()
    assert "\n" not in line, "JSONL 必须是一行"
    restored = Trace.from_jsonl(line)
    assert restored == trace
    assert restored.total_ms() == 1620


def test_trace_id_is_unique():
    assert Trace().trace_id != Trace().trace_id


# --- 业务约束：这些字段不是「建议」，是 MVP计划.md 的硬性条款 ---


def test_advice_requires_2_to_3_options():
    """MVP计划.md §1.2 P0：输出 2～3 个修改方向。"""
    good = AdviceOption(label="A", chords=["C"], feature="f", reason="r")
    with pytest.raises(ValueError):
        Advice(analysis="a", options=[good])  # 只有 1 个
    with pytest.raises(ValueError):
        Advice(analysis="a", options=[good] * 4)  # 4 个


def test_advice_option_rejects_blank_reason():
    """§9 第 6 条：每个建议附带通俗解释和理论依据。"""
    with pytest.raises(ValueError):
        AdviceOption(label="A", chords=["C"], feature="f", reason="   ")


def test_starter_plan_rejects_blank_explanation():
    """§3.3.3：起步方案每个选择都必须有通俗解释。"""
    with pytest.raises(ValueError):
        StarterPlan(
            key="C Major",
            key_explanation=" ",
            tempo=82,
            tempo_explanation="t",
            sections=[
                StarterSection(name="主歌", explanation="a"),
                StarterSection(name="副歌", explanation="b"),
            ],
        )
    with pytest.raises(ValueError):
        StarterSection(name="主歌", explanation="")


def test_starter_plan_requires_at_least_two_sections():
    """§3.3.3 的示例含主歌与副歌两段。"""
    with pytest.raises(ValueError):
        StarterPlan(
            key="C Major",
            key_explanation="k",
            tempo=82,
            tempo_explanation="t",
            sections=[StarterSection(name="主歌", explanation="a")],
        )


def test_empty_artifact_detection_routes_to_starter():
    """素材为空 → Creative Starter；有素材 → Creative Tutor（Artifact-aware）。"""
    assert MusicArtifact().is_empty is True
    assert MusicArtifact(source_format="chords_text", chords=[ChordSymbol(raw="C", root="C")]).is_empty is False


def test_artifact_convenience_accessors():
    art = MusicArtifact(
        source_format="chords_text",
        chords=[
            ChordSymbol(raw="C", root="C", roman="I", function="Tonic"),
            ChordSymbol(raw="G", root="G", roman="V", function="Dominant"),
        ],
    )
    assert art.chord_names == ["C", "G"]
    assert art.roman_numerals == ["I", "V"]
    assert art.functions == ["Tonic", "Dominant"]


def test_grounding_report_error_filtering():
    report = GroundingReport(
        ok=False,
        issues=[
            GroundingIssue(kind="out_of_key", severity="error"),
            GroundingIssue(kind="weak_grounding", severity="warning"),
        ],
    )
    assert len(report.errors) == 1
    assert report.kinds() == ["out_of_key", "weak_grounding"]


def test_evidence_citation_format():
    ev = Evidence(entry_id="harmony.seventh_chords.01", title="七和弦")
    assert ev.citation == "[harmony.seventh_chords.01] 七和弦"
