"""刷新令牌：签发 / 轮换 / 吊销 / 重放检测（Token Lifecycle）。

## 策略（明确写下来，避免"看起来更安全"的随意设计）

| 项 | 策略 |
| --- | --- |
| access token | JWT，短期（默认 1h，`HELIX_ACCESS_TOKEN_TTL`），只带身份 + `typ=access` |
| refresh token | **不透明随机串**（48 字节），库里只存 `sha256`；默认 30 天（`HELIX_REFRESH_TOKEN_TTL`） |
| 轮换 | 每次 `/auth/refresh` 吊销旧行、签发新行，`replaced_by` 记录链；同一族（`family_id`）表示一次登录会话 |
| 重放 | 拿**已被轮换**的旧 refresh token 再来 → 判定重放：整族吊销（`revoked_reason="replay"`）并写审计 |
| 撤销 | 登出吊销当前令牌；`revoke-all` 吊销本人全部；管理员可吊销指定用户的全部 |
| 过期 | 到期即失效（`expires_at`），由数据库判定，不依赖进程内存 |
| 多进程 | 状态全在 DB（`refresh_tokens` 表），任意进程/实例看到同一结论 |

**refresh token 不可能当 access token 用**：它不是 JWT，`decode_token` 直接失败；
即便有人把 refresh 语义包进 JWT，`decode_token(expected_type="access")` 也会因
`typ` 不匹配而拒绝。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.auth import audit
from backend.auth.security import new_refresh_token, hash_refresh_token

# 失效原因码（进审计与响应，不暴露内部细节之外的敏感信息）
REASON_PROACTIVE = "proactive"     # 轮换（正常）
REASON_LOGOUT = "logout"
REASON_REVOKE_ALL = "revoke_all"
REASON_ADMIN = "admin_revoked"
REASON_REPLAY = "replay"
REASON_EXPIRED = "expired"


class TokenError(Exception):
    """refresh 流程的可预期失败（由 API 层转 401）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now()


