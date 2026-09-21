"""P2 解析器测试 —— 和弦与旋律文本解析。

对应 ``实现阶段计划.md`` §6.1 的 P2.1 / P2.2：
支持 ``|`` ``,`` 空格分隔与 ``C`` / ``Am`` / ``G7`` / ``Cmaj7`` / ``G/B`` 写法。
"""

from __future__ import annotations

import pytest

from app.services.parser.chords import (
    ChordParseError,
    normalize_note,
    parse_chord,
    parse_progression,
    simplify_quality,
)
from app.services.parser.melody import (
    MelodyParseError,
    note_to_midi,
    parse_melody,
    parse_notes,
)

# --------------------------------------------------------------------------- #
# 和弦解析
# --------------------------------------------------------------------------- #


def test_parse_mvp_example_progression():
    """§3.4.1 的输入示例。"""
    chords = parse_progression("C | G | Am | F")
    assert [c.raw for c in chords] == ["C", "G", "Am", "F"]
    assert [c.root for c in chords] == ["C", "G", "A", "F"]
    assert [c.quality for c in chords] == ["major", "major", "minor", "major"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("C | G | Am | F", ["C", "G", "Am", "F"]),
        ("C,G,Am,F", ["C", "G", "Am", "F"]),
        ("C G Am F", ["C", "G", "Am", "F"]),
        ("C→G→Am→F", ["C", "G", "Am", "F"]),
        ("C -> G -> Am -> F", ["C", "G", "Am", "F"]),
        ("C｜G｜Am｜F", ["C", "G", "Am", "F"]),
        ("C，G，Am，F", ["C", "G", "Am", "F"]),
        ("C、G、Am、F", ["C", "G", "Am", "F"]),
        ("  C  |  G  |  Am  |  F  ", ["C", "G", "Am", "F"]),
        ("C | G | Am | F |", ["C", "G", "Am", "F"]),
    ],
)
def test_parse_progression_separators(text, expected):
    """用户手写习惯差异很大，分隔符必须宽容。"""
    assert [c.raw for c in parse_progression(text)] == expected


@pytest.mark.parametrize(
    "text,root,quality",
    [
        ("C", "C", "major"),
        ("Am", "A", "minor"),
        ("Amin", "A", "minor"),
        ("A-", "A", "minor"),
        ("G7", "G", "dom7"),
        ("Cmaj7", "C", "maj7"),
        ("CM7", "C", "maj7"),
        ("CΔ7", "C", "maj7"),
        ("Am7", "A", "min7"),
        ("Am9", "A", "min7"),
        ("Cdim", "C", "dim"),
        ("C°", "C", "dim"),
        ("Csus4", "C", "sus4"),
        ("Cadd9", "C", "add9"),
        ("F#m", "F#", "minor"),
        ("Bbmaj7", "Bb", "maj7"),
    ],
)
def test_parse_chord_qualities(text, root, quality):
    chord = parse_chord(text)
    assert chord.root == root
    assert chord.quality == quality


def test_quality_suffix_precedence():
    """回归：长后缀必须优先匹配。

    ``maj7`` 若被 ``m`` 抢先匹配，会变成小和弦；``m7`` 若被 ``m`` 抢先，
    七音会丢失。这是解析器最容易出错的地方。
    """
    assert parse_chord("Cmaj7").quality == "maj7"
    assert parse_chord("Am7").quality == "min7"
    assert parse_chord("Cm").quality == "minor"
    assert parse_chord("Cdim7").quality == "dim"
    assert parse_chord("Cmaj9").quality == "maj7"


def test_parse_inversion_and_bass():
    """转位 / 斜杠低音（§3.4.1 进阶写法）。"""
    chord = parse_chord("G/B")
    assert chord.root == "G"
    assert chord.bass == "B"
    assert chord.raw == "G/B"

    plain = parse_chord("G")
    assert plain.bass is None


def test_parse_progression_with_inversions():
    chords = parse_progression("C | G/B | Am | F/A")
    assert [c.bass for c in chords] == [None, "B", None, "A"]


def test_unicode_accidentals_are_normalized():
    """用户可能输入 ♯ / ♭，必须识别。"""
    assert parse_chord("F♯m").root == "F#"
    assert parse_chord("B♭").root == "Bb"
    assert normalize_note("c#") == "C#"


