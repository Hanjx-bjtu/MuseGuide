"""MuseGuide —— 面向零基础音乐创作爱好者的知识增强型 AI 创作陪伴助手。

分层依赖规则（由 tests/test_architecture.py 静态强制）::

    core/  ←── 谁都可以依赖，它不依赖任何业务模块
      ↑
    providers/ ←── services/ ←── api/ ←── app/main.py
"""

__version__ = "0.1.0"
