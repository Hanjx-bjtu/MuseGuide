"""P1 通俗化输出测试 —— 阶段门 G1「§3.8.2 的 7 条术语映射全就位」。

对齐 ``MVP计划.md`` §3.8.2（转换规则表）与 §3.8.3（三档输出策略）。
"""

from __future__ import annotations

import pytest

from app.core.options import (
    EMOTION_OPTIONS,
    STYLE_OPTIONS,
    TEMPO_OPTIONS,
    Option,
    emotion_option,
    find_option,
    selection_payload,
    style_option,
    tempo_bpm,
    tempo_option_by_label,
)
from app.services.layman import (
    BANNED_TERMS,
    STYLE_GUIDES,
    TERM_MAP,
    TERM_NOTE_LOOKUP,
    find_banned_terms,
    has_unexplained_terms,
    prompt_style_block,
    style_guide,
    to_layman,
)

# --------------------------------------------------------------------------- #
# §3.8.2：七条转换规则
# --------------------------------------------------------------------------- #

TERM_CASES = [
    ("I → V → vi → IV", "流行歌最常用的走向之一"),
    ("功能关系稳定", "听起来平稳，没有太强的起伏"),
    ("和声张力较低", "比较温和，没有紧张感"),
    ("色彩和弦较少", "比较素，缺少层次感"),
    ("Modal Interchange", "从别的调借一个和弦，增加色彩"),
    ("Cadence", "音乐的「句号」，让人感觉告一段落"),
    ("Tension / Resolution", "紧张感→释放感，像讲故事的高潮和结尾"),
]


def test_term_map_covers_all_seven_rules():
    """§3.8.2 的表格有七行 —— 一条都不能少。"""
    assert len(TERM_MAP) == 7
    mapped = {e.professional: e.layman for e in TERM_MAP}
    for professional, layman in TERM_CASES:
        assert mapped.get(professional) == layman, f"{professional!r} 的通俗表述不符"


@pytest.mark.parametrize("professional,layman", TERM_CASES, ids=[c[0] for c in TERM_CASES])
def test_term_map_replaces_professional_phrase(professional, layman):
    """专业表述出现在文本中时，必须被替换为通俗表述。"""
    text = f"你的和弦进行是 {professional}，所以听感比较稳定。"
    out = to_layman(text, "zero")
    assert layman in out
    assert professional not in out


def test_to_layman_longest_key_wins():
    """长键优先：`I → V → vi → IV` 不应被更短的键部分替换。"""
    out = to_layman("当前是 I → V → vi → IV", "zero")
    assert out == "当前是 流行歌最常用的走向之一"


def test_to_layman_advanced_level_keeps_terms():
    """进阶用户版本保留专业术语（§3.8.3）。"""
    text = "当前进行为 I → V → vi → IV"
    assert to_layman(text, "advanced") == text


def test_to_layman_some_level_uses_notes():
    """中间状态改为「通俗说法 + 术语旁注」（§3.8.3）。"""
    out = to_layman("这里用了 Modal Interchange", "some")
    assert out in ("这里用了 调式借用（Modal Interchange）",)
    assert "调式借用" in out


def test_term_note_lookup_has_note_for_every_rule():
    """每条规则都要有旁注写法，否则中间档会退化成原样输出。"""
    for entry in TERM_MAP:
        assert entry.note, f"{entry.professional!r} 缺少旁注写法"
        assert TERM_NOTE_LOOKUP[entry.professional.lower()] == entry.note


def test_to_layman_is_case_insensitive():
    assert "从别的调借一个和弦，增加色彩" in to_layman("使用了 modal interchange", "zero")


def test_to_layman_handles_empty_input():
    assert to_layman("", "zero") == ""
    assert to_layman("普通文本", "zero") == "普通文本"


# --------------------------------------------------------------------------- #
# §3.8.3：三档输出策略
# --------------------------------------------------------------------------- #


def test_all_three_levels_have_complete_guides():
    assert set(STYLE_GUIDES) == {"zero", "some", "advanced"}
    for level, guide in STYLE_GUIDES.items():
        for field in ("audience", "tone", "terms", "structure", "forbidden", "example"):
            assert guide.get(field), f"{level} 档缺少 {field}"


