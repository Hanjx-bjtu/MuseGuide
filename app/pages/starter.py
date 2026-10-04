"""零基础入口：意图输入 → 引导式交互 → 起步方案（P5.2 / P5.3 / P5.4）。

布局严格对齐 ``MVP计划.md`` §3.10.2（首页）、§3.10.3（引导式交互）、
§3.10.4（起步方案展示）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 修正 sys.path：streamlit run 会把脚本所在目录放进 sys.path[0]，
# 导致 `import app.*` 失败。详见 app/bootstrap.py。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from app.ui.client import APIError, MuseGuideClient  # noqa: E402
from app.ui.components import (  # noqa: E402
    format_chords,
    guard_layman_text,
    render_degradation,
    render_evidence,
)

#: §3.10.2 的首页大方向快捷按钮。
#:
#: ⚠️ 这里存的必须是**概念名**（``concept``）而不是界面文案 ——
#: 引导页按 concept 匹配选项。实测踩过：早期存的是界面文案，
#: 导致引导页反查失败抛 ``StopIteration``。
QUICK_START = [
    ("🎸 温柔民谣", "民谣", "温暖"),
    ("🎤 流行抒情", "流行", "释然"),
    ("🎹 钢琴曲", "钢琴", "温柔"),
    ("🥁 轻快节奏", "流行", "轻快"),
    ("🌙 梦幻电子", "电子", "梦幻"),
]


def _init_state() -> None:
    defaults = {
        "starter_text": "",
        "starter_emotion": None,
        "starter_style": None,
        "starter_tempo": None,
        "starter_stage": "home",  # home -> guide -> plan
        "starter_result": None,
        # 生成期间置 True 并禁用按钮，防止用户重复点击造成多次 API 调用
        "starter_busy": False,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _client() -> MuseGuideClient:
    return MuseGuideClient()


def _load_options() -> dict:
    """选择式输入选项（§3.10.2：每个选项都带通俗说明）。"""
    try:
        return _client().options()
    except APIError as exc:
        st.error(str(exc))
        return {"emotion": [], "style": [], "tempo": []}


# --------------------------------------------------------------------------- #
# Stage 1：首页（§3.10.2）
# --------------------------------------------------------------------------- #


def _render_home(options: dict) -> None:
    st.title("你想写什么样的歌？")
    st.caption("不需要懂乐理，用你自己的话描述就行。")

    text = st.text_area(
        "用日常语言说说你的想法",
        value=st.session_state.starter_text,
        height=120,
        placeholder="我想写一首关于毕业的歌，有点伤感但最后是释然的感觉……",
        label_visibility="collapsed",
    )

    st.markdown("**或者，先选一个大方向：**")
    columns = st.columns(len(QUICK_START))
    for column, (label, style, emotion) in zip(columns, QUICK_START):
        if column.button(label, use_container_width=True):
            st.session_state.starter_text = text or st.session_state.starter_text
            st.session_state.starter_style = style
            st.session_state.starter_emotion = emotion
            st.session_state.starter_stage = "guide"
            st.rerun()

    st.write("")
    if st.button("开始 →", type="primary", use_container_width=True):
        st.session_state.starter_text = text
        st.session_state.starter_stage = "guide"
        st.rerun()

    st.divider()
    st.caption("已有雏形？可以直接去左边的「已有雏形」页面，输入和弦与目标。")


# --------------------------------------------------------------------------- #
# Stage 2：引导式交互（§3.10.3）
# --------------------------------------------------------------------------- #


def _render_option_group(
    title: str,
    question: str,
    items: list[dict],
    state_key: str,
) -> str | None:
    """渲染一组选择式输入，返回选中项的 ``concept``。

    **用「标签列表 + index」而不是「标签 → concept 反查」**：
    反查写法在标签对不上时会抛 ``StopIteration``（实测踩过），
    而下拉/单选本身就是按索引工作的。同时把 ``None`` 显式处理成
    「用户还没选」，而不是让它变成一次查找失败。

    每个选项都带 ``hint`` 作为通俗说明（§3.10.2 / §3.10.3 的硬性要求）。
    """
    st.subheader(title)
    st.markdown(question)
    if not items:
        st.caption("选项加载失败，请检查后端服务。")
        return None

    labels = [item["label"] for item in items]
    concepts = [item["concept"] for item in items]

    current = st.session_state.get(state_key)
    index = concepts.index(current) if current in concepts else None

    # 未选择时给出明确的空选项，而不是让 st.radio 自己显示「没有任何选中」。
    #
    # **实测问题：** 三组都是 `index=None` 时，用户看到的是三排没有光标的按钮，
    # 既不知道要不要选、也不知道不选会怎样；而点「生成」竟然能直接出结果。
    # 显式加一个「还没想好」选项后，用户至少有确定的默认态可点。
    placeholder = "还没想好，帮我决定"
    options = [placeholder, *labels]

    chosen_label = st.radio(
        title,
        options,
        index=(index + 1) if index is not None else 0,
        label_visibility="collapsed",
        captions=["交给系统根据你的描述来定"] + [item.get("hint", "") for item in items],
        key=f"{state_key}_widget",
    )

    concept = concepts[labels.index(chosen_label)] if chosen_label in labels else None
    st.session_state[state_key] = concept
    return concept


def _render_guide(options: dict) -> None:
    st.title("先说几个大概方向")
    st.caption("选不出来也没关系，随便挑一个接近的就行。")

    if st.session_state.starter_text:
        st.info(f"你的想法：{st.session_state.starter_text}")

    # Step 1：速度感（§3.10.3 的界面草案）
    _render_option_group(
        "Step 1：速度感",
        "你觉得这首歌大概什么速度？",
        options.get("tempo", []),
        "starter_tempo",
    )

    # Step 2：风格
    _render_option_group(
        "Step 2：风格",
        "你想要什么感觉？",
        options.get("style", []),
        "starter_style",
    )

    # Step 3：情绪
    _render_option_group(
        "Step 3：情绪",
        "这首歌想让人感觉到什么？",
        options.get("emotion", []),
        "starter_emotion",
    )

    st.divider()

    # 提交前先回显「系统将依据什么来生成」，避免用户不清楚自己的选择是否生效。
    _render_selection_summary()

    left, right = st.columns([1, 1])
    if left.button("← 返回", use_container_width=True):
        st.session_state.starter_stage = "home"
        st.rerun()
    if right.button(
        "生成起步方案 →",
        type="primary",
        use_container_width=True,
        disabled=st.session_state.get("starter_busy", False),
    ):
        st.session_state.starter_busy = True
        try:
            with st.spinner("正在为你搭一个框架……（约 5~10 秒）"):
                result = _client().starter(
                    text=st.session_state.starter_text,
                    emotion=st.session_state.starter_emotion,
                    style=st.session_state.starter_style,
                    tempo_label=st.session_state.starter_tempo,
                )
            st.session_state.starter_result = result
            st.session_state.starter_stage = "plan"
        except APIError as exc:
            st.error(str(exc))
        finally:
            st.session_state.starter_busy = False
        st.rerun()


def _render_selection_summary() -> None:
    """回显当前选择，并说明没选的项会怎么处理。

    实测问题：三组选择都不点时，用户完全不知道系统会用什么值 ——
    而系统确实会从描述里推断，或退回默认值。**把这件事说清楚，
    比让用户点一个没有反馈的按钮要好。**
    """
    emotion = st.session_state.get("starter_emotion")
    style = st.session_state.get("starter_style")
    tempo = st.session_state.get("starter_tempo")

    chosen = [
        label
        for label, value in (("情绪", emotion), ("风格", style), ("速度", tempo))
        if value
    ]

    if len(chosen) == 3:
        st.caption(f"将依据：情绪 **{emotion}**、风格 **{style}**、速度 **{tempo}**")
    elif chosen:
        picked = "、".join(
            f"{name} **{value}**"
            for name, value in (("情绪", emotion), ("风格", style), ("速度", tempo))
            if value
        )
        st.caption(f"将依据：{picked}；其余部分由系统根据你的描述推断。")
    else:
        st.caption("将依据：你的描述 —— 三组都交给系统根据描述推断。")


# --------------------------------------------------------------------------- #
# Stage 3：起步方案（§3.10.4）
# --------------------------------------------------------------------------- #


def _render_plan() -> None:
    result = st.session_state.starter_result
    if result is None:
        st.session_state.starter_stage = "home"
        st.rerun()
        return

    plan = result.plan
    st.title("你的起步方案")

    render_degradation(result.degradation)
    for warning in result.warnings:
        st.warning(warning)

    # 调性
    st.subheader(f"调性：{plan.get('key', '')}")
    st.markdown(guard_layman_text(plan.get("key_explanation", "")))
    st.caption(f"速度：{plan.get('tempo', '')} BPM — {plan.get('tempo_explanation', '')}")

    # 段落（§3.10.4：主歌（伤感）/ 副歌（释然））
    for section in plan.get("sections", []):
        emotion = section.get("emotion") or ""
        heading = f"{section.get('name', '')}（{emotion}）" if emotion else section.get("name", "")
        st.subheader(heading)
        st.markdown(f"### {format_chords(section.get('chords', []))}")
        st.markdown(guard_layman_text(section.get("explanation", "")))

    if plan.get("why"):
        st.divider()
        st.subheader("为什么这样选")
        st.markdown(guard_layman_text(plan["why"]))

    hints = plan.get("adjust_hints") or []
    if hints:
        st.subheader("如果你想调整")
        for hint in hints:
            st.markdown(f"- {guard_layman_text(hint)}")

    # 依据（P5.6）
    st.divider()
    st.subheader("理论依据")
    render_evidence(result.evidence)

    if result.intent:
        with st.expander("看看系统是怎么理解你的描述的"):
            st.json(result.intent)

    st.divider()
    left, right = st.columns([1, 1])
    if left.button("← 重新选择", use_container_width=True):
        st.session_state.starter_stage = "guide"
        st.rerun()
    if right.button("换一个想法", use_container_width=True):
        st.session_state.starter_text = ""
        st.session_state.starter_emotion = None
        st.session_state.starter_style = None
        st.session_state.starter_tempo = None
        st.session_state.starter_result = None
        st.session_state.starter_stage = "home"
        st.rerun()

    st.caption(f"trace_id：`{result.trace_id}`（可用于复现这次结果）")


def main() -> None:
    _init_state()
    options = _load_options()

    stage = st.session_state.starter_stage
    if stage == "home":
        _render_home(options)
    elif stage == "guide":
        _render_guide(options)
    else:
        _render_plan()


main()
