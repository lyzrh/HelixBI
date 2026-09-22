"""运行结果验收：对最终态做确定性检查，产出可观测的 validation 段。

为什么需要它：`Run.ok` 只是一个布尔，回答不了"这轮结果到底可不可信"。
验收把「执行成功 / 有产物 / 结论非空 / 图表文件真的存在 / 表格结构完整 / stderr 干净」
拆成可列举的检查项写进 `run.trace`，前端时间线与评估都能读同一份结论。

分层：**硬门槛**（execution_ok / has_artifact）在 `backend.agent.acceptance`——
Self-Repair 也要用它决定「要不要修」，两处必须是同一把尺子；本模块在硬门槛之上
附加运行期软检查（文件落盘、表结构、stderr），软检查失败只降级为 `warn`，不拦流程。
"""

from pathlib import Path

from backend.agent.acceptance import HARD_CHECKS, acceptance_gate, meaningful_tables


def _chart_exists(run_dir: str, name: str) -> bool:
    if not run_dir or not name:
        return False
    return (Path(run_dir) / "out" / name).exists()


def validate_final(final: dict) -> dict:
    """→ `{"status": "ok"|"warn"|"fail", "checks": [...], "failed": [...]}`"""
    execution = (final or {}).get("execution") or {}
    gate = acceptance_gate(execution)
    charts = execution.get("charts") or []
    tables = execution.get("tables") or {}
    text = (execution.get("text") or "").strip()
    answer = (final.get("answer") or "").strip()
    run_dir = execution.get("run_dir") or ""
    stderr = (execution.get("stderr") or "").strip()
    ok = gate["execution_ok"]

    checks: list[dict] = []

    def add(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(passed), "detail": detail})

    for check in gate["checks"]:
        add(check["name"], check["ok"], check["detail"])
    add("answer_present", bool(answer), f"{len(answer)} 字符")

    missing_charts = [c for c in charts if not _chart_exists(run_dir, c)]
    add("charts_on_disk", not missing_charts,
        f"缺失 {missing_charts}" if missing_charts else f"{len(charts)} 个图表文件")

    bad_tables = [
        name for name, rows in tables.items()
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict)
    ]
    add("tables_wellformed", not bad_tables,
        f"异常结果表 {bad_tables}" if bad_tables else f"{len(meaningful_tables(execution))} 张结果表")

    add("clean_stderr", not (ok and stderr), stderr[-200:] if (ok and stderr) else "无告警输出")

    hard_failed = [c["name"] for c in checks if not c["ok"] and c["name"] in HARD_CHECKS]
    soft_failed = [c["name"] for c in checks if not c["ok"] and c["name"] not in HARD_CHECKS]
    if not ok or hard_failed:
        status = "fail"
    elif soft_failed:
        status = "warn"
    else:
        status = "ok"
    return {"status": status, "checks": checks,
            "failed": [c["name"] for c in checks if not c["ok"]]}


__all__ = ["validate_final", "HARD_CHECKS", "acceptance_gate"]
