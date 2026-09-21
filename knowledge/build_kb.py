"""知识库构建器 —— 校验 + 构建（**仅使用标准库**，不引入第三方依赖）。

对齐 ``MVP计划.md`` §3.6.1（目录树）、§3.6.2（条目结构）、§3.6.3（20~30 篇）。

用法::

    python knowledge/build_kb.py --check      # 只校验，不产出
    python knowledge/build_kb.py --build      # 校验 + 产出 knowledge/build/*.jsonl

**校验是硬门槛**：条目缺来源 / 缺通俗层 / id 重复 / related 悬空，一律失败。
这是「知识库质量」从口号变成工程约束的地方（§8 风险应对「知识库质量不足」）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

KB_ROOT = Path(__file__).resolve().parent
ENTRIES_DIR = KB_ROOT / "entries"
BUILD_DIR = KB_ROOT / "build"

#: §3.6.1 要求的六个分类，全部必须非空
REQUIRED_CATEGORIES = ("harmony", "melody", "emotion", "starter", "examples", "layman")

#: §3.6.2 要求的必填字段（含通俗解释层）
REQUIRED_FIELDS = ("id", "title", "category", "content", "layman_title", "layman_content", "source")

LIST_FIELDS = ("tags", "examples", "related", "roman_numerals", "emotions", "applicable_genres")
SCALAR_FIELDS = ("subcategory", "title_en", "key_context", "difficulty", "priority", "confidence_level")

SOURCE_FIELDS = ("title", "url", "license")


class KbError(Exception):
    """知识库校验失败。"""


# --------------------------------------------------------------------------- #
# 极简 YAML front-matter 解析（仅支持本知识库用到的子集，零依赖）
# --------------------------------------------------------------------------- #


def parse_entry_file(path: Path) -> dict:
    """解析 ``---`` 包裹的 front-matter + 正文。

    支持：``key: value``、``key: [a, b]``、``key:`` + 缩进列表、``|`` 块标量、嵌套 ``source:``。
    """
    text = path.read_text(encoding="utf-8")
    if not text.lstrip().startswith("---"):
        raise KbError(f"{path.name}: 缺少 YAML front-matter（文件必须以 --- 开头）")

    body_lines = text.lstrip().splitlines()
    end = next((i for i, ln in enumerate(body_lines[1:], start=1) if ln.strip() == "---"), None)
    if end is None:
        raise KbError(f"{path.name}: front-matter 未闭合（缺少结束的 ---）")

    fm_lines = body_lines[1:end]
    content_body = "\n".join(body_lines[end + 1 :]).strip()

    data = _parse_block(fm_lines, path)
    if content_body and "content" not in data:
        data["content"] = content_body
    data["_path"] = str(path.relative_to(KB_ROOT.parent))
    return data


def _parse_block(lines: list[str], path: Path, base_indent: int = 0) -> dict:
    data: dict = {}
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.strip().startswith("#"):
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent < base_indent:
            break
        if ":" not in raw:
            raise KbError(f"{path.name}: 第 {i + 1} 行不是合法的 key: value —— {raw!r}")

        key, _, value = raw.strip().partition(":")
        key, value = key.strip(), value.strip()

        if value in ("|", ">"):
            block, i = _collect_block_scalar(lines, i + 1, indent)
            data[key] = block
            continue

        if value == "":
            # 嵌套映射（如 source:）或缩进列表
            sub, i = _collect_nested(lines, i + 1, indent, path)
            data[key] = sub
            continue

        data[key] = _parse_scalar(value)
        i += 1
    return data


def _collect_block_scalar(lines: list[str], start: int, parent_indent: int) -> tuple[str, int]:
    out: list[str] = []
    i = start
    while i < len(lines):
        raw = lines[i]
        if raw.strip() and (len(raw) - len(raw.lstrip())) <= parent_indent:
            break
        out.append(raw.strip())
        i += 1
    return "\n".join(out).strip(), i


def _collect_nested(lines: list[str], start: int, parent_indent: int, path: Path) -> tuple[object, int]:
    items: list[str] = []
    mapping: dict = {}
    i = start
    while i < len(lines):
        raw = lines[i]
        if not raw.strip():
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent <= parent_indent:
            break
        stripped = raw.strip()
        if stripped.startswith("- "):
            items.append(_parse_scalar(stripped[2:].strip()))
        elif ":" in stripped:
            k, _, v = stripped.partition(":")
            mapping[k.strip()] = _parse_scalar(v.strip())
        else:
            raise KbError(f"{path.name}: 第 {i + 1} 行无法解析 —— {raw!r}")
        i += 1

    if items and mapping:
        raise KbError(f"{path.name}: 同一字段下混用了列表与映射")
    return (items if items else mapping), i


def _parse_scalar(value: str):
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(x.strip()) for x in inner.split(",")]
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return _strip_quotes(value)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #


def load_entries() -> list[dict]:
    if not ENTRIES_DIR.is_dir():
        raise KbError(f"知识库目录不存在：{ENTRIES_DIR}")
    entries: list[dict] = []
    for path in sorted(ENTRIES_DIR.rglob("*.yaml")):
        entries.append(parse_entry_file(path))
    return entries


def validate(entries: list[dict]) -> list[str]:
    """返回错误列表（空列表 = 通过）。"""
    errors: list[str] = []
    seen_ids: dict[str, str] = {}
    categories: dict[str, int] = {}

    for entry in entries:
        path = entry.get("_path", "?")
        for field in REQUIRED_FIELDS:
            if not entry.get(field):
                errors.append(f"{path}: 缺少必填字段 {field!r}")

        entry_id = entry.get("id")
        if entry_id:
            if entry_id in seen_ids:
                errors.append(f"{path}: id 重复 —— {entry_id}（已在 {seen_ids[entry_id]}）")
            seen_ids[entry_id] = path

        category = entry.get("category")
        if category:
            categories[category] = categories.get(category, 0) + 1
            if category not in REQUIRED_CATEGORIES:
                errors.append(f"{path}: 非法分类 {category!r}，允许 {REQUIRED_CATEGORIES}")
            elif entry_id and not str(entry_id).startswith(category + "."):
                errors.append(f"{path}: id 应以分类为前缀（{category}.），实际为 {entry_id}")

        source = entry.get("source") or {}
        if not isinstance(source, dict):
            errors.append(f"{path}: source 必须是映射（title/url/license）")
        else:
            for field in SOURCE_FIELDS:
                if not source.get(field):
                    errors.append(f"{path}: source 缺少 {field!r}（§3.6.2 要求 100% 可追溯）")

        for field in LIST_FIELDS:
            if field in entry and entry[field] is not None and not isinstance(entry[field], list):
                errors.append(f"{path}: {field} 必须是列表")

        for field in SCALAR_FIELDS:
            if field in entry and isinstance(entry[field], list):
                errors.append(f"{path}: {field} 应为标量，实际是列表")

    # related 引用可解析
    for entry in entries:
        for ref in entry.get("related") or []:
            if ref not in seen_ids:
                errors.append(f"{entry.get('_path', '?')}: related 引用了不存在的 id —— {ref}")

    # §3.6.1：六个分类全部非空
    for category in REQUIRED_CATEGORIES:
        if categories.get(category, 0) == 0:
            errors.append(f"分类 {category!r} 为空 —— §3.6.1 要求六个目录全部非空")

    return errors


def build(entries: list[dict]) -> dict:
    """产出构建产物：JSONL + 统计信息（带内容 hash，供实验溯源）。"""
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entries, ensure_ascii=False, sort_keys=True).encode("utf-8")
    version = hashlib.sha256(payload).hexdigest()[:12]

    clean = [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]
    jsonl_path = BUILD_DIR / "kb.jsonl"
    jsonl_path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in clean), encoding="utf-8"
    )

    stats = {
        "version": version,
        "entry_count": len(clean),
        "by_category": {},
        "by_difficulty": {},
    }
    for e in clean:
        stats["by_category"][e["category"]] = stats["by_category"].get(e["category"], 0) + 1
        diff = e.get("difficulty", "unknown")
        stats["by_difficulty"][diff] = stats["by_difficulty"].get(diff, 0) + 1

    (BUILD_DIR / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MuseGuide 知识库校验 / 构建")
    parser.add_argument("--check", action="store_true", help="只校验")
    parser.add_argument("--build", action="store_true", help="校验并产出构建产物")
    args = parser.parse_args(argv)

    try:
        entries = load_entries()
    except KbError as exc:
        print(f"[FAIL] {exc}")
        return 1

    errors = validate(entries)
    if errors:
        print(f"[FAIL] 校验未通过，共 {len(errors)} 个问题：")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"[OK] 校验通过：{len(entries)} 篇条目")
    if args.build or not args.check:
        stats = build(entries)
        print(f"[OK] 构建完成：version={stats['version']}  {stats['by_category']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
