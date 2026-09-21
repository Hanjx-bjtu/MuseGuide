"""关于页：说明定位、边界与依据来源。

**为什么要有这一页：**
``MVP计划.md`` §7.3 与 §9 强调对零基础用户的诚实性。
这一页把「系统能做什么、不能做什么、依据从哪来」写在用户看得到的地方，
而不是只藏在 README 里。
"""

from __future__ import annotations

import streamlit as st

from app.ui.client import MuseGuideClient


def _render_health() -> None:
    st.subheader("当前系统状态")
    try:
        health = MuseGuideClient().health()
    except Exception as exc:  # noqa: BLE001
        st.error("连不上后端服务。")
        st.caption(str(exc).splitlines()[0])
        return

    left, middle, right = st.columns(3)
    left.metric("AI 模型", "已接入" if health.get("llm_available") else "未接入")
    middle.metric("知识库条目", health.get("kb_entries", 0))
    right.metric("版本", health.get("version", "-"))

    if not health.get("llm_available"):
        st.info(
            "当前为简化模式：没有接入 AI 模型，内容由音乐知识库直接生成。"
            "结构和使用方式与接入模型时一致，只是措辞不如模型灵活。"
        )


def main() -> None:
    st.title("关于 MuseGuide")
    st.markdown(
        "**面向零基础音乐创作爱好者的知识增强型 AI 创作陪伴助手。**"
    )
    st.markdown(
        "它不替你写完整首歌，而是帮你迈出第一步，"
        "并在每一步解释「为什么这样选」。"
    )

    _render_health()

    st.divider()
    st.subheader("它能做什么")
    st.markdown(
        "- **从零起步**：用日常语言描述你想要的歌，得到调性与和弦框架，每个选择都配通俗解释\n"
        "- **已有雏形**：输入和弦或旋律与你的修改目标，得到 2~3 个有理论依据的修改方向\n"
        "- **概念科普**：遇到不懂的词，它会用日常类比解释"
    )

    st.subheader("它不做什么")
    st.markdown(
        "依据 `MVP计划.md` §1.3 的明确边界：\n\n"
        "- ❌ 不生成完整歌曲、不自动编曲 / 混音 / 母带\n"
        "- ❌ 不支持哼唱与音频文件上传（当前仅支持文本）\n"
        "- ❌ 不提供试听音频（用文字描述替代）\n"
        "- ❌ 不记住你的历史作品与偏好"
    )

    st.divider()
    st.subheader("依据从哪来")
    st.markdown(
        "每条建议都可以展开「理论依据」，看到它引用的知识条目原文、标签与来源链接。"
        "这是它与普通音乐 AI 的主要差别 —— **建议是可追溯的，不是凭空生成的**。"
    )
    st.markdown(
        "知识库共 29 篇，覆盖和声、旋律、情绪、起步引导、案例与通俗解释六类，"
        "每一条都标注了来源与许可证（主要为 Open Music Theory，CC BY-SA 4.0）。"
    )

    st.subheader("它怎么保证建议靠谱")
    st.markdown(
        "系统对每条建议做**依据校验（Grounding）**，检查：\n\n"
        "- 引用的来源是否真实存在（编造引用会被标出）\n"
        "- 建议的和弦是否在你这首曲子的调性下成立\n"
        "- 建议是否回应了你提出的目标\n"
        "- 是否把你要求「保持」的东西改掉了"
    )
    st.caption(
        "校验不通过时会给出提示，而不是假装没问题。"
        "音乐创作本身有主观性，系统的建议仅供参考。"
    )

    st.divider()
    st.caption("MuseGuide · MVP 阶段 · 依据 `MVP计划.md` 实现")


main()
