"""Prompt 组装（``MVP计划.md`` §3.3.4 / §3.9.2）。

Prompt 结构严格对齐设计文档给出的四段式：
``[用户创作意图] [Intent Mapping 结果] [检索到的知识] [任务]``。
**不自由发挥段落名**，因为 §3.9.3 的展示模板与 §7.3 的评测口径都依赖它。
"""

from __future__ import annotations

from app.core.brief import CreativeIntent, UserLevel
from app.core.evidence import Evidence
from app.core.plan import AnalysisResult
from app.services.layman import prompt_style_block

#: §3.9.3 起步方案的固定输出结构要求
STARTER_OUTPUT_SCHEMA = """{
  "key": "调性，如 \\"C Major\\"",
  "key_explanation": "为什么选这个调，用一句通俗的话",
  "tempo": 82,
  "tempo_explanation": "为什么是这个速度，用一句通俗的话",
  "sections": [
    {
      "name": "主歌",
      "chords": ["Am", "F", "C", "G"],
      "emotion": "偏伤感",
      "explanation": "这组和弦听起来会怎样，用通俗的话说"
    },
    {
      "name": "副歌",
      "chords": ["C", "G", "Am", "F"],
      "emotion": "转向释然",
      "explanation": "同上"
    }
  ],
  "why": "整体上为什么这样安排，尤其是情绪是怎么变化的",
  "adjust_hints": ["如果想调整，可以试试的方向1", "方向2"]
}"""


def render_intent(intent: CreativeIntent) -> str:
    """渲染 Intent Mapping 结果（§3.3.4 的第二段）。"""
    lines = [
        f"- emotion: {'、'.join(intent.emotion) if intent.emotion else '（未确定）'}",
        f"- style: {intent.style or '（未确定）'}",
        f"- tempo_feel: {intent.tempo_feel or '（未确定）'}",
        f"- key_preference: {intent.key_preference or '（未确定）'}",
        f"- harmony_needs: {'、'.join(intent.harmony_needs) if intent.harmony_needs else '（未确定）'}",
    ]
    return "\n".join(lines)


def render_evidence(evidence: list[Evidence], level: UserLevel = "zero") -> str:
    """渲染检索到的知识（§3.3.4 的第三段）。

    零基础模式下优先使用 ``layman_content`` —— 这是 Layman-aware 的关键：
    **送给模型的证据本身就是通俗的**，模型才更可能输出通俗的解释。
    """
    if not evidence:
        return "（本次未检索到相关知识，请基于通用乐理常识作答，不要编造来源）"

    blocks: list[str] = []
    for idx, item in enumerate(evidence, start=1):
        title = item.layman_title if (level == "zero" and item.layman_title) else item.title
        body = item.layman_content if (level == "zero" and item.layman_content) else item.content
        source = f"{item.source.title}（{item.source.license}）" if item.source.title else "未标注来源"
        blocks.append(f"{idx}. [{item.entry_id}] {title}\n{body}\n（来源：{source}）")
    return "\n\n".join(blocks)


def render_artifact(analysis: AnalysisResult | None) -> str:
    """渲染用户作品（§3.9.2 的第一段，供进阶链路使用）。"""
    if analysis is None or not analysis.raw:
        return "（用户尚未提供音乐素材）"

    lines = [f"Key: {analysis.key or '未确定'}"]
    if analysis.roman:
        lines.append(f"Progression: {' → '.join(analysis.roman)}")
    if analysis.raw:
        lines.append(f"Chords: {' | '.join(analysis.raw)}")
    if analysis.melody:
        notes = analysis.melody.get("notes") or []
        if notes:
            lines.append(f"Melody: {' '.join(notes)}")
    if analysis.notes:
        lines.extend(f"注：{n}" for n in analysis.notes)
    return "\n".join(lines)


def starter_prompt(
    *,
    raw_text: str,
    intent: CreativeIntent,
    evidence: list[Evidence],
    level: UserLevel = "zero",
    selections_note: str = "",
) -> str:
    """组装起步方案 Prompt —— 严格对齐 §3.3.4 的四段结构。"""
    selection_block = f"\n[用户的选择式补充]\n{selections_note}\n" if selections_note else ""

    return f"""{prompt_style_block(level)}

[用户创作意图]
{raw_text or '（用户未提供文字描述，请依据下面的意图映射结果）'}
{selection_block}
[Intent Mapping 结果]
{render_intent(intent)}

[检索到的知识]
{render_evidence(evidence, level)}

[任务]
1. 建议一个合适的调性，并解释为什么（用通俗的话）
2. 为主歌和副歌各设计一组基础和弦（每组四个）
3. 用通俗语言解释每个选择的原因
4. 说明主歌和副歌之间的情绪变化是如何实现的

[输出格式]
只输出如下结构的 JSON，不要输出任何解释性文字或 markdown 围栏：
{STARTER_OUTPUT_SCHEMA}"""


def tutor_prompt(
    *,
    goal_text: str,
    analysis: AnalysisResult,
    evidence: list[Evidence],
    level: UserLevel = "some",
) -> str:
    """组装修改建议 Prompt —— 严格对齐 §3.9.2 的四段结构。"""
    return f"""{prompt_style_block(level)}

[用户作品]
{render_artifact(analysis)}

[用户目标]
{goal_text or '（用户未明确说明目标）'}

[检索到的知识]
{render_evidence(evidence, level)}

[任务]
1. 分析当前作品特征
2. 指出可能的问题
3. 给出 2-3 个修改方向
4. 每个方向说明理论依据
5. 引用检索到的知识（用 [编号] 标注来源）

[输出格式]
只输出如下结构的 JSON，不要输出任何解释性文字或 markdown 围栏：
{{
  "analysis": "对当前作品的分析",
  "problems": ["问题1", "问题2"],
  "options": [
    {{
      "label": "建议 A：简短标题",
      "chords": ["Cmaj7", "G/B", "Am7", "Fmaj7"],
      "feature": "这个方向的特点（面向进阶用户）",
      "reason": "通俗解释：听起来会怎样、为什么",
      "theory": ["理论依据要点"]
    }}
  ]
}}"""
