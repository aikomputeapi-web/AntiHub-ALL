"""
Kiro External IdP（外部身份提供商）相关的请求模型

注意：
- External IdP 指通过外部 OIDC 身份提供商（如 Microsoft Entra ID / Azure AD）
  进行认证的 Kiro 账户。
- Azure AD access_token (JWT) 直接作为 Bearer token 用于调用 AWS Q / CodeWhisperer API。
  无需 AWS SSO OIDC 中间步骤。
- Token 刷新通过外部 IdP 的 OAuth2 token endpoint 完成，需使用 Kiro API 专用 scope。
- profileArn 可选，但推荐提供以确保完整的 API 访问权限。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class KiroExternalIdpImportRequest(BaseModel):
    """
    单个 External IdP 账户导入请求

    前端通过手动填写表单或 JSON 导入提交凭据。
    Azure AD access_token 直接用于调用 AWS API。
    """

    # --- 外部 IdP（Azure AD）凭据 ---
    refresh_token: str = Field(
        ..., alias="refreshToken", description="外部 IdP（Azure AD）refresh token"
    )
    client_id: str = Field(
        ..., alias="clientId", description="外部 IdP 的 OAuth2 client ID"
    )
    token_endpoint: str = Field(
        ...,
        alias="tokenEndpoint",
        description="外部 IdP 的 token endpoint URL（例如 https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token）",
    )
    issuer_url: Optional[str] = Field(
        None,
        alias="issuerUrl",
        description="外部 IdP 的 OIDC issuer URL（可选，用于元数据记录）",
    )
    scopes: Optional[str] = Field(
        None,
        description="外部 IdP OAuth2 scopes（空格分隔），留空则自动使用 Kiro API scopes",
    )
    access_token: Optional[str] = Field(
        None,
        alias="accessToken",
        description="当前有效的 Azure AD access_token（可选，留空则在导入时自动刷新获取）",
    )
    profile_arn: Optional[str] = Field(
        None,
        alias="profileArn",
        description="AWS CodeWhisperer profile ARN（推荐提供，可从 kiro-cli 数据库获取）",
    )

    region: Optional[str] = Field(
        "us-east-1",
        description="AWS API 区域ID（用于 q.*/codewhisperer.* API 请求），默认 us-east-1",
    )
    account_name: Optional[str] = Field(
        None,
        alias="accountName",
        description="账号显示名称（可选，不传则后端使用默认值）",
    )
    is_shared: int = Field(0, alias="isShared", description="0=私有账号，1=共享账号")

    model_config = {"populate_by_name": True}


class KiroExternalIdpBatchImportRequest(BaseModel):
    """
    批量 External IdP 账户导入请求

    accounts 列表中的每个对象支持 camelCase 和 snake_case 两种字段命名风格。
    """

    accounts: List[Dict[str, Any]] = Field(
        ...,
        description="External IdP 账户凭据列表，每个对象包含 refresh_token、client_id、token_endpoint 等字段",
    )
    region: Optional[str] = Field(
        "us-east-1",
        description="全局默认 AWS API 区域ID，单个账户未指定时使用此值",
    )
    is_shared: int = Field(0, description="0=私有账号，1=共享账号")

    model_config = {"populate_by_name": True}
