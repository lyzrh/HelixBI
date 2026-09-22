"""数据源凭据加密测试：加解密正确性、密文落库、API 不回明文、迁移与缺密钥行为。

覆盖需求点名的数据源用例：密码 API 永不明文返回、库里确认是密文、加解密正确、
错误密钥 / 缺失密钥行为正确。
"""

import importlib
import os
import sys
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

MASTER_KEY = "unit-test-master-key-9f2c1b"


@pytest.fixture(autouse=True)
def master_key(monkeypatch):
    """单元级用例默认配好主密钥（缺失密钥的行为单独用 monkeypatch 验证）。"""
    monkeypatch.setenv("HELIX_SECRET_KEY", MASTER_KEY)
    yield


# ---- 纯函数：加解密 ----

def test_roundtrip_and_format():
    from backend.datasource import secrets as sec

    cipher = sec.encrypt_secret("p@ss-中文-123")
    assert cipher.startswith(sec.PREFIX)
    assert "p@ss" not in cipher
    assert sec.decrypt_secret(cipher) == "p@ss-中文-123"
    assert sec.is_encrypted(cipher) is True


def test_ciphertext_is_unique_per_call():
    """同一明文两次加密结果不同（随机 IV），避免密文可对比。"""
    from backend.datasource import secrets as sec

    assert sec.encrypt_secret("same") != sec.encrypt_secret("same")


def test_empty_and_none_are_passthrough():
    from backend.datasource import secrets as sec

    assert sec.encrypt_secret("") == "" and sec.decrypt_secret("") == ""
    assert sec.encrypt_secret(None) == ""
    assert sec.decrypt_secret(None) == ""


def test_encrypt_is_idempotent():
    from backend.datasource import secrets as sec

    once = sec.encrypt_secret("secret-value")
    assert sec.encrypt_secret(once) == once
    assert sec.decrypt_secret(once) == "secret-value"


def test_tampered_ciphertext_is_rejected():
    from backend.datasource import secrets as sec

    cipher = sec.encrypt_secret("value-1")
    for broken in (cipher[:-4] + "AAAA", cipher.replace("enc:v1:", "enc:v1:AA"),
                   cipher[:-1], cipher + "x"):
        with pytest.raises(sec.SecretDecryptError):
            sec.decrypt_secret(broken)


def test_wrong_key_fails_loudly(monkeypatch):
    from backend.datasource import secrets as sec

    cipher = sec.encrypt_secret("value-2")
    monkeypatch.setenv("HELIX_SECRET_KEY", "another-master-key")
    with pytest.raises(sec.SecretDecryptError):
        sec.decrypt_secret(cipher)


def test_missing_key_refuses_to_encrypt(monkeypatch):
    """没有密钥就**拒绝加密**（绝不静默降级成明文）。"""
    from backend.datasource import secrets as sec

    monkeypatch.delenv("HELIX_SECRET_KEY", raising=False)
    monkeypatch.setattr("backend.config.SECRET_KEY", "", raising=False)
    assert sec.is_configured() is False
    with pytest.raises(sec.SecretsUnavailable):
        sec.encrypt_secret("would-be-plaintext")


def test_missing_key_cannot_decrypt_ciphertext(monkeypatch):
    from backend.datasource import secrets as sec

    cipher = sec.encrypt_secret("value-3")
    monkeypatch.delenv("HELIX_SECRET_KEY", raising=False)
    monkeypatch.setattr("backend.config.SECRET_KEY", "", raising=False)
    with pytest.raises(sec.SecretsUnavailable):
        sec.decrypt_secret(cipher)


def test_legacy_plaintext_is_readable(monkeypatch):
    """历史明文兼容读取（真正的收口在启动迁移，不在这里抛错把老库打挂）。"""
    from backend.datasource import secrets as sec

    assert sec.decrypt_secret("legacy-plain-password") == "legacy-plain-password"


# ---- API + 落库 ----

@pytest.fixture(scope="module")
def api(tmp_path_factory):
    db_dir = tmp_path_factory.mktemp("sec_secret_db")
    os.environ["APP_DB_PATH"] = str(db_dir / "test_app.db")
    os.environ.setdefault("HELIX_SECRET_KEY", MASTER_KEY)

    import backend.config as config
    importlib.reload(config)
    import backend.db as db_mod
    importlib.reload(db_mod)

    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as c:
        yield c

    os.environ.pop("APP_DB_PATH", None)


@pytest.fixture(scope="module")
def db_session(api):
    from backend.db import SessionLocal

    with SessionLocal() as db:
        yield db


def _admin(api):
    r = api.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _h(token, ws=1):
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": str(ws)}


