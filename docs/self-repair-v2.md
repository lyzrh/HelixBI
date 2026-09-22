# Agent Self-Repair V2：错误分类 → 定向修复 → 有界重试 → 兜底

> 目标不是"多修几次"，而是**用更少的 LLM 调用修对更多错误，并且能解释每一次修复与放弃**。
> 核心原则：**宁可停手给出结构化失败，也不无限重新生成同一个错。**

- 范围：`backend/agent/`（repair / acceptance / graph / prompts / sandbox）、
  `backend/analysis/`（runtime 的 trace 与 SSE、validation 的验收门）、
  `backend/skills/engine.py`（重放路径不进自修复）、`backend/evaluation/`（度量与基准）。
- 约束：不引入 Multi-Agent / MCP / RAG / 向量库；**不新增任何 LLM 调用**（只是把已有那次
  调用的提示换成针对性的）；不改写下述既有机制，只在其上加分类与止停：
  LangGraph 线性流程、Docker 沙箱、Result Acceptance、Evaluation、Observability、Skill Retrieval V2。
- 复现命令：
  ```bash
  python -m backend.evaluation                              # 完整报告（含 Self-Repair 离线仿真段）
  python -m backend.evaluation --repair-benchmark            # V1 vs V2 对照表
  python -m backend.evaluation --repair-benchmark --repair-live   # 追加真实链路实测（需 Docker + LLM）
  pytest -q
  ```

---


> **数字校准说明（Cost & Latency Optimization V1 之后重跑）**：上表的 LLM 调用与 Token 来自同一次
> `--repair-benchmark` 重跑，绝对值比首版更低（V1 6.08→5.67、V2 5.00→4.17），原因是追问推荐改为
> **确定性生成**——每轮少一次 LLM 调用，两条策略同等受益。V1 / V2 的对照关系不受影响：比的是同一批
> 场景、同一个图谱，唯一变量仍是重试策略。成本侧的进一步优化（意图快路径 / 上下文瘦身 / 预算护栏 /
> 缓存）见 [cost-latency-v1.md](cost-latency-v1.md)。

## 1. Problem：旧自修复的三个真实缺口

改造前的自修复只有一条路径（`backend/agent/graph.py` V1）：

```
generate_code → execute → 失败？ → FIX_USER_TMPL（同一段话）→ generate_code → …
                            └─ 直到 attempts > MAX_FIX_ATTEMPTS
```

| 缺口 | 具体表现 | 后果 |
| --- | --- | --- |
| 提示词与病因无关 | 列名写错、`to_datetime` 类型不对、执行超时、结果为空——四种完全不同的病拿到同一段"请分析错误原因" | 模型只能盲修，改对是运气；结构性错误尤其修不动 |
| 不识别复读 | 第二次返回**一模一样**的 traceback，系统照旧再烧一次 token | `MAX_FIX_ATTEMPTS` 成了唯一刹车，Token 花在同一个坑里 |
| 不可解释 | `Run.trace` 只有 `attempts=3` 与 `repair_count` | 回答不了"为什么修 2 次才成功、第 1 次错在哪、第 3 次换了什么策略、复读发生在第几次" |

还有一个隐藏缺口：**验收门与重试决策是两套判断**。`validate_final()` 只写给 trace 看，
路由只看 `ok and (text or tables or charts)`——于是"回传了空结果表"这种"代码跑通但没算出来"
的情况被当成成功，直接进了 summarize。

---

## 2. Baseline（V1）的量化

真实执行指标需要 Docker 沙箱 + LLM。**本机 Docker 守护进程不可用**（`docker info` 失败），
因此按项目既有口径，`execution` / `self_repair` / `end_to_end` 三个阶段在报告里显示
**「未采集」+ 原因**，不编造数字。

为了仍然能对**策略本身**做出可评估的结论，本轮新增了**离线策略仿真**（见第 5 节）：
桩化 LLM 与沙箱两个外部边界，让**真实图谱**（分类、策略、复读检测、额度控制、路由、
prompt 组装、token 记账、验收门）跑完 12 条失败场景，V1 与 V2 用同一批场景对比。

Baseline（V1，离线仿真口径，12 条场景）：

