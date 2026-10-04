"""MIDI 解析（``MVP计划.md`` §3.4.3，M6 可选）。

用 ``music21`` 读取 ``.mid`` 并提取 调性 / 和弦 / 音符 / 速度，
转成统一的 :class:`~app.core.artifact.MusicArtifact`。

**MVP 边界（§3.4.3 明文）：** 只做基础提取，不做复杂结构分析。
**置信度必须透出**：MIDI 的调性判断是推断结果，不是用户声明的，
因此 ``key_confidence`` 会低于 1.0，并在界面上可被提示。
"""

from __future__ import annotations

from pathlib import Path

from app.core.artifact import ChordSymbol, MelodyInfo, MusicArtifact
from app.services.analyzer.key import format_key
from app.services.parser.chords import normalize_note

#: MIDI 推断的调性置信度上限 —— 它来自算法而非用户声明
MIDI_KEY_CONFIDENCE = 0.8


class MidiParseError(ValueError):
    """MIDI 文件无法解析。"""


def is_music21_available() -> bool:
    """music21 是否可用（不可用时 MIDI 解析整体降级）。"""
    try:
        import music21  # noqa: F401,PLC0415

        return True
    except ImportError:
        return False


def parse_midi(path: str | Path) -> MusicArtifact:
    """解析 MIDI 文件为 ``MusicArtifact``。

    :raises MidiParseError: music21 未安装、文件不存在或解析失败
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise MidiParseError(f"文件不存在：{file_path}")

    try:
        from music21 import converter  # noqa: PLC0415
    except ImportError as exc:
        raise MidiParseError(
            "未安装 music21，无法解析 MIDI。安装后重试：pip install music21"
        ) from exc

    try:
        score = converter.parse(str(file_path))
    except Exception as exc:  # noqa: BLE001 - music21 异常种类多
        raise MidiParseError(f"MIDI 解析失败：{exc}") from exc

    artifact = MusicArtifact(source_format="midi")

    # --- 速度 ---
    try:
        markings = score.parts[0].getElementsByClass("MetronomeMark") if score.parts else []
    except Exception:  # noqa: BLE001
        markings = []
    if markings:
        number = getattr(markings[0], "number", None)
        if number:
            artifact.tempo = int(number)
    if artifact.tempo is None:
        try:
            default = score.metronomeMarkBoundaries()
            if default:
                artifact.tempo = int(default[0][2].number or 0) or None
        except Exception:  # noqa: BLE001
            pass

    # --- 拍号 ---
    try:
        time_signatures = score.recurse().getElementsByClass("TimeSignature")
        if time_signatures:
            signature = time_signatures[0]
            artifact.meter = f"{signature.numerator}/{signature.denominator}"
    except Exception:  # noqa: BLE001
        pass

    # --- 调性（music21 推断）---
    try:
        analysed = score.analyze("key")
        mode = "Minor" if analysed.mode == "minor" else "Major"
        tonic = normalize_note(analysed.tonic.name.replace("-", "b"))
        artifact.key = format_key(tonic, mode)
        artifact.key_confidence = MIDI_KEY_CONFIDENCE
    except Exception:  # noqa: BLE001 - 调性推断失败不致命
        artifact.key = None
        artifact.key_confidence = 0.0

    # --- 和弦（按拍切片取同时发声的音）---
    artifact.chords = _extract_chords(score)

    # --- 旋律（最高声部的音名序列）---
    artifact.melody = _extract_melody(score)

    artifact.confidence = {
        "key": artifact.key_confidence,
        "tempo": 0.9 if artifact.tempo else 0.0,
        "chords": 0.6 if artifact.chords else 0.0,
        "melody": 0.7 if artifact.melody else 0.0,
    }

    return artifact


def _flat(stream):
    """取「扁平化」后的流，兼容 music21 各版本。

    ⚠️ **music21 v10 起移除了 ``.flat`` 属性**（``Score.flat`` 与 ``Part.flat``
    都不再存在），改用 ``.flatten()`` 方法。早期代码写的是 ``part.flat.notes``，
    在 v10 下抛 ``AttributeError`` —— 而它被一个 ``except Exception`` 吞掉了，
    于是「旋律解析静默返回 None」，界面上看不出任何问题。

    这个坑值得记下来：**宽泛的异常捕获会让 API 版本不兼容伪装成「数据为空」。**
    """
    if hasattr(stream, "flatten"):
        return stream.flatten()
    return stream.flat  # pragma: no cover - 兼容旧版本


def _extract_chords(score) -> list[ChordSymbol]:
    """从乐谱中提取和弦标记。

    只取显式的 ``ChordSymbol`` 标记（很多 MIDI 并不带），
    不做「从音高反推和弦」的和弦识别 —— 那属于 §1.3 明确排除的范围。
    """
    chords: list[ChordSymbol] = []
    try:
        from music21 import harmony  # noqa: PLC0415

        for symbol in score.recurse().getElementsByClass(harmony.ChordSymbol):
            figure = symbol.figure
            if not figure:
                continue
            try:
                from app.services.parser.chords import parse_chord  # noqa: PLC0415

                chords.append(parse_chord(figure))
            except Exception:  # noqa: BLE001 - 单个和弦解析失败不影响整体
                continue
    except Exception:  # noqa: BLE001
        return []
    return chords


def _extract_melody(score) -> MelodyInfo | None:
    """取音符最多的声部作为旋律线索（§3.4.3 的「基础提取」）。"""
    try:
        from music21 import note as m21note  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return None

    notes: list[str] = []
    try:
        parts = list(score.parts) or [score]
        # 取音符数最多的声部作为旋律线索
        target = max(parts, key=lambda part: len(_flat(part).notes))
        for element in _flat(target).notes:
            if isinstance(element, m21note.Note):
                name = element.name.replace("-", "b")
                notes.append(f"{normalize_note(name)}{element.octave}")
    except Exception:  # noqa: BLE001
        return None

    if not notes:
        return None

    # 复用旋律解析器计算 range / contour / repetition
    from app.services.parser.melody import note_to_midi  # noqa: PLC0415

    try:
        values = [note_to_midi(n) for n in notes]
    except Exception:  # noqa: BLE001
        return MelodyInfo(notes=notes)

    low_index = values.index(min(values))
    high_index = values.index(max(values))

    from app.services.parser.melody import _contour, _has_repetition  # noqa: PLC0415

    return MelodyInfo(
        notes=notes,
        range=[notes[low_index], notes[high_index]],
        contour=_contour(values),
        repetition=_has_repetition(notes),
    )
