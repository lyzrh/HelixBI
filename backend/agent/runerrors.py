"""运行期控制异常（Production Runtime V1）。

独立成模块的原因：`backend/agent/graph.py`（抛出方）与 `backend/analysis/runtime.py`
（捕获方）都要引用，放任何一边都会造成循环导入。

- `RunCancelled`：客户端断开 / 主动取消。Agent 在**节点边界**响应（粒度 = 单个
  节点执行完为止），沙箱内的单次执行不可中断，由执行超时兜底。
- `RunDeadlineExceeded`：整轮运行超过 `TOTAL_RUN_TIMEOUT_SECONDS`。
  Self-Repair 的每一轮修复都要先过 execute 的 deadline 预检，所以修复次数再多
  也不可能突破总时限。
"""

import time


class RunCancelled(Exception):
    """运行被取消（客户端断开或主动取消）。"""


class RunDeadlineExceeded(Exception):
    """整轮运行超过总时限。"""

    def __init__(self, timeout_type: str = "total_run", message: str = ""):
        self.timeout_type = timeout_type
        super().__init__(message or f"运行超过总时限（{timeout_type}）")


def check_runtime_limits(limits: dict | None) -> None:
    """统一入口：在任何阻塞动作（沙箱执行 / LLM 调用）之前调用。

    取消优先于超时——用户已经不等了，再报超时没有意义。
    """
    if not limits:
        return
    cancel = limits.get("cancel")
    if cancel is not None and cancel.is_set():
        raise RunCancelled()
    deadline = limits.get("deadline_ts")
    if deadline and time.monotonic() > deadline:
        raise RunDeadlineExceeded()
