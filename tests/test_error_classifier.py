"""错误分类与 error signature：Self-Repair V2 的判定基础。

分类错了，后面所有事都错：修错方向（列名问题却去改代码结构）、
或者该停的时候不停（复读检测依赖 signature 的稳定性）。
所以这一组用例把「什么错 → 什么类」与「同一个错 ↔ 同一个指纹」钉死。
"""

import pytest

from backend.agent.acceptance import acceptance_gate
from backend.agent.repair import (
    CATEGORIES,
    CATEGORY_COLUMN,
    CATEGORY_CONTRACT,
    CATEGORY_EMPTY,
    CATEGORY_ENVIRONMENT,
    CATEGORY_NAME,
    CATEGORY_RESOURCE,
    CATEGORY_SYNTAX,
    CATEGORY_TIMEOUT,
    CATEGORY_TYPE,
    CATEGORY_UNKNOWN,
    classify_error,
    error_signature,
    last_exception,
    normalize_message,
)
from backend.evaluation import repair_cases as cases

TEMPLATES = cases.FAILURE_TEMPLATES


def _classify(category: str, **overrides):
    payload = dict(TEMPLATES[category])
    payload.update(overrides)
    return classify_error(payload, acceptance_gate(payload))


# ---- 九类错误都要认出来 ----

@pytest.mark.parametrize("category", [
    CATEGORY_SYNTAX, CATEGORY_NAME, CATEGORY_COLUMN, CATEGORY_TYPE, CATEGORY_EMPTY,
    CATEGORY_TIMEOUT, CATEGORY_RESOURCE, CATEGORY_CONTRACT, CATEGORY_ENVIRONMENT,
])
def test_every_required_category_is_covered(category):
    """需求列出的 9 类必须都有对应的分类结果。"""
    info = _classify(category)
    assert info.category == category, (category, info.to_dict())
    assert info.signature.startswith(f"{category}|")
    assert info.accepted is False


def test_unknown_falls_back_and_never_raises():
    payload = {"ok": False, "stderr": "something went sideways", "exit_code": 1}
    info = classify_error(payload, acceptance_gate(payload))
    assert info.category == CATEGORY_UNKNOWN
    assert CATEGORIES.count(info.category) == 1


def test_acceptance_pass_marks_accepted():
    payload = dict(cases.SUCCESS_RESULT)
    info = classify_error(payload, acceptance_gate(payload))
    assert info.accepted is True


def test_empty_table_is_not_an_artifact():
    """代码跑通但只回传空表 → 验收不通过 → 归到 empty_result（可修）。"""
    payload = {"ok": True, "text": "", "tables": {"结果": []}, "charts": []}
    info = classify_error(payload, acceptance_gate(payload))
    assert info.category == CATEGORY_EMPTY
    assert info.retryable is True


def test_timeout_uses_structured_flag_not_copy():
    """超时以 `timed_out` 结构化标记为准：stderr 文案改了也不该失准。"""
    payload = {"ok": False, "timed_out": True, "stderr": "whatever wording"}
    info = classify_error(payload, acceptance_gate(payload))
    assert info.category == CATEGORY_TIMEOUT


def test_exit_137_is_resource_limit():
    payload = {"ok": False, "exit_code": 137, "stderr": "no clear message"}
    info = classify_error(payload, acceptance_gate(payload))
    assert info.category == CATEGORY_RESOURCE
    assert info.detail["signal"] == "SIGKILL"


def test_environment_failure_is_not_retryable():
    info = _classify(CATEGORY_ENVIRONMENT)
    assert info.retryable is False
    assert info.detail  # 保留原始信息便于排障


# ---- signature：可用、稳定、可比较 ----

def test_last_exception_picks_final_exception():
    stderr = ("Traceback (most recent call last):\n"
              '  File "/out/_analysis.py", line 3, in <module>\n'
              "    df['x']\n"
              "ValueError: boom\n"
              "KeyError: 'x'")
    assert last_exception(stderr) == ("KeyError", "'x'")


def test_signature_ignores_line_numbers_and_addresses():
    a = "File \"/out/_analysis.py\", line 12\nKeyError: '销售额'"
    b = "File \"/out/_analysis.py\", line 88\nKeyError: '销售额'"
    assert error_signature(CATEGORY_COLUMN, a) == error_signature(CATEGORY_COLUMN, b)


