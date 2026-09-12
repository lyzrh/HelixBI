"""评估 CLI：`python -m backend.evaluation`。

默认只跑离线阶段（确定性、无外部依赖，CI 可跑）；
加 `--with-pipeline` 才真实驱动 Agent 链路（需 Docker 沙箱 + LLM API Key）。
"""

import argparse
import json
import sys
from pathlib import Path

from backend.evaluation.metrics import render_report
from backend.evaluation.runner import run_all


def _print_failures(report: dict) -> None:
    for stage, label in (("semantic", "语义解析"), ("planning", "口径注入")):
        failures = (report.get(stage) or {}).get("failures") or []
        if not failures:
            continue
        print(f"\n[{label}] 未完全命中的样例（前 5 条）：")
        for item in failures[:5]:
            print(f"  - {item.get('id')}  {item.get('question')}")
            if item.get("expected") is not None:
                print(f"      expected: {item['expected']}")
                print(f"      got     : {item.get('got')}")
            if item.get("not_injected"):
                print(f"      未注入 prompt: {item['not_injected']}")
    pipeline = report.get("end_to_end") or {}
    for item in (pipeline.get("errors") or [])[:5]:
        print(f"\n[端到端] {item.get('id')} 失败：{item.get('error')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.evaluation",
        description="HelixBI Agent 评估：语义解析 / 规划契约 / Skill 匹配 / 端到端",
    )
    parser.add_argument("--with-pipeline", action="store_true",
                        help="真实驱动 Agent 链路（需 Docker + LLM；缺失时自动跳过）")
    parser.add_argument("--limit", type=int, default=3,
                        help="端到端阶段的问题条数上限（默认 3）")
    parser.add_argument("--json", dest="json_path", default="",
                        help="把完整结果写成 JSON 文件")
    parser.add_argument("--verbose", action="store_true", help="额外打印失败样例明细")
    args = parser.parse_args(argv)

    report = run_all(with_pipeline=args.with_pipeline, limit=args.limit)
    print(render_report(report))

    if args.verbose:
        _print_failures(report)
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出 JSON：{args.json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
