"""仪表板 / 单轮分析的 HTML 导出（自包含，图表 base64 内嵌）。

复用 backend/report/builder.py 的样式与实现思路。
"""

import base64
import html
import pathlib
from datetime import datetime

from backend.report.builder import build_html_report
from backend.models import Dashboard, DashboardItem, Insight, Run
from backend.analysis.runtime import chart_url_to_path
from backend.models import jload

STYLE = """
  body { font-family: "Segoe UI","Microsoft YaHei",sans-serif; color:#1e293b;
         max-width: 1100px; margin: 32px auto; padding: 0 20px; line-height:1.7; }
  h1 { font-size: 22px; border-bottom: 2px solid #2563eb; padding-bottom: 10px; }
  h2 { font-size: 18px; margin: 28px 0 12px; }
  .meta { color:#64748b; font-size:13px; margin-bottom: 4px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .card { border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; background:#fff; }
  .card h3 { font-size: 15px; margin: 0 0 10px; color:#334155; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { border: 1px solid #e2e8f0; padding: 5px 9px; text-align: left; }
  th { background: #f8fafc; }
  img { max-width: 100%; border: 1px solid #e2e8f0; border-radius: 8px; }
  .sev-critical { color:#dc2626; font-weight:600; }
  .sev-warning { color:#d97706; font-weight:600; }
  .sev-info { color:#2563eb; }
"""


def _rows_table(rows: list[dict], limit: int = 100) -> str:
    """rows → 静态 HTML 表格（导出用）。"""
    if not rows:
        return ""
    headers = list(rows[0].keys())
    thead = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    trs = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(r.get(h, '')))}</td>" for h in headers)
        + "</tr>" for r in rows[:limit])
    return f'<table><thead><tr>{thead}</tr></thead><tbody>{trs}</tbody></table>'


def build_dashboard_html(dashboard: Dashboard, items: list[DashboardItem],
                         allowed_run_dirs: set[str] | None = None,
                         visible_run_ids: set[int] | None = None) -> bytes:
    """仪表板导出（图表 base64 内嵌）。

    安全（Security Hardening V1）：仪表板条目的 `payload.chart_url` 与 `source_run_id`
    都是**客户端提交**的，因此导出时逐条校验：

    - `chart_url` 必须落在 RUNS_DIR 内（`chart_url_to_path` 拒绝路径遍历）；
    - 若传入了 `allowed_run_dirs`（当前工作区可见的产物目录），图表目录必须在其中——
      否则跳过该图，不把别人的工作区产物嵌进我们的报告；
    - 若传入了 `visible_run_ids`，`source_run_id` 不在其中时整条跳过。
    """
    cards_html = []
    for item in items:
        payload = jload(item.payload)
        if visible_run_ids is not None and item.source_run_id \
                and item.source_run_id not in visible_run_ids:
            continue
        inner = ""
        if item.type == "chart":
            path = chart_url_to_path(payload.get("chart_url", ""))
            allowed = (path is not None
                       and (allowed_run_dirs is None or path.parent.parent.name in allowed_run_dirs))
            if allowed and path.exists():
                b64 = base64.b64encode(path.read_bytes()).decode()
                inner = f'<img src="data:image/png;base64,{b64}"/>'
            elif payload.get("rows"):
                # 自助分析（ECharts 组件）条目：导出静态数据表兜底
                inner = _rows_table(payload["rows"])
        elif item.type == "table":
            inner = _rows_table(payload.get("rows", []))
        elif item.type == "text":
            inner = _markdown_lite(payload.get("text", ""))
        elif item.type == "insight":
            inner = _markdown_lite(payload.get("text", ""))
        if inner:
            cards_html.append(f'<div class="card"><h3>{html.escape(item.title)}</h3>{inner}</div>')

    doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>{html.escape(dashboard.name)} · 仪表板</title><style>{STYLE}</style></head><body>
<h1>{html.escape(dashboard.name)}</h1>
<div class="meta">由绎数 · Helix BI 工作台导出 · {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
<p class="meta">{html.escape(dashboard.description or '')}</p>
<div class="grid">{''.join(cards_html)}</div>
</body></html>"""
    return doc.encode("utf-8")


def build_run_html(run: Run) -> bytes:
    """单轮分析导出（复用 app/report.py）。路径越界（含 `../`）的图表一律忽略。"""
    import markdown as _md

    charts_abs = []
    for url in jload(run.charts, []):
        path = chart_url_to_path(url)
        if path is not None:
            charts_abs.append(str(path))
    entry = {
        "question": run.question,
        "ts": run.created_at,
        "answer": run.answer,
        "answer_html": _md.markdown(run.answer, extensions=["tables"]),
        "tables": jload(run.tables),
        "charts": charts_abs,
    }
    return build_html_report(entry)


def _markdown_lite(text: str) -> str:
    """轻量 markdown 渲染（结论/洞察文本）。"""
    try:
        import markdown as _md
        return _md.markdown(text, extensions=["tables"])
    except Exception:
        return f"<p>{html.escape(text)}</p>"
