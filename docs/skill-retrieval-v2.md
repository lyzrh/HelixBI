# Skill Retrieval V2 / Replay Admission 工程化改造

> 目标不是「提高 Recall」，而是**提高正确的 Replay，同时降低 False Replay、LLM 调用次数、
> 平均延迟与成本**。核心原则：**系统宁愿放弃 Replay，也不能错误 Replay。**

- 范围：`backend/skills/`（检索与准入）、`backend/routers/analysis.py`（路由）、
  `backend/evaluation/`（度量）、`backend/semantic/resolver.py`（时间窗/排序解析补强）。
- 约束：不引入向量数据库 / RAG / Multi-Agent；不新增 LLM 调用；旧 Skill 数据保持兼容；
  RBAC / Workspace / Observability 不被破坏。
- 复现命令：
  ```bash
  python -m backend.evaluation                          # 完整报告
  python -m backend.evaluation --benchmark              # Baseline vs V2 对照表
  python -m backend.evaluation --tune-weights --sensitivity   # 权重标定与敏感性
  pytest -q
  ```

---

## 1. 改造前的架构与三个真实缺口

改造前（V1）：

```
POST /sessions/{id}/analyze
  → match_skills()  词面 2-gram 命中数 + 同包 +3 + 分析类型一致 +2，取 Top-2
  → render_skill_prompt()  作为 few-shot 注入 generate_code
  → 完整 Agent（永远重新生成代码）
```

也就是说：**V1 的 Skill 在分析链路里只是 few-shot 素材**，真正的「重放」只存在于
`POST /skills/{id}/run`（用户显式点击）。三个缺口：

| 缺口 | 具体表现 | 后果 |
| --- | --- | --- |
| 判别信号太少 | 只有词面 + 包 + 分析类型 | 「同指标不同维度」「同维度不同指标」在词面层面几乎不可分，Top1 会错 |
| 重放守卫太弱 | `run_skill()` 只查「列是子集 + pandas 读取函数与扩展名兼容」 | 指标/维度/排序方向/时间窗完全不同也会重放，返回**看起来对、口径错**的结果 |
| 缺少准入概念 | 没有「分数够不够、能不能复用」的判定，也没有可解释的拒绝原因 | 要么全靠 Top1（危险），要么全走 Agent（贵）；排障时说不出「为什么没重放」 |

Baseline（改造前，65 条问题集，词面口径）：

| 指标 | 值 |
| --- | ---: |
| Skill 匹配 Top1 准确率 | 72.3%（47/65） |
| Skill 匹配 Recall@2 | 86.2% |
| Skill 命中率 | 100% |
| 重放准入判定正确率（结构守卫） | 100%（只测列/读取器，非业务口径） |
| LLM 调用 / Token / 平均延迟 / 自修复 | **未采集**（Docker 沙箱与 LLM 不可用，不编造数字） |

---

## 2. Retrieval V2 架构

```
User Query
  │
  ├─① Semantic Resolution      backend.semantic.resolve()（零 token、确定性）
  │      指标 / 维度 / 分析类型 / 排序方向与 TopN / 时间窗（区间 + 粒度）
  │
  ├─② Candidate Retrieval      skills/retrieval.py::retrieve()
  │      作用域过滤（global / workspace / user）+ 全量打分（**不做相关性裁剪，召回优先**）
  │
  ├─③ Candidate Scoring        8 路可解释信号线性加权 → final_score
  │      semantic · metric · dimension · type · ranking · time · datasource · history
  │
  ├─④ Admission Decision       skills/retrieval.py::admit()
  │      blocker 硬约束 → 分层（High / Medium / Low）→ 候选歧义检测
  │
  └─⑤ Replay OR Full Agent
         replay：**0 次 LLM 生成**，直接跑已验证代码（仍然过沙箱、仍然记 trace）
         agent ：few-shot 注入 + 完整 Agent（Code Generation → Sandbox → Self-Repair）
                 重放失败时也会安全回到这条路径
```

两条设计约束贯穿始终：

