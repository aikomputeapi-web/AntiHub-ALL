"""
GitHub Copilot 账号管理 API

功能：
- 导入账号（通过 GitHub token）
- 账号列表/详情/启用禁用/改名/删除
- 手动刷新 Copilot token
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session, get_redis
from app.cache import RedisClient
from app.models.user import User
from app.schemas.copilot import (
    CopilotAccountImportRequest,
    CopilotAccountUpdateStatusRequest,
    CopilotAccountUpdateNameRequest,
)
from app.services.copilot_service import CopilotService

router = APIRouter(prefix="/api/copilot", tags=["Copilot账号管理"])
logger = logging.getLogger(__name__)


def get_copilot_service(
    db: AsyncSession = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis),
) -> CopilotService:
    return CopilotService(db, redis)


@router.post("/accounts/import", summary="导入 GitHub Copilot 账号（PAT 方式）")
async def copilot_import_account(
    request: CopilotAccountImportRequest,
    current_user: User = Depends(get_current_user),
    service: CopilotService = Depends(get_copilot_service),
):
    try:
        return await service.import_account(
            user_id=current_user.id,
            github_token=request.github_token,
            account_name=request.account_name,
            is_shared=request.is_shared,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("copilot import failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="导入 Copilot 账号失败",
        )


@router.post("/oauth/device-code", summary="发起 GitHub OAuth Device Flow")
async def copilot_start_device_flow(
    current_user: User = Depends(get_current_user),
    service: CopilotService = Depends(get_copilot_service),
):
    """发起 GitHub OAuth Device Flow，返回 user_code 和验证链接"""
    try:
        return await service.start_device_flow(user_id=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("copilot device flow start failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="发起 Device Flow 失败",
        )


@router.post("/oauth/device-poll", summary="轮询 GitHub Device Flow 授权状态")
async def copilot_poll_device_flow(
    request: dict,
    current_user: User = Depends(get_current_user),
    service: CopilotService = Depends(get_copilot_service),
):
    """轮询 Device Flow 状态，授权成功后自动导入账号"""
    user_code = (request.get("user_code") or "").strip()
    if not user_code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_code 不能为空")
    try:
        return await service.poll_device_flow(user_id=current_user.id, user_code=user_code)
    except Exception as e:
        logger.error("copilot device flow poll failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="轮询 Device Flow 失败",
        )


@router.get("/accounts", summary="获取 Copilot 账号列表")
async def copilot_list_accounts(
    current_user: User = Depends(get_current_user),
    service: CopilotService = Depends(get_copilot_service),
):
    return await service.list_accounts(current_user.id)


@router.get("/accounts/{account_id}", summary="获取 Copilot 账号详情")
async def copilot_get_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    service: CopilotService = Depends(get_copilot_service),
):
    try:
        return await service.get_account(current_user.id, account_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.put("/accounts/{account_id}/status", summary="启用/禁用 Copilot 账号")
async def copilot_update_status(
    account_id: int,
    request: CopilotAccountUpdateStatusRequest,
    current_user: User = Depends(get_current_user),
    service: CopilotService = Depends(get_copilot_service),
):
    try:
        return await service.update_account_status(current_user.id, account_id, request.status)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/accounts/{account_id}/name", summary="修改 Copilot 账号名称")
async def copilot_update_name(
    account_id: int,
    request: CopilotAccountUpdateNameRequest,
    current_user: User = Depends(get_current_user),
    service: CopilotService = Depends(get_copilot_service),
):
    try:
        return await service.update_account_name(current_user.id, account_id, request.account_name)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/accounts/{account_id}", summary="删除 Copilot 账号")
async def copilot_delete_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    service: CopilotService = Depends(get_copilot_service),
):
    try:
        return await service.delete_account(current_user.id, account_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/accounts/{account_id}/refresh", summary="手动刷新 Copilot token")
async def copilot_refresh_token(
    account_id: int,
    current_user: User = Depends(get_current_user),
    service: CopilotService = Depends(get_copilot_service),
):
    try:
        return await service.refresh_account_token(current_user.id, account_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/accounts/{account_id}/models", summary="查询 Copilot 可用模型")
async def copilot_list_models(
    account_id: int,
    current_user: User = Depends(get_current_user),
    service: CopilotService = Depends(get_copilot_service),
):
    try:
        return await service.list_available_models(current_user.id, account_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
