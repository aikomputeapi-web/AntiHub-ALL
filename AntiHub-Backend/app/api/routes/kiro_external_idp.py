"""
Kiro External IdP（外部身份提供商）账户导入 API

External IdP 账户通过外部 OIDC 身份提供商（如 Microsoft Entra ID / Azure AD）
进行认证，使用外部 IdP 的 tokenEndpoint 刷新令牌，而非 AWS SSO OIDC。

路由：
- POST /api/kiro/external-idp/import — 单个 External IdP 账户导入
- POST /api/kiro/external-idp/batch-import — 批量 External IdP 账户导入
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session, get_redis
from app.cache import RedisClient
from app.models.user import User
from app.schemas.kiro_external_idp import (
    KiroExternalIdpBatchImportRequest,
    KiroExternalIdpImportRequest,
)
from app.services.kiro_service import KiroService, UpstreamAPIError

router = APIRouter(
    prefix="/api/kiro/external-idp", tags=["Kiro External IdP"]
)


def get_kiro_service(
    db: AsyncSession = Depends(get_db_session),
    redis: RedisClient = Depends(get_redis),
) -> KiroService:
    return KiroService(db, redis)


def _get_first_value(data: Dict[str, Any], keys: list[str]) -> Optional[str]:
    """从 dict 中按优先级取第一个非空字符串值（支持 camelCase/snake_case）。"""
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _validate_is_shared(is_shared: Any) -> int:
    if isinstance(is_shared, bool):
        is_shared = 1 if is_shared else 0
    try:
        is_shared_int = int(is_shared)
    except Exception:
        raise ValueError("is_shared 必须是 0 或 1")
    if is_shared_int not in (0, 1):
        raise ValueError("is_shared 必须是 0 或 1")
    return is_shared_int


def _validate_token_endpoint(url: Optional[str]) -> str:
    """校验 token endpoint 必须是 https URL。"""
    if not url:
        raise ValueError("missing token_endpoint")
    url = url.strip()
    if not url.startswith("https://"):
        raise ValueError("token_endpoint 必须使用 https 协议")
    return url


# ==================== 单个 External IdP 账户导入 ====================


@router.post(
    "/import",
    summary="导入单个 Kiro External IdP 账户凭据",
    description="提交通过外部身份提供商（如 Microsoft Entra ID）获取的 OIDC 凭据，"
    "后端解析并落库为 ExternalIdp 账号。",
)
async def import_kiro_external_idp_credentials(
    request: KiroExternalIdpImportRequest,
    current_user: User = Depends(get_current_user),
    service: KiroService = Depends(get_kiro_service),
):
    try:
        is_shared = _validate_is_shared(request.is_shared)
        token_endpoint = _validate_token_endpoint(request.token_endpoint)

        if not request.refresh_token or not request.refresh_token.strip():
            raise ValueError("missing refresh_token")
        if not request.client_id or not request.client_id.strip():
            raise ValueError("missing client_id")

        region = (request.region or "us-east-1").strip() or "us-east-1"
        machineid = secrets.token_hex(32)

        account_data: Dict[str, Any] = {
            "account_name": request.account_name or "Kiro External IdP",
            "auth_method": "ExternalIdp",
            "provider": "ExternalIdp",
            "refresh_token": request.refresh_token.strip(),
            "client_id": request.client_id.strip(),
            "token_endpoint": token_endpoint,
            "issuer_url": request.issuer_url.strip() if request.issuer_url else None,
            "scopes": request.scopes.strip() if request.scopes else None,
            "access_token": request.access_token.strip() if request.access_token else None,
            "profile_arn": request.profile_arn.strip() if request.profile_arn else None,
            "machineid": machineid,
            "region": region,
            "is_shared": is_shared,
        }

        return await service.create_account(current_user.id, account_data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except UpstreamAPIError as e:
        return JSONResponse(
            status_code=e.status_code,
            content={"error": e.extracted_message, "type": "api_error"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"导入 Kiro External IdP 账户失败: {str(e)}",
        )


# ==================== 批量 External IdP 账户导入 ====================


@router.post(
    "/batch-import",
    summary="批量导入 Kiro External IdP 账户凭据",
    description="提交包含多个 External IdP 账户凭据的 JSON 数组，逐个处理并返回每个账户的导入结果。"
    "每个账户对象支持 camelCase 和 snake_case 两种字段命名风格。",
)
async def batch_import_kiro_external_idp_credentials(
    request: KiroExternalIdpBatchImportRequest,
    current_user: User = Depends(get_current_user),
    service: KiroService = Depends(get_kiro_service),
):
    try:
        is_shared = _validate_is_shared(request.is_shared)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    results = []

    for index, account_raw in enumerate(request.accounts):
        try:
            refresh_token = _get_first_value(account_raw, ["refresh_token", "refreshToken"])
            client_id = _get_first_value(account_raw, ["client_id", "clientId"])
            token_endpoint = _get_first_value(account_raw, ["token_endpoint", "tokenEndpoint"])
            issuer_url = _get_first_value(account_raw, ["issuer_url", "issuerUrl"])
            scopes = _get_first_value(account_raw, ["scopes"])
            access_token = _get_first_value(account_raw, ["access_token", "accessToken"])
            profile_arn = _get_first_value(account_raw, ["profile_arn", "profileArn"])
            region = _get_first_value(account_raw, ["region"]) or request.region or "us-east-1"
            account_name = _get_first_value(account_raw, ["account_name", "accountName"]) or "Kiro External IdP"

            if not refresh_token:
                raise ValueError("missing refresh_token")
            if not client_id:
                raise ValueError("missing client_id")
            token_endpoint = _validate_token_endpoint(token_endpoint)

            machineid = secrets.token_hex(32)

            account_data: Dict[str, Any] = {
                "account_name": account_name,
                "auth_method": "ExternalIdp",
                "provider": "ExternalIdp",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "token_endpoint": token_endpoint,
                "issuer_url": issuer_url,
                "scopes": scopes,
                "access_token": access_token,
                "profile_arn": profile_arn,
                "machineid": machineid,
                "region": region,
                "is_shared": is_shared,
            }

            data = await service.create_account(current_user.id, account_data)
            results.append({"index": index, "success": True, "data": data})

        except ValueError as e:
            results.append({"index": index, "success": False, "error": str(e)})
        except UpstreamAPIError as e:
            results.append({"index": index, "success": False, "error": e.extracted_message})
        except Exception as e:
            results.append({"index": index, "success": False, "error": str(e)})

    return {"results": results}