def test_lowercase_roots_are_accepted():
    assert parse_chord("c").root == "C"
    assert parse_chord("am").quality == "minor"


def test_empty_input_returns_empty_list():
    assert parse_progression("") == []
    assert parse_progression("   ") == []


def test_invalid_chord_raises_with_readable_message():
    """非法输入要给出可读原因（§1.1 目标 3）。"""
    with pytest.raises(ChordParseError) as exc:
        parse_progression("H | X")
    assert "无法识别" in str(exc.value)


def test_decorative_noise_is_skipped():
    """小节号等噪声不应导致解析失败。"""
    chords = parse_progression("1. C | 2. G | 3. Am")
    assert [c.raw for c in chords] == ["C", "G", "Am"]


def test_simplify_quality_groups():
    assert simplify_quality("major") == "major"
    assert simplify_quality("maj7") == "major"
    assert simplify_quality("minor") == "minor"
    assert simplify_quality("min7") == "minor"
    assert simplify_quality("dom7") == "other"


# --------------------------------------------------------------------------- #
# 旋律解析
# --------------------------------------------------------------------------- #


def test_parse_mvp_melody_example():
    """§3.4.2 的输入示例，输出须逐字对应。

    注意 ``E4 G4 A4 G4 E4`` 是**回文**：它的重复是「原样折返」，
    而不是连续的重复片段 —— 这正是 ``repetition: true`` 的来源。
    """
    melody = parse_melody("E4 G4 A4 G4 E4")
    assert melody.notes == ["E4", "G4", "A4", "G4", "E4"]
    assert melody.range == ["E4", "A4"]
    assert melody.contour == "up-down"
    assert melody.repetition is True


@pytest.mark.parametrize(
    "text",
    ["E4 G4 A4", "E4,G4,A4", "E4|G4|A4", "E4-G4-A4", "E4，G4，A4"],
)
def test_melody_separators(text):
    assert parse_notes(text) == ["E4", "G4", "A4"]


def test_melody_defaults_to_octave_four():
    assert parse_notes("E G A") == ["E4", "G4", "A4"]


def test_note_to_midi_anchors_at_c4_60():
    assert note_to_midi("C4") == 60
    assert note_to_midi("A4") == 69
    assert note_to_midi("C5") == 72
    assert note_to_midi("E4") == 64


def test_range_uses_actual_extremes():
    melody = parse_melody("C4 G5 E4")
    assert melody.range == ["C4", "G5"]


def test_repetition_detects_retrograde():
    """回归：逆行重复（片段倒着再现）必须被识别。

    §3.4.2 的示例 `E4 G4 A4 G4 E4` 没有任何连续重复片段，
    它的重复是折返式的 —— 只做连续片段检测会得到 false，与设计文档不符。
    """
    assert parse_melody("E4 G4 A4 G4 E4").repetition is True  # 回文
    assert parse_melody("C4 D4 E4 D4 C4").repetition is True  # 回文
    assert parse_melody("C4 E4 G4 E4 C4 G4").repetition is True  # 含逆行片段


@pytest.mark.parametrize(
    "text,expected",
    [
        ("C4 D4 E4 F4", "up"),
        ("F4 E4 D4 C4", "down"),
        ("C4 E4 G4 E4 C4", "up-down"),
        ("G4 E4 C4 E4 G4", "down-up"),
        ("C4 C4 C4 C4", "flat"),
    ],
)
def test_contour_detection(text, expected):
    assert parse_melody(text).contour == expected


def test_repetition_detection():
    assert parse_melody("C4 D4 C4 D4 C4").repetition is True
    assert parse_melody("C4 C4").repetition is True
    assert parse_melody("C4 D4 E4 F4 G4").repetition is False


def test_invalid_note_raises():
    with pytest.raises(MelodyParseError):
        parse_melody("H4 X5")


def test_empty_melody_raises():
    with pytest.raises(MelodyParseError):
        parse_melody("   ")


def test_melody_output_is_contract_valid():
    import json

    from app.core.artifact import MelodyInfo

    melody = parse_melody("E4 G4 A4")
    restored = MelodyInfo.model_validate_json(melody.model_dump_json())
    assert restored == melody
    assert json.loads(melody.model_dump_json())["notes"] == ["E4", "G4", "A4"]
