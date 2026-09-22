# Cost & Latency Optimization V1 —— 让分析更省、更快，且不牺牲正确性

> 一句话结论：在**不改动任何分析口径、不绕过任何安全检查**的前提下，把一次需要 Agent 的
> 查询从 **4.00 次 LLM 调用 / 3346 token** 降到 **2.19 次 / 1925 token**（−45.4% / −42.5%），
> 全查询口径的 **Token / 查询下降 42.5%**，确定性阶段耗时 p50 从 15.8ms 降到 14.3ms；
> 而"跳过 LLM 解析"的那 81.5% 查询，其意图解析结果与人工标注 **100% 一致**。

---

## 1. Problem

HelixBI 的分析链路是"语义解析 → 生成代码 → 沙箱执行 → 自修复 → 结论整理 → 推荐追问"。
前几轮把**可靠性**（Self-Repair V2）与**安全**（Security Hardening V1）做扎实了，但**每一轮
分析固定要付 4 次 LLM 调用**，其中至少两次的价值是有疑问的：

| 调用 | 问题 |
| --- | --- |
| `parse_intent` | 项目里已有一个**确定性**语义解析器（65 条问题集严格准确率 90.8%，零 token），却仍然每次都问 LLM |
| `generate_code` | prompt 里塞了整包语义层、两例 few-shot 完整代码、重复的文件清单 |
| `summarize` | 结果表整表进 prompt（最多 100 行 × N 张表） |
| `suggest_followups` | 用一次 LLM 调用生成三句模板化的"下钻 / 趋势 / 对比"问题 |

同时还有几处**与 LLM 无关的确定性浪费**：同一次分析里 Skill 检索跑了两遍、
同一批数据的画像被反复重读、语义层渲染每次重算。

成本问题在没有度量之前只能靠感觉。所以本轮先建基线。

## 2. Baseline

度量口径（**能实测的实测，测不到的不编造**）：

- **LLM 调用次数**：真实图谱节点发出的调用次数（桩只替换模型，不替换决策）；
- **输入 / 输出 token**：对**真实拼装出来的 prompt 文本**用真实分词器计数
  （`backend/agent/tokens.py`，报告里标注所用编码器，本轮为 `tiktoken:o200k_base`）；
- **确定性耗时**：图谱总墙钟减去桩内耗时，剩下的是真实代码（解析 / 检索 / 装配 / 分类 / 落库）；
- **LLM 与沙箱耗时**：本机没有可用 Docker、也不该为基准去烧真实 provider 额度，
  因此这部分是**线性投影**（`MODELED` 常量），在报告里显式标注 `modeled`；
- 重放率 / 兜底率 / False Replay：来自真实的检索与准入判定，不建模。

基线（改造前，65 条固定问题集，`python -m backend.evaluation --cost-benchmark`）：

```
Agent 路径：4.00 次 LLM 调用 / 3346 token / 查询
生成 prompt 上下文：p50 926 tok，p95 1220 tok
确定性阶段：p50 16.06 ms（p95 受机器负载影响，仅作"未变差"参考）
整体投影：p50 3850 ms（含建模的 LLM 2480ms + 沙箱 2200ms）
全查询口径：0.12 次调用 / 102.9 token（重放占 96.9%，永远是 0 调用）
```

基线冻结方式与仓库既有约定一致：**同一份代码 + 一组冻结开关**
（`INTENT_MODE=llm`、`CONTEXT_POLICY=v1`、`FOLLOWUP_MODE=llm`），
不是另写一套"旧实现"来对比。

## 3. Cost Bottleneck（按测量定位，不靠直觉）

### 3.1 调用次数：4 次里至少有 2 次是可省的

```
[基线] parse_intent(LLM) → generate_code(LLM) → execute → summarize(LLM) → followups(LLM)
[优化] parse_intent(确定性，0 调用) ─────────┘        └─────────────┘  └ 确定性模板
```

`parse_intent` 的价值只在"问题里有确定性解析器理解不了的东西"时才体现（例如取值过滤
「华东地区的销售额」）。对"各品类的总销售额是多少"这类问题，让它再解析一遍是纯开销。

`followups` 推荐的三个问题本质上可以从**本次的指标与维度**直接拼出来，
而它不影响任何分析结论——用一次调用换三句模板问题，性价比最低。

### 3.2 prompt 规模：一次生成请求的构成（实测，含 2 例 few-shot）

