> 本文件由 `python -m backend.evaluation --benchmark --markdown docs/skill-retrieval-v2-benchmark.md` 生成（2026-09-21），是磁盘上的可复现基准快照；
> 设计与结论见 [`skill-retrieval-v2.md`](skill-retrieval-v2.md)。修改检索/准入逻辑后请重新生成本文件。

# Skill Retrieval / Replay Admission — Baseline vs V2

- 问题集：65 条（零售 + 制造 + 口语化对抗），对抗准入样例：20 条
- 路径库：fine（完整签名）59 条 / coarse（语义包+指标+类型）33 条
- V1 = 冻结的 Baseline 策略（词面 Top1 + 仅结构守卫）；V2 = Candidate Scoring + 分层 Admission
- fine 口径 = 语义包 / 指标 / 维度 / 分析类型 / 排序方向与 TopN / 时间窗

## 1. 检索（coarse 口径，与既有 CI 门禁同定义）

| Metric | Baseline | V2 | Delta |
| --- | ---: | ---: | ---: |
| Top1 | 69.2% | 78.5% | +9.2pp |
| Recall@2 | 81.5% | 89.2% | +7.7pp |
| Recall@5 | 96.9% | 96.9% | +0.0pp |
| Hit Rate | 100.0% | 100.0% | +0.0pp |

## 2. 检索 + 重放（fine 口径：一条可复用分析路径的完整签名）

| Metric | Baseline | V2 | Delta |
| --- | ---: | ---: | ---: |
| Top1 | 95.4% | 100.0% | +4.6pp |
| Recall@2 | 98.5% | 100.0% | +1.5pp |
| Replay Precision | 95.4% | 100.0% | +4.6pp |
| Replay Recall | 95.4% | 96.9% | +1.5pp |
| False Replay Rate | 4.6% | 0.0% | -4.6pp |
| 错误重放条数 | 3 | 0 | -3 |

## 3. 对抗准入（自带候选池的负样本，判定 + 原因全对才算过）

| Metric | Baseline | V2 | Delta |
| --- | ---: | ---: | ---: |
| Admission Accuracy | 50.0% | 100.0% | +50.0pp |
| 判定+原因一致性 | 20.0% | 100.0% | +80.0pp |
| 误放行 False Accept | 10 | 0 | -10 |
| 误拒绝 False Reject | 0 | 0 | +0 |

## 4. 效率（LLM 侧：调用 / token / 延迟 / 成本）

- Agent 画像来源：`measured:D:\HelixBI\data\app.db`（单次 Agent ≈ 3.0 次调用 / 3942 tokens / 7667 ms）
- 延迟口径：LLM 侧延迟（Agent 路径用遥测画像，重放路径为 0；沙箱执行耗时未采集）

| Metric | Baseline | V2 | Delta |
| --- | ---: | ---: | ---: |
| 重放占比 | 100.0% | 96.9% | -3.1pp |
| Agent 兜底率 | 0.0% | 3.1% | +3.1pp |
| LLM 调用 / 查询 | 0.00 | 0.09 | +0.09 |
| └ 计入误重放后 | 0.14 | 0.09 | -0.05 |
| 平均 Token / 查询 | 0 | 121 | +121.31 |
| 平均 LLM 延迟 / 查询（ms） | 0 | 236 | +235.91 |
| 估算成本 / 查询 | 未配置单价 | 未配置单价 | — |
| 重放失败率 | 0.0% | 0.0% | +0.0pp |
| 沙箱执行耗时 | 未采集 | 未采集 | — |

> 「沙箱执行耗时」需要 Docker 实测，缺失时标注未采集而不是填估计值；
> 两条策略的沙箱成本相同，因此上表的 LLM 侧差异就是策略差异。
> Baseline 的 LLM 调用量更低，是因为它**错误重放**后直接返回了错结果——
> 「计入误重放后」一行把这类查询还原成 Agent 成本，才是可比的口径。
