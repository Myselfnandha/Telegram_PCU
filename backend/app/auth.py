import os
from fastapi import Request, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional

API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "").strip()
ENABLE_API_AUTH = os.getenv("ENABLE_API_AUTH", "false").lower() in ("1", "true", "yes")

security = HTTPBearer(auto_error=False)


def is_authenticated(request: Request) -> bool:
    """
    Validates whether the incoming request contains a valid API authentication token.
    Token can be supplied via:
    1. Authorization: Bearer <token>
    2. Header X-API-Key: <token>
    3. Query parameter ?token=<token>
    """
    token_to_check = API_AUTH_TOKEN
    if not token_to_check and not ENABLE_API_AUTH:
        # Auth disabled / not configured in local single-user mode
        return True

    # 1. Bearer Header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        client_token = auth_header[7:].strip()
        if client_token == token_to_check:
            return True

    # 2. X-API-Key Header
    api_key_header = request.headers.get("X-API-Key", "").strip()
    if api_key_header and api_key_header == token_to_check:
        return True

    # 3. Query Parameter
    query_token = request.query_params.get("token", "").strip()
    if query_token and query_token == token_to_check:
        return True

    return False


async def verify_auth(request: Request):
    """
    FastAPI dependency for private APIs, playback controls, cache mutations, and system actions.
    If API_AUTH_TOKEN is configured or ENABLE_API_AUTH is true, verifies caller token.
    """
    if (API_AUTH_TOKEN or ENABLE_API_AUTH) and not is_authenticated(request):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Valid API authentication token required."
        )
