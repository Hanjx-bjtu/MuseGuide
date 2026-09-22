"""检索与生成指标（``MVP计划.md`` §7.1 / §7.2）。

**两级 Recall 口径（贯穿全项目）：**

```text
宽松 Recall@K：命中任一 gold 即算对     ← §7.1 的字面要求
严格 Recall@K：必须召回该题的全部 gold  ← 本项目补充
```

严格口径不是「加码」，而是让 §7.1 的评价**具备区分度**：
若只有宽松口径，多 gold 题目只要命中一条就算满分，不同检索策略会饱和到
一样的分数，结论既无法证明也无法证伪（v1 项目的实测教训）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class RetrievalMetrics:
    """一次检索评测的汇总指标。"""

    n_cases: int = 0
    loose_recall: float = 0.0
    strict_recall: float = 0.0
    mrr: float = 0.0
    ndcg: float = 0.0
    context_precision: float = 0.0
    per_case: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "n_cases": self.n_cases,
            "loose_recall@k": round(self.loose_recall, 4),
            "strict_recall@k": round(self.strict_recall, 4),
            "mrr": round(self.mrr, 4),
            "ndcg@k": round(self.ndcg, 4),
            "context_precision": round(self.context_precision, 4),
        }


def recall_at_k(retrieved: list[str], gold: list[str], k: int, *, strict: bool) -> float:
    """单题的 Recall@K。

    :param strict: ``False`` 为宽松口径（命中任一即 1.0）；
        ``True`` 为严格口径（命中比例，全部命中才为 1.0）
    """
    if not gold:
        return 0.0
    top = set(retrieved[:k])
    hits = len(top & set(gold))
    if not strict:
        return 1.0 if hits > 0 else 0.0
    return hits / len(gold)


def reciprocal_rank(retrieved: list[str], gold: list[str]) -> float:
    """首个 gold 的倒数排名（MRR 的单题分量）。"""
    gold_set = set(gold)
    for index, doc_id in enumerate(retrieved, start=1):
        if doc_id in gold_set:
            return 1.0 / index
    return 0.0


def ndcg_at_k(retrieved: list[str], gold: list[str], k: int) -> float:
    """二值相关性的 nDCG@K。

    ``DCG = Σ rel_i / log2(i+1)``，``IDCG`` 为理想排序（所有 gold 排最前）。
    """
    if not gold:
        return 0.0
    gold_set = set(gold)

    dcg = 0.0
    for index, doc_id in enumerate(retrieved[:k], start=1):
        if doc_id in gold_set:
            dcg += 1.0 / math.log2(index + 1)

    ideal_hits = min(len(gold_set), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def context_precision(retrieved: list[str], gold: list[str], k: int) -> float:
    """Context Precision：召回的 K 条中与 gold 相关的比例。"""
    top = retrieved[:k]
    if not top:
        return 0.0
    return len(set(top) & set(gold)) / len(top)


def evaluate(
    results: list[dict],
    *,
    k: int = 5,
) -> RetrievalMetrics:
    """对一批检索结果求平均指标。

    :param results: ``[{"question":..., "gold":[...], "retrieved":[...]}, ...]``
    """
    metrics = RetrievalMetrics(n_cases=len(results))
    if not results:
        return metrics

    for item in results:
        retrieved = item.get("retrieved") or []
        gold = item.get("gold") or []

        loose = recall_at_k(retrieved, gold, k, strict=False)
        strict = recall_at_k(retrieved, gold, k, strict=True)
        rr = reciprocal_rank(retrieved, gold)
        ndcg = ndcg_at_k(retrieved, gold, k)
        precision = context_precision(retrieved, gold, k)

        metrics.loose_recall += loose
        metrics.strict_recall += strict
        metrics.mrr += rr
        metrics.ndcg += ndcg
        metrics.context_precision += precision

        metrics.per_case.append(
            {
                "question": item.get("question", ""),
                "gold": gold,
                "retrieved": retrieved[:k],
                "loose": loose,
                "strict": strict,
                "rr": round(rr, 4),
                "ndcg": round(ndcg, 4),
                "missed": sorted(set(gold) - set(retrieved[:k])),
            }
        )

    n = len(results)
    metrics.loose_recall /= n
    metrics.strict_recall /= n
    metrics.mrr /= n
    metrics.ndcg /= n
    metrics.context_precision /= n
    return metrics


def has_discrimination(results: list[RetrievalMetrics]) -> bool:
    """多组配置之间是否存在可区分的差异。

    **为什么单独做这个判断：** 如果所有配置的宽松口径都是 1.000，
    说明评测集已饱和，其上跑出的任何对比都**不足以支撑结论**。
    这是 v1 项目最关键的教训，因此把它固化成一条可检查的性质。
    """
    if len(results) < 2:
        return False
    loose = {round(m.loose_recall, 3) for m in results}
    strict = {round(m.strict_recall, 3) for m in results}
    return len(loose) > 1 or len(strict) > 1


def paired_bootstrap(
    a: list[float],
    b: list[float],
    *,
    iterations: int = 2000,
    seed: int = 20240101,
) -> tuple[float, float, float]:
    """配对 bootstrap 置信区间（用于「A 是否真的优于 B」）。

    :return: ``(mean_diff, ci_low, ci_high)``；区间不含 0 即认为差异显著。
    """
    import random

    if len(a) != len(b) or not a:
        return 0.0, 0.0, 0.0

    diffs = [x - y for x, y in zip(a, b)]
    mean_diff = sum(diffs) / len(diffs)

    rng = random.Random(seed)
    n = len(diffs)
    samples = []
    for _ in range(iterations):
        resample = [diffs[rng.randrange(n)] for _ in range(n)]
        samples.append(sum(resample) / n)
    samples.sort()

    low = samples[int(0.025 * iterations)]
    high = samples[int(0.975 * iterations) - 1]
    return round(mean_diff, 4), round(low, 4), round(high, 4)
