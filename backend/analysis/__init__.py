"""分析领域：Analysis Runtime 的正式落点。

| 文件 | 职责 |
| --- | --- |
| `runtime.py` | 对话式分析运行器：驱动 `backend/agent/graph.py`，映射 SSE 事件并落库 |
| `explore.py` | 自助分析引擎：结构化查询（聚合 / 筛选 / 只读 SQL），本地 pandas 计算、零 token |
"""
