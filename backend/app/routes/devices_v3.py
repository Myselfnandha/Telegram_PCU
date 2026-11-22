import uuid
import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.auth_v3.models import Principal, DeviceInfo, ApproveDeviceRequest
from app.auth_v3.session_service import get_current_principal
from app.auth_v3.device_service import DeviceService

logger = logging.getLogger("devices_v3_router")

router = APIRouter(prefix="/api/v3/devices", tags=["Trusted Devices v3"])


@router.get("", response_model=List[DeviceInfo])
async def list_devices(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db)
):
    return await DeviceService.list_user_devices(
        session=db,
        user_id=principal.user_id,
        current_device_id=principal.device_id
    )


@router.get("/pending")
async def list_pending_devices(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db)
):
    try:
        return await DeviceService.get_pending_devices(
            session=db,
            user_id=principal.user_id,
            approver_device_id=principal.device_id
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/{device_id}/approve")
async def approve_device(
    device_id: str,
    req: ApproveDeviceRequest = ApproveDeviceRequest(),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db)
):
    try:
        dev_uuid = uuid.UUID(device_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid device UUID format.")

    try:
        success = await DeviceService.approve_device(
            session=db,
            user_id=principal.user_id,
            device_id=dev_uuid,
            challenge_code=req.challenge_code,
            approver_device_id=principal.device_id
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to approve device. Device not found or challenge code invalid."
        )

    return {"message": "Device approved successfully."}


@router.post("/{device_id}/revoke")
async def revoke_device(
    device_id: str,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db)
):
    try:
        dev_uuid = uuid.UUID(device_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid device UUID format.")

    success = await DeviceService.revoke_device(
        session=db,
        user_id=principal.user_id,
        device_id=dev_uuid
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")

    return {"message": "Device and associated sessions revoked successfully."}
