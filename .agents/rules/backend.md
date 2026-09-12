# 后端规则（FastAPI / Python）

## 分层与文件

- `backend/routers/`：**API 层**，一个业务域一个文件（analysis/sessions/datasources/agents/skills/insights/dashboards/explore/settings/usage/misc）。router 只做参数校验、调用领域模块、组装响应，**不写业务逻辑、不直接操作 ORM 细节**。
- **领域模块**（按能力划分，不按技术划分）：
  - `backend/agent/`：Agent 内核与沙箱客户端（`graph.py` / `prompts.py` / `profiler.py` / `sandbox.py`）。
  - `backend/analysis/`：`runtime.py` 对话式分析运行器（SSE 流式 + 落库）；`explore.py` 自助分析（本地 pandas / 只读 SQL，零 token）。
  - `backend/skills/`：`engine.py` Skill 捕获 / 匹配 / 重放 / few-shot。
  - `backend/insights/`：`engine.py` 规则扫描 + LLM 诊断，`scheduler.py` 定时任务。
  - `backend/datasource/`：`service.py` 文件 / 数据库接入、预览、parquet 物化。
  - `backend/semantic/`：语义包运行时（`registry.py` 加载检索 + `render.py` 渲染 prompt）。
  - `backend/report/`：`builder.py` 单轮分析 HTML，`exporter.py` 仪表板导出。
- 基础设施：`backend/config.py`（全局配置单一入口：路径 / LLM / 沙箱 / 数据接入）、`backend/db.py`（SQLite WAL）、`models.py` / `schemas.py`、`seed.py`。
- 配置数据：`semantic_packs/*.yaml`（业务语义唯一来源）；LLM 连接信息支持 `.env` 与设置中心热更新（改端点保存即生效，无需重启）。

## 惯例

- Python 3.11+，异步接口用 `async def`；SSE 用 StreamingResponse 输出 `spec/code/step/answer/chart/table` 等事件。
- SQLite 处于 WAL 模式，不要关闭；所有元数据访问走 SQLAlchemy，不要裸写 SQL 字符串拼接。
- LLM 调用统一走 OpenAI 兼容接口（DeepSeek / 智谱 / 通义 / vLLM 均可），不要引入厂商私有 SDK。
- Token 纪律：Skill 重放不经过 LLM；连接测试只发 `max_tokens=1` 探测；洞察诊断等批量任务注意控制上下文长度。
- 新增依赖先确认必要性，写入 `requirements.txt`；不要引入重量级框架替换现有 LangGraph 链路。
- 新增 / 移动领域目录时，同一次提交里同步更新 `AGENTS.md` 架构地图与 `.agents/rules/architecture.md`。

## 启动与验证

```bash
python -m uvicorn backend.main:app --port 8000
pytest -q
```

改完后端后至少验证：服务能启动、种子数据可加载（首次运行 seed）、相关 router 的冒烟请求通过、`pytest -q` 全绿。

改了语义解析 / Skill 匹配 / 沙箱链路等影响指标的行为时，额外跑 `python -m backend.evaluation` 看 Evaluation Report（见 `.agents/rules/architecture.md` 的指标门禁条目）。
