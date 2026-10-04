"""``Trace`` —— 一次请求的完整可回放记录。

**为什么它是 P0 的核心交付：**

``MVP计划.md`` §3.9.3 要求「固定输出格式，保证可解释性与可评估性」。
`Trace` 是这句话的工程实现 —— 评测、报告、界面依据展示，三者消费同一个对象：

======================  ==========================================
要解决的问题             Trace 如何解决
======================  ==========================================
评测要手写解析           评测层直接消费 Trace 列表
报告数字可能漂移         报告由 Trace 渲染，不可能手工誊写
「为什么这条建议出现」     UI 直接渲染 candidates 的逐级排名
实验无法复现             Trace 含 config + prompt + 原始输出
======================  ==========================================
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.core.brief import CreationInput, CreativeIntent
from app.core.degradation import Degradation
from app.core.evidence import Candidate, DecomposedQuery, Evidence, GroundingReport
from app.core.plan import Advice, AnalysisResult, StarterPlan


def _new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Trace(BaseModel):
    """逐位可回放的一次请求记录。

    存档约定：``experiments/{experiment_name}/{date}.jsonl``，每行一个 Trace。
    """

    trace_id: str = Field(default_factory=_new_trace_id)
    timestamp: str = Field(default_factory=_now_iso)

    # 本次请求用了哪组配置（模型 / top_k / 检索策略）—— v2 的 ExperimentConfig 预留位
    config: dict = Field(default_factory=dict)

    # 输入与中间产物
    input: CreationInput = Field(default_factory=CreationInput)
    intent: CreativeIntent | None = None
    analysis: AnalysisResult | None = None

    # 检索链路
    queries: list[DecomposedQuery] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    # 生成链路
    prompt: str = ""
    raw_output: str = ""
    plan: StarterPlan | None = None
    advice: Advice | None = None
    grounding: GroundingReport = Field(default_factory=GroundingReport)

    # 运维信息
    degradation: list[Degradation] = Field(default_factory=list)
    timings_ms: dict[str, int] = Field(default_factory=dict)
    usage: dict = Field(default_factory=dict)

    def degraded(self) -> bool:
        return bool(self.degradation)

    def total_ms(self) -> int:
        return sum(self.timings_ms.values())

    def to_jsonl(self) -> str:
        """存档为一行 JSONL。"""
        return self.model_dump_json()

    @classmethod
    def from_jsonl(cls, line: str) -> "Trace":
        return cls.model_validate_json(line)
