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

    def add_once(self, kind: DegradationKind, detail: str = "", fallback_to: str = "") -> bool:
        """只在同类事件尚未记录时添加。

        **为什么需要它：** 一次请求会经过 Intent Mapping、Query 分解、生成
        三个都会检测 LLM 可用性的环节。若各自无条件记录，同一次请求会留下
        多条 ``llm_unavailable``，使「降级次数」这类统计指标失真 ——
        P6 的报告要按降级类型计数，重复计数会直接污染结论。

        :return: 是否真的添加了（``False`` 表示已有同类事件）
        """
        if self.has(kind):
            return False
        self.add(kind, detail, fallback_to)
        return True

    def has(self, kind: DegradationKind) -> bool:
        """是否已记录过某类事件。"""
        return any(item.kind == kind for item in self._items)

    def kinds(self) -> list[str]:
        """事件类型列表（供展示与统计）。"""
        return [item.kind for item in self._items]

    def items(self) -> list[Degradation]:
        return list(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __len__(self) -> int:
        return len(self._items)
