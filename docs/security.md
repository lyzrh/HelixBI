# Security Hardening V1：从「RBAC + 工作区隔离」到完整安全边界

> 目标不是"看起来更安全"，而是**把已经存在的暴露点补上**，并且每一步都能被测试与审计验证。
> 核心原则：**权限只由后端决定，密钥只在后端出现，越权一律不暴露存在性。**

- 范围：`backend/auth/`（Token 生命周期 + 审计）、`backend/routers/`（权限门与产物下发）、
  `backend/datasource/`（凭据加密）、`backend/analysis|report/`（导出链路）、`tests/`、`docs/`。
- 约束：不引入 Multi-Agent / MCP / RAG；**不重写 RBAC**；不新增"看起来安全但没用"的组件；
  Token 里只放身份，权限永远实时解析。
- 复现命令：
  ```bash
  pytest -q                                                  # 含安全用例（tests/test_security_*.py）
  python -m backend.evaluation                               # 评估不回归（65 条问题集 + 检索 + 自修复）
  python scripts/live_api_test.py                            # 黑盒冒烟（含 14 条安全用例）
  ```

---

## 1. Problem：这一轮要解决的暴露点

Roadmap P1 里挂着四条"已知风险"，本轮逐条收口；审计过程中又新发现四条真实缺口：

| # | 风险 | 性质 |
| --- | --- | --- |
| 1 | `/runs`、`/uploads`、`/data` 静态目录匿名可访问（**连 SQLite 元数据库都能下载**） | P1 已知 |
| 2 | 数据源数据库口令**明文入库**（`data/app.db` 一被拷走即泄露全部外部库口令） | P1 已知 |
| 3 | 只有 12 小时长效 JWT，**没有 refresh、没有撤销、没有登出** | P1 已知 |
| 4 | 一批接口"登录即可"：LLM 设置、用量与成本、场景 Agent、权限清单 | P1 已知 |
| 5 | **产物路由与真实产物 URL 对不上**：路由按 `Run.id`(int) 取目录，而链路生成的是
`/runs/<运行目录名>/out/x.png` → 图表/报告实际取不到文件（鉴权改造引入的回归） | 审计发现 |
| 6 | 孤儿运行（会话已删）被当作"全局可读"，任何登录用户都能读别人的历史产物 | 审计发现 |
| 7 | 仪表板导出把客户端提交的 `chart_url` 直接读盘 → **任意文件读取**（`../` 可读到元数据库、uploads） | 审计发现 |
| 8 | `/api/explore/*` 只校验"数据源存在"，**不校验工作区** → 猜 id 就能读别的租户数据（含只读 SQL） | 审计发现 |
| 9 | Skill 按 id 读取/修改/删除没有作用域校验；`from-run` 能把别人的运行沉淀成自己的资产 | 审计发现 |
| 10 | 个人偏好/资料是**全局 KV**：一个人改、所有人受影响，`custom_instructions` 还会被注入到**别人**的分析 prompt 里 | 审计发现 |

---

## 2. Security Design

六层边界，每一层都能独立测试：

```
① Authentication      口令 pbkdf2 + 短期 access JWT（typ=access）+ 可撤销 refresh
        │
② Authorization       UserContext（user → membership → role → permission），
        │             require_permission 挂到端点；11 → 14 个权限点
        │
③ Workspace Isolation 数据源 / 会话 / 运行 / Skill / 仪表板 / 洞察 全部按工作区过滤；
        │             跨工作区返回 404（不暴露存在性）
        │
④ Artifact Protection 取消静态目录，全部走鉴权路由；路径遍历防护 + 产物归属反查
        │
⑤ Secret Protection  数据源口令应用级加密（密钥只来自环境变量）+ 启动迁移
        │
⑥ Token Lifecycle    refresh 只存哈希、轮换 + 重放检测（整族吊销）、登出/强制下线
        │
⑦ Audit              安全事件入库（脱敏：口令/令牌/密钥永不落库）
```

### 状态码约定（全项目统一）

