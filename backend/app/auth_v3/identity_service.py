import os
import json
import time
import uuid
import secrets
import hashlib
import hmac
import logging
from typing import Optional, Tuple, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
import webauthn
from webauthn.helpers.structs import (
    PublicKeyCredentialCreationOptions,
    PublicKeyCredentialRequestOptions,
    AuthenticatorSelectionCriteria,
    UserVerificationRequirement,
    ResidentKeyRequirement,
    PublicKeyCredentialDescriptor,
)

from app.db.models.user import AppUser, LoginCredential
from app.db.repositories.user_repo import UserRepository
from app.db.repositories.audit_repo import AuditRepository

logger = logging.getLogger("identity_service")

# Rate limiting settings
MAX_FAILED_ATTEMPTS = int(os.getenv("MAX_FAILED_LOGIN_ATTEMPTS", "5"))
LOCKOUT_WINDOW_SECONDS = int(os.getenv("LOGIN_LOCKOUT_WINDOW_SECS", "900"))  # 15 minutes

# WebAuthn settings
WEBAUTHN_RP_ID = os.getenv("WEBAUTHN_RP_ID", "localhost")
WEBAUTHN_RP_NAME = os.getenv("WEBAUTHN_RP_NAME", "TG Power Suite")
WEBAUTHN_ORIGIN = os.getenv("WEBAUTHN_ORIGIN", "http://localhost:3000")
ADMIN_BOOTSTRAP_TOKEN = os.getenv("ADMIN_BOOTSTRAP_TOKEN", "").strip()


def get_or_generate_bootstrap_token() -> str:
    """
    Returns configured ADMIN_BOOTSTRAP_TOKEN or reads/generates a local random token
    saved with restricted POSIX permissions.
    """
    env_token = os.getenv("ADMIN_BOOTSTRAP_TOKEN", "").strip()
    if env_token:
        return env_token
    from app.config import DATA_DIR
    token_file = DATA_DIR / "admin_bootstrap_token.txt"
    try:
        if token_file.exists():
            content = token_file.read_text().strip()
            if content:
                return content
    except Exception:
        pass
    generated = secrets.token_hex(24)
    try:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(generated)
        os.chmod(token_file, 0o600)
        logger.info(f"Generated administrator bootstrap token at {token_file}")
    except Exception as e:
        logger.warning(f"Could not persist admin bootstrap token to file: {e}")
    return generated


class RateLimitError(Exception):
    """Raised when maximum login attempts are exceeded."""
    pass


class AuthenticationError(Exception):
    """Raised for invalid credentials."""
    pass


