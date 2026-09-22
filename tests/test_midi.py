"""P6.3 测试：MIDI 解析（M6，可选交付）。

对应 ``MVP计划.md`` §3.4.3：用 music21 提取 key / chords / notes / tempo，
**MVP 阶段仅做基础提取**，不做复杂结构分析。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.parser.midi import (
    MIDI_KEY_CONFIDENCE,
    MidiParseError,
    is_music21_available,
    parse_midi,
)

music21 = pytest.importorskip("music21", reason="MIDI 解析需要 music21")


def test_music21_is_available():
    assert is_music21_available() is True


def test_missing_file_raises_readable_error():
    with pytest.raises(MidiParseError, match="文件不存在"):
        parse_midi("不存在的文件.mid")


def test_invalid_file_raises_readable_error(tmp_path):
    """非 MIDI 内容应给出可读错误，而不是 music21 的原始异常。"""
    bogus = tmp_path / "fake.mid"
    bogus.write_bytes(b"this is not a midi file")
    with pytest.raises(MidiParseError):
        parse_midi(bogus)


@pytest.fixture(scope="module")
def generated_midi(tmp_path_factory) -> Path:
    """用 music21 现场生成一个最小 MIDI，使测试不依赖外部文件。"""
    from music21 import chord as m21chord
    from music21 import meter, note, stream, tempo

    score = stream.Score()
    part = stream.Part()
    part.append(meter.TimeSignature("4/4"))
    part.append(tempo.MetronomeMark(number=90))
    for name in ("C4", "E4", "G4", "C5"):
        part.append(note.Note(name, quarterLength=1))
    part.append(m21chord.Chord(["C4", "E4", "G4"], quarterLength=2))
    score.append(part)

    path = Path(tmp_path_factory.mktemp("midi")) / "sample.mid"
    score.write("midi", fp=str(path))
    return path


def test_parse_generated_midi_extracts_core_fields(generated_midi):
    """§3.4.3：提取 key / notes / tempo。"""
    artifact = parse_midi(generated_midi)
    assert artifact.source_format == "midi"
    assert artifact.melody is not None
    assert artifact.melody.notes, "应提取到音符序列"
    assert artifact.meter == "4/4"


def test_parse_generated_midi_extracts_tempo(generated_midi):
    artifact = parse_midi(generated_midi)
    assert artifact.tempo == 90


def test_key_confidence_is_disclosed(generated_midi):
    """MIDI 的调性是推断结果，置信度必须低于 1.0 并透出（ADR：置信度透明）。"""
    artifact = parse_midi(generated_midi)
    if artifact.key:
        assert artifact.key_confidence == MIDI_KEY_CONFIDENCE
        assert artifact.key_confidence < 1.0, "MIDI 推断的调性不应声称 100% 置信"


def test_confidence_dict_covers_each_field(generated_midi):
    """逐字段置信度必须存在 —— 界面据此提示「这段是推断结果」。"""
    artifact = parse_midi(generated_midi)
    assert "key" in artifact.confidence
    assert "tempo" in artifact.confidence
    assert "melody" in artifact.confidence


def test_melody_has_range_and_contour(generated_midi):
    artifact = parse_midi(generated_midi)
    assert artifact.melody is not None
    assert len(artifact.melody.range) == 2
    assert artifact.melody.contour in ("up", "down", "up-down", "down-up", "flat")


def test_music21_v10_flatten_compatibility(generated_midi):
    """回归：music21 v10 移除了 ``.flat`` 属性。

    早期代码写的是 ``part.flat.notes``，在 v10 下抛 ``AttributeError``，
    却被宽泛的 ``except Exception`` 吞掉 —— 表现为「旋律静默返回 None」，
    界面上完全看不出问题。这里直接断言不再踩这个坑。
    """
    artifact = parse_midi(generated_midi)
    assert artifact.melody is not None, "v10 下应仍能提取旋律（使用 flatten 而非 flat）"


def test_parse_result_is_contract_valid(generated_midi):
    from app.core.artifact import MusicArtifact

    artifact = parse_midi(generated_midi)
    restored = MusicArtifact.model_validate_json(artifact.model_dump_json())
    assert restored == artifact