def test_normalize_strips_volatile_parts():
    text = normalize_message('File "x.py", line 42\nMemoryError at 0x7ffd1234, need 4096 MB')
    assert "42" not in text and "0x7ffd1234" not in text and "4096" not in text
    assert "MemoryError" in text


def test_signature_differs_between_categories():
    assert (error_signature(CATEGORY_SYNTAX, "boom")
            != error_signature(CATEGORY_COLUMN, "boom"))


def test_classification_is_deterministic():
    """同一输入必须稳定得到同一指纹——复读检测依赖它。"""
    first = _classify(CATEGORY_COLUMN)
    second = _classify(CATEGORY_COLUMN)
    assert first.signature == second.signature
    assert first.to_dict()["signature"] == second.to_dict()["signature"]


def test_error_info_is_json_serializable():
    import json

    payload = json.loads(json.dumps(_classify(CATEGORY_TYPE).to_dict(), ensure_ascii=False))
    assert payload["category"] == CATEGORY_TYPE


def test_attribute_error_maps_to_name_error():
    """`df.销售额` 这类属性写法也应当走「对齐真实 schema / 符号」的修复方向。"""
    payload = {
        "ok": False, "exit_code": 1,
        "stderr": ("Traceback (most recent call last):\n"
                   '  File "/out/_analysis.py", line 5, in <module>\n'
                   "    df.销售额\n"
                   "AttributeError: 'DataFrame' object has no attribute '销售额'"),
    }
    assert classify_error(payload, acceptance_gate(payload)).category == CATEGORY_NAME


# ---- 沙箱基础设施失败：结构化标记与文案双保险 ----

DOCKER_DOWN_STDERR = (
    "failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; "
    "check if the path is correct and if the daemon is running: open //./pipe/"
    "dockerDesktopLinuxEngine: The system cannot find the file specified."
)


def test_docker_daemon_down_is_environment_without_structured_flag():
    """历史运行 / 其他调用方可能不带 failure_kind，光看 stderr 也要认出"环境不可用"。"""
    payload = {"ok": False, "exit_code": 125, "stderr": DOCKER_DOWN_STDERR}
    info = classify_error(payload, acceptance_gate(payload))
    assert info.category == CATEGORY_ENVIRONMENT
    assert info.retryable is False


def test_docker_daemon_down_is_environment_with_structured_flag():
    payload = {"ok": False, "exit_code": 125, "failure_kind": "sandbox_unavailable",
               "stderr": DOCKER_DOWN_STDERR}
    assert classify_error(payload, acceptance_gate(payload)).category == CATEGORY_ENVIRONMENT


def test_infra_markers_do_not_fire_on_ordinary_errors():
    """普通业务错误不能被误判成"环境不可用"（否则该修的也不修了）。"""
    from backend.agent.sandbox import is_infra_failure

    assert is_infra_failure("KeyError: '销售额'") is False
    assert is_infra_failure("") is False
    assert is_infra_failure("MemoryError: Unable to allocate 3.2 GiB") is False
    assert is_infra_failure(DOCKER_DOWN_STDERR) is True


def test_run_in_sandbox_marks_daemon_outage(monkeypatch, tmp_path):
    """宿主侧判定：`docker run` 因守护进程不可用失败时，必须带上结构化标记。"""
    import subprocess

    from backend.agent import sandbox as sandbox_mod

    class _Proc:
        returncode = 125
        stdout = ""
        stderr = DOCKER_DOWN_STDERR

    monkeypatch.setattr(sandbox_mod, "resolve_docker", lambda: "docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())

    src = tmp_path / "d.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    result = sandbox_mod.run_in_sandbox("smoke-infra", "print(1)", {"d.csv": str(src)})

    assert result.ok is False
    assert result.failure_kind == "sandbox_unavailable"
    assert classify_error(
        {"ok": result.ok, "stderr": result.stderr, "exit_code": result.exit_code,
         "failure_kind": result.failure_kind},
        acceptance_gate({"ok": False, "stderr": result.stderr}),
    ).category == CATEGORY_ENVIRONMENT
