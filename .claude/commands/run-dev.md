# 本地启动开发环境

按顺序启动 Helix BI 本地开发环境并做冒烟验证。

## 步骤

1. 确认 `.env` 存在且已填 `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `MODEL_NAME`（缺失则提示用户补齐，不要读取 .env 内容）。
2. 确认 Docker 可用（`docker info`）；沙箱镜像 `helix-sandbox:latest` 不存在则构建：
   ```bash
   docker build -t helix-sandbox:latest sandbox/
   ```
3. 启动后端：
   ```bash
   python -m uvicorn backend.main:app --port 8000
   ```
4. 前端开发模式（另开终端）：
   ```bash
   cd frontend && npm run dev
   ```
5. 冒烟验证：访问 http://127.0.0.1:8000 确认页面加载、示例数据源存在。

## 注意

- 沙箱不可用时分析 / Skill 重放不可用，属预期，不要为此修改沙箱安全参数。
- 内置示例为生成的演示数据（零售约 6 个月、制造约 3 个月），"最近 30/90 天"类问题开箱可用。
