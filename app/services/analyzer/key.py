"""调性识别（``MVP计划.md`` §3.4.1）。

**双路策略**（§3.4.1「优先使用 music21 进行调性识别与和弦分析；
若输入简单，可用自定义映射表快速处理」）：

1. **和弦证据法**（自建，主）：用户给的是和弦进行，
   用「各调的和弦集合覆盖度 + 首尾和弦加权」投票，这对和弦文本最可靠。
2. **music21 交叉验证**（可选）：把和弦音展开成音高序列后交给 music21 的
   Krumhansl-Schmuckler 分析器，作为第二个意见。

两路一致 → ``key_confidence`` 高；不一致 → 取和弦证据法结果并降低置信度。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.artifact import ChordSymbol
from app.services.parser.chords import NOTE_TO_PITCH, simplify_quality

#: 调性 → (音级集合, 各级和弦性质)
#: 大调音阶：全全半全全全半；小调用自然小调并额外承认和声小调的 V。
MAJOR_INTERVALS = (0, 2, 4, 5, 7, 9, 11)
NATURAL_MINOR_INTERVALS = (0, 2, 3, 5, 7, 8, 10)

#: 大调各级三和弦性质（I ii iii IV V vi vii°）
MAJOR_TRIAD_QUALITIES = ("major", "minor", "minor", "major", "major", "minor", "dim")
#: 自然小调各级三和弦性质（i ii° III iv v VI VII）
MINOR_TRIAD_QUALITIES = ("minor", "dim", "major", "minor", "minor", "major", "major")

#: 各调的常见程度，用于在同分情况下优先选择更常用的调
KEY_PREFERENCE_ORDER = (
    "C", "G", "D", "A", "E", "F", "Bb", "Eb", "Ab", "B", "F#", "Db",
)

FLAT_TO_SHARP_ROOTS = {
    "Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#",
}


@dataclass(frozen=True)
class KeyCandidate:
    """一个候选调性及其得分。"""

    key: str
    score: float
    matched: int
    total: int


def _root_pitch(root: str) -> int | None:
    return NOTE_TO_PITCH.get(root)


#: 和弦性质 → 相对根音的音程（半音数），用于展开成实际音高
QUALITY_INTERVALS: dict[str, tuple[int, ...]] = {
    "major": (0, 4, 7),
    "minor": (0, 3, 7),
    "dom7": (0, 4, 7, 10),
    "maj7": (0, 4, 7, 11),
    "min7": (0, 3, 7, 10),
    "dim": (0, 3, 6),
    "aug": (0, 4, 8),
    "sus4": (0, 5, 7),
    "add9": (0, 4, 7, 14),
    "other": (0, 4, 7),
}

_PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def chord_pitches(chord: ChordSymbol, octave: int = 4) -> list[str]:
    """把和弦展开为实际音高名列表（如 Cmaj7 → ['C4','E4','G4','B4']）。

    低音（转位）会一并加入最低声部。
    """
    root = NOTE_TO_PITCH.get(chord.root)
    if root is None:
        return []
    intervals = QUALITY_INTERVALS.get(chord.quality, QUALITY_INTERVALS["other"])

    pitches = [_PITCH_NAMES[(root + i) % 12] + str(octave + (root + i) // 12) for i in intervals]

    if chord.bass and chord.bass != chord.root:
        bass_pitch = NOTE_TO_PITCH.get(chord.bass)
        if bass_pitch is not None:
            pitches.insert(0, _PITCH_NAMES[bass_pitch % 12] + str(octave - 1))
    return pitches


def key_pitch_classes(key: str) -> set[int]:
    """取某调的音级集合（大调或自然小调）。"""
    tonic_name, mode = parse_key(key)
    tonic = NOTE_TO_PITCH.get(tonic_name)
    if tonic is None:
        return set()
    intervals = MAJOR_INTERVALS if mode == "Major" else NATURAL_MINOR_INTERVALS
    return {(tonic + i) % 12 for i in intervals}


def parse_key(key: str) -> tuple[str, str]:
    """``"C Major"`` → ``("C", "Major")``；``"A Minor"`` → ``("A", "Minor")``。"""
    if not key:
        return "", "Major"
    parts = key.strip().split()
    tonic = parts[0].capitalize()
    mode = "Minor" if len(parts) > 1 and parts[1].lower().startswith("min") else "Major"
    if len(parts) == 1 and key.strip().lower().endswith("m") and len(tonic) > 1:
        # 兼容 "Am" 这种写法
        tonic, mode = tonic[:-1].capitalize(), "Minor"
    return tonic, mode


def format_key(tonic: str, mode: str) -> str:
    """规范化为 ``"C Major"`` 形式 —— 契约里的 ``key`` 字段格式。"""
    return f"{tonic} {mode}"


def _chord_fits(chord: ChordSymbol, tonic_pitch: int, intervals: tuple[int, ...]) -> bool:
    """和弦的根音是否落在该调内。"""
    root = _root_pitch(chord.root)
    if root is None:
        return False
    return (root - tonic_pitch) % 12 in intervals


def detect_key_from_chords(chords: list[ChordSymbol]) -> KeyCandidate | None:
    """用和弦进行推断调性（主路径）。

    **评分的关键在于「谁更像主和弦」，而不是「谁装得下更多和弦」。**
    实测教训：``C | G | Am | F`` 的四个和弦同属 C 大调与 F 大调
    （C=I,G=V,Am=vi,F=IV vs C=V,G=II,Am=iii,F=I），
    若只数「有多少和弦在调内」，两者得分完全相同，胜负取决于枚举顺序 ——
    那等于没有识别，只是碰运气。

    因此真正的判据是**和弦的功能角色**：

    * 首个和弦是主和弦 +2.5（歌曲几乎总是从主和弦开始）
    * 末个和弦是主和弦 +2.0（终止感）
    * 主和弦在句中至少出现一次 +0.5
    * 属功能（V / vii°）存在 +0.5 —— 有属才有调性中心
    * 和弦性质与该级应有性质一致 +0.5
    * 和弦根音在调内 +1.0
    * **关系大小调消歧**：自然小调若没有出现「大三级属和弦」，
      说明属功能证据不存在，扣分（否则 C 大调的歌会被判成 A 小调）
    """
    if not chords:
        return None

    best: KeyCandidate | None = None
    for tonic_name in KEY_PREFERENCE_ORDER:
        tonic_pitch = NOTE_TO_PITCH[tonic_name]
        for mode, intervals, qualities in (
            ("Major", MAJOR_INTERVALS, MAJOR_TRIAD_QUALITIES),
            ("Minor", NATURAL_MINOR_INTERVALS, MINOR_TRIAD_QUALITIES),
        ):
            score = 0.0
            matched = 0
            tonic_seen = False
            dominant_seen = False

            for idx, chord in enumerate(chords):
                root = _root_pitch(chord.root)
                if root is None or not _chord_fits(chord, tonic_pitch, intervals):
                    continue
                matched += 1
                score += 1.0

                degree = intervals.index((root - tonic_pitch) % 12)
                expected = qualities[degree]
                if simplify_quality(chord.quality) == simplify_quality(expected):
                    score += 0.5

                if degree == 0 and simplify_quality(chord.quality) == simplify_quality(
                    qualities[0]
                ):
                    tonic_seen = True

                # 属功能：V 级大三和弦，或 vii° 减和弦
                if degree == 4 and simplify_quality(chord.quality) == "major":
                    dominant_seen = True
                if degree == 6 and chord.quality == "dim":
                    dominant_seen = True

                if idx == 0 and degree == 0:
                    score += 2.5
                if idx == len(chords) - 1 and degree == 0:
                    score += 2.0

            if matched == 0:
                continue

            if tonic_seen:
                score += 0.5
            if dominant_seen:
                score += 0.5

            # 关系大小调消歧
            if mode == "Minor" and not dominant_seen:
                score -= 1.0

            normalized = score / len(chords)
            candidate = KeyCandidate(
                key=format_key(tonic_name, mode),
                score=round(normalized, 4),
                matched=matched,
                total=len(chords),
            )
            if best is None or candidate.score > best.score:
                best = candidate

    return best


def detect_key_with_music21(chords: list[ChordSymbol]) -> str | None:
    """用 music21 的 Krumhansl-Schmuckler 分析器做交叉验证。

    ⚠️ **必须喂给它完整的和弦音，而不是只喂根音。**
    实测教训：只把根音序列（如 ``C G A F``）交给 music21 时，
    它会挑一个「最能装下这些音」的音阶 —— 对 ``C | G | Am | F`` 会给出
    ``F Major``。原因是它看到的是四个孤立的音，完全没有和弦功能信息，
    而 F 大调的音阶确实能覆盖 C、G、A、F 这四个音。
    正确做法是把每个和弦展开成三度叠置的音，让它看到真正的和声内容。

    返回 ``None`` 表示 music21 不可用或分析失败 —— **不抛异常**，
    因为它是可选的意见来源，不是链路依赖。
    """
    if not chords:
        return None
    try:
        from music21 import chord as m21chord
        from music21 import key as m21key
        from music21 import stream as m21stream
    except ImportError:
        return None

    try:
        part = m21stream.Part()
        for chord in chords:
            pitches = chord_pitches(chord)
            if pitches:
                part.append(m21chord.Chord(pitches))
        if not len(part.notes):
            return None
        detected = part.analyze("key")
        if not isinstance(detected, m21key.Key):
            return None
        mode = "Minor" if detected.mode == "minor" else "Major"
        return format_key(detected.tonic.name.replace("-", "b"), mode)
    except Exception:  # noqa: BLE001 - music21 内部异常种类多，统一视为「无意见」
        return None


def detect_key(chords: list[ChordSymbol], *, cross_check: bool = True) -> tuple[str, float]:
    """返回 ``(key, confidence)``。

    置信度规则：
    * 只有一路结果 → 0.7
    * 两路一致 → 0.95
    * 两路冲突 → 0.5（取和弦证据法结果）
    """
    primary = detect_key_from_chords(chords)
    if primary is None:
        return "C Major", 0.0

    if not cross_check:
        return primary.key, 0.7

    secondary = detect_key_with_music21(chords)
    if secondary is None:
        return primary.key, 0.7
    if secondary == primary.key:
        return primary.key, 0.95
    return primary.key, 0.5
