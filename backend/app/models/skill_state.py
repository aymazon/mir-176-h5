"""Skill state table (per-character skill level/exp, 0->3 by usage)."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class SkillState(Base):
    __tablename__: str = "skill_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("character.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill_id: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    exp: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__: tuple[object, ...] = (
        UniqueConstraint("character_id", "skill_id", name="uq_skill_state_character_skill"),
    )
