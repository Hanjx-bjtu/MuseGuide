"""证据链契约：``DecomposedQuery`` → ``Candidate`` → ``Evidence``。

**设计要点：这条链把「可解释性」变成数据结构。**
每一次请求都能回答：这条建议依据的是哪条知识、它由哪条 query 召回、在第几级排名。

依据：``MVP计划.md`` §3.5.2/§3.5.3（Query 分解）、§3.6.2（知识条目）、§3.7.3（融合排序）、§3.9.3（理论依据）
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RecallRoute = Literal["dense", "sparse", "both", "none"]
IssueSeverity = Literal["error", "warning"]

IssueKind = Literal[
    "out_of_key",  # 建议的和弦不在声称调性下成立
    "citation_out_of_range",  # [来源N] 的 N 超出 evidence 长度
    "weak_grounding",  # 引用内容与结论关键词重叠度过低
    "goal_mismatch",  # 建议未回应用户目标
    "missing_layman",  # 零基础模式下缺少通俗解释
]


class DecomposedQuery(BaseModel):
    """Query 分解结果 —— 严格对齐 §3.5.2 与 §3.5.3 的四个字段。"""

    intent: str = Field(default="", description="如 '从零起步创作' / '修改已有作品'")
    goal: str = Field(default="", description="如 '伤感→释然的情绪转折'")
    object: str = Field(default="", description="如 '整首歌' / '和弦进行'")
    queries: list[str] = Field(default_factory=list, description="3~5 条音乐理论检索 query")
    source: Literal["rule", "llm"] = "rule"


class Candidate(BaseModel):
    """召回中间态：保留逐级排名，使「为什么这条证据排第一」可展示。"""

    entry_id: str
    score: float = 0.0
    route: RecallRoute = "none"
    rank_dense: int | None = None
    rank_sparse: int | None = None
    rank_final: int | None = None
    from_query: str | None = None


class EvidenceSource(BaseModel):
    """知识条目的来源与许可（§3.6.2 ``source`` 字段，100% 必填）。"""

    title: str = ""
    url: str = ""
    license: str = ""


class Evidence(BaseModel):
    """最终证据。

    零基础模式下渲染优先使用 ``layman_title`` / ``layman_content`` —— 这是
    Layman-aware Generation 的物理载体，不是可选装饰（§3.6.2、§3.8）。
    """

    entry_id: str
    title: str = ""
    layman_title: str | None = None
    content: str = ""
    layman_content: str | None = None
    tags: list[str] = Field(default_factory=list)
    source: EvidenceSource = Field(default_factory=EvidenceSource)
    score: float = 0.0
    from_query: str | None = None
    candidate: Candidate | None = Field(
        default=None, description="携带召回溯源，便于 UI 逐级展示排名"
    )

    @property
    def citation(self) -> str:
        """报告中引用的显示形式。"""
        return f"[{self.entry_id}] {self.title}"


class GroundingIssue(BaseModel):
    kind: IssueKind
    detail: str = ""
    severity: IssueSeverity = "warning"


class GroundingReport(BaseModel):
    """建议可信性校验结果（P4.6）。

    它把「幻觉」从一个只能靠人看的问题，变成报告里可统计的字段：
    ``编造引用数 = 0`` / ``调外和弦数 = 0``。
    """

    ok: bool = True
    issues: list[GroundingIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[GroundingIssue]:
        return [i for i in self.issues if i.severity == "error"]

    def kinds(self) -> list[str]:
        return [i.kind for i in self.issues]
