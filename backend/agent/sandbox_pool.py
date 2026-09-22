"""Warm Sandbox Pool（Production Runtime V1）。

改造前每次执行都是 `docker run --rm` 冷启动（镜像已在本机时也要付出
容器创建 + Python 解释器启动的固定开销，且并发数不受控）。本模块把沙箱
升级为**可复用的预热容器池**：

- 预热：`docker run -d ... sleep infinity` 创建固定数量的常驻容器，
  安全限制与冷启动**完全一致**（network none / CPU / 内存 / pids-limit）；
- 执行：`docker exec` 跑 `/out/_analysis.py`，dahelper 的 `/data`（只读）与
  `/out`（写结果）契约路径**不变**——用容器内符号链接把本次执行的两个目录
  指到该容器**私有挂载根**下的本次子目录；
- 归还：清理本次工作目录（容器内符号链接 + 宿主两侧），容器回池复用；
  用满 `MAX_CONTAINER_USES` 次强制销毁重建，限制任何残留数据的寿命；
- 异常：执行超时 / 容器命令失败 → 立即销毁该容器并后台补充新实例；
- 池满：调用方排队（等待者上限 `SANDBOX_POOL_QUEUE`），超过
  `SANDBOX_ACQUIRE_TIMEOUT` 秒拿不到 → `PoolTimeout`，由 `run_in_sandbox`
  **降级为临时冷容器**（不拒绝请求、不超开容器）；
- 工作区隔离：每个容器有**独立**的宿主挂载根，容器 A 永远看不到容器 B
  的任何执行目录；执行产物在执行后搬回 `runs/<run_id>/out`（产物下发
  链路不变），本次 data / out 随即删除——用户数据不留在 warm 容器里。

诚实边界：本机 CI / 开发环境可能没有 Docker 守护进程。池的创建失败会被
**静默降级**（标记 degraded，`run_in_sandbox` 走原冷启动路径），绝不阻塞
主流程；所有池行为测试用可注入的 docker 命令桩完成（见 tests/test_sandbox_pool.py）。
"""

import shutil
import threading
import time
import uuid
from pathlib import Path

from backend.config import (
    CODE_TIMEOUT_SECONDS,
    RUNS_DIR,
    SANDBOX_ACQUIRE_TIMEOUT,
    SANDBOX_CPUS,
    SANDBOX_IMAGE,
    SANDBOX_MEMORY,
    SANDBOX_POOL_MAX_USES,
    SANDBOX_POOL_QUEUE,
    SANDBOX_POOL_SIZE,
    SANDBOX_STARTUP_TIMEOUT,
)

POOL_ROOT = RUNS_DIR / "_pool"


class PoolUnavailable(Exception):
    """池不可用（Docker 缺失 / 已降级 / 等待队列满）。"""


class PoolTimeout(Exception):
    """在 SANDBOX_ACQUIRE_TIMEOUT 秒内没拿到空闲容器。"""


def _docker_exe() -> str | None:
    from backend.agent.sandbox import resolve_docker

    return resolve_docker()


def _run_docker(args: list[str], timeout: float) -> tuple[int, str, str]:
    """执行一条 docker 命令（测试桩的注入点）。

    `subprocess.TimeoutExpired` 不是 `TimeoutError` 的子类，这里统一转换，
    让上层只面对一种超时异常。
    """
    import subprocess

    exe = _docker_exe()
    if not exe:
        return 1, "", "未找到 docker 可执行文件"
    try:
        proc = subprocess.run([exe, *args], capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"docker 命令超时（>{timeout}s）")
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


