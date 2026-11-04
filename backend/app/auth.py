import os
from fastapi import Request, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional

API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "").strip()
ENABLE_API_AUTH = os.getenv("ENABLE_API_AUTH", "false").lower() in ("1", "true", "yes")

security = HTTPBearer(auto_error=False)


def is_loopback_request(request: Request) -> bool:
    """
    Checks if the request is strictly from local loopback (127.0.0.1 / ::1 / localhost / testserver)
    and did NOT arrive through an external proxy, tunnel, or forwarded header.
    """
    # Any forwarding proxy headers indicate external routing (e.g. Cloudflare Tunnel, ngrok, reverse proxy)
    for header in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
        if header in request.headers:
            return False

    client_host = getattr(request.client, "host", None) if request.client else None
    return client_host in ("127.0.0.1", "::1", "localhost", "testserver", "testclient")


def is_authenticated(request: Request) -> bool:
    """
    Validates whether the incoming request contains a valid API authentication token.
    Token can be supplied via:
    1. Authorization: Bearer <token>
    2. Header X-API-Key: <token>
    3. Query parameter ?token=<token>
    """
    token_to_check = API_AUTH_TOKEN
    is_loopback = is_loopback_request(request)

    # VLC and native mobile players cannot reliably attach custom auth headers. Accept a
    # short-lived capability only for GET/HEAD requests to one exact /dl/{chat}/{message}
    # resource. It cannot authorize API routes, transmux endpoints, or another media item.
    req_method = request.scope.get("method", "GET") if hasattr(request, "scope") else getattr(request, "method", "GET")
    if req_method in ("GET", "HEAD"):
        path = request.scope.get("path", "") if hasattr(request, "scope") else getattr(request, "path", "")
        parts = path.split("/")
        if not token_to_check and not ENABLE_API_AUTH and path.startswith(("/dl/", "/stream/")):
            return True
        media_identity = None
        if path.startswith("/dl/") and len(parts) >= 4 and parts[2] and parts[3].isdigit():
            media_identity = (parts[2], int(parts[3]))
        elif path.startswith("/api/media/subtitles/download/") and len(parts) >= 7 and parts[5] and parts[6].isdigit():
            media_identity = (parts[5], int(parts[6]))
        if media_identity:
            try:
                from app.services.vlc_playback import playback_grants
                if playback_grants.allows(
                    request.query_params.get("playback_token", ""), media_identity[0], media_identity[1]
                ):
                    return True
            except Exception:
                pass

    if not token_to_check and not ENABLE_API_AUTH and is_loopback:
        return True

    if not token_to_check:
        # Non-loopback request or auth enabled, but server has no secret token configured
        return False

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
    Authentication is required if:
    1. API_AUTH_TOKEN is configured, OR
    2. ENABLE_API_AUTH is true, OR
    3. The request is non-loopback (LAN, Cloudflare tunnel, remote proxy)
    """
    is_loopback = is_loopback_request(request)
    must_authenticate = bool(API_AUTH_TOKEN) or ENABLE_API_AUTH or not is_loopback

    if must_authenticate:
        # A valid media-scoped playback capability is also an authentication credential
        # for its single media resource, including when the permanent API token is unset.
        if is_authenticated(request):
            return
        if not API_AUTH_TOKEN:
            # Non-loopback / remote access attempted without configured API_AUTH_TOKEN
            raise HTTPException(
                status_code=403,
                detail="Forbidden: Non-loopback or remote requests require API_AUTH_TOKEN to be configured on the server or a valid media playback link."
            )
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Valid API authentication token required."
        )
