"""FastAPI 应用（P2.10）。

路由按链路划分：
* ``/api/options`` ``/api/health`` —— 元信息（P2）
* ``/api/starter`` —— 零基础链路（P2）
* ``/api/analyze`` —— 作品分析（P2，P4 会在此基础上加 ``/api/advice``）
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from app import __version__
from app.api.schemas import (
    AnalysisRequest,
    AnalysisResponse,
    HealthResponse,
    OptionsResponse,
    StarterRequest,
    StarterResponse,
)
from app.core.brief import CreationInput, SelectionInput
from app.core.config import get_settings
from app.core.options import selection_payload
from app.services.analyzer import analyze_text
from app.services.kb import load_entries
from app.services.parser.chords import ChordParseError
from app.services.parser.melody import MelodyParseError
from app.services.starter import generate_starter_plan

app = FastAPI(
    title="MuseGuide API",
    description="面向零基础音乐创作爱好者的知识增强型 AI 创作陪伴助手",
    version=__version__,
)


@lru_cache(maxsize=1)
def _default_llm():
    """按配置构造 LLM provider。

    未配置 Key 时返回一个 ``available=False`` 的 DeepSeek provider，
    上层据此走降级 —— **不抛异常**（ADR-0004）。
    """
    from app.providers.deepseek import DeepSeekProvider

    return DeepSeekProvider()


def _llm_override():
    """测试可通过 ``app.dependency_overrides`` 注入 Mock。"""
    return None


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """健康检查：报告 LLM 可用性与知识库规模。

    界面可据此提前提示「当前为简化模式」，而不是等用户点了按钮才报错。
    """
    settings = get_settings()
    try:
        kb_count = len(load_entries(settings.kb_dir))
    except Exception:  # noqa: BLE001 - 知识库缺失不应导致健康检查失败
        kb_count = 0

    return HealthResponse(
        status="ok",
        version=__version__,
        llm_available=settings.llm_available,
        kb_entries=kb_count,
        llm_model=settings.llm_model,
    )


@app.get("/api/options", response_model=OptionsResponse)
def options() -> OptionsResponse:
    """下发选择式输入（§3.10.2 / §3.10.3）。

    零基础用户的兜底入口：**不依赖用户主动输入任何术语**。
    """
    return OptionsResponse(**selection_payload())


@app.post("/api/starter", response_model=StarterResponse)
def starter(request: StarterRequest) -> StarterResponse:
    """零基础链路：创作意图 → 起步方案（§3.3）。"""
    payload = CreationInput(
        mode="starter",
        raw_text=request.text,
        selections=SelectionInput(
            emotion=request.emotion,
            style=request.style,
            tempo_label=request.tempo_label,
        ),
        user_level=request.user_level,
    )

    llm = _llm_override()
    if llm is None:
        llm = _default_llm()

    result = generate_starter_plan(payload, llm=llm)

    return StarterResponse(
        plan=result.plan,
        intent=result.trace.intent.model_dump() if result.trace.intent else {},
        evidence=result.plan.evidence,
        degradation=[d.kind for d in result.trace.degradation],
        warnings=result.warnings,
        trace_id=result.trace.trace_id,
    )


@app.post("/api/analyze", response_model=AnalysisResponse)
def analyze(request: AnalysisRequest) -> AnalysisResponse:
    """进阶链路第一步：作品分析（§3.4）。"""
    if not (request.chords.strip() or request.melody.strip()):
        raise HTTPException(status_code=400, detail="请至少提供和弦进行或旋律")

    try:
        artifact, result = analyze_text(
            chord_text=request.chords,
            melody_text=request.melody,
            key=request.key,
            tempo=request.tempo,
            meter=request.meter,
        )
    except ChordParseError as exc:
        raise HTTPException(status_code=400, detail=f"和弦解析失败：{exc}") from exc
    except MelodyParseError as exc:
        raise HTTPException(status_code=400, detail=f"旋律解析失败：{exc}") from exc

    return AnalysisResponse(
        key=result.key,
        key_confidence=result.key_confidence,
        raw=result.raw,
        roman=result.roman,
        functions=result.functions,
        melody=result.melody,
        layman=result.layman.model_dump(),
        notes=result.notes,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request, exc: Exception) -> JSONResponse:
    """统一异常响应。

    对外返回可读信息而不是堆栈 —— ``MVP计划.md`` §8 要求
    零基础用户不应看到技术错误。
    """
    return JSONResponse(
        status_code=500,
        content={"detail": "服务暂时不可用，请稍后重试", "error_type": type(exc).__name__},
    )
