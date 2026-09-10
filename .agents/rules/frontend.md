# 前端规则（React 18 + AntD 5 + Zustand + Vite）

## 结构

- `frontend/src/pages/`：Chat / Workbench / Explore / Agents / Skills / Insights / Dashboards / Datasources / Usage——一个功能页一个文件。
- `frontend/src/stores/chatStore.ts`：SSE 状态机，**所有流式状态管理集中在这里**，页面组件通过 hook 消费，不直接处理 EventSource/fetch 流解析。
- `frontend/src/stores/appStore.ts`：全局偏好（语言、设置等），持久化到 localStorage。
- `frontend/src/api/`：后端接口封装；`frontend/src/i18n.ts`：中英文案（导航 / 工作台 / 设置已覆盖）。

## 惯例

- TypeScript 严格一些：接口返回先定义类型再用；不要 `any` 满天飞。
- 组件用 AntD 5；图表用 ECharts（自服务分析全部本地渲染，零 token——不要把图表渲染改成后端出图）。
- 主题遵循"绎紫"设计系统：主色 `#5645D4`、深海军蓝 `#0A1530`（见 `theme.ts` / `global.css`）；新增 UI 不要引入体系外的主色。
- 新增用户可见文案必须同时提供 zh / en 两条，进 `i18n.ts`。
- SSE 事件类型与后端 `analysis_runner` 输出对齐，新增事件类型时前后端同一次提交里一起改。

## 启动与验证

```bash
cd frontend && npm run dev    # :5173，代理到 :8000，需后端已启动
npm run build                 # 交付前确认构建通过
```
