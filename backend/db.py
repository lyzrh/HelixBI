"""SQLite 元数据库引擎（WAL + StaticPool）与 FastAPI 依赖。"""

from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import DB_PATH
from backend.models import Base

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    import backend.seed as seed

    Base.metadata.create_all(engine)
    _migrate()
    with SessionLocal() as db:
        seed.run(db)
        _migrate_credentials(db)


def _migrate_credentials(db) -> None:
    """启动时把历史明文的数据源口令一次性加密（幂等；缺密钥则如实跳过并告警）。

    为什么放在启动而不是懒迁移：明文在库里多躺一分钟就多一分钟的风险；
    而且这一步是确定性的、可重复执行的，失败也不会影响服务启动。
    """
    import logging

    from backend.auth import audit
    from backend.datasource import secrets as credential_secrets

    logger = logging.getLogger(__name__)
    try:
        result = credential_secrets.migrate_plaintext_passwords(db)
    except Exception as exc:  # noqa: BLE001 — 迁移失败不阻塞启动，但必须可见
        logger.warning("数据源口令加密迁移失败：%s", exc)
        return
    if result["status"] == "skipped":
        logger.warning("数据源口令加密迁移跳过（%s）：历史明文将保持原样，"
                       "配置密钥后重启即可迁移", result["reason"])
        return
    if result["migrated"]:
        # 只记数量与结果，绝不记口令内容
        audit.record("datasource.credential_migrated", "ok", target="data_sources",
                     detail={"migrated": result["migrated"],
                             "remaining_plaintext": result["remaining"]})
        logger.info("已加密 %d 条历史明文数据源口令（剩余明文 %d 条）",
                    result["migrated"], result["remaining"])


def _migrate() -> None:
    """轻量列迁移：create_all 不会给已存在的表补新列。"""
    with engine.connect() as conn:
        ds_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(data_sources)")}
        for col in ("file_name", "file_type"):
            if col not in ds_cols:
                conn.exec_driver_sql(f"ALTER TABLE data_sources ADD COLUMN {col} TEXT")
                conn.commit()
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(dashboard_items)")}
        if "span" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE dashboard_items ADD COLUMN span INTEGER NOT NULL DEFAULT 12")
            conn.commit()
        run_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(runs)")}
        if "trace" not in run_cols:
            conn.exec_driver_sql("ALTER TABLE runs ADD COLUMN trace TEXT DEFAULT '{}'")
            conn.commit()
        # ---- 认证 / 工作区 / Skill 作用域（feat/user-auth-workspace-rbac）----
        ds_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(data_sources)")}
        if "workspace_id" not in ds_cols:
            conn.exec_driver_sql("ALTER TABLE data_sources ADD COLUMN workspace_id INTEGER")
            conn.commit()
        sk_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(skills)")}
        for col, ddl in (("scope", "TEXT DEFAULT 'workspace'"),
                         ("workspace_id", "INTEGER"), ("user_id", "INTEGER")):
            if col not in sk_cols:
                conn.exec_driver_sql(f"ALTER TABLE skills ADD COLUMN {col} {ddl}")
                conn.commit()
        sess_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(sessions)")}
        for col in ("user_id", "workspace_id"):
            if col not in sess_cols:
                conn.exec_driver_sql(f"ALTER TABLE sessions ADD COLUMN {col} INTEGER")
                conn.commit()
        # 仪表板工作区归属（历史行保持 NULL = 全局可见）
        dash_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(dashboards)")}
        if "workspace_id" not in dash_cols:
            conn.exec_driver_sql("ALTER TABLE dashboards ADD COLUMN workspace_id INTEGER")
            conn.commit()
        # Skill 数据源指纹（feat/skill-retrieval-v2）：历史行保持 NULL = 未知，
        # 检索层回退到列结构 + 读取函数兼容性判断，不因缺指纹而拒绝旧 Skill
        sk_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(skills)")}
        if "datasource_key" not in sk_cols:
            conn.exec_driver_sql("ALTER TABLE skills ADD COLUMN datasource_key TEXT")
            conn.commit()