- **打分只决定排序，安全来自 blocker**。任何一个硬约束不满足，无论分数多高都不重放。
  这也是敏感性分析的结论（见第 6 节）：权重怎么调，安全指标都不动。
- **不新增 LLM 调用**。8 路信号全部来自语义解析、Skill metadata 与历史成功率。

### 为什么是 8 路而不是 6 路

评测数据逼出来两路（不是「看起来更全」）：

- 「销售额最高的门店」vs「销售额最低的门店」——词面、指标、维度、分析类型**全一样**，
  但代码里 `sort_values(ascending=False)` 与 `True` 相反 → 必须比 `ranking.order`；
- 「近 30 天趋势」vs「近 90 天趋势」，「日趋势」vs「周趋势」——同样全一样，
  但代码里窗口与重采样粒度是写死的 → 必须比 `time`（区间 + 粒度）。

`top_n` 也要比：`head(10)` 的代码回答不了「Top5」。

---

## 3. Admission Policy

```
                    ┌─ 有 blocker ？ ──是──→ agent（reason_code = blocked:<blocker>）
                    │
candidates ─────────┤  取「无 blocker」的最佳候选（Top1 不安全不代表放弃：Top2 干净就用 Top2）
                    │
                    ├─ score ≥ 0.72 且 领先第二名 ≥ 0.03 ──→ **replay**（high_confidence）
                    │
                    ├─ 0.72 > score ≥ 0.50，或分差过小 ──→ 严格校验
                    │        （metric/dimension/type/ranking/time/datasource 必须全部满分，
                    │          仅「双方都没这个信息」的信号可跳过）
                    │        通过 → **replay**（verify_passed）；否则 → agent（verify_failed:<signal>）
                    │
                    └─ score < 0.50 ────────────────────→ agent（low_confidence）
```

Hard blockers（任一命中即拒绝重放）：

| blocker | 触发条件 | 例子 |
| --- | --- | --- |
| `metric_mismatch` / `metric_partial` | 指标集合完全不相交 / 部分重叠 | 「各品类的订单量」对「各品类的销售额」；多要一个指标 |
| `dimension_mismatch` / `dimension_partial` | 维度集合不相交 / 部分重叠 | 「各区域销售额」对「各品类销售额」；用户另加了地区筛选 |
| `analysis_type_mismatch` | 分析类型不同（拆解 / 趋势 / 排名 / 同环比） | 「各品类销售额」对「上月各品类销售额环比」 |
| `ranking_direction_mismatch` | 排序方向相反 | 「最高」对「最低」 |
| `ranking_limit_mismatch` | TopN 不同 | Top5 对 Top10 |
| `time_window_mismatch` | 时间区间或粒度不同 | 近 30 天对近 90 天；日趋势对周趋势 |
| `pack_mismatch` | 语义包不同 | 零售 Skill 用在制造语义包 |
| `column_missing` | Skill 依赖的列在当前数据里不存在 | 需要「折扣」列 |
| `reader_mismatch` | pandas 读取函数与文件类型不兼容 | `read_excel` 代码对 csv 数据 |
| `datasource_changed` | 数据源指纹（语义包::文件类型）变化 | Skill 沉淀自 xlsx，当前是 csv |
| `skill_incomplete` | Skill 没有代码 | 只填了元数据的空壳 |

**兼容性**：`datasource_key` 是本次新增列，历史 Skill 为 `NULL` → 跳过指纹校验，
回退到「列结构 + 读取函数」判断（`test_legacy_skill_without_fingerprint_still_replayable`）。

---

## 4. Baseline vs V2（同一批用例、同一候选池）

V1 = 冻结的 Baseline 策略（词面 Top1 + 仅结构守卫），代码保留在 `retrieval.legacy_*`；
V2 = 上述打分 + 准入。两套口径：

- **coarse**：`(语义包, 指标集合, 分析类型)` —— 与既有 CI 门禁同定义，用于衔接历史数字；
- **fine**：再加 `(维度集合, 排序方向/TopN, 时间窗)` —— **一条可复用分析路径的完整签名**，
  重放正确性必须按这个口径度量。

