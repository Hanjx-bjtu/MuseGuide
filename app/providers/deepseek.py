"""DeepSeek 客户端（OpenAI 兼容端点）。

**Key 只从 ``Settings`` 读入**，而 ``Settings`` 只从环境变量 / ``.env`` 读入（ADR-0004）。
未配置 Key 时 ``available`` 为 False，上层走降级 —— **不抛异常**。
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.providers.base import LLMError


class DeepSeekProvider:
    """DeepSeek Chat。兼容任何 OpenAI 风格端点（改 ``base_url`` 即可）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.name = f"deepseek:{self._settings.llm_model}"
        self._client = None

    @property
    def available(self) -> bool:
        return self._settings.llm_available

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - 依赖缺失时才触发
                raise LLMError("openai 包未安装，无法调用 DeepSeek") from exc
            self._client = OpenAI(
                api_key=self._settings.llm_api_key,
                base_url=self._settings.llm_base_url,
                timeout=self._settings.llm_timeout_s,
            )
        return self._client

    def complete(self, prompt: str, *, system: str | None = None, json_mode: bool = False) -> str:
        if not self.available:
            raise LLMError("未配置 DEEPSEEK_API_KEY")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": self._settings.llm_model,
            "messages": messages,
            "temperature": self._settings.llm_temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = self._get_client().chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 统一转成 LLMError 供上层降级
            raise LLMError(f"DeepSeek 调用失败：{exc}") from exc

        return resp.choices[0].message.content or ""
