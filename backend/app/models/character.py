"""Character table (one character per player account for MVP)."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class CharacterClass(str, enum.Enum):
    warrior = "warrior"
    wizard = "wizard"
    taoist = "taoist"


class Character(Base):
    __tablename__: str = "character"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    # DB column keeps the upstream-contract name "class"; Python attr avoids the keyword.
    class_: Mapped[CharacterClass] = mapped_column(
        "class",
        SAEnum(CharacterClass, name="character_class", native_enum=True, validate_strings=True),
        nullable=False,
    )
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    exp: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    hp: Mapped[int] = mapped_column(Integer, nullable=False)
    mp: Mapped[int] = mapped_column(Integer, nullable=False)
    gold: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    map_id: Mapped[str] = mapped_column(String(64), nullable=False)
    bag_capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=40, server_default="40")
    # 回城绑定城（gamedata map id），建号时默认 = config.BIRTH_MAP_ID（todo 3）；todo 30 传送前置。
    bind_city: Mapped[str] = mapped_column(String(64), nullable=False)
    # 心跳列：tick 落库/断线/登出时刷新（todo 15），供 seed 工具进程外判活（todo 7）。
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
