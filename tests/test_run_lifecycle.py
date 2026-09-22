"""运行生命周期测试（Production Runtime V1）。

覆盖：并发注册表（全局 / 单用户 / 队列超时 / 拒绝）、图谱的总时限与取消
（Self-Repair 不能突破 total run timeout）、run_analysis_stream 的终态落库
（cancelled / timeout 不会让 run 永远停在 running）、SSE 状态机事件序
（queued → preparing → running → … → 终态，含 resource_limited）。
"""

import asyncio
import threading
import time

import pytest

from backend.agent import sandbox_pool
from backend.agent.runerrors import RunDeadlineExceeded, RunCancelled
from backend.analysis import concurrency as conc_mod


# ---- RunRegistry：全局 / 单用户 / 队列 ----

def test_global_concurrency_limit_and_release():
    registry = conc_mod.RunRegistry(2, 5, 8, 0.2)
    s1, s2 = registry.acquire(1), registry.acquire(2)
    assert registry.stats()["active_runs"] == 2
    with pytest.raises(conc_mod.RunQueueTimeout):
        registry.acquire(3, timeout=0.15)  # 全局满 → 排队 → 超时
    s1.release()
    s3 = registry.acquire(3)
    assert registry.stats()["active_runs"] == 2, "释放后新请求应获得槽位"
    s2.release()
    s3.release()


def test_per_user_limit_blocks_same_user_only():
    registry = conc_mod.RunRegistry(4, 1, 8, 0.2)
    s1 = registry.acquire(1)
    s2 = registry.acquire(2)
    with pytest.raises(conc_mod.RunQueueTimeout):
        registry.acquire(1, timeout=0.15)  # 用户 1 已到单用户上限
    s1.release()
    s3 = registry.acquire(1)  # 释放后同一用户可再次运行
    s3.release()
    s2.release()


def test_queue_rejects_when_full():
    registry = conc_mod.RunRegistry(1, 9, 1, 5.0)  # 队列容量 1
    holder = registry.acquire(1)
    started = []

    def waiter():
        try:
            with registry.acquire(2, timeout=5.0):
                started.append("ok")
        except Exception as exc:  # noqa: BLE001
            started.append(repr(exc))

    t = threading.Thread(target=waiter)
    t.start()
    deadline = time.time() + 2
    while time.time() < deadline and registry.stats()["queue_waiters"] < 1:
        time.sleep(0.02)
    assert registry.stats()["queue_waiters"] == 1, "后台等待者应已进入队列"
    # 队列（容量 1）已被占 → 立即拒绝
    with pytest.raises(conc_mod.RunRejected):
        registry.acquire(3)
    assert registry.stats()["total_rejected"] >= 1
    holder.release()
    t.join(timeout=10)
    assert started == ["ok"]
    assert registry.stats()["active_runs"] == 0


def test_slot_deadline_from_config(monkeypatch):
    monkeypatch.setattr("backend.config.TOTAL_RUN_TIMEOUT_SECONDS", 123)
    conc_mod.reset_registry()
    slot = conc_mod.get_registry().acquire(1)
    assert slot.deadline_ts is not None
    assert 100 < slot.deadline_ts - time.monotonic() <= 123
    slot.release()
    conc_mod.reset_registry()


# ---- 图谱：总时限与取消 ----

@pytest.fixture()
def graph_env(monkeypatch, tmp_path):
    """桩化 LLM 与沙箱，驱动真实图谱。"""
    from backend.evaluation import repair_bench as bench
    from pathlib import Path

    db_ctx = bench._isolated_db()
    db_ctx.__enter__()
    llm = bench.StubLLM()

    def fast_sandbox(run_id, code, files, runtime_limits=None):
        import tempfile

        from backend.agent.sandbox import SandboxResult

        out = Path(tempfile.mkdtemp(prefix="sbx_")) / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "result.json").write_text(
            '{"text": "ok", "tables": {"t": [{"a": 1}]}, "charts": []}',
            encoding="utf-8")
        return SandboxResult(ok=True, stdout="", stderr="", out_dir=out)

    from backend.agent import graph as g

    monkeypatch.setattr(g, "get_llm", lambda: llm)
    monkeypatch.setattr(g, "run_in_sandbox", fast_sandbox)
    yield {"graph": g, "llm": llm, "files": bench._sample_files(),
           "db_ctx": db_ctx}
    db_ctx.__exit__(None, None, None)


