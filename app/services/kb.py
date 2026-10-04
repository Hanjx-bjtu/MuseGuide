"""知识库加载（供检索层使用）。

与 ``knowledge/build_kb.py`` 共用同一套解析逻辑 —— **不重复实现 YAML 解析**，
避免两处解析规则漂移导致「校验通过但检索读不到」这类问题。
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from app.core.config import PROJECT_ROOT

_KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"


def _import_build_kb():
    """把 ``knowledge/`` 加入模块路径后导入 build_kb。"""
    path = str(_KNOWLEDGE_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    import build_kb  # type: ignore[import-not-found]

    return build_kb


def load_entries(kb_dir: Path | None = None) -> list[dict]:
    """加载并解析全部知识条目。

    :param kb_dir: 知识库根目录；默认 ``<项目根>/knowledge``
    :raises FileNotFoundError: 知识库目录或条目不存在
    """
    build_kb = _import_build_kb()
    root = Path(kb_dir) if kb_dir else _KNOWLEDGE_DIR
    entries_dir = root / "entries"
    if not entries_dir.is_dir():
        raise FileNotFoundError(f"知识库条目目录不存在：{entries_dir}")

    original = build_kb.ENTRIES_DIR
    try:
        build_kb.ENTRIES_DIR = entries_dir
        entries = build_kb.load_entries()
    finally:
        build_kb.ENTRIES_DIR = original

    if not entries:
        raise FileNotFoundError(f"知识库为空：{entries_dir}")
    return entries


@lru_cache(maxsize=1)
def cached_entries() -> tuple[dict, ...]:
    """进程内缓存（知识库在运行期不变，避免每次请求重复读盘）。"""
    return tuple(load_entries())


def clear_cache() -> None:
    """清除缓存（测试或知识库热更新后使用）。"""
    cached_entries.cache_clear()
