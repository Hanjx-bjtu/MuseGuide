"""和弦文本解析（``MVP计划.md`` §3.4.1）。

把用户输入的 ``C | G | Am | F`` 这类文本解析为 :class:`~app.core.artifact.ChordSymbol`。

**设计取舍：** ``MVP计划.md`` §3.4.1 建议优先用 ``music21`` 做调性识别与和弦分析，
但也写明「若输入简单，可用自定义映射表快速处理」。本项目两条路都走：

* 本模块用自建解析器处理和弦**符号**（music21 对 ``C``/``Am7`` 这类
  纯文本符号的支持需要通过 ``harmony.ChordSymbol``，且对 ``|`` 分隔、
  中文全角符号等用户手写习惯不友好）
* :mod:`app.services.analyzer.key` 用 music21 做**调性识别**的交叉验证

这样既拿到了 music21 的权威性，又保证了对用户随手输入的鲁棒性。
"""

from __future__ import annotations

import re

from app.core.artifact import ChordSymbol

#: 音名 → 音级（用于计算音程与罗马数字）
NOTE_TO_PITCH = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "Fb": 4, "E#": 5, "F": 5, "F#": 6, "Gb": 6,
    "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10,
    "B": 11, "Cb": 11,
}

#: 和弦性质后缀 → 规范化性质名。
#:
#: 顺序重要：**长后缀必须排在短后缀之前**，否则 ``maj7`` 会被 ``m`` 抢先匹配成小和弦。
#: 这是解析器最容易出错的地方，已由 ``test_quality_suffix_precedence`` 固化。
QUALITY_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("maj9", "maj7"),
    ("maj7", "maj7"),
    ("M7", "maj7"),
    ("Δ7", "maj7"),
    ("Δ", "maj7"),
    ("min9", "min7"),
    ("min7", "min7"),
    ("m9", "min7"),
    ("m7", "min7"),
    ("-7", "min7"),
    ("dim7", "dim"),
    ("°7", "dim"),
    ("dim", "dim"),
    ("°", "dim"),
    ("ø7", "min7"),
    ("ø", "min7"),
    ("aug", "aug"),
    ("+", "aug"),
    ("sus4", "sus4"),
    ("sus2", "sus4"),
    ("sus", "sus4"),
    ("add9", "add9"),
    ("add2", "add9"),
    ("6/9", "other"),
    ("69", "other"),
    ("13", "dom7"),
    ("11", "dom7"),
    ("7", "dom7"),
    ("m", "minor"),
    ("min", "minor"),
    ("-", "minor"),
)

#: 分隔符：半角与全角的竖线、逗号、顿号、箭头、空白
SPLIT_PATTERN = re.compile(r"[|｜,，、;；\->→\s]+")

#: 和弦符号的正则：根音 + 可选后缀 + 可选低音
CHORD_PATTERN = re.compile(
    r"^(?P<root>[A-Ga-g][#b♯♭]?)"
    r"(?P<quality>[^/]*?)"
    r"(?:/(?P<bass>[A-Ga-g][#b♯♭]?))?$"
)


class ChordParseError(ValueError):
    """和弦文本无法解析。"""


def normalize_note(note: str) -> str:
    """规范化音名：统一大小写，并把 Unicode 升降号转为 ASCII。"""
    if not note:
        return note
    note = note.strip().replace("♯", "#").replace("♭", "b")
    return note[0].upper() + note[1:]


def _apply_quality(suffix: str) -> str:
    """后缀 → 规范化性质名（长后缀优先）。"""
    if not suffix:
        return "major"
    stripped = suffix.strip()
    for token, quality in QUALITY_SUFFIXES:
        if stripped.startswith(token):
            return quality
    return "other"


def parse_chord(token: str, key: str | None = None) -> ChordSymbol:
    """解析单个和弦符号。

    :param token: 如 ``"C"`` / ``"Am7"`` / ``"G/B"`` / ``"F#maj7"``
    :param key: 已知调性时一并计算 roman 与 function
    :raises ChordParseError: 无法识别
    """
    raw = token.strip()
    if not raw:
        raise ChordParseError("空的和弦符号")

    # 兼容带括号的写法，如 "C(add9)" / "Cmaj7(no5)"
    cleaned = re.sub(r"[（(][^)）]*[)）]", "", raw).strip() or raw

    match = CHORD_PATTERN.match(cleaned)
    if not match:
        raise ChordParseError(f"无法识别的和弦符号：{raw!r}")

    root = normalize_note(match.group("root"))
    if root not in NOTE_TO_PITCH:
        raise ChordParseError(f"无法识别的根音：{root!r}（来自 {raw!r}）")

    quality = _apply_quality(match.group("quality") or "")
    bass = match.group("bass")
    bass = normalize_note(bass) if bass else None
    if bass and bass not in NOTE_TO_PITCH:
        raise ChordParseError(f"无法识别的低音：{bass!r}（来自 {raw!r}）")

    return ChordSymbol(raw=raw, root=root, quality=quality, bass=bass)  # type: ignore[arg-type]


def parse_progression(text: str, key: str | None = None) -> list[ChordSymbol]:
    """解析一整条和弦进行。

    支持 ``C | G | Am | F``、``C,G,Am,F``、``C G Am F``、``C→G→Am→F`` 等写法。
    """
    if not text or not text.strip():
        return []

    tokens = [t for t in SPLIT_PATTERN.split(text.strip()) if t]
    if not tokens:
        return []

    chords: list[ChordSymbol] = []
    errors: list[str] = []
    for token in tokens:
        # 跳过序号 / 小节标记等噪声，如 "1." "①"
        if re.fullmatch(r"[\d.①②③④⑤⑥⑦⑧⑨⑩]+", token):
            continue
        try:
            chords.append(parse_chord(token, key=key))
        except ChordParseError as exc:
            errors.append(str(exc))

    if errors and not chords:
        raise ChordParseError("；".join(errors))
    return chords


def simplify_quality(quality: str) -> str:
    """把性质归并到「大 / 小 / 其他」三类 —— 供通俗解释使用。"""
    if quality in ("major", "maj7", "add9", "sus4", "aug"):
        return "major"
    if quality in ("minor", "min7", "dim"):
        return "minor"
    return "other"
