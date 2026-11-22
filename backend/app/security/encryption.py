import os
import uuid
import base64
import logging
from typing import Tuple, Dict, Optional
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from app.config import DATA_DIR

logger = logging.getLogger("session_encryption")

FORMAT_VERSION = "v1"
KEY_FILE = DATA_DIR / ".session_master_key"


class SessionEncryptionError(Exception):
    """Base exception for session encryption failures."""
    pass


class SessionDecryptionError(SessionEncryptionError):
    """Raised when ciphertext decryption fails due to invalid key, tampered data, or tenant mismatch."""
    pass


class KeyNotFoundError(SessionEncryptionError):
    """Raised when the specified key version cannot be found."""
    pass


def _parse_candidate_key(var_name: str, val: str) -> bytes:
    """Parses and validates a candidate master key from an environment variable."""
    clean = val.strip()
    if not clean:
        raise SessionEncryptionError(f"{var_name} is set but empty.")

    if var_name == "SESSION_MASTER_KEY_B64":
        try:
            decoded = base64.b64decode(clean, validate=True)
            if len(decoded) == 32:
                return decoded
        except Exception:
            pass
        raise SessionEncryptionError(
            f"Invalid {var_name}: must be valid Base64 decoding to exactly 32 bytes (256 bits)."
        )

    if var_name == "SESSION_VAULT_MASTER_KEY":
        # 1. 64-character hex string
        if len(clean) == 64:
            try:
                decoded = bytes.fromhex(clean)
                if len(decoded) == 32:
                    return decoded
            except Exception:
                pass
        # 2. Base64 string
        try:
            decoded = base64.b64decode(clean, validate=True)
            if len(decoded) == 32:
                return decoded
        except Exception:
            pass
        # 3. Raw 32 UTF-8 bytes
        if len(clean.encode("utf-8")) == 32:
            return clean.encode("utf-8")
        raise SessionEncryptionError(
            f"Invalid {var_name}: must be 64-character hex or Base64 decoding to exactly 32 bytes."
        )

    if var_name == "SESSION_MASTER_KEY":
        if len(clean.encode("utf-8")) == 32:
            return clean.encode("utf-8")
        try:
            decoded = base64.b64decode(clean, validate=True)
            if len(decoded) == 32:
                return decoded
        except Exception:
            pass
        raise SessionEncryptionError(
            f"Invalid {var_name}: must be exactly 32 bytes."
        )

    raise SessionEncryptionError(f"Unknown key variable: {var_name}")


def _get_or_create_master_key() -> bytes:
    """
    Retrieves the 256-bit AES-GCM master key from environment or protected file.
    Canonical env var: SESSION_MASTER_KEY_B64 (base64-encoded 32 bytes).
    Compatibility alias: SESSION_VAULT_MASTER_KEY (hex-encoded 64 chars or base64).
    Enforces that if multiple aliases are set, they must decode to the same key;
    conflicting configurations are strictly rejected.
    Never persists to Git or logs.
    """
    candidates = {}
    for var in ("SESSION_MASTER_KEY_B64", "SESSION_VAULT_MASTER_KEY", "SESSION_MASTER_KEY"):
        val = os.getenv(var, "").strip()
        if val:
            candidates[var] = _parse_candidate_key(var, val)

    if candidates:
        first_var, first_key = next(iter(candidates.items()))
        for other_var, other_key in candidates.items():
            if other_key != first_key:
                raise SessionEncryptionError(
                    f"Conflicting master key configuration: {first_var} and {other_var} "
                    f"decode to different 32-byte keys. Aliases must decode to the same key or only one must be configured."
                )
        return first_key

    # Production gate: Enforce externally injected master key on production / Render
    is_production = (
        os.getenv("APP_ENV", "").lower() in ("production", "prod")
        or os.getenv("RENDER", "").lower() in ("true", "1")
        or os.getenv("ENVIRONMENT", "").lower() == "production"
    )
    if is_production:
        raise SessionEncryptionError(
            "CRITICAL: In production / Render deployment, encryption master key "
            "(SESSION_MASTER_KEY_B64 or SESSION_VAULT_MASTER_KEY) must be explicitly "
            "provided in environment variables. Generating an ephemeral master key is strictly prohibited "
            "as it causes permanent session loss on container restart."
        )

    # Fallback to local protected master key file (Development/Testing only)
    try:
        if KEY_FILE.exists():
            key_data = KEY_FILE.read_bytes()
            if len(key_data) == 32:
                return key_data
    except Exception:
        pass

    # Generate new 256-bit key
    new_key = AESGCM.generate_key(bit_length=256)
    try:
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_bytes(new_key)
        os.chmod(KEY_FILE, 0o600)
    except Exception as e:
        logger.warning(f"Could not persist local master key file (ephemeral container): {e}")

    return new_key