| 指标 | V1 | V2 | 说明 |
| --- | ---: | ---: | --- |
| First-pass Success Rate | 8.3% | 8.3% | 相同（只由"第一次就写对"决定） |
| Repair Success Rate | 18.2% | **80.0%** | 修复后通过验收的比例 |
| Overall Success Rate | 25.0% | **75.0%** | 首次成功 + 修复成功 |
| Average Repair Attempts | 2.67 | **1.17** | 平均发起几次修复 |
| Repeated Error Rate | 0.0% | 8.3% | V1 无复读检测 |
| Repair Exhaustion Rate | 75.0% | **16.7%** | 把额度烧光的比例 |
| Fallback Rate | 0.0% | 8.3% | V2 对"重试也没用"的环境错误直接兜底 |
| LLM Calls / Query | 5.67 | **4.17** | −26.5% |
| Tokens / Query | 4096 | **2594** | −36.7% |
| Repair Latency p50 / p95 | 14.7s / 14.7s | **4.9s** / 14.7s | 折算口径（见第 5 节） |

---

## 3. Optimization：V2 的架构

```
generate_code
   → sandbox execute
   → classify        ← 新增节点：错误分类 + 结果验收门（backend/agent/repair.py + acceptance.py）
        ├─ 通过验收 ─────────────────────────────→ summarize
        ├─ 可修复且有额度 ─→ targeted repair hint ─→ generate_code（回到顶部）
        └─ 复读 / 额度用尽 / 不可修复 ────────────→ summarize（结构化失败，不抛异常）
```

仍然是线性 LangGraph（无 tool-calling、无新节点类型外的编排），只在 `execute` 之后插入了
`classify` 节点，并在 `AgentState` 上增加最小钩子字段。

### 3.1 错误分类（9 类 + 1 类"不该重试"）

分类是**纯函数**，只读执行态（`ok / stderr / exit_code / timed_out / failure_kind / 产物`），
不调 LLM、不读库（`backend/agent/repair.py::classify_error`）。优先级从"基础设施事实"到"文本线索"：

| 类别 | 触发依据（确定性规则） | 定向处方（注入给 LLM 的提示） |
| --- | --- | --- |
| `environment` | `failure_kind=sandbox_unavailable`（Docker 缺失/未启动） | **不重试**，直接结构化失败 |
| `timeout` | `timed_out` 结构化标记，或 stderr 命中超时文案 | 只读必要列/行、先过滤再聚合、向量化、`head(N)`、绘图基于聚合小表 |
| `resource_limit` | `exit_code=137`（SIGKILL）或 MemoryError/OOM 特征串 | 降 dtype、分块、避免中间结果物化、及时释放 |
| `empty_result` | 执行成功但**无可用产物**（无结论 / 结果表 0 行），即验收门不过 | 核对时间范围与时间列 min/max、`value_counts()` 核对过滤取值、检查 dropna、必须调 `save_text` |
| `syntax_error` | `SyntaxError` / `IndentationError` / `TabError` | **只修代码结构**（括号引号、全角标点、缩进、缺 `:`），不动口径 |
| `column_error` | `KeyError` / `ColumnNotFoundError` / `not in index` | 对齐「数据概况」真实列名 + 语义层 `field`，统一 `df["列名"]`，禁止臆造 |
| `type_error` | `TypeError` / `ValueError` / dtype 相关 | 修 dtype 与转换链路（`to_numeric` / `to_datetime` / 分母为 0 / 缺失值） |
| `name_error` | `NameError` / `UnboundLocalError` / `AttributeError` / `ImportError` | 只用已导入模块与已定义变量，检查是否把 DataFrame 当 dict |
| `contract_error` | stderr 命中 `dahelper` / `result.json` / `save_*` | 严格按 dahelper 契约回传（含 `save_text` 必须调用、JSON 可序列化） |
| `unknown` | 兜底 | 通用诊断：按 traceback 最后一帧定位，最小片段自证后再补齐 |

两点设计说明：

- **分类优先级里"事实"高于"文本"**：超时优先读 `timed_out`，OOM 优先读 `exit_code=137`。
  只靠 stderr 文案猜很脆（文案一改就失准），因此 `sandbox.py` 新增了
  `exit_code / timed_out / failure_kind` 三个结构化字段。
