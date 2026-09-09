"""Pydantic v2 请求/响应模型。"""

from pydantic import BaseModel, Field


# ---- 会话 ----
class SessionCreate(BaseModel):
    title: str = "新会话"
    agent_id: int | None = None


class SessionPatch(BaseModel):
    title: str | None = None
    agent_id: int | None = None


# ---- 分析 ----
class AnalyzeBody(BaseModel):
    question: str = Field(min_length=1)
    data_source_ids: list[int] = Field(default_factory=list)
    spec: dict | None = None  # 已人工确认的 QuerySpec（跳过 parse_intent）
    agent_id: int | None = None


class ParseBody(BaseModel):
    question: str
    data_source_ids: list[int] = Field(default_factory=list)


# ---- 数据源 ----
class DbConfig(BaseModel):
    db_type: str = Field(pattern="^(sqlite|mysql|postgresql)$")
    host: str = ""
    port: int | None = None
    database: str = ""
    username: str = ""
    password: str = ""
    sqlite_path: str = ""  # sqlite 文件路径


class DataSourceDbCreate(BaseModel):
    name: str
    config: DbConfig
    pack_id: str | None = None


class DataSourcePatch(BaseModel):
    name: str | None = None
    pack_id: str | None = None


class MaterializeBody(BaseModel):
    table: str | None = None
    force: bool = False


class DbTestBody(BaseModel):
    config: DbConfig


# ---- Skill ----
class SkillCreate(BaseModel):
    name: str
    description: str = ""
    pack_id: str | None = None
    question: str = ""
    code: str = ""
    tags: list[str] = Field(default_factory=list)


class SkillFromRun(BaseModel):
    run_id: int
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class SkillPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    tags: list[str] | None = None


class SkillRunBody(BaseModel):
    session_id: int | None = None
    data_source_ids: list[int] = Field(default_factory=list)


# ---- 场景 Agent ----
class AgentCreate(BaseModel):
    name: str
    description: str = ""
    pack_id: str
    data_source_ids: list[int] = Field(default_factory=list)
    intro: str = ""
    recommended_questions: list[str] = Field(default_factory=list)
    icon: str = "robot"
    color: str = "#2563eb"


class AgentPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    pack_id: str | None = None
    data_source_ids: list[int] | None = None
    intro: str | None = None
    recommended_questions: list[str] | None = None
    icon: str | None = None
    color: str | None = None
    enabled: bool | None = None


# ---- 洞察 ----
class InsightGenerateBody(BaseModel):
    data_source_ids: list[int] = Field(min_length=1)


class InsightPatch(BaseModel):
    status: str | None = None  # new|read|resolved


# ---- 仪表板 ----
class DashboardCreate(BaseModel):
    name: str
    description: str = ""


class DashboardPatch(BaseModel):
    name: str | None = None
    description: str | None = None


class DashboardItemCreate(BaseModel):
    type: str = Field(pattern="^(chart|table|text|insight)$")
    title: str = ""
    payload: dict = Field(default_factory=dict)
    source_run_id: int | None = None


class DashboardItemPatch(BaseModel):
    title: str | None = None
    span: int | None = Field(default=None, ge=4, le=24)
    payload: dict | None = None


class ItemReorderBody(BaseModel):
    ids: list[int] = Field(min_length=1)  # 按新顺序排列的条目 id


# ---- 通用导出 ----
class ExportSheet(BaseModel):
    name: str = "数据"
    rows: list[dict] = Field(default_factory=list)


class ExportTableBody(BaseModel):
    filename: str = "导出数据"
    sheets: list[ExportSheet] = Field(min_length=1)
