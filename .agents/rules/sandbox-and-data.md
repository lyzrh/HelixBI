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
- 连接保存前必须先测试连通；测试探测只发 `max_tokens=1`。
- **数据库数据先物化为 parquet 缓存再进入沙箱**，数据库凭据永远不进沙箱。
- 用户上传文件存 `uploads/`（gitignore）；SQL 查询（explore）在本机 SQLite 上执行，只读、零 token。
- 洞察扫描按月部分缺数据要自动剔除，避免误报阈值告警。

## 红线清单

- 不提交 `.env`、任何密钥、真实生产数据。
- 不在日志/异常信息里输出 API Key 或数据库密码。
- 不放松沙箱网络/资源限制来"修复"某个功能。
