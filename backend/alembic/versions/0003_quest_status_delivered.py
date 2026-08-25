"""add 'delivered' value to quest_status enum (todo 29 四态状态机终点)

Revision ID: 0003_quest_delivered
Revises: 0002_add_buff_json
Create Date: 2026-08-25

PG 12+ 允许事务内 ALTER TYPE ADD VALUE（新值不能同事务使用——本迁移只加值，
使用发生在后续应用查询，安全）。降级为 no-op：PG 不支持删 enum 值，
残留值无害（列约束仍按值域校验）。
"""

from collections.abc import Sequence

# Migration files follow alembic op.* idiom; silence strict-mode noise for generated-style code.
# pyright: reportUnusedCallResult=false, reportUnknownArgumentType=false

from alembic import op

revision: str = "0003_quest_delivered"
down_revision: str | Sequence[str] | None = "0002_add_buff_json"
branch_labels: str | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE quest_status ADD VALUE IF NOT EXISTS 'delivered'")


def downgrade() -> None:
    # PG 不支持 ALTER TYPE DROP VALUE；delivered 残留不影响三态回滚语义。
    pass
