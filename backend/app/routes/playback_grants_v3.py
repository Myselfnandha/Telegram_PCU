import uuid
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field

from app.auth_v3.session_service import get_current_principal, Principal
from app.services.playback_grants import playback_grant_service, normalize_chat_id

router = APIRouter(prefix="/api/v3/media/grants", tags=["playback_grants_v3"])


class IssueGrantRequest(BaseModel):
    chat_id: str = Field(..., description="Telegram chat ID")
    message_id: int = Field(..., description="Telegram message ID")
    operation: str = Field(default="stream", description="stream, download, or all")
    account_id: Optional[uuid.UUID] = Field(default=None, description="Optional bound Telegram account ID")
    ttl_seconds: Optional[int] = Field(default=14400, ge=60, le=86400 * 7, description="Grant validity in seconds")


class GrantResponse(BaseModel):
    token: str
    grant_id: str
    chat_id: str
    message_id: int
    operation: str
    expires_at: float
    streaming_url: str
    download_url: str


@router.post("", response_model=GrantResponse)
async def issue_playback_grant(
    req: IssueGrantRequest,
    principal: Principal = Depends(get_current_principal)
):
    """
    Issues a cryptographically signed, time-limited, revocable Playback Grant
    scoped to the requesting user and designated media item.
    """
    token = playback_grant_service.issue_grant(
        user_id=principal.user_id,
        chat_id=req.chat_id,
        message_id=req.message_id,
        operation=req.operation,
        account_id=req.account_id,
        ttl_seconds=req.ttl_seconds
    )
    norm_chat = normalize_chat_id(req.chat_id)
    # Extract grant_id from token
    grant_info = playback_grant_service.verify_grant(
        token=token,
        chat_id=req.chat_id,
        message_id=req.message_id,
        operation=req.operation
    )

    streaming_url = f"/stream/{norm_chat}/{req.message_id}?grant={token}"
    download_url = f"/dl/{norm_chat}/{req.message_id}?grant={token}"

    return GrantResponse(
        token=token,
        grant_id=grant_info.grant_id if grant_info else "",
        chat_id=norm_chat,
        message_id=req.message_id,
        operation=req.operation,
        expires_at=grant_info.expires_at if grant_info else 0.0,
        streaming_url=streaming_url,
        download_url=download_url
    )


@router.get("", response_model=List[Dict[str, Any]])
async def list_active_grants(
    principal: Principal = Depends(get_current_principal)
):
    """Lists all active playback grants belonging to the authenticated user."""
    return playback_grant_service.list_user_grants(principal.user_id)


@router.delete("/{grant_id}")
async def revoke_playback_grant(
    grant_id: str,
    principal: Principal = Depends(get_current_principal)
):
    """Revokes a specific playback grant."""
    success = playback_grant_service.revoke_grant(principal.user_id, grant_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grant not found or not owned by caller"
        )
    return {"status": "revoked", "grant_id": grant_id}


@router.delete("")
async def revoke_all_grants(
    principal: Principal = Depends(get_current_principal)
):
    """Revokes all playback grants issued by the authenticated user."""
    count = playback_grant_service.revoke_all_user_grants(principal.user_id)
    return {"status": "all_revoked", "revoked_count": count}
