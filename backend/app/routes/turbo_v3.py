from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth_v3.session_service import get_current_principal, Principal
from app.telegram.turbo_pool import turbo_pool_manager, TurboMode

router = APIRouter(prefix="/api/v3/turbo", tags=["turbo_v3"])


class SetModeRequest(BaseModel):
    mode: str = Field(..., description="off, balanced, adaptive, or maximum")


@router.get("/status", response_model=Dict[str, Any])
async def get_turbo_status(
    principal: Principal = Depends(get_current_principal)
):
    """Returns the Multi-Turbo status, worker metrics, and FloodWait indicators for the authenticated user."""
    return await turbo_pool_manager.get_pool_status(principal.user_id)


@router.post("/mode")
async def set_turbo_mode(
    req: SetModeRequest,
    principal: Principal = Depends(get_current_principal)
):
    """Sets the Multi-Turbo scheduler mode (off, balanced, adaptive, or maximum)."""
    valid_modes = [m.value for m in TurboMode]
    mode_str = req.mode.lower().strip()
    if mode_str not in valid_modes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid turbo mode '{req.mode}'. Valid modes: {', '.join(valid_modes)}"
        )
    current_mode = await turbo_pool_manager.set_user_mode(principal.user_id, mode_str)
    return {"status": "updated", "mode": current_mode}
