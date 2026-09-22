"""生成检索评测集的初稿，供人工核对。

**为什么先生成再人工改：** 60+ 道题全部手写成本高，但**未加核对的
自动标注会让评测失去意义**（这正是 v1 项目的教训：20 题、gold 标注不严，
导致宽松口径全员饱和到 1.000，结论既无法证明也无法证伪）。

因此这里的做法是：脚本按「条目 → 用户可能怎么问」生成候选题目与
gold 列表，**人工逐条复核后**再冻结进 ``datasets/retrieval_qa/v1.json``。
生成结果只作为起点，不作为最终数据。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "knowledge")

from app.services.kb import load_entries  # noqa: E402

#: 条目 → 用户可能提的问题（人工设计的问法，覆盖零基础与进阶两种口吻）
QUESTION_TEMPLATES: dict[str, list[str]] = {
    "harmony.modal_interchange.01": [
        "怎么让和弦听起来更有色彩但不改变整体感觉",
        "从别的调借和弦是什么手法",
    ],
    "harmony.seventh_chords.01": [
        "给和弦加七音会有什么效果",
        "Cmaj7 和 C 有什么区别",
    ],
    "harmony.progression_basics.01": [
        "和弦进行的基本逻辑是什么",
        "为什么和弦有稳定和不稳定的区别",
    ],
    "harmony.cadence_function.01": [
        "怎样让歌曲结尾有结束感",
        "终止式有哪几种",
    ],
    "harmony.secondary_dominant.01": [
        "副属和弦怎么用",
        "想在副歌前推一下情绪该用什么和弦",
    ],
    "harmony.chord_color.01": [
        "让和弦不那么普通的办法有哪些",
        "如何在保持原有走向的前提下增加色彩",
    ],
    "melody.tension_release.01": [
        "旋律怎么做出紧张和释放的感觉",
        "怎样让旋律听起来有收尾感",
    ],
    "melody.contour.01": [
        "旋律往上走和往下走有什么区别",
        "怎么让副歌的旋律更突出",
    ],
    "melody.range_emotion.01": [
        "音域宽窄会影响情绪吗",
        "副歌应该唱得更高吗",
    ],
    "melody.motif_development.01": [
        "怎么让旋律好记又不重复",
        "什么是模进",
    ],
    "emotion.warm_harmony.01": [
        "怎样让歌听起来温暖",
        "想做出治愈的感觉该怎么做",
    ],
    "emotion.melancholy.01": [
        "怎样让歌听起来忧郁",
        "想写一首很丧的歌",
    ],
    "emotion.sad_to_hopeful.01": [
        "伤感但最后释然的感觉怎么实现",
        "怎样让歌曲从悲伤转向希望",
    ],
    "emotion.tension_resolution.01": [
        "怎么做出高潮感",
        "张力与释放是什么意思",
    ],
    "emotion.farewell_scene.01": [
        "写一首关于毕业告别的歌",
        "离别主题的歌应该怎么写",
    ],
    "starter.how_to_start.01": [
        "我想写歌但完全不知道从哪开始",
        "写第一首歌需要做哪些决定",
    ],
    "starter.choosing_key_and_tempo.01": [
        "第一首歌选什么调比较好",
        "速度应该怎么定",
    ],
    "starter.common_progressions.01": [
        "民谣常用的和弦走向是什么",
        "有哪些不会出错的和弦进行",
    ],
    "starter.song_structure.01": [
        "一首歌的基本结构是怎样的",
        "主歌和副歌有什么区别",
    ],
    "starter.first_song_landing.01": [
        "拿到和弦之后下一步该做什么",
        "怎么把和弦变成一首歌",
    ],
    "examples.pop_progressions.01": [
        "流行歌常用的和弦进行有哪些",
        "C G Am F 是不是很常见",
    ],
    "examples.folk_progressions.01": [
        "民谣和弦进行案例",
        "吉他弹唱常用的和弦走向",
    ],
    "examples.jpop_progressions.01": [
        "日系歌曲的和声有什么特点",
        "什么是王道进行",
    ],
    "examples.bass_line.01": [
        "低音线怎么走比较好听",
        "如何让和弦连接更连贯",
    ],
    "layman.what_is_chord.01": [
        "和弦是什么",
        "完全不懂乐理，和弦是什么意思",
    ],
    "layman.what_is_key.01": [
        "调性是什么意思",
        "大调和小调有什么区别",
    ],
    "layman.what_is_progression.01": [
        "和弦进行是什么",
        "为什么歌里要换和弦",
    ],
    "layman.what_is_cadence.01": [
        "终止式是什么",
        "为什么我的歌结尾感觉没说完",
    ],
    "layman.emotion_and_harmony.01": [
        "为什么换个和弦情绪就变了",
        "音乐是怎么影响情绪的",
    ],
}


def main() -> int:
    entries = load_entries()
    by_id = {e["id"]: e for e in entries}

    cases = []
    missing = []
    for entry_id, questions in QUESTION_TEMPLATES.items():
        if entry_id not in by_id:
            missing.append(entry_id)
            continue
        entry = by_id[entry_id]
        for question in questions:
            cases.append(
                {
                    "question": question,
                    "gold": [entry_id],
                    "_draft_from": entry["id"],
                    "_category": entry["category"],
                    "_title": entry["title"],
                }
            )

    output = Path("datasets/retrieval_qa/_draft.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "_comment": "自动生成的初稿 —— 必须人工复核并补充多 gold 后，再冻结为 v1.json",
                "count": len(cases),
                "cases": cases,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"生成 {len(cases)} 条初稿 -> {output}")
    if missing:
        print(f"⚠️ 模板引用了不存在的条目：{missing}")
    print(f"知识库共 {len(entries)} 条，其中 {len({c['_draft_from'] for c in cases})} 条被题目覆盖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