def _fmt(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def refresh_ttl_seconds() -> int:
    from backend import config

    return int(getattr(config, "REFRESH_TOKEN_TTL_SECONDS", 30 * 24 * 3600)
               or 30 * 24 * 3600)


def issue_refresh(db, user_id: int, *, family_id: str | None = None,
                  workspace_id: int | None = None, user_agent: str = "",
                  commit: bool = True):
    """签发 refresh token → (明文 token, RefreshToken 行)。

    明文只在这一次响应里返回；库里只有 `sha256`，之后无法反查。
    """
    from backend.models import RefreshToken

    raw = new_refresh_token()
    row = RefreshToken(
        user_id=user_id,
        token_hash=hash_refresh_token(raw),
        family_id=family_id or new_refresh_token()[:32],
        workspace_id=workspace_id,
        user_agent=(user_agent or "")[:120],
        issued_at=_fmt(_now()),
        expires_at=_fmt(_now() + timedelta(seconds=refresh_ttl_seconds())),
    )
    db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return raw, row


def _find(db, raw_token: str):
    from backend.models import RefreshToken

    if not raw_token:
        return None
    return (db.query(RefreshToken)
            .filter(RefreshToken.token_hash == hash_refresh_token(raw_token)).first())


def revoke_family(db, family_id: str, reason: str) -> int:
    """吊销整族（同一个 refresh token 链的全部令牌）。"""
    from backend.models import RefreshToken

    if not family_id:
        return 0
    rows = (db.query(RefreshToken)
            .filter(RefreshToken.family_id == family_id,
                    RefreshToken.revoked_at.is_(None)).all())
    stamp = _fmt(_now())
    for row in rows:
        row.revoked_at = stamp
        row.revoked_reason = reason
    db.commit()
    return len(rows)


def revoke(db, raw_token: str, reason: str) -> bool:
    """吊销单个 refresh token（登出）；不存在或已吊销返回 False（幂等，不报错）。"""
    row = _find(db, raw_token)
    if not row or row.revoked_at:
        return False
    row.revoked_at = _fmt(_now())
    row.revoked_reason = reason
    db.commit()
    return True


def revoke_all_for_user(db, user_id: int, reason: str,
                        workspace_id: int | None = None) -> int:
    """吊销某用户全部有效 refresh token（改密 / 停用 / 管理员强制下线）。"""
    from backend.models import RefreshToken

    query = db.query(RefreshToken).filter(RefreshToken.user_id == user_id,
                                          RefreshToken.revoked_at.is_(None))
    if workspace_id is not None:
        query = query.filter(RefreshToken.workspace_id == workspace_id)
    rows = query.all()
    stamp = _fmt(_now())
    for row in rows:
        row.revoked_at = stamp
        row.revoked_reason = reason
    db.commit()
    return len(rows)


def rotate(db, raw_token: str, *, user_agent: str = "", request=None):
    """用 refresh token 换一组新令牌：`(access_token, refresh_token, row)`。

    失败一律抛 `TokenError`（含原因码），并把事件写进审计。重放会牵连整族。
    """
    from backend.models import User

    row = _find(db, raw_token)
    if not row:
        audit.record("auth.refresh", "failed", detail={"reason": "unknown_token"},
                     request=request)
        raise TokenError("invalid_refresh_token", "刷新令牌无效，请重新登录")

    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        revoke_family(db, row.family_id, REASON_ADMIN)
        audit.record("auth.refresh", "denied", user_id=row.user_id,
                     username=getattr(user, "username", ""),
                     detail={"reason": "user_inactive"}, request=request)
        raise TokenError("user_inactive", "账号不可用，请联系管理员")

    if row.revoked_at:
        if row.revoked_reason == REASON_PROACTIVE:
            # 已被轮换的旧令牌再次出现 → 视为泄露重放：整族作废（标准做法）
            revoked = revoke_family(db, row.family_id, REASON_REPLAY)
            audit.record("auth.token_replay", "denied", user_id=row.user_id,
                         username=user.username, workspace_id=row.workspace_id,
                         target=f"family:{row.family_id}",
                         detail={"revoked_tokens": revoked}, request=request)
            raise TokenError("refresh_token_replay",
                             "检测到刷新令牌重复使用，已吊销该会话全部令牌，请重新登录")
        audit.record("auth.refresh", "denied", user_id=row.user_id,
                     username=user.username, detail={"reason": row.revoked_reason},
                     request=request)
        raise TokenError("refresh_token_revoked", "刷新令牌已失效，请重新登录")

    if row.expires_at < _fmt(_now()):
        row.revoked_at = _fmt(_now())
        row.revoked_reason = REASON_EXPIRED
        db.commit()
        audit.record("auth.refresh", "denied", user_id=row.user_id,
                     username=user.username, detail={"reason": "expired"},
                     request=request)
        raise TokenError("refresh_token_expired", "登录已过期，请重新登录")

    # 轮换：旧行标记 replaced_by，新行继承 family_id（同一条登录链）
    new_raw, new_row = issue_refresh(db, row.user_id, family_id=row.family_id,
                                     workspace_id=row.workspace_id,
                                     user_agent=user_agent, commit=False)
    row.revoked_at = _fmt(_now())
    row.revoked_reason = REASON_PROACTIVE
    row.replaced_by = new_row.token_hash
    db.commit()

    from backend.auth.security import create_token

    access = create_token(user.id, user.username)
    audit.record("auth.refresh", "ok", user_id=user.id, username=user.username,
                 workspace_id=row.workspace_id, target=f"family:{row.family_id}",
                 request=request)
    return access, new_raw, new_row


def active_sessions(db, user_id: int, limit: int = 50) -> list[dict]:
    """某用户当前有效的登录会话（刷新令牌族），供"我的登录 / 管理员查看"使用。"""
    from backend.models import RefreshToken

    rows = (db.query(RefreshToken)
            .filter(RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None))
            .order_by(RefreshToken.id.desc()).limit(limit).all())
    out = []
    for row in rows:
        out.append({"id": row.id, "family_id": row.family_id,
                    "workspace_id": row.workspace_id,
                    "issued_at": row.issued_at, "expires_at": row.expires_at,
                    "user_agent": row.user_agent})
    return out


__all__ = ["REASON_ADMIN", "REASON_EXPIRED", "REASON_LOGOUT", "REASON_PROACTIVE",
           "REASON_REPLAY", "REASON_REVOKE_ALL", "TokenError", "active_sessions",
           "issue_refresh", "refresh_ttl_seconds", "revoke", "revoke_all_for_user",
           "revoke_family", "rotate"]
