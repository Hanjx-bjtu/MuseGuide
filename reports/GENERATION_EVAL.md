# MuseGuide 生成质量评测

> 由 `knowledge/eval/run_generation_eval.py` 自动生成，无手工誊写。
> 生成时间：2026-09-22 14:03 UTC
> **本次评测使用真实 LLM**，非 Mock 或知识库降级路径。

## 总体结果

- 用例数：**16**
- 通过：**16**（100.0%）
- 端到端延迟：中位数 **6.3s**、最大 **8.1s**、最小 **3.7s**
- 延迟预算：60s（`MVP计划.md` §9 第 8 条） → ✅ 全部达标

## 逐题结果

| 用例 | 类型 | 结果 | 延迟 | 失败项 |
|---|---|---|---|---|
| `gen.starter.001` | starter | ✅ | 6.2s | — |
| `gen.starter.002` | starter | ✅ | 5.2s | — |
| `gen.starter.003` | starter | ✅ | 4.5s | — |
| `gen.starter.004` | starter | ✅ | 4.2s | — |
| `gen.starter.005` | starter | ✅ | 5.1s | — |
| `gen.starter.006` | starter | ✅ | 3.7s | — |
| `gen.starter.007` | starter | ✅ | 3.9s | — |
| `gen.starter.008` | starter | ✅ | 4.5s | — |
| `gen.advice.001` | advice | ✅ | 7.8s | — |
| `gen.advice.002` | advice | ✅ | 8.1s | — |
| `gen.advice.003` | advice | ✅ | 7.2s | — |
| `gen.advice.004` | advice | ✅ | 6.7s | — |
| `gen.advice.005` | advice | ✅ | 7.8s | — |
| `gen.advice.006` | advice | ✅ | 7.2s | — |
| `gen.advice.007` | advice | ✅ | 8.0s | — |
| `gen.advice.008` | advice | ✅ | 6.4s | — |

## 降级情况

- 4 个用例触发了降级：
  - `gen.starter.007`：intent_fallback
  - `gen.advice.003`：intent_fallback
  - `gen.advice.006`：intent_fallback
  - `gen.advice.008`：intent_fallback

## 样例输出

- `gen.starter.001`：C Major | 82BPM | 主歌: Am→F→C→G / 副歌: C→G→Am→F
- `gen.starter.002`：C Major | 82BPM | 主歌: C→Am→F→G / 副歌: F→G→C→Am
- `gen.starter.003`：C Major | 60BPM | 主歌: Am→F→C→G / 副歌: C→G→Am→F
- `gen.starter.004`：C Major | 100BPM | 主歌: C→G→Am→F / 副歌: F→G→C→Am
- `gen.starter.005`：C Major | 82BPM | 主歌: C→Am→F→G / 副歌: F→G→C→Am
- `gen.starter.006`：A Minor | 82BPM | 主歌: Am→F→C→G / 副歌: C→G→Am→F

## 局限

- 本评测只覆盖**可机器判定的维度**（结构合规、接地性、术语通俗度、数值约束、延迟）。
- `MVP计划.md` §7.2 的 Theory Validity / Usefulness / Comprehensibility / Actionability 等维度**仍需要人工评分**，本次未做。
- 用例数有限，不能替代真实用户测试。

---

*本报告由 `knowledge/eval/run_generation_eval.py` 自动生成。*