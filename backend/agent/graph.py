"""LangGraph agent core: plan -> generate code -> sandbox execute -> classify -> self-repair.

自修复的可靠性核心（OpenCodeInterpreter / PandasAI 的经验）在本项目里经历了两个版本：

- **V1**：execute → 失败就用同一段 prompt 重新生成，最多 `MAX_FIX_ATTEMPTS` 次；
- **V2（当前）**：execute → `classify`（错误分类 + 验收门）→ `decide`（定向策略 +
  有界额度 + 复读检测）→ 需要时注入**针对性修复提示**重新生成 → execute …
  → 通过验收则 summarize，否则以结构化失败收尾。

分类与决策全部确定性、零 token（不新增 LLM 调用），实现见 `backend/agent/repair.py`。
"""

import json
import logging
import re
import time
import uuid
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from backend import config
from backend.config import MAX_FIX_ATTEMPTS

from . import prompts
from . import repair as repair_mod
from .acceptance import acceptance_gate
from .profiler import profile_all
from .sandbox import SandboxResult, run_in_sandbox

logger = logging.getLogger(__name__)

_llm = None

# ---- 用户偏好（设置页可改：回答风格 / 创意度 / 追问开关 / 自定义指令） ----
#
# 偏好按**用户**存储：键是 `user_preferences:<user_id>`，读取时回退到旧的全局键
# `user_preferences`（历史兼容）。这样自定义指令只会注入本人的分析 prompt，
# 不会跨用户串味——安全上这是"跨用户影响"的收口点（见 docs/security.md）。
# 键的构造放在内核层（消费方在这里），设置路由复用同一函数，避免两份约定漂移。

PREFERENCE_KV_KEY = "user_preferences"
PROFILE_KV_KEY = "user_profile"

_prefs_cache: dict[str, dict] = {}
_PREFS_TTL_SECONDS = 30

STYLE_PROMPTS = {
    "concise": "\n\n【回答风格】简洁模式：100 字以内，只给核心结论与关键数字，不展开分析过程。",
    "detailed": "\n\n【回答风格】详细模式：充分展开——结论、依据、数据细节、业务解读与建议，可分点呈现（500 字以内）。",
    "standard": "",
}


def preference_kv_key(user_id: int | None = None) -> str:
    return f"{PREFERENCE_KV_KEY}:{int(user_id)}" if user_id else PREFERENCE_KV_KEY


def profile_kv_key(user_id: int | None = None) -> str:
    return f"{PROFILE_KV_KEY}:{int(user_id)}" if user_id else PROFILE_KV_KEY


def _state_user_id(state: "AgentState") -> int | None:
    """从可信 UserContext 取当前用户 id（匿名旧链路返回 None）。"""
    context = state.get("user_context") or {}
    try:
        return int(context.get("user_id")) if context.get("user_id") else None
    except (TypeError, ValueError):
        return None


def _get_prefs(user_id: int | None = None) -> dict:
    """读用户偏好的 KV，30 秒 TTL 缓存（按用户缓存，避免每节点都查库）。"""
    import time

    from backend import config

    cache_key = str(user_id or "global")
    now = time.time()
    cached = _prefs_cache.get(cache_key)
    if cached and now - cached["ts"] < _PREFS_TTL_SECONDS:
        return cached["data"]
    prefs = {"answer_style": "standard", "followups_enabled": True,
             "custom_instructions": "", "temperature": config.LLM_TEMPERATURE}
    try:
        import json as _json

        from backend.db import SessionLocal
        from backend.models import SystemSetting

        with SessionLocal() as db:
            row = db.get(SystemSetting, preference_kv_key(user_id)) if user_id else None
            if row is None:                      # 兼容旧版全局偏好
                row = db.get(SystemSetting, PREFERENCE_KV_KEY)
            if row:
                data = _json.loads(row.value)
                prefs.update({k: v for k, v in data.items() if k in prefs})
    except Exception:
        pass
    _prefs_cache[cache_key] = {"ts": now, "data": prefs}
    return prefs


def invalidate_prefs_cache() -> None:
    """设置页保存偏好后调用，让下一轮分析立即生效。"""
    _prefs_cache.clear()