| 块 | 基线 | 占比 | 说明 |
| --- | ---: | ---: | --- |
| few-shot 示例代码 | 849 tok | 50% | 两例完整代码（真实生成代码 p50 39 行 / p95 57 行） |
| 数据概况（profile） | 408 tok | 24% | 防幻觉锚点，**必须保留** |
| 行业语义层 | 384 tok | 23% | 整包指标 + 维度 + 口径 + 图表建议 + 示例问题 |
| QuerySpec | 63 tok | 4% | 已确认意图 |
| **合计（首轮无历史）** | **1704 tok** | 100% | 多轮对话再加 672 tok 历史 |

三个可省的点一眼可见：**few-shot 给了两例**、**语义层注入了本次根本没用到的口径**、
**历史保留了 3 轮 × 600 字**。

### 3.3 重复计算：确定性开销也有浪费

- `routers/analysis.py` 先调 `route_query()`（召回 + 8 路打分 + 准入），紧接着又调
  `match_skills()`（**再召回 + 再打分一遍**）只为拿 few-shot 候选；
- `profile_all()` 每次都重新读盘（大 CSV / parquet 尤其明显），
  而同一次会话的后续提问用的是同一份数据；
- `resolve()` 每次重建术语索引并排序（一次运行里会被路由、意图、trace 各调一次）。

## 4. Optimization

七处改动，每一处都能单独关掉，关掉即回到基线行为。开关全部在 `backend/config.py`。

### ① 确定性意图快路径（`backend/agent/intent.py`）——省掉 1 次调用

命中条件（缺一不可）：

1. 解析器命中了至少一个语义层指标；
2. 置信度 ≥ `INTENT_FASTPATH_MIN_CONFIDENCE`（默认 0.60 = 指标 + 至少一个结构化信号）；
3. **问题里没有任何"解释不了的内容词"**——这一条是正确性底线。

第 3 条是本设计的核心。确定性解析器能识别「区域」这个维度，但识别不了「华东」这个**取值**；
如果不加这条门，"华东地区的销售额"会被解析成"按区域分组"，生成出来的代码就是错的——
**省一次调用却答错问题，比多花一次调用糟得多**。所以门一旦不过，立刻回落 LLM。

快路径还会把确定性独有的信息显式给足：`ranking`（排序方向 + TopN）、`time_phrase`
（用户原话里的时间短语）、绝对化的 `time_range`（「上周」→ 具体起止日期），
避免因为"没有改写环节"而丢约束。

### ② 上下文装配器（`backend/agent/context.py`）——省 token

把"该给 LLM 看什么"收成一处可测、可记账的确定性逻辑：

- **语义层只注入被引用的口径**：QuerySpec 一旦确定，其余指标 / 维度不再被这次分析引用；
  时间字段与业务口径（"同比怎么算"这类必须知道的背景）**保留**；
- **few-shot 只注入最相关的一例**（`SKILL_FEWSHOT_MAX`），超长代码带标注截断；
- **修复轮不再重复注入文件清单**（同一次请求的基础块里已有）；
- **历史收敛**到 2 轮 × 300 字（`HISTORY_TURNS` / `HISTORY_ANSWER_CHARS`）；
- **结论轮结果表按行收敛**（`SUMMARIZE_TABLE_ROWS=30`），**真实数字不改写**；
- 每次装配输出**分块 token 记账**，直接进 `Run.trace.performance.context`。

裁剪后（实测，同一条问题）：

| 场景 | 基线 | 优化后 | 降幅 |
| --- | ---: | ---: | ---: |
| 首轮提问 | 1704 tok | 1145 tok | −32.8% |
| 多轮对话（3 轮历史） | 2376 tok | 1531 tok | −35.6% |
| 修复轮附加上下文 | 1120 tok | 781 tok | −30.3% |

### ③ 确定性追问（`backend/agent/followups.py`）——省掉 1 次调用

`FOLLOWUP_MODE` 四档：`deterministic`（0 调用）/ **`hybrid`（默认：凑不满才补一次 LLM）** /
`llm`（冻结基线）/ `off`。追问的问题来自本次的指标、维度、粒度与语义包维度表，
不是编造；措辞不如 LLM 灵活——这是明确接受的取舍（见第 7 节）。

### ④ LLM 运行预算（`backend/agent/budget.py`）——拦病态，不拦正常