def test_db_password_never_returned_and_stored_encrypted(api, db_session, tmp_path):
    from backend.datasource import secrets as sec
    from backend.models import DataSource

    sqlite_file = tmp_path / "src.sqlite3"
    sqlite_file.write_bytes(b"")           # 空 sqlite 文件即可通过连接探测
    token = _admin(api)
    name = f"cred-{uuid.uuid4().hex[:6]}"
    resp = api.post("/api/datasources/db", headers=_h(token), json={
        "name": name,
        "config": {"db_type": "sqlite", "database": str(sqlite_file),
                   "sqlite_path": str(sqlite_file), "username": "u",
                   "password": "S3cret-DB-Pass"},
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # API 不回明文，只给布尔
    assert "password" not in body
    assert body["has_password"] is True

    row = db_session.query(DataSource).filter(DataSource.name == name).first()
    assert row.password.startswith(sec.PREFIX)
    assert "S3cret-DB-Pass" not in row.password
    # 后端内部仍能解出原文（连接用）
    from backend.datasource.service import connection_password

    assert connection_password(row) == "S3cret-DB-Pass"
    # 列表接口同样不含明文
    listed = api.get("/api/datasources", headers=_h(token)).json()
    assert "S3cret-DB-Pass" not in str(listed)


def test_create_db_source_without_key_refuses_to_store(api, db_session, tmp_path,
                                                       monkeypatch):
    """缺密钥 → 明确报错且**不写入任何凭据行**（生产环境同样不静默降级）。"""
    from backend.datasource import secrets as sec
    from backend.models import DataSource

    monkeypatch.delenv("HELIX_SECRET_KEY", raising=False)
    monkeypatch.setattr("backend.config.SECRET_KEY", "", raising=False)
    try:
        sqlite_file = tmp_path / "src2.sqlite3"
        sqlite_file.write_bytes(b"")
        token = _admin(api)
        name = f"nokey-{uuid.uuid4().hex[:6]}"
        resp = api.post("/api/datasources/db", headers=_h(token), json={
            "name": name,
            "config": {"db_type": "sqlite", "database": str(sqlite_file),
                       "sqlite_path": str(sqlite_file), "username": "u",
                       "password": "plaintext-should-not-be-stored"},
        })
        assert resp.status_code in (400, 500), resp.text
        assert "HELIX_SECRET_KEY" in resp.json()["detail"]
        assert db_session.query(DataSource).filter(DataSource.name == name).first() is None
    finally:
        monkeypatch.setenv("HELIX_SECRET_KEY", MASTER_KEY)
    assert sec.is_configured() is True


def test_credential_saved_event_is_audited_without_secret(api, db_session):
    token = _admin(api)
    items = api.get("/api/auth/audit?event=datasource&limit=50", headers=_h(token)).json()["items"]
    assert any(e["event"] == "datasource.credential_saved" for e in items), items[:3]
    assert "S3cret-DB-Pass" not in str(items)


def test_migration_encrypts_legacy_plaintext(db_session):
    from backend.datasource import secrets as sec
    from backend.models import DataSource

    legacy = DataSource(name=f"legacy-{uuid.uuid4().hex[:6]}", type="db",
                        db_type="sqlite", password="legacy-cleartext",
                        workspace_id=1)
    db_session.add(legacy)
    db_session.commit()
    assert not sec.is_encrypted(legacy.password)

    result = sec.migrate_plaintext_passwords(db_session)
    assert result["status"] == "ok" and result["migrated"] >= 1 and result["remaining"] == 0
    db_session.refresh(legacy)
    assert sec.is_encrypted(legacy.password)
    assert "legacy-cleartext" not in legacy.password
    assert sec.decrypt_secret(legacy.password) == "legacy-cleartext"

    # 幂等：再跑一次不会重复加密 / 不会破坏已有密文
    again = sec.migrate_plaintext_passwords(db_session)
    assert again["migrated"] == 0
    db_session.refresh(legacy)
    assert sec.decrypt_secret(legacy.password) == "legacy-cleartext"


def test_migration_skips_without_key(db_session, monkeypatch):
    from backend.datasource import secrets as sec
    from backend.models import DataSource

    row = DataSource(name=f"legacy2-{uuid.uuid4().hex[:6]}", type="db",
                     db_type="sqlite", password="still-cleartext", workspace_id=1)
    db_session.add(row)
    db_session.commit()

    monkeypatch.delenv("HELIX_SECRET_KEY", raising=False)
    monkeypatch.setattr("backend.config.SECRET_KEY", "", raising=False)
    result = sec.migrate_plaintext_passwords(db_session)
    assert result["status"] == "skipped" and "HELIX_SECRET_KEY" in result["reason"]
    db_session.refresh(row)
    assert row.password == "still-cleartext"     # 未配置密钥时保持原样，而不是假装迁移

    monkeypatch.setenv("HELIX_SECRET_KEY", MASTER_KEY)
    assert sec.migrate_plaintext_passwords(db_session)["migrated"] >= 1
    db_session.refresh(row)
    assert sec.is_encrypted(row.password)


def test_connection_password_handles_missing_key(monkeypatch, db_session):
    """已加密的凭据在缺密钥时应报明确错误，而不是把密文当口令去连库。"""
    from backend.datasource import secrets as sec
    from backend.datasource.service import connection_password
    from backend.models import DataSource

    row = DataSource(name=f"enc-{uuid.uuid4().hex[:6]}", type="db", db_type="sqlite",
                     password=sec.encrypt_secret("real-pass"), workspace_id=1)
    db_session.add(row)
    db_session.commit()
    assert connection_password(row) == "real-pass"

    monkeypatch.delenv("HELIX_SECRET_KEY", raising=False)
    monkeypatch.setattr("backend.config.SECRET_KEY", "", raising=False)
    with pytest.raises(sec.SecretsUnavailable):
        connection_password(row)
