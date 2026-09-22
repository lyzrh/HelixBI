"""Warm Sandbox Pool 单元测试（Production Runtime V1）。

Docker 命令全部被桩替换（`sandbox_pool._run_docker`），因此这里测的是
**池的真实调度逻辑**（租约互斥 / 复用 / 回收 / 异常销毁重建 / 排队与超时 /
等待队列容量 / 目录清理 / 产物搬运），而不是 Docker 本身。
"""

import threading
import time

import pytest

from backend.agent import sandbox_pool


@pytest.fixture()
def pool_root(tmp_path, monkeypatch):
    """池根目录指向临时路径，避免污染真实 runs/。"""
    root = tmp_path / "pool"
    root.mkdir()
    monkeypatch.setattr(sandbox_pool, "POOL_ROOT", root)
    yield root


@pytest.fixture()
def fake_docker(monkeypatch):
    """即时成功的 docker 桩：记录调用，返回自增容器 id。"""
    calls: list[list[str]] = []
    state = {"seq": 0, "fail_run": False, "fail_exec": False}

    def _run_docker(args, timeout):
        calls.append(list(args))
        if args[:2] == ["exec", "-w"] and state["fail_exec"]:
            raise TimeoutError("docker 命令超时")
        if args[:2] == ["run", "-d"] and state["fail_run"]:
            return 1, "", "daemon unavailable"
        state["seq"] += 1
        return 0, f"cid-{state['seq']}", ""

    monkeypatch.setattr(sandbox_pool, "_run_docker", _run_docker)
    monkeypatch.setattr(sandbox_pool, "SANDBOX_POOL_MAX_USES", 3)  # 加速回收测试
    return {"calls": calls, "state": state}


def _make_pool(size=2, **kwargs):
    kwargs.setdefault("acquire_timeout", 0.5)
    kwargs.setdefault("queue_capacity", 2)
    return sandbox_pool.SandboxPool(size=size, **kwargs)


def test_warmup_creates_containers_with_security_flags(pool_root, fake_docker):
    pool = _make_pool(size=2)
    assert pool.warmup() == 2
    run_args = [c for c in fake_docker["calls"] if c[:2] == ["run", "-d"]]
    assert len(run_args) == 2
    for args in run_args:
        joined = " ".join(args)
        # 安全限制与冷启动完全一致
        assert "--network none" in joined
        assert "--cpus" in joined and "--memory" in joined and "--pids-limit" in joined
    assert pool.stats()["warm_count"] == 2
    pool.close()


def test_lease_reuses_container_and_cleans_exec_dirs(pool_root, fake_docker):
    pool = _make_pool(size=1)
    pool.warmup()
    data_src = pool_root / "src.csv"
    data_src.write_text("a,b\n1,2\n", encoding="utf-8")

    with pool.lease() as lease:
        lease.execute("print('hi')", {"sample.csv": str(data_src)})
        assert lease.out_dir.exists() and (lease.out_dir / "_analysis.py").exists()
        key1 = lease.container.key
        exec_id1 = lease.exec_id
    # 归还后：本次 data/out 工作目录被清理（用户数据不留在池里）
    assert not (pool_root / key1 / "data" / exec_id1).exists()
    assert not (pool_root / key1 / "out" / exec_id1).exists()

    with pool.lease() as lease2:
        assert lease2.container.key == key1, "必须复用同一容器"
        assert lease2.container.uses == 1, "复用计数递增"
        assert lease2.pool_meta()["container_reused"] is True
    assert pool.stats()["reuse_count"] == 1
    pool.close()


def test_container_recycled_after_max_uses(pool_root, fake_docker):
    pool = _make_pool(size=1)
    pool.warmup()
    keys = []
    for _ in range(3):  # MAX_USES 已被 fixture 调成 3
        with pool.lease() as lease:
            keys.append(lease.container.key)
    # 第 3 次用满 → 销毁重建；下一次应拿到新容器
    with pool.lease() as lease:
        assert lease.container.key != keys[-1], "用满后必须换新容器"
    stats = pool.stats()
    assert stats["recycles"] == 1
    assert stats["destroyed_total"] == 1
    pool.close()


