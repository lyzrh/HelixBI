"""Self-Repair 离线评测场景（确定性、无 Docker / 无 LLM）。

## 这些场景是什么

每条场景描述「一个查询会遇到什么样的执行失败序列」，然后由
`backend/evaluation/repair_bench.py` 用**桩执行器 + 桩 LLM** 驱动**真实图谱**
（`backend.agent.graph.stream_analysis`）跑一遍，检查 V1 / V2 两种重试策略的决策结果。

被桩化的只有两个外部边界——LLM 与 Docker 沙箱（本机没有可用沙箱时无法真实执行）；
分类、策略选择、复读检测、额度控制、路由、prompt 组装、token 记账全部是真实代码路径。

## 场景里唯一的建模假设（必须显式声明）

`failures[].fixed_by` 表示"当**注入的修复提示**属于这些策略时，下一轮生成即修复成功"：

- 命中 `fixed_by` → 下一轮执行成功（定向处方解决了这个病因）；
- 未命中（例如 V1 始终注入统一提示）→ **同一个错误会再出现一次**（`repeat` 控制出现次数），
  这正是 V1 的病灶：提示词与病因无关，模型只能盲修。
- `fixed_by = ["*"]` 表示任何提示都能修好（用于隔离"只看停止策略"的场景）。

这条假设是**假设**，不是实测结论；`--repair-benchmark --live` 会调用真实 LLM + Docker
复核它。没有沙箱时报告里会明确标注这是离线策略仿真。
"""

# 桩执行器的默认产出
SUCCESS_RESULT = {
    "ok": True,
    "stdout": "",
    "text": "结论：目标指标为 1,234，环比上升 8.6%。",
    "tables": {"结果": [{"维度": "A", "值": 1234}]},
    "charts": [],
}

# 各类失败的构造结果（桩执行器返回，字段与真实 SandboxResult 对齐）
FAILURE_TEMPLATES = {
    "syntax_error": {
        "ok": False, "exit_code": 1, "failure_kind": "exit_code",
        "stderr": '  File "/out/_analysis.py", line 12\n    df = pd.read_csv("/data/sample_sales.csv"\n'
                  '                                       ^\n'
                  'SyntaxError: \'(\' was never closed',
    },
    "column_error": {
        "ok": False, "exit_code": 1, "failure_kind": "exit_code",
        "stderr": 'Traceback (most recent call last):\n'
                  '  File "/out/_analysis.py", line 18, in <module>\n'
                  '    total = df["销售额"].sum()\n'
                  'KeyError: \'销售额\'',
    },
    "type_error": {
        "ok": False, "exit_code": 1, "failure_kind": "exit_code",
        "stderr": 'Traceback (most recent call last):\n'
                  '  File "/out/_analysis.py", line 21, in <module>\n'
                  '    df["日期"] = pd.to_datetime(df["日期"])\n'
                  'ValueError: time data "2026/01/01" does not match format "%Y-%m-%d"',
    },
    "empty_result": {
        # 执行成功但**没有回传任何产物**（没调 save_text、没有表、没有图）
        "ok": True, "exit_code": 0, "failure_kind": "exit_code",
        "stdout": "done", "text": "", "tables": {}, "charts": [],
    },
    "timeout": {
        "ok": False, "timed_out": True, "failure_kind": "timeout",
        "stderr": "执行超时（>120s），请优化代码性能或减少数据量。",
    },
    "resource_limit": {
        "ok": False, "exit_code": 137, "failure_kind": "resource_limit",
        "stderr": "Killed\nMemoryError: Unable to allocate 3.2 GiB for an array",
    },
    "name_error": {
        "ok": False, "exit_code": 1, "failure_kind": "exit_code",
        "stderr": 'Traceback (most recent call last):\n'
                  '  File "/out/_analysis.py", line 9, in <module>\n'
                  '    df = pd.read_csvs("/data/sample_sales.csv")\n'
                  'AttributeError: module \'pandas\' has no attribute \'read_csvs\'',
    },
    "contract_error": {
        "ok": False, "exit_code": 1, "failure_kind": "exit_code",
        "stderr": 'Traceback (most recent call last):\n'
                  '  File "/usr/local/lib/python3.11/site-packages/dahelper.py", line 47\n'
                  'TypeError: Object of type int64 is not JSON serializable',
    },
    "environment": {
        "ok": False, "failure_kind": "sandbox_unavailable",
        "stderr": "未找到 docker 可执行文件：请确认 Docker Desktop 已安装并运行。",
    },
}


