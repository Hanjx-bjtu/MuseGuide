"""统一降级协议。

``MVP计划.md`` §8「风险与应对」要求：
「LLM + 规则映射双保险，规则兜底」「试听/检索不可用时用文字描述替代」。

实现为**统一留痕**：任何降级都追加一条 ``Degradation``，使
「系统为什么给了这个结果」永远可解释，并让 P6 的能量化指标
（如「结构化输出解析成功率 ≥ 0.98」）可以直接从 Trace 统计出来。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DegradationKind = Literal[
    "llm_unavailable",  # 未配置 API Key / 服务不可达
    "llm_failed",  # 调用超时或报错
    "parse_failed",  # 结构化输出解析失败
    "retrieval_unavailable",  # 向量库 / 嵌入不可用，退化为离线知识直出
    "embedding_fallback",  # 嵌入后端降级（如降到哈希嵌入）
    "intent_fallback",  # Intent Mapping 回落到规则 / 选择层
]


class Degradation(BaseModel):
    """一次降级事件。"""

    kind: DegradationKind
    detail: str = ""
    fallback_to: str = Field(default="", description="降级后实际走的路径描述")


class DegradationLog:
    """累积降级事件的轻量容器（不依赖 pydantic，便于在服务层随手使用）。"""

    def __init__(self) -> None:
        self._items: list[Degradation] = []

    def add(self, kind: DegradationKind, detail: str = "", fallback_to: str = "") -> None:
        self._items.append(Degradation(kind=kind, detail=detail, fallback_to=fallback_to))

    def items(self) -> list[Degradation]:
        return list(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __len__(self) -> int:
        return len(self._items)
