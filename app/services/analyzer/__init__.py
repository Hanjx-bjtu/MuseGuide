"""音乐分析编排（``MVP计划.md`` §3.4）。

把 :class:`~app.core.artifact.MusicArtifact` 转为
:class:`~app.core.plan.AnalysisResult` —— 两条链路共用的分析出口。
"""

from __future__ import annotations

from app.core.artifact import MusicArtifact
from app.core.plan import AnalysisResult
from app.services.analyzer import key as key_mod
from app.services.analyzer import laymanize
from app.services.analyzer.roman import analyze_chords, annotate
from app.services.parser.chords import ChordParseError, parse_progression
from app.services.parser.melody import MelodyParseError, parse_melody


def build_artifact(
    *,
    chord_text: str | None = None,
    melody_text: str | None = None,
    key: str | None = None,
    tempo: int | None = None,
    meter: str | None = None,
    strict: bool = True,
) -> MusicArtifact:
    """从原始文本构建 ``MusicArtifact``。

    这是 §3.1.1 输入表的实现：和弦进行（P1）、旋律（P2）、调性/BPM/拍号（P2）。

    :param strict: 为 ``True`` 时，用户提供了内容但完全解析不出东西会抛异常。
        这很重要：**静默吞掉解析失败会让用户拿到一个「成功但空白」的响应**，
        界面上看不出任何问题，用户也不知道自己输入错了。
        调用方（API 层）据此返回 400 + 可读原因。
    """
    artifact = MusicArtifact(source_format="none")

    if chord_text and chord_text.strip():
        chords = parse_progression(chord_text)  # 失败时抛 ChordParseError
        if not chords and strict:
            raise ChordParseError("未能从输入中解析出任何和弦")
        artifact.source_format = "chords_text"
        artifact.chords = chords

    if melody_text and melody_text.strip():
        try:
            artifact.melody = parse_melody(melody_text)
        except MelodyParseError:
            if strict:
                raise
            artifact.melody = None
        if artifact.source_format == "none":
            artifact.source_format = "melody_text"

    if key:
        artifact.key = key
    elif artifact.chords:
        detected, confidence = key_mod.detect_key(artifact.chords)
        artifact.key = detected
        artifact.key_confidence = confidence

    if tempo is not None:
        artifact.tempo = tempo
    if meter:
        artifact.meter = meter

    # 填充 roman / function（需要 key）
    if artifact.chords and artifact.key:
        artifact.chords = annotate(artifact.chords, artifact.key)

    return artifact


def analyze(artifact: MusicArtifact) -> AnalysisResult:
    """产出 §3.4.1 / §3.4.2 的分析结果，含 ``layman`` 子对象。

    这是 P4 进阶链路的核心输入。
    """
    result = AnalysisResult()

    if artifact.chords:
        key = artifact.key or "C Major"
        romans, functions, notes = analyze_chords(artifact.chords, key)
        result.key = key
        result.key_confidence = artifact.key_confidence
        result.raw = artifact.chord_names
        result.roman = romans
        result.functions = functions
        result.notes = notes
        result.layman = laymanize.laymanize_chords(artifact.chords, key, romans)

    if artifact.melody:
        result.melody = {
            "notes": artifact.melody.notes,
            "range": artifact.melody.range,
            "contour": artifact.melody.contour,
            "repetition": artifact.melody.repetition,
        }
        melody_layman = laymanize.laymanize_melody(artifact.melody)
        # 旋律的通俗解释与和弦的合并（同一个 LaymanNote 契约）
        if result.layman.is_empty():
            result.layman = melody_layman
        else:
            result.layman = result.layman.model_copy(
                update={
                    "range": melody_layman.range,
                    "contour": melody_layman.contour,
                    "character": result.layman.character or melody_layman.character,
                }
            )

    return result


def analyze_text(
    *,
    chord_text: str | None = None,
    melody_text: str | None = None,
    key: str | None = None,
    tempo: int | None = None,
    meter: str | None = None,
) -> tuple[MusicArtifact, AnalysisResult]:
    """便捷入口：文本 → ``(artifact, analysis)``。"""
    artifact = build_artifact(
        chord_text=chord_text, melody_text=melody_text, key=key, tempo=tempo, meter=meter
    )
    return artifact, analyze(artifact)
