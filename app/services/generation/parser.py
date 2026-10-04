"""结构化输出解析（``MVP计划.md`` §3.3.3 / §3.9.3）。

**为什么不用正则解析 Markdown：** §3.9.3 的展示模板虽然长得像 Markdown，
但它是一份**固定结构**。用正则去抠标题和列表，一旦模型换个措辞就会失败。
本项目改为「JSON schema 约束 + pydantic 校验」：
让模型直接输出 JSON，由契约层验证 —— 失败即降级，且失败原因可统计。

对应 ``实现阶段计划.md`` 的验收：结构化输出解析成功率 ≥ 0.98。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.plan import Advice, StarterPlan
from app.core.plan import StarterSection as PlanSection


class OutputParseError(ValueError):
    """模型输出无法解析为契约结构。"""


def extract_json(text: str) -> Any:
    """从模型输出中抽取 JSON。

    容忍三种常见情况：裸 JSON、```json 围栏、前后夹带解释文字。
    """
    raw = (text or "").strip()
    if not raw:
        raise OutputParseError("模型返回为空")

    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, flags=re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 退一步：抓最外层的一对花括号
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise OutputParseError(f"无法解析 JSON：{exc}") from exc

    raise OutputParseError("输出中未找到 JSON 内容")


def _coerce_chord_list(value: Any) -> list[str]:
    """和弦列表兼容多种写法：列表、竖线分隔的字符串、单个字符串。"""
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[|｜,，、\s]+", value.strip())
        return [p.strip() for p in parts if p.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def parse_starter_plan(text: str) -> StarterPlan:
    """把模型输出解析为 :class:`~app.core.plan.StarterPlan`（§3.3.3）。

    :raises OutputParseError: JSON 非法，或结构不满足契约（如缺少解释、
        段落不足两段）—— 由调用方降级并留痕。
    """
    data = extract_json(text)
    if not isinstance(data, dict):
        raise OutputParseError("起步方案应为一个 JSON 对象")

    sections_raw = data.get("sections")
    if not isinstance(sections_raw, list) or len(sections_raw) < 2:
        raise OutputParseError("起步方案必须包含至少两个段落（主歌 + 副歌）")

    sections: list[PlanSection] = []
    for item in sections_raw:
        if not isinstance(item, dict):
            raise OutputParseError("sections 的每一项都应为对象")
        try:
            sections.append(
                PlanSection(
                    name=str(item.get("name") or "").strip() or "未命名段落",
                    chords=_coerce_chord_list(item.get("chords")),
                    emotion=str(item.get("emotion") or "").strip(),
                    explanation=str(item.get("explanation") or "").strip(),
                )
            )
        except Exception as exc:  # noqa: BLE001 - 段落校验失败统一转为解析错误
            name = item.get("name") or "未命名段落"
            raise OutputParseError(f"段落「{name}」不合法：{exc}") from exc

    try:
        return StarterPlan(
            key=str(data.get("key") or "").strip(),
            key_explanation=str(data.get("key_explanation") or "").strip(),
            tempo=int(data.get("tempo") or 82),
            tempo_explanation=str(data.get("tempo_explanation") or "").strip(),
            sections=sections,
            why=str(data.get("why") or "").strip(),
            adjust_hints=_coerce_str_list(data.get("adjust_hints")),
        )
    except Exception as exc:  # noqa: BLE001 - pydantic 校验失败统一转为解析错误
        raise OutputParseError(f"起步方案结构不合法：{exc}") from exc


def parse_advice(text: str, level: str = "some") -> Advice:
    """把模型输出解析为 :class:`~app.core.plan.Advice`（§3.9.3）。

    ``options`` 的 2~3 条限制由契约层强制 —— 这里不重复实现。
    """
    from app.core.plan import AdviceOption

    data = extract_json(text)
    if not isinstance(data, dict):
        raise OutputParseError("建议应为一个 JSON 对象")

    options_raw = data.get("options")
    if not isinstance(options_raw, list):
        raise OutputParseError("建议缺少 options 字段")

    options: list[AdviceOption] = []
    for item in options_raw:
        if not isinstance(item, dict):
            raise OutputParseError("options 的每一项都应为对象")
        options.append(
            AdviceOption(
                label=str(item.get("label") or "").strip() or "建议",
                chords=_coerce_chord_list(item.get("chords")),
                feature=str(item.get("feature") or "").strip(),
                reason=str(item.get("reason") or "").strip(),
                theory=_coerce_str_list(item.get("theory")),
            )
        )

    try:
        return Advice(
            analysis=str(data.get("analysis") or "").strip(),
            problems=_coerce_str_list(data.get("problems")),
            options=options,
            layman_level=level,  # type: ignore[arg-type]
        )
    except Exception as exc:  # noqa: BLE001
        raise OutputParseError(f"建议结构不合法：{exc}") from exc
