"""幂等 seeding：示例数据源、场景 Agent、默认仪表板、预置 Skill。

只在对应表为空时执行，可重复启动。内置 Skill 的 code 使用 {name} 占位
文件名，replay 前按当前数据源名替换（skill_engine 负责）。
"""

import shutil

from sqlalchemy.orm import Session

from backend.models import Dashboard, DataSource, SceneAgent, Skill
from backend.services.datasource import count_rows, read_columns

EXAMPLES = [
    ("sample_sales.csv", "零售销售示例数据", "retail_sales"),
    ("sample_production.csv", "生产制造示例数据", "manufacturing_production"),
]

AGENTS = [
    {
        "name": "零售销售分析",
        "description": "面向零售业务的销售 / 客单价 / 区域 / 趋势分析专家",
        "pack_id": "retail_sales",
        "data_source_names": ["sample_sales.csv"],
        "intro": "我是零售销售分析助手，已加载示例销售数据（订单日期 / 品类 / 区域 / 销售额 / 数量）。"
                 "可以直接问：各品类销售额、区域客单价对比、Top10 订单…",
        "recommended_questions": [
            "各品类的总销售额是多少？按从高到低排序",
            "近90天销售额的周趋势如何？计算环比",
            "哪个区域的客单价最高？",
        ],
        "icon": "shopping-cart",
        "color": "#2563eb",
    },
    {
        "name": "生产制造分析",
        "description": "面向制造业务的产量 / 良率 / 达成率 / 停机分析专家",
        "pack_id": "manufacturing_production",
        "data_source_names": ["sample_production.csv"],
        "intro": "我是生产制造分析助手，已加载示例生产数据（生产日期 / 产线 / 产品 / 计划产量 / 实际产量 / 不良数 / 停机分钟）。"
                 "可以直接问：产线达成率、良率趋势、不良分布…",
        "recommended_questions": [
            "各产线的实际产量是多少？达成率如何？",
            "近30天良率趋势，计算环比",
            "哪个产品的不良数最多？不良率是多少？",
        ],
        "icon": "tool",
        "color": "#16a34a",
    },
]

SKILLS = [
    {
        "name": "品类销售分析",
        "description": "按品类汇总销售额并排序，输出 Top 品类与集中度",
        "pack_id": "retail_sales",
        "question": "各品类的总销售额是多少？按从高到低排序",
        "tags": ["零售", "销售额", "品类"],
        "columns": ["订单日期", "品类", "区域", "销售额", "数量"],
        "code": '''import pandas as pd
import matplotlib.pyplot as plt
import dahelper

plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei"]
plt.rcParams["axes.unicode_minus"] = False

df = pd.read_csv("/data/{name}", parse_dates=["订单日期"])
t = df.groupby("品类")["销售额"].sum().sort_values(ascending=False).reset_index()
dahelper.save_table("品类销售额", t.round(2).to_dict("records"))

fig, ax = plt.subplots(figsize=(8, 4))
ax.bar(t["品类"], t["销售额"], color="#2563eb")
ax.set_title("各品类销售额")
dahelper.save_chart(fig, "品类销售额")

top = t.iloc[0]
top3_pct = t["销售额"].head(3).sum() / t["销售额"].sum() * 100
dahelper.save_text(f"销售额最高品类为 {top['品类']}（{top['销售额']:,.0f} 元）；Top3 品类占比 {top3_pct:.1f}%。")''',
    },
    {
        "name": "月度销售趋势",
        "description": "按月聚合销售额与销量，输出趋势与环比",
        "pack_id": "retail_sales",
        "question": "近几个月销售额的月度趋势如何？计算环比",
        "tags": ["零售", "趋势", "环比"],
        "columns": ["订单日期", "品类", "区域", "销售额", "数量"],
        "code": '''import pandas as pd
import matplotlib.pyplot as plt
import dahelper

plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei"]
plt.rcParams["axes.unicode_minus"] = False

df = pd.read_csv("/data/{name}", parse_dates=["订单日期"])
m = df.set_index("订单日期").resample("MS")[["销售额", "数量"]].sum().reset_index()
m["环比%"] = (m["销售额"].pct_change() * 100).round(2)
dahelper.save_table("月度销售趋势", m.round(2).to_dict("records"))

fig, ax = plt.subplots(figsize=(8, 4))
ax.plot(m["订单日期"], m["销售额"], marker="o", color="#2563eb")
ax.set_title("月度销售额趋势")
dahelper.save_chart(fig, "月度销售额趋势")

last = m.iloc[-1]
dahelper.save_text(f"最近月份（{last['订单日期']:%Y-%m}）销售额 {last['销售额']:,.0f} 元，环比 {last['环比%']:+.1f}%。")''',
    },
    {
        "name": "产线达成率与良率",
        "description": "按产线汇总计划/实际产量，计算达成率与良率",
        "pack_id": "manufacturing_production",
        "question": "各产线的实际产量是多少？达成率如何？",
        "tags": ["制造", "达成率", "良率"],
        "columns": ["生产日期", "产线", "产品", "计划产量", "实际产量", "不良数", "停机分钟"],
        "code": '''import pandas as pd
import matplotlib.pyplot as plt
import dahelper

plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei"]
plt.rcParams["axes.unicode_minus"] = False

df = pd.read_csv("/data/{name}", parse_dates=["生产日期"])
g = df.groupby("产线").agg(计划产量=("计划产量", "sum"), 实际产量=("实际产量", "sum"), 不良数=("不良数", "sum")).reset_index()
g["达成率%"] = (g["实际产量"] / g["计划产量"] * 100).round(2)
g["良率%"] = ((g["实际产量"] - g["不良数"]) / g["实际产量"] * 100).round(2)
dahelper.save_table("产线达成率与良率", g.round(2).to_dict("records"))

fig, ax = plt.subplots(figsize=(8, 4))
ax.bar(g["产线"], g["达成率%"], color="#16a34a", label="达成率%")
ax.axhline(100, color="#dc2626", linestyle="--", linewidth=1)
ax.set_title("各产线达成率")
dahelper.save_chart(fig, "产线达成率")

worst = g.loc[g["达成率%"].idxmin()]
dahelper.save_text(f"达成率最低的产线为 {worst['产线']}（{worst['达成率%']:.1f}%），良率 {worst['良率%']:.1f}%，建议优先排查。")''',
    },
]