def test_style_guide_falls_back_to_zero():
    """未知档位必须回落到零基础 —— 安全默认。"""
    assert style_guide("unknown") == STYLE_GUIDES["zero"]  # type: ignore[arg-type]


def test_prompt_style_block_is_renderable():
    block = prompt_style_block("zero")
    assert "[输出风格要求]" in block
    assert "禁止" in block


def test_zero_guide_forbids_terminology():
    """零基础档必须明确禁止术语，这是它区别于其它档的核心。"""
    assert "禁止" in STYLE_GUIDES["zero"]["forbidden"]


# --------------------------------------------------------------------------- #
# 术语守卫（P6 会复用）
# --------------------------------------------------------------------------- #


def test_banned_terms_detected():
    assert find_banned_terms("这里涉及三度叠置") == ["三度叠置"]
    assert find_banned_terms("很通俗的一句话") == []


def test_has_unexplained_terms_only_applies_to_zero():
    text = "这里涉及三度叠置"
    assert has_unexplained_terms(text, "zero") is True
    assert has_unexplained_terms(text, "some") is False
    assert has_unexplained_terms(text, "advanced") is False


def test_banned_terms_do_not_overlap_with_layman_outputs():
    """术语守卫不应误伤我们自己产出的通俗表述。"""
    for _professional, layman in TERM_CASES:
        assert find_banned_terms(layman) == [], f"通俗表述 {layman!r} 被守卫误判"


# --------------------------------------------------------------------------- #
# 受控词表（P1.1）
# --------------------------------------------------------------------------- #


def test_tempo_options_have_ui_labels_and_bpm():
    """§3.10.3 的每个速度选项都要有日常类比 + BPM。"""
    assert len(TEMPO_OPTIONS) >= 3
    for opt in TEMPO_OPTIONS:
        assert opt.label and opt.concept and opt.hint
        assert opt.bpm and 40 <= opt.bpm <= 200
        assert "像" in opt.label, f"{opt.label!r} 缺少日常类比（§3.10.3）"


def test_emotion_and_style_options_have_hints():
    """每个选项都必须带通俗说明（§3.10.2）。"""
    for options in (EMOTION_OPTIONS, STYLE_OPTIONS):
        assert len(options) >= 6 if options is STYLE_OPTIONS else len(options) >= 8
        for opt in options:
            assert opt.label and opt.concept and opt.hint, f"{opt} 缺少说明"


def test_options_are_unique():
    for options in (TEMPO_OPTIONS, EMOTION_OPTIONS, STYLE_OPTIONS):
        concepts = [o.concept for o in options]
        labels = [o.label for o in options]
        assert len(concepts) == len(set(concepts)), "概念名重复"
        assert len(labels) == len(set(labels)), "界面文案重复"


def test_tempo_bpm_lookup():
    assert tempo_bpm("很慢") == 60
    assert tempo_bpm("中等偏慢") == 82
    assert tempo_bpm("中等") == 100
    assert tempo_bpm("未知说法") == 82  # 默认中等偏慢
    assert tempo_bpm(None) == 82


def test_find_option_matches_label_concept_and_alias():
    assert tempo_option_by_label("很慢，像翻相册").concept == "很慢"
    assert tempo_option_by_label("很慢").concept == "很慢"
    assert tempo_option_by_label("不存在的速度") is None
    assert style_option("folk").concept == "民谣"
    assert emotion_option("伤感").concept == "伤感"


def test_selection_payload_is_frontend_ready():
    """下发给界面的载荷必须含 UI 文案与说明（§3.10.2）。"""
    payload = selection_payload()
    assert set(payload) == {"emotion", "style", "tempo"}
    for group in payload.values():
        assert group
        for item in group:
            assert set(item) >= {"label", "concept", "hint"}


def test_option_requires_all_user_facing_fields():
    """Option 契约允许空 hint（默认值），但受控词表里的项必须填 —— 由上面的测试保证。"""
    opt = Option(label="x", concept="y")
    assert opt.hint == ""
    assert opt.bpm is None


def test_find_option_handles_none_and_blank():
    assert find_option(TEMPO_OPTIONS, None) is None
    assert find_option(TEMPO_OPTIONS, "   ") is None
