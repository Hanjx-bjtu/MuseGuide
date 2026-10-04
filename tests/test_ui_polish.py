"""P5 打磨的回归测试：界面可用性与生成层通俗度。

覆盖本轮修复的问题：

* D23 代理拦截导致界面请求失败（httpx ``trust_env``）
* 引导页三组选择默认未选、且不选也能静默提交
* 提交按钮无防重复点击
* 零基础档未解释和弦符号（人工评分 ``comprehensibility`` 唯一未达标项）
"""

from __future__ import annotations

import inspect
import sys

import pytest

from app.services.generation import prompts
from app.services.layman import find_banned_terms

# --------------------------------------------------------------------------- #
# D23：本地后端请求必须绕过系统代理
# --------------------------------------------------------------------------- #


def test_client_disables_environment_proxy():
    """回归：界面客户端必须 ``trust_env=False``。

    实测教训（一个极难定位的 bug）：界面连本地后端时频繁报
    ``ReadError: [WinError 10054]``，偶尔返回空的 ``502``
    （说明不是 uvicorn 发的），而同一时刻 PowerShell 完全正常。

    根因：httpx 默认 ``trust_env=True``，会从**操作系统级代理设置**
    （Windows 注册表）读取代理 —— 所以「环境变量里没有 PROXY」并不代表没有代理。
    发往 127.0.0.1 的请求被绕进代理，代理返回 502 或直接断连。
    实测：默认设置下 3/5 失败，``trust_env=False`` 后 5/5 成功。
    """
    pytest.importorskip("streamlit")
    import httpx

    from app.ui.client import MuseGuideClient

    client = MuseGuideClient("http://127.0.0.1:9")
    transport = client._client._transport
    # httpx 会把 trust_env 记在 client 上
    assert client._client.trust_env is False, "界面客户端必须禁用环境代理"

    # 文档化这条约束：源码里应写明原因
    source = inspect.getsource(MuseGuideClient.__init__)
    assert "trust_env" in source
    assert "代理" in source, "应留下注释说明为什么禁用代理"


def test_client_accepts_injected_transport_for_tests():
    """测试可注入 transport —— 使界面测试不依赖真实后端。"""
    pytest.importorskip("streamlit")
    import httpx

    from app.ui.client import MuseGuideClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    client = MuseGuideClient("http://127.0.0.1:9", transport=httpx.MockTransport(handler))
    assert client.health()["status"] == "ok"


# --------------------------------------------------------------------------- #
# 零基础档必须解释和弦符号（人工评分发现的缺口）
# --------------------------------------------------------------------------- #


def test_layman_chord_rule_exists():
    """规则常量必须存在且有实质内容。"""
    assert hasattr(prompts, "LAYMAN_CHORD_RULE")
    rule = prompts.LAYMAN_CHORD_RULE
    assert "听起来像什么" in rule or "类比" in rule
    assert "maj7" in rule.lower() or "后缀" in rule
    assert "斜杠" in rule or "G/B" in rule


def test_layman_chord_rule_is_itself_terminology_free():
    """解释要求本身不能出现它自己禁止的术语。

    如果规则里写着「请解释七和弦与转位」，模型会照着冒出这些词。
    """
    banned = find_banned_terms(prompts.LAYMAN_CHORD_RULE)
    assert not banned, f"解释要求里出现了禁区术语：{banned}"


def test_starter_prompt_includes_chord_rule_for_zero_level():
    """零基础档的起步方案 Prompt 必须带去符号解释要求。

    实测背景：人工评分中 ``comprehensibility`` 是八维里唯一未达标的
    （3.94 / 门槛 4.0），且 16 题里 15 题给 4 分、**无一题给 5 分** ——
    说明是普遍存在一层门槛，而非个别题差。定位到原因是
    「和弦符号本身没被解释」。
    """
    prompt = prompts.starter_prompt(
        raw_text="写首歌", intent=prompts.CreativeIntent(), evidence=[], level="zero"
    )
    assert "和弦符号的解释要求" in prompt


def test_tutor_prompt_includes_chord_rule_for_zero_level():
    """进阶链路的零基础档同样要解释符号。

    这一条尤其重要：实测发现进阶建议里和弦串是
    ``Cmaj7 | G/B | Am7 | Fmaj7``，但解释里**一个和弦名都没提**。
    """
    from app.core.plan import AnalysisResult

    prompt = prompts.tutor_prompt(
        goal_text="觉得平淡",
        analysis=AnalysisResult(key="C Major", raw=["C", "G"]),
        evidence=[],
        level="zero",
    )
    assert "和弦符号的解释要求" in prompt


