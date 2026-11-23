import logging
from typing import Optional
from fastapi import APIRouter, Depends, Request, Response, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.auth_v3.models import (
    Principal,
    RegisterRequest,
    RegisterResponse,
    LoginRequest,
    LoginResponse,
    UserProfileResponse,
    PasskeyRegisterOptionsRequest,
    PasskeyRegisterVerifyRequest,
    PasskeyLoginOptionsRequest,
    PasskeyLoginVerifyRequest
)
from app.auth_v3.identity_service import (
    IdentityService,
    RateLimitError,
    AuthenticationError
)
from app.auth_v3.session_service import (
    SessionService,
    get_current_principal,
    get_optional_principal
)
from app.auth_v3.device_service import DeviceService

logger = logging.getLogger("auth_v3_router")

router = APIRouter(prefix="/api/v3/auth", tags=["Authentication v3"])


def _get_client_ip(request: Request) -> str:
    if "cf-connecting-ip" in request.headers:
        return request.headers["cf-connecting-ip"]
    if "x-forwarded-for" in request.headers:
        return request.headers["x-forwarded-for"].split(",")[0].strip()
    return getattr(request.client, "host", "unknown") if request.client else "unknown"


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    ip = _get_client_ip(request)
    ua = request.headers.get("user-agent", "")

    try:
        user, recovery_codes = await IdentityService.register_user(
            session=db,
            username=req.username,
            password=req.password,
            bootstrap_token=req.bootstrap_token,
            ip_address=ip,
            user_agent=ua
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Register initial device (auto-verify initial registration device)
    device, is_verified, _ = await DeviceService.register_or_resolve_device(
        session=db,
        user_id=user.id,
        display_name=req.device_name or "Primary Device",
        device_fingerprint=req.device_fingerprint,
        auto_verify=True
    )

    # Issue session
    raw_token, _ = await SessionService.create_session(
        session=db,
        user_id=user.id,
        device_id=device.id
    )

    SessionService.set_session_cookie(response, raw_token)
    from app.auth import API_AUTH_TOKEN
    if API_AUTH_TOKEN:
        is_https = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto", "").lower() == "https"
        )
        response.set_cookie(
            key="tg_api_token",
            value=API_AUTH_TOKEN,
            max_age=31536000,
            path="/",
            httponly=True,
            samesite="lax",
            secure=is_https
        )

    # Auto-adopt legacy Telegram session if one is currently active on the server
    try:
        from app.telegram_client import TelegramClientManager
        from app.config import SESSION_STRING
        if await TelegramClientManager.is_authorized():
            info = await TelegramClientManager.get_me_info()
            tg_user_id = info.get("id") if info else None
            raw_session = SESSION_STRING
            if not raw_session and getattr(TelegramClientManager, "_client", None) and TelegramClientManager._client.session:
                from telethon.sessions import StringSession
                raw_session = StringSession.save(TelegramClientManager._client.session)

            if tg_user_id and raw_session:
                from app.db.repositories.telegram_repo import TelegramAccountRepository
                from app.telegram.session_vault import SessionVaultRepository
                acc_repo = TelegramAccountRepository(db)
                vault_repo = SessionVaultRepository(db)
                existing = await acc_repo.get_by_telegram_user_id(tg_user_id)
                if not existing:
                    acc = await acc_repo.create_account(
                        owner_user_id=user.id,
                        telegram_user_id=tg_user_id,
                        role="primary",
                        username=info.get("username"),
                        first_name=info.get("first_name"),
                        phone_number=info.get("phone")
                    )
                    await vault_repo.save_session(
                        owner_user_id=user.id,
                        account_id=acc.id,
                        plaintext_session=raw_session
                    )
                    await db.commit()
                    logger.info(f"Auto-adopted active server Telegram session into user {user.id} vault.")
    except Exception as e:
        logger.warning(f"Session auto-adoption notice: {e}")

    return RegisterResponse(
        user_id=str(user.id),
        username=user.display_name or "",
        role=user.role,
        session_token=raw_token,
        recovery_codes=recovery_codes,
        device_id=str(device.id),
        device_verified=is_verified
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    ip = _get_client_ip(request)
    ua = request.headers.get("user-agent", "")

    try:
        user = await IdentityService.authenticate_password(
            session=db,
            username=req.username,
            password=req.password,
            ip_address=ip,
            user_agent=ua
        )
    except RateLimitError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    except AuthenticationError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    # Resolve device (Direct Password Access: verified credentials auto-verify the device)
    device, is_verified, approval_code = await DeviceService.register_or_resolve_device(
        session=db,
        user_id=user.id,
        display_name=req.device_name or "Client Device",
        device_fingerprint=req.device_fingerprint,
        auto_verify=True
    )

    # Issue session
    raw_token, _ = await SessionService.create_session(
        session=db,
        user_id=user.id,
        device_id=device.id
    )

    SessionService.set_session_cookie(response, raw_token)
    from app.auth import API_AUTH_TOKEN
    if API_AUTH_TOKEN:
        is_https = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto", "").lower() == "https"
        )
        response.set_cookie(
            key="tg_api_token",
            value=API_AUTH_TOKEN,
            max_age=31536000,
            path="/",
            httponly=True,
            samesite="lax",
            secure=is_https
        )

    return LoginResponse(
        user_id=str(user.id),
        username=user.display_name or "",
        role=user.role,
        session_token=raw_token,
        device_id=str(device.id),
        device_verified=is_verified,
        approval_code=None  # Security hardening: Code is never returned to the untrusted caller
    )


