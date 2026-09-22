"""实验与评测层（P6 的核心）。

`Config × Dataset → Trace[] → Metrics → Report`，全程自动化。

**为什么单独成层：** 报告里的每个数字都必须由代码从实验产物渲染出来，
不能手工誊写 —— 手工誊写的表格会与数据漂移，改一次参数就要重抄一遍。
"""

from app.experiment.metrics import (
    RetrievalMetrics,
    context_precision,
    evaluate,
    has_discrimination,
    ndcg_at_k,
    paired_bootstrap,
    recall_at_k,
    reciprocal_rank,
)
from app.experiment.runner import (
    ExperimentResult,
    compare,
    load_retrieval_dataset,
    run_matrix,
    run_retrieval_experiment,
    save_results,
)

__all__ = [
    "RetrievalMetrics",
    "context_precision",
    "evaluate",
    "has_discrimination",
    "ndcg_at_k",
    "paired_bootstrap",
    "recall_at_k",
    "reciprocal_rank",
    "ExperimentResult",
    "compare",
    "load_retrieval_dataset",
    "run_matrix",
    "run_retrieval_experiment",
    "save_results",
]
