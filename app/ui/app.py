import html as html_mod
import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime

import markdown as md_lib
import pandas as pd
import streamlit as st

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app import history, semantic  # noqa: E402
from app.config import MODEL_NAME, OPENAI_API_KEY  # noqa: E402
from app.graph import NODE_LABELS, parse_intent, stream_analysis  # noqa: E402
from app.report import build_html_report  # noqa: E402

st.set_page_config(
    page_title="AI 数据分析助手",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------- design tokens
CSS = """
<style>
:root { --accent:#2563eb; --ink:#0f172a; --muted:#64748b; --line:#e2e8f0; --bg:#f6f8fb; }

#MainMenu, footer { display: none !important; }
header[data-testid="stHeader"] { background: transparent !important; }
header[data-testid="stHeader"] [data-testid="stAppDeployButton"] { display: none !important; }
.block-container { padding: 1rem 1.8rem 2.5rem; max-width: 1560px; }
.app-view-container, .main { background: var(--bg); }

/* ---- sidebar ---- */
section[data-testid="stSidebar"] {
    background: #ffffff; border-right: 1px solid var(--line); min-width: 272px;
}
section[data-testid="stSidebar"] .block-container { padding: 1rem 1rem 1.2rem; }
.brand { display:flex; align-items:center; gap:10px; padding: 2px 6px 10px; }
.brand-logo {
    width:32px; height:32px; border-radius:8px; background:var(--ink);
    color:#fff; display:flex; align-items:center; justify-content:center; flex:none;
}
.brand-name { font-size:16px; font-weight:750; color:var(--ink); letter-spacing:.02em; }
.newtask-btn button {
    width:100%; background: var(--ink) !important; color:#fff !important;
    border:none !important; border-radius:9px !important; font-weight:600 !important;
    padding: 9px 0 !important; margin: 0 0 6px;
}
.newtask-btn button:hover { background:#1e293b !important; }
.alltasks {
    font-size:11.5px; font-weight:700; letter-spacing:.08em; color:var(--muted);
    margin: 14px 4px 4px; display:flex; align-items:center; gap:6px;
}
section[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="secondary"] {
    width:100%; background:transparent !important; color:#334155 !important; text-align:left !important;
    border:none !important; box-shadow:none !important; border-radius:8px !important;
    font-size:13px !important; font-weight:500 !important; padding: 7px 12px !important;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
section[data-testid="stSidebar"] div[data-testid="stButton"] > button[kind="secondary"]:hover {
    background:#f1f5f9 !important; color:#334155 !important;
}
.user-card {
    margin-top:12px; display:flex; align-items:center; gap:9px; padding:9px 10px;
    border:1px solid var(--line); border-radius:10px; background:#f8fafc;
}
.user-avatar {
    width:28px; height:28px; border-radius:50%; background:#e2e8f0; color:#475569;
    display:flex; align-items:center; justify-content:center; flex:none;
}
.user-name { font-size:13px; font-weight:650; color:var(--ink); }
.user-sub { font-size:11px; color:var(--muted); }
.env-line { font-size:11.5px; color:var(--muted); padding: 8px 4px 0; }
.env-dot { width:7px; height:7px; border-radius:50%; display:inline-block; margin-right:5px; }
.env-dot.ok { background:#22c55e; } .env-dot.bad { background:#ef4444; } .env-dot.warn { background:#f59e0b; }

/* ---- page head & callout ---- */
.page-head { display:flex; align-items:baseline; gap:12px; margin:2px 0 14px; }
.page-title { font-size:19px; font-weight:750; color:var(--ink); }
.page-sub { font-size:12.5px; color:var(--muted); }
.main-head { display:flex; align-items:center; gap:12px; padding: 0 4px 10px; }
.main-title { font-size:15.5px; font-weight:700; color:var(--ink); }
.head-badge {
    margin-left:auto; display:inline-flex; align-items:center; gap:6px; font-size:12px; color:#475569;
    background:#fff; border:1px solid var(--line); border-radius:999px; padding:4px 12px;
}
.head-badge + .head-badge { margin-left:0; }
.callout {
    background:#f8fafc; border:1px solid var(--line); border-left:3px solid #94a3b8;
    border-radius:8px; padding:11px 14px; font-size:13.5px; color:#475569; line-height:1.7;
}

/* ---- hero home ---- */
.hero { text-align:center; padding: 30px 0 4px; }
.hero-logo {
    width:46px; height:46px; border-radius:11px; margin: 0 auto 12px;
    background:var(--ink); color:#fff; display:flex; align-items:center; justify-content:center;
}
.hero-title { font-size:30px; font-weight:750; color:var(--ink); letter-spacing:.01em; }
.hero-sub { font-size:12px; color:var(--muted); letter-spacing:.26em; margin-top:7px; }
.rec-title {
    text-align:center; font-size:11.5px; font-weight:700; letter-spacing:.14em; color:var(--muted);
    margin: 30px 0 14px;
}
.ask-file {
    font-size:12px; color:var(--muted); background:#f1f5f9; border:1px solid var(--line);
    border-radius:999px; padding:3px 11px;
}

/* ---- chat ---- */
.q-user { display:flex; gap:10px; align-items:flex-start; margin: 6px 0 12px; }
.q-avatar {
    width:28px; height:28px; border-radius:50%; background:#e2e8f0; color:#475569;
    font-size:12px; font-weight:700; display:flex; align-items:center; justify-content:center; flex:none;
}
.q-bubble {
    background:#fff; border:1px solid var(--line); border-radius: 4px 12px 12px 12px;
    padding: 10px 14px; font-size:14.5px; font-weight:600; color:var(--ink);
}
.step-row { display:flex; align-items:center; gap:8px; padding:3px 0; }
.step-ico { width:20px; height:20px; border-radius:50%; background:#f1f5f9; color:#475569;
    font-size:11px; display:flex; align-items:center; justify-content:center; flex:none; }
.step-name { font-size:12.5px; font-weight:650; color:#334155; }
.step-detail { font-size:12px; color:var(--muted); margin-left:auto; white-space:nowrap;
    overflow:hidden; text-overflow:ellipsis; max-width:60%; }
.answer { font-size:14.5px; line-height:1.75; color:#1e293b;
    background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
.answer table { border-collapse:collapse; font-size:13px; margin:8px 0; }
.answer th, .answer td { border:1px solid var(--line); padding:5px 10px; }
.answer th { background:#f8fafc; }
.sec { font-size:11.5px; font-weight:700; letter-spacing:.08em; color:var(--muted); margin:14px 0 6px; }
.sec-inline { font-size:12px; font-weight:700; color:var(--muted); margin:12px 0 6px; }
.hint { font-size:13px; color:var(--muted); line-height:1.8; }

/* ---- sandbox window ---- */
.sbx-titlebar {
    display:flex; align-items:center; gap:7px; padding: 10px 14px;
    border-bottom:1px solid var(--line); background:#f8fafc;
}
.tl { width:11px; height:11px; border-radius:50%; }
.tl.r { background:#ff5f57; } .tl.y { background:#febc2e; } .tl.g { background:#28c840; }
.sbx-title { font-size:13px; font-weight:650; color:#475569; margin-left:6px; }
.sbx-done {
    margin-left:auto; display:inline-flex; align-items:center; gap:5px; font-size:12px; font-weight:600;
    color:#16a34a; background:#f0fdf4; border:1px solid #bbf7d0; border-radius:999px; padding:2px 10px;
}
.term {
    background:#0f172a; color:#86efac; font-family:"Cascadia Code",Consolas,monospace;
    font-size:12px; line-height:1.6; padding:13px 15px; border-radius:9px;
    white-space:pre-wrap; word-break:break-all; max-height:200px; overflow-y:auto; margin:0;
}
.sbx-empty { padding: 26px 18px; text-align:center; font-size:13px; color:var(--muted); line-height:1.8; }

div[data-testid="stExpander"] { border:1px solid var(--line); border-radius:10px; }
div[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:8px; }
div[data-testid="stForm"] { background:#fff; border:1px solid var(--line); border-radius:12px; padding:12px 14px; }
div[data-testid="stForm"] .stTextInput input { border:1px solid var(--line); border-radius:8px; font-size:14.5px; }
button[kind="primary"], button[kind="primaryFormSubmit"] {
    background: var(--ink) !important; color:#fff !important;
    border:none !important; border-radius:9px !important; font-weight:600 !important;
}
button[kind="secondary"] {
    background:#fff !important; color:#334155 !important;
    border:1px solid var(--line) !important; border-radius:8px !important;
    font-size:12.5px !important; font-weight:500 !important; padding:3px 12px !important;
    box-shadow:none !important;
}
button[kind="secondary"]:hover { border-color:#94a3b8 !important; color: var(--ink) !important; }
svg { vertical-align:middle; }
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)

DATA_TYPES = ["csv", "xlsx", "xls", "parquet"]
UPLOADER_KEY = "ds-uploads"


def _svg(d: str, size: int = 15) -> str:
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round">{d}</svg>'
    )


ICONS = {
    "logo": '<rect x="4" y="13" width="3.6" height="7" rx="1" fill="currentColor" stroke="none"/><rect x="10.2" y="8.5" width="3.6" height="11.5" rx="1" fill="currentColor" stroke="none"/><rect x="16.4" y="4" width="3.6" height="16" rx="1" fill="currentColor" stroke="none"/>',
    "explore": '<circle cx="12" cy="12" r="9"/><polygon points="15.5 8.5 13.5 13.5 8.5 15.5 10.5 10.5 15.5 8.5" fill="currentColor" stroke="none"/>',
    "bolt": '<path d="M13 2 4.5 13.5H11L9.5 22 18 10.5h-6.5L13 2Z"/>',
    "db": '<ellipse cx="12" cy="5.5" rx="7.5" ry="3"/><path d="M4.5 5.5v13c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3v-13"/><path d="M4.5 12c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3"/>',
    "book": '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V4H6.5A2.5 2.5 0 0 0 4 6.5v13Z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    "clip": '<path d="m21 12-8.5 8.5a5 5 0 0 1-7-7L14 4.5a3.5 3.5 0 0 1 5 5L10.5 18a2 2 0 0 1-3-3L16 7"/>',
    "chart": '<path d="M3 21h18"/><rect x="5" y="12" width="3.5" height="7" rx="1"/><rect x="10.2" y="8" width="3.5" height="11" rx="1"/><rect x="15.4" y="4" width="3.5" height="15" rx="1"/>',
    "trend": '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    "coins": '<circle cx="9" cy="9" r="6"/><path d="M15.5 4.2a6 6 0 0 1 0 15.6M7 15.5a6 6 0 0 0 12 .3"/>',
    "trophy": '<path d="M8 21h8M12 17v4M7 4h10v6a5 5 0 0 1-10 0V4Z"/><path d="M7 6H4.5a2.5 2.5 0 0 0 2.6 4M17 6h2.5a2.5 2.5 0 0 1-2.6 4"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5"/>',
}

# 技能库模板：前 4 个可直接运行，其余为规划中
SKILL_TEMPLATES = [
    ("chart", "#2563eb", "#eff6ff", "品类销售分析", "按品类汇总销售额并生成可视化图表",
     "各品类的总销售额是多少？按从高到低排序，并绘制条形图"),
    ("trend", "#16a34a", "#f0fdf4", "月度趋势", "按月份统计销售额趋势，绘制折线图",
     "按月份统计销售额趋势，并绘制折线图"),
    ("coins", "#d97706", "#fefce8", "客单价对比", "对比各区域的客单价（销售额/数量）",
     "哪个区域的客单价（销售额/数量）最高？"),
    ("trophy", "#9333ea", "#fdf4ff", "Top 10 订单", "找出销售额最高的十笔订单",
     "销售额 Top 10 的订单是哪些？"),
    ("trend", "#64748b", "#f1f5f9", "同环比对比", "自动计算同比增长与环比，标注异常波动", None),
    ("db", "#64748b", "#f1f5f9", "数据质量画像", "缺失值 / 重复行 / 分布偏斜一键体检", None),
]


@st.cache_data(ttl=60, show_spinner=False)
def docker_ok() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def load_df(name: str, raw: bytes | None = None) -> pd.DataFrame:
    p = PROJECT_ROOT / "uploads" / name
    p.parent.mkdir(exist_ok=True)
    if raw is not None:
        p.write_bytes(raw)
    suffix = name.lower()
    if suffix.endswith((".xlsx", ".xls")):
        return pd.read_excel(p)
    if suffix.endswith(".parquet"):
        return pd.read_parquet(p)
    return pd.read_csv(p)


def discover_disk_files() -> list[str]:
    up = PROJECT_ROOT / "uploads"
    if not up.exists():
        return []
    return sorted(
        p.name for p in up.iterdir()
        if p.suffix.lower() in (".csv", ".xlsx", ".xls", ".parquet")
    )


def file_columns(name: str) -> list[str]:
    try:
        return list(load_df(name).columns)
    except Exception:
        return []


def rel_time(ts: str) -> str:
    try:
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    except Exception:
        return ts
    delta = datetime.now() - t
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "刚刚"
    if mins < 60:
        return f"{mins}分钟前"
    if mins < 60 * 24:
        return f"{mins // 60}小时前"
    if mins < 60 * 24 * 7:
        return f"{mins // 1440}天前"
    return ts[:10]


def load_entry_artifacts(entry: dict) -> dict:
    run_dir = pathlib.Path(entry.get("run_dir") or "")
    result_path = run_dir / "result.json"
    if result_path.exists():
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
            entry["charts"] = [str(run_dir / c) for c in data.get("charts", [])]
            entry["tables"] = data.get("tables", {})
        except Exception:
            entry["charts"], entry["tables"] = [], {}
    else:
        entry["charts"], entry["tables"] = [], {}
    return entry


sandbox = docker_ok()
key_ok = bool(OPENAI_API_KEY)

# 数据源清单：上传控件带 key，其值在渲染前即可从 session_state 读取
_uploads = st.session_state.get(UPLOADER_KEY) or []
_disk = discover_disk_files()
AVAILABLE = [f.name for f in _uploads] + [n for n in _disk if n not in {f.name for f in _uploads}]


def build_semantic_block() -> tuple[str, str | None]:
    """按当前数据文件解析行业语义包，渲染 prompt 注入块。"""
    if not AVAILABLE:
        return "", None
    first = AVAILABLE[0]
    pack_id = semantic.pack_for_file(first, file_columns(first))
    return semantic.render_semantic_prompt(pack_id), pack_id


def render_steps(entry: dict) -> None:
    ok = entry.get("ok")
    spec = entry.get("spec") or {}
    spec_detail = "、".join(spec.get("metrics", [])) or str(spec.get("rewritten_question", ""))[:30] or "—"
    if spec.get("dimensions"):
        spec_detail += f" / 按{('、'.join(spec.get('dimensions', [])))}分组"
    steps = [
        ("1", "理解问题（语义解析）", spec_detail),
        ("2", "生成分析代码", (entry.get("plan") or "")[:40] or "—"),
        ("3", "沙箱执行",
         ("执行成功" if ok else "执行失败")
         + (f"（重试 {entry.get('attempts', 1) - 1} 次）" if entry.get("attempts", 1) > 1 else "")),
        ("4", "整理结论", "已生成" if entry.get("answer") else "—"),
        ("5", "推荐追问", f"{len(entry.get('followups', []))} 个" if entry.get("followups") else "—"),
    ]
    for no, name, detail in steps:
        st.markdown(
            f"""<div class="step-row"><span class="step-ico">{no}</span>
            <span class="step-name">{name}</span>
            <span class="step-detail">{html_mod.escape(str(detail))}</span></div>""",
            unsafe_allow_html=True,
        )


def render_answer(entry: dict, key_prefix: str) -> None:
    answer_html = md_lib.markdown(entry.get("answer", ""), extensions=["tables"])
    st.markdown(f'<div class="answer">{answer_html}</div>', unsafe_allow_html=True)
    if entry.get("followups"):
        st.markdown('<div class="sec-inline">推荐追问</div>', unsafe_allow_html=True)
        cols = st.columns(len(entry["followups"]))
        for j, q in enumerate(entry["followups"]):
            if cols[j].button(q, key=f"{key_prefix}-fu{j}", type="secondary"):
                st.session_state.pending = q
    if entry.get("charts"):
        st.markdown('<div class="sec-inline">图表</div>', unsafe_allow_html=True)
        for i in range(0, len(entry["charts"]), 2):
            cols = st.columns(2)
            for j, path in enumerate(entry["charts"][i : i + 2]):
                cols[j].image(path, use_container_width=True)
    if entry.get("tables"):
        st.markdown('<div class="sec-inline">结果表</div>', unsafe_allow_html=True)
        tabs = st.tabs(list(entry["tables"].keys()))
        for tab, (tname, rows) in zip(tabs, entry["tables"].items()):
            with tab:
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _stream_run(pending: str, spec: dict, sem_block: str) -> None:
    """第二阶段：带确认后的 QuerySpec 跑完整分析流并落库。"""
    saved = {n: str(PROJECT_ROOT / "uploads" / n) for n in AVAILABLE}
    with st.status("正在分析…", expanded=True) as status:
        final = None
        try:
            for node, delta, merged in stream_analysis(
                pending, saved,
                history=history.history_context(st.session_state.task_id, 3),
                spec=spec or None,
                semantic_block=sem_block,
            ):
                st.markdown(f"**{NODE_LABELS[node]}**")
                if node == "generate_code" and (delta.get("plan") or "").strip():
                    st.caption(delta["plan"].strip()[:200])
                if node == "execute":
                    ex = delta.get("execution", {})
                    st.caption(
                        f"执行成功 · 图表 {len(ex.get('charts', []))} · 表 {len(ex.get('tables', {}))}"
                        if ex.get("ok") else "执行出错，准备修复重试…"
                    )
            final = merged
        except Exception as exc:
            st.error(f"运行出错：{exc}", icon="⚠")
        status.update(label="分析完成" if final else "分析失败", state="complete")
    if final:
        execution = final["execution"]
        history.save_run({
            "ts": time.strftime("%Y-%m-%d %H:%M"),
            "question": pending,
            "ok": bool(execution.get("ok")),
            "answer": final["answer"]
            or (f"```\n{execution.get('stderr', '')[-800:]}\n```" if not execution.get("ok") else ""),
            "plan": final.get("plan", ""),
            "code": final.get("code", ""),
            "run_dir": execution.get("run_dir", ""),
            "followups": final.get("followups", []),
            "attempts": final.get("attempts", 1),
            "task_id": st.session_state.task_id,
            "stdout": execution.get("stdout", ""),
            "stderr": execution.get("stderr", ""),
            "spec": spec or final.get("spec", {}),
        })
        st.rerun()


def run_question(pending: str, preset_spec: dict | None = None) -> None:
    """入口：校验 → 语义解析 → 人工确认卡片 → 执行。

    preset_spec 不为空时跳过确认（重跑场景复用既有意图）。
    """
    if not AVAILABLE:
        st.error("请先在左侧上传数据文件（CSV / Excel / Parquet）。", icon="⚠")
        return
    if not OPENAI_API_KEY:
        st.error(
            "尚未配置模型 API Key。请复制项目根目录的 `.env.example` 为 `.env`，"
            "填写 `OPENAI_API_KEY` 后重启本应用。", icon="⚠"
        )
        return
    if not sandbox:
        st.error("Docker 沙箱未就绪。请先启动 Docker Desktop，再刷新本页面。", icon="⚠")
        return
    if not st.session_state.task_id:
        st.session_state.task_id = history.create_task(pending[:24])
    if not history.get_task_runs(st.session_state.task_id):
        history.rename_task(st.session_state.task_id, pending[:24])

    sem_block, pack_id = build_semantic_block()

    if preset_spec:
        _stream_run(pending, preset_spec, sem_block)
        return

    # ---- 阶段一：语义解析（意图理解）----
    with st.status("理解问题（语义解析）…", expanded=True) as s1:
        spec: dict = {}
        try:
            spec = parse_intent({
                "question": pending, "semantic_block": sem_block, "spec": {},
            }).get("spec", {})
            st.caption(json.dumps(spec, ensure_ascii=False)[:300])
        except Exception as exc:
            st.caption(f"解析降级：{exc}")
        s1.update(label="已理解，请确认分析意图", state="complete")

    if not isinstance(spec, dict):
        spec = {}
    spec.setdefault("rewritten_question", pending)

    # ---- 阶段二：人工确认（FineBI NEXT 式过程可控）----
    st.markdown('<div class="sec-inline">分析意图确认</div>', unsafe_allow_html=True)
    st.markdown('<div class="callout">以下为 Agent 对问题的语义解析结果，可直接修改后执行'
                f'（语义包：{semantic.pack_name(pack_id) if pack_id else "未识别"}）。</div>',
                unsafe_allow_html=True)
    with st.form("specform", clear_on_submit=False):
        c1, c2 = st.columns(2)
        metrics = c1.text_input("指标（逗号分隔）", value="、".join(spec.get("metrics", [])))
        dims = c2.text_input("分组维度（逗号分隔）", value="、".join(spec.get("dimensions", [])))
        c3, c4 = st.columns(2)
        compare = c3.selectbox("对比方式", ["无", "同比", "环比"],
                               index={"无": 0, "yoy": 1, "mom": 2}.get(spec.get("compare"), 0))
        chart = c4.selectbox("图表偏好", ["auto", "bar", "line", "pie"],
                             index=["auto", "bar", "line", "pie"].index(spec.get("chart", "auto"))
                             if spec.get("chart") in ("auto", "bar", "line", "pie") else 0)
        rewritten = st.text_input("规范化问题", value=spec.get("rewritten_question", pending))
        go_run = st.form_submit_button("确认并开始分析", use_container_width=True, type="primary")

    rc1, rc2 = st.columns([1, 3])
    reparse = rc1.button("重新解析", type="secondary")

    if reparse:
        run_question(pending)
        return

    if go_run:
        spec2 = {
            "metrics": [m.strip() for m in metrics.replace("，", ",").split(",") if m.strip()],
            "dimensions": [m.strip() for m in dims.replace("，", ",").split(",") if m.strip()],
            "compare": {"无": None, "同比": "yoy", "环比": "mom"}[compare],
            "chart": chart,
            "rewritten_question": rewritten or pending,
        }
        _stream_run(pending, spec2, sem_block)


# ---------------------------------------------------------------- pages
def page_explore():
    if "task_id" not in st.session_state:
        tasks = history.list_tasks(1)
        st.session_state.task_id = tasks[0]["id"] if tasks else None

    task_runs: list[dict] = []
    task_title = "新任务"
    if st.session_state.task_id:
        task_runs = [load_entry_artifacts(r)
                     for r in history.get_task_runs(st.session_state.task_id)]
        tasks_now = {t["id"]: t for t in history.list_tasks(50)}
        if st.session_state.task_id in tasks_now:
            t = tasks_now[st.session_state.task_id]
            task_title = t["title"]
            if task_runs and t["title"] == "新任务":
                task_title = task_runs[0]["question"][:24]

    pending = st.session_state.pop("pending", None)
    preset_spec = st.session_state.pop("preset_spec", None)

    st.markdown(
        f"""<div class="main-head"><span class="main-title">AI 数据分析助手</span>
        <span class="head-badge"><span class="env-dot {'ok' if sandbox else 'bad'}"></span>
        沙箱 {'就绪' if sandbox else '离线'}</span>
        <span class="head-badge"><span class="env-dot {'ok' if key_ok else 'warn'}"></span>{MODEL_NAME}</span>
        </div>""",
        unsafe_allow_html=True,
    )

    # ---- 主页（无运行记录）----
    if not task_runs:
        st.markdown(
            f"""<div class="hero"><div class="hero-logo">{_svg(ICONS['logo'], 24)}</div>
            <div class="hero-title">AI 数据分析助手</div>
            <div class="hero-sub">AGENTIC DATA DRIVEN DECISIONS</div></div>""",
            unsafe_allow_html=True,
        )
        lc, cc, rc = st.columns([1.4, 3.4, 1.4], gap="small")
        with cc:
            with st.form("qform-home", clear_on_submit=True):
                question = st.text_input(
                    "分析问题", label_visibility="collapsed",
                    placeholder="向数据提问、上传 CSV，或生成一份分析报告…",
                )
                tc1, tc2, tc3 = st.columns([3, 2, 1])
                tc1.markdown(
                    f'<span class="ask-file">{_svg(ICONS["clip"], 13)} '
                    f'{AVAILABLE[0] if AVAILABLE else "未上传数据文件"}</span>',
                    unsafe_allow_html=True,
                )
                tc2.markdown(
                    f"""<span class="ask-file" style="display:inline-flex;align-items:center;gap:6px">
                    <span style="width:16px;height:16px;border-radius:50%;background:var(--ink);color:#fff;
                    font-size:9px;display:inline-flex;align-items:center;justify-content:center">Z</span>
                    {MODEL_NAME}</span>""",
                    unsafe_allow_html=True,
                )
                go = tc3.form_submit_button("发送", use_container_width=True, type="primary")

        if go and question.strip():
            pending = question.strip()

        if not pending:
            # 行业动态示例：按当前数据文件匹配的语义包给出推荐问题
            examples: list[tuple[str, str]] = []
            seen_packs: list[str] = []
            for n in AVAILABLE:
                pid = semantic.pack_for_file(n, file_columns(n))
                if pid in seen_packs:
                    continue
                seen_packs.append(pid)
                pack = semantic.load_pack(pid) or {}
                for q in pack.get("example_questions", [])[:2]:
                    examples.append((q, semantic.pack_name(pid)))
            if not examples:
                examples = [("各品类的总销售额是多少？", "零售销售")]
            st.markdown('<div class="rec-title">RECOMMENDED EXAMPLES</div>', unsafe_allow_html=True)
            half = (len(examples) + 1) // 2
            for row_cols, chunk in ((st.columns(2, gap="medium"), examples[:half]),
                                    (st.columns(2, gap="medium"), examples[half:])):
                for col, (q, pname) in zip(row_cols, chunk):
                    with col:
                        with st.container(border=True):
                            st.markdown(
                                f"""<div style="padding:2px 0 8px">
                                <div style="font-size:13.5px;font-weight:700;color:var(--ink)">{html_mod.escape(q)}</div>
                                <div style="font-size:12px;color:var(--muted);margin-top:3px">{html_mod.escape(pname)}</div>
                                </div>""",
                                unsafe_allow_html=True,
                            )
                            if st.button("运行 →", key=f"ex-{hash(q)}", disabled=not AVAILABLE,
                                         type="secondary"):
                                pending = q

        if pending:
            run_question(pending)
        return

    # ---- 任务视图 ----
    col_chat, col_sbx = st.columns([0.58, 0.42], gap="medium")

    with col_chat:
        st.markdown(
            f"""<div style="display:flex;align-items:baseline;gap:12px;margin:0 0 10px">
            <span style="font-size:15px;font-weight:700;color:var(--ink)">{html_mod.escape(task_title)}</span>
            <span style="font-size:12px;color:var(--muted)">{len(task_runs)} 轮分析</span></div>""",
            unsafe_allow_html=True,
        )
        for idx, entry in enumerate(task_runs):
            st.markdown(
                f"""<div class="q-user"><span class="q-avatar">我</span>
                <span class="q-bubble">{html_mod.escape(entry['question'])}</span></div>""",
                unsafe_allow_html=True,
            )
            render_steps(entry)
            render_answer(entry, key_prefix=f"r{entry.get('id', idx)}")
            st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)

        with st.form("qform-task", clear_on_submit=True):
            c_in, c_btn = st.columns([5, 1])
            question = c_in.text_input(
                "分析问题", placeholder="继续追问…", label_visibility="collapsed"
            )
            go = c_btn.form_submit_button("发送", use_container_width=True, type="primary")
        if go and question.strip():
            run_question(question.strip())
        if pending:
            run_question(pending, preset_spec=preset_spec)

    with col_sbx:
        latest = task_runs[-1] if task_runs else None
        with st.container():
            if latest:
                st.markdown(
                    f"""<div class="sbx-titlebar">
                    <span class="tl r"></span><span class="tl y"></span><span class="tl g"></span>
                    <span class="sbx-title">沙箱工作区</span>
                    <span class="sbx-done">✓ 已{'完成' if latest.get('ok') else '失败'}</span>
                    </div>""",
                    unsafe_allow_html=True,
                )
                t1, t2 = st.tabs(["执行步骤", f"任务文件 {len(latest.get('charts', [])) + len(latest.get('tables', {}))}"])
                with t1:
                    render_steps(latest)
                    if latest.get("spec"):
                        with st.expander("QuerySpec（分析意图）"):
                            st.json(latest.get("spec"), expanded=True)
                    st.markdown('<div class="sec">生成代码</div>', unsafe_allow_html=True)
                    st.code(latest.get("code") or "# 无代码", language="python")
                with t2:
                    if latest.get("charts"):
                        for p in latest["charts"]:
                            st.image(p, use_container_width=True)
                    if latest.get("tables"):
                        for tname, rows in latest["tables"].items():
                            st.caption(tname)
                            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                    if not latest.get("charts") and not latest.get("tables"):
                        st.caption("本次运行没有图表或表格产物")
                st.markdown(
                    f"""<div style="padding:10px 14px;border-top:1px solid var(--line)">
                    <div class="term">{html_mod.escape(
                        ((latest.get('stdout') or '').strip() or (latest.get('stderr') or '').strip() or '（无输出）')
                    )}</div></div>""",
                    unsafe_allow_html=True,
                )
                a1, a2, a3 = st.columns(3)
                with a1:
                    if st.button("↻ 重跑", key="rerun", use_container_width=True, type="secondary"):
                        st.session_state.pending = latest["question"]
                        st.session_state.preset_spec = latest.get("spec") or None
                        st.rerun()
                with a2:
                    pin_label = "移出仪表盘" if latest.get("pinned") else "加入仪表盘"
                    if st.button(pin_label, key="pin", use_container_width=True, type="secondary"):
                        history.toggle_pin(latest["id"])
                        st.rerun()
                with a3:
                    e2 = dict(latest)
                    e2["answer_html"] = md_lib.markdown(e2.get("answer", ""), extensions=["tables"])
                    st.download_button(
                        "导出报告", data=build_html_report(e2),
                        file_name=f"分析报告-{e2.get('ts', '').replace(':', '')}.html",
                        mime="text/html", key="export-latest", use_container_width=True,
                        type="secondary",
                    )
            else:
                st.markdown(
                    """<div class="sbx-titlebar">
                    <span class="tl r"></span><span class="tl y"></span><span class="tl g"></span>
                    <span class="sbx-title">沙箱工作区</span></div>
                    <div class="sbx-empty">等待任务…<br>发起分析后，这里实时展示<br>沙箱中的代码、输出与产物</div>""",
                    unsafe_allow_html=True,
                )


def page_skills():
    st.markdown('<div class="page-head"><span class="page-title">技能库</span>'
                '<span class="page-sub">行业分析模板，点击直接对当前数据运行</span></div>', unsafe_allow_html=True)
    for row in range((len(SKILL_TEMPLATES) + 1) // 2):
        cols = st.columns(2, gap="medium")
        for col_i in range(2):
            idx = row * 2 + col_i
            if idx >= len(SKILL_TEMPLATES):
                continue
            icon_key, icon_color, bg, title, desc, q = SKILL_TEMPLATES[idx]
            runnable = q is not None
            with cols[col_i]:
                with st.container(border=True):
                    st.markdown(
                        f"""<div style="display:flex;gap:11px;align-items:flex-start;padding:2px 0 6px">
                        <span style="width:32px;height:32px;border-radius:8px;background:{bg};
                        color:{icon_color};display:flex;align-items:center;justify-content:center;flex:none">
                        {_svg(ICONS[icon_key], 16)}</span>
                        <span><div style="font-size:13.5px;font-weight:700;color:var(--ink)">{title}</div>
                        <div style="font-size:12px;color:var(--muted);line-height:1.55;margin-top:2px">{desc}</div>
                        </span></div>""",
                        unsafe_allow_html=True,
                    )
                    bcol, _ = st.columns([1, 1.4])
                    if bcol.button("运行" if runnable else "即将上线",
                                   key=f"tpl{idx}", type="secondary",
                                   disabled=not runnable or not AVAILABLE):
                        st.session_state.pending = q
                        run_question(q)


def page_datasources():
    st.markdown('<div class="page-head"><span class="page-title">数据源</span>'
                '<span class="page-sub">行业语义包分配 · CSV / Excel（多工作表）/ Parquet</span></div>',
                unsafe_allow_html=True)
    if not AVAILABLE:
        st.markdown('<div class="callout">还没有数据文件。在左侧「数据源」区域上传 CSV / Excel / Parquet 即可开始。</div>',
                    unsafe_allow_html=True)
        return
    packs = semantic.list_packs()
    pack_options = ["自动推断"] + [f"{p['name']}（{p['id']}）" for p in packs]
    for n in AVAILABLE:
        cols = list(file_columns(n))
        current = semantic.pack_for_file(n, cols)
        with st.container(border=True):
            c1, c2, c3 = st.columns([2.4, 2, 1.2])
            c1.markdown(f"**{n}**")
            c2.caption(f"{len(cols)} 个字段")
            if c3.button("设为默认", key=f"def-{n}", type="secondary",
                         disabled=current == semantic.DEFAULT_PACK):
                semantic.assign_pack(n, semantic.DEFAULT_PACK)
                st.rerun()
            choice = c2.selectbox(
                "行业包", pack_options,
                index=(pack_options.index(f"{semantic.pack_name(current)}（{current}）")
                       if any(current == p["id"] for p in packs) else 0),
                key=f"pack-{n}", label_visibility="collapsed",
            )
            if not choice.startswith("自动"):
                chosen = choice.split("（")[-1].rstrip("）")
                if chosen != current:
                    semantic.assign_pack(n, chosen)
                    st.rerun()
            c3.caption(f"当前：{semantic.pack_name(current)}")
            st.caption("字段：" + "、".join(cols[:8]) + ("…" if len(cols) > 8 else ""))


def page_knowledge():
    st.markdown('<div class="page-head"><span class="page-title">知识库</span>'
                '<span class="page-sub">让 Agent 带着业务上下文分析</span></div>', unsafe_allow_html=True)
    st.markdown(
        """<div class="callout"><b>已生效</b>：行业语义层（指标口径/维度/时间字段）随每次分析注入；
        任务内携带最近几轮问答作为上下文；解析后的 QuerySpec 可人工确认修正。</div>""",
        unsafe_allow_html=True,
    )
    st.markdown('<div class="sec">规划中</div>', unsafe_allow_html=True)
    st.markdown(
        """<div class="callout">
        <b>业务术语表</b> —— 沉淀字段口径（如"客单价 = 销售额/数量"），消除歧义<br>
        <b>few-shot 样例库</b> —— 优质问答对向量化检索，作为生成参考（借鉴 Vanna 的训练集机制）<br>
        <b>文档问答</b> —— 上传业务文档，分析时自动引用
        </div>""",
        unsafe_allow_html=True,
    )


def page_dashboard():
    st.markdown('<div class="page-head"><span class="page-title">仪表盘</span>'
                '<span class="page-sub">从任务分析中固定的关键结论与图表</span></div>', unsafe_allow_html=True)
    pins = history.list_pinned()
    if not pins:
        st.markdown('<div class="callout">还没有固定内容。在任务分析的「沙箱工作区」点击「加入仪表盘」，'
                    '把关键结论沉淀到这里，形成可复看的经营看板。</div>', unsafe_allow_html=True)
        return
    for row in range(0, len(pins), 2):
        cols = st.columns(2, gap="medium")
        for j, entry in enumerate(pins[row : row + 2]):
            entry = load_entry_artifacts(entry)
            with cols[j]:
                with st.container(border=True):
                    st.markdown(
                        f"""<div style="display:flex;align-items:baseline;gap:8px;padding:2px 0 6px">
                        <span style="font-size:13.5px;font-weight:700;color:var(--ink)">
                        {html_mod.escape(entry['question'])}</span>
                        <span style="font-size:11px;color:var(--muted);margin-left:auto">{entry.get('ts', '')}</span>
                        </div>""",
                        unsafe_allow_html=True,
                    )
                    answer_html = md_lib.markdown(entry.get("answer", ""), extensions=["tables"])
                    st.markdown(f'<div class="answer">{answer_html}</div>', unsafe_allow_html=True)
                    for p in entry.get("charts", [])[:2]:
                        st.image(p, use_container_width=True)
                    for tname, rows in (entry.get("tables") or {}).items():
                        with st.expander(f"结果表：{tname}"):
                            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                    if st.button("移出仪表盘", key=f"unpin-{entry['id']}", type="secondary"):
                        history.toggle_pin(entry["id"])
                        st.rerun()


# ---------------------------------------------------------------- navigation & sidebar
page_explore_p = st.Page(page_explore, title="探索", icon=":material/explore:", default=True)
page_skills_p = st.Page(page_skills, title="技能", icon=":material/bolt:")
page_datasources_p = st.Page(page_datasources, title="数据源", icon=":material/database:")
page_knowledge_p = st.Page(page_knowledge, title="知识库", icon=":material/menu_book:")
page_dashboard_p = st.Page(page_dashboard, title="仪表盘", icon=":material/space_dashboard:")
nav = st.navigation([page_explore_p, page_skills_p, page_datasources_p, page_knowledge_p, page_dashboard_p])

with st.sidebar:
    st.markdown(
        f"""<div class="brand"><div class="brand-logo">{_svg(ICONS['logo'], 17)}</div>
        <div class="brand-name">数据分析助手</div></div>""",
        unsafe_allow_html=True,
    )
    if st.button("＋ 新建任务", key="new-task"):
        st.session_state.task_id = history.create_task()
        st.session_state.nav_go_explore = True

    tasks = history.list_tasks(30)
    st.markdown('<div class="alltasks">全部任务 <span>›</span></div>', unsafe_allow_html=True)
    if tasks:
        for t in tasks:
            active = "▸ " if st.session_state.get("task_id") == t["id"] else ""
            title = t["title"][:20] + ("…" if len(t["title"]) > 20 else "")
            if st.button(f"{active}{title}  ·  {rel_time(t['ts'])}", key=f"task-{t['id']}",
                         use_container_width=True, help=t["title"]):
                st.session_state.task_id = t["id"]
                st.session_state.nav_go_explore = True
    else:
        st.caption("　暂无任务")

    st.markdown('<div class="sec">数据源</div>', unsafe_allow_html=True)
    st.file_uploader(
        "上传数据文件", type=DATA_TYPES, accept_multiple_files=True,
        label_visibility="collapsed", key=UPLOADER_KEY,
    )
    disk_now = discover_disk_files()
    up_names = {f.name for f in (st.session_state.get(UPLOADER_KEY) or [])}
    n_disk = len([n for n in disk_now if n not in up_names])
    if n_disk:
        st.caption(f"已加载 {n_disk} 个历史文件")

    st.markdown(
        f"""<div class="user-card"><div class="user-avatar">{_svg(ICONS['user'], 14)}</div>
        <div><div class="user-name">数据分析师</div>
        <div class="user-sub">本地工作区</div></div></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class="env-line">
        <span class="env-dot {'ok' if sandbox else 'bad'}"></span>沙箱 {'就绪' if sandbox else '离线'}　
        <span class="env-dot {'ok' if key_ok else 'warn'}"></span>{MODEL_NAME}</div>""",
        unsafe_allow_html=True,
    )

nav.run()

# 点任务/新建任务后回到探索页（SPA 内切换，无整页刷新）
if st.session_state.pop("nav_go_explore", False):
    try:
        st.switch_page(page_explore_p)
    except Exception:
        pass
