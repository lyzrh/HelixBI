"""系统设置：LLM API 接口配置（运行时热更新）+ 个人资料（KV 存储）。"""

import json

from fastapi import APIRouter, Depends
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import config
from app.graph import get_llm, reset_llm
from backend.db import get_db
from backend.models import SystemSetting, now_str

router = APIRouter(prefix="/settings")

K_PROFILE = "user_profile"
DEFAULT_PROFILE = {"nickname": "数据探索者", "role": "数据分析师", "avatar_color": "#2563eb"}

K_PREFERENCES = "user_preferences"
DEFAULT_PREFERENCES = {
    "answer_style": "standard",        # concise | standard | detailed
    "temperature": 0.0,                # 创意度：0 精确 / 0.5 平衡 / 0.9 创意
    "followups_enabled": True,         # 回答后推荐追问
    "custom_instructions": "",         # 自定义指令（附加到 system prompt）
}
_TEMPERATURE_CHOICES = (0.0, 0.5, 0.9)
_STYLES = ("concise", "standard", "detailed")


def _read_kv(db: Session, key: str, defaults: dict) -> dict:
    """读 KV JSON 并合并默认值。"""
    data = {}
    row = db.get(SystemSetting, key)
    if row:
        try:
            data = json.loads(row.value)
        except Exception:
            data = {}
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
def get_llm_settings():
    return {
        "base_url": config.OPENAI_BASE_URL,
        "model": config.MODEL_NAME,
        "api_key_masked": mask_key(config.OPENAI_API_KEY),
        "configured": bool(config.OPENAI_API_KEY),
    }


@router.put("/llm")
def update_llm_settings(body: LlmSettingsBody):
    if not any([body.base_url, body.api_key, body.model]):
        return {"ok": True, "message": "没有需要保存的变更"}
    config.update_llm_config(body.base_url, body.api_key, body.model)
    reset_llm()
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
def test_llm(body: LlmTestBody):
    """测试连通性：用极小请求（max_tokens=1）验证，尽量省 token。

    传了字段则用传入值临时测试（不落盘）；否则用当前已保存配置。
    """
    base_url = (body.base_url or config.OPENAI_BASE_URL).strip()
    api_key = (body.api_key or "").strip() or config.OPENAI_API_KEY
    model = (body.model or config.MODEL_NAME).strip()
    if not api_key:
        return {"ok": False, "message": "缺少 API Key，无法测试"}

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


# ---- 个人资料 ----


class ProfileBody(BaseModel):
    nickname: str | None = None
    role: str | None = None
    avatar_color: str | None = None


@router.get("/profile")
def get_profile(db: Session = Depends(get_db)):
    return _read_kv(db, K_PROFILE, DEFAULT_PROFILE)


@router.put("/profile")
def update_profile(body: ProfileBody, db: Session = Depends(get_db)):
    profile = _read_kv(db, K_PROFILE, DEFAULT_PROFILE)
    if body.nickname is not None:
        profile["nickname"] = body.nickname.strip()[:20] or DEFAULT_PROFILE["nickname"]
    if body.role is not None:
        profile["role"] = body.role.strip()[:20] or DEFAULT_PROFILE["role"]
    if body.avatar_color is not None and body.avatar_color.startswith("#"):
        profile["avatar_color"] = body.avatar_color[:9]
    _write_kv(db, K_PROFILE, profile)
    return profile


# ---- 偏好设置（chat agent 通用偏好） ----


class PreferencesBody(BaseModel):
    answer_style: str | None = None        # concise | standard | detailed
    temperature: float | None = None       # 0 / 0.5 / 0.9
    followups_enabled: bool | None = None
    custom_instructions: str | None = None


@router.get("/preferences")
def get_preferences(db: Session = Depends(get_db)):
    return _read_kv(db, K_PREFERENCES, DEFAULT_PREFERENCES)


@router.put("/preferences")
def update_preferences(body: PreferencesBody, db: Session = Depends(get_db)):
    prefs = _read_kv(db, K_PREFERENCES, DEFAULT_PREFERENCES)
    if body.answer_style in _STYLES:
        prefs["answer_style"] = body.answer_style
    if body.temperature in _TEMPERATURE_CHOICES:
        prefs["temperature"] = body.temperature
    if body.followups_enabled is not None:
        prefs["followups_enabled"] = bool(body.followups_enabled)
    if body.custom_instructions is not None:
        prefs["custom_instructions"] = body.custom_instructions.strip()[:500]
    _write_kv(db, K_PREFERENCES, prefs)

    # 温度热更新 LLM 单例 + 图内偏好缓存失效
    from app import config
    config.LLM_TEMPERATURE = prefs["temperature"]
    reset_llm()
    from app.graph import invalidate_prefs_cache
    invalidate_prefs_cache()
    return prefs


def _write_kv(db: Session, key: str, value: dict) -> None:
    row = db.get(SystemSetting, key)
    payload = json.dumps(value, ensure_ascii=False)
    if row:
        row.value = payload
        row.updated_at = now_str()
    else:
        db.add(SystemSetting(key=key, value=payload))
    db.commit()