`MAX_LLM_CALLS_PER_RUN` / `MAX_INPUT_TOKENS_PER_RUN` / `MAX_OUTPUT_TOKENS_PER_RUN` /
`MAX_TOTAL_TOKENS_PER_RUN` / `MAX_RUN_COST_USD` / `MAX_REPAIR_ATTEMPTS`。

- **预检在调用之前**：能提前算出来的（调用次数、输入 token、成本下界）直接拦掉，
  超限的请求根本不发出；
- **终止原因进 trace**：`termination_reason` 写进 `Run.trace.cost_control`；
- **兜底不编造**：预算耗尽时结论由**沙箱的真实执行结果**拼出（执行成功与否、失败摘要、
  已产出的结果表规模），并明确写明"本轮达到预算上限，跳过了结论整理"；
- 默认值是**宽松上限**（8 次调用 / 40k 输入 token），只拦病态循环；
  `0` 或留空 = 不限制（回到改造前行为）；
- 修复额度**只允许收紧**：`MAX_REPAIR_ATTEMPTS` 永远不可能超过 `MAX_FIX_ATTEMPTS`。

### ⑤ 确定性缓存（`backend/agent/profiler.py` + `backend/analysis/cache.py`）——省延迟

只缓存**确定性、只读、与权限无关**的内容，key 里带能区分数据归属的维度：

| 缓存 | key | 失效于 |
| --- | --- | --- |
| 数据画像 | workspace + 挂载名 + (路径, 大小, mtime) | 文件被替换 / 追加 |
| 语义渲染 | pack + 命中的指标维度集合 | 语义包变更 / 重启 |
| 确定性解析 | 问题 + pack | 语义包变更 / 重启 |
| Skill 意图 | skill_id + updated_at + 问题 | Skill 更新 |

**不缓存任何身份判定**（权限、可见性、准入结论）——缓存"谁能看"是跨工作区污染的高危面；
`analysis/cache.py::describe()` 把这条约定写进登记表，测试会守住它。
命中 / 未命中增量进 `Run.trace.performance.cache`，"省下来的"是可验证的数据。

### ⑥ 消除重复检索（`routers/analysis.py` + `skills/engine.py`）

few-shot 候选直接复用路由阶段已经算好的候选（`AdmissionDecision.candidates` 已按分数排序），
不再重复 `visible_skills` + 8 路打分。可见性规则与检索层共用同一个实现，
**没有为了省一次查询而放松作用域校验**。

### ⑦ trace 可观测性扩展（`analysis/runtime.py`）

`Run.trace` 新增两段，回答"为什么调了 2 次 / Token 花在哪 / 哪个阶段最慢 / 为什么提前结束"：

```json
{
  "cost_control": {
    "llm_calls": 2,
    "calls_by_node": { "generate_code": 1, "summarize": 1 },
    "input_tokens": 1945, "output_tokens": 591, "total_tokens": 2536,
    "budget_limit": { "max_llm_calls": 8, "max_total_tokens": 60000 },
    "budget_used": { "llm_calls": 2, "total_tokens": 2536, "blocked_calls": 0 },
    "budget_utilization": { "llm_calls": 0.25, "total_tokens": 0.042 },
    "termination_reason": "", "blocked_node": "",
    "usage_source": "provider", "token_counter": "tiktoken:o200k_base",
    "intent_source": "deterministic", "followup_source": "skipped",
    "summarize_source": "llm"
  },
  "performance": {
    "bottleneck": { "node": "generate_code", "label": "生成分析代码", "duration_ms": 2473 },
    "stage_latency": [
      { "node": "parse_intent", "duration_ms": 20 },
      { "node": "generate_code", "duration_ms": 2473 },
      { "node": "execute", "duration_ms": 203 },
      { "node": "classify", "duration_ms": 0 },
      { "node": "summarize", "duration_ms": 928 },
      { "node": "suggest_followups", "duration_ms": 1 }
    ],
    "llm_stage_ms": 3422, "sandbox_stage_ms": 203,
    "cache": { "hits": 1, "misses": 3, "hit_rate": 0.25 },
    "context": {
      "generation": { "total_tokens": 746, "policy": "v2",
                      "blocks": [ { "name": "semantic", "tokens": 202 },
                                  { "name": "spec", "tokens": 63 },
                                  { "name": "data", "tokens": 408 } ] }
    }
  }
}
```

上例是**真实运行**（本机 Docker 不可用，故 `execute` 仅 203ms；
`parse_intent` 20ms = 确定性快路径、`suggest_followups` 1ms = 确定性追问）。
前端的「运行时间线」面板会把这些直接渲染出来。

