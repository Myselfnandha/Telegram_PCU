from __future__ import annotations
import uuid
from typing import Optional, TYPE_CHECKING
from datetime import datetime
from sqlalchemy import (
    String,
    BigInteger,
    Integer,
    DateTime,
    ForeignKey,
    LargeBinary,
    Index,
    CheckConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.db.models.user import AppUser


class TelegramAccount(Base):
    __tablename__ = "telegram_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    # Global uniqueness: A Telegram user ID cannot be silently bound to multiple tenant owners
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True
    )
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    role: Mapped[str] = mapped_column(
        String(50),
        default="primary",
        nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default="linked",
        nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False
    )

    __table_args__ = (
        CheckConstraint("role IN ('primary', 'turbo')", name="chk_telegram_account_role"),
        CheckConstraint("status IN ('linked', 'active', 'relink_required', 'unlinked')", name="chk_telegram_account_status"),
        # Partial unique index for primary account per owner (PostgreSQL)
        Index(
            "uq_owner_primary_account",
            "owner_user_id",
            unique=True,
            postgresql_where=(role == "primary"),
            sqlite_where=(role == "primary")
        ),
    )

    owner: Mapped["AppUser"] = relationship("AppUser", back_populates="telegram_accounts")
    vault: Mapped[Optional["TelegramSessionVault"]] = relationship(
        "TelegramSessionVault", back_populates="account", uselist=False, cascade="all, delete-orphan"
    )
    lease: Mapped[Optional["TelegramClientLease"]] = relationship(
        "TelegramClientLease", back_populates="account", uselist=False, cascade="all, delete-orphan"
    )


class TelegramSessionVault(Base):
    __tablename__ = "telegram_session_vault"

    telegram_account_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("telegram_accounts.id", ondelete="CASCADE"),
        primary_key=True
    )
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # 12-byte random nonce
    key_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    session_version: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False
    )

    account: Mapped["TelegramAccount"] = relationship("TelegramAccount", back_populates="vault")


class TelegramClientLease(Base):
    __tablename__ = "telegram_client_leases"

    telegram_account_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("telegram_accounts.id", ondelete="CASCADE"),
        primary_key=True
    )
    owner_instance_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False
    )
    fencing_epoch: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False
    )

    account: Mapped["TelegramAccount"] = relationship("TelegramAccount", back_populates="lease")