class IdentityService:
    # Memory store for active WebAuthn challenges: {challenge_id: (challenge_bytes, expires_at)}
    _challenges: Dict[str, Tuple[bytes, float]] = {}
    # Memory store for failed login attempts: {identifier_key: [(timestamp)]}
    _failed_attempts: Dict[str, List[float]] = {}

    @classmethod
    def _is_rate_limited(cls, key: str) -> bool:
        now = time.time()
        cls._cleanup_rate_limits()
        attempts = cls._failed_attempts.get(key, [])
        valid_attempts = [t for t in attempts if now - t < LOCKOUT_WINDOW_SECONDS]
        cls._failed_attempts[key] = valid_attempts
        return len(valid_attempts) >= MAX_FAILED_ATTEMPTS

    @classmethod
    def _record_failed_attempt(cls, key: str) -> None:
        now = time.time()
        if key not in cls._failed_attempts:
            cls._failed_attempts[key] = []
        cls._failed_attempts[key].append(now)

    @classmethod
    def _reset_failed_attempts(cls, key: str) -> None:
        cls._failed_attempts.pop(key, None)

    @classmethod
    def _cleanup_rate_limits(cls) -> None:
        now = time.time()
        for k in list(cls._failed_attempts.keys()):
            valid = [t for t in cls._failed_attempts[k] if now - t < LOCKOUT_WINDOW_SECONDS]
            if not valid:
                cls._failed_attempts.pop(k, None)
            else:
                cls._failed_attempts[k] = valid

    @classmethod
    def _save_challenge(cls, user_key: str, challenge: bytes, ttl: int = 300) -> None:
        cls._cleanup_challenges()
        cls._challenges[user_key] = (challenge, time.time() + ttl)

    @classmethod
    def _consume_challenge(cls, user_key: str) -> Optional[bytes]:
        cls._cleanup_challenges()
        entry = cls._challenges.pop(user_key, None)
        if not entry:
            return None
        challenge, expires_at = entry
        if time.time() > expires_at:
            return None
        return challenge

    @classmethod
    def _cleanup_challenges(cls) -> None:
        now = time.time()
        for k in list(cls._challenges.keys()):
            if cls._challenges[k][1] < now:
                cls._challenges.pop(k, None)

    # ------------------ Password Hashing ------------------

    @staticmethod
    def hash_password(password: str) -> str:
        salt = secrets.token_bytes(16)
        iterations = 600_000
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return f"pbkdf2_sha256${iterations}${salt.hex()}${key.hex()}"

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        try:
            parts = hashed.split("$")
            if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
                return False
            iterations = int(parts[1])
            salt = bytes.fromhex(parts[2])
            expected_key = bytes.fromhex(parts[3])
            actual_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(expected_key, actual_key)
        except Exception:
            return False

    @staticmethod
    def generate_recovery_codes(count: int = 8) -> Tuple[List[str], List[str]]:
        """Generates random recovery codes and their SHA-256 hashes."""
        plain_codes = [f"{secrets.token_hex(3).upper()}-{secrets.token_hex(3).upper()}" for _ in range(count)]
        hashed_codes = [hashlib.sha256(c.encode("utf-8")).hexdigest() for c in plain_codes]
        return plain_codes, hashed_codes

    # ------------------ User Registration & Verification ------------------

    @classmethod
    async def register_user(
        cls,
        session: AsyncSession,
        username: str,
        password: Optional[str] = None,
        bootstrap_token: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> Tuple[AppUser, List[str]]:
        user_repo = UserRepository(session)
        audit_repo = AuditRepository(session)

        # Check if username already exists
        existing = await user_repo.get_by_display_name(username)
        if existing:
            raise ValueError(f"Username '{username}' is already taken.")

        # Determine user role with strict bootstrap verification
        has_admin = await user_repo.has_admin()
        expected_token = get_or_generate_bootstrap_token()
        role = "user"

        if bootstrap_token:
            if has_admin:
                raise ValueError("Administrator account already bootstrapped.")
            if expected_token and hmac.compare_digest(bootstrap_token.strip(), expected_token):
                role = "admin"
            else:
                raise ValueError("Invalid admin bootstrap token.")

        user = await user_repo.create_user(
            display_name=username,
            status="active",
            role=role
        )

        recovery_codes: List[str] = []
        if password:
            hashed_pwd = cls.hash_password(password)
            cred_payload = json.dumps({"hash": hashed_pwd})
            await user_repo.add_credential(user.id, "password_hash", cred_payload)

            # Generate recovery codes
            recovery_codes, hashed_codes = cls.generate_recovery_codes()
            recovery_payload = json.dumps({"codes": hashed_codes})
            await user_repo.add_credential(user.id, "recovery_codes", recovery_payload)

        await audit_repo.log_event(
            event_type="user.registered",
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"username": username, "role": role}
        )

        return user, recovery_codes

    @classmethod
    async def authenticate_password(
        cls,
        session: AsyncSession,
        username: str,
        password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> AppUser:
        rate_key = f"{username}:{ip_address or 'unknown'}"
        if cls._is_rate_limited(rate_key):
            raise RateLimitError("Too many failed login attempts. Please try again in 15 minutes.")

        user_repo = UserRepository(session)
        audit_repo = AuditRepository(session)

        user = await user_repo.get_by_display_name(username)
        if not user or user.status != "active":
            cls._record_failed_attempt(rate_key)
            await audit_repo.log_event(
                event_type="auth.failed",
                ip_address=ip_address,
                user_agent=user_agent,
                metadata={"username": username, "reason": "user_not_found_or_disabled"}
            )
            raise AuthenticationError("Invalid username or password.")

        credentials = await user_repo.get_credentials(user.id, "password_hash")
        if not credentials:
            cls._record_failed_attempt(rate_key)
            raise AuthenticationError("Password login not configured for this account.")

        authenticated = False
        for cred in credentials:
            try:
                data = json.loads(cred.credential_data)
                hashed = data.get("hash", "")
                if cls.verify_password(password, hashed):
                    authenticated = True
                    break
            except Exception:
                continue

        if not authenticated:
            cls._record_failed_attempt(rate_key)
            await audit_repo.log_event(
                event_type="auth.failed",
                user_id=user.id,
                ip_address=ip_address,
                user_agent=user_agent,
                metadata={"username": username, "reason": "password_mismatch"}
            )
            raise AuthenticationError("Invalid username or password.")

        # Success - reset rate limit and log
        cls._reset_failed_attempts(rate_key)
        await audit_repo.log_event(
            event_type="auth.success",
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"username": username, "method": "password"}
        )

        return user

    # ------------------ WebAuthn / Passkeys ------------------

    @classmethod
    async def get_passkey_registration_options(
        cls,
        session: AsyncSession,
        user_id: uuid.UUID
    ) -> Dict[str, Any]:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_id(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found.")

        # Get existing passkeys to exclude re-registration
        credentials = await user_repo.get_credentials(user.id, "passkey")
        exclude_credentials: List[PublicKeyCredentialDescriptor] = []
        for cred in credentials:
            try:
                data = json.loads(cred.credential_data)
                cred_id_bytes = bytes.fromhex(data["credential_id"])
                exclude_credentials.append(PublicKeyCredentialDescriptor(id=cred_id_bytes))
            except Exception:
                continue

        options: PublicKeyCredentialCreationOptions = webauthn.generate_registration_options(
            rp_id=WEBAUTHN_RP_ID,
            rp_name=WEBAUTHN_RP_NAME,
            user_id=user.id.bytes,
            user_name=user.display_name or "user",
            user_display_name=user.display_name or "TG Power Suite User",
            authenticator_selection=AuthenticatorSelectionCriteria(
                user_verification=UserVerificationRequirement.PREFERRED,
                resident_key=ResidentKeyRequirement.PREFERRED
            ),
            exclude_credentials=exclude_credentials if exclude_credentials else None
        )

        # Cache challenge
        cls._save_challenge(str(user.id), options.challenge)
        return json.loads(webauthn.options_to_json(options))

    @classmethod
    async def verify_passkey_registration(
        cls,
        session: AsyncSession,
        user_id: uuid.UUID,
        credential_json: Dict[str, Any],
        device_name: str = "Passkey Authenticator"
    ) -> LoginCredential:
        challenge = cls._consume_challenge(str(user_id))
        if not challenge:
            raise AuthenticationError("Passkey registration challenge expired or missing.")

        user_repo = UserRepository(session)
        audit_repo = AuditRepository(session)
        user = await user_repo.get_by_id(user_id)
        if not user:
            raise ValueError(f"User {user_id} not found.")

        try:
            expected_origins = [
                WEBAUTHN_ORIGIN,
                f"http://{WEBAUTHN_RP_ID}",
                f"https://{WEBAUTHN_RP_ID}",
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:8000",
                "http://127.0.0.1:8000"
            ]
            verified = webauthn.verify_registration_response(
                credential=credential_json,
                expected_challenge=challenge,
                expected_rp_id=WEBAUTHN_RP_ID,
                expected_origin=expected_origins,
                require_user_verification=False
            )
        except Exception as e:
            logger.warning(f"WebAuthn registration verification failed: {e}")
            raise AuthenticationError(f"Passkey registration failed: {str(e)}")

        cred_payload = json.dumps({
            "credential_id": verified.credential_id.hex(),
            "public_key": verified.credential_public_key.hex(),
            "sign_count": verified.sign_count,
            "device_name": device_name
        })

        cred = await user_repo.add_credential(user.id, "passkey", cred_payload)
        await audit_repo.log_event(
            event_type="passkey.enrolled",
            user_id=user.id,
            metadata={"credential_id": verified.credential_id.hex(), "device_name": device_name}
        )
        return cred

    @classmethod
    async def get_passkey_login_options(
        cls,
        session: AsyncSession,
        username: Optional[str] = None
    ) -> Dict[str, Any]:
        allow_credentials: Optional[List[PublicKeyCredentialDescriptor]] = None
        user_key = f"login:{username}" if username else f"login:anonymous:{secrets.token_hex(8)}"

        if username:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_display_name(username)
            if user:
                credentials = await user_repo.get_credentials(user.id, "passkey")
                allow_credentials = []
                for cred in credentials:
                    try:
                        data = json.loads(cred.credential_data)
                        allow_credentials.append(
                            PublicKeyCredentialDescriptor(id=bytes.fromhex(data["credential_id"]))
                        )
                    except Exception:
                        continue

        options: PublicKeyCredentialRequestOptions = webauthn.generate_authentication_options(
            rp_id=WEBAUTHN_RP_ID,
            allow_credentials=allow_credentials if allow_credentials else None,
            user_verification=UserVerificationRequirement.PREFERRED
        )

        cls._save_challenge(user_key, options.challenge)
        return {
            "options": json.loads(webauthn.options_to_json(options)),
            "challenge_key": user_key
        }

    @classmethod
    async def verify_passkey_login(
        cls,
        session: AsyncSession,
        username: str,
        credential_json: Dict[str, Any],
        challenge_key: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> AppUser:
        key = challenge_key or f"login:{username}"
        challenge = cls._consume_challenge(key)
        if not challenge:
            raise AuthenticationError("Passkey login challenge expired or invalid.")

        user_repo = UserRepository(session)
        audit_repo = AuditRepository(session)
        user = await user_repo.get_by_display_name(username)
        if not user or user.status != "active":
            raise AuthenticationError("User not found or disabled.")

        credentials = await user_repo.get_credentials(user.id, "passkey")
        target_cred: Optional[LoginCredential] = None
        target_data: Optional[Dict[str, Any]] = None

        cred_id_input = credential_json.get("id") or credential_json.get("rawId")
        if not cred_id_input:
            raise AuthenticationError("Missing credential ID in authentication response.")

        for cred in credentials:
            try:
                data = json.loads(cred.credential_data)
                # Compare hex or base64url credential id
                if data["credential_id"] == cred_id_input or bytes.fromhex(data["credential_id"]) == webauthn.base64url_to_bytes(cred_id_input):
                    target_cred = cred
                    target_data = data
                    break
            except Exception:
                continue

        if not target_cred or not target_data:
            raise AuthenticationError("Matching passkey not registered for this user.")

        try:
            expected_origins = [
                WEBAUTHN_ORIGIN,
                f"http://{WEBAUTHN_RP_ID}",
                f"https://{WEBAUTHN_RP_ID}",
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:8000",
                "http://127.0.0.1:8000"
            ]
            verified = webauthn.verify_authentication_response(
                credential=credential_json,
                expected_challenge=challenge,
                expected_rp_id=WEBAUTHN_RP_ID,
                expected_origin=expected_origins,
                credential_public_key=bytes.fromhex(target_data["public_key"]),
                credential_current_sign_count=target_data.get("sign_count", 0),
                require_user_verification=False
            )
        except Exception as e:
            logger.warning(f"WebAuthn authentication verification failed: {e}")
            raise AuthenticationError(f"Passkey verification failed: {str(e)}")

        # Update sign count
        target_data["sign_count"] = verified.new_sign_count
        target_cred.credential_data = json.dumps(target_data)
        await session.flush()

        await audit_repo.log_event(
            event_type="auth.success",
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"username": username, "method": "passkey"}
        )

        return user
