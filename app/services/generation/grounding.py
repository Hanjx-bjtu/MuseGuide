"""Grounding 校验器 —— 把「幻觉」变成机器可判定的问题。

**这是本 MVP 最被低估的技术亮点**（``实现阶段计划.md`` §8.3）。

``MVP计划.md`` §7.2 要求评估 Groundedness，§9 第 6 条要求「每个建议附带通俗解释
和理论依据」，§15 强调「所有理论性建议尽可能建立在检索到的证据上」。
这些要求看似只能靠人看，实际上**大部分可以被规则判定**：

===========================  ==================================================
校验项                       判定依据
===========================  ==================================================
调外和弦                    建议的和弦是否在声称调性下成立（复用 ``is_in_key``）
引用越界                    ``[来源N]`` 的 N 是否超出 evidence 长度
弱依据                      证据 tags 与建议关键词的重叠度是否过低
目标不符                    建议是否真的回应了用户创作目标
缺少通俗解释                零基础模式下是否缺少通俗说明（§3.8.3）
===========================  ==================================================

效果是：报告里可以出现「**编造引用数 = 0**」「**调外和弦数 = 0**」这样的硬指标，
而不是「我们人工检查过了」。
"""

from __future__ import annotations

import re

from app.core.brief import CreativeIntent
from app.core.evidence import Evidence, GroundingIssue, GroundingReport
from app.core.plan import Advice, AdviceOption

#: 引用标记：``[1]`` / ``[来源1]`` / ``[来源 1]``
CITATION_PATTERN = re.compile(r"\[(?:来源\s*)?(\d+)\]")

#: 从和弦名里提取调外判定所需的元素
CHORD_TOKEN_PATTERN = re.compile(r"[A-G][#b]?")

#: 弱依据的判定比例：建议词元中命中证据词元的**占比**低于此值即告警。
#:
#: 为什么用比例而不是「是否为空」：中文 bigram 会产生大量噪声片段，
#: 任何两句中文都极易共享一两个单字（实测「改成三拍子圆舞曲」与
#: 「调式借用」的证据只共享一个「弦」字）。若以「有重叠即通过」判定，
#: 这个校验等于永远不触发 —— 一个从不报警的指标是没有价值的。
WEAK_GROUNDING_MIN_RATIO = 0.12

#: 停用词（不参与目标匹配度计算）
_STOPWORDS = frozenset(
    "的 了 是 在 和 与 或 把 让 要 想 会 能 就 都 也 很 更 那 这 有 没 不 但 而 一个 一些".split()
)


def _tokens(text: str) -> set[str]:
    """极简词元化：中文按字符 bigram，英文/和弦按词。用于重叠度计算。"""
    text = (text or "").lower()
    tokens: set[str] = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9#/]+", text):
        if re.match(r"[\u4e00-\u9fff]", chunk):
            if len(chunk) == 1:
                if chunk not in _STOPWORDS:
                    tokens.add(chunk)
            else:
                tokens.update(
                    chunk[i : i + 2] for i in range(len(chunk) - 1)
                )
                tokens.update(c for c in chunk if c not in _STOPWORDS)
        else:
            if chunk not in _STOPWORDS:
                tokens.add(chunk)
    return tokens


# --------------------------------------------------------------------------- #
# 单项校验
# --------------------------------------------------------------------------- #


def check_citations(text: str, evidence: list[Evidence]) -> list[GroundingIssue]:
    """引用编号是否越界。

    ``[来源3]`` 在只有 2 条证据时是**编造的引用** —— 这是最典型的幻觉形式，
    也是最能体现「RAG 让建议可追溯」这一价值的检查项。
    """
    issues: list[GroundingIssue] = []
    for match in CITATION_PATTERN.finditer(text or ""):
        number = int(match.group(1))
        if number < 1 or number > len(evidence):
            issues.append(
                GroundingIssue(
                    kind="citation_out_of_range",
                    detail=f"引用了 [{number}]，但本次只有 {len(evidence)} 条证据",
                    severity="error",
                )
            )
    return issues


