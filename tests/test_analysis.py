"""分析层金标准测试（P2.3 / 阶段门 G2）。

**这是「评测左移」的落点：** 在写分析器之前先建金标准集，
使「调性判断 ≥ 90%」「级数标注 ≥ 85%」从愿望变成 CI 里的断言。

依据：``实现阶段计划.md`` §6.3、``docs/ACCEPTANCE.md`` §3.2
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.artifact import MusicArtifact
from app.services.analyzer import analyze, build_artifact
from app.services.analyzer.key import detect_key_from_chords, key_pitch_classes, parse_key
from app.services.parser.chords import parse_progression
from app.services.parser.melody import parse_melody

GOLD_PATH = Path(__file__).resolve().parents[1] / "datasets" / "analysis_gold" / "v1.json"


@pytest.fixture(scope="module")
def gold() -> dict:
    return json.loads(GOLD_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 目标：调性判断准确率 ≥ 0.90，级数标注 ≥ 0.85（docs/ACCEPTANCE.md §3.2）
# --------------------------------------------------------------------------- #


def test_key_detection_accuracy_on_gold(gold):
    """调性判断准确率 —— 门槛 90%。

    标注了 ``acceptable_keys`` 的案例属于**真实的调性歧义**
    （关系大小调共享全部音级，且进行中不含属和弦或导音），
    这些案例接受任意一个可接受答案。

    这样做而不是调参去凑满分，是因为：**把歧义案例强行标注为唯一答案，
    会让「准确率」这个数字失去意义** —— 那只是在测量标注者的偏好，
    而不是分析器的能力。
    """
    cases = gold["cases"]
    correct = 0
    ambiguous = 0
    misses = []
    for case in cases:
        chords = parse_progression(case["chords"])
        detected = detect_key_from_chords(chords)
        acceptable = case.get("acceptable_keys") or [case["key"]]
        if len(acceptable) > 1:
            ambiguous += 1
        if detected and detected.key in acceptable:
            correct += 1
        else:
            misses.append(
                (case["id"], case["chords"], acceptable, detected.key if detected else None)
            )

    accuracy = correct / len(cases)
    assert accuracy >= 0.90, f"调性准确率 {accuracy:.3f} < 0.90，未命中：{misses}"


def test_gold_ambiguity_is_declared_not_hidden(gold):
    """歧义案例必须在数据里显式声明，而不是靠放宽断言蒙混过去。

    这条测试的作用是：**防止有人为了让准确率变高而悄悄扩大可接受集合。**
    只有当 `acceptable_keys` 确实对应关系大小调（音级集合相同）时才允许存在。
    """
    for case in gold["cases"]:
        acceptable = case.get("acceptable_keys")
        if not acceptable:
            continue
        assert len(acceptable) <= 2, f"{case['id']}: 可接受答案过多，疑似放宽标准"
        # 关系大小调的音级集合必须完全相同
        pitch_sets = [frozenset(key_pitch_classes(k)) for k in acceptable]
        assert all(ps == pitch_sets[0] for ps in pitch_sets), (
            f"{case['id']}: {acceptable} 不是关系大小调，不能作为歧义案例"
        )


def test_roman_numeral_accuracy_on_gold(gold):
    """罗马数字标注准确率 —— 门槛 85%（逐和弦比对）。"""
    total = 0
    correct = 0
    misses = []
    for case in gold["cases"]:
        artifact = build_artifact(chord_text=case["chords"], key=case["key"])
        result = analyze(artifact)
        for got, want in zip(result.roman, case["roman"]):
            total += 1
            if got == want:
                correct += 1
            else:
                misses.append((case["id"], got, want))

    accuracy = correct / total if total else 0.0
    assert accuracy >= 0.85, f"级数标注准确率 {accuracy:.3f} < 0.85，未命中：{misses[:10]}"


def test_function_accuracy_on_gold(gold):
    """功能标注准确率 —— 门槛 85%。"""
    total = 0
    correct = 0
    misses = []
    for case in gold["cases"]:
        artifact = build_artifact(chord_text=case["chords"], key=case["key"])
        result = analyze(artifact)
        for got, want in zip(result.functions, case["functions"]):
            total += 1
            if got == want:
                correct += 1
            else:
                misses.append((case["id"], got, want))

    accuracy = correct / total if total else 0.0
    assert accuracy >= 0.85, f"功能标注准确率 {accuracy:.3f} < 0.85，未命中：{misses[:10]}"


def test_melody_gold_cases(gold):
    """旋律分析：range / contour / repetition 三项全对。"""
    misses = []
    for case in gold["melody_cases"]:
        melody = parse_melody(case["melody"])
        if melody.range != case["range"]:
            misses.append((case["id"], "range", melody.range, case["range"]))
        if melody.contour != case["contour"]:
            misses.append((case["id"], "contour", melody.contour, case["contour"]))
        if melody.repetition != case["repetition"]:
            misses.append((case["id"], "repetition", melody.repetition, case["repetition"]))
    assert not misses, f"旋律分析未命中：{misses}"


def test_gold_dataset_is_well_formed(gold):
    """金标准集自身必须完整，否则上面的准确率没有意义。"""
    ids = [c["id"] for c in gold["cases"]]
    assert len(ids) == len(set(ids)), "金标准集存在重复 id"
    assert len(gold["cases"]) >= 20, "金标准集至少 20 个片段"

    for case in gold["cases"]:
        assert len(case["roman"]) == len(parse_progression(case["chords"])), (
            f"{case['id']}: roman 数量与和弦数量不匹配"
        )
        assert len(case["functions"]) == len(case["roman"]), (
            f"{case['id']}: functions 数量与 roman 不匹配"
        )
        tonic, _mode = parse_key(case["key"])
        assert tonic, f"{case['id']}: key 格式非法"


def test_gold_covers_multiple_keys(gold):
    """金标准集必须覆盖多个调性，否则「准确率」只证明了一个调。"""
    keys = {c["key"] for c in gold["cases"]}
    assert len(keys) >= 8, f"调性覆盖过少：{keys}"
    assert any("Minor" in k for k in keys), "缺少小调案例"


# --------------------------------------------------------------------------- #
# 调性识别的单元行为
# --------------------------------------------------------------------------- #


def test_key_pitch_classes():
    assert key_pitch_classes("C Major") == {0, 2, 4, 5, 7, 9, 11}
    assert key_pitch_classes("A Minor") == {9, 11, 0, 2, 4, 5, 7}


def test_detect_key_prefers_tonic_ending():
    """结束在主和弦是调性最强的证据。"""
    assert detect_key_from_chords(parse_progression("C | G | Am | F")).key == "C Major"
    assert detect_key_from_chords(parse_progression("C | F | G | C")).key == "C Major"


def test_detect_key_handles_empty():
    assert detect_key_from_chords([]) is None


def test_same_chords_read_in_two_keys():
    """同样的和弦在不同调下应有不同的级数标注 —— 说明 key 真的在起作用。"""
    chords = parse_progression("C | G | Am | F")
    in_c = analyze(MusicArtifact(source_format="chords_text", chords=chords, key="C Major"))
    in_g = analyze(MusicArtifact(source_format="chords_text", chords=chords, key="G Major"))
    assert in_c.roman == ["I", "V", "vi", "IV"]
    assert in_g.roman[0] == "IV"  # C 在 G 大调里是 IV
    assert in_c.roman != in_g.roman


def test_out_of_key_chord_is_flagged():
    """调外和弦标 ``?`` 并给出中文说明 —— 供 P4 的 Grounding 使用。"""
    artifact = build_artifact(chord_text="C | G | Am | Fm", key="C Major")
    result = analyze(artifact)
    assert "?" in result.roman, "Fm 在 C 大调中属于调外和弦"
    assert any("Fm" in note for note in result.notes)


def test_analysis_is_deterministic():
    artifact = build_artifact(chord_text="C | G | Am | F", key="C Major")
    assert analyze(artifact) == analyze(artifact)


# --------------------------------------------------------------------------- #
# §3.4.1 / §3.4.2 的输出结构逐字段核对
# --------------------------------------------------------------------------- #


def test_analysis_output_matches_mvp_section_3_4_1():
    """§3.4.1 的输出 JSON 有 key / raw / roman / functions / layman 五项。"""
    artifact = build_artifact(chord_text="C | G | Am | F", key="C Major")
    result = analyze(artifact)

    assert result.key == "C Major"
    assert result.raw == ["C", "G", "Am", "F"]
    assert result.roman == ["I", "V", "vi", "IV"]
    assert result.functions == ["Tonic", "Dominant", "Tonic", "Subdominant"]
    # layman 三个子字段必须都有内容（不能是空壳）
    assert result.layman.key
    assert result.layman.progression
    assert result.layman.character


def test_analysis_output_matches_mvp_section_3_4_2():
    """§3.4.2 的输出含 notes / range / contour / repetition / layman。"""
    artifact = build_artifact(melody_text="E4 G4 A4 G4 E4")
    result = analyze(artifact)

    assert result.melody is not None
    assert result.melody["notes"] == ["E4", "G4", "A4", "G4", "E4"]
    assert result.melody["range"] == ["E4", "A4"]
    assert result.melody["contour"] == "up-down"
    assert result.melody["repetition"] is True
    assert result.layman.range
    assert result.layman.contour


def test_layman_progression_text_matches_mvp():
    """§3.4.1 示例的通俗描述应为「流行歌最常用的走向之一」。"""
    artifact = build_artifact(chord_text="C | G | Am | F", key="C Major")
    result = analyze(artifact)
    assert result.layman.progression == "流行歌最常用的走向之一"


def test_layman_character_mentions_stability_and_color():
    """§3.10.5 的「特征」要求提到功能稳定与色彩较少。"""
    artifact = build_artifact(chord_text="C | G | Am | F", key="C Major")
    result = analyze(artifact)
    assert "稳定" in result.layman.character
    assert "色彩" in result.layman.character


def test_seventh_chords_change_the_character_description():
    """加了七音，特征描述应当变化（说明 layman 真的依赖分析结果）。"""
    plain = analyze(build_artifact(chord_text="C | G | Am | F", key="C Major"))
    seventh = analyze(build_artifact(chord_text="Cmaj7 | G | Am7 | Fmaj7", key="C Major"))
    assert plain.layman.character != seventh.layman.character
    assert "七" in seventh.layman.character


def test_build_artifact_detects_key_when_not_given():
    artifact = build_artifact(chord_text="C | F | G | C")
    assert artifact.key == "C Major"
    assert artifact.key_confidence > 0


def test_build_artifact_marks_source_format():
    assert build_artifact(chord_text="C | G").source_format == "chords_text"
    assert build_artifact(melody_text="C4 D4").source_format == "melody_text"
    assert build_artifact().source_format == "none"


def test_empty_artifact_analysis_is_safe():
    """空素材不得抛异常 —— Creative Starter 模式下没有素材是正常情况。"""
    result = analyze(MusicArtifact())
    assert result.key is None
    assert result.roman == []
    assert result.layman.is_empty()
