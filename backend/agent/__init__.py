"""Agent 内核：LangGraph 分析链路 + 沙箱执行客户端。

| 文件 | 职责 |
| --- | --- |
| `graph.py` | LangGraph：parse_intent → generate_code → execute(自修复) → summarize → suggest_followups |
| `prompts.py` | 各节点的 system / user 提示词模板 |
| `profiler.py` | 数据画像（注入 prompt 的紧凑 schema 摘要） |
| `sandbox.py` | Docker 沙箱客户端（无网络 / 资源限额 / 数据只读） |

保持线性链路：扩展能力优先在 `backend/analysis/`、`backend/skills/` 等领域层实现，
只通过 `AgentState.skill_block` 这类最小钩子接入。
"""
