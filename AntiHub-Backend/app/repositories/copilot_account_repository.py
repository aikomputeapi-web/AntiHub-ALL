"""
GitHub Copilot 账号数据仓储

约定：
- Repository 层不负责 commit()，事务由调用方（依赖注入的 get_db）统一管理
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.copilot_account import CopilotAccount


class CopilotAccountRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_by_user_id(self, user_id: int) -> Sequence[CopilotAccount]:
        result = await self.db.execute(
            select(CopilotAccount)
            .where(CopilotAccount.user_id == user_id)
            .order_by(CopilotAccount.id.asc())
        )
        return result.scalars().all()

    async def list_enabled_by_user_id(self, user_id: int) -> Sequence[CopilotAccount]:
        result = await self.db.execute(
            select(CopilotAccount)
            .where(CopilotAccount.user_id == user_id, CopilotAccount.status == 1)
            .order_by(CopilotAccount.id.asc())
        )
        return result.scalars().all()

    async def get_by_id(self, account_id: int) -> Optional[CopilotAccount]:
        result = await self.db.execute(
            select(CopilotAccount).where(CopilotAccount.id == account_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_and_user_id(self, account_id: int, user_id: int) -> Optional[CopilotAccount]:
        result = await self.db.execute(
            select(CopilotAccount).where(
                CopilotAccount.id == account_id,
                CopilotAccount.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_user_id_and_login(self, user_id: int, github_login: str) -> Optional[CopilotAccount]:
        result = await self.db.execute(
            select(CopilotAccount).where(
                CopilotAccount.user_id == user_id,
                CopilotAccount.github_login == github_login,
            )
        )
        return result.scalar_one_or_none()

    async def create(self, account: CopilotAccount) -> CopilotAccount:
        self.db.add(account)
        await self.db.flush()
        await self.db.refresh(account)
        return account

    async def delete(self, account: CopilotAccount) -> None:
        await self.db.delete(account)
        await self.db.flush()
