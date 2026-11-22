import time
import uuid
import secrets
import logging
from typing import Optional, Tuple, List, Dict
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utc_now
from app.db.models.user import TrustedDevice
from app.db.repositories.user_repo import UserRepository
from app.db.repositories.audit_repo import AuditRepository
from app.auth_v3.models import DeviceInfo

logger = logging.getLogger("device_service")

CHALLENGE_TTL_SECONDS = 600  # 10 minutes


class DeviceService:
    # Memory store for pending device approval codes: {f"{user_id}:{device_id}": (code, expires_at)}
    _pending_approvals: Dict[str, Tuple[str, float]] = {}

    @classmethod
    def _save_approval_code(cls, user_id: uuid.UUID, device_id: uuid.UUID) -> str:
        cls._cleanup_approvals()
        # 6-digit numeric verification code
        code = f"{secrets.randbelow(900000) + 100000}"
        key = f"{user_id}:{device_id}"
        cls._pending_approvals[key] = (code, time.time() + CHALLENGE_TTL_SECONDS)
        return code

    @classmethod
    def _verify_and_consume_code(cls, user_id: uuid.UUID, device_id: uuid.UUID, code: Optional[str]) -> bool:
        cls._cleanup_approvals()
        key = f"{user_id}:{device_id}"
        entry = cls._pending_approvals.get(key)
        if not entry:
            return False
        stored_code, expires_at = entry
        if time.time() > expires_at:
            cls._pending_approvals.pop(key, None)
            return False
        if code and secrets.compare_digest(stored_code, code.strip()):
            cls._pending_approvals.pop(key, None)
            return True
        return False

    @classmethod
    def _cleanup_approvals(cls) -> None:
        now = time.time()
        for k in list(cls._pending_approvals.keys()):
            if cls._pending_approvals[k][1] < now:
                cls._pending_approvals.pop(k, None)

    @classmethod
    async def register_or_resolve_device(
        cls,
        session: AsyncSession,
        user_id: uuid.UUID,
        display_name: str,
        device_fingerprint: Optional[str] = None
    ) -> Tuple[TrustedDevice, bool, Optional[str]]:
        """
        Registers a new device or matches an existing trusted device.
        Returns (device, is_verified, approval_challenge_code).
        """
        user_repo = UserRepository(session)
        audit_repo = AuditRepository(session)
        existing_devices = await user_repo.list_devices(user_id)

        # Match fingerprint if provided
        if device_fingerprint:
            for dev in existing_devices:
                if dev.device_fingerprint == device_fingerprint:
                    await user_repo.update_device_seen(dev.id)
                    is_verified = dev.verified_at is not None
                    code = None
                    if not is_verified:
                        code = cls._save_approval_code(user_id, dev.id)
                    return dev, is_verified, code

        # If user has no existing verified devices, auto-verify this initial device
        verified_count = sum(1 for d in existing_devices if d.verified_at is not None)
        auto_verify = (verified_count == 0)

        device = await user_repo.create_device(
            user_id=user_id,
            display_name=display_name or "Unknown Device",
            device_fingerprint=device_fingerprint,
            verified=auto_verify
        )

        approval_code = None
        if not auto_verify:
            approval_code = cls._save_approval_code(user_id, device.id)
            await audit_repo.log_event(
                event_type="device.pending_verification",
                user_id=user_id,
                metadata={"device_id": str(device.id), "display_name": display_name}
            )
        else:
            await audit_repo.log_event(
                event_type="device.registered_trusted",
                user_id=user_id,
                metadata={"device_id": str(device.id), "display_name": display_name}
            )

        return device, auto_verify, approval_code

    @classmethod
    async def approve_device(
        cls,
        session: AsyncSession,
        user_id: uuid.UUID,
        device_id: uuid.UUID,
        challenge_code: Optional[str] = None,
        approver_device_id: Optional[uuid.UUID] = None
    ) -> bool:
        """
        Approves a pending device. Requires approval from an already-verified trusted device session,
        or submission of a verified one-time challenge code.
        An unverified device cannot approve itself.
        """
        user_repo = UserRepository(session)
        audit_repo = AuditRepository(session)

        # Security check: Unverified device cannot approve itself
        if approver_device_id and approver_device_id == device_id:
            raise PermissionError("An unverified device cannot approve itself.")

        # If approved from an active session, verify that the approving device is already trusted
        if approver_device_id:
            approver_dev = await user_repo.get_device(approver_device_id, user_id)
            if not approver_dev or approver_dev.verified_at is None:
                raise PermissionError("Device approval requires an already-verified trusted device session.")

        device = await user_repo.get_device(device_id, user_id)
        if not device:
            return False

        if challenge_code:
            valid = cls._verify_and_consume_code(user_id, device_id, challenge_code)
            if not valid:
                return False
        elif not approver_device_id:
            # Cannot approve without either challenge code or verified approver session
            return False

        # Mark device verified
        success = await user_repo.verify_device(device_id, user_id)
        if success:
            cls._pending_approvals.pop(f"{user_id}:{device_id}", None)
            await audit_repo.log_event(
                event_type="device.approved",
                user_id=user_id,
                metadata={"device_id": str(device_id), "approver_device_id": str(approver_device_id) if approver_device_id else None}
            )
        return success

    @classmethod
    async def get_pending_devices(
        cls,
        session: AsyncSession,
        user_id: uuid.UUID,
        approver_device_id: Optional[uuid.UUID] = None
    ) -> List[Dict[str, Any]]:
        """
        Returns list of devices pending approval along with their active challenge codes.
        Only accessible by an already-verified trusted device session.
        """
        user_repo = UserRepository(session)
        if approver_device_id:
            approver = await user_repo.get_device(approver_device_id, user_id)
            if not approver or approver.verified_at is None:
                raise PermissionError("Only verified devices can review pending device approval requests.")

        devices = await user_repo.list_devices(user_id)
        pending = []
        for dev in devices:
            if dev.verified_at is None:
                key = f"{user_id}:{dev.id}"
                code_entry = cls._pending_approvals.get(key)
                code = code_entry[0] if (code_entry and code_entry[1] > time.time()) else None
                pending.append({
                    "id": str(dev.id),
                    "display_name": dev.display_name,
                    "device_fingerprint": dev.device_fingerprint,
                    "created_at": dev.created_at.isoformat(),
                    "challenge_code": code
                })
        return pending

    @classmethod
    async def revoke_device(
        cls,
        session: AsyncSession,
        user_id: uuid.UUID,
        device_id: uuid.UUID
    ) -> bool:
        """Revokes a device and cascades revocation to all active sessions bound to it."""
        user_repo = UserRepository(session)
        audit_repo = AuditRepository(session)

        success = await user_repo.revoke_device(device_id, user_id)
        if success:
            cls._pending_approvals.pop(f"{user_id}:{device_id}", None)
            await audit_repo.log_event(
                event_type="device.revoked",
                user_id=user_id,
                metadata={"device_id": str(device_id)}
            )
        return success

    @classmethod
    async def list_user_devices(
        cls,
        session: AsyncSession,
        user_id: uuid.UUID,
        current_device_id: Optional[uuid.UUID] = None
    ) -> List[DeviceInfo]:
        user_repo = UserRepository(session)
        devices = await user_repo.list_devices(user_id)
        return [
            DeviceInfo(
                id=str(d.id),
                display_name=d.display_name,
                device_fingerprint=d.device_fingerprint,
                verified=d.verified_at is not None,
                is_current=(d.id == current_device_id),
                last_seen_at=d.last_seen_at.isoformat() if d.last_seen_at else None,
                created_at=d.created_at.isoformat()
            )
            for d in devices
        ]
