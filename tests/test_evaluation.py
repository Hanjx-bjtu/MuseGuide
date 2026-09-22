"""P6 测试：指标、实验运行器、报告渲染（阶段门 G6）。

对应 ``实现阶段计划.md`` §10：

* 评测集有区分度（不再全员饱和）
* 一条命令可复现
* 报告数字由代码渲染，不手工誊写
* **必须包含未达预期的诚实结论**
"""

from __future__ import annotations

import json

import pytest

from app.experiment.metrics import (
    context_precision,
    evaluate,
    has_discrimination,
    ndcg_at_k,
    paired_bootstrap,
    recall_at_k,
    reciprocal_rank,
)
from app.experiment.report import (
    _default_notes,
    render_comparisons,
    render_misses,
    render_report,
    render_retrieval_table,
)
from app.experiment.runner import (
    ExperimentResult,
    compare,
    load_retrieval_dataset,
    run_retrieval_experiment,
)
from app.services.retrieval.hybrid import EXPERIMENT_MATRIX, PRODUCTION_CONFIG

# --------------------------------------------------------------------------- #
# 指标
# --------------------------------------------------------------------------- #


def test_loose_recall_hits_any_gold():
    """宽松口径：命中任一 gold 即算对（§7.1 的字面要求）。"""
    assert recall_at_k(["a", "x"], ["a", "b"], 5, strict=False) == 1.0
    assert recall_at_k(["x", "y"], ["a", "b"], 5, strict=False) == 0.0


def test_strict_recall_requires_all_gold():
    """严格口径：必须召回全部 gold —— 这是防止评测集饱和的关键。"""
    assert recall_at_k(["a", "x"], ["a", "b"], 5, strict=True) == 0.5
    assert recall_at_k(["a", "b"], ["a", "b"], 5, strict=True) == 1.0


def test_recall_respects_k():
    assert recall_at_k(["x", "a"], ["a"], 1, strict=False) == 0.0
    assert recall_at_k(["x", "a"], ["a"], 2, strict=False) == 1.0


def test_recall_with_empty_gold_is_zero():
    assert recall_at_k(["a"], [], 5, strict=True) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(["a"], ["a"]) == 1.0
    assert reciprocal_rank(["x", "a"], ["a"]) == 0.5
    assert reciprocal_rank(["x", "y"], ["a"]) == 0.0


def test_ndcg_at_k():
    assert ndcg_at_k(["a"], ["a"], 5) == 1.0
    # 排在第二位的 nDCG 应低于排在第一位
    assert ndcg_at_k(["x", "a"], ["a"], 5) < ndcg_at_k(["a"], ["a"], 5)


def test_context_precision():
    assert context_precision(["a", "b"], ["a"], 5) == 0.5
    assert context_precision(["a"], ["a"], 5) == 1.0


def test_evaluate_aggregates():
    results = [
        {"question": "q1", "gold": ["a"], "retrieved": ["a"]},
        {"question": "q2", "gold": ["b"], "retrieved": ["x"]},
    ]
    metrics = evaluate(results, k=5)
    assert metrics.n_cases == 2
    assert metrics.loose_recall == 0.5
    assert metrics.mrr == 0.5
    assert len(metrics.per_case) == 2
    assert metrics.per_case[1]["missed"] == ["b"]


def test_has_discrimination_detects_saturation():
    """**评测集饱和检测** —— v1 项目最关键的教训。

    若所有配置分数相同，其上跑出的任何对比都不足以支撑结论。
    """
    same = [evaluate([{"gold": ["a"], "retrieved": ["a"]}]) for _ in range(3)]
    assert has_discrimination(same) is False

    different = [
        evaluate([{"gold": ["a"], "retrieved": ["a"]}]),
        evaluate([{"gold": ["a"], "retrieved": ["x"]}]),
    ]
    assert has_discrimination(different) is True


def test_paired_bootstrap_returns_interval():
    a = [1.0] * 20
    b = [0.0] * 20
    mean, low, high = paired_bootstrap(a, b)
    assert mean == 1.0
    assert low > 0, "全胜的差异应显著为正"


def test_paired_bootstrap_detects_no_difference():
    a = [0.5] * 20
    _, low, high = paired_bootstrap(a, a)
    assert low <= 0 <= high, "无差异时区间应跨 0"


