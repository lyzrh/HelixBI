"""LangGraph agent core: plan -> generate code -> sandbox execute -> classify -> self-repair.

自修复的可靠性核心（OpenCodeInterpreter / PandasAI 的经验）在本项目里经历了两个版本：

- **V1**：execute → 失败就用同一段 prompt 重新生成，最多 `MAX_FIX_ATTEMPTS` 次；
- **V2（当前）**：execute → `classify`（错误分类 + 验收门）→ `decide`（定向策略 +
  有界额度 + 复读检测）→ 需要时注入**针对性修复提示**重新生成 → execute …
  → 通过验收则 summarize，否则以结构化失败收尾。

分类与决策全部确定性、零 token（不新增 LLM 调用），实现见 `backend/agent/repair.py`。

成本与延迟（Cost & Latency Optimization V1）在这一层加了四件事，都是为了"少花不该花的"：

1. **意图快路径**（`intent.py`）：确定性解析满足门条件时**跳过 `parse_intent` 的 LLM 调用**；
2. **上下文装配器**（`context.py`）：按命中口径收窄语义层、去重、few-shot 限流，
   并把每块 token 记账写进 trace；
3. **运行预算**（`budget.py`）：调用前预检，超预算不发请求，终止原因进 trace；
4. **确定性追问**（`followups.py`）：追问推荐不再默认调用 LLM。

四件事都可通过 `config` 关掉（`INTENT_MODE=llm` / `CONTEXT_POLICY=v1` /
`FOLLOWUP_MODE=llm` / 预算留空），关掉后行为与改造前一致。
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

from . import budget as budget_mod
from . import context as context_mod
from . import followups as followups_mod
from . import intent as intent_mod
from . import prompts
from . import repair as repair_mod
from . import tokens as token_mod
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


# ---- 受预算约束的 LLM 调用（成本优化的唯一入口）----
#
# 所有 LLM 调用都必须经过 `_guarded_invoke`，这样"预算"才是真约束而不是事后统计：
# 它在**调用前**用本地计数判断还够不够（`tokens.count_messages`），够才发请求。
# 用量优先取 provider 返回值；provider 未返回 usage 时退回本地估算并标注来源，
# 让 `Run.trace.cost_control` 里的数字既可用又诚实。

def _budget_of(state: "AgentState") -> tuple[budget_mod.RunBudget, budget_mod.BudgetState]:
    limit = state.get("budget_limit") or {}
    budget = budget_mod.RunBudget(
        max_llm_calls=limit.get("max_llm_calls"),
        max_input_tokens=limit.get("max_input_tokens"),
        max_output_tokens=limit.get("max_output_tokens"),
        max_total_tokens=limit.get("max_total_tokens"),
        max_cost_usd=limit.get("max_cost_usd"),
        max_repair_attempts=limit.get("max_repair_attempts"),
    )
    return budget, budget_mod.state_from_dict(state.get("budget_used"))


def _guarded_invoke(state: "AgentState", node: str, messages: list):
    """受预算约束的一次 LLM 调用 → `(response | None, state update)`。

    `response is None` 表示**这次调用被预算拦下**（UPDATE 里带终止原因），
    调用方必须走确定性兜底，不允许伪造结果。
    """
    budget, used = _budget_of(state)
    estimated_input = token_mod.count_messages(messages)
    decision = budget_mod.precheck(budget, used, node, estimated_input)
    if not decision.allowed:
        budget_mod.note_denied(used, node, decision)
        logger.info("预算拦下 LLM 调用：%s（%s）", node, decision.reason_code)
        return None, {"budget_used": used.to_dict(),
                      "termination_reason": decision.reason_code,
                      "budget_blocked_node": node,
                      "budget_reason": decision.reason}

    response = get_llm().invoke(messages)
    input_tok, output_tok, cost = _extract_usage(response)
    usage_source = "provider"
    if input_tok + output_tok == 0:
        # provider 没给 usage：用本地计数兜住登记（口径标注为估算，不冒充计费值）
        input_tok, output_tok, cost = estimated_input, 0, 0.0
        usage_source = "estimated"
    _log_token_usage(state, node, response)

    used.llm_calls += 1
    used.input_tokens += input_tok
    used.output_tokens += output_tok
    used.cost_usd += cost
    post = budget_mod.postcheck(budget, used, node)
    update: dict[str, Any] = {
        "budget_used": used.to_dict(),
        "budget_estimated_input_tokens": estimated_input,
        "budget_usage_source": usage_source,
    }
    if not post.allowed:
        used.termination_reason = post.reason_code
        update["budget_used"] = used.to_dict()
        update["termination_reason"] = post.reason_code
        update["budget_reason"] = post.reason
    return response, update


def _deterministic_answer(state: "AgentState", reason: str) -> str:
    """无 LLM 的兜底结论：只说**已经真实发生**的事，绝不编造数字。"""
    execution = state.get("execution") or {}
    gate = acceptance_gate(execution)
    lines = [
        f"本次分析未能给出结论：{reason}",
        "",
        f"- 代码执行：{'成功' if execution.get('ok') else '失败'}"
        f"（共 {state.get('attempts', 0) or 0} 次）",
        f"- 结果验收：{'通过' if gate['passed'] else '未通过'}"
        + (f"（未通过项：{'、'.join(gate.get('failed') or [])}）" if gate.get("failed") else ""),
    ]
    tables = execution.get("tables") or {}
    if tables:
        sizes = "、".join(f"{name}({len(rows)} 行)" if isinstance(rows, list) else str(name)
                          for name, rows in list(tables.items())[:3])
        lines.append(f"- 已产出的结果表：{sizes}（原始数据可在下方执行日志中查看）")
    if execution.get("text"):
        lines.append(f"- 沙箱内生成的说明文字：{str(execution['text'])[:300]}")
    if not execution.get("ok") and execution.get("stderr"):
        lines.append(f"- 错误摘要：{str(execution['stderr'])[-300:]}")
    lines.append("")
    lines.append("说明：本轮达到运行预算上限（或未配置可用模型），因此跳过了 LLM 结论整理；"
                 "以上内容全部来自沙箱的真实执行结果，未经任何改写。")
    return "\n".join(lines)


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
    semantic_block: str  # 行业语义层 prompt 块（意图解析用的**全量**块）
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
    # ---- Cost & Latency V1 state（全部是新增字段，缺省即退化为改造前行为）----
    semantic_packs: list[str]    # 本次用的语义包 id（上下文装配器据此按命中口径收窄）
    skill_candidates: list       # few-shot 候选（由路由阶段的候选直接复用，避免重复检索）
    context_policy: str          # "v2"（默认）| "v1"（冻结的 prompt 装配基线）
    budget_limit: dict           # 运行预算上限（None 表示不限制）
    budget_used: dict            # 已用量 + 终止原因（trace.cost_control 直接读它）
    budget_blocked_node: str     # 最近一次被预算拦下的节点
    budget_reason: str           # 拦下的可读原因
    budget_usage_source: str     # provider | estimated（用量来源，诚实标注）
    budget_estimated_input_tokens: int  # 最近一次调用的本地预估输入 token
    termination_reason: str      # 整轮终止原因（"" = 正常结束）
    intent_source: str           # deterministic | llm | predefined | budget_fallback
    intent_meta: dict            # 快路径判定依据（置信度 / 未覆盖内容 / 原因）
    followup_source: str         # deterministic | llm | off | budget
    summarize_source: str        # llm | deterministic（预算拦下结论整理时走确定性兜底）
    generate_blocked: bool       # 生成本身被预算拦下（没有新代码可执行）
    context_stats: dict          # 各节点最后一次装配的分块 token 记账



def _history_block(history: list[dict[str, str]] | None,
                   policy: str | None = None) -> str:
    """Compact prior Q&A context so follow-up questions keep continuity.

    轮次与摘要长度由上下文策略决定（v1 = 改造前的 3 轮 × 600 字；v2 = 2 轮 × 300 字）。
    """
    return context_mod.history_block(history, context_mod.resolve_policy(policy)).text


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


def _intent_pack(state: "AgentState") -> str:
    """意图解析用的语义包：优先本次实际挂载的包，其次留空（不做快路径）。"""
    for pack in (state.get("semantic_packs") or []):
        if pack:
            return str(pack)
    return ""


def parse_intent(state: AgentState) -> dict:
    """意图理解：问题 + 语义层 + 对话上下文 → QuerySpec（FineChatBI 式语义解析）。

    **成本优化**：先用确定性解析器试一次（零 token）。命中安全门
    （置信度达标 + 问题内容全部有出处）就直接产出 QuerySpec，跳过这次 LLM 调用；
    只要有解释不了的内容就回落 LLM——这一点是正确性底线（取值过滤这类意图
    确定性解析识别不了，硬猜会把"筛选华东"变成"按区域分组"）。
    """
    if state.get("spec"):
        return {"intent_source": "predefined",
                "intent_meta": {"reason": "UI 已确认 QuerySpec，跳过意图解析"}}

    pack_id = _intent_pack(state)
    if pack_id:
        spec, meta = intent_mod.deterministic_intent(state["question"], pack_id)
        if meta.get("eligible"):
            return {"spec": spec, "intent_source": "deterministic", "intent_meta": meta}
    else:
        meta = {"eligible": False, "reason_code": "no_semantic_pack",
                "reason": "本次没有可用语义包，跳过确定性快路径"}

    try:
        human = (
            _history_block(state.get("history"), state.get("context_policy"))
            + f"{state.get('semantic_block', '')}\n\n## 用户问题\n{state['question']}"
        )
        messages = [
            SystemMessage(content=prompts.PARSE_SYSTEM),
            HumanMessage(content=human),
        ]
        response, update = _guarded_invoke(state, "parse_intent", messages)
        if response is None:
            # 预算拦下解析：用确定性结果兜底（有指标就用，没有就原问题直通），
            # 不做"跳过解析直接生成"之外的任何猜测
            fallback, _ = intent_mod.deterministic_intent(state["question"], pack_id) \
                if pack_id else ({}, {})
            spec = fallback or {"rewritten_question": state["question"]}
            update.update({"spec": spec, "intent_source": "budget_fallback",
                           "intent_meta": {**meta, "reason_code": "budget_blocked",
                                           "reason": update.get("budget_reason", "")}})
            return update

        content = response.content if isinstance(response.content, str) else str(response.content)
        spec = _parse_spec_json(content)
        if spec is None:
            logger.warning("parse_intent 输出不是合法 JSON，尝试修复重试")
            retry_messages = [
                SystemMessage(content=prompts.PARSE_SYSTEM),
                HumanMessage(
                    content=human
                    + f"\n\n## 上次输出（不是合法 JSON）\n{content[:2000]}\n\n"
                    "请重新输出：只输出合法 JSON，不要任何其他文字。"
                ),
            ]
            retry_response, retry_update = _guarded_invoke(state, "parse_intent", retry_messages)
            update = retry_update or update
            if retry_response is not None:
                content = (retry_response.content if isinstance(retry_response.content, str)
                           else str(retry_response.content))
                spec = _parse_spec_json(content)
        if spec is None:
            logger.warning("parse_intent JSON 修复重试仍失败，回退为原始问题")
            spec = {}
        spec.setdefault("rewritten_question", state["question"])
        spec.setdefault("source", "llm")
        update.update({"spec": spec, "intent_source": "llm", "intent_meta": meta})
        return update
    except Exception as exc:
        logger.warning("parse_intent 失败，回退为原始问题: %s", exc)
        return {"spec": {"rewritten_question": state["question"], "source": "fallback"},
                "intent_source": "fallback",
                "intent_meta": {**meta, "reason_code": "llm_error", "reason": str(exc)[:200]}}



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


def generate_code(state: AgentState) -> dict:
    """生成分析代码。

    **成本优化**：prompt 由上下文装配器统一拼装（按命中口径收窄语义层、去掉重复注入、
    few-shot 限流），分块 token 记账进 state；调用本身受运行预算约束——预算不足时
    **不发请求**，把状态标成"生成被拦下"，由路由直接收尾（结构化失败，不编造结果）。
    """
    files = state["files"]
    policy = context_mod.resolve_policy(state.get("context_policy"))
    assembly = context_mod.generation_context(state, policy)
    base = assembly.text
    stats = {"generation": assembly.stats()}

    messages = [
        SystemMessage(content=prompts.GENERATE_SYSTEM
                       + _pref_block("generate", _state_user_id(state))),
        HumanMessage(content=base),
    ]
    if state["attempts"] > 0:
        repair = context_mod.repair_context(state, policy)
        stats["repair"] = repair.stats()
        messages.append(HumanMessage(content=repair.text))

    response, update = _guarded_invoke(state, "generate_code", messages)
    update["context_stats"] = stats
    if response is None:
        # 生成被预算拦下：保留上一轮代码（如果有），标记"本轮未产生新代码"
        update.update({
            "generate_blocked": True,
            "repair_status": repair_mod.STATUS_EXHAUSTED,
            "repair_reason": f"生成被预算拦下：{update.get('budget_reason', '')}",
        })
        return update
    content = response.content if isinstance(response.content, str) else str(response.content)
    update.update({"plan": content.split("```")[0].strip(), "code": _extract_code(content),
                   "generate_blocked": False})
    return update



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


def summarize(state: AgentState) -> dict:
    """整理结论。装配走上下文装配器（结果表按行数收敛、真实数字不变）；
    预算不足时不发请求，改用**只陈述真实执行结果**的确定性结论。"""
    policy = context_mod.resolve_policy(state.get("context_policy"))
    assembly = context_mod.summarize_context(state, policy)
    messages = [
        SystemMessage(content=prompts.SUMMARIZE_SYSTEM
                      + _pref_block("summarize", _state_user_id(state))),
        HumanMessage(content=assembly.text),
    ]
    response, update = _guarded_invoke(state, "summarize", messages)
    update["context_stats"] = {**(state.get("context_stats") or {}),
                               "summarize": assembly.stats()}
    if response is None:
        update["answer"] = _deterministic_answer(
            state, update.get("budget_reason") or "达到运行预算上限，已跳过结论整理")
        update["summarize_source"] = "deterministic"
        return update
    content = response.content if isinstance(response.content, str) else str(response.content)
    update.update({"answer": content, "summarize_source": "llm"})
    return update


def suggest_followups(state: AgentState) -> dict:
    """推荐追问：默认由 QuerySpec 确定性生成（0 次 LLM），只有凑不满时才补一次 LLM。"""
    execution = state["execution"]
    if not execution.get("ok"):
        return {"followups": [], "followup_source": "skipped"}
    if not _get_prefs(_state_user_id(state)).get("followups_enabled", True):
        return {"followups": [], "followup_source": "disabled"}

    mode = getattr(config, "FOLLOWUP_MODE", followups_mod.MODE_HYBRID)
    candidates, needs_llm = followups_mod.plan_followups(
        state["question"], state.get("spec") or {},
        pack_id=_intent_pack(state) or None, mode=mode)
    if not needs_llm:
        return {"followups": candidates,
                "followup_source": ("off" if str(mode).lower() == followups_mod.MODE_OFF
                                    else "deterministic")}

    assembly = context_mod.followup_context(state)
    messages = [
        SystemMessage(content=prompts.FOLLOWUP_SYSTEM),
        HumanMessage(content=assembly.text),
    ]
    try:
        response, update = _guarded_invoke(state, "followup", messages)
    except Exception:
        return {"followups": candidates, "followup_source": "deterministic"}
    update["context_stats"] = {**(state.get("context_stats") or {}),
                               "followup": assembly.stats()}
    if response is None:
        # 预算拦下补问：确定性候选就是最终答案（不降级为"没有追问"）
        update.update({"followups": candidates,
                       "followup_source": "budget" if candidates else "budget_empty"})
        return update
    content = response.content if isinstance(response.content, str) else str(response.content)
    questions = [
        q.strip().lstrip("0123456789.、-）) ") for q in content.splitlines() if q.strip()
    ][:3]
    questions = [q for q in questions if len(q) >= 4]
    return {**update, "followups": questions or candidates, "followup_source": "llm"}


def route_after_generate(state: AgentState) -> str:
    """生成被预算拦下时不再执行（没有新代码可跑），直接收尾给结构化结论。"""
    return "summarize" if state.get("generate_blocked") else "execute"


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
    # generate_code → execute：唯一的分支点是"预算拦下生成"（没有新代码可执行）
    graph.add_conditional_edges("generate_code", route_after_generate)
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
    semantic_packs: list[str] | None = None,
    skill_candidates: list | None = None,
    context_policy: str | None = None,
    budget_overrides: dict | None = None,
) -> AgentState:
    workspace_id = (user_context or {}).get("workspace_id")
    return {
        "question": question,
        "files": files,
        # 画像按文件指纹 + 工作区缓存：同一批数据重复分析不再重复读盘
        "profile": profile_all(files, workspace_id=workspace_id,
                               use_cache=getattr(config, "PROFILE_CACHE_ENABLED", True)),
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
        # Cost & Latency V1：新增字段（默认值 = 改造前行为）
        "semantic_packs": list(semantic_packs or []),
        "skill_candidates": list(skill_candidates or []),
        "context_policy": context_mod.resolve_policy(context_policy),
        "budget_limit": budget_mod.budget_from_config(budget_overrides).to_dict(),
        "budget_used": budget_mod.BudgetState().to_dict(),
        "budget_blocked_node": "",
        "budget_reason": "",
        "budget_usage_source": "",
        "budget_estimated_input_tokens": 0,
        "termination_reason": "",
        "intent_source": "",
        "intent_meta": {},
        "followup_source": "",
        "summarize_source": "",
        "generate_blocked": False,
        "context_stats": {},
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
    semantic_packs: list[str] | None = None,
    skill_candidates: list | None = None,
    context_policy: str | None = None,
    budget_overrides: dict | None = None,
) -> AgentState:
    app = build_graph()
    initial = _initial_state(question, files, history, spec, semantic_block, skill_block,
                             run_id=run_id, session_id=session_id,
                             user_context=user_context, repair_policy=repair_policy,
                             semantic_packs=semantic_packs,
                             skill_candidates=skill_candidates,
                             context_policy=context_policy,
                             budget_overrides=budget_overrides)
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
    semantic_packs: list[str] | None = None,
    skill_candidates: list | None = None,
    context_policy: str | None = None,
    budget_overrides: dict | None = None,
):
    """Run the graph yielding (node, delta, merged_state) after every node,
    so the UI can render DB-GPT-style live steps while the agent works."""
    app = build_graph()
    initial = _initial_state(question, files, history, spec, semantic_block, skill_block,
                             run_id=run_id, session_id=session_id,
                             user_context=user_context, repair_policy=repair_policy,
                             semantic_packs=semantic_packs,
                             skill_candidates=skill_candidates,
                             context_policy=context_policy,
                             budget_overrides=budget_overrides)
    merged: dict = dict(initial)
    for update in app.stream(initial, stream_mode="updates"):
        for node, delta in update.items():
            # 预置 spec 时 parse_intent 返回 {}，LangGraph 会发出 delta=None
            delta = delta or {}
            merged.update(delta)
            yield node, delta, merged