### 检索（coarse）

| Metric | Baseline | V2 | Delta |
| --- | ---: | ---: | ---: |
| Top1 | 69.2% | 78.5% | +9.2pp |
| Recall@2 | 81.5% | 89.2% | +7.7pp |
| Recall@5 | 96.9% | 96.9% | +0.0pp |
| Hit Rate | 100.0% | 100.0% | +0.0pp |

### 检索 + 重放（fine）

| Metric | Baseline | V2 | Delta |
| --- | ---: | ---: | ---: |
| Top1 | 95.4% | 100.0% | +4.6pp |
| Recall@2 | 98.5% | 100.0% | +1.5pp |
| Replay Precision | 95.4% | 100.0% | +4.6pp |
| Replay Recall | 95.4% | 96.9% | +1.5pp |
| **False Replay Rate** | 4.6%（3 条） | **0.0%（0 条）** | **−4.6pp** |

### 对抗准入（20 条自带候选池的负样本）

| Metric | Baseline | V2 | Delta |
| --- | ---: | ---: | ---: |
| Admission Accuracy | 50.0% | 100.0% | +50.0pp |
| 判定+原因一致性 | 20.0% | 100.0% | +80.0pp |
| 误放行 False Accept | 10 | 0 | −10 |
| 误拒绝 False Reject | 0 | 0 | 0 |

### 效率（LLM 侧）

- Agent 画像来源：`data/app.db` 真实遥测（3 次运行：≈3.0 次调用 / 3942 tokens / 7667 ms）。
- 延迟口径：LLM 侧；Agent 路径用遥测画像，重放路径为 0。
  **沙箱执行耗时未采集**（需 Docker，不编造）。

| Metric | Baseline | V2 | Delta |
| --- | ---: | ---: | ---: |
| 重放占比 | 100.0% | 96.9% | −3.1pp |
| Agent 兜底率 | 0.0% | 3.1% | +3.1pp |
| LLM 调用 / 查询 | 0.00 | 0.09 | +0.09 |
| └ **计入误重放后** | **0.14** | **0.09** | **−0.05** |
| 平均 Token / 查询 | 0 | 121 | +121 |
| 平均 LLM 延迟 / 查询 | 0 ms | 236 ms | +236 ms |
| 估算成本 / 查询 | 未配置单价 | 未配置单价 | — |
| 沙箱执行耗时 | 未采集 | 未采集 | — |

**怎么读这张表**：Baseline 的「0 次 LLM 调用」不是省钱，而是**错误重放**后直接返回了错结果。
把这类查询还原成 Agent 成本（「计入误重放后」一行）才是可比口径：V2 用 0.09 次调用换来
**0 条错误重放**（Baseline 是 3 条 / 65 条查询）。成本单价未配置时只报 token，
不用编造的美元数字凑一张好看的表。

### 保守拒绝（可接受代价）

V2 有 2 条本可重放的样例选择走 Agent（`hard_005 近半年的销售走势`、`mfg_004`）：
确定性解析器在这两句口语化表达上没解析出指标集合（`hard_cases` 语义准确率 60% 是已知缺口），
落在 Medium 档后严格校验不通过。按「宁可放弃重放」的原则这是正确行为，
代价是 0.09 次 LLM 调用/查询。

---

## 5. 权重是怎么来的（不是拍脑袋）

- 权重集中配置在 `backend/config.py::SKILL_RETRIEVAL_WEIGHTS`，支持环境变量覆盖；
- 标定脚本：`python -m backend.evaluation --tune-weights`（坐标下降，字典序目标：
  先**消错误放行**，再**提正确重放**，最后看 Top1——避免用高召回掩盖错重放）；
- 单信号敏感性：`--sensitivity`，把每路权重分别推到 0.01 / 0.06 / 0.17 / 0.29。

