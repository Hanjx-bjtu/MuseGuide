"""界面层（Streamlit）。

**分层约束（ADR-0003）：** ``ui`` 可以依赖 ``services`` / ``core`` / ``providers``，
但**不得被服务层依赖**。它通过 HTTP 与后端通信（ADR-0005）。
"""