## 5. Benchmark

`python -m backend.evaluation --cost-benchmark`（完整表见
[`cost-latency-v1-benchmark.md`](cost-latency-v1-benchmark.md)）。

### 5.1 Agent 路径单次成本（实测口径）

| 指标 | 基线（改造前） | 优化后 | 降幅 |
| --- | ---: | ---: | ---: |
| LLM 调用 / Agent 查询 | 4.00 | **2.19** | **−45.4%** |
| 输入 Token / Agent 查询 | 3162 | **1811** | **−42.7%** |
| 总 Token / Agent 查询 | 3346 | **1925** | **−42.5%** |
| 生成 prompt 上下文 p50 | 926 | 795 | −14.1% |
| 生成 prompt 上下文 p95 | 1220 | 1003 | −17.8% |

### 5.2 全查询口径（含 96.9% 重放路径，与既有报告同口径）

| 指标 | 基线 | 优化后 | 降幅 |
| --- | ---: | ---: | ---: |
| LLM 调用 / 查询 | 0.12 | 0.07 | −41.7% |
| 总 Token / 查询 | 102.9 | **59.2** | **−42.5%** |
| 重放占比 | 96.9% | 96.9% | 不变（路由未动） |
| 意图快路径命中率 | 0.0% | **81.5%** | +81.5pp |
| 追问零 LLM 率 | 0.0% | **100%** | +100pp |

### 5.3 延迟

| 指标 | 基线 | 优化后 | Δ |
| --- | ---: | ---: | ---: |
| 确定性阶段 p50（实测） | 16.06 ms | **14.37 ms** | −1.69 ms |
| 确定性阶段 p95（实测） | 44.75 ms | **29.78 ms** | −14.97 ms |
| 整体投影 p50 | 3850 ms | **3039 ms** | −811 ms |
| 整体投影 p95 | 3861 ms | **3450 ms** | −411 ms |

> p50 很稳（多次重跑在 14.3–15.3 ms 之间），p95 受机器负载与首轮冷缓存影响较大
> （同一份代码重跑可在 17–45 ms 间波动），因此 p95 只应读作"没有变差"，不要当成精确差值。

> 整体投影 = 确定性（实测）+ 建模的 LLM 与沙箱耗时。**LLM 与沙箱的真实耗时未采集**
> （本机无可用 Docker / 不为基准烧真实额度）——少掉一次 LLM 调用带来的真实延迟下降，
> 与"投影里少了一次调用的固定开销 + 那一次调用的 token 折算"是一致的，但不应被当作实测。

### 5.4 正确性证据（没有"省调用换查错"）

| 置信度门槛 | 快路径覆盖率 | 严格准确率（对人工标注） | 回落 LLM |
| ---: | ---: | ---: | ---: |
| 0.45 | 84.6% | **100.0%** | 10 条 |
| **0.60（线上默认）** | **81.5%** | **100.0%** | 12 条 |
| 0.70 | 69.2% | 100.0% | 20 条 |
| 0.85 | 9.2% | 100.0% | 60 条 |

"严格准确率"= 命中快路径的用例，其确定性 QuerySpec 与人工标注在
**指标 / 维度 / 同比环比 / 分析类型**四项上完全一致。
回落原因分布（默认门槛）：5 条未命中指标、4 条存在未解释内容、3 条置信度不足。

### 5.5 预算压力测试（把每次运行的 LLM 调用上限压到 2）

- 触发终止：5/6（83.3%），终止原因均为 `llm_call_limit`；
- 终止后仍给出**基于真实执行结果**的结论：100%（不抛异常、不编造数字）。

## 6. Result

- **调用**：Agent 路径 4.00 → 2.19 次（−45.4%）；快路径覆盖 81.5% 的查询；
- **Token**：Agent 路径 3346 → 1925（−42.5%）；全查询口径 102.9 → 59.2（−42.5%）；
- **延迟**：确定性阶段 p50 16.06 → 14.37 ms；整体投影 p50 −811ms（主要来自少一次 LLM 调用）；
- **正确性**：快路径命中处与人工标注 100% 一致；语义解析 / 口径注入 / Skill 检索 /
  重放准入 / False Replay / Self-Repair / 验收 / RBAC **全部未回归**（见第 8 节）；
- **可观测**：`Run.trace.cost_control` + `performance` 让"为什么调了 N 次""Token 花在哪"
  "哪个阶段最慢""为什么提前结束"都能从数据回答，而不是靠读代码猜。

