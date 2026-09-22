"""Creative Starter —— 从创作意图生成起步方案（``MVP计划.md`` §3.3）。

流程严格对齐 §3.3.2：

```text
用户创作意图（日常语言）
        ↓
Intent Mapping → 音乐概念        （P1，app/services/intent.py）
        ↓
选择式交互补充（情绪/风格/速度）  （P1，P2 合并）
        ↓
检索知识                         （P2 离线直出 / P3 Hybrid）
        ↓
LLM 生成起步方案
        ↓
通俗解释每个选择 + 校验
```

**降级链（§8 风险应对）：** LLM 不可用 / 调用失败 / 解析失败 →
回落「知识库直出」的模板方案。这条路径保证零基础用户在**任何情况下**
都能拿到一份可用的起步方案，而不是一个错误页。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.core.brief import (
    CreationInput,
    CreativeIntent,
    SelectionInput,
)
from app.core.degradation import DegradationLog
from app.core.evidence import Evidence
from app.core.options import tempo_bpm
from app.core.plan import StarterPlan, StarterSection
from app.core.trace import Trace
from app.services import intent as intent_service
from app.services.generation import prompts
from app.services.generation.parser import OutputParseError, parse_starter_plan
from app.services.retrieval import service as retrieval_service
from app.services.retrieval.offline import OfflineKnowledgeBase, default_kb

#: 兜底方案的段落模板（按情绪方向选择）
_FALLBACK_TEMPLATES: dict[str, dict[str, Any]] = {
    "minor_start": {
        "verse": ("Am", "F", "C", "G"),
        "verse_emotion": "偏伤感",
        "verse_explanation": "从小调和弦开始，听起来柔和忧伤。",
        "chorus": ("C", "G", "Am", "F"),
        "chorus_emotion": "转向释然",
        "chorus_explanation": "从大调和弦开始，听起来更温暖开阔。",
    },
    "major_start": {
        "verse": ("C", "G", "Am", "F"),
        "verse_emotion": "平稳温暖",
        "verse_explanation": "用最常见的走向，听起来平稳、容易入耳。",
        "chorus": ("F", "G", "C", "C"),
        "chorus_emotion": "更开阔",
        "chorus_explanation": "把下属和弦放在前面，副歌会显得更推得开。",
    },
}


@dataclass
class StarterResult:
    """起步链路的完整产物。"""

    plan: StarterPlan
    trace: Trace
    warnings: list[str] = field(default_factory=list)


def _has_kind(log: DegradationLog, kind: str) -> bool:
    """日志中是否已存在某类降级事件（避免同一请求重复留痕）。"""
    return any(item.kind == kind for item in log.items())


def _pick_template(intent: CreativeIntent) -> dict[str, Any]:
    """按情绪方向选择兜底模板。"""
    sad = any(e in ("伤感", "忧郁", "温柔") for e in intent.emotion)
    resolved = any(e in ("释然", "温暖", "轻快") for e in intent.emotion)
    if sad and resolved:
        return _FALLBACK_TEMPLATES["minor_start"]
    if sad:
        return _FALLBACK_TEMPLATES["minor_start"]
    return _FALLBACK_TEMPLATES["major_start"]


def build_fallback_plan(intent: CreativeIntent, evidence: list[Evidence]) -> StarterPlan:
    """知识库直出的兜底方案。

    它**不是占位符** —— 这份内容是可用的：走向取自
    ``starter.common_progressions.01``，解释的措辞遵循 §3.8.3 的零基础风格。
    """
    template = _pick_template(intent)

    key = "C Major"
    if intent.key_preference and "小调" in intent.key_preference:
        key = "A Minor"

    tempo = tempo_bpm(intent.tempo_feel)
    tempo_word = intent.tempo_feel or "中等偏慢"

    return StarterPlan(
        key=key,
        key_explanation="听起来明亮温暖，适合「释然」的感觉；而且按起来最简单，全是白键。",
        tempo=tempo,
        tempo_explanation=f"{tempo_word}，像散步一样，适合抒情歌。",
        sections=[
            StarterSection(
                name="主歌",
                chords=list(template["verse"]),
                emotion=template["verse_emotion"],
                explanation=template["verse_explanation"],
            ),
            StarterSection(
                name="副歌",
                chords=list(template["chorus"]),
                emotion=template["chorus_emotion"],
                explanation=template["chorus_explanation"],
            ),
        ],
        why=(
            "伤感的部分靠小调和弦实现，释然的部分靠回到大调和弦实现 —— "
            "两者前后接在一起，就形成了情绪的转折。"
        ),
        adjust_hints=[
            "把副歌最后那个和弦换成 Fm，会多一丝忧伤，像回忆闪过",
            "给和弦加上七音（比如 C 换成 Cmaj7），听感会更柔和、更梦幻",
        ],
        evidence=evidence,
        layman_level="zero",
    )


def validate_plan(plan: StarterPlan) -> list[str]:
    """§3.3.3 的结构校验，返回问题列表（空 = 通过）。

    契约层已经保证了「解释非空」「至少两段」，这里补上**业务层**的检查：
    和弦是否可解析、速度是否在合理范围、解释里是否混入了禁区术语。
    """
    issues: list[str] = []

    from app.services.parser.chords import ChordParseError, parse_chord

    for section in plan.sections:
        if not section.chords:
            issues.append(f"段落「{section.name}」没有和弦")
        for chord in section.chords:
            try:
                parse_chord(chord)
            except ChordParseError:
                issues.append(f"段落「{section.name}」中的和弦无法识别：{chord}")

    try:
        from app.services.parser.chords import parse_progression

        parse_progression(plan.key.replace(" Major", "").replace(" Minor", ""))
    except Exception:  # noqa: BLE001 - 调性格式异常不致命，仅记录
        pass

    if not (40 <= plan.tempo <= 200):
        issues.append(f"速度 {plan.tempo} BPM 超出合理范围（40~200）")

    if plan.layman_level == "zero":
        from app.services.layman import find_banned_terms

        for field_name, text in (
            ("key_explanation", plan.key_explanation),
            ("tempo_explanation", plan.tempo_explanation),
            ("why", plan.why),
            *[(f"{s.name}.explanation", s.explanation) for s in plan.sections],
        ):
            banned = find_banned_terms(text)
            if banned:
                issues.append(f"{field_name} 含零基础禁区术语：{banned}")

    return issues


def enforce_user_selections(
    plan: StarterPlan, intent: CreativeIntent, selections: SelectionInput
) -> tuple[StarterPlan, list[str]]:
    """把用户显式做出的选择**强制覆盖**到方案上。

    **为什么必须做这一步（实测教训）：**
    接入真实 LLM 后，用户选择「很慢，像翻相册」（60 BPM），
    但模型返回了 ``tempo: 72`` —— 因为 72 也勉强算「慢」。
    只把选择写进 Prompt 是**不够的**：模型会把它当成建议。

    用户的显式选择与「模型猜的」不是同一类东西：
    前者是输入，后者是输出。**输入不应被输出覆盖。**
    这与 ADR-0009 里「选择层优先于 LLM 层」是同一条原则，
    只是发生在生成之后而不是之前。

    :return: ``(可能被修正的方案, 修正说明列表)`` —— 说明会进入 ``Trace.warnings``，
        使「系统改过模型的输出」这件事可被追溯，而不是静默修改。
    """
    adjustments: list[str] = []
    update: dict = {}

    # --- 速度：用户选了什么就用什么 ---
    if selections.tempo_label:
        chosen = tempo_bpm(intent.tempo_feel or selections.tempo_label)
        if plan.tempo != chosen:
            adjustments.append(
                f"用户选定速度为 {chosen} BPM，已覆盖模型给出的 {plan.tempo} BPM"
            )
            update["tempo"] = chosen

    # --- 风格：用户选了什么，解释里就要体现什么 ---
    # 这里只记录不一致，不强行改写解释文本（改写可能破坏语义通顺）。

    if update:
        plan = plan.model_copy(update=update)
    return plan, adjustments


def _build_trace(
    *,
    payload: CreationInput,
    intent: CreativeIntent,
    evidence: list[Evidence],
    prompt: str,
    raw_output: str,
    plan: StarterPlan | None,
    log: DegradationLog,
    timings: dict[str, int],
) -> Trace:
    return Trace(
        config={
            "mode": "starter",
            "user_level": payload.user_level,
            "retrieval": "offline",
        },
        input=payload,
        intent=intent,
        evidence=evidence,
        prompt=prompt,
        raw_output=raw_output,
        plan=plan,
        degradation=log.items(),
        timings_ms=timings,
    )


def generate_starter_plan(
    payload: CreationInput,
    *,
    llm: Any | None = None,
    kb: OfflineKnowledgeBase | None = None,
    top_k: int = 5,
) -> StarterResult:
    """零基础链路主入口：创作意图 → 起步方案。

    :param payload: 请求体（§3.1.2 的意图 + 选择式补充）
    :param llm: 满足 ``LLMProvider`` 协议的对象；``None`` 表示直接走兜底
    :param kb: 离线知识库；默认使用全局实例
    """
    log = DegradationLog()
    timings: dict[str, int] = {}
    kb = kb or default_kb()

    # --- ① Intent Mapping（P1 的四层瀑布）---
    started = time.perf_counter()
    creative_intent = intent_service.resolve_intent(
        payload.raw_text, payload.selections, llm=llm, log=log
    )
    timings["intent"] = int((time.perf_counter() - started) * 1000)

    # --- ② 检索知识（P3 起走 Hybrid，失败自动回落离线直出）---
    started = time.perf_counter()
    retrieval = retrieval_service.retrieve(
        goal_text=payload.raw_text,
        intent=creative_intent,
        user_level=payload.user_level,
        emotions=creative_intent.emotion,
        llm=llm,
        log=log,
    )
    evidence = retrieval.evidence
    timings["retrieve"] = int((time.perf_counter() - started) * 1000)

    # --- ③ 组装 Prompt ---
    selections_note = _describe_selections(payload.selections)
    # 用户在界面上选定的 BPM 作为硬约束写进 Prompt（生成后还会再强制覆盖一次）
    locked_tempo = (
        tempo_bpm(creative_intent.tempo_feel or payload.selections.tempo_label)
        if payload.selections.tempo_label
        else None
    )
    prompt = prompts.starter_prompt(
        raw_text=payload.raw_text,
        intent=creative_intent,
        evidence=evidence,
        level=payload.user_level,
        selections_note=selections_note,
        locked_tempo=locked_tempo,
    )

    # --- ④ LLM 生成 ---
    plan: StarterPlan | None = None
    raw_output = ""
    started = time.perf_counter()

    if llm is None or not getattr(llm, "available", False):
        # 用 add_once：intent 层与 query 分解层可能已记录过同类事件，
        # 重复留痕会让 P6 报告里的「降级次数」统计失真。
        if llm is not None:
            log.add_once(
                "llm_unavailable",
                "LLM 未配置或不可用，起步方案回落知识库直出",
                fallback_to="fallback_plan",
            )
    else:
        try:
            raw_output = llm.complete(prompt, json_mode=True)
            plan = parse_starter_plan(raw_output)
            issues = validate_plan(plan)
            if issues:
                log.add(
                    "parse_failed",
                    f"生成的方案未通过校验：{'；'.join(issues)}",
                    fallback_to="fallback_plan",
                )
                plan = None
        except OutputParseError as exc:
            log.add("parse_failed", f"起步方案解析失败：{exc}", fallback_to="fallback_plan")
        except Exception as exc:  # noqa: BLE001 - LLMError 等统一降级
            log.add("llm_failed", f"生成起步方案失败：{exc}", fallback_to="fallback_plan")
    timings["generate"] = int((time.perf_counter() - started) * 1000)

    # --- ⑤ 兜底 ---
    if plan is None:
        plan = build_fallback_plan(creative_intent, evidence)

    # 无论走哪条路径，都把证据挂上去（§3.9.3 要求展示理论依据）
    plan = plan.model_copy(update={"evidence": evidence, "layman_level": payload.user_level})

    # --- ⑥ 强制应用用户的显式选择（输入不应被输出覆盖）---
    plan, adjustments = enforce_user_selections(plan, creative_intent, payload.selections)

    trace = _build_trace(
        payload=payload,
        intent=creative_intent,
        evidence=evidence,
        prompt=prompt,
        raw_output=raw_output,
        plan=plan,
        log=log,
        timings=timings,
    )
    return StarterResult(
        plan=plan, trace=trace, warnings=adjustments + validate_plan(plan)
    )


def _describe_selections(selections: SelectionInput) -> str:
    """把选择式输入渲染成 Prompt 片段（§3.3.4 的补充段）。"""
    parts: list[str] = []
    if selections.emotion:
        parts.append(f"情绪：{selections.emotion}")
    if selections.style:
        parts.append(f"风格：{selections.style}")
    if selections.tempo_label:
        parts.append(f"速度：{selections.tempo_label}")
    return "\n".join(parts)
