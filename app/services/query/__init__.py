"""Query 分解（``MVP计划.md`` §3.5）。

用户的问题通常不是单一查询。§3.5.4 要求「用一次 LLM 调用完成分解」，
输出 ``intent / goal / object / queries`` 四个字段（§3.5.2 / §3.5.3）。

**双路设计（与 Intent Mapping 同构）：**
* 规则分解器 —— 零成本、确定性、可单测，也是**降级路径**
* LLM 分解器 —— 质量更好，但需要 API Key

这样 P6 的消融实验可以对比「规则分解 vs LLM 分解」的效果差异。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.brief import CreativeIntent
from app.core.degradation import DegradationLog
from app.core.evidence import DecomposedQuery

#: §3.5.4 的 prompt 模板
QUERY_DECOMPOSE_PROMPT = """用户创作意图/目标：{goal}
用户音乐素材：{music}
用户知识水平：{user_level}（零基础/有基础）

请拆解成 3-5 个音乐理论检索 query，输出 JSON 列表。
如果是零基础用户，query 应侧重"如何起步"和"情绪如何实现"。
如果是进阶用户，query 应侧重"如何修改"和"如何增加色彩"。

输出格式（只输出 JSON，不要解释）：
{{"intent": "...", "goal": "...", "object": "...", "queries": ["...", "..."]}}"""


#: 情绪 → 检索方向（与 ``app/core/options.py`` 的受控词表同源）
EMOTION_QUERIES: dict[str, str] = {
    "伤感": "伤感的情绪如何用和声实现",
    "忧郁": "忧郁色彩的和声手法",
    "释然": "如何做出释然、开阔的听感",
    "温暖": "如何让和声听起来温暖",
    "激昂": "如何做出激昂的高潮感",
    "温柔": "温柔柔和的和声与速度选择",
    "轻快": "轻快明亮的和声进行",
    "梦幻": "梦幻漂浮的和声色彩",
}

#: 创作目标关键词 → 检索方向（对齐 §3.2.2 的映射规则）
GOAL_QUERIES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("普通", "一般", "特别", "新颖", "色彩"), ("增加和声色彩的方法", "常见的和弦色彩手法")),
    (("高潮", "推上", "爆发", "张力"), ("如何制造张力与释放", "副歌高潮的和声设计")),
    (("收尾", "结束", "结尾", "句号"), ("终止式的类型与用法", "如何做出结束感")),
    (("连贯", "流畅", "顺"), ("低音线与声部进行", "如何让和弦连接更顺畅")),
    (("不对劲", "别扭", "怪"), ("和弦功能逻辑问题", "不协和音的成因")),
)

#: 风格 → 检索方向
STYLE_QUERIES: dict[str, str] = {
    "民谣": "民谣风格常见和声框架",
    "流行": "流行歌常见和弦进行",
    "摇滚": "摇滚的和声与节奏特点",
    "钢琴": "钢琴伴奏的和声写法",
    "电子": "电子音乐的氛围和声",
    "爵士": "爵士和声的色彩手法",
}

_INTENT_ZERO = "从零起步创作"
_INTENT_TUTOR = "修改已有作品"


def decompose_by_rules(
    *,
    goal_text: str = "",
    intent: CreativeIntent | None = None,
    artifact_summary: str = "",
    user_level: str = "zero",
    max_queries: int = 5,
) -> DecomposedQuery:
    """规则分解器 —— 零成本、确定性，也是 LLM 不可用时的降级路径。

    检索方向来自四处：情绪映射、目标关键词、风格倾向，以及**原始问题本身**。

    ⚠️ **原始问题必须排在第一位。**
    实测教训：早期版本只从关键词生成检索词，并总是追加两条兜底 query。
    结果是「终止式有哪几种」被拆成「常见和弦进行如何增加色彩」
    与「七和弦与延伸音的作用」—— **用户实际问的东西完全丢失了**，
    检索结果自然答非所问（严格 Recall@5 仅 0.403，修好后升至 0.8+）。

    兜底 query 只用于「补足条数」，绝不能挤掉原问题。
    """
    intent = intent or CreativeIntent()
    text = goal_text or ""

    queries: list[str] = []

    # 0) 原始问题优先 —— 它永远是最贴近用户意图的检索词
    if text.strip():
        queries.append(text.strip())

    # 1) 情绪 → 手法
    for emotion in intent.emotion:
        query = EMOTION_QUERIES.get(emotion)
        if query and query not in queries:
            queries.append(query)

    # 2) 目标关键词 → 手法
    for keywords, targets in GOAL_QUERIES:
        if any(word in text for word in keywords):
            for target in targets:
                if target not in queries:
                    queries.append(target)

    # 3) 风格 → 框架
    if intent.style:
        query = STYLE_QUERIES.get(intent.style)
        if query and query not in queries:
            queries.append(query)

    # 4) 和声需求
    for need in intent.harmony_needs:
        if need not in queries:
            queries.append(need)

    # 5) 兜底：仅在条数不足时补足，且**不覆盖已有内容**
    if len(queries) < 3:
        fallbacks = (
            ("如何开始写第一首歌", "主歌与副歌如何形成对比")
            if user_level == "zero"
            else ("常见和弦进行如何增加色彩", "七和弦与延伸音的作用")
        )
        for query in fallbacks:
            if len(queries) >= max_queries:
                break
            if query not in queries:
                queries.append(query)

    return DecomposedQuery(
        intent=_INTENT_ZERO if user_level == "zero" else _INTENT_TUTOR,
        goal=text or (("、".join(intent.emotion)) if intent.emotion else "未明确"),
        object=_infer_object(artifact_summary, user_level),
        queries=queries[:max_queries],
        source="rule",
    )


def _infer_object(artifact_summary: str, user_level: str) -> str:
    """判断用户在问「整首歌」还是某个具体对象。"""
    if "副歌" in artifact_summary:
        return "副歌"
    if "主歌" in artifact_summary:
        return "主歌"
    if "和弦" in artifact_summary:
        return "和弦进行"
    return "整首歌" if user_level == "zero" else "作品"


def _extract_json(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("LLM 返回为空")
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, flags=re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ValueError(f"无法解析 JSON：{exc}") from exc
        raise ValueError("未找到 JSON 内容")


def parse_decomposed(payload: str, max_queries: int = 5) -> DecomposedQuery:
    """解析 LLM 的分解结果。

    :raises ValueError: 解析失败或缺少 queries
    """
    data = _extract_json(payload)
    if not isinstance(data, dict):
        raise ValueError("分解结果应为 JSON 对象")

    queries = data.get("queries")
    if isinstance(queries, str):
        queries = [queries]
    if not isinstance(queries, list):
        raise ValueError("分解结果缺少 queries 列表")

    cleaned = [str(q).strip() for q in queries if str(q).strip()]
    if not cleaned:
        raise ValueError("queries 为空")

    return DecomposedQuery(
        intent=str(data.get("intent") or "").strip(),
        goal=str(data.get("goal") or "").strip(),
        object=str(data.get("object") or "").strip(),
        queries=cleaned[:max_queries],
        source="llm",
    )


def decompose(
    *,
    goal_text: str = "",
    intent: CreativeIntent | None = None,
    artifact_summary: str = "",
    user_level: str = "zero",
    llm: Any | None = None,
    log: DegradationLog | None = None,
    max_queries: int = 5,
) -> DecomposedQuery:
    """编排：优先 LLM，失败或不可用时回落规则分解。

    与 Intent Mapping 一样，**降级是留痕的**，因此
    「这次用了哪种分解器」可被 P6 的对比实验统计。
    """
    log = log if log is not None else DegradationLog()

    if llm is not None and getattr(llm, "available", False):
        prompt = QUERY_DECOMPOSE_PROMPT.format(
            goal=goal_text or "（未提供）",
            music=artifact_summary or "（未提供音乐素材）",
            user_level="零基础" if user_level == "zero" else "有基础",
        )
        try:
            raw = llm.complete(prompt, json_mode=True)
            return parse_decomposed(raw, max_queries=max_queries)
        except ValueError as exc:
            log.add("parse_failed", f"Query 分解解析失败：{exc}", fallback_to="rule_decompose")
        except Exception as exc:  # noqa: BLE001
            log.add("llm_failed", f"Query 分解调用失败：{exc}", fallback_to="rule_decompose")
    elif llm is not None:
        log.add_once(
            "llm_unavailable",
            "LLM 不可用，Query 分解回落规则层",
            fallback_to="rule_decompose",
        )

    return decompose_by_rules(
        goal_text=goal_text,
        intent=intent,
        artifact_summary=artifact_summary,
        user_level=user_level,
        max_queries=max_queries,
    )
