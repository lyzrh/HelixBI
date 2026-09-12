# 企业级 AgentBI 改造实施计划（参考 FineBI NEXT）

> **历史文档**：本文件记录当时（Streamlit → FastAPI + React 改造期）的设计决策与落地清单，
> 其中的路径是**当时的**结构，已被后续重构取代。当前结构请看
> [AGENTS.md](../../AGENTS.md) 的架构地图与 [.agents/rules/architecture.md](../rules/architecture.md)。
> 路径对照：`app/graph.py` → `backend/agent/graph.py`；`app/sandbox.py` → `backend/agent/sandbox.py`；
> `app/semantic.py` + `semantics/` → `backend/semantic/` + `semantic_packs/`；
> `app/report.py` → `backend/report/builder.py`；
> `backend/services/*` → `backend/analysis/` `backend/skills/` `backend/insights/` `backend/datasource/` `backend/report/`。

## 一、背景与目标

当前项目是 LangGraph 驱动的对话式数据分析 Agent（Streamlit 单页 UI），本次改造参考帆软 FineBI NEXT（2026 Data Agent 平台）的功能形态，升级为**面向制造业和零售业的企业级 AgentBI**。

**用户已确认的决策：**
- UI 架构：**FastAPI + React 前后端分离**（替代 Streamlit 成为主界面，旧 UI 保留为 legacy）
- 数据源：**文件上传 + 数据库连接**（SQLAlchemy 接 SQLite/MySQL/PostgreSQL）
- 功能范围：**Skill 引擎 + 场景 Agent**、**主动洞察 + 仪表板沉淀**
- 内核保持现有线性 LangGraph 流程（不做 tool-calling 重构，不加指标中心/记忆中心）

**不变的底层（核心约束）：**
- `app/graph.py`：LangGraph 流程 `parse_intent → generate_code → execute(自修复) → summarize → suggest_followups`，只允许加 `skill_block` 最小钩子
- `app/sandbox.py`：Docker 无网络沙箱 + `dahelper.py` 结果契约（charts/tables/text）——分析数据永远以文件进入 `/data`
- `app/semantic.py` + `semantics/*.yaml`：行业语义包（retail_sales / manufacturing_production）
- 旧 Streamlit UI（`app/ui/app.py`）保留可独立运行（:8501）

## 二、总体架构

```
React 18 + AntD5 + Zustand (frontend/, Vite dev :5173, proxy → :8000)
   │  fetch + SSE 流解析
   ▼
FastAPI (backend/, :8000)
   ├── routers: sessions/analysis/datasources/skills/agents/insights/dashboards/misc
   ├── services: analysis_runner / datasource / skill_engine / insight_engine / report_export
   ├── 元数据库 SQLite data/app.db（SQLAlchemy, WAL，9 张表）
   └── 调用现有 app/ 内核
         ├── app/graph.stream_analysis()  ← 唯一钩子：AgentState.skill_block
         ├── app/semantic（行业包渲染 + registry.json）
         ├── app/sandbox.run_in_sandbox()（Docker 无网络沙箱）
         └── app/report.py（HTML 导出样式复用）
产物：runs/{run_id}/out（result.json + PNG）→ 静态挂载 /runs
数据：uploads/（文件源）、data/materialized/（DB 物化 parquet）
```

**核心不变式：LLM 代码只跑在 Docker 沙箱；分析数据以文件进入 /data；洞察引擎（自研确定性代码）跑本机进程。**

## 三、分阶段实施（6 阶段）

### 阶段 1：后端基座（FastAPI + 元数据库 + 数据源）

