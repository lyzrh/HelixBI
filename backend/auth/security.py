"""口令哈希与 JWT（stdlib 实现，零新增依赖）。

- 口令：pbkdf2_hmac-SHA256，格式 pbkdf2_sha256$iterations$salt_hex$hash_hex。
- JWT：HS256 手写三段式 token（header.payload.signature），仅本服务签发与
  校验；若未来需要更多 JWT 特性可平滑替换为 PyJWT（接口保持一致）。
- 密钥：环境变量 HELIX_JWT_SECRET，未设置时启动期生成随机密钥（单进程
  内存内一致；多进程部署必须显式配置）。

Token 生命周期（Security Hardening V1）：

- **access token**：短期（默认 1 小时，`HELIX_ACCESS_TOKEN_TTL`），载荷带 `typ="access"`；
  业务接口只接受 access token——校验时强制比对 `typ`，任何其它类型一律拒绝。
- **refresh token**：**不是 JWT，而是随机不可读字符串**，只存哈希在数据库里
  （见 `backend/auth/tokens.py`），因此它天生不可能被当成 access token 用去访问业务 API。
- 令牌里**只放身份**（`sub` / `username`），不放角色与权限：权限每次请求由后端
  按 `User → WorkspaceMember → Role → Permission` 实时解析。
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

PBKDF2_ITERATIONS = 120_000
# 历史常量（保留兼容）：现在以 config.ACCESS_TOKEN_TTL_SECONDS 为准，见 access_ttl()
TOKEN_TTL_SECONDS = 12 * 3600
ACCESS_TYP = "access"
REFRESH_TYP = "refresh"

_SECRET = os.getenv("HELIX_JWT_SECRET", "")

if not _SECRET:
    # 单进程开发/单机部署场景：进程内生成一次即可（uvicorn --reload 的
    # 子进程各自生成也不影响 token 生命周期短于进程寿命的常态使用）。
    _SECRET = secrets.token_hex(32)


def access_ttl() -> int:
    """access token 有效期（秒）：动态读 config，设置页/测试改配置即时生效。"""
    from backend import config

    return int(getattr(config, "ACCESS_TOKEN_TTL_SECONDS", TOKEN_TTL_SECONDS) or
               TOKEN_TTL_SECONDS)


# ---- 口令 ----

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                 PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except Exception:
        return False


# ---- JWT（HS256）----

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def create_token(user_id: int, username: str, ttl: int | None = None,
                 typ: str = ACCESS_TYP) -> str:
    """签发 access JWT：只放身份（user_id/username）+ 类型标记，不放权限。

    `typ` 参与签名，校验端会强制比对——因此把 refresh 语义塞进 JWT 也走不通。
    """
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": user_id, "username": username, "iat": now,
               "exp": now + (ttl if ttl is not None else access_ttl()), "typ": typ}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "." + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    sig = hmac.new(_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64url(sig)


def decode_token(token: str, expected_type: str | None = ACCESS_TYP) -> dict | None:
    """校验签名、有效期与 token 类型，返回 payload；任何失败返回 None。

    `expected_type=None` 用于极少数需要读原始载荷的场景；业务接口一律用默认值
    （只认 access），保证 refresh 之类其它类型的 token 不能直接访问业务 API。
    """
    try:
        signing_input, sig_b64 = token.rsplit(".", 1)
        expected = hmac.new(_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            return None
        header_b64, payload_b64 = signing_input.split(".", 1)
        header = json.loads(_b64url_decode(header_b64))
        if header.get("alg") != "HS256":
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        if int(payload.get("exp", 0)) < time.time():
            return None
        if expected_type is not None and payload.get("typ") != expected_type:
            return None
        return payload
    except Exception:
        return None


# ---- refresh token（不透明随机串，只存哈希）----

def new_refresh_token() -> str:
    """生成 refresh token：48 字节 URL-safe 随机串（不可解析、不可伪造）。"""
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw: str) -> str:
    """入库前哈希：即使数据库泄露也无法直接拿来换 access token。"""
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()

