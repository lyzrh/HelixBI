# Production Runtime V1 — Warm Sandbox Pool / 并发控制 / 超时取消

> 状态：已落地并全量回归。本文档如实记录**实际完成与实际测试到的能力**；
> 本机无 Docker 守护进程，所有容器级行为用可注入的 docker 命令桩测试，
> 容器耗时在 Runtime Benchmark 中为**建模值**（`measured=false`）——
> 真实 Docker 实测在守护进程可用前一律标注「未采集」。这不是"生产级高可用"。

## 1. Problem

功能上"单个 Agent 请求可以运行"，但运行时层是单请求假设：

- **冷启动**：每次执行 `docker run --rm`（容器创建 + 解释器启动），同一批
  重复分析反复付出同样的固定开销；Cost & Latency V1 省的是 LLM 侧，沙箱侧没动。
- **并发不受控**：路由层只有一把 `asyncio.Semaphore(2)`——第 3 个请求直接 429，
  没有排队、没有单用户限制、没有统计；一个用户发两个请求就能占满全部容量。
- **没有取消与总时限**：客户端断开后 SSE worker 继续跑完（还 `await task` 阻塞断开）；
  run 可能永远停在 `running`；Self-Repair 理论上可以被"执行 120s × 4 次"拖满 8 分钟。
- **异常状态不可见**：SSE 只有 `step/error/done`，排队中 / 被拒 / 超时 / 取消
  对前端都是"无限 loading 或一个突兀的 429"。
- **清理分散**：容器生命周期靠 `--rm`，没有统一的生命周期管理；异常路径
  （LLM 异常、超时、断连、修复耗尽）各自为政。

## 2. Runtime Design

三层结构，每层独立可关（环境变量），关闭后行为与改造前一致：

```
请求 → [SSE 生命周期] queued → preparing → running → repairing → validating
        │                                                      → completed / failed
        │ 异常终态：cancelled / timeout / resource_limited
        ▼
[RunRegistry 并发控制]  全局 MAX_CONCURRENT_RUNS + 单用户 MAX_CONCURRENT_PER_USER
        │  等待队列 RUN_QUEUE_SIZE（超出 → RunRejected 明确拒绝）
        │  队列等待 > RUN_QUEUE_TIMEOUT → RunQueueTimeout（明确告知）
        ▼
[SandboxPool 沙箱池]    SANDBOX_POOL_SIZE 个预热容器（security flags 与冷启动一致）
           acquire（池满排队，>SANDBOX_ACQUIRE_TIMEOUT → 降级冷启动）
           exec（执行超时 → 容器销毁重建）
           release（清理工作目录 → 回池 / 用满 MAX_CONTAINER_USES 回收重建）
           池不可用 / 排队超时 → 临时冷容器兜底（绝不超开容器）
```

### Warm Sandbox Pool（backend/agent/sandbox_pool.py）

- **预热**：`docker run -d ... sleep infinity`，安全限制与冷启动**完全一致**
  （`--network none` / `--cpus` / `--memory` / `--pids-limit 128`）；启动期后台预热
  （`main.py` lifespan），失败静默降级。
- **契约不变**：dahelper 的 `/data`（只读）与 `/out` 路径用容器内符号链接实现——
  每个容器有**私有挂载根**（data 根以 `:ro` 挂载 → 输入数据内核级只读），
  借出时把 `/data`、`/out` 指到本次执行的子目录。生成代码看到的世界与冷启动相同。
- **工作区隔离**：租约独占容器；一次只有一个执行在容器里跑；每次执行的数据
  只存在于本次子目录，归还时**宿主与容器两侧同时清理**——用户数据不留在
  warm 容器里，也没有跨工作区残留。
- **产物链路不变**：执行后把 `/out` 内容搬回 `runs/<run_id>/out`，
  图表 URL / 鉴权下发 / 导出全部沿用既有链路。
- **回收与补充**：容器用满 `SANDBOX_POOL_MAX_USES` 次强制销毁重建（限制任何
  残留数据寿命）；执行超时 / 容器命令异常 → 立即销毁并后台补充。
- **降级阶梯**：池忙 / 排队超时 / 未就绪 → `docker run --rm` 冷启动
  （改造前行为）；Docker 缺失 → 结构化 `sandbox_unavailable`（Self-Repair 直接兜底）。

### 并发控制（backend/analysis/concurrency.py）

- 全局 + 单用户 + 队列三层上限（见上图），全部服务端判定、基于 user_id；
- 线程实现（分析运行在工作线程里跑），状态不依赖事件循环；
- 统计（active / waiters / 累计排队 / 拒绝 / 超时）暴露在 `/api/health.runtime`。

### 超时 / 取消

| 超时 | 配置 | 生效点 |
| --- | --- | --- |
| 排队 | `RUN_QUEUE_TIMEOUT` | RunRegistry.acquire |
| 容器获取 | `SANDBOX_ACQUIRE_TIMEOUT` | SandboxPool.lease（超时 → 降级冷启动） |
| 单次执行 | `CODE_TIMEOUT_SECONDS` | docker exec / docker run 超时（超时的容器销毁重建） |
| 容器启动 | `SANDBOX_STARTUP_TIMEOUT` | 池预热 |
| 整轮运行 | `TOTAL_RUN_TIMEOUT_SECONDS`（默认 900s） | 节点边界 + 每次 LLM/沙箱调用前预检 |

