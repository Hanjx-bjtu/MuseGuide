"""Intent Mapping —— 把「日常语言」映射到音乐概念。

依据：``MVP计划.md`` §3.2（含 §3.2.2 的映射规则表与 §3.2.3 的 prompt 结构）。

**四层瀑布（§5.2 of 实现阶段计划）：**

```text
① 规则层：扫描 RULE_MAP 关键词（零成本、确定性、可单测）
      ↓ 命中数 < 2 或 关键字段为空
② LLM 层：DeepSeek 结构化输出（§3.2.3 的 prompt）
      ↓ 超时 / 报错 / JSON 解析失败
③ 选择层：直接采用用户的下拉选择（§3.10.2 的「选择式输入兜底」）
      ↓ 仍为空（用户什么都没填）
④ 默认层：温和流行 + 中等偏慢 + C 大调，并显式提示用户
```

每一层都在 ``Trace.degradation`` 留痕，因此「系统为什么给了这个方向」永远可解释。
这是 ``MVP计划.md`` §8 风险应对「LLM + 规则映射双保险，规则兜底」的实现。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.brief import CreativeIntent, IntentSource, SelectionInput
from app.core.degradation import DegradationLog
from app.core.options import (
    emotion_option,
    style_option,
    tempo_option_by_label,
)

# --------------------------------------------------------------------------- #
# §3.2.2 映射规则表
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class IntentRule:
    """一条「日常语言 → 音乐概念」规则。

    严格对应 ``MVP计划.md`` §3.2.2 的 JSON 映射表：``key`` 是用户可能说的话，
    ``value`` 是要映射到的音乐概念。``keywords`` 是命中所依据的关键词。
    """

    name: str
    """规则名（写入 ``CreativeIntent.matched_rules``，用于解释映射来源）。"""

    keywords: tuple[str, ...]
    """命中关键词。任一出现即算命中该规则。"""

    concepts: tuple[str, ...]
    """映射到的音乐概念（§3.2.2 的 value）。"""

    emotion: tuple[str, ...] = ()
    style: str | None = None
    tempo_feel: str | None = None
    key_preference: str | None = None
    harmony_needs: tuple[str, ...] = ()


#: §3.2.2 的九条规则，逐条落为代码。
#:
#: 顺序即优先级：越靠前越先参与合并（情绪类规则在前，因为它们最能决定后续方向）。
RULE_MAP: tuple[IntentRule, ...] = (
    IntentRule(
        name="听起来很普通",
        keywords=("很普通", "太普通", "普通", "太大众", "没什么特别", "很一般", "平淡"),
        concepts=("缺乏和声色彩", "常见进行"),
        harmony_needs=("需要色彩变化",),
    ),
    IntentRule(
        name="感觉不对劲",
        keywords=("不对劲", "怪怪", "不太对", "别扭", "哪里错"),
        concepts=("可能存在不协和", "功能逻辑问题"),
        harmony_needs=("需要检查和声逻辑",),
    ),
    IntentRule(
        name="想要更温暖",
        keywords=("温暖", "暖", "温柔", "柔和", "治愈"),
        concepts=("倾向大调和声", "纯五度关系"),
        emotion=("温暖",),
        key_preference="大调（温暖感）",
        harmony_needs=("倾向大调和声",),
    ),
    IntentRule(
        name="想要更伤感",
        keywords=("伤感", "悲伤", "难过", "失落", "忧伤", "忧郁", "心碎", "告别"),
        concepts=("倾向小调", "借用和弦", "下行旋律"),
        emotion=("伤感",),
        key_preference="小调（忧伤感）",
        harmony_needs=("需要小调色彩",),
    ),
    IntentRule(
        name="想要高潮感",
        keywords=("高潮", "爆发", "推上去", "有力量", "激昂", "燃"),
        concepts=("Tension build-up", "力度变化", "音域升高"),
        emotion=("激昂",),
        harmony_needs=("需要张力推进",),
    ),
    IntentRule(
        name="想要收尾的感觉",
        keywords=("收尾", "结束", "结尾", "句号", "告一段落", "落下"),
        concepts=("Cadence", "终止式"),
        harmony_needs=("需要终止感",),
    ),
    IntentRule(
        name="有点伤感但最后释然",
        keywords=("释然", "放下", "看开", "走出来", "释怀"),
        concepts=("小调→大调转向", "下行→上行旋律", "张力→释放"),
        emotion=("伤感", "释然"),
        key_preference="大调（释然感）",
        harmony_needs=("需要情绪转折",),
    ),
    IntentRule(
        name="温柔",
        keywords=("温柔", "轻柔", "安静", "舒缓"),
        concepts=("大调", "慢速", "柔和音色"),
        emotion=("温柔",),
        tempo_feel="很慢",
        key_preference="大调（温暖感）",
    ),
    IntentRule(
        name="激昂",
        keywords=("激昂", "热血", "振奋", "有力", "燃"),
        concepts=("大调", "快速", "强力度", "高音域"),
        emotion=("激昂",),
        tempo_feel="中等",
        key_preference="大调（明亮感）",
    ),
)

#: 规则名 → 规则（供测试与调试按名取用）
RULES_BY_NAME: dict[str, IntentRule] = {r.name: r for r in RULE_MAP}


# --------------------------------------------------------------------------- #
# 风格 / 速度的关键词（§3.1.2 示例与 §3.10.2 选项）
# --------------------------------------------------------------------------- #

STYLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("民谣", ("民谣", "吉他", "弹唱", "folk")),
    ("流行", ("流行", "抒情", "电台", "pop")),
    ("摇滚", ("摇滚", "乐队", "rock")),
    ("钢琴", ("钢琴", "纯音乐", "器乐", "piano")),
    ("电子", ("电子", "氛围", "合成器", "synth")),
    ("爵士", ("爵士", "jazz", "蓝调")),
)

#: 速度关键词 → 受控词表中的速度概念。
#:
#: **顺序即优先级，必须「最具体者优先」**：``中等偏慢`` 必须排在 ``很慢`` 之前，
#: 否则「速度中等偏慢」会因为含有裸字 ``慢`` 而被误判为「很慢」。
#: 这是一个真实踩过的坑，已在 ``test_tempo_keyword_precedence`` 固化为回归用例。
TEMPO_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("中等偏慢", ("中等偏慢", "偏慢", "稍慢", "散步", "抒情")),
    ("很慢", ("很慢", "缓慢", "慢速", "慢", "回忆", "翻相册")),
    ("中等", ("中等", "中速", "走路", "轻快")),
)

#: 情绪关键词 → 受控词表中的情绪概念
EMOTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("伤感", ("伤感", "悲伤", "难过", "失落", "忧伤", "忧郁", "心碎", "告别", "毕业")),
    ("释然", ("释然", "放下", "释怀", "看开", "走出来", "开阔")),
    ("温暖", ("温暖", "暖", "治愈", "柔和")),
    ("激昂", ("激昂", "热血", "振奋", "燃", "爆发")),
    ("温柔", ("温柔", "轻柔", "安静", "舒缓")),
    ("轻快", ("轻快", "活泼", "明快")),
    ("梦幻", ("梦幻", "漂浮", "朦胧", "空灵")),
)


def _contains(text: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in text for kw in keywords)


# --------------------------------------------------------------------------- #
# 第①层：规则映射
# --------------------------------------------------------------------------- #


def map_by_rules(text: str) -> CreativeIntent:
    """规则层映射 —— **零成本、确定性、可脱离 LLM 独立工作**。

    这是 §8 风险应对里「规则兜底」的实际承担者：即使完全没有 API Key，
    这一层也能给出可用的创作方向。
    """
    intent = CreativeIntent(raw_text=text, source="rule")
    raw = (text or "").strip()
    if not raw:
        return intent

    matched: list[str] = []
    emotions: list[str] = []
    harmony_needs: list[str] = []
    # key/tempo 由**最后**一条命中的规则决定 —— 因为越具体的规则越靠后，
    # 而具体规则（如「有点伤感但最后释然」）才真正描述用户的整体意图。
    # 若用「首个命中者优先」，泛化规则「想要更伤感」会把它覆盖掉。
    key_pref: str | None = None
    tempo_feel: str | None = None

    for rule in RULE_MAP:
        if _contains(raw, rule.keywords):
            matched.append(rule.name)
            for emo in rule.emotion:
                if emo not in emotions:
                    emotions.append(emo)
            for need in rule.harmony_needs:
                if need not in harmony_needs:
                    harmony_needs.append(need)
            if rule.key_preference:
                key_pref = rule.key_preference
            if rule.tempo_feel:
                tempo_feel = rule.tempo_feel

    # 情绪补充：规则未覆盖到的情绪词，从 EMOTION_KEYWORDS 兜一层
    for concept, kws in EMOTION_KEYWORDS:
        if _contains(raw, kws) and concept not in emotions:
            emotions.append(concept)

    # 风格
    style: str | None = None
    for concept, kws in STYLE_KEYWORDS:
        if _contains(raw, kws):
            style = concept
            break

    # 速度（仅当规则没给出时）
    if tempo_feel is None:
        for concept, kws in TEMPO_KEYWORDS:
            if _contains(raw, kws):
                tempo_feel = concept
                break

    # 只保留受控词表中的情绪，避免自由文本污染下游
    intent.emotion = [e for e in emotions if emotion_option(e)]
    intent.style = style
    intent.tempo_feel = tempo_feel
    intent.key_preference = key_pref
    intent.harmony_needs = harmony_needs
    intent.matched_rules = matched
    return intent


# --------------------------------------------------------------------------- #
# 第②层：LLM 映射（§3.2.3）
# --------------------------------------------------------------------------- #

#: §3.2.3 的 prompt 模板 —— 五个输出字段一个不多一个不少。
INTENT_PROMPT_TEMPLATE = """用户用日常语言描述了创作意图：
"{user_input}"

