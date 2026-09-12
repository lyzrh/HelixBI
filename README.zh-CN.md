<div align="center">

<img src="docs/logo.svg" alt="绎数 Helix BI" width="240"/>

# 绎数 · Helix BI

**面向制造业与零售业的对话式 AgentBI**

自然语言提问 → Agent 生成分析代码 → Docker 沙箱安全执行 → 图表 / 表格 / 结论，
分析资产自动沉淀为 Skill、洞察与仪表板，**越用越聪明**。

[快速开始](#快速开始) · [核心特性](#核心特性) · [架构](#架构) · [评估](#evaluation-评估) · [技术栈](#技术栈) · [Roadmap](#roadmap)

**简体中文** · [English](README.md)

</div>

---

## 这是什么？

绎数（Helix BI）是一个开源的企业级数据分析 Agent 工作台。它把「对话式分析」和「传统 BI 资产沉淀」放在同一条链路上：

```
连接数据（文件 / 数据库）→ 对话提问（可选口径确认）
  → SSE 流式分析（Skill 命中秒级重放 / 未命中 LLM 生成 + 失败自修复）
  → 结论 + 图表 + 表格 + 追问
  → 沉淀为 Skill / 固定到仪表板
  → 定时洞察扫描 → LLM 经营诊断 → 仪表板 → 导出报告
```

内置**零售销售**与**生产制造**两个行业语义包（指标口径、派生公式、同义词、时间口径），也随时可以接自己的数据。

## 界面预览

| 对话分析（Agent 流式执行） | 工作台 |
|:---:|:---:|
| ![对话分析](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/chat.png) | ![工作台](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/workbench.png) |

| 自助分析（拖拽零 token 出图） | 主动洞察（定时扫描 + 概览） |
|:---:|:---:|
| ![自助分析](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/explore.png) | ![主动洞察](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/insights.png) |

| 数据源管理（上传 / 数据库 / SQL 查询） |
|:---:|
| ![数据源](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/datasources.png) |

## 核心特性

| 模块 | 能力 |
| --- | --- |
| **对话分析** | 自然语言提问，SSE 流式展示步骤进度 / 代码 / 终端 / 图表 / 表格 / 结论；「先确认查询」模式可人工编辑 QuerySpec 再执行；失败自动修复重试（最多 3 次）；推荐追问一键续问 |
| **自助分析** | 点击 / 拖拽字段即时出图（ECharts 交互渲染，本地计算零 token）；柱 / 线 / 饼 / 面 / 散点 / 堆叠等图表类型随时切换；聚合方式与排序可调 |
| **场景 Agent** | 预置零售销售 / 生产制造行业专家：绑定语义包与数据源、开场白、推荐问题、启停管理，开箱即聊 |
| **Skill 库** | 验证过的分析路径自动沉淀为可复用 Skill；相似问题直接重放代码（秒回）；列结构变化时作为 few-shot 参考重新生成；使用 / 成功率统计 |
| **主动洞察** | 定时扫描全部数据源：指标突变 / 连续趋势 / 异常值 / 头部份额变化 / 阈值越界（自动剔除残月避免误报）；新告警 LLM 自动诊断（现象 → 证据 → 原因 → 建议）；概览统计卡 + 状态流转 |
| **仪表板** | 对话中的图表 / 表格 / 结论、洞察诊断一键固定；网格布局浏览；导出自包含 HTML 报告 |
| **数据源** | CSV / Excel / Parquet 上传；MySQL / PostgreSQL / SQLite 连接（先测试后保存）；DB 表物化为 parquet 缓存后进沙箱；数据预览 + **只读 SQL 查询**（本地 sqlite 执行，零 token） |
| **语义层** | 行业语义包定义指标（含派生公式：良率、达成率、客单价等）、维度、同义词、时间口径、图表建议，注入生成 prompt 保证口径一致 |
| **设置中心** | LLM 接口热更新（DeepSeek / 智谱 / 通义 / OpenAI 兼容接口，保存即生效）；偏好设置（回答风格 / 创意度 / 追问开关 / 自定义指令）；**界面语言中英切换**（导航 / 工作台 / 设置中心即时生效，本地持久化）；个人资料 |
| **可靠性** | 沙箱无网络 + CPU / 内存限制 + 数据只读；`dahelper` JSON 契约回传；SQLite 元数据库 WAL 模式 |
| **评估体系** | 65 条固定问题集（零售 / 制造 / 口语化对抗样例）+ 分阶段指标报告（语义解析 / 口径注入 / Skill 匹配 / 重放准入）；指标作为 CI 门禁进 pytest |
| **可观测性** | 每轮分析落 `Run.trace`：分阶段耗时、LLM 调用与 token、Skill 命中模式、六项结果验收；前端「运行时间线」面板可展开查看——Skill 重放的 `LLM calls == 0` 是数据不是文案 |

## 为什么是 HelixBI？

| 路线 | 痛点 |
| --- | --- |
| **传统 BI** | 建模、口径、看板都靠人工预置，问一个没被建模的问题就答不了 |
| **LLM 直接分析** | 灵活但不可信：口径自由发挥、代码不可控执行、错一次就重头再来 |

HelixBI 的答案是给 Agent 加四层约束，让「灵活」与「可靠」同时成立：

```
语义层（semantic_packs）   口径先对齐，LLM 不即兴发挥
   ↓
QuerySpec 确认             算什么先讲清楚，再写代码
   ↓
Docker 沙箱执行            无网络 + 资源限制 + 数据只读
   ↓
结果验收 + Skill 沉淀      可信结论沉淀为 Skill，下次秒级重放、零 token
```

## Evaluation（评估）

`python -m backend.evaluation` 输出分阶段评估报告（离线可跑，不需 Docker / LLM；
需要沙箱的阶段在资源缺失时如实标注「未采集」而不是编造数字）。

当前真实指标（65 条固定问题：零售 25 + 制造 25 + 口语化对抗样例 15）：

```
Evaluation Report — HelixBI Agent Pipeline
语义解析准确率（严格）        90.8%   (59/65)
语义解析准确率（宽松）        93.8%   (61/65)
  指标集合命中               93.8%
  维度集合命中               100.0%
  同比/环比判定              100.0%
  指标级 P / R               100.0% / 94.0%   F1 96.9%
  维度级 P / R               100.0% / 100.0%  F1 100.0%
  解析耗时 p50 / p95         0.015 / 0.029 ms（确定性解析，零 token）
口径注入完整率               96.6%   (113/117 条口径)
  已解析口径渲染保真度        100.0%  (113/113)
  派生指标公式注入           100.0%  (19/19)
Skill 匹配 Top1 准确率       72.3%   （65 次查询 / 33 条沉淀路径）
Skill 匹配 Recall@2          86.2%
重放准入判定正确率           100.0%  (65/65)
代码执行 / 自修复 / 端到端    需 Docker 沙箱，资源缺失时如实标注「未采集」
```

评估不是摆设，它直接驱动过两次真实修复：
口语化对抗样例（60%）暴露了语义包同义词缺口（如「地区」）与排名词缺口（如「最长」），
修复后标准表述达 100%；Skill 匹配引入语义解析骨架后 Top1 从 64.6% 提升到 72.3%、
Recall@2 从 76.9% 提升到 86.2%。指标阈值同时作为 pytest 门禁（`tests/evaluation/`）。

## 可观测性

每轮分析（含 Skill 重放）都落一条 `Run.trace`：

- **分阶段耗时**：意图解析 → 语义解析 → Skill 匹配 → 代码生成 → 沙箱执行 → 总结
- **LLM 用量**：调用次数（按节点分布）、输入 / 输出 token、成本——Skill 重放轮为 0
- **结果验收**：执行成功 / 有产物 / 结论非空 / 图表文件真实存在 / 表格结构完整 / stderr 干净，六项检查与 `ok` 状态解耦
- **失败留痕**：失败的运行同样写 trace，排查问题时那一轮才是最需要看的

前端历史消息新增「运行时间线」面板，展开即可看到上述全部内容。

## 架构

后端按**业务领域**组织（而不是按技术分层堆砌）：`backend/` 下每个目录代表一项能力，
`routers/` 只是薄薄的 API 层，业务实现都在领域目录里。

```
┌──────────────── React 18 + AntD 5 前端（Vite） ────────────────┐
│ 工作台 / 对话分析 / 自助分析 / 场景Agent / Skill库 /            │
│ 主动洞察 / 仪表板 / 数据源 / 设置中心                            │
└───────────────────────────┬───────────────────────────────────┘
                    SSE 流式（spec/code/step/answer/chart…）
┌───────────────────────────▼───────────────────────────────────┐
│                     FastAPI 后端（:8000）                       │
│  routers/    API 层：analysis(SSE) sessions datasources agents  │
│              skills insights dashboards explore(SQL) settings  │
│              usage misc                                        │
│  agent/      Agent 内核：graph（LangGraph 图谱）+ sandbox 客户端 │
│  analysis/   Analysis Runtime（驱动 + 落库）+ 自助分析（零 token）│
│  skills/     Skill 沉淀 / 匹配 / 重放 / few-shot                │
│  insights/   规则扫描 + 定时调度 + LLM 诊断                      │
│  datasource/ 文件 + DB 连接 + parquet 物化                       │
│  semantic/   语义包运行时（读取 semantic_packs/）                │
│  report/     自包含 HTML 报告导出                                │
│  SQLite 元数据库（WAL）：会话/消息/运行/数据源/Agent/Skill/       │
│                         洞察/仪表板/系统设置/Token用量            │
└───────────────────────────┬───────────────────────────────────┘
                            ▼
             LangGraph 内核（parse_intent → generate_code
               → execute → 自修复循环 → summarize → followup）
                            ▼
         Docker 沙箱：--network none、CPU/内存限制、/data 只读
         文件型数据源直接挂载；数据库数据先物化为 parquet 再进沙箱
```

口径与配置的边界：`semantic_packs/` 是**配置**（业务语义唯一来源），
`backend/semantic/` 是**运行时**（加载 + 渲染成 prompt）。

## 快速开始

### 前置要求

- Python 3.11+
- Node.js 18+（仅开发前端需要；生产模式用后端托管的构建产物）
- Docker（Windows/macOS 装 Desktop，Linux 用 Docker Engine）——沙箱执行需要；离线时分析/Skill 重放不可用，其余页面正常

### 1. 后端

```bash
git clone https://github.com/lyzrh/HelixBI.git
cd HelixBI

python -m venv .venv
```

安装依赖并创建 `.env`：

```bash
# Windows（CMD / PowerShell）
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env

# Linux / macOS
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，填入 `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `MODEL_NAME`。

`.env` 支持任何 OpenAI 兼容接口（DeepSeek / 智谱 GLM / 通义千问 / 本地 vLLM…），也可以启动后在页面右上角「设置中心」里在线配置（保存即生效，无需重启）：

```ini
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=sk-xxx
MODEL_NAME=deepseek-chat
```

### 2. 沙箱镜像

```bash
# Docker 运行中；Hub 不可直连时可先从镜像源拉基础镜像
docker pull docker.m.daocloud.io/library/python:3.11-slim
docker tag docker.m.daocloud.io/library/python:3.11-slim python:3.11-slim
docker build -t helix-sandbox:latest sandbox/
```

### 3. 启动

```bash
# Windows（CMD / PowerShell）
.venv\Scripts\python -m uvicorn backend.main:app --port 8000

# Linux / macOS（venv 已激活）
python -m uvicorn backend.main:app --port 8000
```

打开 <http://127.0.0.1:8000> 即可使用（内置示例数据源 / 场景 Agent / Skill）。

内置示例数据为演示用生成数据：零售销售约 6 个月、生产制造约 3 个月，均截止到近期，
开箱即可直接问「近30天 / 近90天」类问题。

前端开发模式：

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173（已配代理到 8000）
```

生产部署只需 `npm run build`，产物由后端静态托管。

## 目录结构

```
backend/                # FastAPI 服务（按业务领域组织）
  main.py               # 入口（CORS / 静态托管 / lifespan）
  config.py             # 全局配置单一入口（路径 / LLM / 沙箱 / 数据接入）
  db.py                 # SQLite 引擎（WAL）
  models.py schemas.py  # ORM（元数据表）与 API 模型
  seed.py               # 内置数据源 / Agent / Skill / 仪表板
  routers/              # API 层：analysis sessions datasources agents skills
                        #   insights dashboards explore settings usage misc
  agent/                # Agent 内核：graph prompts profiler sandbox
  analysis/             # Analysis Runtime（runtime）+ 自助分析（explore）
  skills/               # Skill 沉淀 / 匹配 / 重放
  insights/             # 规则扫描（engine）+ 定时调度（scheduler）
  datasource/           # 文件 / DB 接入 + parquet 物化
  semantic/             # 语义包运行时（registry 加载 + render 渲染 + resolver 确定性解析）
  evaluation/           # 评估流水线：数据集 / 指标 / 运行器（python -m backend.evaluation）
  report/               # 报告导出（builder 单轮 / exporter 仪表板）
semantic_packs/         # 行业语义包（retail_sales / manufacturing_production yaml）
sandbox/                # 独立执行环境：沙箱镜像（pandas/pyarrow/matplotlib/
                        #   中文字体 + dahelper 结果契约）
frontend/               # React + AntD + Zustand + Vite
  src/pages/            # Chat Workbench Explore Agents Skills Insights
                        # Dashboards Datasources Usage
  src/stores/           # chatStore（SSE 状态机） appStore
tests/                  # pytest 套件（含 evaluation/ 指标门禁）
examples/               # 示例数据（零售 / 生产 CSV，演示用生成数据）
screenshots/            # README 截图
AGENTS.md               # AI 编程助手通用规则（AGENTS 标准）
.agents/                # Agent 协作文档：rules/（按需规则）+ plans/（设计决策）
.claude/                # Claude Code 配置：settings + 斜杠命令 + 子代理
uploads/ data/ runs/    # 运行期目录（gitignore）
```

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | React 18 · Ant Design 5 · Zustand · ECharts · Vite |
| 后端 | FastAPI · SQLAlchemy · SSE |
| Agent 内核 | LangGraph · LangChain（OpenAI 兼容接口） |
| 执行 | Docker 沙箱（无网络、资源限制、数据只读） · pandas / pyarrow / matplotlib |
| 元数据 | SQLite（WAL 模式） |
| 数据接入 | CSV / Excel / Parquet 文件 · MySQL / PostgreSQL / SQLAlchemy（物化为 parquet） |

## 设计思路

- **口径先行**：行业语义包 + QuerySpec 确认，先对齐「算什么」再写代码，避免 LLM 自由发挥导致口径漂移
- **品牌视觉**：绎紫设计系统（主色 `#5645D4` + 深海军蓝 `#0A1530`），双螺旋品牌标识——紫链路代表业务数据、青链路代表分析智能，节点象征沉淀的分析资产
- **沙箱兜底**：生成的代码永远在无网络 Docker 容器里跑，数据只读，结果通过 `dahelper` JSON 契约回传
- **资产沉淀**：一次成功的分析变成 Skill（重放秒回）、一次有价值的发现固定到仪表板——系统随使用变强，而不是每次从零开始
- **主动而非被动**：定时洞察扫描让系统在用户提问之前就把异常送到眼前
- **Token 可控**：Skill 重放不调 LLM；自助分析与 SQL 查询全部本地计算；追问推荐可关闭；测试连接只发 max_tokens=1 的探针请求

灵感来自 FineBI NEXT 的产品形态，以及 DB-GPT / PandasAI / Vanna / OpenCodeInterpreter 等开源项目在代码解释执行、自修复、追问推荐上的实践。

## Roadmap

- **R2**：仪表板图表前端化（ECharts 交互渲染替代 PNG）、洞察订阅推送、多用户与权限、其余页面文案国际化（当前中英切换已覆盖导航 / 工作台 / 设置中心）
- **R3**：~~评估集回归~~（✅ 已落地：`backend/evaluation/` + `tests/evaluation/` 门禁；下一步扩充问题集并采集沙箱执行 / 自修复指标）、语义包可视化编辑器、指标血缘
- **架构演进（P2）**：`backend/routers/` → `api/`、`config/db/models/schemas` 收敛到 `core/`；前端引入 `features/` 分域（详见 [.agents/rules/architecture.md](.agents/rules/architecture.md)）

## License

[MIT](LICENSE) © 2026 Helix BI Contributors
