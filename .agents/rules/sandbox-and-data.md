# 沙箱与数据安全规则

> 涉及沙箱、数据源、文件读写的改动必读。这一层是产品的安全底线。

## Docker 沙箱（backend/agent/sandbox.py + sandbox/）

- 镜像基于 `python:3.11-slim`，预装 pandas / pyarrow / matplotlib / 中文字体与 `dahelper`。
- 构建命令：`docker build -t helix-sandbox:latest sandbox/`；Docker Hub 不可达时可先走镜像源。
- 运行参数**一个都不能少**：`--network none`（断网）、CPU/内存限额、`/data` 只读挂载。
- LLM 生成的代码**只允许**在沙箱内执行；任何"直接在本机跑一段生成代码"的实现都不允许合入。
- 沙箱与后端之间的唯一结果通道是 `dahelper` JSON 契约（charts/tables/text）；matplotlib 产物落盘 PNG 到 `runs/{run_id}/out`。
- 自修复循环最多重试 3 次（`MAX_FIX_ATTEMPTS`），错误信息要回传给 LLM（脱敏后）用于修复。
- `sandbox/` 是**独立执行环境**（镜像构建上下文），不要并入 `backend/`；Python 侧的客户端在 `backend/agent/sandbox.py`。

## 数据接入（backend/datasource/service.py）

- 支持文件上传（CSV / Excel / Parquet）与数据库连接（MySQL / PostgreSQL / SQLite，SQLAlchemy）。
- **数据源归属工作区**：`data_sources.workspace_id` 标识归属；列表按工作区过滤，`analysis/runtime.py::_prepare` 在进入沙箱前再过滤一次——非本工作区且非全局（`workspace_id IS NULL`）的数据源即使拿到 id 也不能进沙箱。写入类接口需要 `datasource:write`，只读查询需要 `datasource:read`。
- 连接保存前必须先测试连通；测试探测只发 `max_tokens=1`。
- **数据库数据先物化为 parquet 缓存再进入沙箱**，数据库凭据永远不进沙箱。
- 用户上传文件存 `uploads/`（gitignore）；SQL 查询（explore）在本机 SQLite 上执行，只读、零 token，需要 `sql:execute` 权限（Viewer 默认没有）。
- 洞察扫描按月部分缺数据要自动剔除，避免误报阈值告警。

## 权限与执行的交界

- 「Tool 执行」在沙箱链路上有两道门：`analysis/runtime.py::run_analysis_stream` 入口检查 `analysis:execute`，`agent/graph.py::execute` 节点在调用沙箱前再检查一次（纵深防御）。
- 因此**不要**为了"方便调试"而绕过 `UserContext` 直接调用 `run_in_sandbox` / `run_analysis_stream`；手工调试也应按正常权限上下文构造 `user_context`。
- 权限不决定沙箱参数：无论谁触发，沙箱参数（断网 / 限额 / 只读）都不允许放宽。

## 红线清单

- 不提交 `.env`、任何密钥、真实生产数据。
- 不在日志/异常信息里输出 API Key、数据库密码或 JWT。
- 不放松沙箱网络/资源限制来"修复"某个功能。
- 不把权限判断放到前端或让 LLM 参与：越权请求必须由后端返回 401/403。
