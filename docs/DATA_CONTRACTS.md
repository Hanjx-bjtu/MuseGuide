# 数据契约表（DATA_CONTRACTS）

> **阶段：** P0 交付物
> **用途：** 冻结上层模块之间的数据形状。任何字段变更都必须改本文件 + 补 ADR + 补往返测试。
> **依据：** `MVP计划.md` §3.3.3 / §3.4.1 / §3.4.2 / §3.5.2 / §3.6.2 / §3.9.3

---

## 1. 契约清单与来源对照

| 契约 | 模块 | 来源条款 | 说明 |
|---|---|---|---|
| `MusicArtifact` | `app/core/artifact.py` | §3.4.1 §3.4.2 §3.11 | 统一音乐素材表示，两条链路的公共出口 |
| `ChordSymbol` / `MelodyInfo` | 同上 | §3.4.1 §3.4.2 | 和弦与旋律的子结构 |
| `CreativeIntent` | `app/core/brief.py` | §3.2.3 | Intent Mapping 的 5 字段输出 |
| `CreativeGoal` | 同上 | §3.1.1 P1 | 进阶链路的创作目标 |
| `CreationInput` | 同上 | §3.1.2 §3.1.3 | 两条链路的统一请求体 |
| `Query` / `DecomposedQuery` | `app/core/evidence.py` | §3.5.2 §3.5.3 | Query 分解产物 |
| `Candidate` | 同上 | §3.7.3 | 召回中间态（含逐级分数） |
| `Evidence` | 同上 | §3.6.2 §3.9.3 | 最终证据，带来源 |
| `StarterPlan` | `app/core/plan.py` | §3.3.3 | 起步方案 |
| `AnalysisResult` | 同上 | §3.4.1 §3.4.2 | 分析与通俗解读 |
| `Advice` / `AdviceOption` | 同上 | §3.9.3 | 修改建议 |
| `GroundingReport` | `app/core/evidence.py` | §3.9.3 §7.2 | 建议的可信性校验结果 |
| `Trace` | `app/core/trace.py` | 本文新增 | 一次请求的完整可回放记录 |
| `SelectionInput` | `app/core/options.py` | §3.10.2 §3.10.3 | 选择式输入 |
| `Degradation` | `app/core/degradation.py` | §8 风险应对 | 降级留痕 |

---

## 2. `MusicArtifact`

对齐 `设计草案.md` §11 的结构化表示，并裁剪到 `MVP计划.md` 允许的范围（**不含 rhythm 分析、不含 motifs**——§1.3 排除复杂乐谱分析）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `source_format` | `"chords_text" \| "melody_text" \| "meta_text" \| "midi" \| "none"` | ✔ | 素材来源，决定置信度默认值 |
| `key` | `str \| None` | | 如 `"C Major"` |
| `key_confidence` | `float` (0~1) | ✔ | 默认 1.0；推断路径 < 1.0 |
| `tempo` | `int \| None` | | BPM |
| `meter` | `str \| None` | | 如 `"4/4"` |
| `chords` | `list[ChordSymbol]` | ✔ | 可为空列表 |
| `melody` | `MelodyInfo \| None` | | |
| `sections` | `list[Section]` | ✔ | 可为空列表 |
| `confidence` | `dict[str, float]` | ✔ | 逐字段置信度（音频/MIDI 路径必填） |

### `ChordSymbol`

| 字段 | 类型 | 说明 |
|---|---|---|
| `raw` | `str` | 用户原始写法，如 `"G/B"` |
| `root` | `str` | 根音，如 `"G"` |
| `quality` | `str` | `major` / `minor` / `dom7` / `maj7` / `min7` / `dim` / `sus4` / `add9` / `other` |
| `bass` | `str \| None` | 转位低音，如 `"B"` |
| `roman` | `str \| None` | 如 `"V6"`，需 `key` 才能计算 |
| `function` | `str \| None` | `Tonic` / `Dominant` / `Subdominant` / `Other` |

### `MelodyInfo`

| 字段 | 类型 | 说明 | 来源 |
|---|---|---|---|
| `notes` | `list[str]` | `["E4","G4","A4","G4","E4"]` | §3.4.2 |
| `range` | `list[str]` | `["E4","A4"]` | §3.4.2 |
| `contour` | `str` | `"up-down"` 等 | §3.4.2 |
| `repetition` | `bool` | 是否有重复片段 | §3.4.2 |

---

## 3. `CreativeIntent`（Intent Mapping 输出）

严格对齐 `MVP计划.md` §3.2.3 的 prompt 输出字段，**一个不多一个不少**：

