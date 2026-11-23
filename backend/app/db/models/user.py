from __future__ import annotations
import uuid
from typing import Optional, List, TYPE_CHECKING
from datetime import datetime
from sqlalchemy import (
    String,
    Text,
    DateTime,
    ForeignKey,
    LargeBinary,
    Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.db.models.telegram import TelegramAccount


class AppUser(Base):
    __tablename__ = "app_users"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="user", nullable=False)
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

    # Relationships
    credentials: Mapped[List["LoginCredential"]] = relationship(
        "LoginCredential", back_populates="user", cascade="all, delete-orphan"
    )
    devices: Mapped[List["TrustedDevice"]] = relationship(
        "TrustedDevice", back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[List["AppSession"]] = relationship(
        "AppSession", back_populates="user", cascade="all, delete-orphan"
    )
    telegram_accounts: Mapped[List["TelegramAccount"]] = relationship(
        "TelegramAccount", back_populates="owner", cascade="all, delete-orphan"
    )


class LoginCredential(Base):
    __tablename__ = "login_credentials"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    credential_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'passkey', 'password_hash', 'recovery_code'
    credential_data: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False
    )

    user: Mapped["AppUser"] = relationship("AppUser", back_populates="credentials")


class TrustedDevice(Base):
    __tablename__ = "trusted_devices"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    device_fingerprint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    public_key: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False
    )

    user: Mapped["AppUser"] = relationship("AppUser", back_populates="devices")
    sessions: Mapped[List["AppSession"]] = relationship("AppSession", back_populates="device")


class AppSession(Base):
    __tablename__ = "app_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    device_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trusted_devices.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False
    )

    user: Mapped["AppUser"] = relationship("AppUser", back_populates="sessions")
    device: Mapped[Optional["TrustedDevice"]] = relationship("TrustedDevice", back_populates="sessions")
