"""LangGraph agent core: plan -> generate code -> sandbox execute -> self-fix loop.

The self-repair loop (execute-error -> regenerate, up to MAX_FIX_ATTEMPTS) is
the reliability core identified in the research (OpenCodeInterpreter /
PandasAI lessons).
"""

import json
import logging
import re
import uuid
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from . import config, prompts
from .config import MAX_FIX_ATTEMPTS
from .profiler import profile_all
from .sandbox import SandboxResult, run_in_sandbox

logger = logging.getLogger(__name__)

_llm = None

# ---- 用户偏好（设置页可改：回答风格 / 创意度 / 追问开关 / 自定义指令） ----

_prefs_cache: dict = {"ts": 0.0, "data": None}

STYLE_PROMPTS = {
    "concise": "\n\n【回答风格】简洁模式：100 字以内，只给核心结论与关键数字，不展开分析过程。",
    "detailed": "\n\n【回答风格】详细模式：充分展开——结论、依据、数据细节、业务解读与建议，可分点呈现（500 字以内）。",
    "standard": "",
}


def _get_prefs() -> dict:
    """读用户偏好 KV，30 秒 TTL 缓存（避免每个节点都查库）。"""
    import time

    now = time.time()
    if _prefs_cache["data"] is not None and now - _prefs_cache["ts"] < 30:
        return _prefs_cache["data"]
    prefs = {"answer_style": "standard", "followups_enabled": True, "custom_instructions": ""}
    try:
        import json as _json

        from backend.db import SessionLocal
        from backend.models import SystemSetting

        with SessionLocal() as db:
            row = db.get(SystemSetting, "user_preferences")
            if row:
                data = _json.loads(row.value)
                prefs.update({k: v for k, v in data.items() if k in prefs})
    except Exception:
        pass
    _prefs_cache["ts"] = now
    _prefs_cache["data"] = prefs
    return prefs


def invalidate_prefs_cache() -> None:
    """设置页保存偏好后调用，让下一轮分析立即生效。"""
    _prefs_cache["data"] = None


def _pref_block(style_target: str = "summarize") -> str:
    """偏好 → 附加 prompt 块：回答风格（仅结论节点）+ 自定义指令（全节点）。"""
    prefs = _get_prefs()
    block = ""
    if style_target == "summarize":
        block += STYLE_PROMPTS.get(prefs.get("answer_style", "standard"), "")
    ci = (prefs.get("custom_instructions") or "").strip()
    if ci:
        block += f"\n\n## 用户全局指令（必须遵守）\n{ci}"
    return block


def _log_token_usage(state: "AgentState", node: str, response: Any) -> None:
    """从 LLM 响应中提取 token 用量并持久化（静默失败，不阻塞主流程）。"""
    try:
        usage = getattr(response, "usage_metadata", None)
        if not usage:
            return
        from backend.db import SessionLocal
        from backend.models import TokenUsage
        input_tok = int(getattr(usage, "input_tokens", 0) or 0)
        output_tok = int(getattr(usage, "output_tokens", 0) or 0)
        cost = float(getattr(usage, "total_cost", 0) or 0)
        if input_tok + output_tok == 0:
            return
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


def generate_code(state: AgentState) -> dict:
    files = state["files"]
    from .semantic import render_spec_prompt

    messages = [
        SystemMessage(content=prompts.GENERATE_SYSTEM + _pref_block("generate")),
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
                content=prompts.FIX_USER_TMPL.format(
                    code=state["code"],
                    stdout=execution.get("stdout", ""),
                    stderr=execution.get("stderr", ""),
                    files_block=prompts.file_list_block(list(files)),
                )
            )
        )
    llm = get_llm()
    response = llm.invoke(messages)
    content = response.content if isinstance(response.content, str) else str(response.content)
    _log_token_usage(state, "generate_code", response)
    return {"plan": content.split("```")[0].strip(), "code": _extract_code(content)}


def execute(state: AgentState) -> dict:
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
    }
    return {"execution": execution, "attempts": state["attempts"] + 1}


def route_after_execute(state: AgentState) -> str:
    execution = state["execution"]
    if execution["ok"] and (execution["text"] or execution["tables"] or execution["charts"]):
        return "summarize"
    if state["attempts"] <= MAX_FIX_ATTEMPTS:
        return "generate_code"
    return "summarize"


def summarize(state: AgentState) -> dict:
    execution = state["execution"]
    result_json = {
        k: execution.get(k)
        for k in ("ok", "text", "tables", "charts")
    }
    llm = get_llm()
    response = llm.invoke(
        [
            SystemMessage(content=prompts.SUMMARIZE_SYSTEM + _pref_block("summarize")),
            HumanMessage(
                content=f"## 用户问题\n{state['question']}\n\n## 执行结果\n"
                f"```json\n{result_json}\n```\n\n## stderr（若失败）\n"
                f"{execution.get('stderr', '')[-1500:]}"
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
    if not _get_prefs().get("followups_enabled", True):
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
    graph.add_node("summarize", summarize)
    graph.add_node("suggest_followups", suggest_followups)
    graph.set_entry_point("parse_intent")
    graph.add_edge("parse_intent", "generate_code")
    graph.add_edge("generate_code", "execute")
    graph.add_conditional_edges("execute", route_after_execute)
    graph.add_edge("summarize", "suggest_followups")
    graph.add_edge("suggest_followups", END)
    return graph.compile()


NODE_LABELS = {
    "parse_intent": "理解问题（语义解析）",
    "generate_code": "生成分析代码",
    "execute": "沙箱执行",
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
) -> AgentState:
    app = build_graph()
    initial = _initial_state(question, files, history, spec, semantic_block, skill_block,
                             run_id=run_id, session_id=session_id)
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
):
    """Run the graph yielding (node, delta, merged_state) after every node,
    so the UI can render DB-GPT-style live steps while the agent works."""
    app = build_graph()
    initial = _initial_state(question, files, history, spec, semantic_block, skill_block,
                             run_id=run_id, session_id=session_id)
    merged: dict = dict(initial)
    for update in app.stream(initial, stream_mode="updates"):
        for node, delta in update.items():
            merged.update(delta)
            yield node, delta, merged
