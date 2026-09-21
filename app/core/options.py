"""受控词表（§3.10.2 / §3.10.3）。

**规则：** 每个选项必须同时提供「用户能读懂的说法」与「音乐概念名」——
界面直接渲染前者，Prompt 使用后者。缺少任一项的选项不得加入本表。

这正是零基础友好的实现方式：用户不需要理解术语也能做选择（§8 风险应对
「选择式输入兜底，不依赖用户主动输入」）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Option(BaseModel):
    """一个可选项。

    ``label`` 是界面文案（用户视角），``concept`` 是音乐概念（系统视角），
    ``hint`` 是通俗说明。

    ``bpm`` / ``aliases`` 只对部分选项有意义（速度项有 bpm，情绪项有别名）。
    """

    label: str = Field(description="用户能读懂的说法")
    concept: str = Field(description="对应的音乐概念")
    hint: str = Field(default="", description="通俗说明")
    bpm: int | None = Field(default=None, description="速度项的建议 BPM")
    aliases: list[str] = Field(default_factory=list, description="LLM 可能输出的同义写法")

    def __str__(self) -> str:  # pragma: no cover - 便捷显示
        return self.label


#: 速度选项（§3.10.3：附「像翻相册 / 像散步 / 像走路」这类日常类比）
TEMPO_OPTIONS: list[Option] = [
    Option(label="很慢，像翻相册", concept="很慢", hint="约 60 BPM，适合回忆、独白式的表达", bpm=60),
    Option(label="中等偏慢，像散步", concept="中等偏慢", hint="约 82 BPM，适合抒情歌", bpm=82),
    Option(label="中等，像走路", concept="中等", hint="约 100 BPM，适合大多数流行歌", bpm=100),
]

#: 情绪选项（§3.2.2 的映射规则覆盖这些情绪词）
EMOTION_OPTIONS: list[Option] = [
    Option(label="温柔", concept="温柔", hint="大调 + 慢速，听起来柔和"),
    Option(label="伤感", concept="伤感", hint="小调倾向，听起来柔和忧伤"),
    Option(label="释然", concept="释然", hint="大调收束，听起来开阔明亮"),
    Option(label="激昂", concept="激昂", hint="大调 + 快速 + 强力度"),
    Option(label="忧郁", concept="忧郁", hint="小调 + 下行旋律"),
    Option(label="轻快", concept="轻快", hint="中速 + 明亮和声"),
    Option(label="梦幻", concept="梦幻", hint="七和弦 + 缓速变化"),
    Option(label="温暖", concept="温暖", hint="大调和声 + 纯五度关系"),
]

#: 风格选项（§3.10.2 的「温柔民谣 / 流行抒情 / 钢琴曲 / 轻快节奏 / 梦幻电子」）
STYLE_OPTIONS: list[Option] = [
    Option(label="流行抒情", concept="流行", hint="电台里那种，和弦走向比较常见", aliases=["pop", "抒情", "流行歌"]),
    Option(label="温柔民谣", concept="民谣", hint="像抱着吉他弹唱", aliases=["folk", "吉他", "民谣风"]),
    Option(label="轻摇滚", concept="摇滚", hint="带点力量感", aliases=["rock", "乐队"]),
    Option(label="钢琴曲", concept="钢琴", hint="以钢琴为主，安静、留白多", aliases=["piano", "纯音乐", "器乐"]),
    Option(label="梦幻电子", concept="电子", hint="氛围铺底，听感漂浮", aliases=["electronic", "氛围", "synth"]),
    Option(label="爵士小调", concept="爵士", hint="和声色彩丰富，带点慵懒", aliases=["jazz", "蓝调"]),
]

#: 首页的大方向快捷按钮（§3.10.2：温柔民谣 / 流行抒情 / 钢琴曲 / 轻快节奏 / 梦幻电子 / more）
QUICK_START_KEYS: list[str] = [
    "温柔民谣",
    "流行抒情",
    "钢琴曲",
    "轻快节奏",
    "梦幻电子",
]


# --------------------------------------------------------------------------- #
# 查表工具
# --------------------------------------------------------------------------- #


def _match(option: Option, value: str) -> bool:
    """一个选项是否匹配给定写法（界面文案 / 概念名 / 别名，大小写不敏感）。"""
    needle = value.strip().lower()
    if not needle:
        return False
    candidates = [option.label, option.concept, *option.aliases]
    return any(needle == c.strip().lower() for c in candidates)


def find_option(options: list[Option], value: str | None) -> Option | None:
    """在给定词表中按任一写法查找。"""
    if not value:
        return None
    for opt in options:
        if _match(opt, value):
            return opt
    return None


def tempo_option_by_label(label: str | None) -> Option | None:
    """按界面文案 / 概念名 / 别名查速度选项。

    用户可能从界面、LLM 输出或历史记录里带回任一写法，因此三处都要认。
    """
    return find_option(TEMPO_OPTIONS, label)


def emotion_option(value: str | None) -> Option | None:
    return find_option(EMOTION_OPTIONS, value)


def style_option(value: str | None) -> Option | None:
    return find_option(STYLE_OPTIONS, value)


def tempo_bpm(concept: str | None) -> int:
    """概念名 → 建议 BPM（§3.3.3 的 ``tempo`` 字段）。

    默认 82（中等偏慢）—— 与 §3.3.3 示例一致，也是抒情歌最常用的速度。
    """
    opt = tempo_option_by_label(concept)
    return opt.bpm if opt and opt.bpm else 82


def emotion_concepts() -> list[str]:
    return [o.concept for o in EMOTION_OPTIONS]


def style_concepts() -> list[str]:
    return [o.concept for o in STYLE_OPTIONS]


def options_as_payload(options: list[Option]) -> list[dict]:
    """下发给前端的选项载荷（§3.10.2/§3.10.3：每个选项都必须带通俗说明）。"""
    return [o.model_dump() for o in options]


def selection_payload() -> dict[str, list[dict]]:
    """一次性下发三组选择式输入，供零基础界面渲染。"""
    return {
        "emotion": options_as_payload(EMOTION_OPTIONS),
        "style": options_as_payload(STYLE_OPTIONS),
        "tempo": options_as_payload(TEMPO_OPTIONS),
    }
