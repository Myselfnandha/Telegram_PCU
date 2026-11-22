import uuid
import logging
from typing import List, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.auth_v3.models import Principal
from app.auth_v3.session_service import get_current_principal
from app.db.repositories.telegram_repo import (
    TelegramAccountRepository,
    DuplicateTelegramAccountError,
    PrimaryAccountConflictError
)
from app.telegram.link_service import (
    TelegramLinkService,
    LinkAttempt
)
from app.telegram.client_manager import (
    multi_tenant_manager,
    AccountNotLinkedError,
    RelinkRequiredError
)

logger = logging.getLogger("telegram_link_v3_router")

router = APIRouter(prefix="/api/v3/telegram", tags=["Telegram Accounts & Linking v3"])


class StartLinkAttemptRequest(BaseModel):
    role: str = Field("primary", description="Account role: 'primary' or 'turbo'")


class Submit2FARequest(BaseModel):
    password: str = Field(..., description="Telegram two-step verification password")


class TelegramAccountResponse(BaseModel):
    id: str
    telegram_user_id: int
    role: str
    status: str
    username: Optional[str] = None
    first_name: Optional[str] = None
    phone_number: Optional[str] = None
    created_at: str


@router.post("/link-attempts", status_code=status.HTTP_201_CREATED)
async def start_link_attempt(
    req: StartLinkAttemptRequest,
    principal: Principal = Depends(get_current_principal)
):
    try:
        attempt = await TelegramLinkService.start_link_attempt(
            user_id=principal.user_id,
            role=req.role
        )
        return attempt.to_dict()
    except PrimaryAccountConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting link attempt: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/link-attempts/{attempt_id}")
async def get_link_attempt(
    attempt_id: str,
    principal: Principal = Depends(get_current_principal)
):
    try:
        aid = uuid.UUID(attempt_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid attempt UUID.")

    attempt = TelegramLinkService.get_link_attempt(principal.user_id, aid)
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link attempt not found.")

    return attempt.to_dict()


@router.post("/link-attempts/{attempt_id}/2fa")
async def submit_2fa(
    attempt_id: str,
    req: Submit2FARequest,
    principal: Principal = Depends(get_current_principal)
):
    try:
        aid = uuid.UUID(attempt_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid attempt UUID.")

    success = await TelegramLinkService.submit_2fa_password(
        user_id=principal.user_id,
        attempt_id=aid,
        password=req.password
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to submit 2FA password. Attempt not found, wrong user, or not awaiting 2FA."
        )

    return {"message": "2FA password received and processing."}


@router.post("/link-attempts/{attempt_id}/cancel")
async def cancel_link_attempt(
    attempt_id: str,
    principal: Principal = Depends(get_current_principal)
):
    try:
        aid = uuid.UUID(attempt_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid attempt UUID.")

    success = await TelegramLinkService.cancel_link_attempt(principal.user_id, aid)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link attempt not found.")

    return {"message": "Link attempt cancelled."}


@router.get("/accounts", response_model=List[TelegramAccountResponse])
async def list_accounts(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db)
):
    tg_repo = TelegramAccountRepository(db)
    accounts = await tg_repo.list_user_accounts(principal.user_id)
    return [
        TelegramAccountResponse(
            id=str(a.id),
            telegram_user_id=a.telegram_user_id,
            role=a.role,
            status=a.status,
            username=a.username,
            first_name=a.first_name,
            phone_number=a.phone_number,
            created_at=a.created_at.isoformat()
        )
        for a in accounts
    ]


@router.delete("/accounts/{account_id}")
async def unlink_account(
    account_id: str,
    principal: Principal = Depends(get_current_principal)
):
    try:
        aid = uuid.UUID(account_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid account UUID.")

    success = await multi_tenant_manager.revoke_telegram_account(principal, aid)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Telegram account not found.")

    return {"message": "Telegram account unlinked and vault data removed."}


@router.post("/accounts/{account_id}/set-primary")
async def set_primary_account(
    account_id: str,
    principal: Principal = Depends(get_current_principal)
):
    try:
        aid = uuid.UUID(account_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid account UUID.")

    success = await multi_tenant_manager.set_primary_account(principal, aid)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found or could not set primary.")

    return {"message": "Primary account updated."}