| 码 | 含义 |
| --- | --- |
| 400 | 请求参数不合法 |
| 401 | 未认证 / 令牌无效或过期 / **请求的工作区不在你的成员关系内**（UserContext 建不起来） |
| 403 | 已认证，但对该操作缺少权限点（或 Skill 不属于当前工作区这类显式拒绝） |
| 404 | 资源不存在，**或资源不属于你的工作区**（产物 / 运行 / 数据源 / 仪表板一律用 404） |

---

## 3. Implementation

### 3.1 Artifact 访问（④）

- `backend/routers/files.py` 三条路由全部在登录门之上再挂权限点：
  `/runs/**` → `analysis:read`；`/uploads/**`、`/data/materialized/**` → `datasource:read`；
- **归属反查**：URL 里的 key 既支持运行目录名（链路真实生成的形状）也支持 `Run.id`，
  但归属一律由 `Run → Session.workspace_id` 决定 —— 拿到别人的目录名也没用；
- **孤儿运行**（会话已删 / `session_id` 为空）不再视为"全局可读"，直接 404；
- **路径遍历**：`_safe_join` 做 resolve + commonpath 校验；上传文件只认服务登记过的
  `file_name` 基线名；`/data` 只暴露 `materialized/` 子目录；
- **导出链路**：`chart_url_to_path` 拒绝越界路径（返回 None），仪表板导出额外按
  "当前工作区可见的产物目录 + 可见运行 id"过滤条目；`source_run_id` 在写入时校验归属；
- 被拒访问写 `artifact.access_denied` 审计（含遍历、跨工作区、未登记路径三种原因）；
- 图片 / PDF 以 `inline` 下发，前端 `AuthImg` 仍用带鉴权的 fetch + blob 渲染，图表能力不受影响。

顺带修好了第 5 条（真实回归）：产物 URL 与鉴权路由现在共用 `runtime.run_artifact_key()`
生成/解析 key，图表与报告导出重新可用。

### 3.2 数据源凭据加密（⑤）

`backend/datasource/secrets.py`：

- 密钥：只读 `HELIX_SECRET_KEY`（环境变量，不进库不进 Git）；**未配置就拒绝保存**并给出
  生成密钥的命令提示（生产环境同样不静默降级成明文）；
- 存储：`enc:v1:<iv>:<body>:<tag>`，带版本号便于将来换算法；**写入前加密、读取只在后端解密**
  （`datasource/service.py::connection_password` 是唯一解密点），API 只返回 `has_password` 布尔；
- 迁移：启动时 `migrate_plaintext_passwords()` 把历史明文一次性加密（幂等）；
  缺密钥则如实跳过并打日志，不假装迁移过；
- 审计：`datasource.credential_saved` / `datasource.credential_migrated`（只记"是否带口令、
  是否已加密、迁移条数"）。

> **算法选择（诚实说明）**：运行环境装不上 `cryptography`（离线），项目 requirements 里也没有
> 密码学库，所以这里用标准库实现了**标准形状**的构造：HKDF-SHA256 派生 + HMAC-SHA256 计数器
> 模式密钥流 + encrypt-then-MAC 完整性校验（详见模块 docstring）。它保证机密性与完整性，
> 但**属于自研构造**：生产环境建议替换为 AES-GCM / Fernet（格式已带 `v1` 版本号，替换成本很低）。

### 3.3 Token 生命周期（⑥）

| 项 | 策略 | 实现 |
| --- | --- | --- |
| access token | JWT，默认 1h（`HELIX_ACCESS_TOKEN_TTL`），载荷含 `typ="access"` | `auth/security.py` |
| refresh token | 不透明随机串（48B），库里只存 `sha256`，默认 30 天 | `auth/tokens.py` |
| 轮换 | 每次 `/auth/refresh` 吊销旧行、签发新行，`replaced_by` 记录链条 | 同上 |
| 重放 | 拿已轮换的旧 token 再来 → 整族（`family_id`）吊销 + `auth.token_replay` 审计 | 同上 |
| 撤销 | `/auth/logout`（本会话）、`/auth/revoke-all`（本人全部）、`/auth/users/{uid}/revoke`（管理员强制下线） | `routers/auth.py` |
| 多进程 | 撤销状态全在 `refresh_tokens` 表，任意实例同一结论 | 同上 |
| 不能混用 | 业务接口只接受 `typ="access"`；refresh 不是 JWT，`decode_token` 直接失败 | `auth/deps.py` |

