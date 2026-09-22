"""数据源凭据的应用级加密（Secret Protection）。

## 为什么需要它

`DataSource.password` 原先明文入库：一旦 `data/app.db` 被拷走（备份、共享、误提交），
所有外部数据库口令直接泄露。本期把「落库」与「使用」两端收口：

- **写入**：只存密文，格式 `enc:v1:<iv>:<body>:<tag>`（带版本号，便于将来换算法）；
- **读取**：只在后端内部解密（`datasource/service.py` 建连接前），API 响应永不返回明文
  （`DataSource.to_dict()` 只给 `has_password` 布尔）；
- **密钥**：只从环境变量 `HELIX_SECRET_KEY` 读，不进数据库、不进 Git；
- **缺密钥**：**拒绝保存**并给出可操作的提示，绝不静默降级写明文（生产环境尤其如此）；
- **历史明文**：兼容读取（不破坏既有数据源），并在启动时一次性安全迁移为密文。

## 算法选择（诚实说明）

没有引入第三方密码学库（离线环境装不了 `cryptography`，项目 requirements 里也没有），
因此这里用标准库 `hmac` / `hashlib` 实现了一个**标准形状**的构造：

```
KDF   HKDF-SHA256（RFC 5869：extract + expand）→ enc_key(32B) + mac_key(32B)
加密  HMAC-SHA256 作为 PRF 的计数器模式密钥流（HMAC-CTR），IV 每次随机 16 字节
完整性 encrypt-then-MAC：tag = HMAC-SHA256(mac_key, "v1" || iv || ciphertext)
```

它给出「机密性 + 完整性」（篡改 / 换密钥 / 截断都会失败），但不做抗非随机 IV 的滥用防护。
**这是一处已知限制**：生产环境建议把 `encrypt_secret` / `decrypt_secret` 换成
`cryptography` 的 AES-GCM 或 Fernet——格式里已经带 `v1` 版本号，替换时只需多写一个分支，
历史密文仍可解。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

PREFIX = "enc:v1:"
SCHEME = b"v1"
INFO = b"helixbi/datasource-credential"
_IV_BYTES = 16
_TAG_BYTES = 32


class SecretsUnavailable(RuntimeError):
    """未配置主密钥（HELIX_SECRET_KEY）——拒绝加密/解密，绝不降级为明文。"""


class SecretDecryptError(RuntimeError):
    """密文损坏或被篡改（含换了主密钥的情况）。"""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def master_secret() -> bytes | None:
    """读主密钥：优先环境变量（进程启动后设置也生效），其次 config 快照。"""
    raw = (os.environ.get("HELIX_SECRET_KEY") or "").strip()
    if not raw:
        from backend import config

        raw = (getattr(config, "SECRET_KEY", "") or "").strip()
    return raw.encode("utf-8") if raw else None


def is_configured() -> bool:
    return master_secret() is not None


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(PREFIX)


def _derive_keys(master: bytes, iv: bytes) -> tuple[bytes, bytes]:
    """HKDF-SHA256：extract（HMAC(salt=iv, ikm=master)）→ expand 出两把 32 字节子密钥。"""
    prk = hmac.new(iv, master, hashlib.sha256).digest()
    enc_key = hmac.new(prk, INFO + b"\x01", hashlib.sha256).digest()
    mac_key = hmac.new(prk, INFO + b"\x02", hashlib.sha256).digest()
    return enc_key, mac_key


def _keystream(enc_key: bytes, iv: bytes, length: int) -> bytes:
    """HMAC-CTR 密钥流：block_i = HMAC(enc_key, iv || counter_i)。"""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(enc_key, iv + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, stream))


def encrypt_secret(plaintext: str | None) -> str:
    """明文 → `enc:v1:iv:body:tag`；空值原样返回（没有秘密就没什么可加密）。"""
    text = plaintext or ""
    if not text:
        return ""
    if is_encrypted(text):
        return text  # 幂等：已是密文不再套一层
    master = master_secret()
    if master is None:
        raise SecretsUnavailable(
            "未配置数据源凭据加密密钥：请设置环境变量 HELIX_SECRET_KEY 后重启"
            "（生成方式：python -c \"import secrets;print(secrets.token_urlsafe(48))\"）"
        )
    iv = secrets.token_bytes(_IV_BYTES)
    enc_key, mac_key = _derive_keys(master, iv)
    body = _xor(text.encode("utf-8"), _keystream(enc_key, iv, len(text.encode("utf-8"))))
    tag = hmac.new(mac_key, SCHEME + iv + body, hashlib.sha256).digest()
    return f"{PREFIX}{_b64(iv)}:{_b64(body)}:{_b64(tag)}"


def decrypt_secret(stored: str | None) -> str:
    """密文 → 明文。

    - 历史明文（无 `enc:v1:` 前缀）直接返回：兼容旧库，启动时的迁移会把它加密；
    - 密文校验失败（篡改 / 换密钥 / 缺密钥）抛异常，绝不返回半截内容。
    """
    value = stored or ""
    if not value or not is_encrypted(value):
        return value
    master = master_secret()
    if master is None:
        raise SecretsUnavailable("未配置 HELIX_SECRET_KEY，无法解密已加密的数据源凭据")
    try:
        iv_b64, body_b64, tag_b64 = value[len(PREFIX):].split(":")
        iv, body, tag = _unb64(iv_b64), _unb64(body_b64), _unb64(tag_b64)
    except Exception as exc:  # noqa: BLE001 — 格式错误与篡改同等对待
        raise SecretDecryptError("凭据密文格式非法") from exc
    if len(tag) != _TAG_BYTES or len(iv) != _IV_BYTES:
        raise SecretDecryptError("凭据密文长度异常")
    enc_key, mac_key = _derive_keys(master, iv)
    expected = hmac.new(mac_key, SCHEME + iv + body, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        # 换过密钥 / 被改动过都会走到这里；不暴露是哪一种（避免给攻击者反馈）
        raise SecretDecryptError("凭据解密失败：密文校验不通过（密钥变更或数据被篡改）")
    return _xor(body, _keystream(enc_key, iv, len(body))).decode("utf-8")


def migrate_plaintext_passwords(db, *, commit: bool = True) -> dict:
    """把历史明文口令一次性加密（幂等）。

    只有在配置了主密钥时才会执行；未配置时返回 `skipped` 原因而不是把明文留在库里
    变成"看起来迁移过"。返回 `{status, migrated, remaining, reason}`，可直接进日志。
    """
    from backend.models import DataSource

    if master_secret() is None:
        return {"status": "skipped", "migrated": 0, "remaining": 0,
                "reason": "未配置 HELIX_SECRET_KEY"}

    migrated, remaining = 0, 0
    rows = db.query(DataSource).filter(DataSource.password.isnot(None)).all()
    for ds in rows:
        value = ds.password or ""
        if not value:
            continue
        if is_encrypted(value):
            continue
        ds.password = encrypt_secret(value)
        migrated += 1
    if migrated and commit:
        db.commit()
    remaining = sum(
        1 for ds in db.query(DataSource).filter(DataSource.password.isnot(None)).all()
        if (ds.password or "") and not is_encrypted(ds.password)
    )
    return {"status": "ok", "migrated": migrated, "remaining": remaining, "reason": ""}


__all__ = ["PREFIX", "SecretDecryptError", "SecretsUnavailable", "decrypt_secret",
           "encrypt_secret", "is_configured", "is_encrypted", "master_secret",
           "migrate_plaintext_passwords"]