- **`contract_error` 优先于异常类型**：`TypeError: Object of type int64 is not JSON
  serializable` 发生在 dahelper 回传环节时，要修的是"怎么回传"而不是"怎么聚合"。

### 3.2 定向修复：同一段上下文，换掉处方

`prompts.FIX_USER_TMPL` 保留原来的基础上下文（上次代码 / stdout / stderr / 可用文件），
末尾的"请分析错误原因…"换成 `{repair_hint}`——由错误类别决定，且新增了
**历史失败清单**（前 3 轮的类别 + 报文 + 已试策略），让模型别重蹈覆辙：

```
## 历史失败（同样的错误不要重复犯）
1. [列不存在] '销售额'　已尝试策略：schema_alignment

【定向修复 · 列名映射】错误是「列 / 键不存在」。请只做列名对齐，不要改变分析口径：
① 先看上面「数据概况」里逐个列出的真实列名（注意大小写、前后空格、全角字符）；…
```

仍然是**同一次 LLM 调用**，不增加调用次数（只是 token 略增：定向处方比通用话术更长，
但换来的是更少的重试轮次）。

### 3.3 重试 / 停止策略（有界）

`decide_repair()` 是三段式判定，全部确定性：

```
① 复读检测   相同 signature → 立即停止（identical）
             与上一次同类错误 → 视为无进展，提前停止（equivalent）
② 全局上限   attempts > MAX_FIX_ATTEMPTS → exhausted（与 V1 完全一致：最多 1 + 3 次执行）
③ 每类额度   同类错误累计达到该类上限 → exhausted
             （超时/OOM/契约/未知 = 1 次；空结果 = 2 次；语法/列名/类型 = 3 次）
```

- **错误指纹**：`类别|归一化正文`，归一化会抹掉行号、内存地址与具体数字——
  "第 37 行 KeyError"与"第 41 行 KeyError"必须被判为同一个错误，这正是复读检测要抓的。
- **每类额度只会更早收手**，永远不会突破 `MAX_FIX_ATTEMPTS`（CI 有门禁断言）。
- **停止不等于报错**：停止后照旧走 `summarize`，把"执行几次 / 修几次 / 每轮错在哪 / 为什么停"
  注入结论 prompt，用户拿到的是结构化失败说明而不是异常。

### 3.4 LangGraph state 增量

`AgentState` 新增（全部有默认值，不改动 Skill Replay / QuerySpec / 语义解析 / 验收）：

| 字段 | 含义 |
| --- | --- |
| `repair_policy` | `v2`（默认） / `v1`（冻结基线，仅评估用） |
| `repair_attempt` | 已发起的修复次数 |
| `repair_status` | `not_needed` / `repairing` / `succeeded` / `exhausted` / `repeated_failure` / `not_repairable` |
| `error_category` / `error_signature` | 最近一次执行的错误类别与指纹 |
| `repair_strategy` / `repair_hint` | 采用的策略 id 与实际注入的处方文本 |
| `repair_reason` / `repair_repeat_kind` | 人类可读决策原因 / 复读类型 |
| `previous_errors` | 历史失败序列（类别 / 指纹 / 由哪个策略产生） |
| `repair_history` | 每次修复的起止耗时与结果（可观测性用） |

### 3.5 验收门统一

`backend/agent/acceptance.py` 成为硬验收门的唯一实现（`execution_ok` + `has_artifact`），
被三方复用：Self-Repair 的"要不要修"、`analysis/validation.py` 的 trace 结论、
`analysis/runtime.py` 的 `Run.ok`。分层上放在 agent 内核（`analysis/ → agent/`），
避免 `agent/` 反向依赖 `analysis/`。

顺带修正一个真实缺陷：**空结果表不算产物**。`{"结果": []}` 以前被算作 artifact，
"代码跑通但什么都没算出来"会直接进 summarize；现在它算验收不通过，
归类 `empty_result` 并触发"核对过滤条件 / 时间范围 / 字段取值"的定向修复
（离线场景 `sr11` 专门覆盖这条）。

---

## 4. Observability：`Run.trace.self_repair`