def test_paired_bootstrap_handles_mismatched_lengths():
    assert paired_bootstrap([1.0], [1.0, 2.0]) == (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------- #
# 评测集
# --------------------------------------------------------------------------- #


def test_dataset_is_frozen_and_sized():
    cases = load_retrieval_dataset()
    assert len(cases) >= 60, "§7.3 要求 30~50 个用例，检索集应更充分"


def test_dataset_has_multi_gold_cases():
    """多 gold 是严格口径能工作的前提 —— 全单 gold 时严格=宽松。"""
    cases = load_retrieval_dataset()
    multi = [c for c in cases if len(c["gold"]) > 1]
    assert multi, "评测集应包含多 gold 题目"
    assert len(multi) / len(cases) >= 0.2, (
        f"多 gold 占比过低（{len(multi)}/{len(cases)}），严格口径会退化为宽松口径"
    )


def test_dataset_gold_ids_all_exist():
    from app.services.kb import load_entries

    valid = {e["id"] for e in load_entries()}
    for case in load_retrieval_dataset():
        for gold in case["gold"]:
            assert gold in valid, f"{case['q']} 的 gold {gold} 不存在于知识库"


def test_dataset_questions_are_unique():
    questions = [c["q"] for c in load_retrieval_dataset()]
    assert len(questions) == len(set(questions))


# --------------------------------------------------------------------------- #
# 实验运行器
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def hybrid_result() -> ExperimentResult:
    return run_retrieval_experiment("hybrid_rrf", PRODUCTION_CONFIG, k=5)


def test_experiment_result_is_json_serializable(hybrid_result):
    payload = hybrid_result.to_dict()
    json.dumps(payload)
    assert payload["metrics"]["strict_recall@k"] >= 0


def test_hybrid_meets_strict_recall_gate(hybrid_result):
    """G6 验收：严格 Recall@5 ≥ 0.70（docs/ACCEPTANCE.md §3.1）。"""
    assert hybrid_result.metrics.strict_recall >= 0.70, (
        f"严格 Recall@5 = {hybrid_result.metrics.strict_recall:.3f} 未达门槛 0.70"
    )


def test_hybrid_meets_loose_recall_gate(hybrid_result):
    """G6 验收：宽松 Recall@5 ≥ 0.90。"""
    assert hybrid_result.metrics.loose_recall >= 0.90, (
        f"宽松 Recall@5 = {hybrid_result.metrics.loose_recall:.3f} 未达门槛 0.90"
    )


def test_experiment_is_reproducible(hybrid_result):
    """同配置重跑必须得到同样的数字 —— 计划 §10.3 的复现要求。"""
    again = run_retrieval_experiment("hybrid_rrf", PRODUCTION_CONFIG, k=5)
    assert again.metrics.to_dict() == hybrid_result.metrics.to_dict()


def test_experiment_matrix_covers_required_configs():
    """矩阵必须含 baseline 与单路对照，否则无法归因。"""
    assert "baseline_no_retrieval" in EXPERIMENT_MATRIX
    assert "dense_only" in EXPERIMENT_MATRIX
    assert "sparse_only" in EXPERIMENT_MATRIX
    assert "hybrid_rrf" in EXPERIMENT_MATRIX


def test_matrix_changes_one_variable_at_a_time():
    """消融实验的原则：每两组之间只应有一个字段不同。

    违反这条会让差异无法归因 —— 例如同时改融合策略与过滤开关，
    就说不清收益来自哪一个。
    """
    hybrid = EXPERIMENT_MATRIX["hybrid_rrf"].describe()
    filtered = EXPERIMENT_MATRIX["hybrid_rrf_filter"].describe()
    diffs = {k for k in hybrid if hybrid[k] != filtered[k]}
    assert diffs == {"use_metadata_filter"}, f"两配置差异过多：{diffs}"


def test_compare_returns_interval(hybrid_result):
    sparse = run_retrieval_experiment(
        "sparse_only", EXPERIMENT_MATRIX["sparse_only"], k=5
    )
    comparison = compare(hybrid_result, sparse)
    assert "ci95" in comparison
    assert len(comparison["ci95"]) == 2
    assert isinstance(comparison["significant"], bool)


# --------------------------------------------------------------------------- #
# 报告渲染
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def sample_results(hybrid_result) -> list[ExperimentResult]:
    return [
        run_retrieval_experiment("sparse_only", EXPERIMENT_MATRIX["sparse_only"], k=5),
        hybrid_result,
    ]


def test_report_table_numbers_come_from_metrics(sample_results):
    """报告数字必须等于 metrics 的值 —— 防止手工誊写漂移。"""
    table = render_retrieval_table(sample_results, k=5)
    for result in sample_results:
        assert f"{result.metrics.loose_recall:.3f}" in table
        assert f"{result.metrics.strict_recall:.3f}" in table


def test_report_renders_comparisons_with_intervals(sample_results):
    text = render_comparisons(sample_results)
    assert "置信区间" in text
    assert "显著" in text


def test_report_marks_failed_thresholds_honestly():
    """不达标必须显示 ❌ —— 报告不能只报喜。"""
    weak = ExperimentResult(
        name="weak",
        config={},
        metrics=evaluate([{"gold": ["a"], "retrieved": ["x"]}], k=5),
    )
    report = render_report([weak], k=5, notes=[])
    assert "❌" in report


def test_report_marks_passed_thresholds(sample_results):
    report = render_report(sample_results, k=5, notes=[])
    assert "✅" in report


def test_report_includes_misses_section(sample_results):
    text = render_misses(sample_results[-1])
    assert "未完全命中" in text or "无未命中" in text


def test_report_states_auto_generation(sample_results):
    report = render_report(sample_results, k=5, notes=["测试用局限"])
    assert "自动渲染" in report or "自动生成" in report
    assert "测试用局限" in report


def test_default_notes_disclose_hash_embedding(sample_results):
    """诚实清单必须点明哈希嵌入不代表真实语义能力（ADR-0008）。"""
    notes = " ".join(_default_notes(sample_results))
    assert "哈希" in notes
    assert "语义" in notes
    assert "局限" in notes or "不代表" in notes


def test_default_notes_disclose_missing_llm(sample_results):
    notes = " ".join(_default_notes(sample_results))
    assert "LLM" in notes
