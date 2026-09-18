# 前端规则（React 18 + AntD 5 + Zustand + Vite）

## 结构

- `frontend/src/pages/`：Login / Chat / Workbench / Explore / Agents / Skills / Insights / Dashboards / Datasources / Usage——一个功能页一个文件。
- `frontend/src/stores/chatStore.ts`：SSE 状态机，**所有流式状态管理集中在这里**，页面组件通过 hook 消费，不直接处理 EventSource/fetch 流解析。
- `frontend/src/stores/authStore.ts`：认证态（token / UserContext / 工作区列表），`login / loadMe / switchWorkspace / logout / hasPermission` 都从这里走。
- `frontend/src/stores/appStore.ts`：全局偏好（语言、设置等），持久化到 localStorage。
- `frontend/src/api/`：后端接口封装。`client.ts` 统一注入 `Authorization` 与 `X-Workspace-Id`，并处理 **401 → 清理凭据并跳转 `/login`**；`sse.ts` 复用同一套请求头（SSE 用 fetch 流式读取，不走 EventSource）。
- `frontend/src/i18n.ts`：中英文案（导航 / 工作台 / 设置已覆盖）。

## 认证与权限

- 未登录时 `App.tsx` 的守卫把任意路径重定向到 `/login`；新增页面必须注册在 `AppLayout` 那组受保护路由内，不要挂到守卫之外。
- 切换工作区 = 调 `POST /api/auth/switch-workspace` 重新取 UserContext（角色与权限随工作区变化），随后刷新页面重载数据。
- 前端可用 `useAuthStore(s => s.hasPermission('datasource:write'))` 控制按钮显隐 / 菜单可用性，**但这只是体验优化**：真正的权限由后端判定，隐藏按钮不构成安全措施——不要因为"前端已经藏了"就在后端省掉权限门。

## 惯例

- TypeScript 严格一些：接口返回先定义类型再用；不要 `any` 满天飞。
- 组件用 AntD 5；图表用 ECharts（自服务分析全部本地渲染，零 token——不要把图表渲染改成后端出图）。
- 主题遵循"绎紫"设计系统：主色 `#5645D4`、深海军蓝 `#0A1530`（见 `theme.ts` / `global.css`）；新增 UI 不要引入体系外的主色。
- 新增用户可见文案必须同时提供 zh / en 两条，进 `i18n.ts`。
- SSE 事件类型与后端 `backend/analysis/runtime.py` 输出对齐，新增事件类型时前后端同一次提交里一起改。
- 认证相关文案注意不要泄露实现细节（例如不要把 token 打进控制台日志）。

## 待办（P2）

页面数量已经上来了（Login / Chat / Workbench / Explore / Agents / Skills / Insights / Dashboards / Datasources / Usage）。后续建议引入 `frontend/src/features/`（analysis / skills / insights / dashboards / datasources / agents），把一个功能相关的组件、store、api 封装收敛到同一目录，`components/` 只保留跨功能复用件。

**已记录的缺口**：`Login.tsx` 与工作区选择器部分文案当前为中文硬编码，尚未接入 `i18n.ts`（登录发生在语言偏好加载之前）；新增文案请不要再扩大这个缺口。

## 启动与验证

```bash
cd frontend && npm run dev    # :5173，代理到 :8000，需后端已启动
npm run build                 # 交付前确认构建通过
```
