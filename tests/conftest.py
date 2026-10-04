"""共享测试夹具。

**为什么自定义 ``scratch_dir`` 而不用 pytest 内置的 ``tmp_path``：**
pytest 的 tmp_path 会在系统临时目录（或 ``--basetemp``）下扫描并清理
``pytest-of-*`` 目录，该扫描在本机的受限运行环境下会被拒绝
（``PermissionError: [WinError 5]``），导致用例在 **teardown** 阶段报错。

``scratch_dir`` 直接在工作区下用唯一前缀建目录并自行删除，
不依赖 pytest 的临时目录管理，也不依赖系统临时目录。
功能等价，代价是要自己清理 —— 已在 finally 中处理。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def scratch_dir():
    """提供一个用完即删的临时目录（位于工作区内，避开系统临时目录）。"""
    path = PROJECT_ROOT / f".scratch-{uuid4().hex[:12]}"
    path.mkdir(parents=False, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