前端（`api/client.ts`）在 401 时先用 refresh 换一次新令牌再重试原请求（并发请求共享同一个
刷新 Promise，避免把 refresh 打成重放）；`authStore.logout()` 会先调后端吊销令牌。
**token 里始终只有身份**：权限每次请求由 `UserContext` 实时解析，改权限无需重新登录（有测试证明）。

### 3.4 细粒度权限（②）

新增 3 个权限点（只加"对应一类资源/一类操作"的，不做无意义细分）：

| 权限点 | 含义 | admin | analyst | viewer |
| --- | --- | :---: | :---: | :---: |
| `analysis:read` | 查看分析结果与产物（含下载 / 导出） | ✅ | ✅ | ✅ |
| `usage:read` | 查看用量与成本（平台级经营信息） | ✅ | ❌ | ❌ |
| `settings:write` | 修改平台设置（LLM 接口 / 创意度） | ✅ | ❌ | ❌ |

挂门清单（原先"登录即可"的敏感接口）：

| 接口 | 之前 | 现在 |
| --- | --- | --- |
| `GET/PUT /api/settings/llm`、`POST /api/settings/llm/test` | 登录即可（GET） | GET 分级返回（`base_url` 仅管理员）、PUT/test 需 `settings:write` |
| `GET /api/usage/summary`、`/history` | 登录即可，且**全平台无过滤** | 需 `usage:read`，**按工作区过滤** |
| `GET /api/runs/{id}`、`/runs/recent`、`/runs/{id}/export`、`/api/sessions`、`/sessions/{id}` | 登录即可 | 需 `analysis:read` |
| `GET /api/auth/permissions`、`/auth/roles` | **完全匿名** | 需 `member:manage`（管理员） |
| `PATCH/DELETE /api/skills/{id}`、`GET /api/skills/{id}`、`from-run` | 登录即可（无作用域校验） | 可见性统一走检索层规则；`from-run` 校验运行归属 |
| `/api/explore/*` | `datasource:read` 但**无工作区校验** | 保留权限点 + 补工作区校验（404） |
| 个人资料 / 偏好 | 登录即可且**全局共享** | 按 `user_id` 隔离存储；`创意度` 属平台级 → 需 `settings:write` |

LLM 不参与任何授权判断：所有判定都在 `permission_checker`（读 DB）里完成。

### 3.5 审计（⑦）

`backend/auth/audit.py` + `audit_logs` 表，记录：
`auth.login` / `auth.logout` / `auth.refresh` / `auth.token_replay` / `auth.token_revoked` /
`auth.token_rejected` / `authz.permission_denied` / `authz.workspace_denied` /
`artifact.access_denied` / `datasource.credential_saved` / `datasource.credential_migrated` /
`settings.llm_updated` / `settings.llm_tested`。

- **脱敏**：`redact()` 按 key 名抹掉 password / secret / token / api_key / credential 等，
  超长字符串截断；审计里永远没有口令、令牌、密钥（有测试断言）；
- **独立事务**：被拒请求的业务事务会回滚，所以审计用独立 Session 落库，保证"拒绝"一定留痕；
- **旁路**：审计失败不影响主流程（不会把 401/403 变成 500）；
- 查询入口 `GET /api/auth/audit`（管理员，支持 `event` 前缀过滤与 limit）。

---

## 4. Tests