def _failure(category: str, fixed_by: list[str], repeat: int = 4, **overrides) -> dict:
    payload = dict(FAILURE_TEMPLATES[category])
    payload.update(overrides)
    return {"category": category, "fixed_by": list(fixed_by), "repeat": repeat,
            "result": payload}


# ---------------------------------------------------------------------------
# 场景集：覆盖需求里枚举的 12 类情形
# ---------------------------------------------------------------------------

SCENARIOS: list[dict] = [
    {
        "id": "sr01_first_pass_success", "kind": "first_pass",
        "title": "首次执行即成功（理想路径）",
        "question": "各品类销售额是多少？",
        "failures": [],
        "expect": {"outcome": "success", "first_pass_success": True, "repair_attempts": 0,
                   "executions": 1, "repeated_error": False},
    },
    {
        "id": "sr02_syntax_fixed", "kind": "syntax_error",
        "title": "SyntaxError → 定向修复成功",
        "question": "各品类销售额是多少？",
        "failures": [_failure("syntax_error", ["code_structure"])],
        "expect": {"outcome": "success", "first_pass_success": False, "repair_attempts": 1,
                   "error_category": "syntax_error", "repair_strategy": "code_structure"},
        # V1 的无差别提示修不好结构性错误，只能把 3 次额度烧完
        "expect_v1": {"outcome": "exhausted", "repair_attempts": 3, "executions": 4},
    },
    {
        "id": "sr03_column_fixed", "kind": "column_error",
        "title": "ColumnNotFound (KeyError) → 对齐真实 schema 后成功",
        "question": "各品类销售额是多少？",
        "failures": [_failure("column_error", ["schema_alignment"])],
        "expect": {"outcome": "success", "repair_attempts": 1,
                   "error_category": "column_error", "repair_strategy": "schema_alignment"},
    },
    {
        "id": "sr04_type_fixed", "kind": "type_error",
        "title": "TypeError / ValueError → 修正 dtype 与转换后成功",
        "question": "近 90 天销售额趋势如何？",
        "failures": [_failure("type_error", ["dtype_conversion"])],
        "expect": {"outcome": "success", "repair_attempts": 1,
                   "error_category": "type_error", "repair_strategy": "dtype_conversion"},
    },
    {
        "id": "sr05_empty_fixed", "kind": "empty_result",
        "title": "结果为空（无任何产物）→ 排查过滤条件与字段取值后成功",
        "question": "华东区的销售额是多少？",
        "failures": [_failure("empty_result", ["filter_and_scope"])],
        "expect": {"outcome": "success", "repair_attempts": 1,
                   "error_category": "empty_result", "repair_strategy": "filter_and_scope"},
    },
    {
        "id": "sr06_timeout_fixed", "kind": "timeout",
        "title": "执行超时 → 降低计算量后成功（该类额度 1 次）",
        "question": "按天给出全量明细的销售额",
        "failures": [_failure("timeout", ["bounded_compute"], repeat=4)],
        "expect": {"outcome": "success", "repair_attempts": 1,
                   "error_category": "timeout", "repair_strategy": "bounded_compute"},
        "expect_v1": {"outcome": "exhausted", "repair_attempts": 3},
    },
    {
        "id": "sr07_resource_limit_fixed", "kind": "resource_limit",
        "title": "OOM / 资源超限（exit 137）→ 降低内存后成功",
        "question": "全量数据做多维交叉分析",
        "failures": [_failure("resource_limit", ["memory_bound"], repeat=4)],
        "expect": {"outcome": "success", "repair_attempts": 1,
                   "error_category": "resource_limit", "repair_strategy": "memory_bound"},
    },
    {
        "id": "sr08_repeated_identical", "kind": "repeated_error",
        "title": "同一错误连续出现（复读）→ 提前终止，不无限重试",
        "question": "各门店销售额排名？",
        "failures": [_failure("column_error", [], repeat=4)],
        "expect": {"outcome": "exhausted", "repair_attempts": 1, "repeated_error": True,
                   "repeat_kind": "identical", "executions": 2},
        # V1 没有复读检测：同一个错误会一直重试到额度上限
        "expect_v1": {"outcome": "exhausted", "repair_attempts": 3, "repeated_error": False,
                      "executions": 4},
    },
    {
        "id": "sr09_multi_error_then_success", "kind": "multi_error",
        "title": "多次不同错误（语法 → 列名 → 类型）后成功",
        "question": "近 90 天各品类销售额趋势？",
        "failures": [
            _failure("syntax_error", ["*"], repeat=1),
            _failure("column_error", ["*"], repeat=1),
            _failure("type_error", ["*"], repeat=1),
        ],
        "expect": {"outcome": "success", "repair_attempts": 3, "executions": 4,
                   "error_chain_len": 3},
    },
    {
        "id": "sr10_max_attempts_exhausted", "kind": "exhausted",
        "title": "连续四类不同错误且都修不好 → 达到 MAX_FIX_ATTEMPTS 后收手",
        "question": "各品类销售额是多少？",
        "failures": [
            _failure("syntax_error", [], repeat=1),
            _failure("column_error", [], repeat=1),
            _failure("type_error", [], repeat=1),
            _failure("empty_result", [], repeat=1),
        ],
        "expect": {"outcome": "exhausted", "repair_attempts": 3, "executions": 4},
    },
    {
        "id": "sr11_acceptance_failed_code_ok", "kind": "acceptance_failed",
        "title": "Result Acceptance 未通过但代码执行成功（回传了空结果表）→ 触发修复并成功",
        "question": "各品类销售额是多少？",
        "failures": [_failure("empty_result", ["filter_and_scope"], repeat=2,
                              text="", tables={"结果": []})],
        "expect": {"outcome": "success", "first_pass_success": False, "repair_attempts": 1,
                   "error_category": "empty_result"},
    },
    {
        "id": "sr12_environment_unavailable", "kind": "environment",
        "title": "沙箱环境不可用 → 直接 fallback（不浪费重试额度）",
        "question": "各品类销售额是多少？",
        "failures": [_failure("environment", [], repeat=4)],
        "expect": {"outcome": "fallback", "repair_attempts": 0, "executions": 1,
                   "error_category": "environment"},
        # V1 会把额度烧在"重试也不可能成功"的环境错误上
        "expect_v1": {"outcome": "exhausted", "repair_attempts": 3, "executions": 4},
    },
]

# Skill 重放守卫场景：重放路径**不进入** Self-Repair（单独断言，不参与策略对比）
REPLAY_CASES = [
    {"id": "sr13_replay_success_no_repair", "title": "Skill 高置信重放成功 → 0 次修复、0 次 LLM",
     "ok": True, "expect": {"outcome": "success", "repair_attempts": 0, "llm_calls": 0}},
    {"id": "sr14_replay_failure_fallback", "title": "Skill 重放失败 → 交由上层 fallback 到 Agent（不在重放里自修复）",
     "ok": False, "expect": {"outcome": "fallback", "repair_attempts": 0, "llm_calls": 0}},
]


def scenarios() -> list[dict]:
    return SCENARIOS


def replay_cases() -> list[dict]:
    return REPLAY_CASES


__all__ = ["FAILURE_TEMPLATES", "REPLAY_CASES", "SCENARIOS", "SUCCESS_RESULT",
           "replay_cases", "scenarios"]