请将以下日常语言映射到音乐概念，输出 JSON：
- emotion: 情绪方向（如：忧郁、温暖、激昂）
- style: 风格倾向（如：流行、民谣、摇滚）
- tempo_feel: 速度感（如：慢、中等偏慢、中等）
- key_preference: 调性倾向（如：大调偏温暖、小调偏忧伤）
- harmony_needs: 和声需求（如：需要色彩变化、需要终止感）

只输出 JSON，不要输出任何解释。"""

#: LLM 返回中允许出现的键（其它键一律忽略，防止模型自由发挥污染契约）
_ALLOWED_KEYS = ("emotion", "style", "tempo_feel", "key_preference", "harmony_needs")


def parse_llm_intent(payload: str) -> CreativeIntent:
    """把 LLM 的原始输出解析为 ``CreativeIntent``。

    解析失败抛 ``ValueError``，由调用方降级 —— **这里不吞异常**，
    因为「解析失败」本身是需要留痕的降级事件（``parse_failed``）。
    """
    text = (payload or "").strip()
    if not text:
        raise ValueError("LLM 返回为空")

    data = _extract_json(text)
    if not isinstance(data, dict):
        raise ValueError("LLM 返回的不是 JSON 对象")

    intent = CreativeIntent(source="llm")
    emotion = data.get("emotion")
    if isinstance(emotion, str):
        emotion = [emotion]
    if isinstance(emotion, list):
        # 只保留受控词表内的情绪
        intent.emotion = [str(e).strip() for e in emotion if emotion_option(str(e).strip())]

    for key in ("style", "tempo_feel", "key_preference"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            setattr(intent, key, value.strip())

    needs = data.get("harmony_needs")
    if isinstance(needs, str):
        needs = [needs]
    if isinstance(needs, list):
        intent.harmony_needs = [str(n).strip() for n in needs if str(n).strip()]

    # 规范化为受控概念，便于下游直接查表
    opt = style_option(intent.style)
    if opt:
        intent.style = opt.concept
    tempo_opt = tempo_option_by_label(intent.tempo_feel)
    if tempo_opt:
        intent.tempo_feel = tempo_opt.concept

    return intent


def _extract_json(text: str) -> Any:
    """从可能带 markdown 围栏或前后缀的文本中抽出 JSON。"""
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退一步：抓第一个 { ... } 片段
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"无法解析 JSON：{exc}") from exc
    raise ValueError("未找到 JSON 内容")


# --------------------------------------------------------------------------- #
# 第③层：选择式输入；第④层：默认值
# --------------------------------------------------------------------------- #


def intent_from_selections(selections: SelectionInput) -> CreativeIntent:
    """选择层 —— 用户的下拉选择（§3.10.2 的「选择式输入兜底」）。"""
    intent = CreativeIntent(source="selection")
    if selections.emotion:
        # 情绪允许 "伤感→释然" 这种复合写法
        parts = re.split(r"[→\->/、,，]+", selections.emotion)
        intent.emotion = [
            opt.concept
            for opt in (emotion_option(p.strip()) for p in parts if p.strip())
            if opt is not None
        ]
    if selections.style:
        opt = style_option(selections.style)
        intent.style = opt.concept if opt else selections.style
    if selections.tempo_label:
        opt = tempo_option_by_label(selections.tempo_label)
        intent.tempo_feel = opt.concept if opt else selections.tempo_label
    return intent


def default_intent(raw_text: str = "") -> CreativeIntent:
    """默认层 —— 用户什么都没提供时的起点。

    ``MVP计划.md`` §8 要求「零基础用户仍觉门槛高」时有兜底：
    给出一个温和、通用、容易解释的起点，而不是报错或空转。
    """
    return CreativeIntent(
        emotion=["温暖"],
        style="流行",
        tempo_feel="中等偏慢",
        key_preference="大调（温暖感）",
        harmony_needs=["需要和声色彩"],
        raw_text=raw_text,
        source="default",
    )


# --------------------------------------------------------------------------- #
# 合并：选择优先于 LLM 推断（P1.4）
# --------------------------------------------------------------------------- #


#: 最终情绪列表的上限。
#:
#: 情绪会驱动调性、和弦与旋律建议，超过 2~3 个之后彼此互相抵消
#: （「既伤感又激昂」无法指导创作），且 §3.1.2 的示例本身就是「伤感→释然」两段式。
#: 因此截断到 3 个 —— 保留优先级最高（最先合并）的部分。
MAX_EMOTIONS = 3


def merge_intents(*intents: CreativeIntent, raw_text: str = "") -> CreativeIntent:
    """按参数顺序合并，**先出现者优先**。

    P1.4 的规则：``选择式输入优先于 LLM 推断``（用户可以覆盖系统猜测）。
    调用方应按 ``[selection, llm, rule]`` 的顺序传入。
    """
    merged = CreativeIntent(raw_text=raw_text)
    rules: list[str] = []
    for intent in intents:
        if intent is None:
            continue
        for emo in intent.emotion:
            if emo not in merged.emotion:
                merged.emotion.append(emo)
        for need in intent.harmony_needs:
            if need not in merged.harmony_needs:
                merged.harmony_needs.append(need)
        for name in intent.matched_rules:
            if name not in rules:
                rules.append(name)
        if merged.style is None and intent.style:
            merged.style = intent.style
        if merged.tempo_feel is None and intent.tempo_feel:
            merged.tempo_feel = intent.tempo_feel
        if merged.key_preference is None and intent.key_preference:
            merged.key_preference = intent.key_preference
    merged.emotion = merged.emotion[:MAX_EMOTIONS]
    merged.matched_rules = rules
    return merged


# --------------------------------------------------------------------------- #
# 编排：四层瀑布
# --------------------------------------------------------------------------- #

#: 命中规则数达到此值，即认为规则层已给出足够方向
RULE_HIT_THRESHOLD = 2


def _needs_llm(rule_intent: CreativeIntent) -> bool:
    """是否值得为这次输入付出一次 LLM 调用。

    **判据是「规则层是否留下了空白」，而不是「命中了多少条规则」。**
    只看命中数会犯一个真实的错误：像「有点伤感但最后释然」这样的输入会
    命中两条规则、凑够阈值，于是系统跳过 LLM —— 但规则表对**风格**和**速度**
    一无所知，结果是 style / tempo_feel 全空，下游只能走默认值。
    """
    return (
        rule_intent.style is None
        or rule_intent.tempo_feel is None
        or not rule_intent.emotion
        or len(rule_intent.matched_rules) < RULE_HIT_THRESHOLD
    )


def resolve_intent(
    raw_text: str,
    selections: SelectionInput | None = None,
    *,
    llm: Any | None = None,
    log: DegradationLog | None = None,
    settings: Any | None = None,
) -> CreativeIntent:
    """四层瀑布编排，返回最终 ``CreativeIntent``。

    :param raw_text: 用户的创作意图原文
    :param selections: 选择式输入（可为空）
    :param llm: 满足 ``LLMProvider`` 协议的对象；``None`` 表示不尝试 LLM
    :param log: 降级留痕容器
    :param settings: 保留位（当前未使用，供后续按配置切换行为）
    """
    log = log if log is not None else DegradationLog()
    selections = selections or SelectionInput()

    rule_intent = map_by_rules(raw_text)
    llm_intent: CreativeIntent | None = None

    # ② LLM 层：仅当规则层留下空白且 LLM 可用时才调用
    need_llm = _needs_llm(rule_intent)
    if need_llm and llm is not None:
        if not getattr(llm, "available", False):
            log.add_once(
                "llm_unavailable",
                "LLM 未配置或不可用，Intent Mapping 回落规则层",
                fallback_to="rule+selection",
            )
        else:
            try:
                payload = llm.complete(INTENT_PROMPT_TEMPLATE.format(user_input=raw_text), json_mode=True)
                llm_intent = parse_llm_intent(payload)
            except ValueError as exc:
                log.add("parse_failed", f"Intent Mapping 输出解析失败：{exc}", fallback_to="rule+selection")
            except Exception as exc:  # noqa: BLE001 - LLMError 及其它后端异常统一降级
                log.add("llm_failed", f"Intent Mapping 调用失败：{exc}", fallback_to="rule+selection")

    # ③ 选择层：用户的选择优先于 LLM 推断
    selection_intent = intent_from_selections(selections)
    sources: list[CreativeIntent] = []
    if not selection_intent.is_sparse():
        sources.append(selection_intent)
    if llm_intent is not None and not llm_intent.is_sparse():
        sources.append(llm_intent)
    sources.append(rule_intent)

    merged = merge_intents(*sources, raw_text=raw_text)

    # ④ 默认层：仍然为空时兜底
    if merged.is_sparse() or not merged.emotion:
        fallback = default_intent(raw_text)
        if not merged.emotion:
            merged.emotion = fallback.emotion
        if merged.style is None:
            merged.style = fallback.style
        if merged.tempo_feel is None:
            merged.tempo_feel = fallback.tempo_feel
        if merged.key_preference is None:
            merged.key_preference = fallback.key_preference
        if not merged.harmony_needs:
            merged.harmony_needs = fallback.harmony_needs
        merged.source = "default"
        log.add(
            "intent_fallback",
            "输入信息不足，已采用通用起步方向",
            fallback_to="default_intent",
        )
    elif merged.source == "default":
        # merge 不设置 source，此处按信息来源标注
        merged.source = _infer_source(selection_intent, llm_intent, rule_intent)

    return merged


def _infer_source(
    selection_intent: CreativeIntent,
    llm_intent: CreativeIntent | None,
    rule_intent: CreativeIntent,
) -> IntentSource:
    """标注最终结果的来源层级（用于可解释性与评测统计）。"""
    if not selection_intent.is_sparse():
        return "selection"
    if llm_intent is not None and not llm_intent.is_sparse():
        return "llm"
    if not rule_intent.is_sparse():
        return "rule"
    return "default"


def concepts_for_rule(rule_name: str) -> tuple[str, ...]:
    """按规则名取音乐概念（供测试逐条断言 §3.2.2）。"""
    rule = RULES_BY_NAME.get(rule_name)
    return rule.concepts if rule else ()
