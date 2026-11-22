import os
import base64
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.security.encryption import _get_or_create_master_key, SessionEncryptionError
from app.auth_v3.identity_service import IdentityService

def test_canonical_master_key_b64(monkeypatch):
    raw_key = os.urandom(32)
    b64_key = base64.b64encode(raw_key).decode("utf-8")
    monkeypatch.setenv("SESSION_MASTER_KEY_B64", b64_key)
    monkeypatch.delenv("SESSION_VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("SESSION_MASTER_KEY", raising=False)
    
    key = _get_or_create_master_key()
    assert key == raw_key
    assert len(key) == 32

def test_compatibility_alias_hex(monkeypatch):
    raw_key = os.urandom(32)
    hex_key = raw_key.hex()
    monkeypatch.delenv("SESSION_MASTER_KEY_B64", raising=False)
    monkeypatch.setenv("SESSION_VAULT_MASTER_KEY", hex_key)
    monkeypatch.delenv("SESSION_MASTER_KEY", raising=False)
    
    key = _get_or_create_master_key()
    assert key == raw_key
    assert len(key) == 32

def test_compatibility_alias_b64(monkeypatch):
    raw_key = os.urandom(32)
    b64_key = base64.b64encode(raw_key).decode("utf-8")
    monkeypatch.delenv("SESSION_MASTER_KEY_B64", raising=False)
    monkeypatch.setenv("SESSION_VAULT_MASTER_KEY", b64_key)
    monkeypatch.delenv("SESSION_MASTER_KEY", raising=False)
    
    key = _get_or_create_master_key()
    assert key == raw_key
    assert len(key) == 32

def test_production_fails_closed_when_keys_missing(monkeypatch):
    monkeypatch.delenv("SESSION_MASTER_KEY_B64", raising=False)
    monkeypatch.delenv("SESSION_VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("SESSION_MASTER_KEY", raising=False)
    monkeypatch.setenv("RENDER", "true")
    
    with pytest.raises(SessionEncryptionError) as exc_info:
        _get_or_create_master_key()
    assert "In production / Render deployment" in str(exc_info.value)

def test_password_hashing_algorithm_is_pbkdf2_sha256():
    svc = IdentityService()
    pw = "SuperSecurePassword123!"
    pw_hash = svc.hash_password(pw)
    
    # Verify algorithm format: pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
    parts = pw_hash.split("$")
    assert len(parts) == 4
    assert parts[0] == "pbkdf2_sha256"
    assert int(parts[1]) == 600000  # OWASP standard 600k iterations
    assert len(bytes.fromhex(parts[2])) == 16  # 16-byte random salt
    assert len(bytes.fromhex(parts[3])) == 32  # 32-byte HMAC-SHA256 key
    
    assert svc.verify_password(pw, pw_hash) is True
    assert svc.verify_password("wrong_password", pw_hash) is False
