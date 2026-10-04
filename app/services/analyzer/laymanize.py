"""分析结果的通俗解读生成（``MVP计划.md`` §3.4.1 / §3.4.2 的 ``layman`` 字段）。

**关键设计：** §3.4.1 与 §3.4.2 的输出 JSON 里都有一个 ``layman`` 子对象。
本项目把它做成**与专业字段并列存储**，而不是在渲染时临时翻译 ——
这样通俗层可以被测试断言，也可以被检索（零基础模式下通俗内容优先）。

专业字段与通俗字段的对应关系见 ``docs/DATA_CONTRACTS.md``。
"""

from __future__ import annotations

from app.core.artifact import ChordSymbol, MelodyInfo
from app.core.plan import LaymanNote
from app.services.analyzer.key import parse_key

#: 调性 → 通俗描述（§3.4.1 的 ``layman.key``）
KEY_LAYMAN: dict[str, str] = {
    "C": "听起来明亮温暖的调，而且按起来最简单（全是白键）",
    "G": "听起来开阔明亮的调",
    "D": "听起来更明亮、更有精神的调",
    "A": "听起来明亮但带一点锐度的调",
    "E": "听起来很明亮、偏热烈的调",
    "F": "听起来柔和温暖的调",
    "Bb": "听起来温暖圆润的调",
    "Eb": "听起来温暖厚重的调",
    "Ab": "听起来温暖中带一点阴郁的调",
    "B": "听起来明亮但有点紧张的调",
    "F#": "听起来明亮但偏锐利的调",
}

MINOR_LAYMAN = "听起来柔和忧伤的调"

#: 常见的和弦进行 → 通俗描述（§3.4.1 的 ``layman.progression``）
PROGRESSION_LAYMAN: dict[tuple[str, ...], str] = {
    ("I", "V", "vi", "IV"): "流行歌最常用的走向之一",
    ("vi", "IV", "I", "V"): "流行歌最常用的走向之一，只不过从小调那一头开始",
    ("I", "IV", "V", "I"): "民谣里最常见的走向，像把事情从头讲到尾",
    ("I", "V", "vi", "iii", "IV", "I", "IV", "V"): "很经典的抒情走向",
    ("ii", "V", "I"): "爵士和流行里都很常见的收尾方式",
    ("I", "vi", "IV", "V"): "上世纪老歌常用的走向，听起来很顺",
}

#: 轮廓 → 通俗描述（§3.4.2 的 ``layman.contour``）
CONTOUR_LAYMAN: dict[str, str] = {
    "up": "一路往上走，听起来越来越有精神",
    "down": "一路往下走，像在叹气",
    "up-down": "先往上走再回来，像起伏的波浪",
    "down-up": "先往下沉再抬起来，像情绪从低落到振作",
    "flat": "基本停在原地，比较平，接近说话的语气",
}


def describe_key(key: str) -> str:
    """§3.4.1 的 ``layman.key``。"""
    tonic, mode = parse_key(key)
    if mode == "Minor":
        return MINOR_LAYMAN
    return KEY_LAYMAN.get(tonic, "听起来比较明亮的调")


def describe_progression(romans: list[str]) -> str:
    """§3.4.1 的 ``layman.progression``。"""
    if not romans:
        return ""
    key = tuple(r for r in romans if r != "?")
    if key in PROGRESSION_LAYMAN:
        return PROGRESSION_LAYMAN[key]
    # 退一步：只比对前四个级数
    for pattern, text in PROGRESSION_LAYMAN.items():
        if len(pattern) >= 4 and key[:4] == pattern[:4]:
            return text
    return "这是一组比较常见的和弦走向"


def describe_character(chords: list[ChordSymbol], romans: list[str]) -> str:
    """§3.4.1 的 ``layman.character`` —— 描述功能与色彩特征。

    对应 ``MVP计划.md`` §3.10.5 的「特征」三行：
    功能关系稳定 / 和声张力较低 / 色彩和弦较少。

    :param chords: 已解析的和弦
    :param romans: 已标注的罗马数字（``"?"`` 表示调外和弦）
    """
    if not chords:
        return ""

    traits: list[str] = []

    out_of_key = sum(1 for r in romans if r == "?")
    has_seventh = any(c.quality in ("maj7", "min7", "dom7") for c in chords)
    has_inversion = any(c.bass and c.bass != c.root for c in chords)

    if out_of_key == 0 and not has_seventh:
        traits.append("功能稳定，听感平稳，但色彩较少")
    elif out_of_key == 0:
        traits.append("功能稳定，加上了七音，听感更柔和")
    else:
        traits.append("带有调外和弦，色彩更丰富，但调性稍显模糊")

    if not has_inversion:
        traits.append("低音基本停在根音上，缺少流动感")
    else:
        traits.append("低音有走动，听起来更连贯")

    return "；".join(traits)


def laymanize_chords(
    chords: list[ChordSymbol], key: str, romans: list[str]
) -> LaymanNote:
    """生成和弦分析的 ``layman`` 子对象（§3.4.1 的输出结构）。"""
    return LaymanNote(
        key=describe_key(key),
        progression=describe_progression(romans),
        character=describe_character(chords, romans),
    )


def laymanize_melody(melody: MelodyInfo, key: str | None = None) -> LaymanNote:
    """生成旋律分析的 ``layman`` 子对象（§3.4.2 的输出结构）。"""
    range_text = ""
    if melody.range and len(melody.range) == 2:
        from app.services.parser.melody import note_to_midi

        try:
            span = note_to_midi(melody.range[1]) - note_to_midi(melody.range[0])
        except Exception:  # noqa: BLE001 - 音名异常时退化为按数量描述
            span = 0
        if span <= 7:
            range_text = "音域不宽，比较集中，容易唱"
        elif span <= 12:
            range_text = "音域适中，属于常见的歌唱范围"
        else:
            range_text = "音域比较宽，演唱会有一定难度"

    contour_text = CONTOUR_LAYMAN.get(melody.contour, "走向比较平缓")

    character = (
        "有重复的片段，容易记住" if melody.repetition else "没有明显重复，记忆点需要再加强"
    )

    return LaymanNote(range=range_text, contour=contour_text, character=character)