| 字段 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `emotion` | `list[str]` | 情绪方向（受控词表内） | `["伤感", "释然"]` |
| `style` | `str \| None` | 风格倾向 | `"民谣"` |
| `tempo_feel` | `str \| None` | 速度感 | `"中等偏慢"` |
| `key_preference` | `str \| None` | 调性倾向 | `"大调（释然感）"` |
| `harmony_needs` | `list[str]` | 和声需求 | `["需要情绪转折"]` |
| `raw_text` | `str` | 原始输入（**新增，用于溯源**） | |
| `matched_rules` | `list[str]` | 命中的 §3.2.2 规则（**新增，用于可解释性**） | `["有点伤感但最后释然"]` |
| `source` | `"rule" \| "llm" \| "selection" \| "default"` | 由哪一层产出（**新增**） | `"rule"` |

---

## 4. 证据链：`Query` → `Candidate` → `Evidence`

> **设计要点：这条链把「可解释性」变成数据结构。** 每一次请求都能回答「这条建议依据的是哪条知识、它由哪条 query 召回、在第几级排名」。

| 契约 | 字段 | 说明 |
|---|---|---|
| `DecomposedQuery` | `intent` / `goal` / `object` / `queries` | 严格对齐 §3.5.2 与 §3.5.3 |
| `Candidate` | `entry_id` / `score` / `source`(`dense`\|`sparse`\|`both`) / `rank_dense` / `rank_sparse` / `rank_final` | 逐级排名可展示 |
| `Evidence` | `entry_id` / `title` / `layman_title` / `content` / `layman_content` / `source`(title,url,license) / `score` / `from_query` | 最终证据；`layman_*` 是零基础模式的渲染首选 |
| `GroundingReport` | `ok` / `issues: list[GroundingIssue]` | `GroundingIssue(kind, detail, severity)` |

**`GroundingIssue.kind` 枚举（P4.6）：**

```text
out_of_key              # 建议的和弦不在声称调性下成立
citation_out_of_range   # [来源N] 的 N 超出 evidence 长度
weak_grounding          # 引用内容与结论关键词重叠度过低
goal_mismatch           # 建议未回应用户目标
missing_layman          # 零基础模式下缺少通俗解释
```

---

## 5. `Trace`（本文新增）

> **为什么要有它：** `MVP计划.md` §3.9.3 要求「固定输出格式，保证可解释性与可评估性」。`Trace` 是这句话的工程实现——**评测、报告、UI 的依据展示，三者消费同一个对象。**

| 字段 | 类型 | 说明 |
|---|---|---|
| `trace_id` | `str` | 唯一 id，可回放 |
| `timestamp` | `str` | ISO8601 |
| `config` | `dict` | 本次用的配置（模型、top_k、检索策略） |
| `input` | `dict` | 原始输入 + 解析后的 artifact + goal |
| `intent` | `CreativeIntent \| None` | P1 输出 |
| `analysis` | `AnalysisResult \| None` | P2 输出 |
| `queries` | `list[DecomposedQuery]` | P3 输出 |
| `candidates` | `list[Candidate]` | P3 中间态 |
| `evidence` | `list[Evidence]` | P3 最终证据 |
| `prompt` | `str` | 实际送给 LLM 的完整 prompt |
| `raw_output` | `str` | LLM 原始输出（未解析） |
| `plan` / `advice` | `StarterPlan \| Advice \| None` | P2/P4 产物 |
| `grounding` | `GroundingReport` | P4 校验结果 |
| `degradation` | `list[Degradation]` | 降级留痕 |
| `timings_ms` | `dict[str, int]` | 各阶段耗时 |
| `usage` | `dict` | token 用量 |

**存档约定：** 每次实验写入 `experiments/{experiment_name}/{date}.jsonl`，每行一个 `Trace`。

---

## 6. 受控词表（`app/core/options.py`）

对齐 `MVP计划.md` §3.10.2 与 §3.10.3——**每个选项都必须带通俗说明**，因为界面直接渲染它。

```python
TEMPO_OPTIONS = [
    ("很慢，像翻相册",   "很慢",    60),
    ("中等偏慢，像散步", "中等偏慢", 82),
    ("中等，像走路",     "中等",    100),
]

EMOTION_OPTIONS = ["温柔", "伤感", "释然", "激昂", "忧郁", "轻快", "梦幻", "温暖"]
STYLE_OPTIONS   = ["流行", "民谣", "轻摇滚", "钢琴曲", "梦幻电子", "抒情"]
```

**规则：** 新增选项必须同时提供「用户能读懂的说法」与「音乐概念名」，否则校验失败。

---

## 7. 变更流程

1. 修改本文件对应行
2. 在 `docs/DECISIONS.md` 追加一条 ADR（含原因与影响面）
3. 更新 `app/core/` 中的模型
4. 补充/更新 `tests/test_contracts.py` 的往返用例
5. 跑 `pytest` —— 失败即说明有调用方未同步

> **契约层零业务依赖铁律：** `app/core/` 不得 import `services` / `providers` / `fastapi` / `streamlit`。该规则由 `tests/test_architecture.py` 静态强制。
