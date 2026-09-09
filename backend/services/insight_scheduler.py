"""定时洞察扫描调度器：FastAPI lifespan 启动的后台 asyncio 循环。

- 每 30 秒检查一次：开关开启且距上次扫描超过间隔 → 扫描全部可分析数据源
- 配置存 system_settings 表（KV），改配置即时生效，无需重启
- 扫描本身是纯 pandas 规则检测，不消耗 LLM 额度
"""

import asyncio
import json
from datetime import datetime, timedelta

from sqlalchemy import or_

from backend.db import SessionLocal
from backend.models import DataSource, Insight, SystemSetting, now_str
from backend.services import insight_engine

CHECK_INTERVAL_SECONDS = 30
DEFAULT_INTERVAL_MINUTES = 60
TIME_FMT = "%Y-%m-%d %H:%M:%S"

K_ENABLED = "insight_scan_enabled"
K_INTERVAL = "insight_scan_interval"
K_LAST_RUN = "insight_scan_last_run"
K_LAST_RESULT = "insight_scan_last_result"
K_AUTO_DIAGNOSE = "insight_auto_diagnose"

AUTO_DIAGNOSE_MAX_PER_RUN = 3  # 每轮最多自动诊断条数，控制模型额度消耗

_task: asyncio.Task | None = None
_running = asyncio.Lock()


def _get(db, key: str, default: str = "") -> str:
    row = db.get(SystemSetting, key)
    return row.value if row else default


def _set(db, key: str, value: str) -> None:
    row = db.get(SystemSetting, key)
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value))
    db.commit()


def get_schedule(db) -> dict:
    enabled = _get(db, K_ENABLED, "0") == "1"
    auto_diagnose = _get(db, K_AUTO_DIAGNOSE, "0") == "1"
    try:
        interval = int(_get(db, K_INTERVAL, str(DEFAULT_INTERVAL_MINUTES)))
    except ValueError:
        interval = DEFAULT_INTERVAL_MINUTES
    last_run = _get(db, K_LAST_RUN, "")
    try:
        last_result = json.loads(_get(db, K_LAST_RESULT, "{}"))
    except Exception:
        last_result = {}
    next_run_at = ""
    if enabled and last_run:
        try:
            nxt = datetime.strptime(last_run, TIME_FMT) + timedelta(minutes=interval)
            next_run_at = nxt.strftime(TIME_FMT)
        except ValueError:
            pass
    return {
        "enabled": enabled,
        "auto_diagnose": auto_diagnose,
        "interval_minutes": interval,
        "last_run_at": last_run,
        "next_run_at": next_run_at,
        "last_result": last_result,
    }


def set_schedule(db, enabled: bool | None, interval_minutes: int | None,
                 auto_diagnose: bool | None = None) -> dict:
    if interval_minutes is not None:
        if not (5 <= interval_minutes <= 1440):
            raise ValueError("扫描间隔需在 5~1440 分钟之间")
        _set(db, K_INTERVAL, str(interval_minutes))
    if enabled is not None:
        _set(db, K_ENABLED, "1" if enabled else "0")
    if auto_diagnose is not None:
        _set(db, K_AUTO_DIAGNOSE, "1" if auto_diagnose else "0")
    return get_schedule(db)


def run_scan() -> dict:
    """同步执行一次全量扫描（所有可分析数据源），独立数据库会话。

    开启 auto_diagnose 时，对尚无诊断报告的 warning/critical 洞察
    自动生成 LLM 诊断（每轮最多 3 条，控制额度消耗）。
    """
    db = SessionLocal()
    try:
        sources = db.query(DataSource).filter(or_(
            DataSource.type == "file",
            (DataSource.materialized_path.isnot(None))
            & (DataSource.materialized_path != ""),
        )).all()
        results = []
        for ds in sources:
            try:
                results.append(insight_engine.detect_and_persist(db, ds))
            except Exception:
                continue  # 单源失败不影响其他源

        # 自动诊断：仅处理无报告的 warning/critical，且限制条数
        diagnosed = 0
        if _get(db, K_AUTO_DIAGNOSE, "0") == "1":
            ds_by_id = {ds.id: ds for ds in sources}
            pending = (db.query(Insight)
                       .filter(Insight.report == "",
                               Insight.severity.in_(["warning", "critical"]))
                       .order_by(Insight.created_at.desc())
                       .limit(AUTO_DIAGNOSE_MAX_PER_RUN).all())
            for ins in pending:
                ds = ds_by_id.get(ins.data_source_id)
                if not ds:
                    continue
                try:
                    ins.report = insight_engine.generate_report(ins, ds)
                    diagnosed += 1
                except Exception:
                    continue  # 单条失败不影响整体（如未配置 Key）
            if diagnosed:
                db.commit()

        summary = {
            "ran_at": now_str(),
            "scanned": len(sources),
            "total": sum(r["total"] for r in results),
            "new": sum(r["new"] for r in results),
            "diagnosed": diagnosed,
        }
        _set(db, K_LAST_RUN, summary["ran_at"])
        _set(db, K_LAST_RESULT, json.dumps(summary, ensure_ascii=False))
        return summary
    finally:
        db.close()


async def _loop() -> None:
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            db = SessionLocal()
            try:
                enabled = _get(db, K_ENABLED, "0") == "1"
                interval = int(_get(db, K_INTERVAL, str(DEFAULT_INTERVAL_MINUTES)))
                last_run = _get(db, K_LAST_RUN, "")
            finally:
                db.close()
            if not enabled:
                continue
            if last_run:
                try:
                    last = datetime.strptime(last_run, TIME_FMT)
                    if (datetime.now() - last).total_seconds() < interval * 60:
                        continue
                except ValueError:
                    pass  # 时间解析失败视为需要扫描
            if _running.locked():
                continue  # 上一轮还在跑（手动触发重叠）
            async with _running:
                await asyncio.to_thread(run_scan)
        except asyncio.CancelledError:
            raise
        except Exception:
            continue  # 后台循环不能死


def start_scheduler() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


def stop_scheduler() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None
