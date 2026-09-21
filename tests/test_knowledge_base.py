"""知识库构建的 pytest 校验（对应阶段门 G3）。

这些测试把「知识库质量」从口号变成持续约束：
分类缺失、来源缺失、通俗层缺失、related 悬空，任何一项都会让 CI 失败。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "knowledge"))

import build_kb  # noqa: E402  （路径注入后导入）

REQUIRED_CATEGORIES = build_kb.REQUIRED_CATEGORIES


@pytest.fixture(scope="module")
def entries() -> list[dict]:
    return build_kb.load_entries()


def test_knowledge_base_has_no_validation_errors(entries):
    errors = build_kb.validate(entries)
    assert not errors, "知识库校验失败：\n" + "\n".join(errors)


def test_all_six_categories_present(entries):
    """§3.6.1 的六个目录全部非空。"""
    present = {e["category"] for e in entries}
    missing = [c for c in REQUIRED_CATEGORIES if c not in present]
    assert not missing, f"缺少分类：{missing}"


def test_entry_count_within_mvp_scope(entries):
    """§3.6.3：文档数量 20~30 篇。

    当前为 P0 骨架阶段（每分类 1~2 篇示例）；本测试在 P3 完成知识库后
    会自动成为发布门槛，这也是把它写成断言而非注释的原因。
    """
    assert len(entries) >= 6, "至少每个分类一篇"
    assert len(entries) <= 30, f"超出 MVP 计划的 20~30 篇范围（当前 {len(entries)}）"


def test_every_entry_has_layman_layer(entries):
    """通俗解释层不是可选项 —— 它是 Layman-aware Generation 的物理载体（§3.6.2）。"""
    missing = [e["_path"] for e in entries if not (e.get("layman_title") or e.get("layman_content"))]
    assert not missing, f"以下条目缺少通俗层：{missing}"


def test_every_entry_has_traceable_source(entries):
    """§3.6.2：来源与许可 100% 可追溯。"""
    bad = []
    for e in entries:
        src = e.get("source") or {}
        if not all(src.get(f) for f in ("title", "url", "license")):
            bad.append(e["_path"])
    assert not bad, f"以下条目的来源信息不完整：{bad}"


def test_ids_are_unique_and_prefixed(entries):
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "存在重复 id"
    for e in entries:
        assert e["id"].startswith(e["category"] + "."), f"{e['id']} 未以分类为前缀"


def test_related_references_resolve(entries):
    ids = {e["id"] for e in entries}
    dangling = [
        (e["_path"], ref) for e in entries for ref in (e.get("related") or []) if ref not in ids
    ]
    assert not dangling, f"related 引用了不存在的条目：{dangling}"


def test_build_produces_versioned_artifact(entries, scratch_dir, monkeypatch):
    """构建产物必须带版本 hash —— 实验溯源依赖它。"""
    monkeypatch.setattr(build_kb, "BUILD_DIR", scratch_dir)
    stats = build_kb.build(entries)
    assert stats["version"] and len(stats["version"]) == 12
    assert stats["entry_count"] == len(entries)

    # 统计里出现的分类必须都是合法分类，且覆盖当前已有条目
    assert set(stats["by_category"]) <= set(REQUIRED_CATEGORIES)
    assert set(stats["by_category"]) == {e["category"] for e in entries}

    jsonl = (scratch_dir / "kb.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(jsonl) == len(entries)
    for line in jsonl:
        json.loads(line)  # 每行都是合法 JSON


def test_build_version_changes_with_content(entries, scratch_dir, monkeypatch):
    """内容变了，版本 hash 必须变 —— 否则实验溯源无意义。"""
    monkeypatch.setattr(build_kb, "BUILD_DIR", scratch_dir)
    v1 = build_kb.build(entries)["version"]
    mutated = [dict(entries[0]), *entries[1:]]
    mutated[0] = {**mutated[0], "content": mutated[0]["content"] + "\n补充一句。"}
    v2 = build_kb.build(mutated)["version"]
    assert v1 != v2


# --- 校验器自身的注入式测试：确保它真的能发现问题 ---


def test_validator_detects_missing_source():
    bad = [
        {
            "id": "harmony.x.01",
            "title": "t",
            "category": "harmony",
            "content": "c",
            "layman_title": "l",
            "layman_content": "l",
            "source": {"title": "t"},  # 缺 url / license
            "_path": "fake.yaml",
        }
    ]
    errors = build_kb.validate(bad)
    assert any("source 缺少" in e for e in errors)


def test_validator_detects_dangling_related():
    bad = [
        {
            "id": "harmony.x.01",
            "title": "t",
            "category": "harmony",
            "content": "c",
            "layman_title": "l",
            "layman_content": "l",
            "source": {"title": "t", "url": "u", "license": "l"},
            "related": ["does.not.exist"],
            "_path": "fake.yaml",
        }
    ]
    errors = build_kb.validate(bad)
    assert any("related 引用" in e for e in errors)


def test_validator_detects_duplicate_id():
    base = {
        "id": "harmony.x.01",
        "title": "t",
        "category": "harmony",
        "content": "c",
        "layman_title": "l",
        "layman_content": "l",
        "source": {"title": "t", "url": "u", "license": "l"},
    }
    errors = build_kb.validate([{**base, "_path": "a.yaml"}, {**base, "_path": "b.yaml"}])
    assert any("id 重复" in e for e in errors)


def test_validator_detects_empty_category():
    """六个分类任一为空都应被拦截（§3.6.1）。"""
    errors = build_kb.validate([])
    assert len([e for e in errors if "为空" in e]) == len(REQUIRED_CATEGORIES)
