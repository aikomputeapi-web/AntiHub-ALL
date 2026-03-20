"""add copilot_accounts table

Revision ID: g1a2b3c4d5e6
Revises: f48b0825fd00
Create Date: 2026-03-13

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g1a2b3c4d5e6"
down_revision: Union[str, None] = "f48b0825fd00"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "copilot_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("account_name", sa.String(255), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_shared", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("github_login", sa.String(255), nullable=True, index=True),
        sa.Column("copilot_plan", sa.String(100), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credentials", sa.Text(), nullable=False),
        sa.Column("consumed_input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("consumed_output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("consumed_total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("copilot_accounts")
