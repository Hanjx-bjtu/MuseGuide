"""进阶入口：作品 + 目标 → 分析 + 修改建议（P5.5）。

布局对齐 ``MVP计划.md`` §3.10.5 的进阶用户界面草案。
"""

from __future__ import annotations

import streamlit as st

from app.ui.client import APIError, MuseGuideClient
from app.ui.components import (
    format_chords,
    guard_layman_text,
    render_degradation,
    render_evidence,
    render_grounding,
    render_problems,
)


def _init_state() -> None:
    defaults = {
        "tutor_goal": "",
        "tutor_chords": "C | G | Am | F",
        "tutor_melody": "",
        "tutor_user_level": "some",
        "tutor_keep_warm": True,
        "tutor_result": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _client() -> MuseGuideClient:
    return MuseGuideClient()


def _render_inputs() -> None:
    st.title("已有雏形？让我看看")
    st.caption("输入你的和弦或旋律，再告诉我你想改什么。")

    st.text_input(
        "创作目标",
        key="tutor_goal",
        placeholder="保持温暖，但不要太普通",
    )

    left, right = st.columns(2)
    left.text_input(
        "和弦进行",
        key="tutor_chords",
        placeholder="C | G | Am | F",
        help="用竖线、逗号或空格分隔都行",
    )
    right.text_input(
        "旋律（可选）",
        key="tutor_melody",
        placeholder="E4 G4 A4 G4 E4",
    )

    st.radio(
        "你对自己的乐理水平怎么评价？",
        ["zero", "some", "advanced"],
        key="tutor_user_level",
        horizontal=True,
        format_func=lambda value: {
            "zero": "完全不懂乐理",
            "some": "知道一点",
            "advanced": "有基础",
        }[value],
        help="这决定了解释的通俗程度（§3.8.3 的三档策略）",
    )

    st.checkbox(
        "我想保持温暖的感觉（作为硬约束）",
        key="tutor_keep_warm",
        help="勾选后，把整体转向小调之类的方案会被标记为违反约束（§7.2 的 Relevance 维度）",
    )

    if st.button("开始分析", type="primary", use_container_width=True):
        constraints = ["保持温暖"] if st.session_state.tutor_keep_warm else []
        with st.spinner("正在分析并生成建议……"):
            try:
                st.session_state.tutor_result = _client().advice(
                    goal=st.session_state.tutor_goal,
                    chords=st.session_state.tutor_chords,
                    melody=st.session_state.tutor_melody,
                    user_level=st.session_state.tutor_user_level,
                    constraints=constraints,
                )
                st.rerun()
            except APIError as exc:
                st.error(str(exc))


def _render_analysis(breakdown: dict, problems: list[str]) -> None:
    st.subheader("分析结果")
    if breakdown.get("key"):
        st.markdown(f"**调性**：{breakdown['key']}")

    roman = breakdown.get("roman") or []
    functions = breakdown.get("functions") or []
    if roman:
        st.markdown(f"**和声**：{' → '.join(roman)}")
    if functions:
        st.caption("功能：" + " · ".join(functions))

    layman = breakdown.get("layman") or {}
    if layman.get("progression"):
        st.markdown(f'"{layman["progression"]}"')

    if problems:
        st.markdown("**特征：**")
        render_problems(problems)


def _render_options(options: list[dict], level: str) -> None:
    st.subheader("建议")
    if not options:
        st.caption("没有生成建议。")
        return

    for option in options:
        st.markdown(f"#### {option.get('label', '')}")
        st.markdown(f"**{format_chords(option.get('chords', []))}**")

        if option.get("feature"):
            st.markdown(guard_layman_text(option["feature"], level))
        if option.get("reason"):
            st.markdown(f"> {guard_layman_text(option['reason'], level)}")

        theory = option.get("theory") or []
        if theory:
            with st.expander("理论依据"):
                for item in theory:
                    st.markdown(f"- {item}")


def _render_result() -> None:
    result = st.session_state.tutor_result
    if result is None:
        return

    st.divider()
    render_degradation(result.degradation)

    _render_analysis(result.breakdown, result.problems)
    _render_options(result.options, st.session_state.tutor_user_level)

    st.divider()
    st.subheader("依据与校验")
    render_grounding(result.grounding)

    for warning in result.diversity_warnings:
        st.warning(warning)

    render_evidence(result.evidence)

    if result.trace_id:
        st.caption(f"trace_id：`{result.trace_id}`（可用于复现这次结果）")


def main() -> None:
    _init_state()
    _render_inputs()
    _render_result()


main()