| 文件 | 职责 |
|---|---|
| `backend/__init__.py` | 包标识 |
| `backend/config.py` | `DB_PATH=data/app.db`、`MATERIALIZED_DIR=data/materialized`、`MATERIALIZED_MAX_ROWS=500000`（env 可覆盖）、`MATERIALIZED_TTL_HOURS=24`、`FRONTEND_DIST` |
| `backend/db.py` | SQLAlchemy engine（SQLite, WAL, StaticPool, timeout=30）+ `get_db()` 依赖 + `init_db()`（建表 + seed） |
| `backend/models.py` | 9 张表 ORM 模型（见第四节） |
| `backend/schemas.py` | Pydantic v2 请求/响应模型 |
| `backend/seed.py` | 幂等 seeding：注册 examples 两个 CSV 为内置数据源（复制进 uploads/ + `semantic.assign_pack`）、2 个场景 Agent、默认仪表板、4 个预置 Skill（手写 code，`{name}` 占位文件名） |
| `backend/main.py` | FastAPI 入口 + lifespan(init_db) + CORS(:5173) + 静态挂载 `/runs` `/uploads` `/data/materialized` + 生产模式 serve `frontend/dist`（SPA fallback） |
| `backend/routers/misc.py` | `GET /api/health`（含 sandbox bool，`docker info` 检测 60s 缓存）、`/api/semantic/packs`、`/api/stats` |
| `backend/routers/sessions.py` | 会话 CRUD + 历史消息（meta JSON 解析返回） |
| `backend/routers/datasources.py` | 上传/DB连接/列表/详情/表浏览/预览/物化/PACK/删除 |
| `backend/services/datasource.py` | `build_engine/test_connection/list_tables/preview/materialize/read_columns`；物化超限按 pack 的 time_field `ORDER BY DESC LIMIT` 截断并标记 |
| `requirements.txt`（修改） | 追加 fastapi/uvicorn[standard]/sqlalchemy/python-multipart/pyyaml/pymysql/psycopg2-binary |

### 阶段 2：分析 API（SSE 包装内核）

| 文件 | 改动 |
|---|---|
| `app/graph.py`（唯一内核修改） | 4 处：① `AgentState` 加 `skill_block: str`；② `_initial_state` 加参数；③ `run_analysis/stream_analysis` 加 `skill_block=""` 透传；④ `generate_code` 在 semantic_block 前拼接 `state.get("skill_block","")`。空字符串行为与现状逐字节等价，旧 UI 零影响 |
| `backend/services/analysis_runner.py` | `run_analysis_stream(db, session_id, question, data_source_ids, spec, skill_block, on_event)`；`_build_files`（DB 源取物化 parquet）、`_build_semantic_block`（逐文件 pack 渲染拼接）、`_history_from_session`（取近 3 轮）、`_chart_urls`（**run_id 含中文，必须 urllib.parse.quote**）、`_persist_result`（结束一次性落库） |
| `backend/routers/analysis.py` | `POST /api/sessions/{id}/analyze`（SSE）、`/parse`（仅意图解析）、`GET /api/runs/{id}`、`POST /api/runs/{id}/rerun`（SSE） |

**SSE 桥接关键**（`stream_analysis` 是同步生成器，内部阻塞 LLM/docker）：
- 请求进入：先落 user message + runs 行(status=running) → DB 源惰性物化检查（TTL 24h）→ `loop.run_in_executor` 线程跑内核，`on_event` 回调经 `loop.call_soon_threadsafe` 推入 asyncio.Queue，async 生成器消费输出 SSE 帧
- 客户端断开不影响 worker 线程，结果照常落库，刷新会话可见
- 落库时机：worker 自然结束后一次性 update run + 插入 assistant message（content=answer，meta 含 run_id/charts/tables/followups/spec）
- 并发护栏：`asyncio.Semaphore(2)`（Docker 资源），超出 429

### 阶段 3：前端骨架与对话页

