"""add buff_json to character (todo 26 消耗品限时 buff 持久化)

Revision ID: 0002_add_buff_json
Revises: 0001_initial
Create Date: 2026-08-25

不加新表（计划红线）：未过期效果实例以 JSONB 快照挂在 character 行上，
登出不清空、重登按 saved_at 墙钟折损离线时长（真实时间过期语义）。
"""

# Migration files follow alembic op.* idiom; silence strict-mode noise for generated-style code.
# pyright: reportUnusedCallResult=false, reportUnknownArgumentType=false

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_add_buff_json"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "character",
        sa.Column(
            "buff_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("character", "buff_json")
