"""
GitHub Copilot 账号管理 — Pydantic Schemas
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CopilotAccountImportRequest(BaseModel):
    """导入 GitHub Copilot 账号（通过 GitHub token）"""
    github_token: str = Field(..., description="GitHub Personal Access Token 或 OAuth token")
    account_name: Optional[str] = Field(None, description="账号显示名称（可选）")
    is_shared: int = Field(0, description="0=专属，1=共享（预留）")


class CopilotAccountUpdateStatusRequest(BaseModel):
    status: int = Field(..., description="0=禁用, 1=启用")


class CopilotAccountUpdateNameRequest(BaseModel):
    account_name: str = Field(..., description="新的账号显示名称")


class CopilotAccountResponse(BaseModel):
    id: int
    user_id: int
    account_name: str
    status: int
    is_shared: int
    github_login: Optional[str] = None
    copilot_plan: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    last_refresh_at: Optional[datetime] = None
    consumed_input_tokens: int = 0
    consumed_output_tokens: int = 0
    consumed_total_tokens: int = 0
    created_at: datetime
    updated_at: datetime
    last_used_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