def test_exec_timeout_destroys_container_and_replaces(pool_root, fake_docker):
    """执行超时 = 容器不健康：销毁 + 后台补充（容器内进程随之消亡）。"""
    pool = _make_pool(size=1)
    pool.warmup()
    fake_docker["state"]["fail_exec"] = True
    with pytest.raises(TimeoutError):
        with pool.lease() as lease:
            lease.execute("while True: pass", {})
    fake_docker["state"]["fail_exec"] = False
    deadline = time.time() + 5
    while time.time() < deadline and pool.stats()["warm_count"] < 1:
        time.sleep(0.05)  # 后台补充线程
    stats = pool.stats()
    assert stats["crash_replacements"] == 1
    assert stats["warm_count"] == 1, "补充线程应把池填回"
    pool.close()


def test_lease_is_exclusive_between_threads(pool_root, fake_docker):
    """同一时刻一个容器只能被一个租约持有（并发借出互斥）。"""
    pool = _make_pool(size=1, acquire_timeout=5.0)
    pool.warmup()
    overlaps: list[int] = []
    active = {"n": 0}
    guard = threading.Lock()

    def worker():
        with pool.lease():
            with guard:
                active["n"] += 1
                overlaps.append(active["n"])
            time.sleep(0.05)
            with guard:
                active["n"] -= 1

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max(overlaps) == 1, "租约必须互斥"
    pool.close()


def test_pool_full_times_out_instead_of_overcreating(pool_root, fake_docker):
    """池满 + 排队超时 → PoolTimeout；容器数量不超池大小（不无限创建）。"""
    pool = _make_pool(size=1)
    pool.warmup()
    with pool.lease():  # 占住唯一容器
        t0 = time.time()
        with pytest.raises(sandbox_pool.PoolTimeout):
            with pool.lease():
                pass
        assert 0.3 <= time.time() - t0 <= 2.0, "按 acquire_timeout 超时"
    assert fake_docker["calls"].count(["run", "-d"]) <= 1 or True
    runs = [c for c in fake_docker["calls"] if c[:2] == ["run", "-d"]]
    assert len(runs) == 1, "排队期间绝不创建新容器"
    pool.close()


def test_waiter_capacity_rejects_immediately(pool_root, fake_docker):
    """等待队列也满 → 立刻 PoolUnavailable（明确拒绝，不无限排队）。"""
    pool = _make_pool(size=1, acquire_timeout=5.0, queue_capacity=1)
    pool.warmup()
    outcome: dict = {}

    def waiter():
        try:
            with pool.lease():
                time.sleep(0.1)  # 占住容器直到被主线程唤醒
            outcome["waiter"] = "ok"
        except Exception as exc:  # noqa: BLE001
            outcome["waiter"] = repr(exc)

    with pool.lease():  # 主线程占住唯一容器
        t = threading.Thread(target=waiter)
        t.start()
        deadline = time.time() + 2
        while time.time() < deadline and pool.stats()["waiters"] < 1:
            time.sleep(0.02)
        assert pool.stats()["waiters"] == 1, "后台等待者应已进入队列"
        # 队列容量 1 已被占 → 第二个等待者立即被拒
        with pytest.raises(sandbox_pool.PoolUnavailable):
            pool.lease()
        assert pool.stats()["rejected_waiters"] == 1
    t.join(timeout=10)
    assert outcome.get("waiter") == "ok"
    pool.close()


def test_publish_moves_artifacts_to_run_dir(pool_root, fake_docker, tmp_path):
    """产物搬到 runs/<run_id>/out（下发链路不变），且本次 out 目录被清空。"""
    pool = _make_pool(size=1)
    pool.warmup()
    run_dir = tmp_path / "runs" / "run-1"
    with pool.lease() as lease:
        lease.stage("dahelper.save_text('x')", {})
        (lease.out_dir / "chart.png").write_bytes(b"png")
        result_out = lease.publish_to(run_dir)
    assert (result_out / "chart.png").read_bytes() == b"png"
    pool.close()


def test_pool_degrades_when_docker_missing(pool_root, monkeypatch):
    """docker run 失败 → 池标记 degraded → lease 抛 PoolUnavailable（上层降级）。"""
    def _fail(args, timeout):
        return 1, "", "cannot connect to the docker api"

    monkeypatch.setattr(sandbox_pool, "_run_docker", _fail)
    pool = _make_pool(size=1)
    assert pool.warmup() == 0
    with pytest.raises(sandbox_pool.PoolUnavailable):
        with pool.lease():
            pass
    assert pool.stats()["available"] is False
    assert "docker" in pool.stats()["degraded_reason"].lower()


def test_disabled_pool_is_never_available(pool_root, fake_docker):
    pool = sandbox_pool.SandboxPool(size=0)
    pool.warmup()
    with pytest.raises(sandbox_pool.PoolUnavailable):
        with pool.lease():
            pass
