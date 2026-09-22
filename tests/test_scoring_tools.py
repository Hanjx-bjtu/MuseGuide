"""P6 测试：人工评分工具链。

覆盖 ``make_scoring_sheet.py`` 与 ``summarize_scores.py`` 的**可离线测试**部分 ——
真实 API 调用不在单测范围内（那属于 ``run_generation_eval.py`` 的职责）。

**为什么这些也要测：** 人工评分是整个 P6 唯一「靠人」的环节，
如果工具本身有 bug（如 BOM 导致列名错位、留空被当成 0 分），
评分者花了 1 小时填的表就会白费 —— 而且**错误不会立刻显现**，
只会让报告里的均值悄悄算错。
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = PROJECT_ROOT / "knowledge" / "eval"


def _load(name: str):
    """按路径加载脚本模块（它们不在包结构里）。"""
    path = EVAL_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def summary_mod():
    return _load("summarize_scores")


@pytest.fixture(scope="module")
def sheet_mod():
    return _load("make_scoring_sheet")


# --------------------------------------------------------------------------- #
# 维度定义
# --------------------------------------------------------------------------- #


def test_dimensions_match_mvp_section_7_2(sheet_mod):
    """八个维度必须与 ``MVP计划.md`` §7.2 / §19 的表格一致。"""
    keys = {key for key, _ in sheet_mod.DIMENSIONS}
    assert keys == {
        "theory_validity",
        "relevance",
        "diversity",
        "explainability",
        "groundedness",
        "usefulness",
        "comprehensibility",
        "actionability",
    }


def test_dimensions_are_consistent_between_tools(sheet_mod, summary_mod):
    """生成评分表与汇总报告的维度必须完全一致。

    不一致会导致「表里填了但报告里读不到」这类静默丢失。
    """
    sheet_dims = [key for key, _ in sheet_mod.DIMENSIONS]
    summary_dims = [key for key, _, _ in summary_mod.DIMENSIONS]
    assert sheet_dims == summary_dims


# --------------------------------------------------------------------------- #
# 打分解析：留空必须留空
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", ["", "  ", "abc", "0", "6", "-1", "3.5x"])
def test_parse_score_treats_invalid_as_missing(summary_mod, value):
    """非法或空白一律视为「未评」，**不得当成 0 分**。

    把留空当 0 分会把均值拉低，让报告显示一个虚假的低分。
    """
    assert summary_mod._parse_score(value) is None


@pytest.mark.parametrize("value,expected", [("1", 1), ("5", 5), ("3", 3), (" 4 ", 4), ("4.0", 4)])
def test_parse_score_accepts_valid(summary_mod, value, expected):
    assert summary_mod._parse_score(value) == expected


# --------------------------------------------------------------------------- #
# 汇总统计
# --------------------------------------------------------------------------- #


def _row(case_id: str, **scores) -> dict:
    row = {"case_id": case_id, "kind": "advice", "input": "x", "latency_s": "1.0", "comment": ""}
    row.update(scores)
    return row


def test_summarize_counts_missing_separately(summary_mod):
    """已评与未评必须分开统计，且未评不影响均值。"""
    rows = [
        _row("a", relevance="5", usefulness="4"),
        _row("b", relevance="3"),  # usefulness 留空
    ]
    stats = summary_mod.summarize(rows)
    relevance = stats["dimensions"]["relevance"]
    usefulness = stats["dimensions"]["usefulness"]

    assert relevance["scored"] == 2
    assert relevance["mean"] == 4.0

    assert usefulness["scored"] == 1
    assert usefulness["missing"] == 1
    assert usefulness["mean"] == 4.0, "未评不应拉低均值"


def test_summarize_handles_all_blank(summary_mod):
    """一题都没评时，均值应为 None 而不是 0。"""
    stats = summary_mod.summarize([_row("a")])
    for key, _, _ in summary_mod.DIMENSIONS:
        item = stats["dimensions"][key]
        assert item["mean"] is None
        assert item["scored"] == 0
        assert item["missing"] == 1


# --------------------------------------------------------------------------- #
# Cohen's κ
# --------------------------------------------------------------------------- #


def test_kappa_perfect_agreement(summary_mod):
    assert summary_mod.cohens_kappa([4, 4, 4, 4], [4, 4, 4, 4]) == 1.0


def test_kappa_partial_agreement(summary_mod):
    kappa = summary_mod.cohens_kappa([4, 2, 4, 4], [4, 3, 4, 4])
    assert 0 < kappa < 1


def test_kappa_total_disagreement(summary_mod):
    """系统性相反时 κ 应接近 0 或为负。"""
    assert summary_mod.cohens_kappa([1, 1, 1, 1], [5, 5, 5, 5]) <= 0


def test_kappa_handles_empty(summary_mod):
    assert summary_mod.cohens_kappa([], []) == 0.0
    assert summary_mod.cohens_kappa([1], [1, 2]) == 0.0


def test_kappa_below_threshold_is_flagged(summary_mod):
    """κ < 0.6 的维度必须被标记为「不可用于结论」。"""
    rows = [_row(f"c{i}", relevance=str(score)) for i, score in enumerate([1, 2, 3, 4, 5])]
    second = [_row(f"c{i}", relevance=str(score)) for i, score in enumerate([5, 4, 3, 2, 1])]

    stats = summary_mod.summarize(rows, second)
    item = stats["dimensions"]["relevance"]
    assert "kappa" in item
    assert item["kappa_ok"] is False


# --------------------------------------------------------------------------- #
# CSV 读取的健壮性
# --------------------------------------------------------------------------- #


def test_load_scores_handles_bom(scratch_dir, summary_mod):
    """回归：必须容忍 BOM。

    实测教训：Excel 与 PowerShell 保存 CSV 时会写入 BOM，
    按 utf-8 读取会让第一个列名变成 ``\\ufeffcase_id``，
    导致后续按列名取值全部失败（``KeyError: 'case_id'``）。
    """
    path = scratch_dir / "scores.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "relevance"])
        writer.writeheader()
        writer.writerow({"case_id": "a", "relevance": "4"})

    rows = summary_mod.load_scores(path)
    assert rows[0]["case_id"] == "a"
    assert summary_mod._parse_score(rows[0]["relevance"]) == 4


def test_load_scores_rejects_missing_file(scratch_dir, summary_mod):
    with pytest.raises(SystemExit, match="找不到评分表"):
        summary_mod.load_scores(scratch_dir / "nope.csv")


def test_load_scores_rejects_missing_case_id(scratch_dir, summary_mod):
    path = scratch_dir / "bad.csv"
    path.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="缺少必需列"):
        summary_mod.load_scores(path)


def test_load_scores_rejects_empty_table(scratch_dir, summary_mod):
    path = scratch_dir / "empty.csv"
    path.write_text("case_id,relevance\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="为空"):
        summary_mod.load_scores(path)


# --------------------------------------------------------------------------- #
# 报告的诚实性
# --------------------------------------------------------------------------- #


def test_report_flags_unscored_state(summary_mod):
    """一题未评时必须明确提示，而不是显示一张空表。"""
    stats = summary_mod.summarize([_row("a")])
    report = summary_mod.render([_row("a")], stats, second_used=False)
    assert "尚未评分" in report


def test_report_flags_small_sample(summary_mod):
    """样本量不足时必须提示「不足以支撑结论」。"""
    rows = [_row(f"c{i}", relevance="5") for i in range(3)]
    stats = summary_mod.summarize(rows)
    report = summary_mod.render(rows, stats, second_used=False)
    assert "不足以支撑" in report or "仅供参考" in report


def test_report_marks_below_threshold_dimensions(summary_mod):
    """未达门槛的维度必须显示 ❌，不能只报喜。"""
    rows = [_row(f"c{i}", relevance="1") for i in range(6)]
    stats = summary_mod.summarize(rows)
    report = summary_mod.render(rows, stats, second_used=False)
    assert "❌" in report


def test_report_mentions_scale_limitation(summary_mod):
    """报告必须写明主观评分的局限，不能把它当作绝对结论。"""
    rows = [_row(f"c{i}", relevance="4") for i in range(6)]
    stats = summary_mod.summarize(rows)
    report = summary_mod.render(rows, stats, second_used=False)
    assert "偏差" in report or "局限" in report
    assert "主观" in report


def test_report_states_blank_is_not_zero(summary_mod):
    """必须写明「留空不算 0 分」，否则评分者会以为不填会被扣分。"""
    rows = [_row("a")]
    stats = summary_mod.summarize(rows)
    report = summary_mod.render(rows, stats, second_used=False)
    assert "留空不算 0 分" in report


# --------------------------------------------------------------------------- #
# 评分材料的生成（不调 API，只测渲染）
# --------------------------------------------------------------------------- #


def test_samples_document_explains_blank_is_ok(sheet_mod, scratch_dir, monkeypatch):
    """评分材料本身要告诉评分者「可以留空」。"""
    monkeypatch.setattr(sheet_mod, "OUT_DIR", scratch_dir)
    samples = [
        {
            "case_id": "t.001",
            "kind": "starter",
            "input": "x",
            "latency_s": 1.0,
            "markdown": "### t.001\n\n内容",
            "degradation": [],
        }
    ]
    path = sheet_mod.write_samples(samples)
    text = path.read_text(encoding="utf-8")
    assert "留空比乱填更有价值" in text
    assert "不懂乐理也能评" in text


def test_scores_csv_leaves_score_columns_empty(sheet_mod, scratch_dir, monkeypatch):
    """评分格必须留空 —— 预填任何默认值都会诱导评分者直接采用。"""
    monkeypatch.setattr(sheet_mod, "OUT_DIR", scratch_dir)
    samples = [
        {
            "case_id": "t.001",
            "kind": "starter",
            "input": "x",
            "latency_s": 1.0,
            "markdown": "### t.001",
            "degradation": [],
        }
    ]
    path = sheet_mod.write_scores_csv(samples)
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))

    assert len(rows) == 1
    for key, _ in sheet_mod.DIMENSIONS:
        assert rows[0][key] == "", f"{key} 不应预填值"