def check_chords_in_key(option: AdviceOption, key: str | None) -> list[GroundingIssue]:
    """建议中的和弦是否在声称的调性下成立。

    复用 ``analyzer.roman.is_in_key`` —— 它同时校验根音与和弦性质，
    因此能识别出 ``Fm`` 这类「根音在调内但性质不符」的借用和弦。

    **借用和弦不算错**：它是合法且常用的手法。这里只在「既不在调内、
    也不是已知的借用和弦」时才报 error，否则记为 warning 供人复核。
    """
    if not key:
        return []

    from app.services.analyzer.roman import is_in_key
    from app.services.parser.chords import ChordParseError, parse_chord

    issues: list[GroundingIssue] = []
    for chord_name in option.chords:
        if not CHORD_TOKEN_PATTERN.search(chord_name or ""):
            continue
        try:
            chord = parse_chord(chord_name)
        except ChordParseError:
            issues.append(
                GroundingIssue(
                    kind="out_of_key",
                    detail=f"建议中的和弦无法解析：{chord_name}",
                    severity="error",
                )
            )
            continue

        if is_in_key(chord, key):
            continue

        # 判断是否为常见的借用和弦（同主音小调的 iv / bVI / bVII / bIII）
        borrowed = _is_common_borrowed_chord(chord, key)
        issues.append(
            GroundingIssue(
                kind="out_of_key",
                detail=(
                    f"{chord_name} 不属于 {key}"
                    + ("（属于常见的借用和弦，属正常手法）" if borrowed else "（且不是常见借用和弦）")
                ),
                severity="warning" if borrowed else "error",
            )
        )
    return issues


#: 常见借用和弦：从同主音小调借来的 iv / bVI / bVII / bIII
_BORROWED_DEGREE_INTERVALS = {3: "bIII", 5: "iv", 8: "bVI", 10: "bVII"}


def _is_common_borrowed_chord(chord, key: str) -> bool:
    """是否为常见的调式借用和弦（大调借用同主音小调的和弦）。"""
    from app.services.analyzer.key import parse_key
    from app.services.parser.chords import NOTE_TO_PITCH

    tonic, mode = parse_key(key)
    if mode != "Major":
        # 小调借用大调的情况较少，仅放行 I（Picardy third）
        return chord.quality == "major" and chord.root == tonic

    tonic_pitch = NOTE_TO_PITCH.get(tonic)
    root_pitch = NOTE_TO_PITCH.get(chord.root)
    if tonic_pitch is None or root_pitch is None:
        return False

    offset = (root_pitch - tonic_pitch) % 12
    if offset == 5:  # iv：小三和弦
        return chord.quality in ("minor", "min7")
    if offset in (8, 10, 3):  # bVI / bVII / bIII：大三和弦
        return chord.quality in ("major", "maj7", "dom7")
    return False


def check_weak_grounding(option: AdviceOption, evidence: list[Evidence]) -> list[GroundingIssue]:
    """建议是否真的有证据支撑。

    判定方式：把建议的文本（feature + reason + theory）与**被引用的**证据
    的 tags / 标题 / 正文做词元重叠，计算**建议词元被证据覆盖的比例**。
    比例过低说明「这条建议跟检索到的知识基本没关系」——
    它可能是模型自己发挥的，而不是来自知识库。

    用比例而非「是否为空」的理由见 ``WEAK_GROUNDING_MIN_RATIO`` 的说明：
    中文 bigram 下任意两句话都极易共享一两个单字，
    「有重叠即通过」会让这条校验永不触发。
    """
    if not evidence:
        # 没有证据时不做此项判定，避免把「无知识库」误报为幻觉
        return []

    cited = _cited_evidence(option, evidence) or evidence
    evidence_tokens: set[str] = set()
    for item in cited:
        evidence_tokens |= _tokens(item.title)
        evidence_tokens |= _tokens(item.layman_title or "")
        for tag in item.tags:
            evidence_tokens |= _tokens(tag)
        evidence_tokens |= _tokens(item.content[:400])

    option_tokens = _tokens(f"{option.feature} {option.reason} {' '.join(option.theory)}")
    if not option_tokens:
        return []

    overlap = option_tokens & evidence_tokens
    ratio = len(overlap) / len(option_tokens)

    if ratio < WEAK_GROUNDING_MIN_RATIO:
        return [
            GroundingIssue(
                kind="weak_grounding",
                detail=(
                    f"建议「{option.label}」与检索到的证据重合度仅 {ratio:.1%}"
                    f"（阈值 {WEAK_GROUNDING_MIN_RATIO:.0%}），可能并非来自知识库"
                ),
                severity="warning",
            )
        ]
    return []


def _cited_evidence(option: AdviceOption, evidence: list[Evidence]) -> list[Evidence]:
    """取出该建议显式引用的证据（``[来源N]``）。"""
    text = f"{option.feature} {option.reason} {' '.join(option.theory)}"
    numbers = {int(m.group(1)) for m in CITATION_PATTERN.finditer(text)}
    return [evidence[n - 1] for n in sorted(numbers) if 1 <= n <= len(evidence)]