| 文件 | 职责 |
|---|---|
| `frontend/package.json` / `vite.config.ts` / `tsconfig.json` / `index.html` | Vite+React18+TS；proxy `/api` `/runs` `/uploads` `/data` → :8000 |
| `src/main.tsx` / `App.tsx` / `theme.ts` | AntD ConfigProvider 中文 + token（colorPrimary #2563eb，Marvis 风格浅灰底白卡片）+ 7 路由 |
| `src/layouts/AppLayout.tsx` | 左侧固定侧栏（导航 + 沙箱/模型状态徽标，来自 /api/health）+ 内容区 |
| `src/api/client.ts` / `sse.ts` / `types.ts` | fetch 封装；`sseStream(url, body, onEvent, signal)` 手写 SSE 解析（fetch POST + ReadableStream，按 `\n\n` 切帧）；全量 TS 类型 |
| `src/stores/appStore.ts` / `chatStore.ts` / `datasourceStore.ts` | Zustand：健康/统计；会话/消息/流式状态机（idle→parsing→confirming→running→done/error） |
| `src/pages/Workbench.tsx` | 问候区 + 快捷提问胶囊（场景 Agent 推荐问题）+ 统计卡（ECharts sparkline）+ 最近会话 |
| `src/pages/Chat.tsx` + `src/components/chat/*` | SessionList / MessageList / MessageItem / StepProgress（5 步竖向进度）/ AnswerCard（react-markdown）/ ChartGallery / TableTabs / CodeCollapse / TerminalCollapse / FollowupCapsules / DataSourcePicker / AskInput / SpecConfirmModal |
| `src/pages/Datasources.tsx` | 文件上传卡片、DB 连接表单（Modal）、表浏览+预览抽屉、物化状态/手动刷新、行业包切换 |
| `src/components/common/` | PageHeader / EmptyState |

### 阶段 4：场景 Agent + Skill 引擎

| 文件 | 职责 |
|---|---|
| `backend/services/skill_engine.py` | `capture_from_run`（从成功 run 沉淀 question+spec+code+columns）；`match_skills`（2-gram 切词 + tags/pack 加权，top2, score≥2）；`render_skill_prompt`（few-shot 块："仅借鉴思路与口径，列名以本次数据为准"）；`run_skill` 双模式：**列集合匹配 → 直接重放 code（跳过 LLM 秒回，`{name}` 占位替换文件名）**；不匹配 → few-shot 走 `stream_analysis` |
| `backend/routers/skills.py` / `agents.py` | Skill CRUD + from-run + run(SSE)；场景 Agent CRUD |
| `src/pages/Agents.tsx` + `src/components/agent/*` | Agent 卡片墙，"开始对话"→创建绑定 session 跳 /chat/:id（默认勾选其数据源、intro 开场、推荐问题胶囊） |
| `src/pages/Skills.tsx` + `src/components/skill/*` | Skill 卡片（来源/使用次数）+ 运行 Modal（内嵌 SSE 步骤流） |
| `Chat.tsx`（扩展） | 分析完成后消息尾部"沉淀为 Skill"按钮 |

**场景 Agent 配置**：`{name, description, pack_id, data_source_ids, intro, recommended_questions, icon, color}`；预置"零售销售分析"（retail_sales + sample_sales.csv）和"生产制造分析"（manufacturing_production + sample_production.csv）。

### 阶段 5：主动洞察 + 仪表板

| 文件 | 职责 |
|---|---|
| `backend/services/insight_engine.py` | 规则检测（本机 pandas，避免 pandas 3.x 已移除 API）：`spike`（环比≥20%）/`streak`（连续≥3 期同向）/`outlier`（IQR 1.5×）/`topn_shift`（Top1 份额变化≥5pp）/`quality`（缺失/重复/负值/未来日期）/`threshold`（制造：达成率<90%、良率<95%；零售：区域客单价低于均值 30%）/`gap`（时间断档）；Finding 含 severity+数字证据 evidence；`generate_report`（LLM：现象→证据→可能原因→建议动作，markdown 存 insights.report） |
| `backend/routers/insights.py` | `POST /api/insights/generate`（同步检测入库）、列表/详情/生成报告/状态流转 |
| `backend/services/report_export.py` | `build_dashboard_html`（复用 app/report.py 样式与 base64 内嵌思路）；`build_run_html`（单轮导出） |
| `backend/routers/dashboards.py` | 仪表板 CRUD + 条目（chart_url/表格rows/结论文本/insight_id）+ `GET /export`（text/html 下载） |
| `src/pages/Insights.tsx` + `src/components/insight/*` | severity 徽标筛选、生成洞察（选数据源）、证据抽屉、诊断报告 Modal、"加入仪表板"/"转为分析任务" |
| `src/pages/Dashboards.tsx` + `src/components/dashboard/*` | 仪表板列表 + 条目网格卡片（可移除）+ 导出 HTML |
| `Chat.tsx`（扩展） | 图表/表格/结论旁"固定到仪表板"按钮 |

