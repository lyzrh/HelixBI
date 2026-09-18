"""口令哈希与 JWT（stdlib 实现，零新增依赖）。

- 口令：pbkdf2_hmac-SHA256，格式 pbkdf2_sha256$iterations$salt_hex$hash_hex。
- JWT：HS256 手写三段式 token（header.payload.signature），仅本服务签发与
  校验；若未来需要更多 JWT 特性可平滑替换为 PyJWT（接口保持一致）。
- 密钥：环境变量 HELIX_JWT_SECRET，未设置时启动期生成随机密钥（单进程
  内存内一致；多进程部署必须显式配置）。
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

PBKDF2_ITERATIONS = 120_000
TOKEN_TTL_SECONDS = 12 * 3600  # 12 小时

_SECRET = os.getenv("HELIX_JWT_SECRET", "")

if not _SECRET:
    # 单进程开发/单机部署场景：进程内生成一次即可（uvicorn --reload 的
    # 子进程各自生成也不影响 token 生命周期短于进程寿命的常态使用）。
    _SECRET = secrets.token_hex(32)


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


def create_token(user_id: int, username: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    """签发 JWT：只放身份（user_id/username），不放权限——权限每次请求时
    由后端根据数据库实时解析，避免 token 内权限过期问题。"""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": user_id, "username": username, "iat": now, "exp": now + ttl}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "." + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    )
    sig = hmac.new(_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64url(sig)


def decode_token(token: str) -> dict | None:
    """校验签名与有效期，返回 payload；任何失败返回 None。"""
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
        return payload
    except Exception:
        return None
