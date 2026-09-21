"""API 请求 / 响应模型（P2.10）。

**为什么界面必须通过 HTTP 调后端（ADR-0005）：**
API 是两条链路中**唯一可被自动化回归的接口**。P6 的 45 个评测用例与
Baseline 对照实验必须打 API，不能打 Streamlit —— 否则评测无法脚本化，
与 ``MVP计划.md`` §7.3「准备 30~50 个测试用例」的要求冲突。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.brief import UserLevel
from app.core.evidence import Evidence
from app.core.plan import StarterPlan


class StarterRequest(BaseModel):
    """零基础链路请求（§3.1.2）。"""

    text: str = Field(default="", description="创作意图的自然语言描述")
    emotion: str | None = Field(default=None, description="情绪选择（界面文案或概念名）")
    style: str | None = Field(default=None, description="风格选择")
    tempo_label: str | None = Field(default=None, description="速度选择（界面文案）")
    user_level: UserLevel = "zero"


class StarterResponse(BaseModel):
    """零基础链路响应。"""

    plan: StarterPlan
    intent: dict = Field(default_factory=dict, description="Intent Mapping 结果（可解释性）")
    evidence: list[Evidence] = Field(default_factory=list)
    degradation: list[str] = Field(
        default_factory=list, description="降级留痕，供界面提示「已切换到简化模式」"
    )
    warnings: list[str] = Field(default_factory=list, description="方案校验发现的非致命问题")
    trace_id: str = ""


class AnalysisRequest(BaseModel):
    """分析请求（§3.1.3 进阶输入）。"""

    chords: str = Field(default="", description='和弦进行，如 "C | G | Am | F"')
    melody: str = Field(default="", description='旋律，如 "E4 G4 A4 G4 E4"')
    key: str | None = Field(default=None, description="用户指定的调性；留空则自动识别")
    tempo: int | None = None
    meter: str | None = None


class AnalysisResponse(BaseModel):
    """分析响应（§3.4.1 / §3.4.2）。"""

    key: str | None = None
    key_confidence: float = 1.0
    raw: list[str] = Field(default_factory=list)
    roman: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)
    melody: dict | None = None
    layman: dict = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    trace_id: str = ""


class AdviceRequest(BaseModel):
    """进阶链路请求（§3.1.3 进阶用户输入示例）。"""

    goal: str = Field(default="", description='创作目标，如 "保持温暖，但不要太普通"')
    chords: str = Field(default="", description='和弦进行，如 "C | G | Am | F"')
    melody: str = Field(default="", description='旋律，如 "E4 G4 A4 G4 E4"')
    key: str | None = Field(default=None, description="指定调性；留空则自动识别")
    user_level: UserLevel = "some"
    constraints: list[str] = Field(
        default_factory=list, description="用户明确要求保持的东西，如 ['保持温暖']"
    )


class GroundingIssueOut(BaseModel):
    """一条 Grounding 校验发现。"""

    kind: str
    detail: str = ""
    severity: str = "warning"


class GroundingResponse(BaseModel):
    """Grounding 报告 —— 把「幻觉」变成可统计的指标。"""

    ok: bool = True
    issues: list[GroundingIssueOut] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict, description="计数汇总，供实验报告使用")


class AdviceResponse(BaseModel):
    """进阶链路响应（§3.9.3 进阶版固定结构）。"""

    analysis: str = ""
    problems: list[str] = Field(default_factory=list)
    options: list[dict] = Field(default_factory=list, description="2~3 个修改方向")
    evidence: list[Evidence] = Field(default_factory=list, description="理论依据（可追溯）")
    grounding: GroundingResponse = Field(default_factory=GroundingResponse)
    diversity_warnings: list[str] = Field(
        default_factory=list, description="方案之间差异过小时的提示"
    )
    breakdown: dict = Field(default_factory=dict, description="作品分析结果")
    degradation: list[str] = Field(default_factory=list)
    trace_id: str = ""


class OptionsResponse(BaseModel):
    """选择式输入的选项载荷（§3.10.2 / §3.10.3）。

    界面直接渲染它，**每个选项都带通俗说明**。
    """

    emotion: list[dict]
    style: list[dict]
    tempo: list[dict]


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = ""
    llm_available: bool = False
    kb_entries: int = 0
    llm_model: str = ""
