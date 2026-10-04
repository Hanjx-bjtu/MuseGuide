"""P4 测试：Grounding 校验器 + 幻觉注入（阶段门 G4）。

**这是本项目「把幻觉变成可测指标」的实证。**
验收口径（``docs/ACCEPTANCE.md`` §3.4）：注入式测试捕获率 ≥ 0.80。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.brief import CreativeIntent
from app.core.evidence import Evidence, EvidenceSource
from app.core.plan import Advice, AdviceOption
from app.services.generation.grounding import (
    CITATION_PATTERN,
    check_chords_in_key,
    check_citations,
    check_constraints,
    check_goal_match,
    check_layman_present,
    check_weak_grounding,
    summarize,
    verify_advice,
)

INJECTION_PATH = (
    Path(__file__).resolve().parents[1] / "datasets" / "advice_eval" / "hallucination_injection.json"
)


@pytest.fixture(scope="module")
def injection() -> dict:
    return json.loads(INJECTION_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def evidence(injection) -> list[Evidence]:
    return [
        Evidence(
            entry_id=item["entry_id"],
            title=item["title"],
            layman_title=item.get("layman_title"),
            content=item.get("content", ""),
            layman_content=item.get("layman_content"),
            tags=item.get("tags", []),
            source=EvidenceSource(title="Open Music Theory", url="u", license="CC BY-SA 4.0"),
        )
        for item in injection["evidence"]
    ]


def _build_advice(payload: dict, evidence: list[Evidence]) -> Advice:
    return Advice(
        analysis=payload.get("analysis", ""),
        problems=payload.get("problems", []),
        options=[
            AdviceOption(
                label=o["label"],
                chords=o["chords"],
                feature=o["feature"],
                reason=o["reason"],
                theory=o.get("theory", []),
            )
            for o in payload["options"]
        ],
        evidence=evidence,
    )


# --------------------------------------------------------------------------- #
# 幻觉注入：捕获率 >= 0.80
# --------------------------------------------------------------------------- #


def test_hallucination_capture_rate(injection, evidence):
    """核心指标：注入的幻觉必须被捕获。

    对照组（inj.clean）不计入捕获率 —— 它验证的是**不误报**。
    """
    cases = [c for c in injection["cases"] if c["id"] != "inj.clean"]
    captured = 0
    misses = []

    for case in cases:
        advice = _build_advice(case["advice"], evidence)
        report = verify_advice(
            advice,
            evidence=evidence,
            intent=CreativeIntent(emotion=["温暖"]),
            goal_text=case.get("goal", ""),
            key=case.get("key"),
            constraints=case.get("constraints", []),
            level=case.get("level", "some"),
        )
        kinds = set(report.kinds())
        expected = set(case["expect_kinds"])
        if expected & kinds:
            captured += 1
        else:
            misses.append((case["id"], case["category"], sorted(expected), sorted(kinds)))

    rate = captured / len(cases)
    assert rate >= 0.80, f"幻觉捕获率 {rate:.2f} < 0.80，未捕获：{misses}"


def test_clean_case_produces_no_errors(injection, evidence):
    """对照组：完全合规的建议不得产生 error 级问题（防误报）。

    **假阳性比漏报更有害** —— 如果合规的建议也被判为幻觉，
    这个指标就没人会信，也就失去了意义。
    """
    case = next(c for c in injection["cases"] if c["id"] == "inj.clean")
    advice = _build_advice(case["advice"], evidence)
    report = verify_advice(
        advice,
        evidence=evidence,
        intent=CreativeIntent(emotion=["温暖"]),
        goal_text=case["goal"],
        key=case["key"],
        level="some",
    )
    assert report.ok, f"合规建议被误报为 error：{[i.detail for i in report.errors]}"


def test_injection_dataset_is_well_formed(injection):
    """注入集自身必须完整。"""
    ids = [c["id"] for c in injection["cases"]]
    assert len(ids) == len(set(ids))
    for case in injection["cases"]:
        assert case.get("expect_kinds") is not None, f"{case['id']} 缺少期望"
        assert "category" in case, f"{case['id']} 缺少分类"
        assert len(case["advice"]["options"]) >= 2, "契约要求 2~3 个方案"


def test_injection_covers_all_issue_kinds(injection):
    """注入集必须覆盖所有校验维度，否则捕获率没有说服力。"""
    kinds = {k for c in injection["cases"] for k in c["expect_kinds"]}
    assert kinds == {
        "citation_out_of_range",
        "out_of_key",
        "goal_mismatch",
        "missing_layman",
        "weak_grounding",
    }, f"注入集未覆盖全部问题类型：{kinds}"


# --------------------------------------------------------------------------- #
# 各校验项的单元行为
# --------------------------------------------------------------------------- #


def test_citation_pattern_matches_variants():
    assert CITATION_PATTERN.findall("见 [来源1] 与 [2]") == ["1", "2"]
    assert CITATION_PATTERN.findall("无引用") == []


def test_check_citations_flags_out_of_range(evidence):
    issues = check_citations("参考 [来源5]", evidence)
    assert len(issues) == 1
    assert issues[0].kind == "citation_out_of_range"
    assert issues[0].severity == "error"


def test_check_citations_accepts_valid_range(evidence):
    assert check_citations("参考 [来源1] 与 [2]", evidence) == []


def test_check_chords_in_key_accepts_diatonic_chords():
    option = AdviceOption(
        label="A", chords=["C", "G", "Am", "F"], feature="f", reason="r", theory=[]
    )
    assert check_chords_in_key(option, "C Major") == []


def test_check_chords_in_key_flags_foreign_chord():
    option = AdviceOption(label="A", chords=["Abm"], feature="f", reason="r", theory=[])
    issues = check_chords_in_key(option, "C Major")
    assert len(issues) == 1
    assert issues[0].kind == "out_of_key"
    assert issues[0].severity == "error"


def test_check_chords_in_key_allows_borrowed_chord_as_warning():
    """借用和弦是合法手法 —— 只记 warning，不判为错误。

    这是关键区分：``C | G | Am | Fm`` 正是 ``MVP计划.md`` §3.9.3 给出的
    「建议 B」，它必须被接受。
    """
    option = AdviceOption(label="A", chords=["Fm"], feature="f", reason="r", theory=[])
    issues = check_chords_in_key(option, "C Major")
    assert len(issues) == 1
    assert issues[0].severity == "warning", "借用和弦不应判为 error"


def test_check_chords_in_key_skips_when_no_key():
    option = AdviceOption(label="A", chords=["H", "X"], feature="f", reason="r", theory=[])
    assert check_chords_in_key(option, None) == []


# --------------------------------------------------------------------------- #
# 借用和弦与副属和弦的识别（回归：真实 LLM 暴露的假阳性）
# --------------------------------------------------------------------------- #


def test_secondary_dominant_is_not_flagged_as_hallucination():
    """回归：副属和弦 ``V/vi`` 不得被误判为幻觉。

    实测教训（接入真实 DeepSeek 后暴露）：模型给出「C | G | E7 | Am | F」，
    E7 是标准的 ``V/vi``，却被校验器判为 error。

    **E7 是教科书级用法，而且本项目知识库里就有专门一篇讲它**
    （``harmony.secondary_dominant.01``）。一个把自家知识库推荐的手法
    判为幻觉的校验器，比没有校验器更糟。
    """
    option = AdviceOption(
        label="A", chords=["C", "G", "E7", "Am", "F"], feature="f", reason="r", theory=[]
    )
    issues = check_chords_in_key(option, "C Major")
    e7_issues = [i for i in issues if "E7" in i.detail]
    assert e7_issues, "E7 应被识别为调外和弦并记录"
    assert e7_issues[0].severity == "warning", "副属和弦不应判为 error"


def test_harmonic_minor_dominant_is_not_flagged():
    """回归：小调的和声小调属和弦（A 小调里的 E7）必须放行。

    自然小调的 V 级是小三和弦，但实际创作几乎总是升高七度音变成大三和弦。
    这是小调最标准的终止写法，引入的 G# 正是和声小调的定义特征。
    """
    option = AdviceOption(
        label="A", chords=["Am", "F", "C", "E7", "Am"], feature="f", reason="r", theory=[]
    )
    issues = check_chords_in_key(option, "A Minor")
    e7_issues = [i for i in issues if "E7" in i.detail]
    assert e7_issues, "E7 在自然小调音阶外，应被记录"
    assert e7_issues[0].severity == "warning", "和声小调属和弦不应判为 error"


def test_borrowed_chord_inversion_is_recognized():
    """回归：``Fm/Ab`` 是 iv 借用和弦的第一转位，转位不改变功能。

    早期实现把整个斜杠符号拿去判定，导致转位形态被漏掉并报 error。
    """
    option = AdviceOption(label="A", chords=["Fm/Ab"], feature="f", reason="r", theory=[])
    issues = check_chords_in_key(option, "C Major")
    assert issues
    assert issues[0].severity == "warning", "借用和弦的转位不应判为 error"


def test_diatonic_chord_in_minor_key_is_not_flagged():
    """A 小调自然音阶里的 F 大三和弦属调内，不应被标记。"""
    option = AdviceOption(label="A", chords=["F"], feature="f", reason="r", theory=[])
    assert check_chords_in_key(option, "A Minor") == []


def test_root_in_key_but_wrong_quality_is_warning_not_error():
    """根音在调内但性质不符（A 小调里的 Fm）判 warning 而非 error。

    它**通常**是模型的小失误，但不构成幻觉 ——
    判 error 会拦掉一个可能有趣的创意。
    """
    option = AdviceOption(label="A", chords=["Fm"], feature="f", reason="r", theory=[])
    issues = check_chords_in_key(option, "A Minor")
    assert issues
    assert issues[0].severity == "warning"


def test_truly_foreign_chord_is_still_an_error():
    """**没被削弱**：真正编造的和弦仍必须判 error。

    上面几处放宽都是为了放过「合法但调外」的手法；
    这条测试确保放宽没有把幻觉检测一起放掉。
    """
    for chord in ("Abm", "F#m", "Bbm"):
        option = AdviceOption(label="A", chords=[chord], feature="f", reason="r", theory=[])
        issues = check_chords_in_key(option, "C Major")
        assert issues, f"{chord} 应被标记"
        assert issues[0].severity == "error", f"{chord} 根音不在调内，应判 error"


def test_check_layman_only_applies_to_zero_level():
    """零基础档才会检查通俗解释。

    注意：契约层（``AdviceOption`` 的 validator）已经保证 ``reason`` 非空，
    因此这里构造的是「reason 只有专业内容、没有通俗说法」的可达状态 ——
    直接传空字符串会在构造 AdviceOption 时就抛异常。
    """
    option = AdviceOption(
        label="A", chords=["C"], feature="f", reason="采用 iv 级借用", theory=[]
    )
    # 契约层已挡住空 reason，这一点本身也值得断言
    with pytest.raises(ValueError):
        AdviceOption(label="B", chords=["C"], feature="f", reason="", theory=[])

    # 零基础档：reason 非空即通过本项（真正的通俗性由术语守卫负责）
    assert check_layman_present(option, "zero") == []
    assert check_layman_present(option, "some") == []


def test_check_constraints_flags_violation():
    option = AdviceOption(
        label="A", chords=["Am"], feature="把整体改成小调", reason="r", theory=[]
    )
    issues = check_constraints(option, ["保持温暖"])
    assert issues
    assert issues[0].severity == "error"


def test_check_constraints_passes_when_respected():
    option = AdviceOption(
        label="A", chords=["Cmaj7"], feature="保持温暖的同时增加色彩", reason="r", theory=[]
    )
    assert check_constraints(option, ["保持温暖"]) == []


def test_check_goal_match_accepts_relevant_suggestion():
    """回归：真正回应目标的建议不得被误判。

    早期用中文 bigram 求重叠时，「使用借用和弦」因为不含「普通」二字
    被判为「未体现目标」—— 假阳性会让这个指标失去可信度。
    """
    option = AdviceOption(
        label="建议 B：使用借用和弦",
        chords=["Fm"],
        feature="引入阴影感但不破坏整体温暖",
        reason="多一丝忧伤，不那么普通。",
        theory=["调式借用"],
    )
    assert check_goal_match(option, CreativeIntent(emotion=["温暖"]), "保持温暖，但不要太普通") == []


def test_check_goal_match_flags_irrelevant_suggestion():
    option = AdviceOption(
        label="建议 A",
        chords=["X"],
        feature="把编曲换成管弦乐配器",
        reason="听起来更宏大。",
        theory=["配器法"],
    )
    issues = check_goal_match(option, CreativeIntent(emotion=["温暖"]), "保持温暖，但不要太普通")
    assert issues


def test_check_weak_grounding_skips_without_evidence():
    """没有证据时不做弱依据判定 —— 否则会把「无知识库」误报为幻觉。"""
    option = AdviceOption(label="A", chords=["C"], feature="f", reason="r", theory=[])
    assert check_weak_grounding(option, []) == []


def test_check_weak_grounding_detects_unrelated_option(evidence):
    option = AdviceOption(
        label="A",
        chords=["C"],
        feature="改成三拍子圆舞曲并加入弦乐编写",
        reason="非常华丽。",
        theory=["配器法"],
    )
    assert check_weak_grounding(option, evidence)


def test_check_weak_grounding_accepts_grounded_option(evidence):
    option = AdviceOption(
        label="A",
        chords=["Cmaj7"],
        feature="通过七和弦增加和弦色彩",
        reason="更柔和梦幻。",
        theory=["七和弦扩展色彩"],
    )
    assert check_weak_grounding(option, evidence) == []


# --------------------------------------------------------------------------- #
# 汇总指标（供 P6 报告使用）
# --------------------------------------------------------------------------- #


def test_summarize_counts_by_kind(injection, evidence):
    case = next(c for c in injection["cases"] if c["id"] == "inj.001")
    advice = _build_advice(case["advice"], evidence)
    report = verify_advice(
        advice, evidence=evidence, intent=CreativeIntent(), goal_text="", key="C Major"
    )
    summary = summarize(report)
    assert summary["citations_fabricated"] >= 1
    assert summary["total"] == len(report.issues)


def test_verify_advice_returns_ok_only_without_errors(evidence):
    bad = Advice(
        analysis="见 [来源9]",
        problems=["x"],
        options=[
            AdviceOption(label="A", chords=["C"], feature="f", reason="r", theory=[]),
            AdviceOption(label="B", chords=["G"], feature="f", reason="r", theory=[]),
        ],
        evidence=evidence,
    )
    report = verify_advice(bad, evidence=evidence, key="C Major")
    assert report.ok is False
    assert report.errors


def test_verify_advice_flags_citation_when_no_evidence():
    """引用了来源但本次没有任何证据 —— 这是最赤裸的编造。"""
    advice = Advice(
        analysis="根据 [来源1] 的分析",
        problems=["x"],
        options=[
            AdviceOption(label="A", chords=["C"], feature="f", reason="r", theory=[]),
            AdviceOption(label="B", chords=["G"], feature="f", reason="r", theory=[]),
        ],
    )
    report = verify_advice(advice, evidence=[], key=None)
    assert any(i.kind == "citation_out_of_range" for i in report.issues)