### 阶段 6：联调打磨与交付

- 生产模式 serve `frontend/dist` + SPA fallback（排除 /api /runs /uploads /data）
- 空态/加载态/错误态统一（AntD Empty/Skeleton/Result）
- E2E 回归 + `/api/health` 全绿
- 可选（确认后做）：README 增补新界面启动章节；`.gitignore` 追加 `data/`、`frontend/node_modules/`、`frontend/dist/`

## 四、元数据库 Schema（data/app.db，SQLite WAL）

时间戳 TEXT（ISO），JSON 字段 TEXT 存 `json.dumps(ensure_ascii=False)`：

- **sessions**：id, title, agent_id(→scene_agents, SET NULL), created_at, updated_at + idx(updated_at DESC)
- **messages**：id, session_id(CASCADE), role(user|assistant), content, meta(JSON: run_id/charts/tables/followups/spec/attempts/ok), created_at + idx(session_id,id)
- **runs**：id, session_id(CASCADE), message_id, question, data_source_ids(JSON), status(running|done|failed), spec, plan, code, attempts, ok, stdout, stderr, run_dir, charts(JSON 相对URL), tables(JSON ≤100行/表), answer, followups(JSON), skill_id, duration_ms, created_at
- **data_sources**：id, name(UNIQUE), type(file|db), file_path, size_bytes, db_type(sqlite|mysql|postgresql), host, port, database_name, username, password, pack_id, columns_json, row_count, materialized_path/table/at/truncated, builtin, created_at, updated_at
- **skills**：id, name, description, pack_id, question, spec, code, columns_json, source_run_id, tags, use_count, success_count, enabled, builtin, created_at, updated_at
- **scene_agents**：id, name, description, pack_id, data_source_ids(JSON), intro, recommended_questions(JSON), icon, color, enabled, builtin, created_at, updated_at
- **dashboards**：id, name, description, created_at, updated_at
- **dashboard_items**：id, dashboard_id(CASCADE), type(chart|table|text|insight), title, payload(JSON), source_run_id, sort_order, created_at
- **insights**：id, data_source_id(CASCADE), rule_id, severity(info|warning|critical), title, detail, evidence(JSON), report, status(new|read|resolved), created_at

## 五、SSE 事件协议（text/event-stream）

帧格式 `event: <type>\ndata: <json>\n\n`；每 15s `: ping` 保活；响应头 `Cache-Control: no-cache`、`X-Accel-Buffering: no`。

| event | data | 时机 |
|---|---|---|
| `step` | {node, label, status: running\|done\|error, detail, attempt?} | 每节点开始/结束；node 含 backend 附加的 materialize/skill |
| `spec` | {spec} | parse_intent 完成后 |
| `code` | {plan, code, attempt} | 每次 generate_code（自修复会多次） |
| `execute` | {ok, stdout, stderr, run_dir, charts, tables, text} | 每次 execute |
| `answer` | {answer} | summarize 完成 |
| `followups` | {followups} | suggest_followups 完成 |
| `charts`/`tables` | 最终数据（相对 URL，quote 编码） | 最终成功后 |
| `done` | {run_id, message_id, ok, duration_ms} | 结束且已落库 |
| `error` | {message} | 异常后关闭 |

## 六、关键实现细节备忘

