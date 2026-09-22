<div align="center">

<img src="docs/logo.svg" alt="绎数 Helix BI" width="240"/>

# 绎数 · Helix BI

**面向制造业与零售业的对话式 AgentBI**

自然语言提问 → Agent 生成分析代码 → Docker 沙箱安全执行 → 图表 / 表格 / 结论，
分析资产自动沉淀为 Skill、洞察与仪表板，**越用越聪明**。

[快速开始](#快速开始) · [核心特性](#核心特性) · [认证与权限](#认证与权限rbac) · [架构](#架构) · [API](#api-概览) · [评估](#evaluation-评估) · [技术栈](#技术栈) · [Roadmap](#roadmap)

**简体中文** · [English](README.md)

</div>

---

## 这是什么？

绎数（Helix BI）是一个开源的企业级数据分析 Agent 工作台。它把「对话式分析」和「传统 BI 资产沉淀」放在同一条链路上：

```
登录 → 选择工作区（角色 / 权限 / 数据范围随之确定）
  → 连接数据（文件 / 数据库，归属工作区）
  → 对话提问（可选口径确认）
  → SSE 流式分析（成本感知路由：语义解析 → Skill 检索 → 重放准入 → 要不要调 LLM）
      · Skill 高置信重放：秒级、**0 次 LLM 调用**
      · 确定性意图快路径：解析器能确定就**跳过 LLM 解析**（覆盖率 81.5%，与人工标注 100% 一致）
      · 其余走完整 Agent：LLM 生成 + 失败自修复 + 运行预算护栏（超预算提前结束并如实说明）
  → 结论 + 图表 + 表格 + 追问
  → 沉淀为 Skill / 固定到仪表板
  → 定时洞察扫描 → LLM 经营诊断 → 仪表板 → 导出报告
```

内置**零售销售**与**生产制造**两个行业语义包（指标口径、派生公式、同义词、时间口径），也随时可以接自己的数据。

## 界面预览

| 登录（Premium SaaS 分栏） | 工作台（品牌渐变 Hero） |
|:---:|:---:|
| ![登录](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/login.png) | ![工作台](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/workbench.png) |

| 对话分析（Agent 流式执行） | 自助分析（拖拽零 token 出图） |
|:---:|:---:|
| ![对话分析](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/chat.png) | ![自助分析](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/explore.png) |

| 主动洞察（定时扫描 + 概览） | 工作区成员管理（RBAC） |
|:---:|:---:|
| ![主动洞察](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/insights.png) | ![成员管理](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/members.png) |

| 数据源管理（上传 / 数据库 / SQL 查询） |
|:---:|
| ![数据源](https://cdn.jsdelivr.net/gh/lyzrh/HelixBI@main/screenshots/datasources.png) |

## 核心特性

| 模块 | 能力 |
| --- | --- |
| **认证与权限** | 用户名 / 邮箱 + 口令登录（JWT）+ 自助注册（不授予角色）；用户 → 工作区成员 → 角色 → 权限四层解析出 UserContext，「同一个人在不同工作区可以是不同角色」；11 个权限点覆盖数据源 / SQL / 分析 / 仪表板 / Skill / 成员管理；admin 可视化添加 / 改角色 / 移除成员（末位管理员保护）；权限一律后端判定，绕过前端直调同样被拒；**Token 生命周期**：短期 access + 可撤销 refresh（轮换 / 重放检测 / 登出 / 管理员强制下线，多进程安全）；安全事件审计（登录、刷新、吊销、越权、产物访问被拒） |
| **对话分析** | 自然语言提问，SSE 流式展示步骤进度 / 代码 / 终端 / 图表 / 表格 / 结论；「先确认查询」模式可人工编辑 QuerySpec 再执行；**失败自修复 V2**：错误分类（语法 / 列名 / 类型 / 空结果 / 超时 / OOM / 回传契约 / 未知 / 环境）→ 按类别注入定向修复提示 → 有界重试（全局 3 次 + 每类额度 + 复读提前终止）→ 兜底给出结构化失败；推荐追问一键续问 |
| **自助分析** | 点击 / 拖拽字段即时出图（ECharts 交互渲染，本地计算零 token）；柱 / 线 / 饼 / 面 / 散点 / 堆叠等图表类型随时切换；聚合方式与排序可调 |
| **场景 Agent** | 预置零售销售 / 生产制造行业专家：绑定语义包与数据源、开场白、推荐问题、启停管理，开箱即聊 |
| **Skill 库** | 验证过的分析路径自动沉淀为可复用 Skill；**检索 V2**：8 路可解释信号打分 + 重放准入（指标 / 维度 / 分析类型 / 排序方向 / TopN / 时间窗 / 数据源指纹任一不符就不重放），命中即秒回、**零 LLM 调用**，不命中或重放失败自动回到完整 Agent；三级作用域 global / workspace / user 防串数据；使用 / 成功率统计 |
| **主动洞察** | 定时扫描全部数据源：指标突变 / 连续趋势 / 异常值 / 头部份额变化 / 阈值越界（自动剔除残月避免误报）；新告警 LLM 自动诊断（现象 → 证据 → 原因 → 建议）；概览统计卡 + 状态流转 |
| **仪表板** | 对话中的图表 / 表格 / 结论、洞察诊断一键固定；网格布局浏览；导出自包含 HTML 报告 |
| **数据源** | CSV / Excel / Parquet 上传；MySQL / PostgreSQL / SQLite 连接（先测试后保存）；**数据源归属工作区**，跨工作区不可见也不可进沙箱；DB 表物化为 parquet 缓存后进沙箱；数据预览 + **只读 SQL 查询**（本地 sqlite 执行，零 token） |
| **语义层** | 行业语义包定义指标（含派生公式：良率、达成率、客单价等）、维度、同义词、时间口径、图表建议，注入生成 prompt 保证口径一致 |
| **设置中心** | LLM 接口热更新（DeepSeek / 智谱 / 通义 / OpenAI 兼容接口，保存即生效）；偏好设置（回答风格 / 创意度 / 追问开关 / 自定义指令）；**界面语言中英切换**（导航 / 工作台 / 设置中心即时生效，本地持久化）；个人资料 |
| **可靠性** | 沙箱无网络 + CPU / 内存限制 + 数据只读；`dahelper` JSON 契约回传；SQLite 元数据库 WAL 模式；认证与权限在 API 层、运行时、工具执行前三次收口 |
| **安全边界** | 产物 / 上传 / 缓存全部走**鉴权下发**（无匿名静态目录，含路径遍历防护与工作区归属反查）；数据源口令**应用级加密**（密钥只来自环境变量，缺密钥拒绝保存）；导出链路按工作区过滤；完整清单见 [docs/security.md](docs/security.md) |
| **评估体系** | 65 条固定问题集（零售 / 制造 / 口语化对抗样例）+ **20 条对抗准入样例**（同指标不同维度 / 排序方向相反 / 时间窗变化 / 数据源变化 / 相似 Skill 竞争…）+ **12 条自修复失败场景** + **成本/延迟基准**（改造前 vs 优化后：调用次数 / prompt token / 确定性耗时 / 预算行为）+ 分阶段指标报告（语义解析 / 口径注入 / Skill 检索 / 重放准入 / 自修复 / 成本与延迟 / 效率）；基线可复现（策略与开关冻结留档），指标作为 CI 门禁进 pytest 与 CI |
| **运行时（Production Runtime V1）** | **Warm Sandbox Pool**：预热容器池（安全限制与冷启动一致），`docker exec` 复用、执行完清理工作目录归还、用满强制回收重建、异常容器销毁补充、池满排队/超时自动降级冷启动；**并发控制**：全局 + 单用户上限 + 等待队列（超限明确拒绝）；**超时/取消**：排队/容器/执行/**整轮**四级超时 + 客户端断连取消（Self-Repair 也突破不了总时限）；`Run.trace.runtime` 回答「为什么慢：排队 / 等容器 / 执行」与「容器是复用还是新建」 |
| **运行时（Production Runtime V1）** | **Warm Sandbox Pool**：预热容器池（安全限制与冷启动一致），`docker exec` 复用、执行完清理工作目录归还、用满强制回收重建、异常容器销毁补充、池满排队/超时自动降级冷启动；**并发控制**：全局 + 单用户上限 + 等待队列（超限明确拒绝）；**超时/取消**：排队/容器/执行/**整轮**四级超时 + 客户端断连取消（Self-Repair 也突破不了总时限）；`Run.trace.runtime` 回答「为什么慢：排队 / 等容器 / 执行」与「容器是复用还是新建」 |
| **成本与延迟** | 成本感知路由：确定性意图快路径（命中即跳过 LLM 解析）+ Skill 重放（0 调用）+ 确定性追问 + 上下文装配器（按命中口径收窄语义层、few-shot 限流、去重）+ 运行预算（调用 / token / 成本上限，超限提前结束并说明原因）+ 确定性缓存（按文件指纹 / Skill 版本失效）；`Run.trace.cost_control` / `performance` 逐块记账，**「为什么调了 2 次 LLM」「Token 花在哪」「哪个阶段最慢」都能从数据回答** |
| **可观测性** | 每轮分析落 `Run.trace`：分阶段耗时、LLM 调用与 token、**成本控制档案（调用归因 / 预算使用率 / 终止原因 / 意图与追问来源）**、**性能档案（阶段耗时 / 瓶颈 / 缓存命中 / prompt 分块记账）**、**Skill 检索档案（候选 8 路分数 / 选中项 / 准入结论 / 拒绝与 fallback 原因）**、**自修复档案（首次成功 / 修复次数 / 每轮错误类别与策略 / 每轮耗时 / 最终 success\|exhausted\|fallback）**、六项结果验收、**触发者用户 / 工作区 / 角色**；前端「运行时间线」面板可展开查看——Skill 重放的 `LLM calls == 0` 是数据不是文案 |

## 安全（Security）

六层边界，每层都有测试与审计：

```
① 认证        pbkdf2 口令 + 短期 access JWT（内含 typ=access，业务接口只认这一种）
② 授权        UserContext（user → membership → role → permission），14 个权限点，端点级权限门
③ 工作区隔离   数据源 / 会话 / 运行 / Skill / 仪表板 / 洞察全部按工作区过滤，越权 404
④ 产物保护     取消匿名静态目录，全部鉴权下发；路径遍历防护 + 归属反查（含导出链路）
⑤ 秘密保护     数据源口令应用级加密（HELIX_SECRET_KEY），启动自动迁移历史明文
⑥ 令牌生命周期  可撤销 refresh：轮换、重放检测（整族吊销）、登出、管理员强制下线
⑦ 审计        安全事件入库并脱敏（口令 / 令牌 / 密钥永不落库）
```

- **Token 只放身份**：权限每次请求由后端实时解析，改权限立即生效、无需重新登录；
  refresh token 是不透明随机串且只存哈希，**无法当 access 使用**。
- **状态码约定**：`401` 未认证 / 令牌无效过期 / 不属于所请求的工作区；`403` 已认证但缺权限点；
  `404` 资源不存在**或不属于你的工作区**（不暴露存在性）。
- **"登录即可"的敏感接口已收口**：LLM 设置（写需 `settings:write`，读分级返回）、
  用量与成本（`usage:read` + 按工作区过滤）、权限清单（`member:manage`）、
  分析与产物（`analysis:read`）、数据源读写（`datasource:read/write`）。
- 密钥全部只从环境变量读：`HELIX_JWT_SECRET`、`HELIX_SECRET_KEY`（见 `.env.example`）；
  **未配置加密密钥时拒绝保存数据库口令并给出提示，任何环境都不会静默降级为明文**。

安全设计（Problem → 风险 → 设计 → 实现 → 测试 → 结果 → 剩余限制）见
[docs/security.md](docs/security.md)。

## 认证与权限（RBAC）

登录只负责**认证**；用户能看到什么、能做什么，全部由后端按可信数据解析：

```
Login（用户名 / 邮箱 + 口令）
  ↓ 认证：签发 JWT（token 内只有身份，不含权限）
user_id
  ↓ User Resolver
当前 Workspace（X-Workspace-Id，缺省取用户第一个工作区）
  ↓ Role / Permission / Data Scope
UserContext { user_id, username, workspace_id, role, permissions, data_scope }
  ↓
LangGraph Agent → LLM → 权限检查 → Tools（沙箱执行）→ DataSource → 结果
```

- **角色不可自选**：角色来自 `用户 → 工作区成员 → 角色 → 权限`，同一个人可以在销售工作区是分析师、在营销工作区只是查看者。
- **自助注册**：登录页「注册」入口创建账号（用户名 / 邮箱 / 口令，pbkdf2 哈希存储）；注册只建立认证身份，**不授予任何工作区与角色**——即使请求体传入角色字段也会被忽略。注册用户由管理员在「成员管理」页加入工作区并分配角色后才能登录使用。
- **成员管理**：admin 可在工作区成员页添加（按用户名）/ 改角色 / 移除成员；末位管理员不能被降级或移除（防止工作区锁死）；成员管理只能作用于自己所在的工作区。
- **资源隔离全覆盖**：数据源 / 会话 / 运行记录 / 仪表板 / 洞察 / Skill 按工作区过滤，跨工作区读取统一 404、写操作 403；运行产物（图表）/ 上传文件 / 物化缓存不再匿名下载，改由带登录态与工作区校验的文件下发接口提供。
- **权限点全接线**：系统级配置（LLM、场景 Agent、洞察调度）写操作一律 `workspace:manage`；洞察按「读 datasource:read / 生成与诊断 analysis:execute / 流转 datasource:write」细分。
- **权限点**：`datasource:read/write`、`sql:execute`、`analysis:create/execute`、`dashboard:read/write`、`skill:read/write`、`workspace:manage`、`member:manage`。
- **判断单一入口**：后端一律走 `permission_checker.has_permission(user_context, "datasource:write")`，不硬编码角色；LLM 不参与授权决定。
- **绕过前端无效**：前端隐藏按钮只是体验优化，Viewer 直接调 API 一律 403。
- **数据隔离**：数据源 / Skill / 会话 / 仪表板都带工作区归属，跨工作区访问被拒；Skill 另有 global / workspace / user 三级作用域；历史全局仪表板（`workspace_id=NULL`）对所有工作区可见以保持兼容。
- **默认账号**：内置管理员 `admin / admin123`（**首次部署后请立即改密并新建账号**）。
- **本地联调**：`python -m backend.devseed` 一键创建 `analyst1` / `viewer1` 测试账号（口令 `Dev-12345678`；幂等、仅手动执行时创建，`HELIX_ENV=production` 下拒绝运行）。

内置角色与权限集合：

| 角色 | 权限集合 |
| --- | --- |
| `admin` 管理员 | 全部 11 项 |
| `analyst` 分析师 | 数据源读写、SQL 执行、分析创建 / 执行、仪表板读写、Skill 读写 |
| `viewer` 查看者 | 数据源查看、仪表板查看、Skill 查看 |

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

`python -m backend.evaluation --runtime-benchmark` 输出 Cold Sandbox vs Warm Pool
对照（启动延迟 / 排队 / 端到端 p50-p95 / 吞吐 / 复用率 / 泄漏检查；离线仿真）。
`python -m backend.evaluation --runtime-benchmark` 输出 Cold Sandbox vs Warm Pool
对照（启动延迟 / 排队 / 端到端 p50-p95 / 吞吐 / 复用率 / 泄漏检查；离线仿真）。
`python -m backend.evaluation --cost-benchmark` 输出成本/延迟对照表（改造前 vs 优化后：
调用次数 / prompt token / 确定性耗时 / 预算行为；离线可跑，**不是实测**）。
`python -m backend.evaluation` 输出分阶段评估报告（离线可跑，不需 Docker / LLM；
需要沙箱的阶段在资源缺失时如实标注「未采集」而不是编造数字）。

当前真实指标（65 条固定问题：零售 25 + 制造 25 + 口语化对抗样例 15；另有 20 条
自带候选池的**对抗准入样例**，见下）：

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
Skill 检索 Top1 准确率       78.5%   （65 次查询 / 33 条沉淀路径；基线 72.3%）
Skill 检索 Recall@3          90.8%   Recall@2 89.2%
重放准入判定正确率           100.0%  (65/65)  结构守卫
Skill Retrieval V2（完整路径签名口径：包 / 指标 / 维度 / 类型 / 排序 / 时间窗）
  检索 Top1                  100.0%  (基线 95.4%)
  重放 Precision / Recall    100.0% / 96.9%
  False Replay Rate          0.0%    (基线 4.6%，3 条 → 0 条)
  准入判定正确率（对抗集）     100.0%  (20 条；基线 50.0%，误放行 10 → 0)
  LLM 调用 / 查询            0.09    (计入误重放后 0.09；基线 0.14)
Self-Repair V2（离线策略仿真：桩化 LLM/沙箱，驱动真实图谱；非实测）
  首次执行成功率              8.3%    (1/12)         基线 8.3%
  修复成功率                 80.0%   (8/10 条需修复) 基线 18.2%
  总成功率                   75.0%                    基线 25.0%
  平均修复次数                1.17                     基线 2.67
  复读检测率 / 修复耗尽率      8.3% / 16.7%             基线 0.0% / 75.0%
  LLM 调用 / Token / 查询     4.17 / 2594              基线 5.67 / 4096
  Skill 重放不进自修复         2/2 断言通过              重放成功 = 0 修复 / 0 LLM
Cost & Latency V1（离线：桩化 LLM/沙箱，调用次数与 prompt token 为实测；measured=false）
  LLM 调用 / Agent 查询       2.19                     基线 4.00（-45.4%）
  Token / Agent 查询          1925                     基线 3346（-42.5%）
  生成 prompt 上下文 p50/p95   795 / 1003               基线 926 / 1220
  意图快路径命中率             81.5%                    覆盖率 81.5% / 严格准确率 100.0%
  追问零 LLM 率               100.0%                   基线 0.0%
  Skill 重放零 LLM 校验        6/6 断言通过               重放 = 0 调用 / 0 token / 不进自修复
Runtime V1：Cold vs Warm Pool（离线仿真：池/并发调度真实代码，容器耗时建模）
  单并发端到端 p50             101 ms     冷启动 1600 ms   吞吐 15.9x
  20 并发端到端 p50            454 ms     冷启动 4000 ms   吞吐 7.9x
  容器复用 / 回收 / 异常替换    61 次      回收 1 / 异常替换 0   泄漏检查 通过
  └ 真实 Docker 实测           未采集     需 Docker 守护进程（诚实边界）
Runtime V1：Cold vs Warm Pool（离线仿真：池/并发调度真实代码，容器耗时建模）
  单并发端到端 p50             101 ms     冷启动 1600 ms   吞吐 15.9x
  20 并发端到端 p50            454 ms     冷启动 4000 ms   吞吐 7.9x
  容器复用 / 回收 / 异常替换    61 次      回收 1 / 异常替换 0   泄漏检查 通过
  └ 真实 Docker 实测           未采集     需 Docker 守护进程（诚实边界）
代码执行 / 自修复 / 端到端    需 Docker 沙箱，资源缺失时如实标注「未采集」
```

评估不是摆设，它直接驱动过三次真实修复：
口语化对抗样例（60%）暴露了语义包同义词缺口（如「地区」）与排名词缺口（如「最长」），
修复后标准表述达 100%；Skill 匹配引入语义解析骨架后 Top1 从 64.6% 提升到 72.3%；
Skill Retrieval V2 在此基础上把 Top1 提到 78.5%，并用**重放准入**把错误重放从 4.6% 降到 0。
自修复 V2 把修复成功率从 18.2% 提到 80.0%、总成功率从 25.0% 提到 75.0%，同时把 LLM 调用
降 17.8%、Token 降 34.0%（离线策略仿真口径；真实执行指标在 Docker 可用前标注「未采集」）。
指标阈值同时作为 pytest 门禁（`tests/evaluation/`），并进 CI（见下）。

### Skill Retrieval V2 / Replay Admission

改造前 Skill 在分析链路里只是 few-shot 素材，真正的重放只存在于「手动运行 Skill」；
重放守卫也只看列结构与读取函数，**指标 / 维度 / 排序方向 / 时间窗完全不同也会重放**，
返回「看起来对、口径错」的结果。V2 把它做成一个可评估、可解释、安全的检索 + 路由系统：

```
Query → Semantic Resolution → Candidate Retrieval → Top-K 打分（8 路可解释信号）
      → Replay Admission（blocker 硬约束 + High/Medium/Low 分层）→ Replay（0 次 LLM）或完整 Agent
```

- **宁可放弃重放，也不能错误重放**：指标 / 维度 / 分析类型 / 排序方向 / TopN / 时间窗 /
  数据源指纹 / 列结构 / 读取函数任一不满足 → 一律走 Agent，并在 trace 里写明是哪一条；
- **不新增 LLM 调用**：所有信号来自确定性语义解析与 Skill metadata，权重集中配置在
  `backend/config.py`，并用 `--tune-weights --sensitivity` 在评测集上标定与验证；
- **安全回退**：重放执行失败会自动回到完整 Agent，用户拿到的是结果而不是报错；
- **可回答「为什么」**：`Run.trace.skill.retrieval` 记录候选、8 路分数、准入结论、拒绝原因
  与 fallback 原因（`replay_failed` / `admission_declined:*` / `datasource_changed:*` …）。

对标 Baseline（V1 冻结策略）的完整对比表、权重标定过程与遗留问题：
[`docs/skill-retrieval-v2.md`](docs/skill-retrieval-v2.md)；一键复现
`python -m backend.evaluation --benchmark`。

### Cost & Latency Optimization V1（成本感知路由 → 预算护栏 → 上下文瘦身）

改造前每轮分析固定付 4 次 LLM 调用（意图解析 / 生成代码 / 整理结论 / 推荐追问），
prompt 里塞着整包语义层、两例 few-shot 完整代码与重复的文件清单。本轮在**不改口径、
不绕过安全检查**的前提下把它压到 2.19 次 / 1925 token：

```
语义解析（确定性够用就 0 调用）→ Skill 检索 → 重放准入
   ├─ 高置信重放 ────────────────────────────→ 0 次 LLM
   ├─ 意图解析可确定 ────────────────────────→ 生成 1 次 + 结论 1 次 = 2 次
   └─ 解析器理解不了（如取值过滤「华东」） ────→ 老实回落 LLM，不猜
```

- **意图快路径**（`backend/agent/intent.py`）：命中指标 + 置信度达标 + **问题内容全部有出处**
  三条同时满足才跳过 LLM；只要存在"解释不了的内容"（如「华东」这类**取值过滤**，
  确定性解析器只能识别「区域」这个维度）立刻回落——把"筛选华东"猜成"按区域分组"
  比多花一次调用糟得多。65 条人工标注上：覆盖率 81.5%，命中处严格准确率 **100%**；
- **参数化上下文装配器**（`backend/agent/context.py`）：语义层只注入本次 QuerySpec 命中的口径、
  few-shot 只注入最相关的一例、修复轮去掉重复的文件清单、历史 2 轮 × 300 字、
  结论轮结果表按行收敛（**真实数字不改写**）；首轮 prompt 1704 → 1145 token（−32.8%）；
- **LLM 运行预算**（`backend/agent/budget.py`）：调用次数 / 输入输出 token / 成本上限，
  **预检在调用之前**（超限的请求根本不发出）；预算耗尽时结论由沙箱真实执行结果拼出，
  不编造数字；修复额度只允许收紧，绝不放大 `MAX_FIX_ATTEMPTS`；
- **确定性缓存**（`backend/analysis/cache.py`）：数据画像（按文件指纹 + 工作区）、语义渲染、
  确定性解析、Skill 意图；**不缓存任何身份判定**（权限 / 可见性 / 准入），
  登记表与测试一起守住这条约定；
- **消除重复检索**：few-shot 候选复用路由阶段已算好的候选，不再重复召回与打分。

工程故事（Problem → Baseline → 瓶颈 → 优化 → Benchmark → Result → Trade-offs）、
逐块 token 构成、trace 示例与剩余限制：[`docs/cost-latency-v1.md`](docs/cost-latency-v1.md)；
一键复现 `python -m backend.evaluation --cost-benchmark`。

### Agent Self-Repair V2（错误分类 → 定向修复 → 有界重试 → 兜底）

改造前的自修复只有一条路径：失败 → 把同一段提示（`FIX_USER_TMPL`）连同 stderr 塞回 LLM →
重新生成 → 再执行，最多 `MAX_FIX_ATTEMPTS` 次。三个缺口：**提示词与病因无关**（列名错、
dtype 错、超时、空结果拿到的是一样的劝告）、**不识别复读**（同一个 traceback 还会再烧一次
token）、**不可解释**（只有 `attempts=3`，说不出为什么）。

V2 在 `execute` 之后插入一个确定性（零 token）的 `classify` 节点：

```
execute → classify（错误分类 + 结果验收门）
            ├─ 通过验收 ──────────────────────────→ summarize
            ├─ 可修复且有额度 → 定向修复提示 ──────→ generate_code（回到顶部）
            └─ 复读 / 额度用尽 / 不可修复 ─────────→ summarize（结构化失败，不抛异常）
```

- **错误分类**：语法 / 名称 / 列不存在 / 类型取值 / 结果为空 / 超时 / 资源超限 / 回传契约 /
  未知 / 环境不可用，优先用结构化事实（`exit_code` / `timed_out` / `failure_kind`）而不是猜文案；
- **定向修复**：每类错误一份针对性处方（列名 → 对齐真实 schema 与语义层字段；类型 → 修正
  dtype 与转换链路；空结果 → 核对过滤条件 / 时间范围 / 字段取值；语法 → 只修代码结构；
  超时 / OOM → 降计算量与内存），并携带「前几轮错在哪」的历史，**仍是同一次 LLM 调用**；
- **有界重试**：错误指纹（`类别|归一化正文`）相同即判定复读并立即停止；连续同类错误视为
  无进展提前终止；每类错误另有独立额度；全局上限仍是 `MAX_FIX_ATTEMPTS`（最多执行 4 次）；
- **验收门统一**：`execution_ok + has_artifact` 由 `backend/agent/acceptance.py` 单点实现，
  Self-Repair 与 `validate_final` 共用同一把尺子；顺带修正「空结果表算成功」的老问题。

工程故事（Problem → Baseline → Optimization → Benchmark → Result）、错误分类表、重试与停止
策略、trace 示例与遗留问题：[`docs/self-repair-v2.md`](docs/self-repair-v2.md)；一键复现
`python -m backend.evaluation --repair-benchmark`（有 Docker + LLM 时加 `--repair-live`
采集真实指标）。

## 可观测性

每轮分析（含 Skill 重放）都落一条 `Run.trace`：

- **分阶段耗时**：意图解析 → 语义解析 → Skill 匹配 → 代码生成 → 沙箱执行 → 总结
- **LLM 用量**：调用次数（按节点分布）、输入 / 输出 token、成本——Skill 重放轮为 0
- **Skill 检索档案**：候选 Skill 及各自 8 路分数、选中项、准入结论与拒绝原因、
  重放还是 fallback 到 Agent——「为什么没重放」「为什么选中它」都能从 trace 直接回答
- **自修复档案**：`self_repair` 段记录首次是否成功、修复几次、每轮的错误类别 / 指纹 / 采用的
  策略 / 耗时，以及最终 `success | exhausted | fallback`——「为什么这个 Agent 修了 2 次才成功」
  「为什么第 2 次就放弃」都能直接从 trace 回答；Skill 重放轮标注 `not_applicable`
- **结果验收**：执行成功 / 有产物 / 结论非空 / 图表文件真实存在 / 表格结构完整 / stderr 干净，六项检查与 `ok` 状态解耦
- **触发者**：本轮的用户 / 工作区 / 角色，便于按人按工作区审计
- **失败留痕**：失败的运行同样写 trace，排查问题时那一轮才是最需要看的

前端历史消息新增「运行时间线」面板，展开即可看到上述全部内容。

## CI 门禁

`.github/workflows/ci.yml` 除 `pytest -q` 外还跑离线评估，并用独立步骤断言：

- Replay Precision 不低于 Baseline；
- False Replay 不恶化，且必须为 **0**；
- 准入判定正确率 ≥ 90%、对抗集**零误放行**。

阈值只收紧不放宽，评估数据集没有为了过门禁被改动。


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
│  routers/    API 层：auth analysis(SSE) sessions datasources    │
│              agents skills insights dashboards explore(SQL)    │
│              settings usage misc（含权限门与登录门）             │
│  auth/       认证与 RBAC：UserContext 解析 + 权限判断单一入口     │
│  agent/      Agent 内核：graph（LangGraph 图谱）+ repair（错误   │
│              分类/定向修复/有界重试）+ acceptance（验收门）        │
│              + sandbox 客户端                                     │
│  analysis/   Analysis Runtime（驱动 + 落库）+ 自助分析（零 token）│
│  skills/     Skill 沉淀 / 匹配 / 重放 / few-shot                │
│  insights/   规则扫描 + 定时调度 + LLM 诊断                      │
│  datasource/ 文件 + DB 连接 + parquet 物化                       │
│  semantic/   语义包运行时（读取 semantic_packs/）                │
│  report/     自包含 HTML 报告导出                                │
│  SQLite 元数据库（WAL）：用户/工作区/成员/角色/权限、会话/消息/    │
│        运行/数据源/Agent/Skill/洞察/仪表板/系统设置/Token 用量    │
└───────────────────────────┬───────────────────────────────────┘
                            ▼
             LangGraph 内核（parse_intent → generate_code → execute
               → classify（分类+验收门）→ 定向修复循环 → summarize → followup）
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

编辑 `.env`，填入 `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `MODEL_NAME`；
**生产部署请一并配置** `HELIX_JWT_SECRET`（JWT 签名密钥）与 `HELIX_SECRET_KEY`
（数据源口令加密主密钥，未配置时保存数据库连接会被拒绝，避免明文落库）：

```bash
python -c "import secrets;print('HELIX_JWT_SECRET=' + secrets.token_urlsafe(48))"
python -c "import secrets;print('HELIX_SECRET_KEY=' + secrets.token_urlsafe(48))"
```

`.env` 支持任何 OpenAI 兼容接口（DeepSeek / 智谱 GLM / 通义千问 / 本地 vLLM…），也可以启动后在页面右上角「设置中心」里在线配置（保存即生效，无需重启）：

```ini
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=sk-xxx
MODEL_NAME=deepseek-chat

# 认证签名密钥（未设置时进程内随机生成，仅适合本机单进程开发；
# 多进程 / 生产部署必须显式配置，否则重启后所有登录态失效）
HELIX_JWT_SECRET=请替换为随机长字符串

# 数据源口令加密主密钥（保存数据库连接口令必需；未配置时接口会拒绝保存而不是写明文）
HELIX_SECRET_KEY=请替换为另一个随机长字符串

# 运行环境：development | production
HELIX_ENV=development
# access token 有效期（秒）/ refresh token 有效期（秒，可撤销）
HELIX_ACCESS_TOKEN_TTL=3600
HELIX_REFRESH_TOKEN_TTL=2592000
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

打开 <http://127.0.0.1:8000>，用内置管理员 `admin / admin123` 登录（**请立即修改密码并新建账号**），
随后即可使用内置示例数据源 / 场景 Agent / Skill。

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
  config.py             # 全局配置单一入口（路径 / LLM / 沙箱 / 数据接入 / 运行期目录）
  db.py                 # SQLite 引擎（WAL）+ 启动迁移（新列 / RBAC 表）
  models.py schemas.py  # ORM（元数据表 + 用户/工作区/角色/权限）与 API 模型
  seed.py               # RBAC 基础数据 + 内置数据源 / Agent / Skill / 仪表板
  routers/              # API 层：auth analysis sessions datasources agents skills
                        #   insights dashboards explore settings usage misc
  auth/                 # 认证与 RBAC：security（口令 + access JWT） tokens（refresh
                        #   轮换/重放/吊销） audit（安全事件审计+脱敏） context（UserContext
                        #   + permission_checker） deps（登录门 / 权限门）
  agent/                # Agent 内核：graph prompts profiler sandbox
                        #   + repair（错误分类 / 定向修复 / 有界重试）
                        #   + acceptance（结果验收硬门槛，validation 与 Self-Repair 共用）
                        #   + sandbox_pool（warm 容器池：复用/回收/排队/降级）
                        #   + runerrors（取消 / 总时限控制流）
                        #   + sandbox_pool（warm 容器池：复用/回收/排队/降级）
                        #   + runerrors（取消 / 总时限控制流）
                        #   + intent（确定性意图快路径）followups（确定性追问）
                        #   + context（prompt 上下文装配与分块记账）budget（运行预算）
                        #   + tokens（token 计数）
  analysis/             # Analysis Runtime（runtime）+ 自助分析（explore）
                        #   + cache（确定性缓存登记与命中统计）
                        #   + concurrency（全局/单用户并发上限 + 等待队列）
                        #   + concurrency（全局/单用户并发上限 + 等待队列）
  skills/               # Skill 沉淀 / 检索（retrieval：打分+准入）/ 重放（含作用域隔离）
  insights/             # 规则扫描（engine）+ 定时调度（scheduler）
  datasource/           # 文件 / DB 接入 + parquet 物化 + secrets（凭据加密 + 迁移）
  semantic/             # 语义包运行时（registry 加载 + render 渲染 + resolver 确定性解析）
  evaluation/           # 评估流水线：数据集 / 指标 / 运行器 / 检索基准 / 效率模型
                        #   + 自修复基准（repair_cases 场景 + repair_bench 离线策略仿真）
                        #   + 成本/延迟基准（cost_bench：改造前 vs 优化后）
                        #   （python -m backend.evaluation
                        #     [--benchmark|--repair-benchmark|--cost-benchmark|--tune-weights]）
  report/               # 报告导出（builder 单轮 / exporter 仪表板）
semantic_packs/         # 行业语义包（retail_sales / manufacturing_production yaml）
sandbox/                # 独立执行环境：沙箱镜像（pandas/pyarrow/matplotlib/
                        #   中文字体 + dahelper 结果契约）
frontend/               # React + AntD + Zustand + Vite
  src/pages/            # Login Chat Workbench Explore Agents Skills Insights
                        # Dashboards Datasources Usage
  src/stores/           # chatStore（SSE 状态机） authStore（认证态） appStore
  src/api/              # client（注入 token / 401 跳转） sse（流式读取）
tests/                  # pytest 套件（evaluation/ 指标门禁 + test_auth_rbac.py 权限用例）
docs/                   # api.md（接口清单） + security.md（安全设计与剩余限制）
                        #   + cost-latency-v1.md（成本/延迟工程故事与基准）
                        #   + production-runtime-v1.md（运行时工程故事与基准）
                        #   + production-runtime-v1.md（运行时工程故事与基准）
                        #   + skill-retrieval-v2.md
                        #   + self-repair-v2.md（自修复工程故事与基准）+ README 图片
examples/               # 示例数据（零售 / 生产 CSV，演示用生成数据）
screenshots/            # README 截图
AGENTS.md               # AI 编程助手通用规则（AGENTS 标准）
.agents/                # Agent 协作文档：rules/（按需规则）+ plans/（设计决策）
.claude/                # Claude Code 配置：settings + 斜杠命令 + 子代理
uploads/ data/ runs/    # 运行期目录（gitignore，启动时自动创建）
```

## API 概览

完整接口清单（含每个端点所需权限、状态码约定、SSE 事件协议）见 **[docs/api.md](docs/api.md)**；
服务启动后也可访问 `/docs` 查看交互式文档。

```bash
# 登录（用户名或邮箱 + 口令）
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
# → { "token": "eyJ...", "user": {...}, "workspaces": [...] }

# 带 token 调用业务接口（X-Workspace-Id 指定工作区，缺省用第一个）
curl http://127.0.0.1:8000/api/datasources \
  -H "Authorization: Bearer $TOKEN" -H "X-Workspace-Id: 1"
```

| 分组 | 端点 | 权限 |
| --- | --- | --- |
| 认证 / 工作区 | `/api/auth/register`（公开，仅建账号不授权）`/login`（公开）`/me` `/switch-workspace` `/roles` `/permissions` `/users` `/workspaces/{id}/members`（GET/POST/PATCH/DELETE） | 登录；用户与成员管理需 `workspace:manage` / `member:manage` |
| 对话与分析 | `/api/sessions...`、`/api/sessions/{id}/analyze`、`/api/runs/{id}/rerun`、`/api/runs/{id}/export` | `analysis:create` / `analysis:execute`（分析类为 **SSE**） |
| 数据源与自助 | `/api/datasources...`、`/api/explore/schema` `/profile` `/query` `/sql` | `datasource:read` / `datasource:write` / `sql:execute` / `analysis:execute` |
| Skill / 仪表板 / 洞察 | `/api/skills...`、`/api/dashboards...`、`/api/insights...` | `skill:read/write`、`dashboard:read/write`；洞察为登录 |
| Agent / 设置 / 用量 / 工具 | `/api/agents...`、`/api/settings...`、`/api/usage...`、`/api/utils/export_table` | 登录 |
| 公开 | `/api/health`、`/api/semantic/packs`、`/api/stats` | 无需登录 |

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | React 18 · Ant Design 5 · Zustand · ECharts · Vite |
| 后端 | FastAPI · SQLAlchemy · SSE |
| 认证授权 | JWT（HS256，stdlib 实现）· pbkdf2-sha256 口令哈希 · 自建 RBAC（角色 / 权限 / 工作区） |
| Agent 内核 | LangGraph · LangChain（OpenAI 兼容接口） |
| 执行 | Docker 沙箱（无网络、资源限制、数据只读） · pandas / pyarrow / matplotlib |
| 元数据 | SQLite（WAL 模式） |
| 数据接入 | CSV / Excel / Parquet 文件 · MySQL / PostgreSQL / SQLAlchemy（物化为 parquet） |

## 设计思路

- **口径先行**：行业语义包 + QuerySpec 确认，先对齐「算什么」再写代码，避免 LLM 自由发挥导致口径漂移
- **权限先行**：认证只解决「你是谁」，授权交给后端解析的 UserContext；权限判断单一入口、三层收口（API → 运行时 → 工具），前端按钮显隐只是体验
- **品牌视觉**：绎紫设计系统（主色 `#5645D4` + 深海军蓝 `#0A1530`），双螺旋品牌标识——紫链路代表业务数据、青链路代表分析智能，节点象征沉淀的分析资产
- **沙箱兜底**：生成的代码永远在无网络 Docker 容器里跑，数据只读，结果通过 `dahelper` JSON 契约回传
- **资产沉淀**：一次成功的分析变成 Skill（重放秒回）、一次有价值的发现固定到仪表板——系统随使用变强，而不是每次从零开始
- **主动而非被动**：定时洞察扫描让系统在用户提问之前就把异常送到眼前
- **Token 可控**：Skill 重放不调 LLM；自助分析与 SQL 查询全部本地计算；追问推荐可关闭；测试连接只发 max_tokens=1 的探针请求

灵感来自 FineBI NEXT 的产品形态，以及 DB-GPT / PandasAI / Vanna / OpenCodeInterpreter 等开源项目在代码解释执行、自修复、追问推荐上的实践。

## Roadmap

- **R2**：仪表板图表前端化（ECharts 交互渲染替代 PNG）、洞察订阅推送、其余页面文案国际化（当前中英切换已覆盖导航 / 工作台 / 设置中心，登录页与工作区选择器仍为中文）；~~多用户与权限~~（✅ 已落地：登录 / 工作区 / UserContext / RBAC，见「认证与权限」）
- **R3**：~~评估集回归~~（✅ 已落地：`backend/evaluation/` + `tests/evaluation/` 门禁；下一步扩充问题集并采集沙箱执行指标）、~~成本与延迟优化~~（✅ 已落地：Cost & Latency V1，见 [docs/cost-latency-v1.md](docs/cost-latency-v1.md)；沙箱容器预热池与并发/超时控制已随 Production Runtime V1 落地，见 [docs/production-runtime-v1.md](docs/production-runtime-v1.md)；下一步做流式结论）、语义包可视化编辑器、指标血缘
- **安全加固（P1）**：~~静态产物目录鉴权~~、~~数据库密码加密存储~~、~~刷新令牌与注销~~、~~按权限点细分设置 / 用量 / 分析类接口~~（✅ 已落地：Security Hardening V1，见 [docs/security.md](docs/security.md)；剩余限制也写在该文档里）
- **架构演进（P2）**：`backend/routers/` → `api/`、`config/db/models/schemas` 收敛到 `core/`；前端引入 `features/` 分域（详见 [.agents/rules/architecture.md](.agents/rules/architecture.md)）

## License

[MIT](LICENSE) © 2026 Helix BI Contributors
