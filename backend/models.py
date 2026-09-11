"""SQLAlchemy 2.0 ORM 模型：元数据库 data/app.db 的 9 张表。

约定：时间戳存 ISO 文本；JSON 字段存 TEXT（json.dumps ensure_ascii=False）。
"""

import json

from sqlalchemy import (
    Boolean, Float, Index, Integer, String, Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def jdump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def jload(text, default=None):
    if not text:
        return default if default is not None else {}
    try:
        return json.loads(text)
    except Exception:
        return default if default is not None else {}


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), default="新会话")
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # → scene_agents
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)
    updated_at: Mapped[str] = mapped_column(String(19), default=now_str, onupdate=now_str)

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "agent_id": self.agent_id,
                "created_at": self.created_at, "updated_at": self.updated_at}


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("idx_messages_session", "session_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)

    def to_dict(self) -> dict:
        return {"id": self.id, "session_id": self.session_id, "role": self.role,
                "content": self.content, "meta": jload(self.meta),
                "created_at": self.created_at}


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("idx_runs_session", "session_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question: Mapped[str] = mapped_column(Text, default="")
    data_source_ids: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|done|failed
    spec: Mapped[str] = mapped_column(Text, default="{}")
    plan: Mapped[str] = mapped_column(Text, default="")
    code: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    stdout: Mapped[str] = mapped_column(Text, default="")
    stderr: Mapped[str] = mapped_column(Text, default="")
    run_dir: Mapped[str] = mapped_column(Text, default="")
    charts: Mapped[str] = mapped_column(Text, default="[]")
    tables: Mapped[str] = mapped_column(Text, default="{}")
    answer: Mapped[str] = mapped_column(Text, default="")
    followups: Mapped[str] = mapped_column(Text, default="[]")
    skill_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "session_id": self.session_id, "message_id": self.message_id,
            "question": self.question, "data_source_ids": jload(self.data_source_ids, []),
            "status": self.status, "spec": jload(self.spec), "plan": self.plan,
            "code": self.code, "attempts": self.attempts, "ok": bool(self.ok),
            "stdout": self.stdout, "stderr": self.stderr, "run_dir": self.run_dir,
            "charts": jload(self.charts, []), "tables": jload(self.tables),
            "answer": self.answer, "followups": jload(self.followups, []),
            "skill_id": self.skill_id, "duration_ms": self.duration_ms,
            "created_at": self.created_at,
        }


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    type: Mapped[str] = mapped_column(String(8))  # file | db
    # file 源
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 沙箱挂载用真实存储文件名（含扩展名），让生成代码能选对读取函数
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # csv|tsv|xlsx|xls|parquet|json|jsonl
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # db 源
    db_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # sqlite|mysql|postgresql
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    database_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 公共
    pack_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    columns_json: Mapped[str] = mapped_column(Text, default="[]")
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # db 物化缓存
    materialized_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    materialized_table: Mapped[str | None] = mapped_column(String(255), nullable=True)
    materialized_at: Mapped[str | None] = mapped_column(String(19), nullable=True)
    materialized_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)
    updated_at: Mapped[str] = mapped_column(String(19), default=now_str, onupdate=now_str)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "type": self.type,
            "file_path": self.file_path, "file_name": self.file_name,
            "file_type": self.file_type, "size_bytes": self.size_bytes,
            "db_type": self.db_type, "host": self.host, "port": self.port,
            "database_name": self.database_name, "username": self.username,
            "pack_id": self.pack_id, "columns": jload(self.columns_json, []),
            "row_count": self.row_count,
            "materialized_path": self.materialized_path,
            "materialized_table": self.materialized_table,
            "materialized_at": self.materialized_at,
            "materialized_truncated": bool(self.materialized_truncated),
            "builtin": bool(self.builtin),
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    pack_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[str] = mapped_column(Text, default="{}")
    code: Mapped[str] = mapped_column(Text, default="")
    columns_json: Mapped[str] = mapped_column(Text, default="[]")
    source_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[str] = mapped_column(Text, default="[]")
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)
    updated_at: Mapped[str] = mapped_column(String(19), default=now_str, onupdate=now_str)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "pack_id": self.pack_id, "question": self.question, "spec": jload(self.spec),
            "code": self.code, "columns": jload(self.columns_json, []),
            "source_run_id": self.source_run_id, "tags": jload(self.tags, []),
            "use_count": self.use_count, "success_count": self.success_count,
            "enabled": bool(self.enabled), "builtin": bool(self.builtin),
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


