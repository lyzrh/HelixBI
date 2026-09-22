"""Run LLM-generated analysis code inside a disposable Docker container.

Isolation basics (PandasAI's approach): no network, CPU/memory caps, data
mounted read-only, only /out writable. The script communicates results back
through the dahelper JSON contract instead of stdout parsing.
"""

import json
import os
import pathlib
import shutil
import subprocess

from backend.config import (
    CODE_TIMEOUT_SECONDS,
    RUNS_DIR,
    SANDBOX_CPUS,
    SANDBOX_IMAGE,
    SANDBOX_MEMORY,
)


def resolve_docker() -> str | None:
    """解析 docker 可执行文件路径。

    优先 PATH（shutil.which）；PATH 条目损坏（如中文用户名乱码）时
    回退到 Docker Desktop 常见安装位置。
    """
    exe = shutil.which("docker")
    if exe:
        return exe
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        pathlib.Path(local) / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe",
        pathlib.Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"),
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


_INFRA_MARKERS = (
    "cannot connect to the docker api",
    "failed to connect to the docker api",
    "is the docker daemon running",
    "error during connect",
    "cannot connect to the docker daemon",
    "docker daemon is not running",
    "open //./pipe/",
    "未找到 docker 可执行文件",
)


def is_infra_failure(stderr: str) -> bool:
    """Docker 自身不可用（守护进程没起 / 找不到可执行文件）——重试生成代码没有意义。

    为什么要在宿主侧判定：只看 Python traceback 会把这类错误当 `unknown`，
    于是"再试一次"照样失败，白烧一轮 LLM。Self-Repair 拿到
    `failure_kind=sandbox_unavailable` 后会直接兜底，不再重试。
    """
    text = str(stderr or "").lower()
    return any(marker in text for marker in _INFRA_MARKERS)


class SandboxResult:
    """沙箱执行结果 + **结构化失败信息**。

    `exit_code / timed_out / failure_kind` 是 Self-Repair V2 新增的：只靠 stderr 文本
    猜"是超时还是被 OOM 杀掉"很脆（文案一改就失准），所以把宿主侧能看到的事实
    （超时由 subprocess 抛、137 由 SIGKILL 产生、docker 缺失在启动前就知道）显式带出来，
    分类器优先读这些字段（见 `backend/agent/repair.py::classify_error`）。
    """

    def __init__(self, ok: bool, stdout: str, stderr: str, out_dir: pathlib.Path,
                 exit_code: int | None = None, timed_out: bool = False,
                 failure_kind: str = ""):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.out_dir = out_dir
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.failure_kind = failure_kind
        self.result = self._read_result()

    def _read_result(self) -> dict:
        path = self.out_dir / "result.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    @property
    def charts(self) -> list[str]:
        return self.result.get("charts", [])

    @property
    def tables(self) -> dict:
        return self.result.get("tables", {})

    @property
    def text(self) -> str:
        return self.result.get("text", "")


def run_in_sandbox(run_id: str, code: str, files: dict[str, str]) -> SandboxResult:
    """files: display-name -> host source path; they are copied into the run dir."""
    run_dir = RUNS_DIR / run_id
    data_dir = run_dir / "data"
    out_dir = run_dir / "out"
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    for display_name, src in files.items():
        dest = data_dir / display_name
        if not dest.exists():
            dest.write_bytes(pathlib.Path(src).read_bytes())

    script_path = out_dir / "_analysis.py"
    script_path.write_text(code, encoding="utf-8")

    docker_exe = resolve_docker()
    if not docker_exe:
        return SandboxResult(
            ok=False, stdout="",
            stderr="未找到 docker 可执行文件：请确认 Docker Desktop 已安装并运行。",
            out_dir=out_dir, failure_kind="sandbox_unavailable",
        )

    cmd = [
        docker_exe, "run", "--rm",
        "--network", "none",
        "--cpus", str(SANDBOX_CPUS),
        "--memory", SANDBOX_MEMORY,
        "--pids-limit", "128",
        "-v", f"{data_dir}:/data:ro",
        "-v", f"{out_dir}:/out",
        SANDBOX_IMAGE,
        "python", "/out/_analysis.py",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CODE_TIMEOUT_SECONDS
        )
        ok = proc.returncode == 0
        stdout, stderr, exit_code, timed_out = proc.stdout, proc.stderr, proc.returncode, False
        failure_kind = ""
        if not ok:
            if is_infra_failure(stderr):
                # Docker 守护进程未启动等基础设施问题：不交给"重新生成代码"去解决
                failure_kind = "sandbox_unavailable"
            elif proc.returncode == 137:
                failure_kind = "resource_limit"
            else:
                failure_kind = "exit_code"
    except subprocess.TimeoutExpired as exc:
        ok = False
        exit_code, timed_out, failure_kind = None, True, "timeout"
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = f"执行超时（>{CODE_TIMEOUT_SECONDS}s），请优化代码性能或减少数据量。"

    return SandboxResult(ok=ok, stdout=stdout[-4000:], stderr=stderr[-4000:], out_dir=out_dir,
                         exit_code=exit_code, timed_out=timed_out, failure_kind=failure_kind)