每次运行都会写入一段可自证的自修复记录（前端 `RunTrace` 面板已能展示）：

```json
{
  "policy": "v2",
  "first_pass_success": false,
  "outcome": "success",
  "repair_status": "succeeded",
  "repair_attempts": 3,
  "max_fix_attempts": 3,
  "executions": 4,
  "error_category": "type_error",
  "error_label": "类型/取值错误",
  "error_signature": "type_error|time data \"N/N/N\" does not match format \"%Y-%m-%d\"",
  "repair_strategy": "dtype_conversion",
  "repair_reason": "修复后通过结果验收",
  "repeated_error": false,
  "error_chain": ["语法错误", "列不存在", "类型/取值错误"],
  "errors": { "count": 3, "by_category": {"syntax_error": 1, "column_error": 1, "type_error": 1},
              "repeat_count": 0 },
  "attempts": [
    {"attempt": 1, "trigger_category": "syntax_error", "strategy": "code_structure",
     "duration_ms": 0, "ok": false, "result_category": "column_error"},
    {"attempt": 2, "trigger_category": "column_error", "strategy": "schema_alignment",
     "duration_ms": 0, "ok": false, "result_category": "type_error"},
    {"attempt": 3, "trigger_category": "type_error", "strategy": "dtype_conversion",
     "duration_ms": 0, "ok": true, "result_category": "unknown"}
  ],
  "repair_latency_ms": {"count": 3, "p50_ms": 0, "p95_ms": 0, "max_ms": 0, "total_ms": 0},
  "llm": {"calls": 7, "input_tokens": 3715, "output_tokens": 387, "cost_usd": 0.0}
}
```

**"为什么这个 Agent 修了 2 次才成功"** 现在有确定答案：`error_chain` 说是"语法错误 → 列不存在"，
`attempts[]` 说是"第 1 次用 `code_structure` 修好了语法，结果暴露出列名问题；
第 2 次用 `schema_alignment` 对齐真实列名后通过"，耗时与 token 也都能对到具体那一轮。

放弃的情形同样可解释（复读提前终止）：

```json
{
  "outcome": "exhausted",
  "repair_status": "repeated_failure",
  "repair_attempts": 1,
  "executions": 2,
  "error_category": "column_error",
  "error_signature": "column_error|'销售额'",
  "repair_reason": "复读：与上一次完全相同的错误（列不存在），提前终止",
  "repeated_error": true,
  "repeat_kind": "identical",
  "error_chain": ["列不存在", "列不存在"],
  "llm": {"calls": 4, "input_tokens": 1788, "output_tokens": 220, "cost_usd": 0.0}
}
```

对比 V1 在同样情形下的记录：`repair_attempts=3`、`executions=4`、`llm.calls=7`，
`error_chain` 是四遍一模一样的"列不存在"——省下的就是这一轮轮白烧的 token。

其它可观测细节：

- `stages[]` 里新增 `classify` 阶段（带 `error_category` / `repair_status` / `repair_strategy`），
  前端时间线能直接看出"这一轮是修还是不修、按什么类别修"；
- SSE 新增 `classify` 步骤文案（如「识别为「列不存在」→ 定向修复（schema_alignment）」），
  失败收尾时显示决策原因；
- Skill 重放路径的 trace 写入 `self_repair.policy = "not_applicable"`、
  `repair_attempts = 0`、`llm.calls = 0`——**重放仍然零 LLM、不进自修复**（可断言的事实）。

---

## 5. Evaluation：不测 happy path

### 5.1 两类评测，口径不混

| 类型 | 位置 | 是否实测 | 说明 |
| --- | --- | --- | --- |
| 真实链路（gated） | 报告 `execution` / `self_repair` / `end_to_end` 段；`--with-pipeline` | **是** | 需要 Docker + LLM；本机守护进程不可用 → 显示「未采集 + 原因」 |
| 离线策略仿真 | 报告 `self_repair_offline` 段；`--repair-benchmark` | 否（`measured=false`） | 桩化 LLM 与沙箱，驱动**真实图谱**跑 12 条失败场景，V1 vs V2 |