1. **`execute` 节点每次重试生成新 run_id**，最终产物在最后一次 `execution.run_dir`；图表 URL 基于最终 run_dir + `urllib.parse.quote`（run_id 含中文）
2. **DB 物化**：惰性检查（TTL 24h / 文件不存在）→ 同步物化 parquet（超 500k 行按 time_field 截断+标记），发 `step(materialize)` 事件；沙箱侧与上传文件无差别
3. **Skill replay 的 code 中文件名用 `{name}` 占位**，replay 前按当前数据源 display 名替换
4. **洞察引擎在请求线程同步执行**（确定性自研代码，几十万行内秒级）
5. **仪表板 chart_url 指向 /runs 静态目录**（runs 持久存在；导出 HTML 是自包含备份）
6. **无 Docker 降级**：/api/health 暴露 sandbox bool，前端发送前拦截提示"请启动 Docker Desktop"；无沙箱时内核走自修复→summarize 如实报错不崩溃

## 七、依赖

**requirements.txt 追加**：`fastapi>=0.115`、`uvicorn[standard]>=0.32`、`sqlalchemy>=2.0`、`python-multipart>=0.0.12`、`pyyaml>=6.0`（当前靠 streamlit 传递依赖，backend 直接 import app.semantic 后必须显式）、`pymysql>=1.1`、`psycopg2-binary>=2.9`

**frontend**：react18 / react-router-dom@6 / antd@5 / @ant-design/icons / zustand@5 / echarts@5 + echarts-for-react / react-markdown@9 + remark-gfm / dayjs；dev: vite@6 / typescript@5.6 / @vitejs/plugin-react

## 八、验证方案

| 阶段 | 验证 |
|---|---|
| 1 | `.venv\Scripts\python -m uvicorn backend.main:app --reload --port 8000` → `/docs` 走查：health / 上传 sample_sales.csv 后 pack=retail_sales / seed 的 2 Agent + 2 内置数据源存在 / 9 张表 |
| 2 | `curl.exe -N -X POST :8000/api/sessions/1/analyze -d '{"question":"各品类销售额是多少","data_source_ids":[1]}'` 观察 SSE 序列；done 后 `GET /api/sessions/1` 消息/run 齐备；charts URL 浏览器可见 PNG |
| 3 | `cd frontend && npm install && npm run dev` → 全流程走查：选数据源→提问→Spec 确认→步骤流→图表/表格/代码/终端折叠→追问胶囊→切会话→刷新历史在 |
| 4 | 从成功 run 沉淀 Skill → 同数据源 replay 秒回 → 换生产数据源 few-shot（后端日志确认 skill_block 注入）→ Agents 页"开始对话"默认数据源/开场白正确 |
| 5 | `POST /api/insights/generate` 对 sample 数据应产出 quality/spike 类发现 → 生成 LLM 诊断 → 固定图表到仪表板 → 导出 HTML 离线打开自包含 |
| 6 | `npm run build` 后仅 uvicorn:8000 验证 SPA + 深链刷新；E2E：零售 Agent 全流程 / DB 源物化分析 / 洞察→报告→仪表板；`streamlit run app/ui/app.py` 旧 UI 不受影响 |

## 九、风险与注意事项

1. SSE 不启用 gzip 中间件；前端 fetch 不设超时（分析可能 >120s），仅 AbortController 取消
2. Windows 中文路径：run_id/图表 URL 必须 quote；docker -v 挂载现有 sandbox.py 已验证
3. 本机 docker 当前不在 PATH：health 暴露状态 + 前端拦截提示；端到端验证需启动 Docker Desktop
4. 旧 UI 共存：端口分离（8000/8501）、DB 分离（app.db vs history.db）、uploads 共享只读
5. SQLite 并发：WAL + StaticPool + timeout=30 + worker 线程独立 Session；单用户足够
6. DB 密码明文存 app.db：本地单用户工具可接受，注释注明生产化建议
7. pandas 3.0.5：洞察引擎避免 `append/applymap` 等已移除 API
8. graph.py 改动仅 4 处 skill_block 钩子，空字符串路径与现状等价，旧 Streamlit 零影响
