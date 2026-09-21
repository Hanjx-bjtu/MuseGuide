"""后端 API 客户端（ADR-0005）。

**为什么界面通过 HTTP 调后端而不是直接 import 服务层：**
API 是两条链路中唯一可被自动化回归的接口。P6 的 45 个评测用例与
Baseline 对照实验都要打 API，不能打 Streamlit —— 否则评测无法脚本化。

这个模块把 HTTP 细节集中在一处，使界面层只关心「调用什么」，
也让界面逻辑可以在没有真实后端时被测试（注入 fake client）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx

#: 默认后端地址。可用 ``MUSEGUIDE_API`` 环境变量覆盖。
DEFAULT_BASE_URL = "http://127.0.0.1:8000"

#: 单次请求超时。§9 第 8 条要求端到端 1 分钟内完成，
#: LLM 生成占大头，因此给 90 秒留出余量。
DEFAULT_TIMEOUT = 90.0


class APIError(RuntimeError):
    """后端返回错误或不可达。

    界面对它做统一处理：显示可读提示，而不是堆栈 ——
    ``MVP计划.md`` §8 要求零基础用户不应看到技术错误。
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class StarterOutcome:
    """零基础链路的界面态。"""

    plan: dict = field(default_factory=dict)
    intent: dict = field(default_factory=dict)
    evidence: list[dict] = field(default_factory=list)
    degradation: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    trace_id: str = ""


@dataclass
class AdviceOutcome:
    """进阶链路的界面态。"""

    analysis: str = ""
    problems: list[str] = field(default_factory=list)
    options: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    grounding: dict = field(default_factory=dict)
    diversity_warnings: list[str] = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)
    degradation: list[str] = field(default_factory=list)
    trace_id: str = ""


class MuseGuideClient:
    """后端 API 的薄封装。"""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("MUSEGUIDE_API") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    # ---- 底层 ----

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise APIError(
                f"连不上后端服务（{self.base_url}）。请确认已运行：\n"
                f"uvicorn app.api.server:app --port 8000"
            ) from exc
        except httpx.TimeoutException as exc:
            raise APIError("请求超时。生成较慢时可以稍后重试，或先填写更简短的内容。") from exc

        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
                detail = payload.get("detail") or payload.get("message") or ""
            except Exception:  # noqa: BLE001 - 非 JSON 错误体
                detail = response.text[:200]
            raise APIError(detail or f"后端返回错误（{response.status_code}）", status_code=response.status_code)

        return response.json()

    # ---- 端点 ----

    def health(self) -> dict:
        return self._request("GET", "/api/health")

    def options(self) -> dict:
        """选择式输入选项（§3.10.2 / §3.10.3）。"""
        return self._request("GET", "/api/options")

    def starter(
        self,
        *,
        text: str = "",
        emotion: str | None = None,
        style: str | None = None,
        tempo_label: str | None = None,
        user_level: str = "zero",
    ) -> StarterOutcome:
        """零基础链路：创作意图 → 起步方案。"""
        payload = {
            "text": text,
            "emotion": emotion,
            "style": style,
            "tempo_label": tempo_label,
            "user_level": user_level,
        }
        data = self._request("POST", "/api/starter", json=payload)
        return StarterOutcome(
            plan=data.get("plan", {}),
            intent=data.get("intent", {}),
            evidence=data.get("evidence", []),
            degradation=data.get("degradation", []),
            warnings=data.get("warnings", []),
            trace_id=data.get("trace_id", ""),
        )

    def analyze(self, *, chords: str = "", melody: str = "", key: str | None = None) -> dict:
        """进阶链路第一步：作品分析（§3.4）。"""
        return self._request(
            "POST", "/api/analyze", json={"chords": chords, "melody": melody, "key": key}
        )

    def advice(
        self,
        *,
        goal: str = "",
        chords: str = "",
        melody: str = "",
        key: str | None = None,
        user_level: str = "some",
        constraints: list[str] | None = None,
    ) -> AdviceOutcome:
        """进阶链路：作品 + 目标 → 分析 + 修改方向 + Grounding 校验（§3.9）。"""
        payload = {
            "goal": goal,
            "chords": chords,
            "melody": melody,
            "key": key,
            "user_level": user_level,
            "constraints": constraints or [],
        }
        data = self._request("POST", "/api/advice", json=payload)
        return AdviceOutcome(
            analysis=data.get("analysis", ""),
            problems=data.get("problems", []),
            options=data.get("options", []),
            evidence=data.get("evidence", []),
            grounding=data.get("grounding", {}),
            diversity_warnings=data.get("diversity_warnings", []),
            breakdown=data.get("breakdown", {}),
            degradation=data.get("degradation", []),
            trace_id=data.get("trace_id", ""),
        )

    def close(self) -> None:
        self._client.close()


def default_client() -> MuseGuideClient:
    return MuseGuideClient()
