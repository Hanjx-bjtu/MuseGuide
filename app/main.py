"""MuseGuide Streamlit 应用（P5.1）。

**双入口**（``MVP计划.md`` §3.10.1）：零基础入口为默认，进阶入口并列。

运行::

    streamlit run app/main.py

前提：后端已启动::

    uvicorn app.api.server:app --port 8000

界面通过 HTTP 调后端（ADR-0005），不直接 import 服务层。
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="MuseGuide · 帮你迈出写歌的第一步",
    page_icon="🎵",
    layout="centered",
    initial_sidebar_state="expanded",
)


def _pages():
    """用 ``st.navigation`` 组织双入口（§3.10 的两种模式）。"""
    starter = st.Page(
        "pages/starter.py",
        title="零基础起步",
        icon="🌱",
        default=True,
        url_path="starter",
    )
    tutor = st.Page(
        "pages/tutor.py",
        title="已有雏形",
        icon="🎼",
        url_path="tutor",
    )
    about = st.Page(
        "pages/about.py",
        title="关于与依据",
        icon="📖",
        url_path="about",
    )
    return [starter, tutor, about]


navigation = st.navigation(_pages())

with st.sidebar:
    st.markdown("### 🎵 MuseGuide")
    st.caption("帮你迈出写歌的第一步")

    from app.ui.client import MuseGuideClient

    try:
        health = MuseGuideClient().health()
        if health.get("llm_available"):
            st.success(f"AI 模型已就绪（{health.get('llm_model', '')}）")
        else:
            st.info("当前为简化模式：未接入 AI 模型，内容由知识库直接生成。")
        st.caption(f"知识库：{health.get('kb_entries', 0)} 篇条目")
    except Exception as exc:  # noqa: BLE001 - 后端不可达时也要让界面可用
        st.error("连不上后端服务")
        st.caption("请先在另一个终端运行：")
        st.code("uvicorn app.api.server:app --port 8000", language="powershell")
        st.caption(str(exc).splitlines()[0])

    st.divider()
    st.caption("不需要懂乐理，用你自己的话描述就行，我来帮你翻译成音乐。")

navigation.run()
