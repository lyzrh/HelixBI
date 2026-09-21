"""评估 CLI：`python -m backend.evaluation`。

默认只跑离线阶段（确定性、无外部依赖，CI 可跑）；
加 `--with-pipeline` 才真实驱动 Agent 链路（需 Docker 沙箱 + LLM API Key）。

另外两个子能力：
- `--benchmark`：打印 Skill Retrieval V1(Baseline) vs V2 的对照表（Markdown），可落盘；
- `--tune-weights` / `--sensitivity`：权重的标定与敏感性分析（权重配置化的依据）。
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
    ret = report.get("retrieval") or {}
    for policy in ("v1", "v2"):
        wrong = ((ret.get(policy) or {}).get("fine") or {}).get("false_replay_cases") or []
        if wrong:
            print(f"\n[重放] {policy} 误重放样例：")
            for item in wrong[:5]:
                print(f"  - {item['id']}  score={item['score']}  {item['reason_code']}")
    for policy in ("v1", "v2"):
        fails = ((ret.get(policy) or {}).get("admission") or {}).get("failures") or []
        if fails:
            print(f"\n[准入] {policy} 判定不一致样例：")
            for item in fails[:5]:
                print(f"  - {item['id']} [{item['kind']}] expect {item['expect']}"
                      f"({item['expected_reason']}) got {item['got']}({item['got_reason']})")
    pipeline = report.get("end_to_end") or {}
    for item in (pipeline.get("errors") or [])[:5]:
        print(f"\n[端到端] {item.get('id')} 失败：{item.get('error')}")


def _run_benchmark(args) -> int:
    from backend.evaluation import retrieval_bench as bench

    result = bench.evaluate(top_k=args.top_k)
    markdown = bench.render_markdown(result)
    print(markdown)
    if args.markdown:
        Path(args.markdown).write_text(markdown + "\n", encoding="utf-8")
        print(f"\n已写出 Markdown：{args.markdown}")
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(bench.public_view(result), ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\n已写出 JSON：{args.json_path}")
    return 0


def _run_tuning(args) -> int:
    from backend import config
    from backend.evaluation import retrieval_bench as bench

    print("当前权重：", json.dumps(config.SKILL_RETRIEVAL_WEIGHTS, ensure_ascii=False))
    if args.sensitivity:
        rows = bench.sensitivity()
        print("\n信号      取值    准入正确率  误放行  Top1    重放P   重放R")
        for r in rows:
            value = "  -  " if r["value"] is None else f"{r['value']:.2f}"
            print(f"{r['signal']:10s} {value:>5s}   {r['admission_accuracy']:.2f}"
                  f"      {r['false_accept']:>2d}   {r['top1_accuracy']:.3f}"
                  f"   {r['replay_precision']:.3f}   {r['replay_recall']:.3f}")
    result = bench.tune_weights(rounds=args.rounds)
    print(f"\n坐标下降（{result['evaluations']} 次评估）得到：")
    print(" ", json.dumps(result["weights"], ensure_ascii=False))
    print("  目标（字典序）：", result["objective"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.evaluation",
        description="HelixBI Agent 评估：语义解析 / 规划契约 / Skill 匹配 / 重放准入 / 端到端",
    )
    parser.add_argument("--with-pipeline", action="store_true",
                        help="真实驱动 Agent 链路（需 Docker + LLM；缺失时自动跳过）")
    parser.add_argument("--limit", type=int, default=3,
                        help="端到端阶段的问题条数上限（默认 3）")
    parser.add_argument("--json", dest="json_path", default="",
                        help="把完整结果写成 JSON 文件")
    parser.add_argument("--verbose", action="store_true", help="额外打印失败样例明细")
    parser.add_argument("--benchmark", action="store_true",
                        help="只跑 Skill Retrieval Baseline vs V2 对照（Markdown 表格）")
    parser.add_argument("--markdown", default="", help="--benchmark 的 Markdown 输出路径")
    parser.add_argument("--top-k", type=int, default=5, help="Recall@K 的 K（默认 5）")
    parser.add_argument("--tune-weights", action="store_true", help="标定检索权重")
    parser.add_argument("--sensitivity", action="store_true",
                        help="配合 --tune-weights：打印单信号敏感性表")
    parser.add_argument("--rounds", type=int, default=2, help="坐标下降轮数")
    args = parser.parse_args(argv)

    if args.benchmark:
        return _run_benchmark(args)
    if args.tune_weights:
        return _run_tuning(args)

    report = run_all(with_pipeline=args.with_pipeline, limit=args.limit)
    print(render_report(report))

    if args.verbose:
        _print_failures(report)
    if args.json_path:
        slim = {k: v for k, v in report.items() if not k.startswith("_")}
        if "retrieval" in slim:
            slim["retrieval"] = {k: v for k, v in slim["retrieval"].items()
                                 if not k.startswith("_")}
        Path(args.json_path).write_text(
            json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出 JSON：{args.json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
