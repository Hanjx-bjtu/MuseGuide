"""``MusicArtifact`` —— 统一音乐素材表示。

这是两条链路的公共出口：零基础用户可能没有任何素材（``source_format="none"``），
进阶用户提供和弦文本 / 旋律文本 / MIDI。下游的分析、检索、生成只看这一个结构。

依据：``MVP计划.md`` §3.4.1（和弦）、§3.4.2（旋律）、§3.4.3（MIDI）
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SourceFormat = Literal["chords_text", "melody_text", "meta_text", "midi", "none"]
ChordQuality = Literal[
    "major", "minor", "dom7", "maj7", "min7", "dim", "aug", "sus4", "add9", "other"
]
HarmonicFunction = Literal["Tonic", "Dominant", "Subdominant", "Other"]


class ChordSymbol(BaseModel):
    """单个和弦。``raw`` 保留用户写法，其余字段是规范化结果（§3.4.1）。"""

    model_config = ConfigDict(frozen=True)

    raw: str = Field(description="用户原始写法，如 G/B")
    root: str = Field(description="根音，如 G")
    quality: ChordQuality = "major"
    bass: str | None = Field(default=None, description="转位低音，如 B")
    roman: str | None = Field(default=None, description="罗马数字，如 V6（需 key 才能计算）")
    function: HarmonicFunction | None = None


class MelodyInfo(BaseModel):
    """旋律特征（§3.4.2）。"""

    model_config = ConfigDict(frozen=True)

    notes: list[str] = Field(default_factory=list, description='音名序列，如 ["E4","G4"]')
    range: list[str] = Field(default_factory=list, description="最低与最高音，如 [E4, A4]")
    contour: str = Field(default="flat", description="up / down / up-down / down-up / flat")
    repetition: bool = Field(default=False, description="是否存在重复片段")


class Section(BaseModel):
    """曲式段落，如主歌 / 副歌（§3.3.3 起步方案直接消费它）。"""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="主歌 / 副歌 / 桥段")
    chords: list[str] = Field(default_factory=list)
    emotion: str | None = None
    explanation: str | None = Field(default=None, description="通俗解释")


class MusicArtifact(BaseModel):
    """统一音乐素材。

    ``confidence`` 逐字段记录置信度：文本输入恒为 1.0，MIDI / 推断路径会低于 1.0。
    MVP 阶段虽然不做音频，但该字段是 v2 音频路径接入契约的预留位（见 ADR-0001）。
    """

    source_format: SourceFormat = "none"
    key: str | None = Field(default=None, description='如 "C Major"')
    key_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    tempo: int | None = Field(default=None, ge=20, le=400)
    meter: str | None = None
    chords: list[ChordSymbol] = Field(default_factory=list)
    melody: MelodyInfo | None = None
    sections: list[Section] = Field(default_factory=list)
    confidence: dict[str, float] = Field(
        default_factory=dict, description="逐字段置信度，如 {'key': 0.8}"
    )

    # ---- 便捷查询（刻意保持极薄：真正的分析在 services/analyzer 中）----

    @property
    def is_empty(self) -> bool:
        """用户完全没有提供素材 —— 这决定进入 Creative Starter 还是 Creative Tutor。"""
        return not self.chords and self.melody is None and self.source_format == "none"

    @property
    def chord_names(self) -> list[str]:
        return [c.raw for c in self.chords]

    @property
    def roman_numerals(self) -> list[str]:
        return [c.roman for c in self.chords if c.roman]

    @property
    def functions(self) -> list[str]:
        return [c.function for c in self.chords if c.function]


class StarterPlanSection(Section):
    """起步方案中的段落 —— 与 ``Section`` 同形，但 ``explanation`` 必填（§3.3.3）。"""

    explanation: str
