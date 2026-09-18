@AGENTS.md

# CLAUDE.md（Claude Code 专属补充）

通用项目规则见上方引入的 AGENTS.md——架构不变式、常用命令、规则索引均以该文件为准，修改约定请编辑 AGENTS.md 与 `.agents/rules/`，不要在本文件重复维护。

Claude Code 专属说明：

- 项目级斜杠命令位于 `.claude/commands/`，子代理定义位于 `.claude/agents/`，权限白名单位于 `.claude/settings.json`。
- 个人本地配置请写入 `CLAUDE.local.md`（已 gitignore，禁止提交）。
- 后端启动后监听 `:8000`；前端开发模式监听 `:5173` 并代理到后端，验证 UI 改动时先确认后端已运行。
- 业务接口都需要登录：先 `POST /api/auth/login`（内置 `admin / admin123`）拿 token，再带 `Authorization: Bearer <token>` 请求；权限模型与接口清单见 `.agents/rules/auth-rbac.md` 与 `docs/api.md`。
- 回答与提交信息使用简体中文。