```
信号      取值    准入正确率  误放行  Top1    重放P   重放R
(current)    -     1.00       0   1.000   1.000   0.969
type        0.01   1.00       0   0.969   1.000   0.969   ← 唯一明显受权重影响的信号
type        0.06   1.00       0   0.985   1.000   0.969
type        0.17   1.00       0   1.000   1.000   0.985
semantic    0.29   1.00       0   1.000   1.000   0.969
（其余信号在 0.01–0.29 区间内准入正确率与 False Replay 均不变）
```

结论（也是这次改造最重要的设计判断）：

1. **安全性来自 blocker，不来自权重**——8 路信号里 metric/dimension/type/ranking/time/
   datasource 都既是打分项又是硬约束，所以权重怎么调都拦得住错误重放；
2. 权重只影响**排序**（Top1 / 重放 Recall），其中 `type` 最敏感：降到 0.01 时 Top1 掉到 96.9%；
3. 因此配置取「语义/指标/维度占主导（合计 0.69）、`type` 0.13、其余各自小而明确」，
   而不去精调到小数点后两位——评测集只有 65 + 20 条，过度精调就是过拟合。

---

## 6. 可观测性

每次分析（含重放）都往 `Run.trace.skill` 写一份检索档案：

```json
{
  "mode": "replay",
  "retrieval": {
    "policy": "v2",
    "decision": "replay",
    "confidence": "high",
    "reason_code": "high_confidence",
    "reason": "final_score=0.932 ≥ 0.72，领先第二名 0.211",
    "selected_skill_id": 12,
    "final_score": 0.932, "margin": 0.211,
    "candidate_ids": [12, 9, 4],
    "candidates": [
      {"skill_id": 12, "scores": {"semantic": 0.94, "metric": 1.0, "dimension": 1.0,
        "type": 1.0, "ranking": 1.0, "time": 1.0, "datasource": 1.0, "history": 0.86},
       "final_score": 0.932, "blockers": [], "absent_signals": [], "notes": ["metric:equal", ...]},
      {"skill_id": 9, "blockers": ["dimension_mismatch"], "final_score": 0.71, ...}
    ],
    "query_intent": {"metrics": ["销售额"], "dimensions": ["品类"], "analysis_type": "breakdown",
                     "ranking": {"order": null, "top_n": null},
                     "time": {"kind": null, "value": null, "unit": null, "grain": null}},
    "data_context": {"pack_ids": ["retail_sales"], "signature": "retail_sales::csv", ...}
  }
}
```

于是可以回答三个问题：

- **为什么这个 Query 没有 Replay？** → `reason_code` + `candidates[].blockers`
- **为什么这个 Skill 被选中？** → 8 路分数 + `margin`
- **为什么最终 fallback 到 Agent？** → `fallback_reason` ∈
  `replay_failed` / `admission_declined:<code>` / `admission_selected_other_skill` /
  `column_mismatch` / `reader_mismatch` / `datasource_changed:<from>-><to>`

重放轮的关键证据不变：`llm.calls == 0`（由 `TokenUsage` 汇总，是数据不是文案）。

---

## 7. 测试与 CI 门禁

新增 **54 条**测试（125 → 179 passed，4 skipped 仍为需 Docker/LLM 的端到端用例）：

| 文件 | 数量 | 覆盖 |
| --- | ---: | --- |
| `tests/test_skill_retrieval.py` | 27 | 正常命中、同义词放行、Top1 错 Top2 对、无候选、歧义候选、6 类 blocker、旧 Skill 兼容、作用域隔离（跨工作区/他人 user Skill/停用）、**并发线程安全**、缓存上限、JSON 可序列化 |
| `tests/test_skill_replay_safety.py` | 8 | 重放 0 LLM 调用、**重放失败自动 fallback**、数据源变化不重放、缺列不重放、准入拒绝走 few-shot、跨工作区 Skill 不可运行、权限判定与准入解耦 |
| `tests/test_skill_replay_routing_api.py` | 5 | 真实 HTTP 栈：高置信度 → 重放（Agent 链路零调用）、口径不一致 → Agent、Viewer 403、跨工作区运行 403、运行详情暴露检索依据 |
| `tests/evaluation/test_retrieval_gate.py` | 14 | CI 门禁：Replay Precision 不低于 baseline、False Replay 不恶化且为 0、对抗集零误放行、效率折算后不更差、评估可复现、对抗集场景完整性、配置门 |

