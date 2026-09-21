"""分层依赖静态校验（ADR-0003）。

`app/core/` 是契约层，**必须零业务依赖**。若它可以 import 业务模块，
就无法在无网络、无 Key、无界面的环境下被测试 —— P0 的全部价值随之消失。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = PROJECT_ROOT / "app" / "core"

#: core 中禁止出现的顶层导入前缀
FORBIDDEN_IN_CORE = (
    "app.services",
    "app.api",
    "app.providers",
    "fastapi",
    "streamlit",
    "chromadb",
    "openai",
    "music21",
    "rank_bm25",
)

#: 期望的分层：键可以依赖值中的层（但不含 core，因为 core 谁都能依赖）
#:
#: ``app.ui`` 与 ``app.api`` 同层：二者都是「入口」，彼此不得互相 import
#: （界面通过 HTTP 调后端，见 ADR-0005）。因此它们同 rank，不会互相放行。
LAYER_ORDER = {
    "app.core": 0,
    "app.providers": 1,
    "app.services": 2,
    "app.api": 3,
    "app.ui": 3,
}


def _iter_py(root: Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                modules.append(node.module)
    return modules


def test_core_directory_is_not_empty():
    assert _iter_py(CORE_DIR), "app/core 下找不到任何 Python 文件"


@pytest.mark.parametrize("path", _iter_py(CORE_DIR), ids=lambda p: p.name)
def test_core_has_no_business_dependencies(path: Path):
    """app/core/ 不得反向依赖业务层。"""
    offenders = [
        f"{path.name}: {mod}"
        for mod in _imported_modules(path)
        if any(mod == bad or mod.startswith(bad + ".") for bad in FORBIDDEN_IN_CORE)
    ]
    assert not offenders, "契约层出现业务依赖（违反 ADR-0003）：\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    "path",
    [p for p in _iter_py(PROJECT_ROOT / "app") if "core" not in p.parts],
    ids=lambda p: p.name,
)
def test_no_layer_imports_a_higher_layer(path: Path):
    """services 不得 import api；providers 不得 import services/api。"""
    rel = path.relative_to(PROJECT_ROOT).with_suffix("")
    parts = list(rel.parts)
    own_layer = ".".join(parts[:2]) if parts[0] == "app" and len(parts) > 1 else "app"
    own_rank = LAYER_ORDER.get(own_layer)
    if own_rank is None:
        pytest.skip(f"{own_layer} 不在分层表中")

    offenders = []
    for mod in _imported_modules(path):
        for layer, rank in LAYER_ORDER.items():
            if mod == layer or mod.startswith(layer + "."):
                if rank > own_rank:
                    offenders.append(f"{path.name}: {layer} (rank {rank} > {own_rank})")
    assert not offenders, "出现向上依赖：\n" + "\n".join(offenders)
