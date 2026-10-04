"""Creative Tutor —— 已有雏形 → 分析 → 修改建议（``MVP计划.md`` §3.9）。

流程对齐 §5.2 的进阶数据流：

```text
用户输入（创作目标 + 和弦进行 + 旋律）
        ↓
音乐解析（调性识别 / 和弦级数化 / 旋律特征）
        ↓
检索知识
        ↓
LLM 生成（分析 / 问题 / 建议 / 理论依据）
        ↓
Layman Adaptation（按用户水平调整通俗度）
        ↓
Grounding 校验（把幻觉变成可统计的指标）
```

**与 Creative Starter 的关键差别：** 进阶链路有「用户作品」这个 artifact，
因此它多了一道 **Grounding 校验** —— 建议必须能和用户的实际作品对上，
而不是一篇泛泛的乐理科普。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.core.brief import CreationInput, CreativeGoal
from app.core.degradation import DegradationLog
from app.core.evidence import Evidence
from app.core.plan import Advice, AdviceOption, AnalysisResult
from app.core.trace import Trace
from app.services import intent as intent_service
from app.services.analyzer import analyze, build_artifact
from app.services.generation import prompts
from app.services.generation.grounding import verify_advice
from app.services.generation.parser import OutputParseError, parse_advice
from app.services.layman import adapt_advice
from app.services.retrieval import service as retrieval_service
from app.services.retrieval.offline import OfflineKnowledgeBase, default_kb

#: 方案之间和弦重叠率超过此值即认为缺乏多样性（§7.2 的 Diversity 维度）
DIVERSITY_OVERLAP_LIMIT = 0.75


@dataclass
class AdviceResult:
    """进阶链路的完整产物。"""

    advice: Advice
    analysis: AnalysisResult
    trace: Trace
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 兜底方案（知识库直出）
# --------------------------------------------------------------------------- #


def _fallback_options(analysis: AnalysisResult, evidence: list[Evidence]) -> list[AdviceOption]:
    """按检测到的和声特征生成兜底方案。

    这是「知识库直出」路径：内容取自
    ``examples.pop_progressions.01`` / ``harmony.chord_color.01`` 等条目，
    按**改动成本从低到高**排序 —— 见 ``harmony.chord_color.01`` 的排序原则。
    """
    chords = list(analysis.raw) or ["C", "G", "Am", "F"]

    # 方案 A：加七音 / 换低音（改动最小）
    option_a = AdviceOption(
        label="建议 A：给和弦加一点色彩",
        chords=_reharmonize_with_sevenths(chords, analysis.key),
        feature="保持原有功能关系，通过七和弦与低音线增加色彩，改动成本最低。",
        reason="听起来会更柔和、更梦幻，但原来那种稳定的感觉不会变。",
        theory=["七和弦扩展色彩", "低音级进增加连贯性"],
    )

    # 方案 B：借用和弦（引入阴影感）
    option_b = AdviceOption(
        label="建议 B：借一个和弦，加一层阴影",
        chords=_reharmonize_with_borrowed(chords, analysis.key),
        feature="从平行小调借一个和弦，引入阴影感但不破坏整体框架。",
        reason="最后一下会突然多一丝忧伤，像回忆闪过，但整体还是暖的。",
        theory=["调式借用（Modal Interchange）"],
    )

    # 方案 C：低音线（第三种手法，保证多样性）
    option_c = AdviceOption(
        label="建议 C：让低音一步步走下来",
        chords=_reharmonize_with_bass(chords, analysis.key),
        feature="低音级进下行，增强连贯性与设计感，和声实质不变。",
        reason="听起来更连贯，像一条线把几个和弦串起来。",
        theory=["低音线进行", "转位"],
    )

    return [option_a, option_b, option_c]


def _extract_roots(chords: list[str]) -> list[str]:
    from app.services.parser.chords import ChordParseError, parse_chord

    roots: list[str] = []
    for name in chords:
        try:
            roots.append(parse_chord(name).root)
        except ChordParseError:
            roots.append(name)
    return roots


def _reharmonize_with_sevenths(chords: list[str], key: str | None) -> list[str]:
    """把三和弦换成七和弦（保持根音与功能不变）。

    ⚠️ **不能无脑把大三和弦都变成 maj7。**
    实测教训：``G`` 在 C 大调是 V 级属和弦，换成 ``Gmaj7`` 会引入调外的
    F#（G 的七音应该是 F），Grounding 校验会正确地把它判为调外和弦。
    正确做法是按**级数**决定七音的性质：

    * I、IV 级大三和弦 → maj7（Cmaj7、Fmaj7）
    * V 级大三和弦 → 属七（G7），这是它的功能和声性质决定的
    * 小三和弦 → m7（Am7）
    """
    from app.services.analyzer.key import MAJOR_INTERVALS, NATURAL_MINOR_INTERVALS, parse_key
    from app.services.parser.chords import NOTE_TO_PITCH, ChordParseError, parse_chord

    tonic, mode = parse_key(key or "C Major")
    tonic_pitch = NOTE_TO_PITCH.get(tonic)
    intervals = MAJOR_INTERVALS if mode == "Major" else NATURAL_MINOR_INTERVALS

    out: list[str] = []
    for name in chords:
        try:
            chord = parse_chord(name)
        except ChordParseError:
            out.append(name)
            continue

        if chord.quality == "minor":
            out.append(f"{chord.root}m7")
            continue

        if chord.quality != "major":
            out.append(chord.raw)
            continue

        # 大三和弦：看它的级数决定七音性质
        root_pitch = NOTE_TO_PITCH.get(chord.root)
        degree: int | None = None
        if tonic_pitch is not None and root_pitch is not None:
            offset = (root_pitch - tonic_pitch) % 12
            if offset in intervals:
                degree = intervals.index(offset)

        # 属功能（V 级）用属七；其余用大七
        if degree == 4:
            out.append(f"{chord.root}7")
        else:
            out.append(f"{chord.root}maj7")
    return out


def _reharmonize_with_borrowed(chords: list[str], key: str | None) -> list[str]:
    """把最后一个和弦换成同主音小调的 iv（最常见、改动最小的借用手法）。

    这是 `MVP计划.md` §3.9.3「建议 B：C → G → Am → Fm」的实现。
    """
    if not chords:
        return chords
    from app.services.analyzer.key import parse_key
    from app.services.parser.chords import ChordParseError, parse_chord

    out = list(chords)
    tonic, mode = parse_key(key or "C Major")
    if mode != "Major":
        return out  # 小调场景不套用这个手法

    # 目标：把 IV 级（下属和弦）换成 iv
    from app.services.analyzer.key import MAJOR_INTERVALS
    from app.services.parser.chords import NOTE_TO_PITCH

    subdominant_pitch = (NOTE_TO_PITCH.get(tonic, 0) + MAJOR_INTERVALS[3]) % 12
    for index in range(len(out) - 1, -1, -1):
        try:
            chord = parse_chord(out[index])
        except ChordParseError:
            continue
        if NOTE_TO_PITCH.get(chord.root) == subdominant_pitch and chord.quality == "major":
            out[index] = f"{chord.root}m"
            return out
    return out


def _reharmonize_with_bass(chords: list[str], key: str | None) -> list[str]:
    """给和弦加上**级进下行**的低音（转位写法）。

    这是 ``harmony.chord_color.01`` 的「手法二：换低音」，
    也是 ``examples.bass_line.01`` 描述的 ``C → G/B → Am → F/A``。

    ⚠️ **必须用带八度的音高来规划，不能只用 0~11 的音级。**
    实测教训：只有音级时「下行」是没有意义的 —— 从 G(7) 往下只剩 D(2)，
    而 D 是 G 的五音不是三音，拼出来的线条在听感上并不连贯，
    还会产生 ``A/E`` 这种既非目标低音、又可能是调外音的结果。

    正确做法是在**音高空间**（可跨八度）里选一条严格递减的低音线：
    低音可以低于根音一个八度，这样 ``C4 → B3 → A3 → G3`` 之类的
    平滑下行才有可能，对应到和弦名就是 ``C → G/B → Am → F/A``。

    :param key: 调性。用于优先选择调内低音，避免产生调外和弦。
    """
    from app.services.analyzer.key import key_pitch_classes
    from app.services.parser.chords import NOTE_TO_PITCH, ChordParseError, parse_chord

    parsed = []
    for name in chords:
        try:
            parsed.append(parse_chord(name))
        except ChordParseError:
            parsed.append(None)

    in_key = key_pitch_classes(key) if key else None

    # 第一步：为每个和弦列出「可作为低音的调内音」及其相对根音的音高位移。
    # 位移用 -12 ~ +0 表示：三音/五音可以放在根音的同一八度或低一个八度。
    candidates: list[list[tuple[str, int]]] = []
    for chord in parsed:
        if chord is None:
            candidates.append([])
            continue
        root_pitch = NOTE_TO_PITCH.get(chord.root)
        if root_pitch is None:
            candidates.append([])
            continue

        from app.services.analyzer.key import QUALITY_INTERVALS

        intervals = QUALITY_INTERVALS.get(chord.quality, (0, 4, 7))
        options: list[tuple[str, int]] = []
        for idx, interval in enumerate(intervals[:3]):
            # 低音可以落在根音所在的八度，或低一个八度。
            # 低八度是**低音线能够持续下行**的前提：当线条已经走到根音以下时，
            # 只有允许「低八度原位」才可能继续下落，而不会被迫反弹回原位。
            for octave_shift in (0, -12):
                absolute = root_pitch + interval + octave_shift
                pitch_class = absolute % 12
                if in_key is not None and idx > 0 and pitch_class not in in_key:
                    # 根音一定保留（原位总是合法）；三音/五音若非调内音则跳过，
                    # 否则会产生调外和弦，Grounding 会（正确地）报错。
                    continue
                options.append((_pitch_name(pitch_class), absolute))
        candidates.append(options)

    # 第二步：贪心选一条递减的低音线。
    #
    # 设计约束（每一条都对应一个实测踩过的坑）：
    # 1. 首个和弦用**原位** —— 四六和弦（如 C/G）起手听感不稳定，不适合做起点。
    # 2. 后续优先选「低于前一个低音的**实际音高**」的候选里最高的那个，保证平滑下行。
    # 3. 实在无法下行时保留原位并重置基准，而不是硬选一个跳得很远的音。
    #
    # ⚠️ 比较必须基于**实际音高**而非音级。
    # 实测教训：用音级比较时，从 B(11) 出发会把 A(9) 判为「更低」，
    # 于是给 Am 选了原位 —— 但原位 A 的实际音高（A4）高于 B3，
    # 低音线实际是 C4 → B3 → A4 → F4，中途大幅反弹。
    bass_line: list[tuple[str, int] | None] = []
    previous: int | None = None

    for index, options in enumerate(candidates):
        chord = parsed[index]
        if not options or chord is None:
            bass_line.append(None)
            continue

        root_pitch = NOTE_TO_PITCH.get(chord.root)
        # 原位低音取「根音在同一八度」的那一项，其实际音高作为基准
        root_options = [o for o in options if root_pitch is not None and o[1] % 12 == root_pitch]
        root_option = root_options[0] if root_options else options[0]

        if previous is None:
            pick = root_option
        else:
            # 只在**实际音高**确实更低时才算继续下行
            downward = [o for o in options if o[1] < previous]
            pick = max(downward, key=lambda o: o[1]) if downward else root_option

        bass_line.append(pick)
        previous = pick[1]

    # 第三步：拼装和弦名
    out: list[str] = []
    for chord, bass in zip(parsed, bass_line):
        if chord is None or bass is None:
            out.append(chord.raw if chord else "?")
            continue
        if bass[0] == chord.root:
            out.append(chord.raw)
        else:
            out.append(f"{chord.root}/{bass[0]}")
    return out


def _pitch_name(pitch: int) -> str:
    """半音序号 → 音名（统一用升号写法，与解析器的对照表兼容）。"""
    return ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")[pitch % 12]


def build_fallback_advice(
    analysis: AnalysisResult, evidence: list[Evidence], level: str = "some"
) -> Advice:
    """知识库直出的兜底建议。

    即使完全无法调用 LLM，进阶用户也能拿到 2~3 个**有理论依据、可执行**的方向。
    """
    options = _fallback_options(analysis, evidence)

    roman_text = " → ".join(analysis.roman) if analysis.roman else "（未识别）"
    analysis_text = (
        f"当前进行为 {roman_text}，"
        f"{analysis.layman.progression or '属于比较常见的和声走向'}。"
    )

    problems: list[str] = []
    if analysis.layman.character:
        for trait in analysis.layman.character.split("；"):
            if trait.strip():
                problems.append(trait.strip())

    return Advice(
        analysis=analysis_text,
        problems=problems or ["和声色彩较为常规。"],
        options=options,
        evidence=evidence,
        layman_level=level,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# 多样性校验（P4.5 / §7.2 的 Diversity 维度）
# --------------------------------------------------------------------------- #


def _chord_set(option: AdviceOption) -> set[str]:
    return {c.strip().lower() for c in option.chords if c.strip()}


def check_diversity(advice: Advice) -> list[str]:
    """方案之间是否足够不同。

    ``MVP计划.md`` §7.2 把 Diversity 列为评估维度之一。
    如果三个方案其实是同一个改动的三种说法，用户的选择权是虚假的 ——
    这比只给一个方案更糟，因为它假装提供了选择。
    """
    warnings: list[str] = []
    options = advice.options
    for i in range(len(options)):
        for j in range(i + 1, len(options)):
            a, b = _chord_set(options[i]), _chord_set(options[j])
            if not a or not b:
                continue
            union = a | b
            overlap = len(a & b) / len(union) if union else 0.0
            if overlap > DIVERSITY_OVERLAP_LIMIT:
                warnings.append(
                    f"「{options[i].label}」与「{options[j].label}」的和弦重叠度达 "
                    f"{overlap:.0%}，用户感知到的差异可能很小"
                )
    return warnings


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #


def _evidence_queries(goal: CreativeGoal, analysis: AnalysisResult) -> list[str]:
    """P4 阶段用规则生成检索词；P3.9 完成后会换成 LLM Query 分解。"""
    queries: list[str] = []
    if goal.text:
        queries.append(goal.text)
    queries.extend(goal.intent.harmony_needs)
    if "普通" in (goal.text or ""):
        queries.append("和弦色彩 增加色彩的方式")
    if analysis.key:
        queries.append(f"{analysis.key} 常见和弦进行")
    if not queries:
        queries.append("如何修改和弦进行")
    return queries


def generate_advice(
    payload: CreationInput,
    *,
    goal: CreativeGoal | None = None,
    llm: Any | None = None,
    kb: OfflineKnowledgeBase | None = None,
    top_k: int = 5,
) -> AdviceResult:
    """进阶链路主入口：作品 + 目标 → 分析 + 修改建议。

    :param payload: 请求体（含 ``artifact``）
    :param goal: 创作目标；缺省时从 ``payload.raw_text`` 构造
    :param llm: 满足 ``LLMProvider`` 协议的对象
    """
    log = DegradationLog()
    timings: dict[str, int] = {}
    kb = kb or default_kb()

    if goal is None:
        goal_intent = intent_service.resolve_intent(payload.raw_text, payload.selections, llm=llm, log=log)
        goal = CreativeGoal(text=payload.raw_text, intent=goal_intent)
    else:
        goal_intent = goal.intent

    # --- ① 分析用户作品 ---
    started = time.perf_counter()
    analysis = analyze(payload.artifact)
    if analysis.raw and not payload.artifact.key:
        log.add(
            "intent_fallback",
            "用户未指定调性，已自动识别",
            fallback_to=f"detected:{analysis.key}",
        )
    timings["analyze"] = int((time.perf_counter() - started) * 1000)

    # --- ② 检索知识（P3 起走 Hybrid，失败自动回落离线直出）---
    started = time.perf_counter()
    retrieval = retrieval_service.retrieve(
        goal_text=goal.text,
        intent=goal_intent,
        artifact_summary=(
            f"{analysis.key or ''} {' '.join(analysis.raw)} {analysis.layman.progression or ''}"
        ),
        user_level=payload.user_level,
        emotions=goal_intent.emotion,
        llm=llm,
        log=log,
    )
    evidence = retrieval.evidence
    timings["retrieve"] = int((time.perf_counter() - started) * 1000)

    # --- ③ 组装 Prompt ---
    prompt = prompts.tutor_prompt(
        goal_text=goal.text,
        analysis=analysis,
        evidence=evidence,
        level=payload.user_level,
        constraints=goal.constraints,
    )

    # --- ④ LLM 生成 ---
    advice: Advice | None = None
    raw_output = ""
    started = time.perf_counter()

    if llm is None or not getattr(llm, "available", False):
        # 用 add_once：intent 层与 query 分解层可能已记录过同类事件。
        if llm is not None:
            log.add_once(
                "llm_unavailable",
                "LLM 未配置或不可用，修改建议回落知识库直出",
                fallback_to="fallback_advice",
            )
    else:
        try:
            raw_output = llm.complete(prompt, json_mode=True)
            advice = parse_advice(raw_output, level=payload.user_level)
        except OutputParseError as exc:
            log.add("parse_failed", f"建议解析失败：{exc}", fallback_to="fallback_advice")
        except Exception as exc:  # noqa: BLE001
            log.add("llm_failed", f"生成建议失败：{exc}", fallback_to="fallback_advice")
    timings["generate"] = int((time.perf_counter() - started) * 1000)

    # --- ⑤ 兜底 ---
    if advice is None:
        advice = build_fallback_advice(analysis, evidence, level=payload.user_level)

    # --- ⑥ 分级通俗化（P4.4）---
    # Prompt 里的风格要求只是「请求」模型照办；这一步是确定性的兜底，
    # 保证展示给零基础用户的文本一定经过术语替换。
    advice = adapt_advice(advice, payload.user_level)

    # --- ⑦ Grounding 校验（P4.6）---
    started = time.perf_counter()
    grounding = verify_advice(
        advice,
        evidence=evidence,
        intent=goal_intent,
        goal_text=goal.text,
        key=analysis.key,
        constraints=goal.constraints,
        level=payload.user_level,
    )
    advice = advice.model_copy(
        update={"evidence": evidence, "grounding": grounding, "layman_level": payload.user_level}
    )
    timings["grounding"] = int((time.perf_counter() - started) * 1000)

    warnings = check_diversity(advice)

    trace = Trace(
        config={
            "mode": "tutor",
            "user_level": payload.user_level,
            "retrieval": "offline",
            "top_k": top_k,
        },
        input=payload,
        intent=goal_intent,
        analysis=analysis,
        evidence=evidence,
        prompt=prompt,
        raw_output=raw_output,
        advice=advice,
        grounding=grounding,
        degradation=log.items(),
        timings_ms=timings,
    )

    return AdviceResult(advice=advice, analysis=analysis, trace=trace, warnings=warnings)
