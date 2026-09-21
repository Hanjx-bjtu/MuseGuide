"""Layman Adaptation —— 把专业表述转换为通俗表述。

依据：``MVP计划.md`` §3.8（§3.8.2 转换规则表、§3.8.3 输出策略）。

**为什么这不是「渲染时的字符串替换」：**
术语表在这里是可断言的常量，因此「输出是否通俗」可以被测试，
而不是靠人工读一遍。P6 的「零基础术语守卫」直接消费 ``TERM_MAP`` 与
``BANNED_TERMS``。
"""

from __future__ import annotations

import re
from typing import Literal, NamedTuple

from app.core.brief import UserLevel

# --------------------------------------------------------------------------- #
# §3.8.2 转换规则表
# --------------------------------------------------------------------------- #


class TermEntry(NamedTuple):
    """一条「专业表述 → 通俗表述」映射。"""

    professional: str
    """专业表述（可能作为检索关键词或模型输出出现）。"""

    layman: str
    """通俗表述 —— 这是要展示给零基础用户的话。"""

    note: str = ""
    """进阶用户使用的旁注形式（§3.8.3 的「中间状态」）。"""


#: §3.8.2 的七条规则，逐条落为代码。
TERM_MAP: tuple[TermEntry, ...] = (
    TermEntry("I → V → vi → IV", "流行歌最常用的走向之一", "I–V–vi–IV 进行"),
    TermEntry("功能关系稳定", "听起来平稳，没有太强的起伏", "功能关系稳定"),
    TermEntry("和声张力较低", "比较温和，没有紧张感", "和声张力偏低"),
    TermEntry("色彩和弦较少", "比较素，缺少层次感", "色彩和弦较少"),
    TermEntry("Modal Interchange", "从别的调借一个和弦，增加色彩", "调式借用（Modal Interchange）"),
    TermEntry("Cadence", "音乐的「句号」，让人感觉告一段落", "终止式（Cadence）"),
    TermEntry("Tension / Resolution", "紧张感→释放感，像讲故事的高潮和结尾", "张力—释放（Tension/Resolution）"),
)

#: 专业表述 → 通俗表述（大小写不敏感查找）
TERM_LOOKUP: dict[str, str] = {e.professional.lower(): e.layman for e in TERM_MAP}

#: 专业表述 → 带旁注的写法（§3.8.3「中间状态」）
TERM_NOTE_LOOKUP: dict[str, str] = {
    e.professional.lower(): (e.note or e.professional) for e in TERM_MAP
}


#: 零基础模式下**不应出现**的术语。
#: P6 的术语守卫会扫描用户面文本，发现即告警（§9 第 7 条）。
BANNED_TERMS: tuple[str, ...] = (
    "三度叠置",
    "音程",
    "转位",
    "副属和弦",
    "调式交替",
    "声部进行",
    "功能圈",
    "增四度",
    "解决到",
    "平行五度",
)


# --------------------------------------------------------------------------- #
# §3.8.3 输出策略：三档
# --------------------------------------------------------------------------- #

StyleGuide = dict[str, str]

#: 三档风格指南。``zero`` 是默认，也是本项目的主场景。
STYLE_GUIDES: dict[UserLevel, StyleGuide] = {
    "zero": {
        "audience": "完全不懂乐理的创作爱好者",
        "tone": "像朋友聊天，先描述「听起来会怎样」，再解释「为什么」",
        "terms": "不使用专业术语；必须使用时紧跟一句通俗解释",
        "structure": "每个建议 = 听感描述 + 一句原因 + 一个可执行动作",
        "forbidden": "禁止罗列罗马数字、禁止和弦功能名、禁止调式术语",
        "example": "把 C 换成 Cmaj7，听起来会更柔和、更梦幻 —— 走向没变，只是多了层质感。",
    },
    "some": {
        "audience": "接触过一些乐理、能看懂和弦名的爱好者",
        "tone": "通俗解释为主，专业术语以旁注形式出现",
        "terms": "允许使用术语，但首次出现时用「通俗说法（Term）」的形式",
        "structure": "每个建议 = 结论 + 通俗解释 + 术语旁注 + 理论依据",
        "forbidden": "避免连续的术语堆叠",
        "example": "把 F 换成 Fm（从平行小调借来的和弦），会多一丝忧伤。",
    },
    "advanced": {
        "audience": "有明确理论基础、能读写罗马数字的创作者",
        "tone": "直接使用专业表述，结构化、给依据",
        "terms": "正常使用术语，不必解释",
        "structure": "分析 → 问题 → 建议（含和弦与功能说明）→ 理论依据",
        "forbidden": "不要为了通俗而牺牲精度",
        "example": "将 IV 替换为 iv（modal interchange），保持 I–V–vi 的功能框架不变。",
    },
}


