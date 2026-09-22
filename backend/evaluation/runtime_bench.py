"""Runtime Benchmark（Production Runtime V1）：Cold Sandbox vs Warm Pool。

**诚实边界**：本模块只仿真「容器级耗时」——冷启动成本（`docker run` 创建 +
解释器启动）与执行耗时用可调常数建模；**池与并发调度的全部真实代码**
（`SandboxPool` 的租约/排队/超时/复用/回收、`RunRegistry` 的并发上限/单用户
限制/队列）在真实线程里原样运行。因此排队行为、复用率、拒绝与超时统计是
**真数据**，容器启动/执行的绝对毫秒数是**建模值**。真实 Docker 实测
（需守护进程）在资源可用前如实标注「未采集」，本报告必须标记
`measured=false`。

场景：1 / 5 / 10 / 20 并发 × cold / warm 两种模式，各跑 N 个运行：
- cold：每个运行付出 COLD_START_MS（建模冷启动）+ EXEC_MS（建模执行），
  无池、无排队（与改造前 `docker run --rm` 等价的资源画像）；
- warm：真实 `SandboxPool`（size=POOL_SIZE）借还容器，只有 EXEC_MS；
  池满时在真实 Condition 上排队，拿不到 → 降级为 cold 画像（真实降级路径）。

并发控制两层都跑真实的 `RunRegistry`（全局上限 + 单用户上限 + 队列），
因此 throughput / queue p50-p95 / timeout / 拒绝率反映的是**调度器**的行为，
而不是睡眠时间。
"""

import time
from concurrent.futures import ThreadPoolExecutor
from statistics import median

from backend.agent import sandbox_pool
from backend.analysis import concurrency as conc_mod

# 建模常数（毫秒）：与"镜像已在本机"的典型画像同数量级。
# 真实值因机器 / 镜像 / 卷驱动而异——这正是只对它们标「建模」的原因。
COLD_START_MS = 1500
EXEC_MS = 100
POOL_SIZE = 2
SCENARIO_CONCURRENCY = (1, 5, 10, 20)
RUNS_PER_SCENARIO = 16


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(q * len(ordered)), len(ordered) - 1)
    return ordered[idx]


def _stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    return {"count": len(values), "p50_ms": round(median(values), 1),
            "p95_ms": round(_quantile(values, 0.95), 1),
            "max_ms": round(max(values), 1)}


class _FakeDocker:
    """把 SandboxPool 的 docker 命令替换成即时成功（容器耗时在 sleep 里建模）。"""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.ids = 0

    def __call__(self, args, timeout):
        self.calls.append(args)
        self.ids += 1
        return 0, f"fake-container-{self.ids}", ""


def _run_scenario(mode: str, concurrency: int, n_runs: int,
                  registry: conc_mod.RunRegistry,
                  pool: sandbox_pool.SandboxPool | None) -> dict:
    """跑一个场景：n_runs 个运行、最多 concurrency 个同时执行。"""
    latencies: list[float] = []
    queue_waits: list[float] = []
    fallbacks = 0
    lock_pool = pool or None

    def one_run(_i: int) -> None:
        nonlocal fallbacks
        t0 = time.perf_counter()
        try:
            with registry.acquire(user_id=None, timeout=30):
                if mode == "warm" and pool is not None:
                    try:
                        with pool.lease(timeout=2.0) as lease:
                            queue_waits.append(lease.acquire_ms)
                            time.sleep(EXEC_MS / 1000)
                    except (sandbox_pool.PoolTimeout, sandbox_pool.PoolUnavailable):
                        fallbacks += 1
                        time.sleep((COLD_START_MS + EXEC_MS) / 1000)
                else:
                    time.sleep((COLD_START_MS + EXEC_MS) / 1000)
        except conc_mod.RunQueueTimeout:
            pass  # 计入超时（真实调度行为）
        latencies.append((time.perf_counter() - t0) * 1000)

    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        list(ex.map(one_run, range(n_runs)))
    wall_s = time.perf_counter() - t_start

    pool_stats = pool.stats() if pool is not None else {}
    reg_stats = registry.stats()
    finished = len(latencies)
    return {
        "mode": mode,
        "concurrency": concurrency,
        "runs": n_runs,
        "wall_s": round(wall_s, 2),
        "throughput_rps": round(finished / wall_s, 2),
        "latency_ms": _stats(latencies),
        "queue_wait_ms": _stats(queue_waits) if queue_waits else None,
        "fallback_to_cold": fallbacks,
        "queue_timeouts": reg_stats.get("total_queue_timeouts", 0),
        "rejected": reg_stats.get("total_rejected", 0),
        "container_reuse_rate": (
            round(pool_stats.get("reuse_count", 0)
                  / max(pool_stats.get("warm_executions", 0)
                        + pool_stats.get("reuse_count", 0), 1), 4)
            if pool_stats else None),
        "warm_executions": pool_stats.get("warm_executions", 0),
        "container_recycles": pool_stats.get("recycles", 0),
        "container_crashes": pool_stats.get("crash_replacements", 0),
        "pool_final_idle": pool_stats.get("idle"),
    }