def run(db: Session) -> None:
    _seed_data_sources(db)
    _seed_agents(db)
    _seed_dashboards(db)
    _seed_skills(db)
    db.commit()


def _seed_data_sources(db: Session) -> None:
    from pathlib import Path

    from app.config import PROJECT_ROOT

    # 回填历史空 row_count（文件型）；补齐旧数据的 file_name / file_type
    for ds in db.query(DataSource).filter(
            DataSource.type == "file", DataSource.row_count.is_(None)).all():
        if ds.file_path and Path(ds.file_path).exists():
            ds.row_count = count_rows(ds.file_path)
    for ds in db.query(DataSource).filter(
            DataSource.type == "file", DataSource.file_name.is_(None)).all():
        if ds.file_path:
            from backend.services.datasource import detect_file_type

            ds.file_name = Path(ds.file_path).name
            ds.file_type = detect_file_type(ds.file_path)

    if db.query(DataSource).filter(DataSource.builtin == True).count():  # noqa: E712
        return
    from app.semantic import assign_pack

    for filename, label, pack_id in EXAMPLES:
        src = PROJECT_ROOT / "examples" / filename
        dest = PROJECT_ROOT / "uploads" / filename
        if src.exists() and not dest.exists():
            shutil.copy(src, dest)
        if not dest.exists():
            continue
        columns = read_columns(str(dest))
        assign_pack(filename, pack_id)
        from backend.services.datasource import detect_file_type

        db.add(DataSource(
            name=filename, type="file", file_path=str(dest),
            file_name=dest.name, file_type=detect_file_type(str(dest)),
            size_bytes=dest.stat().st_size, pack_id=pack_id,
            columns_json=_jdump(columns), builtin=True,
            row_count=count_rows(str(dest)),
        ))
    db.flush()  # autoflush=False：显式 flush 让后续 seed 步骤能查到


def _seed_agents(db: Session) -> None:
    if db.query(SceneAgent).filter(SceneAgent.builtin == True).count():  # noqa: E712
        return
    name_to_id = {
        ds.name: ds.id for ds in db.query(DataSource).filter(DataSource.builtin == True)  # noqa: E712
    }
    for cfg in AGENTS:
        ds_ids = [name_to_id[n] for n in cfg["data_source_names"] if n in name_to_id]
        db.add(SceneAgent(
            name=cfg["name"], description=cfg["description"], pack_id=cfg["pack_id"],
            data_source_ids=_jdump(ds_ids), intro=cfg["intro"],
            recommended_questions=_jdump(cfg["recommended_questions"]),
            icon=cfg["icon"], color=cfg["color"], builtin=True,
        ))


def _seed_dashboards(db: Session) -> None:
    if db.query(Dashboard).count():
        return
    db.add(Dashboard(name="经营总览", description="从对话分析中固定的图表 / 表格与结论"))


def _seed_skills(db: Session) -> None:
    if db.query(Skill).filter(Skill.builtin == True).count():  # noqa: E712
        return
    # 预置 Skill 记录示例数据列集合，示例数据源运行时可直接 replay
    for cfg in SKILLS:
        db.add(Skill(
            name=cfg["name"], description=cfg["description"], pack_id=cfg["pack_id"],
            question=cfg["question"], code=cfg["code"], tags=_jdump(cfg["tags"]),
            columns_json=_jdump(cfg["columns"]),
            enabled=True, builtin=True,
        ))


def _jdump(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)