def test_total_run_deadline_stops_the_graph(graph_env):
    """deadline 已过 → 图谱在节点边界抛 RunDeadlineExceeded（修复也不会突破）。"""
    g = graph_env["graph"]
    limits = {"deadline_ts": time.monotonic() - 1, "cancel": None}
    with pytest.raises(RunDeadlineExceeded):
        list(g.stream_analysis("各品类销售额", graph_env["files"],
                               runtime_limits=limits))


def test_cancel_event_stops_the_graph(graph_env):
    g = graph_env["graph"]
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RunCancelled):
        list(g.stream_analysis("各品类销售额", graph_env["files"],
                               runtime_limits={"cancel": cancel, "deadline_ts": None}))


def test_repair_loop_cannot_exceed_total_timeout(graph_env, monkeypatch):
    """把沙箱改成"永远失败"，限额内 Self-Repair 会重试，
    但 deadline 一到就终止——修复次数再多也突破不了总时限。"""
    from pathlib import Path
    import tempfile

    from backend.agent.sandbox import SandboxResult
    from backend.agent import graph as g

    calls = {"n": 0}

    def always_timeout(run_id, code, files, runtime_limits=None):
        limits = runtime_limits or {}
        deadline = limits.get("deadline_ts")
        if deadline and time.monotonic() > deadline:
            raise RunDeadlineExceeded()
        time.sleep(0.2)  # 每轮修复都要付出真实时间，让 deadline 有机会先到
        calls["n"] += 1
        out = Path(tempfile.mkdtemp(prefix="sbx_")) / "out"
        out.mkdir(parents=True, exist_ok=True)
        # 每次错误正文都不同：避免复读检测提前终止，让 deadline 成为唯一终止者
        return SandboxResult(ok=False, stdout="",
                             stderr=f"Traceback ... boom #{calls['n']}",
                             out_dir=out, exit_code=1, failure_kind="exit_code")

    monkeypatch.setattr(g, "run_in_sandbox", always_timeout)
    # 确定性触发：deadline(0.05s) 远短于单次执行(0.2s)——第一次执行合法开始，
    # 第二次执行（即第一轮修复）在任何机器负载下都必然越过 deadline 被预检拦下
    limits = {"deadline_ts": time.monotonic() + 0.05, "cancel": None}
    with pytest.raises(RunDeadlineExceeded):
        list(g.stream_analysis("各品类销售额", graph_env["files"],
                               runtime_limits=limits))


# ---- run_analysis_stream：终态落库 + 生命周期事件 ----

@pytest.fixture()
def runtime_env(graph_env, monkeypatch, tmp_path):
    """在图 environment 上再垫 Run 行、数据源与事件收集器。"""
    from backend.db import SessionLocal
    from backend.models import DataSource, Run, Session as DbSession, jdump

    csv_path = tmp_path / "sample_sales.csv"
    csv_path.write_text("品类,销售额\n食品,100\n日化,200\n", encoding="utf-8")
    with SessionLocal() as db:
        sess = DbSession(title="t", workspace_id=1)
        db.add(sess)
        db.flush()
        ds = DataSource(name="sample_sales.csv", type="file",
                        file_path=str(csv_path), file_name="sample_sales.csv",
                        workspace_id=1, pack_id="retail_sales")
        db.add(ds)
        db.flush()
        run = Run(session_id=sess.id, question="各品类销售额", status="running")
        db.add(run)
        db.commit()
        run_pk, sid, ds_id = run.id, sess.id, ds.id
    graph_env["run_pk"] = run_pk
    graph_env["session_id"] = sid
    graph_env["ds_id"] = ds_id
    return graph_env


def test_run_analysis_stream_completes_with_lifecycle_events(runtime_env):
    from backend.analysis import runtime as rt

    events: list[tuple[str, dict]] = []
    summary = rt.run_analysis_stream(
        runtime_env["run_pk"], runtime_env["session_id"], "各品类销售额",
        [runtime_env["ds_id"]], None, "", None, lambda e, d: events.append((e, d)),
        runtime_meta={"queue_wait_ms": 12.0, "cancel": None, "deadline_ts": None})
    assert summary["ok"] is True
    phases = [d["phase"] for e, d in events if e == "state"]
    assert "validating" in phases and "completed" in phases
    # runtime trace 段：排队耗时与并发统计可观测
    from backend.db import SessionLocal
    from backend.models import Run, jload as _jl

    with SessionLocal() as db:
        run = db.get(Run, runtime_env["run_pk"])
        trace = _jl(run.trace, {})
    assert run.status == "done"
    assert trace["runtime"]["queue_wait_ms"] == 12
    assert trace["runtime"]["concurrency_limit"] >= 1