def _pref_block(style_target: str = "summarize", user_id: int | None = None) -> str:
    """偏好 → 附加 prompt 块：回答风格（仅结论节点）+ 自定义指令（全节点）。"""
    prefs = _get_prefs(user_id)
    block = ""
    if style_target == "summarize":
        block += STYLE_PROMPTS.get(prefs.get("answer_style", "standard"), "")
    ci = (prefs.get("custom_instructions") or "").strip()
    if ci:
        block += f"\n\n## 用户全局指令（必须遵守）\n{ci}"
    return block


def _extract_usage(response: Any) -> tuple[int, int, float]:
    """从 LLM 响应提取 (input, output, cost)。

    langchain 的 usage_metadata 是 dict 而非属性对象，对它取属性恒为 0；
    以 dict 读取，并在缺失时回退到 OpenAI 风格 response_metadata.token_usage。
    """
    usage = getattr(response, "usage_metadata", None) or {}
    input_tok = int(usage.get("input_tokens", 0) or 0)
    output_tok = int(usage.get("output_tokens", 0) or 0)
    cost = float(usage.get("total_cost", 0) or 0)
    if input_tok + output_tok == 0:
        tu = (getattr(response, "response_metadata", None) or {}).get("token_usage") or {}
        input_tok = int(tu.get("prompt_tokens", 0) or 0)
        output_tok = int(tu.get("completion_tokens", 0) or 0)
    return input_tok, output_tok, cost


def _log_token_usage(state: "AgentState", node: str, response: Any) -> None:
    """从 LLM 响应中提取 token 用量并持久化（静默失败，不阻塞主流程）。"""
    try:
        input_tok, output_tok, cost = _extract_usage(response)
        if input_tok + output_tok == 0:
            return
        from backend.db import SessionLocal
        from backend.models import TokenUsage

        run_id = state.get("run_id")
        session_id = state.get("session_id")
        with SessionLocal() as db:
            db.add(TokenUsage(
                run_id=run_id, session_id=session_id, node=node,
                input_tokens=input_tok, output_tokens=output_tok, cost_usd=cost,
            ))
            db.commit()
    except Exception:
        pass  # token 追踪失败不应影响主流程


def get_llm() -> ChatOpenAI:
    """LLM 单例。动态读取 config（运行时可在设置页修改并 reset_llm）。"""
    global _llm
    if _llm is None:
        if not config.OPENAI_API_KEY:
            raise RuntimeError(
                "未配置 API Key：请在「系统设置」中填写 LLM 接口信息后重试"
            )
        _llm = ChatOpenAI(
            model=config.MODEL_NAME,
            base_url=config.OPENAI_BASE_URL,
            api_key=config.OPENAI_API_KEY,
            temperature=config.LLM_TEMPERATURE,
        )
    return _llm


def reset_llm() -> None:
    """配置变更后清空单例，下次调用用新配置重建。"""
    global _llm
    _llm = None


class AgentState(TypedDict):
    question: str
    files: dict[str, str]  # 真实存储文件名（含扩展名）-> host path
    profile: str
    semantic_block: str  # 行业语义层 prompt 块
    skill_block: str  # Skill few-shot 注入块（空字符串时行为与原版完全一致）
    spec: dict  # QuerySpec：意图理解结果（UI 可人工确认/修正）
    history: list[dict[str, str]]  # prior Q&A for follow-up turns
    plan: str
    code: str
    attempts: int
    execution: dict[str, Any]  # sandbox result (json-serializable subset)
    answer: str
    followups: list[str]
    run_id: int | None
    session_id: int | None
    # RBAC 最小钩子（与 skill_block 同模式）：可信后端解析的 UserContext。
    # LLM 不可决定或修改权限；execute 节点执行前做工具级权限校验（纵深防御）。
    user_context: dict
    # ---- Self-Repair V2 state（新增字段，不动 Skill Replay / QuerySpec / 语义解析 / 验收）----
    repair_policy: str          # "v2"（默认，分类+定向修复）| "v1"（冻结基线，仅供评估对比）
    repair_attempt: int         # 已发起的修复次数（首次执行不算）
    repair_status: str          # not_needed / repairing / succeeded / exhausted / repeated_failure / not_repairable
    error_category: str         # 最近一次执行的错误类别（见 repair.CATEGORIES）
    error_signature: str        # 最近一次执行的错误指纹（类别 + 归一化正文）
    repair_strategy: str        # 最近一次修复采用的策略 id
    repair_hint: str            # 注入给 LLM 的定向修复提示（trace 可回溯"当时说了什么"）
    repair_reason: str          # 决策原因（人类可读，回答"为什么修 / 为什么停"）
    repair_repeat_kind: str     # "" | identical | equivalent（复读类型）
    previous_errors: list[dict]  # 历史失败序列（类别 / 指纹 / 触发它的策略）
    repair_history: list[dict]   # 每次修复的耗时与结果（观测"修了多久、修完变成什么错"）



