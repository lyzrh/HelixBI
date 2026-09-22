"""结果验收的硬门槛（纯函数，Agent 内核层）。

为什么单独成模块：验收结论有两个消费者——

- `Run.trace` 的可观测段（analysis 层，`analysis/validation.py` 扩展出软检查）；
- Self-Repair 的「这一轮到底算不算成功、要不要修」（agent 内核层，
  `agent/repair.py::decide_repair`）。

把硬门槛放在内核层，analysis 层复用它再附加运行期检查（图表文件是否真的落盘、
结果表结构是否完整、stderr 是否干净），避免出现两套「执行成没成功」的判断各自漂移。
依赖方向因此仍然是 `analysis/` → `agent/`（与 `runtime.py` 复用 `graph.py` 一致）。
"""

# 硬失败项：不通过即视为整轮失败（也是 Self-Repair 的触发条件）
HARD_CHECKS = ("execution_ok", "has_artifact")


def meaningful_tables(execution: dict) -> dict:
    """只保留「真的有行」的结果表。空表不是可交付产物。"""
    tables = execution.get("tables") or {}
    if not isinstance(tables, dict):
        return {}
    return {name: rows for name, rows in tables.items()
            if isinstance(rows, list) and rows}


def has_artifact(execution: dict) -> bool:
    """有可交付产物：文字结论 / 有行的结果表 / 图表。

    注意与旧口径的差别：`{"t": []}` 这种**空结果表**曾经被算作产物，于是
    「代码跑通但什么都没算出来」进了 summarize，用户拿到一段没有数字的结论。
    现在它算验收不通过，交由 Self-Repair 按其错误类别定向修复（见 `empty_result`）。
    """
    if str(execution.get("text") or "").strip():
        return True
    if execution.get("charts"):
        return True
    return bool(meaningful_tables(execution))


def acceptance_gate(execution: dict) -> dict:
    """硬验收门 → `{passed, failed, execution_ok, has_artifact}`。

    只判 `HARD_CHECKS`；软检查（图表落盘、表结构、stderr）不拦流程，只写进 trace。
    """
    execution = execution or {}
    execution_ok = bool(execution.get("ok"))
    artifact = has_artifact(execution)
    checks = [
        {"name": "execution_ok", "ok": execution_ok,
         "detail": "" if execution_ok else (str(execution.get("stderr") or "")[-200:]
                                            or "沙箱执行未成功")},
        {"name": "has_artifact", "ok": artifact,
         "detail": f"text={len(str(execution.get('text') or '').strip())}字 "
                   f"tables={len(meaningful_tables(execution))} "
                   f"charts={len(execution.get('charts') or [])}"},
    ]
    failed = [c["name"] for c in checks if not c["ok"]]
    return {"passed": not failed, "failed": failed, "checks": checks,
            "execution_ok": execution_ok, "has_artifact": artifact}


__all__ = ["HARD_CHECKS", "acceptance_gate", "has_artifact", "meaningful_tables"]