新增 84 条安全用例（总计 **364 passed / 4 skipped**）：

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_security_tokens.py`（20） | access 过期、refresh 过期/被撤销/未知、轮换、**重放 → 整族吊销**、logout 幂等、revoke-all、管理员强制下线（非管理员 403 / 跨工作区 404）、refresh 不能当 access、`typ=refresh` 的 JWT 被拒、篡改、停用用户、权限变更即时生效、审计不含凭据 |
| `tests/test_security_artifacts.py`（18） | 合法工作区下载、跨工作区 404（目录名与 Run.id 两种 key）、孤儿运行 404、匿名 401、`../` 与 URL 编码变体、未登记上传文件 404、`/data` 全目录不再暴露、导出跳过外区与遍历图表、固定外区运行被拒、拒绝事件进审计 |
| `tests/test_security_secrets.py`（15） | 加解密往返 / 唯一密文 / 篡改与换密钥检测 / 缺密钥拒绝加密、密文落库、API 无明文、缺密钥不写入、迁移加密与幂等、缺密钥迁移跳过、`connection_password` 行为 |
| `tests/test_security_permissions.py`（31） | 匿名 401 全端点、篡改 401、权限清单分级、LLM 设置分级与密钥掩码、用量权限与工作区过滤、viewer 只读边界、去掉 `analysis:read` 立即 403、跨工作区 run/session/datasource/skill 拒绝、伪造工作区头 401、越权与产物拒绝审计、偏好按用户隔离、创意度平台级 |
| 回归调整（2 处） | `test_auth_rbac` / `test_register_members` 里 viewer 的权限集断言加上 `analysis:read`（有意变更） |

另外黑盒冒烟脚本新增 14 条安全用例（`SEC01–SEC14`）。

---

## 5. Result

| 维度 | 结果 |
| --- | --- |
| Artifact | 静态目录全部下线 → 鉴权路由 + 归属反查 + 遍历防护；同时**修复了产物 URL 与路由不匹配导致图表取不到的回归** |
| 数据源凭据 | 明文入库 → 应用级加密（密钥走环境变量）+ 启动迁移 + API 只回 `has_password` |
| Token | 12h 单令牌 → 1h access + 30 天可撤销 refresh，支持轮换/重放检测/登出/强制下线，多进程安全 |
| 权限 | 新增 3 个权限点，8 组敏感接口收紧；`/auth/permissions`、`/auth/roles` 从匿名改为管理员 |
| 审计 | 13 类安全事件入库，严格脱敏，管理员可查 |
| 回归 | 65 条评估问题集、Skill Retrieval V2（False Replay 仍为 0、重放仍 0 LLM）、Self-Repair V2、RBAC 原有测试全部通过（364 passed） |
| 黑盒冒烟 | 51 + 14 = 65 条用例全绿（含限时性能基准） |

---

## 6. Remaining Limitations（诚实清单）

以下**没有**做完，不该被读成"生产级安全"：

1. **凭据加密是自研构造**（HKDF + HMAC-CTR + encrypt-then-MAC，见 3.2）。合法但不如
   成熟库（AES-GCM / Fernet）。建议生产替换，替换点只有 `encrypt_secret` / `decrypt_secret`。
2. **`HELIX_SECRET_KEY` / `HELIX_JWT_SECRET` 未配置时的行为是"开发友好"**：JWT 密钥缺失会
   在进程内随机生成（多进程部署必须显式配置，否则各进程令牌互不认）；加密密钥缺失则拒绝保存
   数据库口令。两者都只打日志告警，**没有强制启动失败**的 production guard。
3. **没有限流 / 防爆破**：登录失败不会锁定账号，也没有 IP 维度限流（审计里有记录，
   可以据此做后续风控，但当前不影响可用性）。
4. **审计表只写不清理**：没有保留期与轮转策略，长期运行需要加归档任务。
5. **SQLite 单文件仍是单点**：元数据库没有加密（only 数据源口令被加密）；
   能读到 `data/app.db` 文件的人依然能看到业务元数据与凭据密文。
6. **`data_scope` 仍是占位**：UserContext 里有字段但没实现行级数据权限（所有工作区成员
   看到的是同一份数据），README 里也如实标注。
7. **HTTP 层面没有强制 HTTPS / HSTS / 安全响应头**：JWT 与 refresh 走明文 HTTP 时可被截获，
   部署时必须由反代提供 TLS（当前代码不做假设）。
8. **前端 token 存在 localStorage**：XSS 可窃取（已用 `AuthImg`/统一 client 收敛注入点，
   但没有做 httpOnly Cookie + CSRF 方案的改造）。
9. **孤儿运行的产物仍然留在磁盘**：会话被删后产物目录不会被清理（只是不可访问），
   需要单独的清理任务。
10. **未做的权限点**：场景 Agent 的读写仍复用 `workspace:manage`（管理员），
    没有拆成 `agent:read/write`——当前角色模型下语义等价，先不引入冗余权限点。