- 取消（客户端断开）与超时统一为 `runerrors.check_runtime_limits` 预检，
  **取消优先于超时**；异常为 `RunCancelled` / `RunDeadlineExceeded` 控制流，
  **不得被任何兜底分支吞掉**（parse_intent / followups / Skill 重放兜底均已放行）。
- **Self-Repair 不能突破总时限**：每一轮修复都要回到 generate_code → execute，
  两处都有预检——修复次数再多，deadline 一到立即终止（有测试断言）。
- **断连策略**：客户端断开 → 取消事件置位 → 运行在下一个节点边界收尾并落库为
  `cancelled` 终态（释放槽位与容器），后台收尾不阻塞断开；已产生的部分结果
  通过 `/runs/recent` 仍可回访。

### SSE 生命周期（backend/routers/analysis.py）

```
queued → preparing → running → repairing → validating → completed
异常终态：cancelled / timeout / resource_limited / failed
```

- 排队在 SSE 流内完成——客户端实时看到"在排队 + 队列长度"，
  而不是一个突兀的 429；队列满 → `resource_limited`，排队超时 →
  `timeout(timeout_type=queue)`，都有明确终态帧；
- 前端 `chatStore` 处理 `state` 事件：四个异常终态都会终结 loading
  （**不会因为后端异常状态一直转圈**）；
- 前端断连不影响上面已落库的终态；重连 / 历史查看走 `/runs/{rid}` 不回归。

### 资源清理（统一生命周期）

| 资源 | 收口点 | 保证 |
| --- | --- | --- |
| warm 容器 | `_Lease` 上下文管理器 | 成功 / 超时 / 异常统一 release；不健康销毁重建 |
| 执行工作目录 | `_Lease.release()` | 宿主 + 容器两侧清理 |
| 并发槽位 | `RunSlot` 上下文管理器（worker `with slot:`） | 任何异常路径都归还 |
| asyncio task | `_background_tasks` 集合 + 完成回调 | 断连后后台收尾不被 GC |
| run 终态 | `run_analysis_stream` 的 except 链 | cancelled / timeout / failed 必落库 |

## 3. Benchmark

`python -m backend.evaluation --runtime-benchmark`（离线仿真：池与并发调度是
**真实代码在真实线程里运行**；容器冷启动 1500ms / 执行 100ms 为建模常数；
`measured=false`）：

| 并发 | Cold p50 | Warm p50 | Cold p95 | Warm p95 | 吞吐倍数 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1601 ms | **101 ms** | 1601 ms | 101 ms | 15.9x |
| 5 | 1600 ms | **102 ms** | 1602 ms | 152 ms | 7.9x |
| 10 | 1601 ms | **304 ms** | 3216 ms | 560 ms | 7.9x |
| 20 | 4001 ms | **457 ms** | 6414 ms | 936 ms | 7.9x |

- 池大小 2：并发 >2 后 warm p50 开始含排队时间（真实排队行为，不是缺陷）；
- 负载结束后泄漏检查 `idle + leased == size` 通过；复用 61 次、到期回收 1 次；
- 真实 Docker 冷启动 / 执行实测：**未采集**（需守护进程）。

## 4. Tests

- `tests/test_sandbox_pool.py`（10）：安全 flags、复用、目录清理、到期回收、
  超时销毁重建、租约互斥、池满超时、等待者容量拒绝、产物搬运、降级、禁用池；
- `tests/test_run_lifecycle.py`（12）：全局/单用户上限、队列拒绝、deadline 注入、
  图谱超时与取消、**修复循环不能突破总时限**、终态落库（done/timeout/cancelled）、
  生命周期事件、SSE happy path 与 resource_limited；
- `tests/evaluation/test_runtime_benchmark.py`（7）：基准门禁（见 CI）。

## 5. Trade-offs / Remaining Limitations

- warm 容器内 `/data` 只读由只读挂载保证，但容器根文件系统仍可写——
  生成代码理论上可以污染容器自身（pandas 已在镜像里，攻击面有限），
  靠 `MAX_CONTAINER_USES` 回收限制残留寿命；更强方案是 `--read-only` + tmpfs，
  需要随镜像一起验证（未做）。
- 取消 / 超时的粒度是**节点边界**：沙箱内单次执行不可中断，由执行超时兜底。
- 排队/并发状态在**单进程内**：多 worker 部署（uvicorn --workers >1）时
  上限是每进程的；跨进程配额需要 Redis 等外部协调（有意未引入）。
- 容器级耗时是建模值；真实冷启动/执行分布需在 Docker 可用时用
  `--repair-live` 同款思路补采。
- 未做：容器预热池的指标导出（Prometheus 等）、多池按工作区配额。
