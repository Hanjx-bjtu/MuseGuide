"""Provider 协议。

上层只依赖这两个协议，**不依赖任何 SDK**（ADR-0007）。这样：
* 测试可以注入 ``MockProvider``，无需 Key 与网络
* 换模型（DeepSeek / Ollama / 其他兼容端点）不改业务代码
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """文本生成。"""

    name: str

    @property
    def available(self) -> bool:
        """是否可用。为 False 时上层必须走降级路径，而不是抛异常。"""
        ...

    def complete(self, prompt: str, *, system: str | None = None, json_mode: bool = False) -> str:
        """返回模型原始文本。失败时抛 ``LLMError``。"""
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """文本嵌入。"""

    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_one(self, text: str) -> list[float]:
        ...


class LLMError(RuntimeError):
    """LLM 调用失败（超时 / 网络 / 服务端错误）。上层据此降级。"""
