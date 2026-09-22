"""安全事件审计（Observability / Audit）。

记录安全相关事件，供管理员排查"谁在什么时候做了什么、被拒了还是成功了"：

```
auth.login           登录（成功 / 失败）
auth.logout          主动登出（吊销 refresh token）
auth.refresh         刷新 access token（含重放检测触发的整族吊销）
auth.token_revoked   令牌被吊销（自助 revoke-all / 管理员吊销）
authz.permission_denied    缺少权限点被拒
authz.workspace_denied     跨工作区访问被拒
artifact.access_denied     产物 / 上传 / 缓存文件访问被拒
datasource.credential_saved    数据源凭据写入（只记"已加密"，不记内容）
datasource.credential_migrated 历史明文口令迁移为密文
settings.llm_updated   LLM 配置变更（只记字段名，不记值）
```

三条纪律：

1. **绝不记录秘密**：口令 / 数据库口令 / access token / refresh token / 加密密钥
   在 `redact()` 里按 key 名一律抹掉，长的可疑字符串也截断；
2. **审计独立于业务事务**：被拒的请求会回滚业务事务，所以这里用**独立 Session**
   写库（与 `backend/analysis/runtime.py` 的 worker 自己管会话同一思路），
   保证"拒绝"这件事一定留痕；
3. **审计失败不影响主流程**：任何异常都吞掉（审计是旁路），但要保证不会因为审计
   问题把正常的 401/403 变成 500。
"""

from __future__ import annotations

import json
from typing import Any

# 命中即整键抹除（大小写不敏感，含子串匹配）
SENSITIVE_KEY_PARTS = (
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "authorization", "credential", "private_key", "access_key",
)
REDACTED = "***"
MAX_STRING = 200


def _is_sensitive(key: Any) -> bool:
    text = str(key).lower()
    return any(part in text for part in SENSITIVE_KEY_PARTS)


def redact(value: Any, depth: int = 0) -> Any:
    """递归脱敏：敏感键抹除、超长字符串截断、非 JSON 类型字符串化。"""
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return {str(k): (REDACTED if _is_sensitive(k) else redact(v, depth + 1))
                for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact(v, depth + 1) for v in list(value)[:20]]
    if isinstance(value, str):
        return value if len(value) <= MAX_STRING else value[:MAX_STRING] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_STRING]


def _client_ip(request) -> str:
    if request is None:
        return ""
    try:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
        return (request.client.host if request.client else "")[:64]
    except Exception:  # noqa: BLE001
        return ""


def record(event: str, outcome: str = "ok", *, db=None, user_id: int | None = None,
           username: str = "", workspace_id: int | None = None, target: str = "",
           detail: dict | None = None, request=None, commit: bool = True) -> int | None:
    """写一条审计事件，返回行 id（失败返回 None，绝不抛异常）。

    `db` 传入时复用它（调用方自己控制事务），否则开一个**独立的** Session——
    被拒请求的业务事务会回滚，审计必须独立落库。
    """
    payload = {
        "event": event, "outcome": outcome, "user_id": user_id,
        "username": (username or "")[:64], "workspace_id": workspace_id,
        "target": (target or "")[:160], "detail": json.dumps(redact(detail or {}),
                                                             ensure_ascii=False),
        "ip": _client_ip(request),
    }
    try:
        from backend.models import AuditLog

        if db is not None:
            row = AuditLog(**payload)
            db.add(row)
            if commit:
                db.commit()
            else:
                db.flush()
            return row.id

        from backend.db import SessionLocal

        with SessionLocal() as own:
            row = AuditLog(**payload)
            own.add(row)
            own.commit()
            return row.id
    except Exception:  # noqa: BLE001 — 审计是旁路，绝不打断主流程
        return None


def record_denied(event: str, *, ctx=None, target: str = "", detail: dict | None = None,
                  request=None) -> int | None:
    """权限 / 工作区 / 产物访问被拒的统一入口（从 UserContext 提取身份）。"""
    info = ctx.to_dict() if hasattr(ctx, "to_dict") else (ctx or {})
    return record(event, "denied", user_id=info.get("user_id"),
                  username=info.get("username", ""),
                  workspace_id=info.get("workspace_id"),
                  target=target, detail=detail, request=request)


def recent(db, limit: int = 50, event: str = "", since_id: int = 0) -> list[dict]:
    """最近的安全事件（管理员查询用；支持按事件名前缀过滤）。"""
    from backend.models import AuditLog

    query = db.query(AuditLog)
    if event:
        query = query.filter(AuditLog.event.like(f"{event}%"))
    if since_id:
        query = query.filter(AuditLog.id > since_id)
    rows = query.order_by(AuditLog.id.desc()).limit(min(max(limit, 1), 200)).all()
    return [r.to_dict() for r in rows]


__all__ = ["REDACTED", "SENSITIVE_KEY_PARTS", "recent", "record", "record_denied", "redact"]
