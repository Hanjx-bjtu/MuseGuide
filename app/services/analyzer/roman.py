"""罗马数字与功能标注（``MVP计划.md`` §3.4.1）。

输出 ``roman``（如 ``["I","V","vi","IV"]``）与 ``functions``
（``Tonic`` / ``Dominant`` / ``Subdominant`` / ``Other``），
与 §3.4.1 的输出结构逐字对应。
"""

from __future__ import annotations

from app.core.artifact import ChordSymbol, HarmonicFunction
from app.services.analyzer.key import NATURAL_MINOR_INTERVALS, MAJOR_INTERVALS, parse_key
from app.services.parser.chords import NOTE_TO_PITCH, simplify_quality

#: 大调各级的罗马数字（大写=大三和弦，小写=小三和弦，°=减和弦）
MAJOR_ROMANS = ("I", "ii", "iii", "IV", "V", "vi", "vii°")
#: 自然小调各级
MINOR_ROMANS = ("i", "ii°", "III", "iv", "v", "VI", "VII")

#: 罗马数字后缀（七和弦等），追加在基础级数之后
QUALITY_SUFFIX = {
    "maj7": "maj7",
    "min7": "7",
    "dom7": "7",
    "dim": "°",
}

#: 级数 → 功能。依据 Open Music Theory 的功能分组：
#: 主功能 I/vi（大调）、i/III/VI（小调）；属功能 V/vii°；下属功能 IV/ii。
MAJOR_FUNCTIONS: tuple[HarmonicFunction, ...] = (
    "Tonic", "Subdominant", "Tonic", "Subdominant", "Dominant", "Tonic", "Dominant",
)
MINOR_FUNCTIONS: tuple[HarmonicFunction, ...] = (
    "Tonic", "Subdominant", "Tonic", "Subdominant", "Dominant", "Subdominant", "Subdominant",
)


def _degree(root: str, tonic_pitch: int, intervals: tuple[int, ...]) -> int | None:
    """和弦根音在调内的级数（0-based）；不在调内返回 ``None``。"""
    pitch = NOTE_TO_PITCH.get(root)
    if pitch is None:
        return None
    offset = (pitch - tonic_pitch) % 12
    if offset not in intervals:
        return None
    return intervals.index(offset)


#: 各级「调内」应有的和弦性质（与 key.py 的调式表一致）
_MAJOR_EXPECTED = ("major", "minor", "minor", "major", "major", "minor", "dim")
_MINOR_EXPECTED = ("minor", "dim", "major", "minor", "minor", "major", "major")


def is_in_key(chord: ChordSymbol, key: str) -> bool:
    """和弦是否属于该调（**同时看根音与性质**）。

    ⚠️ 只检查根音是不够的：``Fm`` 的根音 F 确实在 C 大调音阶内，
    但 F 位置应该是大三和弦（IV），而 ``Fm`` 是小三和弦 —— 它其实是
    从平行小调借来的 iv 级和弦。

    这个区分至关重要：``MVP计划.md`` §3.9.3 的「建议 B：使用借用和弦
    ``C → G → Am → Fm``」正是靠它才能被识别为「引入了新的色彩」，
    而不是被误当成普通的 IV 级。P4 的 Grounding 校验同样依赖它。
    """
    tonic, mode = parse_key(key)
    tonic_pitch = NOTE_TO_PITCH.get(tonic)
    if tonic_pitch is None:
        return False

    intervals = MAJOR_INTERVALS if mode == "Major" else NATURAL_MINOR_INTERVALS
    expected_table = _MAJOR_EXPECTED if mode == "Major" else _MINOR_EXPECTED

    degree = _degree(chord.root, tonic_pitch, intervals)
    if degree is None:
        return False

    actual = simplify_quality(chord.quality)
    expected = simplify_quality(expected_table[degree])

    # 属七和弦（G7）在调内是正常现象，其简化类别为 other，需放行
    if chord.quality == "dom7" and expected == "major":
        return True
    # 七和弦比三和弦多一个音，不改变功能，简化后类别一致即可
    return actual == expected


def analyze_chords(chords: list[ChordSymbol], key: str) -> tuple[list[str], list[str], list[str]]:
    """标注整条和弦进行。

    :return: ``(romans, functions, notes)``
        ``romans`` 为罗马数字列表（调外和弦标为 ``?`` 并附原符号）；
        ``functions`` 为功能列表（调外标 ``Other``）；
        ``notes`` 为给人看的中文提示，用于解释异常。
    """
    tonic, mode = parse_key(key)
    tonic_pitch = NOTE_TO_PITCH.get(tonic)
    if tonic_pitch is None:
        return [], [], []

    intervals = MAJOR_INTERVALS if mode == "Major" else NATURAL_MINOR_INTERVALS
    base_romans = MAJOR_ROMANS if mode == "Major" else MINOR_ROMANS
    base_functions = MAJOR_FUNCTIONS if mode == "Major" else MINOR_FUNCTIONS

    romans: list[str] = []
    functions: list[str] = []
    notes: list[str] = []

    for chord in chords:
        degree = _degree(chord.root, tonic_pitch, intervals)
        if degree is None or not is_in_key(chord, key):
            # 调外和弦：可能是借用和弦或副属和弦。
            # 区分「根音在调外」与「根音在调内但性质不符」两种情形，
            # 后者更常见（如 C 大调里的 Fm），且更容易被用户忽略。
            romans.append("?")
            functions.append("Other")
            if degree is not None:
                notes.append(
                    f"{chord.raw} 的根音在 {key} 内，但和弦性质不属于该调"
                    f"（这是一个借用和弦，会带来额外的色彩）"
                )
            else:
                notes.append(f"{chord.raw} 不在 {key} 的自然音级内（可能是借用和弦或副属和弦）")
            continue

        roman = base_romans[degree]
        suffix = QUALITY_SUFFIX.get(chord.quality)
        if suffix and chord.quality in ("maj7", "min7"):
            roman = roman + suffix
        elif chord.quality == "dom7":
            roman = roman + "7"

        if chord.bass and chord.bass != chord.root:
            roman = roman + "/" + chord.bass

        romans.append(roman)
        functions.append(base_functions[degree])

    return romans, functions, notes


def annotate(chords: list[ChordSymbol], key: str) -> list[ChordSymbol]:
    """返回带 ``roman`` 与 ``function`` 的和弦列表（供契约填充）。"""
    romans, functions, _ = analyze_chords(chords, key)
    annotated: list[ChordSymbol] = []
    for chord, roman, function in zip(chords, romans, functions):
        annotated.append(
            chord.model_copy(
                update={
                    "roman": None if roman == "?" else roman,
                    "function": function,  # type: ignore[arg-type]
                }
            )
        )
    return annotated


def describe_progression(chords: list[ChordSymbol], key: str) -> str:
    """给用户看的一句和弦性质描述（通俗层用）。"""
    if not chords:
        return ""

    romans, _, _ = analyze_chords(chords, key)
    major_count = sum(1 for c in chords if simplify_quality(c.quality) == "major")
    minor_count = sum(1 for c in chords if simplify_quality(c.quality) == "minor")
    out_of_key = sum(1 for r in romans if r == "?")

    parts: list[str] = []
    if out_of_key:
        parts.append(f"其中 {out_of_key} 个和弦不属于这个调，会带来额外的色彩")
    if minor_count and not major_count:
        parts.append("整组都是小调和弦，听感偏柔和忧伤")
    elif major_count and not minor_count:
        parts.append("整组都是大调和弦，听感明亮稳定")
    elif minor_count and major_count:
        parts.append("大小调和弦混用，听感有明暗变化")
    return "；".join(parts)
