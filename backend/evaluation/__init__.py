"""评估领域：把 Agent 链路的「质量」变成可回归、可度量的数字。

| 模块 | 职责 |
| --- | --- |
| `datasets.py` | 固定问题集与人工标注口径的加载（`datasets/*.json`） |
| `metrics.py` | 集合级 PRF / 比率 / 延迟聚合，以及报告渲染 |
| `runner.py` | 分阶段评估：离线阶段必跑，Docker / LLM 阶段按需且缺失时不编造 |
| `samples.py` | 端到端阶段使用的示例数据映射 |

用法：

    python -m backend.evaluation                 # 离线阶段（CI 可跑）
    python -m backend.evaluation --with-pipeline # 追加真实端到端（需 Docker + LLM）
"""

from backend.evaluation.metrics import render_report
from backend.evaluation.runner import run_all

__all__ = ["run_all", "render_report"]