class SceneAgent(Base):
    __tablename__ = "scene_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    pack_id: Mapped[str] = mapped_column(String(64))
    data_source_ids: Mapped[str] = mapped_column(Text, default="[]")
    intro: Mapped[str] = mapped_column(Text, default="")
    recommended_questions: Mapped[str] = mapped_column(Text, default="[]")
    icon: Mapped[str] = mapped_column(String(64), default="robot")
    color: Mapped[str] = mapped_column(String(16), default="#2563eb")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)
    updated_at: Mapped[str] = mapped_column(String(19), default=now_str, onupdate=now_str)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "pack_id": self.pack_id, "data_source_ids": jload(self.data_source_ids, []),
            "intro": self.intro,
            "recommended_questions": jload(self.recommended_questions, []),
            "icon": self.icon, "color": self.color,
            "enabled": bool(self.enabled), "builtin": bool(self.builtin),
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


class Dashboard(Base):
    __tablename__ = "dashboards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)
    updated_at: Mapped[str] = mapped_column(String(19), default=now_str, onupdate=now_str)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description,
                "created_at": self.created_at, "updated_at": self.updated_at}


class DashboardItem(Base):
    __tablename__ = "dashboard_items"
    __table_args__ = (Index("idx_dashboard_items", "dashboard_id", "sort_order"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dashboard_id: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(16))  # chart|table|text|insight
    title: Mapped[str] = mapped_column(String(255), default="")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    source_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    span: Mapped[int] = mapped_column(Integer, default=12)  # 栅格宽度（AntD 24 栅格：6/8/12/24）
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)

    def to_dict(self) -> dict:
        return {"id": self.id, "dashboard_id": self.dashboard_id, "type": self.type,
                "title": self.title, "payload": jload(self.payload),
                "source_run_id": self.source_run_id, "sort_order": self.sort_order,
                "span": self.span, "created_at": self.created_at}


class Insight(Base):
    __tablename__ = "insights"
    __table_args__ = (Index("idx_insights_ds", "data_source_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_source_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(32))  # spike|streak|outlier|topn_shift|quality|threshold|gap
    severity: Mapped[str] = mapped_column(String(16))  # info|warning|critical
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="{}")
    report: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="new")  # new|read|resolved
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)

    def to_dict(self) -> dict:
        return {"id": self.id, "data_source_id": self.data_source_id,
                "rule_id": self.rule_id, "severity": self.severity,
                "title": self.title, "detail": self.detail,
                "evidence": jload(self.evidence), "report": self.report,
                "status": self.status, "created_at": self.created_at}


class TokenUsage(Base):
    """Token 用量追踪：每次 LLM 调用的 input/output token 计数。"""
    __tablename__ = "token_usages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 关联分析运行
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    node: Mapped[str] = mapped_column(String(32))  # parse_intent|generate_code|summarize|followup
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)

    def to_dict(self) -> dict:
        return {"id": self.id, "run_id": self.run_id, "session_id": self.session_id,
                "node": self.node, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cost_usd": self.cost_usd,
                "created_at": self.created_at}


class SystemSetting(Base):
    """系统级 KV 配置（定时扫描开关/间隔/运行状态等）。"""
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(19), default=now_str, onupdate=now_str)

    def to_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "updated_at": self.updated_at}
