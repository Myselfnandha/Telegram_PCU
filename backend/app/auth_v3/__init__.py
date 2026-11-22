from app.auth_v3.models import (
    Principal,
    RegisterRequest,
    RegisterResponse,
    LoginRequest,
    LoginResponse,
    DeviceInfo,
    UserProfileResponse
)
from app.auth_v3.identity_service import (
    IdentityService,
    RateLimitError,
    AuthenticationError
)
from app.auth_v3.session_service import (
    SessionService,
    get_current_principal,
    get_optional_principal,
    require_admin
)
from app.auth_v3.device_service import DeviceService

__all__ = [
    "Principal",
    "RegisterRequest",
    "RegisterResponse",
    "LoginRequest",
    "LoginResponse",
    "DeviceInfo",
    "UserProfileResponse",
    "IdentityService",
    "RateLimitError",
    "AuthenticationError",
    "SessionService",
    "DeviceService",
    "get_current_principal",
    "get_optional_principal",
    "require_admin",
]
