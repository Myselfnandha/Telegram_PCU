import uuid
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from dataclasses import dataclass


@dataclass
class Principal:
    """Authenticated user context for tenant isolation across the application."""
    user_id: uuid.UUID
    role: str
    display_name: Optional[str] = None
    device_id: Optional[uuid.UUID] = None
    session_id: Optional[uuid.UUID] = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, description="User display name / username")
    password: Optional[str] = Field(None, min_length=8, description="Password (optional if using passkey-only)")
    device_name: Optional[str] = Field("Primary Device", max_length=128)
    device_fingerprint: Optional[str] = Field(None, max_length=255)
    bootstrap_token: Optional[str] = Field(None, description="Optional admin bootstrap token")


class RegisterResponse(BaseModel):
    user_id: str
    username: str
    role: str
    session_token: str
    recovery_codes: List[str] = Field(default_factory=list)
    device_id: Optional[str] = None
    device_verified: bool = True


class LoginRequest(BaseModel):
    username: str
    password: str
    device_name: Optional[str] = Field("Current Device", max_length=128)
    device_fingerprint: Optional[str] = Field(None, max_length=255)


class LoginResponse(BaseModel):
    user_id: str
    username: str
    role: str
    session_token: str
    device_id: Optional[str] = None
    device_verified: bool = True
    approval_code: Optional[str] = None


class PasskeyRegisterOptionsRequest(BaseModel):
    user_id: str


class PasskeyRegisterVerifyRequest(BaseModel):
    user_id: str
    credential: Dict[str, Any]
    device_name: Optional[str] = Field("Passkey Authenticator", max_length=128)


class PasskeyLoginOptionsRequest(BaseModel):
    username: Optional[str] = None


class PasskeyLoginVerifyRequest(BaseModel):
    username: str
    credential: Dict[str, Any]
    device_name: Optional[str] = Field("Passkey Device", max_length=128)
    device_fingerprint: Optional[str] = None


class DeviceInfo(BaseModel):
    id: str
    display_name: str
    device_fingerprint: Optional[str]
    verified: bool
    is_current: bool
    last_seen_at: Optional[str]
    created_at: str


class ApproveDeviceRequest(BaseModel):
    challenge_code: Optional[str] = None


class UserProfileResponse(BaseModel):
    user_id: str
    username: str
    role: str
    status: str
    device_id: Optional[str] = None
    is_admin: bool
