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

from .config import (
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


class SandboxResult:
    def __init__(self, ok: bool, stdout: str, stderr: str, out_dir: pathlib.Path):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.out_dir = out_dir
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
            out_dir=out_dir,
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
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        ok = False
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = f"执行超时（>{CODE_TIMEOUT_SECONDS}s），请优化代码性能或减少数据量。"

    return SandboxResult(ok=ok, stdout=stdout[-4000:], stderr=stderr[-4000:], out_dir=out_dir)