@router.post("/logout")
async def logout(
    response: Response,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db)
):
    if principal.session_id:
        await SessionService.revoke_session(db, principal.session_id, principal.user_id)
    SessionService.clear_session_cookie(response)
    response.delete_cookie(key="tg_api_token", path="/")
    return {"message": "Logged out successfully."}


@router.get("/me", response_model=UserProfileResponse)
async def get_me(principal: Principal = Depends(get_current_principal)):
    return UserProfileResponse(
        user_id=str(principal.user_id),
        username=principal.display_name or "",
        role=principal.role,
        status="active",
        device_id=str(principal.device_id) if principal.device_id else None,
        is_admin=principal.is_admin
    )


# ------------------ Passkey Routes ------------------

@router.post("/passkey/register-options")
async def passkey_register_options(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db)
):
    try:
        options = await IdentityService.get_passkey_registration_options(db, principal.user_id)
        return options
    except Exception as e:
        logger.error(f"Error generating passkey registration options: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/passkey/register-verify")
async def passkey_register_verify(
    req: PasskeyRegisterVerifyRequest,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db)
):
    try:
        cred = await IdentityService.verify_passkey_registration(
            session=db,
            user_id=principal.user_id,
            credential_json=req.credential,
            device_name=req.device_name or "Passkey Authenticator"
        )
        return {"message": "Passkey enrolled successfully.", "credential_id": str(cred.id)}
    except AuthenticationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/passkey/login-options")
async def passkey_login_options(
    req: PasskeyLoginOptionsRequest,
    db: AsyncSession = Depends(get_db)
):
    try:
        data = await IdentityService.get_passkey_login_options(db, username=req.username)
        return data
    except Exception as e:
        logger.error(f"Error generating passkey login options: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/passkey/login-verify", response_model=LoginResponse)
async def passkey_login_verify(
    req: PasskeyLoginVerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    ip = _get_client_ip(request)
    ua = request.headers.get("user-agent", "")

    try:
        user = await IdentityService.verify_passkey_login(
            session=db,
            username=req.username,
            credential_json=req.credential,
            ip_address=ip,
            user_agent=ua
        )
    except AuthenticationError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    device, is_verified, approval_code = await DeviceService.register_or_resolve_device(
        session=db,
        user_id=user.id,
        display_name=req.device_name or "Passkey Device",
        device_fingerprint=req.device_fingerprint
    )

    raw_token, _ = await SessionService.create_session(
        session=db,
        user_id=user.id,
        device_id=device.id
    )

    SessionService.set_session_cookie(response, raw_token)

    return LoginResponse(
        user_id=str(user.id),
        username=user.display_name or "",
        role=user.role,
        session_token=raw_token,
        device_id=str(device.id),
        device_verified=is_verified,
        approval_code=None  # Security hardening: Code is never returned to the untrusted caller
    )
