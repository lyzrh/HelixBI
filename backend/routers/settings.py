"""系统设置：LLM API 接口配置（平台级，热更新）+ 个人资料 / 偏好（**按用户隔离**）。

安全边界（Security Hardening V1）：

- **平台级**（LLM 接口 / 密钥 / 创意度）：读需 `workspace:manage`（工作区管理员），
  写需 `settings:write`（平台设置）。API Key 永远只返回掩码，永不回显明文。
- **个人级**（昵称 / 头像 / 回答风格 / 追问开关 / 自定义指令）：按 `user_id` 存储，
  **互不可见、互不影响**。旧版本存的是全局 KV——一个人改、所有人受影响，
  而且自定义指令会被注入到**别人的**分析 prompt 里；现在读取时兼容旧键，
  写入一律写用户自己的键。
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import config
from backend.agent.graph import (
    get_llm, preference_kv_key, profile_kv_key, reset_llm,
)
from backend.auth import audit
from backend.auth.context import UserContext
from backend.auth.deps import get_current_context, require_permission
from backend.db import get_db
from backend.models import SystemSetting, now_str

router = APIRouter(prefix="/settings",
                   dependencies=[Depends(get_current_context)])

# 历史全局键（兼容读取；个人资料 / 偏好新写入一律用 `键:用户id`）
K_PROFILE = "user_profile"
K_PREFERENCES = "user_preferences"
DEFAULT_PROFILE = {"nickname": "数据探索者", "role": "数据分析师", "avatar_color": "#2563eb"}
DEFAULT_PREFERENCES = {
    "answer_style": "standard",        # concise | standard | detailed
    "temperature": 0.0,                # 创意度：**平台级**（影响所有分析），需 settings:write
    "followups_enabled": True,         # 回答后推荐追问
    "custom_instructions": "",         # 自定义指令（只注入本人的分析 prompt）
}
_TEMPERATURE_CHOICES = (0.0, 0.5, 0.9)
_STYLES = ("concise", "standard", "detailed")


def user_profile_key(user_id: int) -> str:
    """按用户隔离的资料键（实现与内核层共用一份约定）。"""
    return profile_kv_key(user_id)


def user_preferences_key(user_id: int) -> str:
    return preference_kv_key(user_id)



def _read_kv(db: Session, key: str, defaults: dict, fallback_key: str | None = None) -> dict:
    """读 KV JSON 并合并默认值；`fallback_key` 用于兼容旧版全局键。"""
    data: dict = {}
    for candidate in (key, fallback_key):
        if not candidate:
            continue
        row = db.get(SystemSetting, candidate)
        if row:
            try:
                data = json.loads(row.value)
            except Exception:
                data = {}
            if data:
                break
    merged = dict(defaults)
    merged.update({k: v for k, v in data.items() if k in defaults})
    return merged


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:5]}***{key[-4:]}"


class LlmSettingsBody(BaseModel):
    base_url: str | None = None
    api_key: str | None = None  # 留空/None 表示不修改
    model: str | None = None


@router.get("/llm")
def get_llm_settings(ctx: UserContext = Depends(get_current_context)):
    """LLM 配置摘要：**登录即可读**（前端设置页要展示模型名），但按权限分级：

    - 所有人：`model` / `configured` / 掩码后的 key（用于判断"配没配"）；
    - `settings:write`：额外返回 `base_url`（可能是内网地址，属平台信息）。

    无论哪种权限，**永远不返回 API Key 明文**。
    """
    from backend.auth.context import permission_checker

    payload = {
        "model": config.MODEL_NAME,
        "api_key_masked": mask_key(config.OPENAI_API_KEY),
        "configured": bool(config.OPENAI_API_KEY),
        "can_edit": permission_checker.has_permission(ctx, "settings:write"),
    }
    if payload["can_edit"]:
        payload["base_url"] = config.OPENAI_BASE_URL
    return payload


@router.put("/llm")
def update_llm_settings(body: LlmSettingsBody, request: Request,
                        ctx: UserContext = Depends(require_permission("settings:write"))):
    if not any([body.base_url, body.api_key, body.model]):
        return {"ok": True, "message": "没有需要保存的变更"}
    # 审计只记"改了哪些字段"，绝不记值（值是密钥）
    changed = [name for name, value in (("base_url", body.base_url),
                                        ("api_key", body.api_key),
                                        ("model", body.model)) if value]
    config.update_llm_config(body.base_url, body.api_key, body.model)
    reset_llm()
    audit.record("settings.llm_updated", "ok", user_id=ctx.user_id,
                 username=ctx.username, workspace_id=ctx.workspace_id,
                 target="settings:llm", detail={"changed_fields": changed},
                 request=request)
    return {
        "ok": True,
        "message": "已保存并即时生效（同时写入 .env，重启后保留）",
        "base_url": config.OPENAI_BASE_URL,
        "model": config.MODEL_NAME,
        "api_key_masked": mask_key(config.OPENAI_API_KEY),
    }


class LlmTestBody(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


@router.post("/llm/test")
def test_llm(body: LlmTestBody, request: Request,
             ctx: UserContext = Depends(require_permission("settings:write"))):
    """测试连通性：用极小请求（max_tokens=1）验证，尽量省 token。

    传了字段则用传入值临时测试（不落盘）；否则用当前已保存配置。
    这里能用任意 base_url 发起请求（等价于一次 SSRF 探测能力），因此必须是
    平台设置权限（管理员），不能开放给普通成员。
    """
    base_url = (body.base_url or config.OPENAI_BASE_URL).strip()
    api_key = (body.api_key or "").strip() or config.OPENAI_API_KEY
    model = (body.model or config.MODEL_NAME).strip()
    if not api_key:
        return {"ok": False, "message": "缺少 API Key，无法测试"}
    audit.record("settings.llm_tested", "ok", user_id=ctx.user_id,
                 username=ctx.username, workspace_id=ctx.workspace_id,
                 target=f"model:{model}", request=request)

    try:
        from langchain_openai import ChatOpenAI

        probe = ChatOpenAI(model=model, base_url=base_url, api_key=api_key,
                           temperature=0, max_tokens=1, timeout=20)
        reply = probe.invoke([HumanMessage(content="hi")])
        # 只要没抛异常即连通；部分网关返回空内容也算成功
        return {"ok": True, "message": f"连接成功（{model}，响应 {len(str(reply.content))} 字符）"}
    except Exception as exc:
        msg = str(exc)
        if "401" in msg or "Unauthorized" in msg or "invalid" in msg.lower():
            return {"ok": False, "message": "认证失败：API Key 无效或过期"}
        if "timeout" in msg.lower() or "timed out" in msg.lower():
            return {"ok": False, "message": "连接超时：检查 Base URL 是否可达"}
        if "model" in msg.lower() and ("not" in msg.lower() or "404" in msg):
            return {"ok": False, "message": f"模型不可用：{model}（检查模型名拼写）"}
        return {"ok": False, "message": f"连接失败：{msg[:160]}"}


# ---- 个人资料（按用户隔离） ----


class ProfileBody(BaseModel):
    nickname: str | None = None
    role: str | None = None
    avatar_color: str | None = None


@router.get("/profile")
def get_profile(db: Session = Depends(get_db),
                ctx: UserContext = Depends(get_current_context)):
    return _read_kv(db, user_profile_key(ctx.user_id), DEFAULT_PROFILE, K_PROFILE)


@router.put("/profile")
def update_profile(body: ProfileBody, db: Session = Depends(get_db),
                   ctx: UserContext = Depends(get_current_context)):
    profile = _read_kv(db, user_profile_key(ctx.user_id), DEFAULT_PROFILE, K_PROFILE)
    if body.nickname is not None:
        profile["nickname"] = body.nickname.strip()[:20] or DEFAULT_PROFILE["nickname"]
    if body.role is not None:
        profile["role"] = body.role.strip()[:20] or DEFAULT_PROFILE["role"]
    if body.avatar_color is not None and body.avatar_color.startswith("#"):
        profile["avatar_color"] = body.avatar_color[:9]
    _write_kv(db, user_profile_key(ctx.user_id), profile)
    return profile


# ---- 偏好设置（按用户隔离；创意度为平台级） ----


class PreferencesBody(BaseModel):
    answer_style: str | None = None        # concise | standard | detailed
    temperature: float | None = None       # 0 / 0.5 / 0.9（平台级，需 settings:write）
    followups_enabled: bool | None = None
    custom_instructions: str | None = None


@router.get("/preferences")
def get_preferences(db: Session = Depends(get_db),
                    ctx: UserContext = Depends(get_current_context)):
    prefs = _read_kv(db, user_preferences_key(ctx.user_id), DEFAULT_PREFERENCES,
                     K_PREFERENCES)
    prefs["temperature"] = config.LLM_TEMPERATURE
    return prefs


@router.put("/preferences")
def update_preferences(body: PreferencesBody, request: Request,
                       db: Session = Depends(get_db),
                       ctx: UserContext = Depends(get_current_context)):
    from backend.auth.context import permission_checker

    prefs = _read_kv(db, user_preferences_key(ctx.user_id), DEFAULT_PREFERENCES,
                     K_PREFERENCES)
    if body.answer_style in _STYLES:
        prefs["answer_style"] = body.answer_style
    if body.followups_enabled is not None:
        prefs["followups_enabled"] = bool(body.followups_enabled)
    if body.custom_instructions is not None:
        prefs["custom_instructions"] = body.custom_instructions.strip()[:500]
    # 创意度是**平台级**参数（影响所有人的分析），普通成员不能静默修改
    if body.temperature is not None and body.temperature in _TEMPERATURE_CHOICES:
        if not permission_checker.has_permission(ctx, "settings:write"):
            audit.record_denied("authz.permission_denied", ctx=ctx,
                                target="settings.temperature",
                                detail={"required": "settings:write"}, request=request)
            raise HTTPException(403, "「创意度」是平台级设置，需要 settings:write 权限")
        if body.temperature != config.LLM_TEMPERATURE:
            audit.record("settings.llm_updated", "ok", user_id=ctx.user_id,
                         username=ctx.username, workspace_id=ctx.workspace_id,
                         target="settings:temperature",
                         detail={"changed_fields": ["temperature"],
                                 "temperature": body.temperature}, request=request)
            config.LLM_TEMPERATURE = body.temperature
            reset_llm()
            _persist_platform_temperature(db, body.temperature)
    prefs["temperature"] = config.LLM_TEMPERATURE
    _write_kv(db, user_preferences_key(ctx.user_id), prefs)

    from backend.agent.graph import invalidate_prefs_cache
    invalidate_prefs_cache()
    return prefs


def _persist_platform_temperature(db: Session, value: float) -> None:
    """把平台级创意度持久化到旧全局键（main.py 启动时据此恢复），跨重启生效。"""
    row = db.get(SystemSetting, K_PREFERENCES)
    payload = {}
    if row:
        try:
            payload = json.loads(row.value) or {}
        except Exception:
            payload = {}
    payload["temperature"] = value
    if row:
        row.value = json.dumps(payload, ensure_ascii=False)
        row.updated_at = now_str()
    else:
        db.add(SystemSetting(key=K_PREFERENCES,
                             value=json.dumps(payload, ensure_ascii=False)))
    db.commit()


def _write_kv(db: Session, key: str, value: dict) -> None:
    row = db.get(SystemSetting, key)
    payload = json.dumps(value, ensure_ascii=False)
    if row:
        row.value = payload
        row.updated_at = now_str()
    else:
        db.add(SystemSetting(key=key, value=payload))
    db.commit()
