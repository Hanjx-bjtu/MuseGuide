"""P5 测试：界面渲染与交互（阶段门 G5）。

**为什么界面也要写测试：**
``MVP计划.md`` §9 第 7 条要求「零基础用户能理解输出内容并据此行动」。
这条要求只有在界面上才能验证 —— 服务层输出正确，不代表界面渲染正确。
实测证明其必要性：第一次驱动交互时，引导页因一个 ``StopIteration`` 直接崩溃，
而「打开页面看一眼」是不会发现的（页面静态渲染是好的，只有点击后才炸）。

用 Streamlit 官方的 ``AppTest`` 无头执行页面代码，配合一个 Fake 客户端，
使测试**不依赖真实后端与网络**。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

streamlit = pytest.importorskip("streamlit", reason="P5 界面测试需要 streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

from app.services.layman import find_banned_terms  # noqa: E402
from app.ui.components import (  # noqa: E402
    DEGRADATION_MESSAGES,
    format_chords,
)

STARTER_PAGE = str(PROJECT_ROOT / "app" / "pages" / "starter.py")
TUTOR_PAGE = str(PROJECT_ROOT / "app" / "pages" / "tutor.py")
ABOUT_PAGE = str(PROJECT_ROOT / "app" / "pages" / "about.py")

#: 供测试的后端地址。指向一个不会应答的端口时，界面应显示可读错误而不是崩溃。
#:
#: ⚠️ 本机沙箱会拦截出站连接，因此「连接被拒绝」与「连接超时」都可能出现
#: （实测端口 9 与 59999 都返回超时）。测试只断言「给出可读提示」，
#: 不断言具体的错误种类 —— 那属于环境细节，不是被测行为。
FAKE_API = "http://127.0.0.1:59999"


@pytest.fixture
def _api_env(monkeypatch):
    """默认把界面指向一个不可达的端口（隔离测试，不依赖真实后端）。

    这同时也是一条**降级测试**：后端不可达时界面必须给出可读提示，而不是崩溃。
    需要真实数据的用例请使用 ``live_backend`` 夹具。
    """
    monkeypatch.setenv("MUSEGUIDE_API", FAKE_API)


@pytest.fixture
def live_backend(monkeypatch):
    """指向真实运行中的后端；未运行则跳过。

    **为什么需要一个真后端：** 选项组、方案展示这些断言依赖后端返回的数据，
    用空数据测出来的「通过」是假的 —— 它会掩盖「选项没渲染出来」这类问题。
    """
    import httpx

    base = os.environ.get("MUSEGUIDE_API_TEST", "http://127.0.0.1:8011")
    try:
        response = httpx.get(f"{base}/api/health", timeout=3)
        if response.status_code != 200:
            pytest.skip(f"后端未就绪：{base}")
    except Exception:  # noqa: BLE001
        pytest.skip(f"后端不可达：{base}（运行 uvicorn app.api.server:app --port 8011 后可跑此用例）")
    monkeypatch.setenv("MUSEGUIDE_API", base)
    return base


def _run(page: str) -> AppTest:
    at = AppTest.from_file(page, default_timeout=30)
    at.run()
    return at


# --------------------------------------------------------------------------- #
# 渲染冒烟：三个页面都必须能渲染，且不抛异常
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "page,name",
    [(STARTER_PAGE, "starter"), (TUTOR_PAGE, "tutor"), (ABOUT_PAGE, "about")],
    ids=["starter", "tutor", "about"],
)
def test_page_renders_without_exception(page, name):
    at = _run(page)
    assert not at.exception, f"{name} 页面渲染异常：{[e.value for e in at.exception]}"


def test_starter_home_matches_mvp_section_3_10_2():
    """§3.10.2 的首页：意图输入框 + 大方向按钮 + 底部提示语。"""
    at = _run(STARTER_PAGE)

    assert at.title[0].value == "你想写什么样的歌？"
    assert len(at.text_area) == 1, "首页应有一个意图输入框"

    labels = [b.label for b in at.button]
    for keyword in ("温柔民谣", "流行抒情", "钢琴曲", "轻快节奏", "梦幻电子"):
        assert any(keyword in label for label in labels), f"缺少大方向按钮：{keyword}"
    assert any("开始" in label for label in labels)


def test_tutor_page_matches_mvp_section_3_10_5():
    """§3.10.5 的进阶页：创作目标 + 和弦进行 + [开始分析]。"""
    at = _run(TUTOR_PAGE)

    assert len(at.text_input) >= 2, "应有创作目标与和弦输入框"
    labels = [t.label for t in at.text_input]
    assert any("目标" in label for label in labels)
    assert any("和弦" in label for label in labels)
    assert any("分析" in b.label for b in at.button)


def test_about_page_states_boundaries():
    """关于页必须写明「不做什么」—— §1.3 的边界要让用户看得到。"""
    at = _run(ABOUT_PAGE)
    text = " ".join(m.value for m in at.markdown)
    assert "不生成完整歌曲" in text
    assert "不支持哼唱" in text


# --------------------------------------------------------------------------- #
# 交互路径（G5：两条链路可纯界面走完）
# --------------------------------------------------------------------------- #


def test_starter_flow_reaches_guide_stage():
    """点「开始」应进入引导页 —— 且引导页必须真的渲染出三组选择。

    回归：引导页曾因反查失败抛 ``StopIteration`` 而崩溃，
    只渲染出 2 组选择。静态渲染测试发现不了，只有点击后才炸。
    """
    at = _run(STARTER_PAGE)
    at.text_area[0].set_value("我想写一首关于毕业的歌")
    at.run()

    start = next(b for b in at.button if "开始" in b.label)
    start.click()
    at.run()

    assert not at.exception, f"引导页异常：{[e.value for e in at.exception]}"
    assert at.title[0].value == "先说几个大概方向"


def test_quick_start_button_enters_guide_with_preset():
    """大方向按钮应带上预设并进入引导页。

    回归：早期按钮存的是**界面文案**而非**概念名**，
    导致引导页按 concept 反查时崩溃。
    """
    at = _run(STARTER_PAGE)
    button = next(b for b in at.button if "温柔民谣" in b.label)
    button.click()
    at.run()

    assert not at.exception, f"快捷按钮导致异常：{[e.value for e in at.exception]}"
    assert at.title[0].value == "先说几个大概方向"
    assert at.session_state["starter_style"] == "民谣"
    assert at.session_state["starter_emotion"] == "温暖"


def test_guide_renders_three_option_groups_with_hints(live_backend):
    """§3.10.3：三组选择，且每个选项都要有通俗说明（captions）。

    需要真实后端才能拿到选项数据 —— 否则渲染出 0 组是「正确地反映了后端不可用」，
    断言「有 3 组」会变成假失败。
    """
    at = _run(STARTER_PAGE)
    at.session_state["starter_stage"] = "guide"
    at.run()

    assert not at.exception, f"引导页异常：{[e.value for e in at.exception]}"
    assert len(at.radio) == 3, f"应有速度/风格/情绪三组，实际 {len(at.radio)}"
    for radio in at.radio:
        assert radio.options, "选项组不能为空"
        assert radio.captions, "每个选项都必须带通俗说明（§3.10.3）"


def test_guide_shows_tempo_daily_analogies(live_backend):
    """§3.10.3 的速度选项要有「像翻相册 / 像散步 / 像走路」这类日常类比。"""
    at = _run(STARTER_PAGE)
    at.session_state["starter_stage"] = "guide"
    at.run()

    tempo_options = at.radio[0].options
    assert any("像" in option for option in tempo_options), (
        f"速度选项缺少日常类比：{tempo_options}"
    )


def test_guide_without_backend_degrades_gracefully():
    """后端不可达时，引导页仍应渲染标题并给出提示，而不是崩溃。"""
    at = _run(STARTER_PAGE)
    at.session_state["starter_stage"] = "guide"
    at.run()

    assert not at.exception
    assert at.title[0].value == "先说几个大概方向"
    assert at.error, "应提示后端不可达"


def test_backend_unreachable_shows_readable_error_not_crash():
    """G5：后端不可达时必须给出可读提示，而不是异常堆栈（§8）。"""
    at = _run(STARTER_PAGE)
    at.session_state["starter_stage"] = "guide"
    at.run()

    generate = next(b for b in at.button if "生成" in b.label)
    generate.click()
    at.run()

    assert not at.exception, "后端不可达不应导致页面崩溃"
    errors = [e.value for e in at.error]
    assert errors, "应给出可读的错误提示"
    assert any("后端" in message or "uvicorn" in message for message in errors), (
        f"错误提示应告诉用户怎么办，实际：{errors}"
    )


def test_options_failure_does_not_break_home_page():
    """后端不可达时首页仍应可用（只是选项为空）。"""
    at = _run(STARTER_PAGE)
    assert at.title[0].value == "你想写什么样的歌？"
    assert at.error, "应提示后端不可达"


# --------------------------------------------------------------------------- #
# 渲染组件（可在无 Streamlit 运行时下测试的部分）
# --------------------------------------------------------------------------- #


def test_format_chords_uses_arrow_separator():
    """§3.10.4 用 ``→`` 连接和弦。"""
    assert format_chords(["Am", "F", "C", "G"]) == "Am  →  F  →  C  →  G"
    assert format_chords([]) == "（未提供）"


def test_every_degradation_kind_has_user_facing_message():
    """每种降级事件都必须有面向用户的说明 —— 否则用户不知道发生了什么。"""
    from app.core.degradation import DegradationKind

    declared = set(DegradationKind.__args__)  # type: ignore[attr-defined]
    missing = declared - set(DEGRADATION_MESSAGES)
    assert not missing, f"以下降级类型缺少用户提示文案：{missing}"


def test_degradation_messages_avoid_banned_terms():
    """降级提示本身也不能出现零基础禁区术语。"""
    for kind, message in DEGRADATION_MESSAGES.items():
        assert not find_banned_terms(message), f"{kind} 的提示含禁区术语"


def test_degradation_messages_are_not_alarming():
    """降级不是错误 —— 文案不应让用户以为系统坏了。"""
    for kind, message in DEGRADATION_MESSAGES.items():
        for word in ("错误", "失败", "崩溃", "异常"):
            assert word not in message, f"{kind} 的提示用了吓人的措辞：{message}"


# --------------------------------------------------------------------------- #
# 客户端层（不依赖 Streamlit）
# --------------------------------------------------------------------------- #


def test_client_raises_readable_error_when_backend_down():
    """API 客户端必须把连接失败翻译成人话，而不是抛原始异常。

    不锁定具体措辞（沙箱环境可能返回超时而非拒绝连接），
    只断言：抛的是 ``APIError``，且消息对用户有意义。
    """
    from app.ui.client import APIError, MuseGuideClient

    client = MuseGuideClient(FAKE_API, timeout=2)
    with pytest.raises(APIError) as excinfo:
        client.health()

    message = str(excinfo.value)
    assert message, "错误消息不能为空"
    # 消息必须是给用户看的，不是 httpx 的原始异常文本
    assert "httpx" not in message.lower()
    assert "Traceback" not in message
    assert any(word in message for word in ("后端", "超时", "稍后")), (
        f"错误消息应对用户有意义，实际：{message}"
    )


def test_client_surfaces_backend_error_detail():
    """后端的 400 详情应原样透出，而不是变成「未知错误」。"""
    from app.ui.client import APIError, MuseGuideClient

    # 用 MockTransport 模拟后端的 400 响应
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "和弦解析失败：无法识别的和弦符号：'H'"})

    client = MuseGuideClient(FAKE_API, transport=httpx.MockTransport(handler))
    with pytest.raises(APIError) as excinfo:
        client.advice(chords="H | X")
    assert "无法识别的和弦符号" in str(excinfo.value)


def test_client_handles_any_transport_error():
    """回归：**任何**传输层异常都必须转成可读提示。

    实测教训：早期只捕获 ``ConnectError`` 与 ``TimeoutException``，
    结果受限网络环境下出现的 ``ReadError``（WinError 10054
    远程主机强迫关闭连接）直接穿透，导致 **Streamlit 整页渲染为空** ——
    界面上什么都看不到，用户也得不到任何提示。

    传输层失败的种类因环境而异，不该逐个枚举，必须兜住 ``httpx.HTTPError``。
    """
    import httpx

    from app.ui.client import APIError, MuseGuideClient

    class ExplodingTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ReadError("远程主机强迫关闭了一个现有的连接")

    client = MuseGuideClient(FAKE_API, transport=ExplodingTransport())
    with pytest.raises(APIError) as excinfo:
        client.health()

    message = str(excinfo.value)
    assert "后端" in message
    assert "uvicorn" in message, "应告诉用户怎么启动后端"


@pytest.mark.parametrize(
    "exc_type",
    ["ConnectError", "ReadError", "RemoteProtocolError", "WriteError"],
)
def test_client_transport_error_types_are_all_wrapped(exc_type):
    """逐个枚举常见传输层异常类型，确保都被兜住。"""
    import httpx

    from app.ui.client import APIError, MuseGuideClient

    error_class = getattr(httpx, exc_type)

    class ExplodingTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            raise error_class("transport failure")

    client = MuseGuideClient(FAKE_API, transport=ExplodingTransport())
    with pytest.raises(APIError):
        client.health()


def test_client_parses_starter_response_shape():
    """客户端必须把响应映射为界面态对象，字段齐全。"""
    import httpx

    from app.ui.client import MuseGuideClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/starter":
            return httpx.Response(
                200,
                json={
                    "plan": {"key": "C Major", "sections": []},
                    "intent": {"emotion": ["伤感"]},
                    "evidence": [{"entry_id": "e1"}],
                    "degradation": ["llm_unavailable"],
                    "warnings": [],
                    "trace_id": "abc",
                },
            )
        return httpx.Response(404)

    client = MuseGuideClient(FAKE_API, transport=httpx.MockTransport(handler))
    outcome = client.starter(text="毕业歌")
    assert outcome.plan["key"] == "C Major"
    assert outcome.degradation == ["llm_unavailable"]
    assert outcome.trace_id == "abc"


def test_client_parses_advice_response_shape():
    import httpx

    from app.ui.client import MuseGuideClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "analysis": "分析",
                "problems": ["问题"],
                "options": [{"label": "A", "chords": ["Cmaj7"]}],
                "evidence": [],
                "grounding": {"ok": True, "issues": [], "summary": {}},
                "diversity_warnings": [],
                "breakdown": {"key": "C Major"},
                "degradation": [],
                "trace_id": "xyz",
            },
        )

    client = MuseGuideClient(FAKE_API, transport=httpx.MockTransport(handler))
    outcome = client.advice(chords="C | G")
    assert outcome.analysis == "分析"
    assert outcome.options[0]["label"] == "A"
    assert outcome.breakdown["key"] == "C Major"
