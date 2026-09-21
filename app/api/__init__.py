"""API 层：FastAPI 路由与请求/响应模型。

依赖方向：``api`` 可以依赖 ``services`` / ``core``，**不得被下层依赖**（ADR-0003）。
"""

from app.api.schemas import (
    AnalysisRequest,
    AnalysisResponse,
    HealthResponse,
    OptionsResponse,
    StarterRequest,
    StarterResponse,
)

__all__ = [
    "AnalysisRequest",
    "AnalysisResponse",
    "HealthResponse",
    "OptionsResponse",
    "StarterRequest",
    "StarterResponse",
]