仿真里**只有两个边界被桩化**（LLM 与 Docker 执行），其余全是真实代码：
LangGraph 路由与状态、错误分类、策略选择、复读检测、额度控制、prompt 组装、
token 记账（写入临时 SQLite，不污染 `data/app.db`）、结果验收门。

**唯一的建模假设**（写在 `backend/evaluation/repair_cases.py` 顶部，报告里也会打印）：
*注入的修复提示与错误类型匹配时，下一轮生成即修复成功；否则同一个错误会再次出现。*
这条假设是"V1 的提示词与病因无关"的直接后果，也正是 `--repair-live` 要复核的对象。

### 5.2 覆盖的情形（12 条场景）

| 场景 | 情形 | V2 结局 | V1 结局 |
| --- | --- | --- | --- |
| sr01 | 首次执行成功 | success / 0 修复 | 相同 |
| sr02 | SyntaxError → 修复成功 | success / `code_structure` | exhausted（4 次执行） |
| sr03 | ColumnNotFound → 修复成功 | success / `schema_alignment` | exhausted |
| sr04 | TypeError/ValueError → 修复成功 | success / `dtype_conversion` | exhausted |
| sr05 | 结果为空（无产物）→ 修复成功 | success / `filter_and_scope` | exhausted |
| sr06 | 超时 → 修复成功（该类额度 1） | success / `bounded_compute` | exhausted |
| sr07 | OOM / exit 137 → 修复成功 | success / `memory_bound` | exhausted |
| sr08 | 相同错误连续出现（复读） | exhausted / 2 次执行（提前终止） | exhausted / 4 次执行 |
| sr09 | 多次不同错误（语法→列名→类型）后成功 | success / 3 次修复 | success（同样 4 次执行） |
| sr10 | 连续四类不同错误且都修不好 | exhausted / 恰好 3 次修复（= MAX_FIX_ATTEMPTS） | exhausted |
| sr11 | Result Acceptance 未通过但代码执行成功（空结果表） | success / 1 次修复 | success（3 次执行） |
| sr12 | 沙箱环境不可用 | fallback / 1 次执行、0 修复 | exhausted（把额度烧光） |
| sr13/14 | Skill 重放成功 / 失败 | 0 修复、0 LLM；失败交上层 fallback | 同 |

### 5.3 新增指标

`First-pass Success Rate`、`Repair Success Rate`、`Overall Success Rate`、
`Average Repair Attempts`、`Repeated Error Rate`、`Repair Exhaustion Rate`、`Fallback Rate`、
`LLM Calls / Query`、`Tokens / Query`、`Repair Latency p50 / p95`，
另加 `expectation_pass_rate`（场景判定与设计期望一致率）与 `replay_guard`（重放不进自修复）。

口径说明（诚实优先）：

- **Token** 由桩 LLM 按**真实 prompt 文本长度**折算（字符数 / 3.5，量级校准自本机实测
  `TokenUsage` 均值），并与真实 token 记账（临时库）交叉验证；
- **延迟**是本机无法实测的量：按声明常量折算（单次 LLM 1900ms、单次沙箱执行 3000ms，
  前者取自实测画像 `efficiency.MEASURED_FALLBACK_PROFILE`），只用于比较两条策略的相对代价，
  报告里明确写"非实测"；真实沙箱耗时仍然显示未采集；
- **成本**沿用全项目口径：未配置单价就显示「未配置单价」。

---

## 6. Benchmark：V1 vs V2

完整对照见 [self-repair-v2-benchmark.md](self-repair-v2-benchmark.md)（由
`python -m backend.evaluation --repair-benchmark --markdown docs/self-repair-v2-benchmark.md` 生成）。
结论：

| 维度 | 结论 |
| --- | --- |
| 修复能力 | 修复成功率 18.2% → **80.0%**；总成功率 25.0% → **75.0%** |
| 重试纪律 | 平均修复次数 2.67 → **1.17**；额度烧光率 75.0% → **16.7%** |
| 成本 | LLM 调用 5.67 → **4.17**/查询（−26.5%）；Token 4096 → **2594**/查询（−36.7%） |
| 延迟（折算） | 修复耗时 p50 14.7s → **4.9s** |
| 决策正确性 | 12 条场景判定与设计期望 **100%** 一致（V1/V2 各 12/12） |
| 既有能力 | Skill 重放仍 **0 LLM**（`replay_llm_calls=0`，重放占比 96.9%）；65 条问题集指标无回归；False Replay 仍为 0 |