class _WarmContainer:
    """一个常驻容器 + 它的私有挂载根。

    挂载根（`POOL_ROOT/<key>/`）：
    - `data/`：以只读方式挂到容器 `/workdata`（每次执行的输入数据放在
      `data/<exec>/`，符号链接 `/data` 指进去 → **内核级只读**，与冷启动的
      `-v data:/data:ro` 等价）；
    - `out/`：以读写方式挂到容器 `/workout`（`out/<exec>/` 经 `/out` 符号链接
      供代码写结果）。

    key 是池内自增序号而非 docker container id：目录必须先于 `docker run` 存在。
    """

    def __init__(self, key: str, container_id: str):
        self.key = key
        self.container_id = container_id
        self.created_at = time.time()
        self.uses = 0
        self.exec_seq = 0

    @property
    def data_root(self) -> Path:
        return POOL_ROOT / self.key / "data"

    @property
    def out_root(self) -> Path:
        return POOL_ROOT / self.key / "out"


class _Lease:
    """一次沙箱执行的租约（独占一个 warm 容器）。

    `with pool.lease(...) as lease:` 语义保证：无论执行成功、超时还是抛异常，
    `release()` 都会被调用——工作目录被清理、健康容器回池、异常容器销毁重建。
    这是"任何路径都不能泄漏 container / temp directory"的单一收口点。
    """

    def __init__(self, container: _WarmContainer, pool: "SandboxPool",
                 acquire_ms: float, waiters: int):
        self.container = container
        self._pool = pool
        self.acquire_ms = acquire_ms
        self.waiters = waiters
        self.exec_id = f"e{container.exec_seq + 1}-{uuid.uuid4().hex[:8]}"
        self.data_dir = container.data_root / self.exec_id
        self.out_dir = container.out_root / self.exec_id / "out"
        self._released = False
        self._healthy = True

    # ---- 准备 ----

    def stage(self, code: str, files: dict[str, str]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for name, src in files.items():
            dest = self.data_dir / name
            if not dest.exists():
                shutil.copyfile(src, dest)
        (self.out_dir / "_analysis.py").write_text(code, encoding="utf-8")

    def _link(self) -> tuple[int, str]:
        """把容器内的 /data、/out 指到本次执行的目录（契约路径不变）。"""
        return _run_docker([
            "exec", "-w", "/", self.container.container_id, "sh", "-c",
            f"rm -rf /data /out; "
            f"ln -s /workdata/{self.exec_id} /data; "
            f"ln -s /workout/{self.exec_id}/out /out",
        ], timeout=15)

    # ---- 执行 ----

    def execute(self, code: str, files: dict[str, str],
                cancel_event=None) -> "SandboxResult":
        from backend.agent.runerrors import RunCancelled
        from backend.agent.sandbox import SandboxResult, is_infra_failure

        if cancel_event is not None and cancel_event.is_set():
            raise RunCancelled()

        self.stage(code, files)
        rc, _, err = self._link()
        if rc != 0:
            self._healthy = False
            return SandboxResult(
                ok=False, stdout="", stderr=err[-2000:] or "warm 容器准备失败",
                out_dir=self.out_dir, exit_code=rc, failure_kind="sandbox_unavailable")

        try:
            rc, stdout, stderr = _run_docker([
                "exec", "-w", "/", self.container.container_id,
                "python", "/out/_analysis.py",
            ], timeout=CODE_TIMEOUT_SECONDS)
            ok = rc == 0
            timed_out, failure_kind = False, ""
            if not ok:
                if is_infra_failure(stderr):
                    failure_kind = "sandbox_unavailable"
                elif rc == 137:
                    failure_kind = "resource_limit"
                else:
                    failure_kind = "exit_code"
        except TimeoutError:
            ok, rc, stdout = False, None, ""
            timed_out, failure_kind = True, "timeout"
            stderr = f"执行超时（>{CODE_TIMEOUT_SECONDS}s），请优化代码性能或减少数据量。"

        return SandboxResult(
            ok=ok, stdout=stdout[-4000:], stderr=stderr[-4000:],
            out_dir=self.out_dir, exit_code=rc, timed_out=timed_out,
            failure_kind=failure_kind,
            pool_meta=self.pool_meta())

    def pool_meta(self) -> dict:
        return {
            "mode": "warm",
            "container_key": self.container.key,
            "container_reused": self.container.uses > 0,
            "container_created": self.container.uses == 0,
            "container_uses": self.container.uses,
            "acquire_ms": int(self.acquire_ms),
            "queue_length": self.waiters,
        }

    def __enter__(self) -> "_Lease":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # 异常路径（含执行超时、取消）同样收口：不健康 → 销毁重建
        if exc_type is not None:
            self._healthy = False
        self.release()

    # ---- 归还 ----

    def publish_to(self, run_dir: Path) -> Path:
        """把本次产物搬回正式产物目录 `runs/<run_id>/out`（下发链路不变）。"""
        target = run_dir / "out"
        target.mkdir(parents=True, exist_ok=True)
        for item in self.out_dir.iterdir():
            dest = target / item.name
            if dest.exists():
                continue
            shutil.move(str(item), str(dest))
        return target

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            # 容器内符号链接与残留（幂等；容器已死时忽略失败）
            _run_docker(["exec", "-w", "/", self.container.container_id,
                         "sh", "-c", "rm -rf /data /out"], timeout=10)
        except Exception:
            pass
        shutil.rmtree(self.data_dir, ignore_errors=True)
        shutil.rmtree(self.container.out_root / self.exec_id, ignore_errors=True)
        self._pool._release(self.container, healthy=self._healthy)


class SandboxPool:
    """线程安全的预热容器池。

    固定容量（`SANDBOX_POOL_SIZE`）：不随负载扩容，池满即排队——
    「不允许无限创建 container」是硬约束。
    """

    def __init__(self, size: int = SANDBOX_POOL_SIZE,
                 acquire_timeout: float = SANDBOX_ACQUIRE_TIMEOUT,
                 queue_capacity: int = SANDBOX_POOL_QUEUE):
        self.size = max(0, int(size))
        self.acquire_timeout = float(acquire_timeout)
        self.queue_capacity = max(0, int(queue_capacity))
        self._cond = threading.Condition()
        self._idle: list[_WarmContainer] = []
        self._all: dict[str, _WarmContainer] = {}
        self._seq = 0
        self._available = False       # docker 可用且至少完成过一次预热
        self._degraded_reason = ""
        self._waiters = 0
        self._closed = False
        self.stats_data = {
            "created_total": 0, "destroyed_total": 0, "reuse_count": 0,
            "recycles": 0, "crash_replacements": 0, "rejected_waiters": 0,
            "timeouts": 0, "warm_executions": 0,
        }

    # ---- 预热 ----

    def warmup(self, target: int | None = None) -> int:
        """把池补到 target（默认 size）个容器；失败静默降级。返回成功数。"""
        target = self.size if target is None else min(target, self.size)
        if target <= 0:
            return 0
        created = 0
        for _ in range(target):
            with self._cond:
                need = target - len(self._all)
            if need <= 0:
                break
            if self._spawn():
                created += 1
            else:
                break
        with self._cond:
            if self._all:
                self._available = True
                self._degraded_reason = ""
            self._cond.notify_all()
        return created

    def _spawn(self) -> bool:
        """创建一个 warm 容器（含私有挂载根）。失败返回 False。"""
        with self._cond:
            self._seq += 1
            key = f"w{self._seq}"
        data_root = POOL_ROOT / key / "data"
        out_root = POOL_ROOT / key / "out"
        data_root.mkdir(parents=True, exist_ok=True)
        out_root.mkdir(parents=True, exist_ok=True)
        try:
            rc, out, err = _run_docker([
                "run", "-d", "--rm",
                "--network", "none",
                "--cpus", str(SANDBOX_CPUS),
                "--memory", SANDBOX_MEMORY,
                "--pids-limit", "128",
                "-v", f"{data_root}:/workdata:ro",
                "-v", f"{out_root}:/workout",
                SANDBOX_IMAGE, "sleep", "infinity",
            ], timeout=SANDBOX_STARTUP_TIMEOUT)
            if rc != 0 or not out:
                self._mark_degraded(err or "docker run -d 失败")
                return False
            container = _WarmContainer(key, out.splitlines()[-1])
            with self._cond:
                self._all[key] = container
                self._idle.append(container)
                self.stats_data["created_total"] += 1
                self._cond.notify_all()
            return True
        except Exception as exc:  # noqa: BLE001 — 任何创建失败都降级，不阻塞主流程
            self._mark_degraded(str(exc)[:200])
            return False

    def _mark_degraded(self, reason: str) -> None:
        with self._cond:
            if not self._all:
                self._available = False
                self._degraded_reason = reason[:200]

    # ---- 借出 / 归还 ----

    def lease(self, timeout: float | None = None):
        """借出一个空闲容器（上下文管理器）。

        - 池不可用 → PoolUnavailable（调用方降级为冷启动）；
        - 等待者已满 → PoolUnavailable（立刻失败，不无限排队）；
        - 超时 → PoolTimeout（同样降级）。
        """
        timeout = self.acquire_timeout if timeout is None else timeout
        with self._cond:
            if self._closed:
                raise PoolUnavailable("沙箱池已关闭")
            if not self._available:
                raise PoolUnavailable(self._degraded_reason or "沙箱池未就绪")
            if self._waiters >= self.queue_capacity:
                self.stats_data["rejected_waiters"] += 1
                raise PoolUnavailable("沙箱池等待队列已满")

            deadline = time.monotonic() + timeout
            self._waiters += 1
            waiters_at_enter = self._waiters
            try:
                while not self._idle:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.stats_data["timeouts"] += 1
                        raise PoolTimeout(
                            f"等待空闲容器超时（>{timeout}s）")
                    if not self._cond.wait(remaining):
                        continue
                container = self._idle.pop()
            finally:
                self._waiters -= 1

        acquire_ms = (time.monotonic() - (deadline - timeout)) * 1000
        container.exec_seq += 1
        if container.uses > 0:
            self.stats_data["reuse_count"] += 1
        return _Lease(container, self, acquire_ms, waiters_at_enter)

    def _release(self, container: _WarmContainer, healthy: bool) -> None:
        with self._cond:
            container.uses += 1
            recycle = (not healthy
                       or container.uses >= SANDBOX_POOL_MAX_USES
                       or container.key not in self._all)
            if recycle:
                self._all.pop(container.key, None)
                if not healthy:
                    self.stats_data["crash_replacements"] += 1
                else:
                    self.stats_data["recycles"] += 1
                self.stats_data["destroyed_total"] += 1
            else:
                self._idle.append(container)
                self.stats_data["warm_executions"] += 1
            self._cond.notify_all()
        if recycle:
            self._destroy(container)
            # 后台补充，不阻塞归还方
            threading.Thread(target=self._spawn, daemon=True,
                             name="sandbox-pool-refill").start()

    def _destroy(self, container: _WarmContainer) -> None:
        try:
            _run_docker(["rm", "-f", container.container_id], timeout=20)
        except Exception:
            pass
        shutil.rmtree(POOL_ROOT / container.key, ignore_errors=True)

    # ---- 观测 ----

    def stats(self) -> dict:
        with self._cond:
            return {
                "enabled": self.size > 0,
                "available": self._available,
                "degraded_reason": self._degraded_reason,
                "size": self.size,
                "warm_count": len(self._all),
                "idle": len(self._idle),
                "leased": len(self._all) - len(self._idle),
                "waiters": self._waiters,
                "queue_capacity": self.queue_capacity,
                **self.stats_data,
            }

    def close(self) -> None:
        with self._cond:
            self._closed = True
            containers = list(self._all.values())
            self._all.clear()
            self._idle.clear()
            self._cond.notify_all()
        for c in containers:
            self._destroy(c)


_pool: SandboxPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> SandboxPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = SandboxPool()
        return _pool


def warm_pool_async() -> None:
    """应用启动时后台预热（绝不阻塞、绝不抛出——失败即降级）。"""

    def _work():
        try:
            if get_pool().size > 0:
                get_pool().warmup()
        except Exception:
            pass

    threading.Thread(target=_work, daemon=True, name="sandbox-pool-warmup").start()


def reset_pool() -> None:
    """测试用：关闭并丢弃单例。"""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
        _pool = None
