import uuid
import json
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models.audit import AuditEvent
from app.db.repositories.base_repo import BaseRepository


class AuditRepository(BaseRepository[AuditEvent]):
    def __init__(self, session: AsyncSession):
        super().__init__(AuditEvent, session)

    async def log_event(
        self,
        event_type: str,
        user_id: Optional[uuid.UUID] = None,
        telegram_account_id: Optional[uuid.UUID] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> AuditEvent:
        # Sanitize metadata to guarantee no credentials or session strings are ever logged
        clean_metadata: Optional[str] = None
        if metadata:
            sanitized = {
                k: v for k, v in metadata.items()
                if "session" not in k.lower() and "token" not in k.lower() and "password" not in k.lower() and "key" not in k.lower()
            }
            clean_metadata = json.dumps(sanitized)

        event = AuditEvent(
            id=uuid.uuid4(),
            user_id=user_id,
            telegram_account_id=telegram_account_id,
            event_type=event_type,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata_json=clean_metadata
        )
        self.session.add(event)
        await self.session.flush()
        return event