def check_goal_match(option: AdviceOption, intent: CreativeIntent, goal_text: str) -> list[GroundingIssue]:
    """建议是否真的回应用户目标。

    ``MVP计划.md`` §7.2 的 Relevance 维度。用户说「保持温暖，但不要太普通」，
    而建议只在讲「如何增加张力」——那这条建议虽然有理论依据，却没回答问题。

    **判定用受控概念词而非原始 n-gram。** 实测教训：用中文 bigram 求重叠时，
    「保持温暖，但不要太普通」会切出 ``持温``、``但不`` 这类无意义片段，
    而一条真正回应了「不要太普通」的建议（如「使用借用和弦」）因为不含
    「普通」二字，反而被判为「未体现目标」—— **假阳性比漏报更有害**，
    它会让人不再信任这个指标。

    因此这里改为识别**目标中出现的音乐概念**，再看建议是否落在同一概念域内。
    """
    concepts = _goal_concepts(goal_text, intent)
    if not concepts:
        return []

    option_tokens = _tokens(f"{option.label} {option.feature} {option.reason}")

    # 判定一：建议命中任一概念域的关键词 → 视为回应了目标
    for concept in concepts:
        if option_tokens & _CONCEPT_LEXICON.get(concept, frozenset()):
            return []

    # 判定二：建议与目标原文有足够的字面重合 → 也算回应（兜住同义表达）
    goal_tokens = _tokens(goal_text)
    if goal_tokens:
        ratio = len(option_tokens & goal_tokens) / len(goal_tokens)
        if ratio >= GOAL_MATCH_MIN_RATIO:
            return []

    # 都没命中时才告警；这是 warning 而非 error —— 相关性能弱，
    # 但不像「引用不存在来源」那样是明确的错误。
    return [
        GroundingIssue(
            kind="goal_mismatch",
            detail=f"建议「{option.label}」未体现用户目标中的任何要点（目标概念：{sorted(concepts)}）",
            severity="warning",
        )
    ]


#: 建议与目标原文的字面重合比例下限。
#:
#: 与 ``WEAK_GROUNDING_MIN_RATIO`` 同理：中文 bigram 下「有重叠即通过」
#: 等于永不触发。这里取一个宽松但非零的门槛。
GOAL_MATCH_MIN_RATIO = 0.25


#: 受控概念词典：把用户目标里的诉求词映射到一组可识别的关键词。
#:
#: 这张表是本项目「Intent Mapping」（§3.2）在**校验侧**的对应物：
#: 映射层负责把日常语言变成音乐概念，校验层负责检查建议是否落回同一概念。
#:
#: ⚠️ **不要放入单字关键词。** 实测教训：给「普通」放一个 ``换`` 之后，
#: 「把编曲**换**成管弦乐配器」这种完全跑题的建议也被判为「回应了目标」。
#: 单字在中文里几乎必然误命中，必须使用 ≥2 字的词。
_CONCEPT_LEXICON: dict[str, frozenset[str]] = {
    "普通": frozenset(
        {"色彩", "七和弦", "借用", "低音", "变化", "特别", "新颖", "替换", "级进", "转位", "音色"}
    ),
    "温暖": frozenset({"温暖", "明亮", "大调", "柔和", "稳定", "保持"}),
    "伤感": frozenset({"伤感", "小调", "忧伤", "忧郁", "下行", "阴影"}),
    "释然": frozenset({"释然", "开阔", "大调", "释放", "解决", "终止", "向前"}),
    "高潮": frozenset({"高潮", "张力", "推进", "音区", "力度", "上行", "副属", "爆发"}),
    "收尾": frozenset({"终止", "句号", "结束", "解决", "收束"}),
    "连贯": frozenset({"低音", "级进", "连贯", "线条", "下行", "转位"}),
    "梦幻": frozenset({"七和弦", "延伸", "柔和", "梦幻", "漂浮"}),
    "安静": frozenset({"慢速", "稀疏", "留白", "柔和", "简化"}),
}


def _goal_concepts(goal_text: str, intent: CreativeIntent) -> set[str]:
    """从用户目标中识别出受控概念（与 §3.2.2 的映射表同源）。"""
    text = goal_text or ""
    concepts: set[str] = set()

    for concept in _CONCEPT_LEXICON:
        if concept in text:
            concepts.add(concept)

    # 情绪标签直接作为概念
    for emotion in intent.emotion:
        if emotion in _CONCEPT_LEXICON:
            concepts.add(emotion)

    # 和声需求映射到概念
    for need in intent.harmony_needs:
        if "色彩" in need:
            concepts.add("普通")
        if "终止" in need:
            concepts.add("收尾")
        if "张力" in need or "推进" in need:
            concepts.add("高潮")
        if "转折" in need:
            concepts.update({"伤感", "释然"})

    return concepts


