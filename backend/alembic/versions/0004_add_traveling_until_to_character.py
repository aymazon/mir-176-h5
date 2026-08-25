"""add traveling_until to character (todo 30 传送员/跑图倒计时)

Revision ID: 0004_traveling_until
Revises: 0003_quest_delivered
Create Date: 2026-08-25

跨图跑动"行进中"倒计时的镜像落库列：warp 时写 now + TRAVEL_SECONDS 随机，
到点切图同事务清 NULL。活跃会话的事实源是进程内注册表（app/sim/travel.py），
本列仅作审计与跨进程可见；进程重启即取消行进，不据此恢复倒计时。
"""

# Migration files follow alembic op.* idiom; silence strict-mode noise for generated-style code.
# pyright: reportUnusedCallResult=false, reportUnknownArgumentType=false

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_traveling_until"
down_revision: str | Sequence[str] | None = "0003_quest_delivered"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "character",
        sa.Column("traveling_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("character", "traveling_until")
