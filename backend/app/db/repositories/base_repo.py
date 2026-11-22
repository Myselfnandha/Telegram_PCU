import uuid
from typing import TypeVar, Generic, Type, Optional, List, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update
from app.db.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    def __init__(self, model: Type[ModelType], session: AsyncSession):
        self.model = model
        self.session = session

    async def get_by_id(self, id: uuid.UUID) -> Optional[ModelType]:
        stmt = select(self.model).where(self.model.id == id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_scoped(self, id: uuid.UUID, owner_user_id: uuid.UUID) -> Optional[ModelType]:
        """Tenant-scoped retrieval ensuring the record belongs to the requesting user."""
        if hasattr(self.model, "owner_user_id"):
            stmt = select(self.model).where(
                self.model.id == id,
                self.model.owner_user_id == owner_user_id
            )
        elif hasattr(self.model, "user_id"):
            stmt = select(self.model).where(
                self.model.id == id,
                self.model.user_id == owner_user_id
            )
        else:
            raise AttributeError(f"{self.model.__name__} has no tenant ownership column")
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_by_owner(self, owner_user_id: uuid.UUID) -> List[ModelType]:
        """Tenant-scoped listing."""
        if hasattr(self.model, "owner_user_id"):
            stmt = select(self.model).where(self.model.owner_user_id == owner_user_id)
        elif hasattr(self.model, "user_id"):
            stmt = select(self.model).where(self.model.user_id == owner_user_id)
        else:
            raise AttributeError(f"{self.model.__name__} has no tenant ownership column")
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def delete_scoped(self, id: uuid.UUID, owner_user_id: uuid.UUID) -> bool:
        """Tenant-scoped deletion."""
        record = await self.get_scoped(id, owner_user_id)
        if record:
            await self.session.delete(record)
            await self.session.flush()
            return True
        return False