class KeyManager:
    """Manages versioned 256-bit keys for envelope encryption and rotation."""
    def __init__(self):
        self._keys: Dict[int, bytes] = {}
        primary_key = _get_or_create_master_key()
        self._keys[1] = primary_key

    def get_key(self, version: int = 1) -> bytes:
        if version not in self._keys:
            raise KeyNotFoundError(f"Key version {version} is not available.")
        return self._keys[version]

    def register_key(self, version: int, key_bytes: bytes) -> None:
        if len(key_bytes) != 32:
            raise ValueError("AES-256 key must be exactly 32 bytes.")
        self._keys[version] = key_bytes


_key_manager = KeyManager()


def get_key_manager() -> KeyManager:
    return _key_manager


def build_aad(tenant_id: uuid.UUID, telegram_account_id: uuid.UUID, key_version: int) -> bytes:
    """Constructs strict Authenticated Associated Data (AAD) for AES-GCM."""
    return f"{FORMAT_VERSION}:{str(tenant_id)}:{str(telegram_account_id)}:{key_version}".encode("utf-8")


def encrypt_session_string(
    plaintext_session: str,
    tenant_id: uuid.UUID,
    telegram_account_id: uuid.UUID,
    key_version: int = 1
) -> Tuple[bytes, bytes, int]:
    """
    Encrypts a Telethon StringSession using AES-256-GCM.
    Returns: (ciphertext, nonce, key_version)
    """
    if not plaintext_session or not plaintext_session.strip():
        raise ValueError("Cannot encrypt an empty or whitespace session string.")

    key = _key_manager.get_key(key_version)
    aesgcm = AESGCM(key)

    # 96-bit (12-byte) cryptographically secure random nonce
    nonce = os.urandom(12)
    aad = build_aad(tenant_id, telegram_account_id, key_version)

    ciphertext = aesgcm.encrypt(nonce, plaintext_session.strip().encode("utf-8"), aad)
    return ciphertext, nonce, key_version


def decrypt_session_string(
    ciphertext: bytes,
    nonce: bytes,
    tenant_id: uuid.UUID,
    telegram_account_id: uuid.UUID,
    key_version: int = 1
) -> str:
    """
    Decrypts a Telethon StringSession using AES-256-GCM.
    Fails closed if the key is wrong, data is corrupted, or tenant/account ID does not match AAD.
    """
    if not ciphertext or not nonce:
        raise SessionDecryptionError("Ciphertext and nonce cannot be empty.")

    key = _key_manager.get_key(key_version)
    aesgcm = AESGCM(key)
    aad = build_aad(tenant_id, telegram_account_id, key_version)

    try:
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, aad)
        decrypted_str = decrypted_bytes.decode("utf-8").strip()
        if not decrypted_str:
            raise SessionDecryptionError("Decrypted session string is empty.")
        return decrypted_str
    except InvalidTag as it:
        raise SessionDecryptionError(
            "Session decryption failed: authentication tag verification failed. "
            "Data may be tampered, encrypted with another key, or belonged to another tenant."
        ) from it
    except Exception as e:
        if isinstance(e, SessionDecryptionError):
            raise
        raise SessionDecryptionError(f"Unexpected session decryption error: {e}") from e
