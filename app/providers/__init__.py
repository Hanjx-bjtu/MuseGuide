"""模型适配层：让上层与具体模型解耦（``MVP计划.md`` §4「LLM：模型无关」）。"""

from app.providers.base import EmbeddingProvider, LLMProvider

__all__ = ["EmbeddingProvider", "LLMProvider"]
