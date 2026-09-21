"""Mock Provider —— 使 P0~P4 的测试在**无 Key、无网络**环境下全绿。

**这不是「假的占位」**：它返回结构合法、确定性（同输入同输出）的结果，
使契约往返、结构合规、降级路径、输出长度约束等测试可以真正断言。

依据：ADR-0004。
"""

from __future__ import annotations

import hashlib
import json
import re


class MockLLMProvider:
    """确定性 LLM。

    根据 prompt 中出现的标记返回对应的合法 JSON：
    * 含 ``[任务]`` 且提到「起步方案」→ 返回 §3.3.3 结构的起步方案
    * 含 ``[任务]`` 且提到「修改方向」→ 返回 §3.9.3 结构的建议
    * 含「拆解」→ 返回 §3.5.2 结构的 query 分解
    * 含「映射到音乐概念」→ 返回 §3.2.3 结构的意图映射
    """

    name = "mock"
    available = True

    def complete(self, prompt: str, *, system: str | None = None, json_mode: bool = False) -> str:
        if "映射到音乐概念" in prompt:
            return json.dumps(_intent_payload(prompt), ensure_ascii=False)
        if "拆解" in prompt and "query" in prompt.lower():
            return json.dumps(_query_payload(prompt), ensure_ascii=False)
        if "修改方向" in prompt or "分析当前作品特征" in prompt:
            return json.dumps(_advice_payload(), ensure_ascii=False)
        if "起步方案" in prompt or "设计一组基础和弦" in prompt:
            return json.dumps(_starter_payload(prompt), ensure_ascii=False)
        return json.dumps({"text": "（Mock 输出）"}, ensure_ascii=False)


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def _intent_payload(prompt: str) -> dict:
    """从 prompt 里粗暴地抽取情绪词，保证 Mock 输出与输入相关（而非恒定）。"""
    emotions = [w for w in ("伤感", "释然", "温暖", "忧郁", "激昂", "温柔", "轻快", "梦幻") if w in prompt]
    if not emotions:
        emotions = ["温暖"]
    style = next((s for s in ("民谣", "流行", "摇滚", "钢琴", "电子", "爵士") if s in prompt), "流行")
    tempo = next((t for t in ("很慢", "中等偏慢", "中等") if t in prompt), "中等偏慢")
    return {
        "emotion": emotions,
        "style": style,
        "tempo_feel": tempo,
        "key_preference": "大调（释然感）" if "释然" in prompt else "小调（忧伤感）",
        "harmony_needs": ["需要情绪转折"] if len(emotions) > 1 else ["需要和声色彩"],
    }


def _query_payload(prompt: str) -> dict:
    return {
        "intent": "从零起步创作",
        "goal": "情绪转折",
        "object": "整首歌",
        "queries": [
            "小调到大调的情绪转向和弦进行",
            "民谣风格常见和声框架",
            "主歌副歌情绪对比的实现方式",
        ],
    }


def _starter_payload(prompt: str) -> dict:
    key = "C Major"
    if "小调" in prompt and "大调" not in prompt:
        key = "A Minor"
    tempo = 82
    for word, bpm in (("很慢", 60), ("中等偏慢", 82), ("中等", 100)):
        if word in prompt:
            tempo = bpm
            break
    return {
        "key": key,
        "key_explanation": "听起来明亮温暖，适合「释然」的感觉。",
        "tempo": tempo,
        "tempo_explanation": "中等偏慢，像散步一样，适合抒情歌。",
        "sections": [
            {
                "name": "主歌",
                "chords": ["Am", "F", "C", "G"],
                "emotion": "偏伤感",
                "explanation": "从小调和弦开始，听起来柔和忧伤。",
            },
            {
                "name": "副歌",
                "chords": ["C", "G", "Am", "F"],
                "emotion": "转向释然",
                "explanation": "从大调和弦开始，听起来更温暖开阔。",
            },
        ],
        "why": "伤感靠小调和弦实现，释然靠回到大调和弦实现，两者形成情绪转折。",
        "adjust_hints": ["把副歌最后的 F 换成 Fm，会多一丝忧伤", "给和弦加上七音，听感更柔和梦幻"],
    }


def _advice_payload() -> dict:
    return {
        "analysis": "当前进行为 I → V → vi → IV，属于常见流行和声，功能关系稳定，但色彩和弦较少。",
        "problems": ["和声张力较低，缺少额外色彩。", "低音线基本停在根音，缺少流动感。"],
        "options": [
            {
                "label": "建议 A：增加七和弦",
                "chords": ["Cmaj7", "G/B", "Am7", "Fmaj7"],
                "feature": "保持原有功能关系，通过七和弦与低音线增加色彩。",
                "reason": "听起来会更柔和、更梦幻，但稳定的感觉不会变。",
                "theory": ["Seventh Chords", "Voice Leading"],
            },
            {
                "label": "建议 B：使用借用和弦",
                "chords": ["C", "G", "Am", "Fm"],
                "feature": "Fm 来自平行小调，引入阴影感但不破坏整体温暖。",
                "reason": "最后一下会突然多一丝忧伤，像回忆闪过。",
                "theory": ["Modal Interchange"],
            },
            {
                "label": "建议 C：改变低音走法",
                "chords": ["C", "G/B", "Am", "F/A"],
                "feature": "低音级进下行，增强连贯性。",
                "reason": "听起来更连贯，像一条线把四个和弦串起来。",
                "theory": ["Voice Leading", "Bass Line"],
            },
        ],
    }


class MockEmbeddingProvider:
    """确定性哈希嵌入。

    ⚠️ **仅用于让检索链路可跑通与可测试，不具备真实语义能力。**
    使用它产出的检索指标**不得写进结论报告**（ADR-0008）。
    """

    name = "mock-hash"
    dim = 64

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _tokenize(text)
        for tok in tokens:
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16)
            vec[h % self.dim] += 1.0
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]


def _tokenize(text: str) -> list[str]:
    """中文按字符 bigram + 英文按词 —— 零依赖的分词兜底。"""
    tokens: list[str] = []
    for chunk in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9#/]+", text):
        if re.match(r"[\u4e00-\u9fff]", chunk):
            tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1)) if len(chunk) > 1 else tokens.append(chunk)
        else:
            tokens.append(chunk.lower())
    return tokens or [text]