def _history_block(history: list[dict[str, str]] | None) -> str:
    """Compact prior Q&A context so follow-up questions keep continuity."""
    if not history:
        return ""
    turns = history[-3:]
    lines = ["## 之前的对话（供参考，新问题可能延续这些结论）"]
    for turn in turns:
        answer = (turn.get("answer") or "").strip()
        lines.append(f"- 问：{turn.get('question', '')}\n  答要旨：{answer[:600]}")
    return "\n".join(lines) + "\n"


def _extract_code(text: str) -> str:
    match = re.search(r"```python\s*(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _parse_spec_json(content: str) -> dict | None:
    """从 LLM 输出中提取首个 JSON 对象；失败返回 None。"""
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except ValueError:
        return None


def parse_intent(state: AgentState) -> dict:
    """意图理解：问题 + 语义层 + 对话上下文 → QuerySpec（FineChatBI 式语义解析）。"""
    if state.get("spec"):
        return {}  # UI 人工确认过，跳过
    try:
        llm = get_llm()
        human = (
            _history_block(state.get("history"))
            + f"{state.get('semantic_block', '')}\n\n## 用户问题\n{state['question']}"
        )
        response = llm.invoke(
            [
                SystemMessage(content=prompts.PARSE_SYSTEM),
                HumanMessage(content=human),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        spec = _parse_spec_json(content)
        if spec is None:
            logger.warning("parse_intent 输出不是合法 JSON，尝试修复重试")
            response = llm.invoke(
                [
                    SystemMessage(content=prompts.PARSE_SYSTEM),
                    HumanMessage(
                        content=human
                        + f"\n\n## 上次输出（不是合法 JSON）\n{content[:2000]}\n\n"
                        "请重新输出：只输出合法 JSON，不要任何其他文字。"
                    ),
                ]
            )
            content = response.content if isinstance(response.content, str) else str(response.content)
            spec = _parse_spec_json(content)
        if spec is None:
            logger.warning("parse_intent JSON 修复重试仍失败，回退为原始问题")
            spec = {}
        spec.setdefault("rewritten_question", state["question"])
        _log_token_usage(state, "parse_intent", response)
        return {"spec": spec}
    except Exception as exc:
        logger.warning("parse_intent 失败，回退为原始问题: %s", exc)
        return {"spec": {"rewritten_question": state["question"]}}


def _repair_policy(state: AgentState) -> repair_mod.RepairPolicy:
    """把配置（或本次运行的显式指定）解析成重试策略。

    `repair_policy="v1"` 是**冻结的 Baseline**：不分类、不复读检测，失败就无差别重试。
    它只为评估对比存在（与 `skills/retrieval.py` 的 `legacy_*` 同一模式），线上默认 v2。
    """
    name = (state.get("repair_policy") or getattr(config, "REPAIR_POLICY", "v2") or "v2").lower()
    return repair_mod.RepairPolicy(
        name="v1" if name == "v1" else "v2",
        max_fix_attempts=MAX_FIX_ATTEMPTS,
        repeat_limit=int(getattr(config, "REPAIR_REPEAT_LIMIT", 1)),
    )


def _repair_hint(state: AgentState) -> str:
    """本次修复要注入的处方：V2 按错误类别定制，V1 用冻结的统一提示。"""
    hint = (state.get("repair_hint") or "").strip()
    if hint:
        return hint
    if _repair_policy(state).is_legacy:
        return repair_mod.LEGACY_UNIFORM_HINT
    category = state.get("error_category") or repair_mod.CATEGORY_UNKNOWN
    return repair_mod.strategy_for(category).hint


def generate_code(state: AgentState) -> dict:
    files = state["files"]
    from backend.semantic import render_spec_prompt

    messages = [
        SystemMessage(content=prompts.GENERATE_SYSTEM
                       + _pref_block("generate", _state_user_id(state))),
        HumanMessage(
            content=_history_block(state.get("history"))
            + state.get("semantic_block", "")
            + state.get("skill_block", "")
            + "\n\n"
            + render_spec_prompt(state.get("spec"))
            + "\n\n"
            + prompts.generate_user_prompt(
                state["question"], state["profile"], list(files)
            )
        ),
    ]
    if state["attempts"] > 0:
        execution = state["execution"]
        messages.append(
            HumanMessage(
                content=prompts.repair_user_prompt(
                    code=state["code"],
                    stdout=execution.get("stdout", ""),
                    stderr=execution.get("stderr", ""),
                    files_block=prompts.file_list_block(list(files)),
                    repair_hint=_repair_hint(state),
                    previous_errors=state.get("previous_errors") or [],
                )
            )
        )
    llm = get_llm()
    response = llm.invoke(messages)
    content = response.content if isinstance(response.content, str) else str(response.content)
    _log_token_usage(state, "generate_code", response)
    return {"plan": content.split("```")[0].strip(), "code": _extract_code(content)}


def execute(state: AgentState) -> dict:
    # 工具级权限校验（纵深防御）：即使有人绕过 API 层直接驱动图谱，
    # 无 analysis:execute 权限的 UserContext 也不能触达沙箱执行。
    user_context = state.get("user_context") or {}
    if user_context:
        from backend.auth.context import permission_checker

        permission_checker.require(user_context, "analysis:execute")
    run_id = state.get("question", "run")[:8].replace(" ", "_") or "run"
    run_id = f"{run_id}-{uuid.uuid4().hex[:6]}"
    files = state["files"]
    result: SandboxResult = run_in_sandbox(run_id, state.get("code", ""), files)
    execution = {
        "ok": result.ok,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "text": result.text,
        "tables": result.tables,
        "charts": result.charts,
        "run_dir": str(result.out_dir),
        # 结构化失败信息：分类器优先读这些字段，而不是去猜 stderr 文案
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "failure_kind": result.failure_kind,
    }
    return {"execution": execution, "attempts": state["attempts"] + 1}


def _close_open_repair(history: list[dict], execution: dict, info,
                       accepted: bool) -> list[dict]:
    """给上一轮修复补上"修完花了多久、结果变成了什么错"。

    修复耗时的口径是「上一次修复决策 → 本次执行结论」的墙钟间隔，即"一轮修复"的
    真实代价（含 LLM 重新生成 + 沙箱执行），而不是某单个节点的耗时。
    """
    if not history:
        return history
    last = dict(history[-1])
    if "duration_ms" in last:
        return history
    started = last.pop("started_at", None)
    last["duration_ms"] = int((time.monotonic() - started) * 1000) if started else 0
    last["result_category"] = info.category
    last["result_signature"] = info.signature
    last["ok"] = bool(accepted)
    last["timed_out"] = bool(execution.get("timed_out"))
    return [*history[:-1], last]


def classify(state: AgentState) -> dict:
    """执行态 → 错误分类 → 有界重试决策（确定性、零 token）。

    这是 Self-Repair V2 的分水岭：V1 把"要不要重试"塞在路由函数里、只看 `ok`；
    V2 把它拆成独立节点并落进 state，于是 `Run.trace` 能回答
    「这次为什么修 2 次」「第 2 次为什么直接放弃」「第一轮错在哪」。
    """
    execution = state.get("execution") or {}
    gate = acceptance_gate(execution)
    info = repair_mod.classify_error(execution, gate)
    policy = _repair_policy(state)
    previous = list(state.get("previous_errors") or [])
    history = _close_open_repair(list(state.get("repair_history") or []),
                                execution, info, gate["passed"])
    attempts = state.get("attempts", 0) or 0
    decision = repair_mod.decide_repair(info, previous, policy, attempts=attempts)

    update: dict[str, Any] = {
        "error_category": info.category,
        "error_signature": info.signature,
        "repair_reason": decision.reason,
        "repair_repeat_kind": decision.repeat_kind,
        "repair_history": history,
        "previous_errors": previous,
        "repair_status": decision.status,
        "repair_strategy": "",
        "repair_hint": "",
    }

    if gate["passed"]:
        # 首次即通过 = not_needed；修过之后才通过 = succeeded（可回答"首次成功率"）
        # 通过验收时清空错误字段：没有失败就没有错误类别，别在 trace 里留噪声
        update["repair_status"] = (repair_mod.STATUS_SUCCEEDED if attempts > 1
                                   else repair_mod.STATUS_NOT_NEEDED)
        update["repair_reason"] = ("修复后通过结果验收" if attempts > 1
                                   else "首次执行即通过结果验收")
        update["error_category"] = ""
        update["error_signature"] = ""
        return update

    # 记录这一次失败：它是由"上一次的策略"产生的结果（首次失败时为全新生成）
    update["previous_errors"] = [*previous, {
        "category": info.category, "label": info.label, "signature": info.signature,
        "message": info.message[:300], "exception": info.exception,
        "strategy": state.get("repair_strategy") or "",
        "attempt": attempts,
    }]

    if decision.action != "repair":
        return update

    strategy = decision.strategy or repair_mod.strategy_for(info.category)
    update.update({
        "repair_attempt": (state.get("repair_attempt") or 0) + 1,
        "repair_strategy": strategy.strategy_id,
        "repair_hint": strategy.hint,
        "repair_history": [*history, {
            "attempt": decision.attempt,
            "category_attempt": decision.category_attempt,
            "trigger_category": info.category,
            "trigger_signature": info.signature,
            "strategy": strategy.strategy_id,
            "focus": strategy.focus,
            "started_at": time.monotonic(),
        }],
    })
    return update


def route_after_classify(state: AgentState) -> str:
    """通过验收 → 收尾；决策为"继续修" → 回到生成；其余（复读 / 耗尽 / 环境）
    一律收尾——**结构化失败也好过无限重试**。"""
    if state.get("repair_status") == repair_mod.STATUS_REPAIRING:
        return "generate_code"
    return "summarize"


def _repair_block(state: AgentState) -> str:
    """给 summarize 的自修复上下文：只在真的修过 / 失败时才注入（不给省 token 的轮次加料）。"""
    attempts = state.get("attempts", 0) or 0
    status = state.get("repair_status") or repair_mod.STATUS_NOT_NEEDED
    if attempts <= 1 and status == repair_mod.STATUS_NOT_NEEDED:
        return ""
    category = state.get("error_category") or repair_mod.CATEGORY_UNKNOWN
    label = repair_mod.CATEGORY_LABELS.get(category, category)
    lines = [
        "\n\n## 自修复过程（若最终失败，请如实说明修了几次、每轮错在哪，不要编造数字）",
        f"- 代码执行 {attempts} 次，发起修复 {state.get('repair_attempt', 0)} 次"
        f"（上限 MAX_FIX_ATTEMPTS={MAX_FIX_ATTEMPTS}）",
        f"- 最终状态：{status}；最近错误类别：{label}（{category}）",
        f"- 决策原因：{state.get('repair_reason') or '-'}",
    ]
    errors = state.get("previous_errors") or []
    if errors:
        chain = " → ".join(e.get("label") or e.get("category", "") for e in errors)
        lines.append(f"- 错误序列：{chain}")
    return "\n".join(lines)


def summarize(state: AgentState) -> dict:
    execution = state["execution"]
    result_json = {
        k: execution.get(k)
        for k in ("ok", "text", "tables", "charts")
    }
    llm = get_llm()
    response = llm.invoke(
        [
            SystemMessage(content=prompts.SUMMARIZE_SYSTEM
                       + _pref_block("summarize", _state_user_id(state))),
            HumanMessage(
                content=f"## 用户问题\n{state['question']}\n\n## 执行结果\n"
                f"```json\n{result_json}\n```\n\n## stderr（若失败）\n"
                f"{execution.get('stderr', '')[-1500:]}"
                + _repair_block(state)
            ),
        ]
    )
    content = response.content if isinstance(response.content, str) else str(response.content)
    _log_token_usage(state, "summarize", response)
    return {"answer": content}


def suggest_followups(state: AgentState) -> dict:
    """Suggest next questions from the result (Vanna's followup-questions idea)."""
    execution = state["execution"]
    if not execution.get("ok"):
        return {"followups": []}
    if not _get_prefs(_state_user_id(state)).get("followups_enabled", True):
        return {"followups": []}
    try:
        llm = get_llm()
        response = llm.invoke(
            [
                SystemMessage(content=prompts.FOLLOWUP_SYSTEM),
                HumanMessage(
                    content=f"## 用户问题\n{state['question']}\n\n## 分析结论\n"
                    f"{state['answer'][:800]}\n\n## 数据概况摘要\n"
                    f"{state['profile'][:600]}"
                ),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        _log_token_usage(state, "followup", response)
        questions = [
            q.strip().lstrip("0123456789.、-）) ") for q in content.splitlines() if q.strip()
        ][:3]
        return {"followups": [q for q in questions if len(q) >= 4]}
    except Exception:
        return {"followups": []}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("parse_intent", parse_intent)
    graph.add_node("generate_code", generate_code)
    graph.add_node("execute", execute)
    graph.add_node("classify", classify)
    graph.add_node("summarize", summarize)
    graph.add_node("suggest_followups", suggest_followups)
    graph.set_entry_point("parse_intent")
    graph.add_edge("parse_intent", "generate_code")
    graph.add_edge("generate_code", "execute")
    # execute → classify：分类 + 验收门 + 有界重试决策（仍然是线性流程，无 tool-calling）
    graph.add_edge("execute", "classify")
    graph.add_conditional_edges("classify", route_after_classify)
    graph.add_edge("summarize", "suggest_followups")
    graph.add_edge("suggest_followups", END)
    return graph.compile()


NODE_LABELS = {
    "parse_intent": "理解问题（语义解析）",
    "generate_code": "生成分析代码",
    "execute": "沙箱执行",
    "classify": "错误分类与修复决策",
    "summarize": "整理结论",
    "suggest_followups": "推荐追问",
}


def _initial_state(
    question: str,
    files: dict[str, str],
    history: list[dict[str, str]] | None,
    spec: dict | None,
    semantic_block: str,
    skill_block: str = "",
    run_id: int | None = None,
    session_id: int | None = None,
    user_context: dict | None = None,
    repair_policy: str | None = None,
) -> AgentState:
    return {
        "question": question,
        "files": files,
        "profile": profile_all(files),
        "semantic_block": semantic_block,
        "skill_block": skill_block,
        "spec": spec or {},
        "history": history or [],
        "plan": "",
        "code": "",
        "attempts": 0,
        "execution": {},
        "answer": "",
        "followups": [],
        "run_id": run_id,
        "session_id": session_id,
        "user_context": user_context or {},
        # Self-Repair V2：全部为新增字段，默认值保证行为与「关掉自修复分类」的旧版一致
        "repair_policy": repair_policy or getattr(config, "REPAIR_POLICY", "v2"),
        "repair_attempt": 0,
        "repair_status": repair_mod.STATUS_NOT_NEEDED,
        "error_category": "",
        "error_signature": "",
        "repair_strategy": "",
        "repair_hint": "",
        "repair_reason": "",
        "repair_repeat_kind": "",
        "previous_errors": [],
        "repair_history": [],
    }


def run_analysis(
    question: str,
    files: dict[str, str],
    history: list[dict[str, str]] | None = None,
    spec: dict | None = None,
    semantic_block: str = "",
    skill_block: str = "",
    run_id: int | None = None,
    session_id: int | None = None,
    user_context: dict | None = None,
    repair_policy: str | None = None,
) -> AgentState:
    app = build_graph()
    initial = _initial_state(question, files, history, spec, semantic_block, skill_block,
                             run_id=run_id, session_id=session_id,
                             user_context=user_context, repair_policy=repair_policy)
    return app.invoke(initial)


def stream_analysis(
    question: str,
    files: dict[str, str],
    history: list[dict[str, str]] | None = None,
    spec: dict | None = None,
    semantic_block: str = "",
    skill_block: str = "",
    run_id: int | None = None,
    session_id: int | None = None,
    user_context: dict | None = None,
    repair_policy: str | None = None,
):
    """Run the graph yielding (node, delta, merged_state) after every node,
    so the UI can render DB-GPT-style live steps while the agent works."""
    app = build_graph()
    initial = _initial_state(question, files, history, spec, semantic_block, skill_block,
                             run_id=run_id, session_id=session_id,
                             user_context=user_context, repair_policy=repair_policy)
    merged: dict = dict(initial)
    for update in app.stream(initial, stream_mode="updates"):
        for node, delta in update.items():
            # 预置 spec 时 parse_intent 返回 {}，LangGraph 会发出 delta=None
            delta = delta or {}
            merged.update(delta)
            yield node, delta, merged
