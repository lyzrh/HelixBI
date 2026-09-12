"""语义层运行时：解释 `semantic_packs/` 下的行业语义包。

分层约定（Runtime 与配置分离）：

    semantic_packs/     配置——业务语义的唯一来源（指标 / 维度 / 口径 / 同义词）
    backend/semantic/   运行时——读取语义包并渲染为 prompt 注入块

| 模块 | 职责 |
| --- | --- |
| `registry.py` | 加载 / 列举语义包，文件 → 语义包的注册与推断 |
| `render.py` | 语义包与 QuerySpec → prompt 块 |
| `resolver.py` | 确定性语义解析：问题 → 指标 / 维度 / 时间 / 对比意图（零 token、可离线评测） |
"""

from backend.semantic.registry import (
    DEFAULT_PACK,
    INFER_RULES,
    SEMANTIC_PACKS_DIR,
    assign_pack,
    infer_pack,
    list_packs,
    load_pack,
    pack_for_file,
    pack_name,
)
from backend.semantic.render import render_semantic_prompt, render_spec_prompt
from backend.semantic.resolver import resolve, resolve_for_packs

__all__ = [
    "DEFAULT_PACK",
    "INFER_RULES",
    "SEMANTIC_PACKS_DIR",
    "assign_pack",
    "infer_pack",
    "list_packs",
    "load_pack",
    "pack_for_file",
    "pack_name",
    "render_semantic_prompt",
    "render_spec_prompt",
    "resolve",
    "resolve_for_packs",
]