**这些数字只能读作"策略差异"**：它们来自离线仿真（`measured=false`），
真实执行成功率在 Docker 可用前一律标为「未采集」。

---

## 7. 测试与 CI 门禁

新增 97 条用例（`pytest -q`：276 passed / 4 skipped）：

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_error_classifier.py` | 9 类错误 + 环境不可用、结构化标记优先于文案、指纹归一化与稳定性、确定性 |
| `tests/test_repair_strategy.py` | 每类错误有独立策略与提示、定向关键词、超时/OOM 额度收紧、复读停止、全局与每类上限、V1 冻结行为、场景脚本与分类器一致 |
| `tests/test_self_repair_loop.py` | 真实图谱集成：首轮成功不进修复、定向提示确实不同、历史失败进提示、复读提前终止、`MAX_FIX_ATTEMPTS` 守界、失败仍有结论、空结果触发修复、节点顺序、trace 字段与落库往返、Skill few-shot 不受影响、重放 trace 不进自修复 |
| `tests/evaluation/test_self_repair_eval.py` | CI 门禁：仿真的"非实测"自证、场景覆盖、指标完整性、V2 优于 V1（成功率↑ / 调用↓ / Token↓）、上限未被放大、场景期望一致率 100%、重放零 LLM、可复现、报告叙事完整 |

门禁阈值（只收紧不放宽，`tests/evaluation/test_self_repair_eval.py::GATE`）：
`v2_overall_success_rate ≥ 0.70`、`v2_repair_success_rate ≥ 0.60`、
`v2_max_avg_repair_attempts ≤ 1.50`、`expectation_pass_rate == 1.0`、`scenarios ≥ 12`、
`replay_guard == 2/2`。

---

## 8. Result

- 自修复从"统一重试"变成"**分类 → 定向 → 有界 → 兜底**"的机制：每个错误类别有针对性处方，
  复读与额度用尽都会提前终止，不可修复的情形（环境不可用）直接兜底。
- LLM 调用与 token 双双下降（同一批场景 −17.8% / −34.0%），修复成功率与总成功率显著上升。
- `Run.trace.self_repair` 让每一次修复与放弃都可解释、可对比、可聚合（前端已可视化）。
- 既有能力零回归：Skill 高置信重放仍 0 LLM 且不进自修复、重放失败仍 fallback 到 Agent、
  Workspace / RBAC 未触碰、65 条固定问题集的语义/口径/检索/准入指标不变。

---

## 9. 遗留问题（诚实清单）

1. **真实执行指标仍未采集**：本机 Docker 守护进程不可用，`execution` / `self_repair` /
   `end_to_end` 三段仍是「未采集 + 原因」。离线仿真的结论必须在有沙箱的环境用
   `--repair-benchmark --repair-live` 复核（脚本已就绪，缺资源时如实 skipped）。
2. **仿真的建模假设未经验证**：*"定向提示命中即修好"* 是基于 V1 病灶的推断，
   真实 LLM 可能一次修不好或需要两轮。live 模式就是为复核它准备的。
3. **每类额度是启发式**：超时/OOM 收紧到 1 次、空结果 2 次，来自"再修一次通常还是同样结果"
   的工程判断，尚未在真实链路标定；`config.REPAIR_REPEAT_LIMIT` 与
   `repair.CATEGORY_ATTEMPT_CAP` 是唯一改参数的地方。
4. **延迟折算是常量**：沙箱单次 3000ms 是声明值，不是实测。
5. **分类规则基于 traceback 文案**：已尽量用结构化字段（`exit_code` / `timed_out` /
   `failure_kind`）打底，但 pandas 新版本换措辞时仍可能需要补特征串
   （`repair.py::_PANDAS_MARKERS` 是唯一入口）。
6. **未做**：跨轮次的错误记忆（同一会话内"上次也栽在列名上"）、按数据源记忆高频错误类别、
   修复策略的成功率在线统计（用于把额度从静态配置变成自适应）。这些需要真实遥测，暂不做。
