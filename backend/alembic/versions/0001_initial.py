"""initial: 7 tables (player/character/equipment_slot/item_instance/skill_state/quest_state/boss_respawn)

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-23

Enum lifecycle is managed explicitly (create before tables, drop after tables)
because op.drop_table does not reliably drop PG native enum types.
"""

# Migration files follow alembic op.* idiom; silence strict-mode noise for generated-style code.
# pyright: reportUnusedCallResult=false, reportUnknownArgumentType=false

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# create_type=False：禁止 create_table/drop_table 事件监听器自动 CREATE/DROP TYPE，
# 类型生命周期完全由下方显式 .create()/.drop() 管理（否则与显式调用双创建，DuplicateObjectError）。
character_class = postgresql.ENUM(
    "warrior", "wizard", "taoist", name="character_class", create_type=False
)
equipment_slot_type = postgresql.ENUM(
    "weapon",
    "armor",
    "helmet",
    "necklace",
    "bracelet_l",
    "bracelet_r",
    "ring_l",
    "ring_r",
    "belt",
    "boots",
    name="equipment_slot_type",
    create_type=False,
)
quest_status = postgresql.ENUM(
    "not_started", "in_progress", "completed", name="quest_status", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    character_class.create(bind, checkfirst=True)
    equipment_slot_type.create(bind, checkfirst=True)
    quest_status.create(bind, checkfirst=True)

    op.create_table(
        "player",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_player"),
        sa.UniqueConstraint("username", name="uq_player_username"),
    )

    op.create_table(
        "character",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("class", character_class, nullable=False),
        sa.Column("level", sa.Integer(), server_default="1", nullable=False),
        sa.Column("exp", sa.Integer(), server_default="0", nullable=False),
        sa.Column("hp", sa.Integer(), nullable=False),
        sa.Column("mp", sa.Integer(), nullable=False),
        sa.Column("gold", sa.Integer(), server_default="0", nullable=False),
        sa.Column("map_id", sa.String(length=64), nullable=False),
        sa.Column("bag_capacity", sa.Integer(), server_default="40", nullable=False),
        sa.Column("bind_city", sa.String(length=64), nullable=False),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["player_id"], ["player.id"], name="fk_character_player_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_character"),
        sa.UniqueConstraint("name", name="uq_character_name"),
    )
    op.create_index("ix_character_player_id", "character", ["player_id"])

    op.create_table(
        "item_instance",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("durability", sa.Integer(), nullable=True),
        sa.Column("lucky", sa.Integer(), server_default="0", nullable=False),
        sa.Column("curse", sa.Integer(), server_default="0", nullable=False),
        sa.Column("extra_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["character_id"], ["character.id"], name="fk_item_instance_character_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_item_instance"),
    )
    op.create_index("ix_item_instance_character_id", "item_instance", ["character_id"])
    # 可堆叠物品(quantity>1)同角色同 item_id 唯一；quantity=1 的装备实例不受限。
    op.create_index(
        "uq_item_instance_stackable",
        "item_instance",
        ["character_id", "item_id"],
        unique=True,
        postgresql_where=sa.text("quantity > 1"),
    )

    op.create_table(
        "equipment_slot",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("slot", equipment_slot_type, nullable=False),
        sa.Column("item_instance_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["character_id"], ["character.id"], name="fk_equipment_slot_character_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_instance_id"],
            ["item_instance.id"],
            name="fk_equipment_slot_item_instance_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_equipment_slot"),
        sa.UniqueConstraint("character_id", "slot", name="uq_equipment_slot_character_slot"),
    )

    op.create_table(
        "skill_state",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.String(length=64), nullable=False),
        sa.Column("level", sa.Integer(), server_default="0", nullable=False),
        sa.Column("exp", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["character_id"], ["character.id"], name="fk_skill_state_character_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_skill_state"),
        sa.UniqueConstraint("character_id", "skill_id", name="uq_skill_state_character_skill"),
    )
    op.create_index("ix_skill_state_character_id", "skill_state", ["character_id"])

    op.create_table(
        "quest_state",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("character_id", sa.Integer(), nullable=False),
        sa.Column("quest_id", sa.String(length=64), nullable=False),
        sa.Column("status", quest_status, server_default="not_started", nullable=False),
        sa.Column("progress", postgresql.JSONB(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["character_id"], ["character.id"], name="fk_quest_state_character_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quest_state"),
        sa.UniqueConstraint("character_id", "quest_id", name="uq_quest_state_character_quest"),
    )
    op.create_index("ix_quest_state_character_id", "quest_state", ["character_id"])

    op.create_table(
        "boss_respawn",
        sa.Column("boss_id", sa.String(length=64), nullable=False),
        sa.Column("map_id", sa.String(length=64), nullable=False),
        sa.Column("killed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("respawn_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("boss_id", name="pk_boss_respawn"),
    )


def downgrade() -> None:
    op.drop_table("boss_respawn")
    op.drop_index("ix_quest_state_character_id", table_name="quest_state")
    op.drop_table("quest_state")
    op.drop_index("ix_skill_state_character_id", table_name="skill_state")
    op.drop_table("skill_state")
    op.drop_table("equipment_slot")
    op.drop_index("uq_item_instance_stackable", table_name="item_instance")
    op.drop_index("ix_item_instance_character_id", table_name="item_instance")
    op.drop_table("item_instance")
    op.drop_index("ix_character_player_id", table_name="character")
    op.drop_table("character")
    op.drop_table("player")

    bind = op.get_bind()
    quest_status.drop(bind, checkfirst=True)
    equipment_slot_type.drop(bind, checkfirst=True)
    character_class.drop(bind, checkfirst=True)
