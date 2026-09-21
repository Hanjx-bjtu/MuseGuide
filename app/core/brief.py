"""创作意图与创作目标：把「日常语言」变成结构化的创作简报。

依据：``MVP计划.md`` §3.2（Intent Mapping）、§3.1.1（创作目标 P1）、§3.1.2/§3.1.3（输入示例）
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.artifact import MusicArtifact

UserLevel = Literal["zero", "some", "advanced"]
CreativeMode = Literal["starter", "tutor"]

#: Intent Mapping 的产出层。对应 §8 风险应对「LLM + 规则映射双保险，规则兜底」。
IntentSource = Literal["rule", "llm", "selection", "default"]


class SelectionInput(BaseModel):
    """选择式输入（§3.10.2 / §3.10.3）。

    这是零基础用户的兜底路径：**不依赖用户主动输入任何术语**。
    用户的选择优先于 LLM 的推断结果。
    """

    emotion: str | None = Field(default=None, description='如 "伤感→释然"')
    style: str | None = Field(default=None, description='如 "民谣"')
    tempo_label: str | None = Field(default=None, description='界面原文，如 "中等偏慢，像散步"')

    @property
    def is_empty(self) -> bool:
        return not (self.emotion or self.style or self.tempo_label)


class CreativeIntent(BaseModel):
    """Intent Mapping 输出 —— 严格对齐 ``MVP计划.md`` §3.2.3 的 5 个字段。

    额外三个字段（``raw_text`` / ``matched_rules`` / ``source``）用于溯源与可解释性：
    回答「系统为什么给出这个方向」时，必须能说清是规则命中还是模型推断。
    """

    emotion: list[str] = Field(default_factory=list, description="情绪方向，如 ['伤感','释然']")
    style: str | None = Field(default=None, description="风格倾向，如 '民谣'")
    tempo_feel: str | None = Field(default=None, description="速度感，如 '中等偏慢'")
    key_preference: str | None = Field(default=None, description="调性倾向，如 '大调（释然感）'")
    harmony_needs: list[str] = Field(default_factory=list, description="和声需求")

    # --- 溯源字段 ---
    raw_text: str = ""
    matched_rules: list[str] = Field(
        default_factory=list, description="命中的 §3.2.2 规则原文，用于解释映射来源"
    )
    source: IntentSource = "default"

    def is_sparse(self) -> bool:
        """信息是否过少 —— 用于决定是否需要回落到下一层（LLM / 选择 / 默认）。"""
        return not self.emotion and not self.style and not self.tempo_feel


class CreativeGoal(BaseModel):
    """进阶链路的创作目标（§3.1.1 P1），如「保持温暖，但不要太普通」。"""

    text: str = ""
    intent: CreativeIntent = Field(default_factory=CreativeIntent)
    target_section: str | None = Field(default=None, description="如 '副歌'")
    constraints: list[str] = Field(
        default_factory=list, description="用户明确要求保持的东西，如 ['保持温暖']"
    )


class CreationInput(BaseModel):
    """两条链路的统一请求体（§3.1.2 零基础示例 / §3.1.3 进阶示例）。"""

    mode: CreativeMode = "starter"
    raw_text: str = Field(default="", description="创作意图或创作目标的原始文本")
    selections: SelectionInput = Field(default_factory=SelectionInput)
    artifact: MusicArtifact = Field(default_factory=MusicArtifact)
    user_level: UserLevel = "zero"