def evaluate(runs: int = RUNS_PER_SCENARIO,
             concurrency_levels: tuple[int, ...] = SCENARIO_CONCURRENCY,
             pool_size: int = POOL_SIZE) -> dict:
    """跑 Cold vs Warm 对照（真实池与并发代码 + 建模容器耗时）。"""
    fake = _FakeDocker()
    original_run_docker = sandbox_pool._run_docker
    sandbox_pool._run_docker = fake  # type: ignore[assignment]
    original_pool = sandbox_pool._pool
    try:
        pool = sandbox_pool.SandboxPool(size=pool_size, acquire_timeout=2.0,
                                        queue_capacity=8)
        warmup_ok = pool.warmup() > 0
        scenarios = []
        for mode in ("cold", "warm"):
            for c in concurrency_levels:
                conc_mod.reset_registry()
                registry = conc_mod.get_registry()
                scenarios.append(_run_scenario(mode, c, runs, registry,
                                               pool if mode == "warm" else None))
        # 池在负载后应回到"全部空闲"——任何泄漏都会让 idle < size
        final_pool = pool.stats()
    finally:
        sandbox_pool._run_docker = original_run_docker  # type: ignore[assignment]
        sandbox_pool._pool = original_pool

    by_key = {(s["mode"], s["concurrency"]): s for s in scenarios}
    comparisons = []
    for c in concurrency_levels:
        cold, warm = by_key[("cold", c)], by_key[("warm", c)]
        comparisons.append({
            "concurrency": c,
            "cold_p50_ms": cold["latency_ms"]["p50_ms"],
            "warm_p50_ms": warm["latency_ms"]["p50_ms"],
            "cold_p95_ms": cold["latency_ms"]["p95_ms"],
            "warm_p95_ms": warm["latency_ms"]["p95_ms"],
            "p50_improvement": round(
                1 - warm["latency_ms"]["p50_ms"] / max(cold["latency_ms"]["p50_ms"], 0.01), 4),
            "throughput_gain": round(
                warm["throughput_rps"] / max(cold["throughput_rps"], 0.01), 2),
            "fallback_to_cold": warm["fallback_to_cold"],
        })

    return {
        "mode": "offline_simulation",
        "measured": False,
        "docker_warmup_succeeded": warmup_ok,
        "model": {"cold_start_ms": COLD_START_MS, "exec_ms": EXEC_MS,
                  "pool_size": pool_size, "runs_per_scenario": runs,
                  "concurrency_levels": list(concurrency_levels),
                  "what_is_real": ["SandboxPool 租约/排队/超时/复用/回收",
                                   "RunRegistry 并发上限/单用户限制/队列"],
                  "what_is_modeled": ["docker run 冷启动耗时", "代码执行耗时"]},
        "scenarios": scenarios,
        "comparisons": comparisons,
        "pool_final": {k: final_pool[k] for k in
                       ("warm_count", "idle", "leased", "created_total",
                        "destroyed_total", "reuse_count", "recycles",
                        "crash_replacements", "timeouts", "rejected_waiters")},
        "pool_leak_check": final_pool["idle"] + final_pool["leased"] == pool_size,
    }


def render_markdown(result: dict) -> str:
    lines = [
        "# Runtime Benchmark — Cold Sandbox vs Warm Pool",
        "",
        f"> 模式：`{result['mode']}`，`measured={str(result['measured']).lower()}`。"
        "池与并发调度是**真实代码**（租约 / 排队 / 超时 / 复用 / 上限），"
        "容器冷启动与执行耗时为**建模值**；真实 Docker 实测未采集。",
        f"> 建模常数：冷启动 {result['model']['cold_start_ms']} ms、"
        f"执行 {result['model']['exec_ms']} ms、池大小 {result['model']['pool_size']}、"
        f"每场景 {result['model']['runs_per_scenario']} 个运行。",
        "",
        "| 并发 | Cold p50 | Warm p50 | Cold p95 | Warm p95 | p50 提升 | 吞吐倍数 | 降级次数 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for c in result["comparisons"]:
        lines.append(
            f"| {c['concurrency']} | {c['cold_p50_ms']:.0f} ms | {c['warm_p50_ms']:.0f} ms"
            f" | {c['cold_p95_ms']:.0f} ms | {c['warm_p95_ms']:.0f} ms"
            f" | {c['p50_improvement'] * 100:.1f}% | {c['throughput_gain']:.2f}x"
            f" | {c['fallback_to_cold']} |")
    pf = result["pool_final"]
    lines += [
        "",
        "## 池状态（负载结束后）",
        "",
        f"- 容器：warm {pf['warm_count']}（空闲 {pf['idle']} / 借出 {pf['leased']}），"
        f"累计创建 {pf['created_total']}、销毁 {pf['destroyed_total']}"
        f"（到期回收 {pf['recycles']}、异常替换 {pf['crash_replacements']}）",
        f"- 复用执行 {pf['reuse_count']} 次；排队超时（池）{pf['timeouts']}、"
        f"等待者被拒 {pf['rejected_waiters']}",
        f"- 泄漏检查：`idle + leased == pool_size` → "
        f"{'通过' if result['pool_leak_check'] else '失败'}",
        "",
        "## 诚实性声明",
        "",
        f"- **真实代码**：{'；'.join(result['model']['what_is_real'])}",
        f"- **建模值**：{'；'.join(result['model']['what_is_modeled'])}",
        "- **未采集**：真实 Docker 冷启动 / 执行耗时（需 Docker 守护进程）。",
        "",
    ]
    return "\n".join(lines)


__all__ = ["evaluate", "render_markdown"]