def check_constraints(option: AdviceOption, constraints: list[str]) -> list[GroundingIssue]:
    """用户明确要求保持的东西是否被破坏。

    例如用户说「保持温暖」，而建议把整段改成小调 —— 那是**违反了用户的硬约束**，
    比「建议不够好」严重得多，因此标为 error。
    """
    issues: list[GroundingIssue] = []
    if not constraints:
        return issues

    from app.services.analyzer.roman import is_in_key
    from app.services.parser.chords import ChordParseError, parse_chord

    text = f"{option.feature} {option.reason}"
    for constraint in constraints:
        if "温暖" in constraint or "明亮" in constraint:
            if any(w in text for w in ("转为小调", "改成小调", "全部小调", "改成忧郁", "改成悲伤")):
                issues.append(
                    GroundingIssue(
                        kind="goal_mismatch",
                        detail=f"建议可能违反用户约束「{constraint}」：把整体转向了小调",
                        severity="error",
                    )
                )
    return issues


def check_layman_present(option: AdviceOption, level: str) -> list[GroundingIssue]:
    """零基础模式下每条建议都必须有通俗解释（§3.8.3 / §9 第 6 条）。"""
    if level != "zero":
        return []
    if not (option.reason or "").strip():
        return [
            GroundingIssue(
                kind="missing_layman",
                detail=f"建议「{option.label}」缺少通俗解释",
                severity="error",
            )
        ]
    return []


def check_banned_terms(option: AdviceOption, level: str) -> list[GroundingIssue]:
    """零基础模式下不得出现未解释的专业术语（§3.8.3）。"""
    if level != "zero":
        return []
    from app.services.layman import find_banned_terms

    text = f"{option.feature} {option.reason}"
    banned = find_banned_terms(text)
    if banned:
        return [
            GroundingIssue(
                kind="missing_layman",
                detail=f"建议「{option.label}」含零基础禁区术语：{banned}",
                severity="warning",
            )
        ]
    return []


# --------------------------------------------------------------------------- #
# 编排
# --------------------------------------------------------------------------- #


def verify_advice(
    advice: Advice,
    *,
    evidence: list[Evidence] | None = None,
    intent: CreativeIntent | None = None,
    goal_text: str = "",
    key: str | None = None,
    constraints: list[str] | None = None,
    level: str = "some",
) -> GroundingReport:
    """对整份建议做 Grounding 校验，返回可统计的报告。

    :param advice: 待校验的建议
    :param evidence: 本次检索到的证据（用于引用越界与弱依据判定）
    :param intent: Intent Mapping 结果（用于目标匹配度）
    :param key: 作品调性（用于调外和弦判定）
    :param constraints: 用户明确要求保持的约束
    :param level: 用户水平（零基础档会额外检查通俗解释与禁区术语）
    """
    evidence = evidence if evidence is not None else list(advice.evidence)
    intent = intent or CreativeIntent()
    constraints = constraints or []

    issues: list[GroundingIssue] = []

    # 整体层面
    issues.extend(check_citations(advice.analysis or "", evidence))
    for problem in advice.problems:
        issues.extend(check_citations(problem, evidence))
    # 引用了不存在的证据编号时，evidence 列表本身可能为空 —— 也应记为越界
    if not evidence:
        for text in [advice.analysis, *advice.problems]:
            if CITATION_PATTERN.search(text or ""):
                issues.append(
                    GroundingIssue(
                        kind="citation_out_of_range",
                        detail="建议引用了来源，但本次请求没有任何证据",
                        severity="error",
                    )
                )

    # 逐条建议
    for option in advice.options:
        issues.extend(check_chords_in_key(option, key))
        issues.extend(check_weak_grounding(option, evidence))
        issues.extend(check_goal_match(option, intent, goal_text))
        issues.extend(check_constraints(option, constraints))
        issues.extend(check_layman_present(option, level))
        issues.extend(check_banned_terms(option, level))
        issues.extend(check_citations(option.feature, evidence))
        issues.extend(check_citations(option.reason, evidence))

    return GroundingReport(ok=not any(i.severity == "error" for i in issues), issues=issues)


def summarize(report: GroundingReport) -> dict[str, int]:
    """把报告压成可写入实验报告的计数（供 P6 统计）。"""
    counts: dict[str, int] = {
        "total": len(report.issues),
        "errors": len([i for i in report.issues if i.severity == "error"]),
        "citations_fabricated": 0,
        "chords_out_of_key": 0,
        "weak_grounding": 0,
        "goal_mismatch": 0,
        "missing_layman": 0,
    }
    for issue in report.issues:
        if issue.kind == "citation_out_of_range":
            counts["citations_fabricated"] += 1
        elif issue.kind == "out_of_key" and issue.severity == "error":
            counts["chords_out_of_key"] += 1
        elif issue.kind in counts:
            counts[issue.kind] += 1
    return counts
