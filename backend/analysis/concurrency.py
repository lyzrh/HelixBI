"""运行并发控制（Production Runtime V1）。

改造前路由层只有一把 `asyncio.Semaphore(2)`：第三个并发请求直接 429，
没有队列、没有单用户限制、没有统计。本模块提供：

- **全局并发上限** `MAX_CONCURRENT_RUNS`：同时真正执行的分析运行数；
- **单用户上限** `MAX_CONCURRENT_PER_USER`：防止单个用户占满全部资源
  （复用 UserContext 的 user_id，服务端判定，与 RBAC 同源）；
- **等待队列** `RUN_QUEUE_SIZE`：超出的请求排队而不是立刻失败；
  队列也满 → `RunRejected`（明确拒绝）；排队超过 `RUN_QUEUE_TIMEOUT` 秒 →
  `RunQueueTimeout`（明确告知，不是无限等）；
- **统计**：active / per-user / 排队长度 / 累计拒绝与超时——写进
  `Run.trace.runtime` 与 `/api/health`，回答"为什么请求进入 queue"。

线程实现（分析运行在工作线程里跑），不依赖 asyncio 事件循环，多 worker
（uvicorn threads / gthread）下语义一致；撤销状态同样不依赖进程内事件循环。
"""

import threading
import time
from collections import Counter


class RunRejected(Exception):
    """等待队列已满，明确拒绝（HTTP 429 / SSE resource_limited）。"""


class RunQueueTimeout(Exception):
    """排队超时（SSE timeout 事件，timeout_type=queue）。"""


class RunSlot:
    """一次运行占用的并发槽位。`with registry.acquire(...)` 保证归还。"""

    def __init__(self, user_id: int | None, queue_wait_s: float,
                 cancel_event: threading.Event, deadline_ts: float | None,
                 registry: "RunRegistry"):
        self.user_id = user_id
        self.queue_wait_s = queue_wait_s
        self.cancel_event = cancel_event
        self.deadline_ts = deadline_ts      # monotonic 时间戳（None = 不限）
        self._registry = registry
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._registry._release(self.user_id)

    def __enter__(self) -> "RunSlot":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class RunRegistry:
    def __init__(self, max_runs: int, max_per_user: int,
                 queue_size: int, queue_timeout: float):
        self.max_runs = max(1, int(max_runs))
        self.max_per_user = max(1, int(max_per_user))
        self.queue_size = max(0, int(queue_size))
        self.queue_timeout = float(queue_timeout)
        self._cond = threading.Condition()
        self._active = 0
        self._per_user: Counter = Counter()
        self._waiters = 0
        self._closed = False
        self.stats_data = {
            "total_runs": 0, "total_queued": 0, "total_rejected": 0,
            "total_queue_timeouts": 0, "total_cancelled": 0,
        }

    def acquire(self, user_id: int | None = None,
                timeout: float | None = None) -> RunSlot:
        """获取一个执行槽位；排队行为见模块注释。"""
        timeout = self.queue_timeout if timeout is None else timeout
        t0 = time.monotonic()
        with self._cond:
            if self._closed:
                raise RunRejected("服务正在关闭，不再接受新的分析请求")
            if self._waiters >= self.queue_size:
                self.stats_data["total_rejected"] += 1
                raise RunRejected(
                    f"分析等待队列已满（{self.queue_size}），请稍后再试")

            self._waiters += 1
            self.stats_data["total_queued"] += 1
            try:
                deadline = time.monotonic() + timeout
                while True:
                    user_free = user_id is None or \
                        self._per_user[user_id] < self.max_per_user
                    if self._active < self.max_runs and user_free:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.stats_data["total_queue_timeouts"] += 1
                        raise RunQueueTimeout(
                            f"排队超时（>{timeout}s），当前并发 "
                            f"{self._active}/{self.max_runs}")
                    self._cond.wait(remaining)
            finally:
                self._waiters -= 1

            self._active += 1
            if user_id is not None:
                self._per_user[user_id] += 1
            self.stats_data["total_runs"] += 1

        cancel_event = threading.Event()
        deadline_ts = None
        from backend.config import TOTAL_RUN_TIMEOUT_SECONDS

        if TOTAL_RUN_TIMEOUT_SECONDS > 0:
            deadline_ts = time.monotonic() + TOTAL_RUN_TIMEOUT_SECONDS
        return RunSlot(user_id, time.monotonic() - t0, cancel_event,
                       deadline_ts, self)

    def _release(self, user_id: int | None) -> None:
        with self._cond:
            self._active = max(0, self._active - 1)
            if user_id is not None:
                self._per_user[user_id] = max(0, self._per_user[user_id] - 1)
            self._cond.notify_all()

    def stats(self) -> dict:
        with self._cond:
            return {
                "active_runs": self._active,
                "queue_waiters": self._waiters,
                "concurrency_limit": self.max_runs,
                "per_user_limit": self.max_per_user,
                "queue_size": self.queue_size,
                "per_user_active": dict(self._per_user),
                **self.stats_data,
            }

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()


_registry: RunRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> RunRegistry:
    """按当前 config 构建单例（首次调用时读取环境变量）。"""
    global _registry
    with _registry_lock:
        if _registry is None:
            from backend.config import (
                MAX_CONCURRENT_PER_USER, MAX_CONCURRENT_RUNS,
                RUN_QUEUE_SIZE, RUN_QUEUE_TIMEOUT,
            )

            _registry = RunRegistry(MAX_CONCURRENT_RUNS, MAX_CONCURRENT_PER_USER,
                                    RUN_QUEUE_SIZE, RUN_QUEUE_TIMEOUT)
        return _registry


def reset_registry() -> None:
    """测试用。"""
    global _registry
    with _registry_lock:
        _registry = None
