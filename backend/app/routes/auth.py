import asyncio
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.models import AuthStatus
from app.telegram_client import TelegramClientManager

logger = logging.getLogger("auth_route")

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SendCodeRequest(BaseModel):
    phone: str


class SignInRequest(BaseModel):
    phone: str
    code: str
    phone_code_hash: str


class Password2FARequest(BaseModel):
    password: str


class SessionStringRequest(BaseModel):
    session_string: str


class ToggleTurboRequest(BaseModel):
    enabled: bool


def _trigger_preload_chats():
    try:
        from app.routes.chats import preload_chats
        asyncio.create_task(preload_chats())
    except Exception:
        pass


def _attach_sniffer():
    try:
        from app.services.sniffer_service import sniffer_service
        async def _attach():
            client = await TelegramClientManager.get_client()
            sniffer_service.attach_to_client(client)
        asyncio.create_task(_attach())
    except Exception:
        pass


@router.get("/status", response_model=AuthStatus)
async def check_auth():
    """Returns the current Telegram MTProto authentication status."""
    try:
        is_auth = await TelegramClientManager.is_authorized()
        if not is_auth:
            return AuthStatus(authenticated=False)

        info = await TelegramClientManager.get_me_info()
        if info:
            return AuthStatus(
                authenticated=True,
                phone=info.get("phone"),
                username=info.get("username"),
                user_id=info.get("id"),
                first_name=info.get("first_name"),
                last_name=info.get("last_name")
            )
        return AuthStatus(authenticated=False)
    except Exception as e:
        logger.error(f"Auth check failed: {e}")
        return AuthStatus(authenticated=False, error=str(e))


@router.post("/qr/start")
async def start_primary_qr_login():
    """Initiates Telegram QR code login for the primary account."""
    try:
        res = await TelegramClientManager.start_qr_login()
        return res
    except Exception as e:
        logger.error(f"Failed to start QR login: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/qr/status")
async def get_primary_qr_status():
    """Returns current polling status for primary QR login."""
    status = TelegramClientManager.get_qr_status()
    if status.get("status") == "authorized":
        _trigger_preload_chats()
        _attach_sniffer()
    return status


@router.post("/send")
@router.post("/send-code")
async def send_auth_code(payload: SendCodeRequest):
    """Sends Telegram MTProto login code to the specified phone number."""
    phone = payload.phone.strip()
    if not phone.startswith("+"):
        phone = f"+{phone}"
    try:
        res = await TelegramClientManager.send_login_code(phone)
        return {"success": True, **res}
    except Exception as e:
        logger.error(f"Failed to send login code to {phone}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sign")
@router.post("/sign-in")
async def sign_in_code(payload: SignInRequest):
    """Verifies Telegram login code and signs into session."""
    try:
        res = await TelegramClientManager.sign_in_with_code(
            phone=payload.phone.strip(),
            code=payload.code.strip(),
            phone_code_hash=payload.phone_code_hash.strip()
        )
        if res.get("status") == "authorized":
            _trigger_preload_chats()
            _attach_sniffer()
        return res
    except Exception as e:
        logger.error(f"Sign-in with code failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/2fa")
async def sign_in_2fa(payload: Password2FARequest):
    """Verifies 2FA password and finalizes login."""
    try:
        res = await TelegramClientManager.sign_in_with_2fa(password=payload.password.strip())
        if res.get("status") == "authorized":
            _trigger_preload_chats()
            _attach_sniffer()
        return res
    except Exception as e:
        logger.error(f"2FA sign-in failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/logout")
async def logout_telegram():
    """Logs out of Telegram MTProto."""
    try:
        from app.routes.chats import clear_chats_cache
        clear_chats_cache()
    except Exception:
        pass
    return await TelegramClientManager.logout()


@router.get("/secondary/status")
async def check_secondary_auth():
    """Returns secondary Telegram account authorization status and Turbo mode setting."""
    from app.config import ENABLE_MULTI_ACCOUNT_TURBO
    try:
        is_auth = await TelegramClientManager.is_secondary_authorized()
        info = await TelegramClientManager.get_secondary_me_info() if is_auth else None
        return {
            "enabled": ENABLE_MULTI_ACCOUNT_TURBO,
            "authenticated": is_auth,
            "user": info
        }
    except Exception as e:
        logger.error(f"Secondary auth check failed: {e}")
        return {
            "enabled": False,
            "authenticated": False,
            "error": str(e)
        }


@router.post("/secondary/qr/start")
async def start_secondary_qr():
    """Initiates Telegram QR code login for secondary account."""
    try:
        res = await TelegramClientManager.start_secondary_qr_login()
        return res
    except Exception as e:
        logger.error(f"Failed to start secondary QR login: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/secondary/qr/status")
async def get_secondary_qr():
    """Returns current polling status for secondary QR login."""
    return TelegramClientManager.get_secondary_qr_status()


@router.post("/secondary/send")
@router.post("/secondary/send-code")
async def send_secondary_auth_code(payload: SendCodeRequest):
    """Sends login code to secondary phone number."""
    phone = payload.phone.strip()
    if not phone.startswith("+"):
        phone = f"+{phone}"
    try:
        res = await TelegramClientManager.send_secondary_login_code(phone)
        return {"success": True, **res}
    except Exception as e:
        logger.error(f"Failed to send secondary login code to {phone}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/secondary/sign")
@router.post("/secondary/sign-in")
async def sign_in_secondary_code(payload: SignInRequest):
    """Verifies secondary account login code."""
    try:
        res = await TelegramClientManager.sign_in_secondary_with_code(
            phone=payload.phone.strip(),
            code=payload.code.strip(),
            phone_code_hash=payload.phone_code_hash.strip()
        )
        return res
    except Exception as e:
        logger.error(f"Secondary sign-in with code failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/secondary/2fa")
async def sign_in_secondary_2fa(payload: Password2FARequest):
    """Verifies secondary account 2FA password."""
    try:
        res = await TelegramClientManager.sign_in_secondary_with_2fa(password=payload.password.strip())
        return res
    except Exception as e:
        logger.error(f"Secondary 2FA sign-in failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/secondary/session")
@router.post("/secondary/session-string")
async def import_secondary_session(payload: SessionStringRequest):
    """Activates secondary account directly via StringSession."""
    try:
        res = await TelegramClientManager.import_secondary_session_string(session_string=payload.session_string.strip())
        return res
    except Exception as e:
        logger.error(f"Secondary session string import failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/secondary/logout")
async def logout_secondary():
    """Unlinks and logs out secondary Telegram account."""
    return await TelegramClientManager.logout_secondary()


@router.post("/secondary/toggle")
async def toggle_secondary_turbo(payload: ToggleTurboRequest):
    """Toggles Multi-Account Turbo 2x speed pooling."""
    from app.config import set_multi_account_turbo
    set_multi_account_turbo(payload.enabled)
    from app.services.rate_governor import rate_governor
    primary_info = TelegramClientManager.get_cached_me()
    primary_prem = bool(primary_info and primary_info.get("is_premium"))
    if payload.enabled and await TelegramClientManager.is_secondary_authorized():
        sec_info = await TelegramClientManager.get_secondary_me_info()
        sec_prem = bool(sec_info and sec_info.get("is_premium"))
        rate_governor.configure_for_tier(is_premium=primary_prem, secondary_premium=sec_prem)
    else:
        rate_governor.configure_for_tier(is_premium=primary_prem, secondary_premium=None)
    return {"enabled": payload.enabled}