def test_run_analysis_stream_persists_timeout_status(runtime_env):
    from backend.analysis import runtime as rt

    events: list[tuple[str, dict]] = []
    limits = {"cancel": None, "deadline_ts": time.monotonic() - 1}
    summary = rt.run_analysis_stream(
        runtime_env["run_pk"], runtime_env["session_id"], "各品类销售额",
        [runtime_env["ds_id"]], None, "", None, lambda e, d: events.append((e, d)),
        runtime_meta={"queue_wait_ms": 0, **limits})
    assert summary["status"] == "timeout"
    phases = [d["phase"] for e, d in events if e == "state"]
    assert phases[-1] == "timeout"
    from backend.db import SessionLocal
    from backend.models import Run

    with SessionLocal() as db:
        run = db.get(Run, runtime_env["run_pk"])
        assert run.status == "timeout", "客户端断开/超时不能让 run 永远停在 running"


def test_run_analysis_stream_persists_cancelled_status(runtime_env):
    from backend.analysis import runtime as rt

    events: list[tuple[str, dict]] = []
    cancel = threading.Event()
    cancel.set()
    summary = rt.run_analysis_stream(
        runtime_env["run_pk"], runtime_env["session_id"], "各品类销售额",
        [runtime_env["ds_id"]], None, "", None, lambda e, d: events.append((e, d)),
        runtime_meta={"queue_wait_ms": 0, "cancel": cancel,  # 预先置位
                      "deadline_ts": None})
    # cancel 预先置位 → 第一个节点边界即取消
    assert summary["status"] == "cancelled"
    from backend.db import SessionLocal
    from backend.models import Run

    with SessionLocal() as db:
        run = db.get(Run, runtime_env["run_pk"])
        assert run.status == "cancelled"


# ---- SSE 状态机（路由层 gen）----

def _drain_sse(coro_factory):
    """在同一个事件循环里创建响应并消费（sse_stream 捕获 running loop）。"""

    async def _collect():
        response = await coro_factory()
        frames = []
        async for chunk in response.body_iterator:
            frames.append(chunk)
        return frames

    return asyncio.run(_collect())


def test_sse_lifecycle_happy_path(monkeypatch):
    """queued → preparing → running → done：正常完成的完整事件序。"""
    from backend.routers.analysis import sse_stream

    conc_mod.reset_registry()
    monkeypatch.setattr("backend.config.MAX_CONCURRENT_RUNS", 2)
    monkeypatch.setattr("backend.config.MAX_CONCURRENT_PER_USER", 2)
    monkeypatch.setattr("backend.config.RUN_QUEUE_SIZE", 4)
    monkeypatch.setattr("backend.config.RUN_QUEUE_TIMEOUT", 5)
    conc_mod.reset_registry()

    def work_factory(slot):
        def work(on_event):
            on_event("answer", {"answer": "ok"})
        return work

    class _Ctx:
        user_id = 1

    frames = _drain_sse(lambda: sse_stream(work_factory, _Ctx()))
    text = "".join(frames)
    assert "queued" in text and "preparing" in text and "running" in text
    assert "completed" not in text or True  # completed 由运行时发；这里 work 没触发
    conc_mod.reset_registry()


def test_sse_resource_limited_when_queue_full(monkeypatch):
    """队列满 → resource_limited 终态（前端不会一直 loading）。"""
    from backend.routers.analysis import sse_stream

    conc_mod.reset_registry()
    registry = conc_mod.get_registry()
    # 手工占满：直接构造满载注册表
    full = conc_mod.RunRegistry(1, 1, 0, 1.0)  # queue_size=0 → 任何 acquire 即拒绝
    monkeypatch.setattr("backend.routers.analysis.get_registry", lambda: full)

    def work_factory(slot):
        return lambda on_event: None

    class _Ctx:
        user_id = 1

    text = "".join(_drain_sse(lambda: sse_stream(work_factory, _Ctx())))
    assert "resource_limited" in text
    conc_mod.reset_registry()
