"""界面渲染组件。

**为什么把渲染逻辑抽成函数而不是写在页面里：**
这些函数可以在没有 Streamlit 运行时的情况下被测试（见 ``tests/test_ui_render.py``），
尤其是「零基础术语守卫」这类规则必须在 CI 里守着，
不能等到有人打开浏览器才发现输出里冒出了一个专业术语。
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.services.layman import find_banned_terms

#: 降级事件 → 面向用户的说明。
#:
#: ``MVP计划.md`` §8 要求「零基础用户仍觉门槛高」时有兜底；
#: 这里的原则是：**降级要说出来，但不能吓到用户**。
DEGRADATION_MESSAGES: dict[str, str] = {
    "llm_unavailable": "当前未接入 AI 模型，以下内容由知识库直接生成。",
    "llm_failed": "AI 模型暂时不可用，以下内容由知识库直接生成。",
    "parse_failed": "AI 的输出格式不完整，已改用知识库生成的内容。",
    "retrieval_unavailable": "知识检索暂时不可用，内容仅供参考。",
    "embedding_fallback": "语义检索使用了简化模式，结果可能不够精确。",
    "intent_fallback": "你的描述信息较少，已按通用方向给出建议。",
}


def render_degradation(degradation: list[str]) -> None:
    """在页面上说明当前走了哪条降级路径（P5.7）。

    用 ``st.info`` 而不是 ``st.error`` —— 降级后系统仍然给出了可用结果，
    这不是错误。把它渲染成红色报错会让零基础用户以为系统坏了。
    """
    if not degradation:
        return

    messages: list[str] = []
    for kind in degradation:
        text = DEGRADATION_MESSAGES.get(kind)
        if text and text not in messages:
            messages.append(text)

    for message in messages:
        st.info(message)


def render_evidence(evidence: list[dict[str, Any]], *, expanded: bool = False) -> None:
    """渲染「查看理论依据」（P5.6）。

    这是**与普通音乐 AI 的可视差异点**：每条建议都能定位到知识库条目原文
    与来源链接（``MVP计划.md`` §3.9.3 的「## 理论依据」段）。

    零基础模式下优先展示通俗标题与通俗正文。
    """
    if not evidence:
        st.caption("本次没有检索到相关知识。")
        return

    for index, item in enumerate(evidence, start=1):
        title = item.get("layman_title") or item.get("title") or item.get("entry_id", "")
        with st.expander(f"{index}. {title}", expanded=expanded):
            body = item.get("layman_content") or item.get("content") or ""
            if body:
                st.markdown(body)

            tags = item.get("tags") or []
            if tags:
                st.caption("标签：" + " · ".join(tags))

            source = item.get("source") or {}
            if source.get("url"):
                license_text = f"（{source['license']}）" if source.get("license") else ""
                st.caption(f"来源：{source.get('title', '')}{license_text}")
                st.markdown(f"[查看原文]({source['url']})")
            st.caption(f"知识条目 ID：`{item.get('entry_id', '')}`")


def render_grounding(grounding: dict[str, Any]) -> None:
    """渲染 Grounding 校验结果（§7.2 的 Groundedness 维度可视化）。"""
    summary = grounding.get("summary") or {}
    ok = grounding.get("ok", True)

    if ok and not grounding.get("issues"):
        st.success("已通过依据校验：没有发现编造来源或调外和弦。")
        return

    fabricated = summary.get("citations_fabricated", 0)
    out_of_key = summary.get("chords_out_of_key", 0)

    if fabricated == 0 and out_of_key == 0:
        st.success(
            f"已通过依据校验（编造引用 0 条，调外和弦 0 个）；"
            f"另有 {summary.get('total', 0)} 条提示供参考。"
        )
    else:
        st.warning(f"依据校验发现 {fabricated} 条编造引用、{out_of_key} 个调外和弦，请谨慎采纳。")

    issues = grounding.get("issues") or []
    if issues:
        with st.expander("查看校验详情"):
            for issue in issues:
                severity = issue.get("severity", "warning")
                icon = "⛔" if severity == "error" else "⚠️"
                st.markdown(f"{icon} **{issue.get('kind', '')}** — {issue.get('detail', '')}")


def guard_layman_text(text: str, level: str = "zero") -> str:
    """术语守卫（P5.8 / §9 第 7 条）。

    零基础模式下若文本含未解释的术语，界面层再拦一次 ——
    **渲染层是最后一道防线**，因为生成层与 Grounding 都可能漏掉。
    这里不静默改写（改写可能破坏语义），而是返回原文并在界面上加提示。
    """
    banned = find_banned_terms(text)
    if level != "zero" or not banned:
        return text

    st.caption(f"提示：这里出现了专业说法（{'、'.join(banned)}），如需了解可以问我。")
    return text


def render_problems(problems: list[str]) -> None:
    """渲染「问题」段。"""
    if not problems:
        return
    for problem in problems:
        st.markdown(f"- {problem}")


def format_chords(chords: list[str]) -> str:
    """和弦序列的统一展示形式（§3.10.4 用 ``→`` 连接）。"""
    return "  →  ".join(chords) if chords else "（未提供）"