CI（`.github/workflows/ci.yml`）在原 `pytest -q` 之后新增三步：

1. `python -m backend.evaluation --json /tmp/evaluation.json`（报告进 CI 日志）
2. `python -m backend.evaluation --benchmark --json /tmp/benchmark.json`（Baseline vs V2 对照）
3. **Replay admission regression gate**：脚本断言
   `Replay Precision ≥ baseline`、`False Replay ≤ baseline`、`False Replay == 0`、
   `准入正确率 ≥ 90%`、`对抗集误放行 == 0`，任一不满足即 CI 失败。

阈值只收紧不放宽；评估**数据没有为了过门禁被修改**——对抗样例放在独立目录
`backend/evaluation/datasets/admission/`，不参与语义解析准确率统计，
既有 `retail_questions` / `manufacturing_questions` / `hard_cases` 三个数据集一字未改。

---

## 8. 遗留问题（诚实清单）

1. **重放的实际执行成功率未实测**：本机与 CI 无 Docker，`沙箱耗时`、`Replay Failure Rate`
   的线上值都标为「未采集」。`replay_failed → Agent fallback` 的路径有单测覆盖，
   但真实沙箱下的失败率需要一次 `--with-pipeline` 评测才有数字。
2. **成本只有 token 没有金额**：`LLM_PRICE_*` 未配置，报告显示「未配置单价」。
   不假设单价是有意为之。
3. **Agent 效率画像是小样本**：来自 3 次真实运行的遥测（≈3 次调用 / 3942 tokens）。
   样本变大后 `avg_latency_ms` 等会随遥测自动更新（`load_agent_profile()` 优先读库）。
4. **2 条保守拒绝**：口语化表达上确定性解析器解析不出指标（`hard_cases` 60%），
   导致本可重放的样例走了 Agent。要提升只能补语义包词表或提升解析器覆盖面。
5. **过滤条件仍是启发式**：`华东地区各品类销售额` 这类「维度当筛选用」的情况，
   靠的是维度集合部分重叠（`dimension_partial`）而不是真正的 filter 抽取——
   能拦住错误重放，但也会把「只加了筛选条件」的合法重放一起拒掉（偏保守，方向安全）。
6. **coarse 口径下的重放指标没有意义**：coarse 库按「包+指标+类型」聚合，
   与「一条可复用路径」的粒度不一致，因此重放指标只在 fine 口径下解读。
7. **V1 的 user 作用域泄漏已修**：旧 `match_skills` 的过滤条件让同工作区的他人
   user Skill 可被检索到（按 workspace_id 命中）；V2 收紧为「user 作用域只看本人」，
   兼容历史 `workspace_id NULL` 数据。

---

## 9. 下一步建议

1. **接一次真实沙箱评测**：`python -m backend.evaluation --with-pipeline` 跑通端到端后，
   把 `Replay Failure Rate` 与沙箱耗时补成实测值，并据此决定是否需要「重放结果语义校验」。
2. **重放结果的事后校验**：目前信任「同一条路径签名 ⇒ 可复用」，可以再加一道
   结果级校验（列名 / 行数 / 与 Skill 历史的量级一致性），把 `replay_failed` 之外
   「跑通了但结果可疑」这一类也兜住。
3. **把 filter 真正抽出来**：给语义包加维度值域（区域枚举等），让「筛选条件变化」
   变成确定性信号而不是靠维度重叠猜测，从而把第 5 条遗留问题变成精确判定。
4. **准入阈值按业务调**：`SKILL_ADMISSION_HIGH/LOW` 已配置化，可以在真实流量上统计
   `reason_code` 分布后再定档（当前值由离线数据标定）。
5. **Skill 库的健康度巡检**：`use_count` / `success_count` 已经在算 `history_score`，
   下一步可以据此定期下线长期失败或从未命中的 Skill，避免库越大越乱。
