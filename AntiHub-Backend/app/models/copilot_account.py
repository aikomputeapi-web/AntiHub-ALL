"""
GitHub Copilot 账号数据模型

说明：
- 账号归属到 User（user_id），支持同一用户保存多个 Copilot 账号
- 凭证（GitHub token 等）使用加密后的 JSON 字符串存储，避免明文落库
- Copilot token 是通过 GitHub token 交换得到的短期 token，自动刷新
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from sqlalchemy import String, Integer, BigInteger, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class CopilotAccount(Base):
    """GitHub Copilot 账号模型"""

    __tablename__ = "copilot_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="关联的用户ID",
    )

    account_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="账号显示名称",
    )

    status: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        comment="账号状态：0=禁用，1=启用",
    )

    is_shared: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="0=专属账号，1=共享账号（预留）",
    )

    github_login: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="GitHub 用户名",
    )

    copilot_plan: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Copilot 订阅类型（individual/business/enterprise）",
    )

    token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Copilot token 过期时间",
    )

    last_refresh_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="最后一次刷新 Copilot token 的时间",
    )

    credentials: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="加密后的凭证 JSON（github_token, copilot_token 等）",
    )

    # Token consumption tracking
    consumed_input_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False, comment="累计输入 tokens"
    )
    consumed_output_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False, comment="累计输出 tokens"
    )
    consumed_total_tokens: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False, comment="累计总 tokens"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间",
    )

    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="最后使用时间",
    )

    user: Mapped["User"] = relationship("User", back_populates="copilot_accounts")

    def __repr__(self) -> str:
        return f"<CopilotAccount(id={self.id}, user_id={self.user_id}, github_login='{self.github_login}')>"
