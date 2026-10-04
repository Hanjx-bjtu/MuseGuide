"""输出契约：起步方案 / 分析结果 / 修改建议。

字段名严格对齐 ``MVP计划.md`` §3.3.3 与 §3.9.3 —— **不自由发挥**，
因为这两个小节给出了前端展示模板，模板直接消费这些字段。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.core.brief import UserLevel
from app.core.evidence import Evidence, GroundingReport


class LaymanNote(BaseModel):
    """``layman`` 子对象（§3.4.1 / §3.4.2）。

    专业字段与通俗解释**并列存放**，而不是在渲染时临时翻译 —— 这样通俗层
    可以被测试断言，也可以被检索（零基础模式下通俗内容优先）。
    """

    key: str | None = None
    progression: str | None = None
    character: str | None = None
    range: str | None = None
    contour: str | None = None

    def is_empty(self) -> bool:
        return not any(self.model_dump().values())


class AnalysisResult(BaseModel):
    """分析结果（§3.4.1 和弦 / §3.4.2 旋律）。"""

    key: str | None = None
    key_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    raw: list[str] = Field(default_factory=list, description="原始和弦写法")
    roman: list[str] = Field(default_factory=list, description='如 ["I","V","vi","IV"]')
    functions: list[str] = Field(default_factory=list, description="Tonic / Dominant / ...")
    melody: dict | None = None
    layman: LaymanNote = Field(default_factory=LaymanNote)
    notes: list[str] = Field(default_factory=list, description="给用户看的分析备注")


class StarterSection(BaseModel):
    """起步方案的段落（§3.3.3）。"""

    name: str
    chords: list[str] = Field(default_factory=list)
    emotion: str = ""
    explanation: str

    @field_validator("explanation")
    @classmethod
    def _explanation_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("StarterSection.explanation 不能为空（§3.3.3 要求每个选择都有通俗解释）")
        return v


class StarterPlan(BaseModel):
    """起步方案（§3.3.3 + §3.9.3 的「为什么这样选 / 如果你想调整 / 理论依据」）。"""

    key: str
    key_explanation: str
    tempo: int = Field(ge=20, le=400)
    tempo_explanation: str
    sections: list[StarterSection] = Field(min_length=2, description="至少主歌 + 副歌")
    why: str = ""
    adjust_hints: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    layman_level: UserLevel = "zero"

    @field_validator("key_explanation", "tempo_explanation")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("起步方案的每个选择都必须有通俗解释（§3.3.3 / §9 第 6 条）")
        return v


class AdviceOption(BaseModel):
    """一条修改方向（§3.9.3 的「建议 A / 建议 B」）。"""

    label: str = Field(description='如 "建议 A：增加七和弦"')
    chords: list[str] = Field(default_factory=list)
    feature: str = Field(description="这个方向的特点（面向进阶用户）")
    reason: str = Field(description="通俗解释：听起来会怎样、为什么")
    theory: list[str] = Field(default_factory=list, description="理论依据要点")

    @field_validator("reason", "feature")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("每条建议都必须有理由与依据（§9 第 6 条）")
        return v


class Advice(BaseModel):
    """修改建议（§3.9.3 进阶版固定 5 段结构）。

    ``options`` 长度受限于 2~3 —— 这是 ``MVP计划.md`` §1.2 P0「输出 2～3 个修改方向」
    的直接编码，由 pydantic 强制而非靠约定。
    """

    analysis: str = ""
    problems: list[str] = Field(default_factory=list)
    options: list[AdviceOption] = Field(min_length=2, max_length=3)
    evidence: list[Evidence] = Field(default_factory=list)
    grounding: GroundingReport = Field(default_factory=GroundingReport)
    layman_level: UserLevel = "some"
