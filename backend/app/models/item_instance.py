"""Item instance table (bag contents / equipment instances)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ItemInstance(Base):
    __tablename__: str = "item_instance"

    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("character.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # gamedata item id（D17：一切引用走 id）
    item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 堆叠数量（Metis D14 修订）；quantity > 1 视为可堆叠物品。
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # 当前耐久；非装备（药品/卷轴）为 NULL。
    durability: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lucky: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    curse: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 176 式极品词缀，如 {"dc_max": 1}（上游契约 docs/MVP/README.md :102-103）。
    extra_json: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__: tuple[object, ...] = (
        # 可堆叠物品同角色同 item_id 仅一行（合并堆叠）；装备等 quantity=1 物品不受限，
        # 允许同 item_id 多行（不同耐久/词缀实例）。
        Index(
            "uq_item_instance_stackable",
            "character_id",
            "item_id",
            unique=True,
            postgresql_where=text("quantity > 1"),
        ),
    )
