"""Equipment slot table (10 slots per character, 176-style)."""

import enum

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class EquipmentSlotType(str, enum.Enum):
    weapon = "weapon"
    armor = "armor"
    helmet = "helmet"
    necklace = "necklace"
    bracelet_l = "bracelet_l"
    bracelet_r = "bracelet_r"
    ring_l = "ring_l"
    ring_r = "ring_r"
    belt = "belt"
    boots = "boots"


class EquipmentSlot(Base):
    __tablename__: str = "equipment_slot"

    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("character.id", ondelete="CASCADE"), nullable=False
    )
    slot: Mapped[EquipmentSlotType] = mapped_column(
        SAEnum(EquipmentSlotType, name="equipment_slot_type", native_enum=True, validate_strings=True),
        nullable=False,
    )
    item_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("item_instance.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__: tuple[object, ...] = (
        UniqueConstraint("character_id", "slot", name="uq_equipment_slot_character_slot"),
    )
