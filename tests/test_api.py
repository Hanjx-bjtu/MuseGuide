"""P2 测试：API 层（P2.10）。

**为什么必须测 API 而不是界面：** API 是两条链路中唯一可被自动化回归的接口。
P6 的 45 个评测用例与 Baseline 对照实验都要打 API（ADR-0005）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.server import app
from app.providers.mock import MockLLMProvider


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def client_with_mock_llm():
    """注入 Mock LLM —— 使 API 测试不依赖网络与 API Key。"""
    app.dependency_overrides.clear()
    import app.api.server as server

    original = server._llm_override
    server._llm_override = lambda: MockLLMProvider()
    try:
        yield TestClient(app)
    finally:
        server._llm_override = original
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 元信息
# --------------------------------------------------------------------------- #


def test_health_reports_kb_and_llm_status(client):
    """健康检查要让界面能提前提示「当前为简化模式」。"""
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["kb_entries"] >= 20, "知识库应已加载"
    assert isinstance(body["llm_available"], bool)
    assert body["llm_model"]


def test_options_endpoint_returns_three_groups_with_hints(client):
    """§3.10.2 / §3.10.3：选项必须带通俗说明，界面直接渲染。"""
    response = client.get("/api/options")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"emotion", "style", "tempo"}

    for group in body.values():
        assert group
        for item in group:
            assert item["label"] and item["concept"] and item["hint"]


def test_tempo_options_carry_daily_analogies(client):
    """§3.10.3 的速度选项要有「像翻相册 / 像散步 / 像走路」这类类比。"""
    tempo = client.get("/api/options").json()["tempo"]
    assert any("像" in item["label"] for item in tempo)


# --------------------------------------------------------------------------- #
# 零基础链路
# --------------------------------------------------------------------------- #


def test_starter_endpoint_returns_plan(client):
    response = client.post(
        "/api/starter",
        json={"text": "我想写一首关于毕业的歌，有点伤感但最后是释然的感觉。"},
    )
    assert response.status_code == 200
    body = response.json()
    plan = body["plan"]
    assert plan["key"]
    assert plan["key_explanation"]
    assert len(plan["sections"]) >= 2
    assert body["trace_id"]


def test_starter_endpoint_returns_evidence(client):
    """§3.9.3 要求展示理论依据 —— API 必须把它带出来。"""
    response = client.post("/api/starter", json={"text": "我想写一首关于毕业的歌"})
    evidence = response.json()["evidence"]
    assert evidence
    for item in evidence:
        assert item["entry_id"]
        assert item["source"]["title"]


def test_starter_endpoint_accepts_selections_only(client):
    """零基础用户的兜底入口：只做选择、不写字。"""
    response = client.post(
        "/api/starter",
        json={"text": "", "emotion": "温柔", "style": "民谣", "tempo_label": "很慢，像翻相册"},
    )
    assert response.status_code == 200
    plan = response.json()["plan"]
    assert plan["key"]
    assert plan["tempo"] == 60


def test_starter_endpoint_works_with_empty_body(client):
    """完全空输入也要给出可用方向，而不是报错（§8 兜底）。"""
    response = client.post("/api/starter", json={})
    assert response.status_code == 200
    assert response.json()["plan"]["key"]


def test_starter_endpoint_reports_degradation(client):
    """无 LLM 时界面要能提示「已切换到简化模式」（§8）。"""
    response = client.post("/api/starter", json={"text": "我想写一首歌"})
    body = response.json()
    assert isinstance(body["degradation"], list)
    assert body["warnings"] == [], "方案不应有校验问题"


def test_starter_endpoint_with_mock_llm(client_with_mock_llm):
    response = client_with_mock_llm.post(
        "/api/starter", json={"text": "我想写一首关于毕业的歌，有点伤感但最后释然"}
    )
    assert response.status_code == 200
    plan = response.json()["plan"]
    assert len(plan["sections"]) >= 2


# --------------------------------------------------------------------------- #
# 分析接口
# --------------------------------------------------------------------------- #


def test_analyze_endpoint_matches_mvp_section_3_4_1(client):
    """§3.4.1 的输出结构逐字段核对。"""
    response = client.post("/api/analyze", json={"chords": "C | G | Am | F"})
    assert response.status_code == 200
    body = response.json()
    assert body["raw"] == ["C", "G", "Am", "F"]
    assert body["roman"] == ["I", "V", "vi", "IV"]
    assert body["functions"] == ["Tonic", "Dominant", "Tonic", "Subdominant"]
    assert body["layman"]["key"]
    assert body["layman"]["progression"]


def test_analyze_endpoint_handles_melody(client):
    response = client.post("/api/analyze", json={"melody": "E4 G4 A4 G4 E4"})
    assert response.status_code == 200
    body = response.json()
    assert body["melody"]["contour"] == "up-down"
    assert body["layman"]["contour"]


def test_analyze_endpoint_accepts_explicit_key(client):
    """用户指定调性时不应被自动识别覆盖。"""
    body = client.post("/api/analyze", json={"chords": "C | G | Am | F", "key": "G Major"}).json()
    assert body["key"] == "G Major"
    assert body["roman"][0] == "IV"


def test_analyze_endpoint_flags_borrowed_chord(client):
    """调外和弦要能被识别 —— P4 的 Grounding 依赖它。"""
    body = client.post("/api/analyze", json={"chords": "C | G | Am | Fm", "key": "C Major"}).json()
    assert "?" in body["roman"]
    assert body["notes"]


def test_analyze_endpoint_rejects_empty_input(client):
    response = client.post("/api/analyze", json={})
    assert response.status_code == 400
    assert "至少提供" in response.json()["detail"]


def test_analyze_endpoint_rejects_invalid_chords(client):
    """非法输入返回 400 + 可读原因，而不是 500（§1.1 目标 3）。"""
    response = client.post("/api/analyze", json={"chords": "H | X"})
    assert response.status_code == 400
    assert "无法识别" in response.json()["detail"]


def test_analyze_endpoint_rejects_invalid_melody(client):
    response = client.post("/api/analyze", json={"melody": "H4 X9"})
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# 契约与文档
# --------------------------------------------------------------------------- #


def test_openapi_schema_is_generated(client):
    """OpenAPI 可生成 —— 保证 API 契约是自描述的。"""
    schema = client.get("/openapi.json").json()
    assert "/api/starter" in schema["paths"]
    assert "/api/analyze" in schema["paths"]
    assert "/api/options" in schema["paths"]


def test_api_responses_are_json_serializable_with_chinese(client):
    """中文不得被转义成 \\uXXXX —— 界面直接展示响应内容。"""
    response = client.post("/api/analyze", json={"chords": "C | G | Am | F"})
    assert "明亮温暖" in response.text
