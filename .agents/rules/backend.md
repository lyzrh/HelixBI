# 后端规则（FastAPI / Python）

## 分层与文件

- `backend/routers/`：一个业务域一个文件（analysis/sessions/datasources/agents/skills/insights/dashboards/explore/settings/usage/misc）。router 只做参数校验、调用 service、组装响应，**不写业务逻辑、不直接操作 ORM 细节**。
- `backend/services/`：业务逻辑。`analysis_runner.py` 是 LangGraph 包装层（SSE 流式）；`skill_engine.py` 负责 Skill 捕获/匹配/重放；`insight_engine.py` + `insight_scheduler.py` 负责规则扫描 + 定时任务 + LLM 诊断；`datasource.py` 负责文件/数据库接入与 parquet 物化。
- 模型：ORM 改 `backend/models.py`，API 模型改 `backend/schemas.py`；内置示例数据在 `backend/seed.py`。
- 配置：`backend/config.py`；LLM 连接信息支持 `.env` 与设置中心热更新（改端点保存即生效，无需重启）。

## 惯例

- Python 3.11+，异步接口用 `async def`；SSE 用 StreamingResponse 输出 `spec/code/step/answer/chart/table` 等事件。
- SQLite 处于 WAL 模式，不要关闭；所有元数据访问走 SQLAlchemy，不要裸写 SQL 字符串拼接。
- LLM 调用统一走 OpenAI 兼容接口（DeepSeek / 智谱 / 通义 / vLLM 均可），不要引入厂商私有 SDK。
- Token 纪律：Skill 重放不经过 LLM；连接测试只发 `max_tokens=1` 探测；洞察诊断等批量任务注意控制上下文长度。
- 新增依赖先确认必要性，写入 `requirements.txt`；不要引入重量级框架替换现有 LangGraph 链路。

## 启动与验证

```bash
python -m uvicorn backend.main:app --port 8000
```

改完后端后至少验证：服务能启动、种子数据可加载（首次运行 seed）、相关 router 的冒烟请求通过。