def test_chord_rule_omitted_for_advanced_level():
    """进阶用户不需要这套解释要求 —— 对他们反而是啰嗦。"""
    from app.core.plan import AnalysisResult

    starter = prompts.starter_prompt(
        raw_text="x", intent=prompts.CreativeIntent(), evidence=[], level="advanced"
    )
    assert "和弦符号的解释要求" not in starter

    tutor = prompts.tutor_prompt(
        goal_text="x",
        analysis=AnalysisResult(key="C Major"),
        evidence=[],
        level="advanced",
    )
    assert "和弦符号的解释要求" not in tutor


def test_chord_rule_gives_concrete_examples():
    """规则必须给具体示例，否则模型容易写成抽象要求。"""
    rule = prompts.LAYMAN_CHORD_RULE
    assert "[" in rule, "示例里应出现具体和弦写法"
    assert any(marker in rule for marker in ("例如", "如：", "比如"))


def test_chord_rule_forbids_roman_numerals():
    """零基础档不得罗列罗马数字 —— 用户看不懂。"""
    rule = prompts.LAYMAN_CHORD_RULE
    assert "罗马数字" in rule


# --------------------------------------------------------------------------- #
# D24：streamlit run 的 sys.path 问题（启动即崩）
# --------------------------------------------------------------------------- #


def test_bootstrap_module_exists_and_fixes_path():
    """``app/bootstrap.py`` 必须能把仓库根加进 sys.path。

    实测背景：``streamlit run app/main.py`` 会把**脚本所在目录**（app/）
    放进 ``sys.path[0]``，而不是工作目录，于是 ``import app.*`` 抛
    ``ModuleNotFoundError: No module named 'app'`` —— **应用启动即崩**。

    这个问题 AppTest 与 pytest 都测不出来：它们从仓库根运行，
    ``sys.path`` 里恰好有仓库根。只有真正 ``streamlit run`` 才暴露。
    """
    from app import bootstrap

    root = bootstrap.ensure_project_root_on_path()
    assert root.name == "MuseGuide"
    assert (root / "app" / "__init__.py").is_file()
    assert str(root) in sys.path


@pytest.mark.parametrize(
    "relative,expected_parents",
    [
        ("app/main.py", 2),  # app/main.py -> parents[1] 是 app/，parents[1]... 见下
        ("app/pages/starter.py", 3),
    ],
)
def test_each_page_computes_project_root_correctly(relative, expected_parents):
    """每个入口文件都必须能算出正确的仓库根。

    ``main.py`` 用 ``parent.parent``（app/ 的上一级）；
    ``app/pages/*.py`` 用 ``parents[2]``。两者都必须指向仓库根。
    """
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    script = project_root / relative

    if "pages" in relative:
        computed = script.resolve().parents[2]
    else:
        computed = script.resolve().parent.parent

    assert computed == project_root, f"{relative} 算出的根目录不对：{computed}"


@pytest.mark.parametrize(
    "relative",
    ["app/main.py", "app/pages/starter.py", "app/pages/tutor.py", "app/pages/about.py"],
)
def test_entry_files_guard_sys_path(relative):
    """每个入口文件都必须自己修正 sys.path。

    不能只靠 ``main.py`` —— ``st.navigation`` 会把页面文件当作独立脚本运行，
    它们不会继承 ``main.py`` 的 sys.path 修改。
    """
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    source = (project_root / relative).read_text(encoding="utf-8")

    assert "sys.path.insert" in source, f"{relative} 缺少 sys.path 修正"
    assert "import streamlit" in source


def test_app_package_importable_from_clean_path():
    """在「仓库根不在 sys.path」的条件下，bootstrap 必须能救回来。

    用一个子进程模拟 streamlit 的 sys.path 行为，避免污染当前测试进程。
    """
    import subprocess
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    script = f"""
import sys, os
from pathlib import Path
ROOT = {str(project_root)!r}
# 模拟 streamlit：脚本目录在首位，仓库根被排除
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path = [p for p in sys.path if os.path.abspath(p or ".") != ROOT]

assert not any(os.path.abspath(p or ".") == ROOT for p in sys.path), "前置条件不成立"

# 页面文件里的修正逻辑
sys.path.insert(0, str(Path(os.path.join(ROOT, "app")).resolve().parent))
import app
print("OK", app.__file__)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(project_root / "app"),
    )
    assert result.returncode == 0, f"子进程失败：{result.stderr[:500]}"
    assert "OK" in result.stdout
