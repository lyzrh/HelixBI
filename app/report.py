"""Self-contained HTML report export (answer + tables + embedded chart PNGs)."""

import base64
import html
import pathlib


def build_html_report(entry: dict) -> bytes:
    q = html.escape(entry.get("question", ""))
    ts = html.escape(entry.get("ts", ""))
    answer_html = entry.get("answer_html") or f"<p>{html.escape(entry.get('answer', ''))}</p>"

    tables_html = ""
    for tname, rows in entry.get("tables", {}).items():
        if not rows:
            continue
        headers = list(rows[0].keys())
        thead = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
        body_rows = []
        for r in rows[:100]:
            body_rows.append(
                "<tr>" + "".join(f"<td>{html.escape(str(r.get(h, '')))}</td>" for h in headers) + "</tr>"
            )
        tables_html += (
            f"<h3>{html.escape(str(tname))}</h3>"
            f"<table><thead><tr>{thead}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
        )

    charts_html = ""
    for path in entry.get("charts", []):
        p = pathlib.Path(path)
        if p.exists():
            b64 = base64.b64encode(p.read_bytes()).decode()
            charts_html += (
                f'<img src="data:image/png;base64,{b64}" alt="{html.escape(p.stem)}"/>'
            )

    doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>{q} · 分析报告</title>
<style>
  body {{ font-family: "Segoe UI","Microsoft YaHei",sans-serif; color:#1e293b;
         max-width: 900px; margin: 32px auto; padding: 0 20px; line-height:1.7; }}
  h1 {{ font-size: 22px; border-bottom: 2px solid #2563eb; padding-bottom: 10px; }}
  h3 {{ font-size: 16px; margin: 24px 0 8px; }}
  .meta {{ color:#64748b; font-size:13px; margin-bottom: 4px; }}
  .tag {{ display:inline-block; background:#eff6ff; color:#1d4ed8; font-size:12px;
          border:1px solid #bfdbfe; border-radius:5px; padding:2px 8px; margin-bottom:16px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
  th, td {{ border: 1px solid #e2e8f0; padding: 6px 10px; text-align: left; }}
  th {{ background: #f8fafc; }}
  img {{ max-width: 100%; margin: 8px 0; border: 1px solid #e2e8f0; border-radius: 8px; }}
</style></head><body>
<h1>{q}</h1>
<div class="meta">由数据分析工作台生成 · {ts}</div>
<span class="tag">分析报告</span>
{answer_html}
{charts_html and '<h2>图表</h2>' + charts_html or ''}
{tables_html and '<h2>结果表</h2>' + tables_html or ''}
</body></html>"""
    return doc.encode("utf-8")
