"""旋律文本解析（``MVP计划.md`` §3.4.2）。

把 ``E4 G4 A4 G4 E4`` 解析为 :class:`~app.core.artifact.MelodyInfo`，
产出 ``notes`` / ``range`` / ``contour`` / ``repetition`` 四个字段 ——
与 §3.4.2 的输出结构逐字对应。
"""

from __future__ import annotations

import re

from app.core.artifact import MelodyInfo

#: 音名 → 半音序号（用于比较高低）。八度由数字部分单独处理。
_NOTE_SEMITONE = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
    "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11,
}

#: 音名（含升降号）+ 八度数字，如 E4 / F#5 / Bb3
NOTE_PATTERN = re.compile(r"^(?P<name>[A-Ga-g][#b♯♭]?)(?P<octave>-?\d+)?$")

SPLIT_PATTERN = re.compile(r"[|｜,，、;；\->→\s]+")


class MelodyParseError(ValueError):
    """旋律文本无法解析。"""


def note_to_midi(note: str) -> int:
    """音名 → MIDI 音高编号（C4 = 60）。

    :raises MelodyParseError: 音名非法
    """
    raw = (note or "").strip().replace("♯", "#").replace("♭", "b")
    match = NOTE_PATTERN.match(raw)
    if not match:
        raise MelodyParseError(f"无法识别的音名：{note!r}")

    name = match.group("name")
    name = name[0].upper() + name[1:]
    if name not in _NOTE_SEMITONE:
        raise MelodyParseError(f"无法识别的音名：{note!r}")

    octave = int(match.group("octave")) if match.group("octave") else 4
    return _NOTE_SEMITONE[name] + (octave + 1) * 12


def parse_notes(text: str) -> list[str]:
    """把旋律文本拆成音名序列。

    支持 ``E4 G4 A4`` / ``E4, G4, A4`` / ``E4-G4-A4`` / ``E4|G4|A4`` 等写法。
    不带八度时默认第 4 八度。
    """
    if not text or not text.strip():
        return []

    tokens = [t for t in SPLIT_PATTERN.split(text.strip()) if t]
    notes: list[str] = []
    errors: list[str] = []
    for token in tokens:
        if re.fullmatch(r"[\d.①②③④⑤⑥⑦⑧⑨⑩]+", token):
            continue
        try:
            note_to_midi(token)  # 仅做合法性校验
        except MelodyParseError as exc:
            errors.append(str(exc))
            continue
        normalized = token.strip().replace("♯", "#").replace("♭", "b")
        if not re.search(r"\d", normalized):
            normalized += "4"
        notes.append(normalized)

    if errors and not notes:
        raise MelodyParseError("；".join(errors))
    return notes


def _contour(midi_values: list[int]) -> str:
    """判断旋律轮廓（§3.4.2 的 ``contour`` 字段）。

    返回 ``up`` / ``down`` / ``up-down`` / ``down-up`` / ``flat``。
    """
    if len(midi_values) < 2:
        return "flat"

    # 用分段方向判断：比较前半段与后半段的净走向
    first, last = midi_values[0], midi_values[-1]
    peak = max(midi_values)
    trough = min(midi_values)
    peak_at = midi_values.index(peak)
    trough_at = midi_values.index(trough)
    span = peak - trough

    if span <= 1:
        return "up-down" if False else "flat"

    # 最高点出现在中间 → 拱形；最低点出现在中间 → 倒拱形
    middle_start, middle_end = max(1, len(midi_values) // 4), max(2, (len(midi_values) * 3) // 4)
    if middle_start <= peak_at <= middle_end and peak_at not in (0, len(midi_values) - 1):
        return "up-down"
    if middle_start <= trough_at <= middle_end and trough_at not in (0, len(midi_values) - 1):
        return "down-up"

    if last > first + 1:
        return "up"
    if last < first - 1:
        return "down"
    if peak > first and peak > last:
        return "up-down"
    if trough < first and trough < last:
        return "down-up"
    return "flat"


def _has_repetition(notes: list[str]) -> bool:
    """是否存在重复片段（§3.4.2 的 ``repetition`` 字段）。

    检测四类重复：

    1. **相邻重复音**（``E4 E4``）
    2. **连续片段重复出现**（``E4 G4 ... E4 G4``）
    3. **逆行重复**：片段反向再现（``E4 G4 A4`` 与 ``A4 G4 E4``）
    4. **拱形对称**：整句是回文，如 §3.4.2 的示例 ``E4 G4 A4 G4 E4``

    ⚠️ 第 3、4 条是必须的：`MVP计划.md` §3.4.2 明确给出
    ``E4 G4 A4 G4 E4`` → ``"repetition": true``，而这一句**没有任何连续的
    重复片段** —— 它的重复是「原样折返」（A4 之后把前两个音倒着走回来）。
    只做前两条检测会得到 ``false``，与设计文档不符。
    """
    if len(notes) < 2:
        return False

    # 1. 相邻重复音
    for i in range(len(notes) - 1):
        if notes[i] == notes[i + 1]:
            return True

    if len(notes) < 4:
        return False

    # 2. 连续片段重复出现
    for size in (3, 2):
        seen: set[tuple[str, ...]] = set()
        for i in range(len(notes) - size + 1):
            chunk = tuple(notes[i : i + size])
            if chunk in seen:
                return True
            seen.add(chunk)

    # 3. 逆行重复：存在长度 ≥3 的片段，其倒序在别处出现
    for size in range(2, len(notes) // 2 + 1):
        for i in range(len(notes) - size + 1):
            chunk = notes[i : i + size]
            reversed_chunk = list(reversed(chunk))
            for j in range(len(notes) - size + 1):
                if j == i:
                    continue
                if notes[j : j + size] == reversed_chunk:
                    return True

    # 4. 整句回文（拱形对称）
    if notes == list(reversed(notes)):
        return True

    return False


def parse_melody(text: str) -> MelodyInfo:
    """解析旋律文本，产出 §3.4.2 的完整结构。

    :raises MelodyParseError: 无有效音名
    """
    notes = parse_notes(text)
    if not notes:
        raise MelodyParseError("未解析到任何音名")

    midi_values = [note_to_midi(n) for n in notes]
    low_idx = midi_values.index(min(midi_values))
    high_idx = midi_values.index(max(midi_values))

    return MelodyInfo(
        notes=notes,
        range=[notes[low_idx], notes[high_idx]],
        contour=_contour(midi_values),
        repetition=_has_repetition(notes),
    )
