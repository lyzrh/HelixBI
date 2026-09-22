"""Prompt 上下文装配器：把"该给 LLM 看什么"变成一处可测、可记账的确定性逻辑。

为什么单独抽一层：

1. **成本可见**——每次 LLM 请求由哪些块组成、每块多少 token，`stats` 里逐块给出，
   `Run.trace.performance.context` 直接落库，能回答"Token 花在哪里"；
2. **有对照基线**——`policy="v1"` 冻结改造前的装配方式（全量语义层 + 两例 few-shot +
   修复块重复注入文件清单 + 历史 3 轮 × 600 字），`policy="v2"` 是优化后的装配，
   两者共用同一份代码，因此基准对比是同一个函数的两组参数，而不是两套实现；
3. **不牺牲正确性**——v2 的裁剪都是"已确定信息的去重/收窄"，不是随手截断：
   - 语义层只保留**本次 QuerySpec 命中的**指标与维度（其余口径未被引用，注入即噪声）；
   - few-shot 示例只注入相关度最高的那条，并对超长代码做**带标注的**截断；
   - 修复轮不再重复注入"可用文件清单"（同一次请求的基础块里已经有了）；
   - 结论轮的表格按行数收敛，但保留真实数字（绝不改写、不编造）。

所有裁剪都可由 `config` 调整或关掉（`CONTEXT_POLICY="v1"` 即完全回到旧行为）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import prompts
from .tokens import count_tokens, method as token_method

POLICY_V1 = "v1"   # 冻结基线：改造前的装配方式
POLICY_V2 = "v2"   # 优化后：去重 + 收窄 + 限额

# v1（基线）常量——改不得：它们是基准对比的"旧口径"
V1_HISTORY_TURNS = 3
V1_HISTORY_ANSWER_CHARS = 600
V1_SKILL_EXAMPLES = 2
V1_REPAIR_OUTPUT_CHARS = 4000
V1_SUMMARIZE_TABLE_ROWS = None


@dataclass
class Block:
    """一个 prompt 块（名字 / 文本 / token 数）。"""

    name: str
    text: str
    tokens: int = 0

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = count_tokens(self.text)

    def to_dict(self) -> dict:
        return {"name": self.name, "tokens": self.tokens, "chars": len(self.text or "")}


@dataclass
class Assembly:
    """装配结果：文本 + 分块记账 + 裁剪痕迹。"""

    blocks: list[Block] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    policy: str = POLICY_V2
    extra: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "".join(b.text for b in self.blocks)

    @property
    def tokens(self) -> int:
        return sum(b.tokens for b in self.blocks)

    def stats(self, **extra) -> dict:
        payload = {
            "policy": self.policy,
            "total_tokens": self.tokens,
            "blocks": [b.to_dict() for b in self.blocks],
            "notes": list(self.notes),
            "token_counter": token_method(),
        }
        payload.update(self.extra)
        payload.update(extra)
        return payload


def policy_from_config() -> str:
    from backend import config

    name = (getattr(config, "CONTEXT_POLICY", POLICY_V2) or POLICY_V2).lower()
    return POLICY_V1 if name == "v1" else POLICY_V2


def resolve_policy(policy: str | None = None) -> str:
    return (policy or policy_from_config()).lower()


def _cfg(name: str, default):
    from backend import config

    value = getattr(config, name, None)
    return default if value is None else value


# ---- 基础块 ----


def history_block(history: list[dict] | None, policy: str) -> Block:
    """对话历史：只保留执行真正需要的轮次（v2：更少的轮次、更短的摘要）。"""
    turns = (V1_HISTORY_TURNS if policy == POLICY_V1
             else int(_cfg("HISTORY_TURNS", 2)))
    answer_chars = (V1_HISTORY_ANSWER_CHARS if policy == POLICY_V1
                    else int(_cfg("HISTORY_ANSWER_CHARS", 300)))
    if not history or turns <= 0:
        return Block("history", "")
    lines = ["## 之前的对话（供参考，新问题可能延续这些结论）"]
    for turn in history[-turns:]:
        answer = (turn.get("answer") or "").strip()
        lines.append(f"- 问：{turn.get('question', '')}\n  答要旨：{answer[:answer_chars]}")
    return Block("history", "\n".join(lines) + "\n")


def narrowed_semantic_block(pack_id: str, focus: list[str]) -> str:
    """按本次命中口径收窄的语义层块（保留口径定义，去掉未被引用的指标/维度）。"""
    from backend.semantic.render import render_semantic_prompt

    return render_semantic_prompt(pack_id, focus=focus)


def semantic_block(state: dict, policy: str) -> Block:
    """语义层块：v1 注入全量；v2 只注入本次 QuerySpec 命中的口径。"""
    full = state.get("semantic_block") or ""
    if policy == POLICY_V1 or not full:
        return Block("semantic", full)

    spec = state.get("spec") or {}
    focus = [n for n in (spec.get("metrics") or []) + (spec.get("dimensions") or []) if n]
    if not focus:
        return Block("semantic", full)          # 没有确定口径时不能收窄，宁可全量
    packs = [p for p in (state.get("semantic_packs") or []) if p]
    if not packs:
        return Block("semantic", full)          # 拿不到包名就无法收窄（保持基线行为）
    narrowed = "\n\n".join(narrowed_semantic_block(p, focus) for p in packs)
    if not narrowed.strip():
        return Block("semantic", full)
    return Block("semantic", narrowed)


def _cap_code(code: str, max_lines: int, keep_full: bool) -> tuple[str, str]:
    lines = (code or "").splitlines()
    if keep_full or max_lines <= 0 or len(lines) <= max_lines:
        return code or "", ""
    head = int(max_lines * 0.75)      # 头部 75%：imports / 读取数据 / 核心计算
    tail = max_lines - head           # 尾部 25%：结果回传（dahelper 调用）
    return ("\n".join(lines[:head] + [f"# …（共 {len(lines)} 行，此处省略 {len(lines) - max_lines} 行）"]
                      + lines[-tail:]), f"代码截断 {len(lines)}→{max_lines} 行")


def skill_block(state: dict, policy: str) -> Block:
    """few-shot 块：v1 注入前两例的完整代码；v2 只注入最相关的一例并限额。"""
    text = state.get("skill_block") or ""
    if policy == POLICY_V1 or not text:
        return Block("skill", text)
    skills = state.get("skill_candidates") or []
    if not skills:
        return Block("skill", text)

    from backend.skills.engine import render_skill_prompt

    limit = max(int(_cfg("SKILL_FEWSHOT_MAX", 1)), 1)
    max_lines = int(_cfg("SKILL_EXAMPLE_MAX_LINES", 60))
    trimmed: list[dict] = []
    for skill in skills[:limit]:
        code, _note = _cap_code(getattr(skill, "code", "") or "", max_lines, keep_full=False)
        trimmed.append({"name": getattr(skill, "name", ""),
                        "question": getattr(skill, "question", ""), "code": code})
    rendered = render_skill_prompt(trimmed, raw=True)
    return Block("skill", rendered or text)


def spec_block(state: dict) -> Block:
    from backend.semantic import render_spec_prompt

    return Block("spec", render_spec_prompt(state.get("spec")))


def data_block(state: dict) -> Block:
    """数据概况 + 可用文件 + 用户问题（三个小块合成一处，便于记账）。"""
    return Block("data", prompts.generate_user_prompt(
        state["question"], state.get("profile", ""), list(state.get("files") or {})))


# ---- 各节点的装配入口 ----


def generation_context(state: dict, policy: str | None = None) -> Assembly:
    """`generate_code` 的 user 消息装配。"""
    pol = resolve_policy(policy or state.get("context_policy"))
    assembly = Assembly(policy=pol)
    assembly.blocks = [
        history_block(state.get("history"), pol),
        semantic_block(state, pol),
        skill_block(state, pol),
        spec_block(state),
        data_block(state),
    ]
    assembly.notes = _skill_notes(state, pol)
    assembly.blocks = [b for b in assembly.blocks if b.text]
    return assembly


def _skill_notes(state: dict, policy: str) -> list[str]:
    skills = state.get("skill_candidates") or []
    if policy == POLICY_V1 or len(skills) <= 1:
        return []
    limit = int(_cfg("SKILL_FEWSHOT_MAX", 1))
    if len(skills) > limit:
        return [f"few-shot 由 {len(skills)} 条收敛到 {limit} 条（最相关优先）"]
    return []


def repair_context(state: dict, policy: str | None = None) -> Assembly:
    """自修复轮的附加消息装配（基础块之上的一大块）。"""
    pol = resolve_policy(policy or state.get("context_policy"))
    execution = state.get("execution") or {}
    cap = (V1_REPAIR_OUTPUT_CHARS if pol == POLICY_V1
           else int(_cfg("REPAIR_OUTPUT_CHARS", 1500)))
    stdout = (execution.get("stdout") or "")[-cap:]
    stderr = (execution.get("stderr") or "")[-cap:]
    hint = state.get("repair_hint") or ""
    if not hint:
        from . import repair as repair_mod

        category = state.get("error_category") or repair_mod.CATEGORY_UNKNOWN
        hint = repair_mod.strategy_for(category).hint
    # v2 不再重复注入文件清单：同一次请求的基础块（data_block）里已经有了
    files_block = (prompts.file_list_block(list(state.get("files") or {}))
                   if pol == POLICY_V1 else "（见上文「可用文件」）")
    text = prompts.repair_user_prompt(
        code=state.get("code", ""), stdout=stdout, stderr=stderr,
        files_block=files_block, repair_hint=hint,
        previous_errors=state.get("previous_errors") or [],
        policy=pol,
    )
    assembly = Assembly(policy=pol)
    assembly.blocks = [Block("repair", text)]
    if pol != POLICY_V1:
        assembly.notes.append(f"输出尾部截断至 {cap} 字符、不再重复注入文件清单")
    return assembly


def repair_summary_block(state: dict) -> str:
    """给结论节点的自修复上下文：只在真的修过 / 失败时才注入（不给省 token 的轮次加料）。"""
    from . import repair as repair_mod

    attempts = state.get("attempts", 0) or 0
    status = state.get("repair_status") or repair_mod.STATUS_NOT_NEEDED
    if attempts <= 1 and status == repair_mod.STATUS_NOT_NEEDED:
        return ""
    category = state.get("error_category") or repair_mod.CATEGORY_UNKNOWN
    label = repair_mod.CATEGORY_LABELS.get(category, category)
    lines = [
        "\n\n## 自修复过程（若最终失败，请如实说明修了几次、每轮错在哪，不要编造数字）",
        f"- 代码执行 {attempts} 次，发起修复 {state.get('repair_attempt', 0)} 次"
        f"（上限 MAX_FIX_ATTEMPTS={_cfg('MAX_FIX_ATTEMPTS', 3)}）",
        f"- 最终状态：{status}；最近错误类别：{label}（{category}）",
        f"- 决策原因：{state.get('repair_reason') or '-'}",
    ]
    errors = state.get("previous_errors") or []
    if errors:
        chain = " → ".join(e.get("label") or e.get("category", "") for e in errors)
        lines.append(f"- 错误序列：{chain}")
    return "\n".join(lines)


def summarize_context(state: dict, policy: str | None = None) -> Assembly:
    """`summarize` 的 user 消息装配。"""
    pol = resolve_policy(policy or state.get("context_policy"))
    execution = state.get("execution") or {}
    rows_limit = (V1_SUMMARIZE_TABLE_ROWS if pol == POLICY_V1
                  else int(_cfg("SUMMARIZE_TABLE_ROWS", 30)))
    result_json = {
        "ok": execution.get("ok"),
        "text": (execution.get("text") or "")[:4000],
        "tables": _trim_tables(execution.get("tables") or {}, rows_limit),
        "charts": execution.get("charts") or [],
    }
    body = (f"## 用户问题\n{state['question']}\n\n## 执行结果\n"
            f"```json\n{json.dumps(result_json, ensure_ascii=False, default=str)}\n```\n\n"
            f"## stderr（若失败）\n{(execution.get('stderr') or '')[-1500:]}"
            + repair_summary_block(state))
    assembly = Assembly(policy=pol)
    assembly.blocks = [Block("summarize", body)]
    if pol != POLICY_V1 and rows_limit:
        assembly.notes.append(f"结果表每张最多 {rows_limit} 行（真实数字不变）")
    return assembly


def _trim_tables(tables: dict, rows_limit: int | None) -> dict:
    if not rows_limit:
        return tables
    out = {}
    for name, rows in (tables or {}).items():
        if isinstance(rows, list):
            out[name] = rows[:rows_limit]
        else:
            out[name] = rows
    return out


def followup_context(state: dict, policy: str | None = None) -> Assembly:
    """`suggest_followups`（仅 LLM 分支）的装配。"""
    pol = resolve_policy(policy or state.get("context_policy"))
    body = (f"## 用户问题\n{state['question']}\n\n## 分析结论\n"
            f"{(state.get('answer') or '')[:800]}\n\n## 数据概况摘要\n"
            f"{(state.get('profile') or '')[:600]}")
    assembly = Assembly(policy=pol)
    assembly.blocks = [Block("followup", body)]
    return assembly


__all__ = [
    "Assembly", "Block", "POLICY_V1", "POLICY_V2", "V1_HISTORY_ANSWER_CHARS",
    "V1_HISTORY_TURNS", "V1_REPAIR_OUTPUT_CHARS", "V1_SKILL_EXAMPLES",
    "V1_SUMMARIZE_TABLE_ROWS", "followup_context", "generation_context",
    "history_block", "narrowed_semantic_block", "policy_from_config",
    "repair_context", "repair_summary_block", "resolve_policy", "semantic_block",
    "skill_block", "summarize_context",
]