## 7. Trade-offs（明确接受的代价）

1. **追问质量**：确定性追问的措辞不如 LLM 灵活。取舍是"追问只是建议、不影响结论"，
   且 `FOLLOWUP_MODE=llm` 一行可恢复；`hybrid` 在确定性凑不满时会自动补一次 LLM，保底不降级。
2. **few-shot 只给一例**：注入两例可能对复杂问题略有帮助，但代价是 +377 token/次；
   `SKILL_FEWSHOT_MAX=2` 可恢复。
3. **语义层收窄**：被裁掉的是"本次未被引用的口径"。如果模型偶尔需要参考同一张表的
   其它字段，它可以从「数据概况」（列名 / dtype / 取值示例，**未裁剪**）拿到。
   `CONTEXT_POLICY=v1` 可恢复。
4. **历史 2 轮 × 300 字**：极长的多轮追问可能需要更多上下文；`HISTORY_*` 可调。
5. **预算默认值**：8 次调用 / 40k 输入 token 是"宽松上限"，正常分析（含 3 次修复）不会触顶；
   但它**会**截断病态循环——这正是目的。

## 8. 不回归验证

| 维度 | 证据 |
| --- | --- |
| 语义解析 / 口径注入 | `tests/evaluation/test_semantic_resolution.py`、`test_analysis_plan.py` 全通过 |
| Skill 检索 V2 | Top1 78.5% / Recall@3 90.8% 不变（`test_skill_matching.py`） |
| 重放准入 / False Replay | False Replay 仍为 0、Precision 1.0、对抗集 100%（`test_retrieval_gate.py`） |
| **重放仍然 0 LLM** | `replay_guard` 6/6 断言通过；重放 trace 的 `cost_control.zero_llm = true` |
| Self-Repair V2 | 修复成功率 80% / 总成功率 75% 不变（`test_self_repair_eval.py`） |
| 结果验收 | 验收门未改动；`tests/test_observability.py` 全通过 |
| RBAC / 工作区 / 产物 | 安全测试全通过；黑盒冒烟 65/65（含 SEC01–SEC14） |
| 前端 | `vite build` 通过；运行时间线新增成本面板 |

完整测试：`pytest -q` → **464 passed, 4 skipped**。

## 9. Remaining Limitations（未做的与做不到的）

1. **真实沙箱与真实 provider 的耗时未采集**：本机 Docker 守护进程不可用，
   基准里 LLM / 沙箱时间是投影。资源可用时 `python -m backend.evaluation --with-pipeline`
   会把真实执行指标补上（缺失则如实显示"未采集"）。
2. **成本数字依赖单价配置**：`LLM_PRICE_*` 未配置时 `cost / query` 显示"未配置单价"，
   不拿编造的单价算美元数字。配好后 `MAX_RUN_COST_USD` 才会真正生效。
3. **token 计数与 provider 计费不完全等同**：本地用 `tiktoken` 的 BPE，
   与具体网关的计费口径可能有差异；因此报告里同时标注编码器与"实测 / 建模"边界。
4. **画像缓存是进程内的**：多进程部署时每个进程各存一份（不影响正确性，
   只是首次 miss 会各读一次盘）；如需共享可外置缓存。
5. **语义包同义词覆盖缺口**：评测集里的「营收」不在零售包同义词表中
   （仅有「销售额 / 销售金额 / GMV / 营业额 / 金额」），因此这类问法只能走 LLM 解析。
   这是**语义层配置**的改进项，本轮没有改动语义包（避免扰动检索与准入的既有基线）。
6. **未做的优化**：沙箱容器**预热池**（每次 `docker run` 的容器启动开销是真实执行延迟的
   大头）、SSE 分片推送中间态、把 `summarize` 改成流式。它们都需要动执行层，
   超出本轮"不重构、只省不必要的开销"的范围。

---

## 附：怎么复现

```bash
# 成本 / 延迟对照（离线，不需要 Docker / LLM Key）
python -m backend.evaluation --cost-benchmark
python -m backend.evaluation --cost-benchmark --markdown docs/cost-latency-v1-benchmark.md

# 完整评估报告（已包含成本段）
python -m backend.evaluation

# 真实执行指标（需要 Docker + LLM Key；缺失时如实显示"未采集"）
python -m backend.evaluation --with-pipeline

# 前端看成本面板：任意一次分析 → 运行时间线
```