def style_guide(level: UserLevel) -> StyleGuide:
    """取某一档的输出风格指南（注入 Prompt 使用）。"""
    return STYLE_GUIDES.get(level, STYLE_GUIDES["zero"])


def prompt_style_block(level: UserLevel) -> str:
    """把风格指南渲染成 Prompt 片段（供 P2/P4 的 PromptBuilder 拼装）。"""
    guide = style_guide(level)
    return (
        f"[输出风格要求]\n"
        f"- 面向：{guide['audience']}\n"
        f"- 语气：{guide['tone']}\n"
        f"- 术语：{guide['terms']}\n"
        f"- 结构：{guide['structure']}\n"
        f"- 禁止：{guide['forbidden']}\n"
        f"- 参考写法：{guide['example']}"
    )


# --------------------------------------------------------------------------- #
# 转换
# --------------------------------------------------------------------------- #


def to_layman(text: str, level: UserLevel = "zero") -> str:
    """按用户水平把专业表述转成对应写法。

    * ``zero``：替换为通俗表述
    * ``some``：替换为带旁注的写法
    * ``advanced``：原样保留
    """
    if not text:
        return text
    if level == "advanced":
        return text

    lookup = TERM_LOOKUP if level == "zero" else TERM_NOTE_LOOKUP
    result = text
    # 长键优先，避免 "I → V → vi → IV" 被更短的键部分匹配
    for key in sorted(lookup, key=len, reverse=True):
        result = re.sub(re.escape(key), lambda _m, v=lookup[key]: v, result, flags=re.IGNORECASE)
    return result


def find_banned_terms(text: str) -> list[str]:
    """扫描零基础文本中出现的禁用术语（P6 术语守卫的核心）。"""
    return [term for term in BANNED_TERMS if term in (text or "")]


def has_unexplained_terms(text: str, level: UserLevel = "zero") -> bool:
    """零基础模式下是否出现了未解释的术语。

    ``some`` / ``advanced`` 档不做此检查 —— 术语对他们是正常的。
    """
    if level != "zero":
        return False
    return bool(find_banned_terms(text))


# --------------------------------------------------------------------------- #
# 分级应用（P4.4）
# --------------------------------------------------------------------------- #


def adapt_advice(advice, level: UserLevel = "zero"):
    """按用户水平改写整份建议的可读文本（§3.8.3 的三档策略）。

    **为什么要在生成之后再转换一次：**
    Prompt 里的 ``[输出风格要求]`` 只是「请求」模型用某种风格说话，
    但模型未必照办。这一步是**确定性的兜底**：无论模型怎么写，
    最终展示给零基础用户的文本都会经过术语替换。

    改写范围仅限**展示文本**（``feature`` / ``reason`` / ``theory``），
    不动 ``chords`` —— 和弦是数据，不是措辞。
    """
    if level == "advanced":
        return advice  # 进阶用户保留原文，不做任何替换

    options = [
        option.model_copy(
            update={
                "feature": to_layman(option.feature, level),
                "reason": to_layman(option.reason, level),
                "theory": [to_layman(t, level) for t in option.theory],
            }
        )
        for option in advice.options
    ]

    return advice.model_copy(
        update={
            "analysis": to_layman(advice.analysis, level),
            "problems": [to_layman(p, level) for p in advice.problems],
            "options": options,
        }
    )


def terminology_density(text: str) -> float:
    """粗略估计术语密度（专业表述与禁区术语的出现次数 / 文本长度 × 100）。

    用于验证三档输出**确实有可测量的区分**，而不是仅仅换了 Prompt 措辞。
    """
    if not text:
        return 0.0
    hits = sum(1 for entry in TERM_MAP if entry.professional.lower() in text.lower())
    hits += len(find_banned_terms(text))
    return round(hits / max(len(text), 1) * 100, 4)
