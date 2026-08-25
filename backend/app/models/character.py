"""Character table (one character per player account for MVP)."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
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
    # 行进中截止时刻（todo 30）：跨图跑动倒计时（UTC 绝对时刻）；NULL = 未在行进中。
    # 活跃会话的事实源是进程内注册表（sim/travel.py），本列为同事务镜像落库。
    traveling_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 心跳列：tick 落库/断线/登出时刷新（todo 15），供 seed 工具进程外判活（todo 7）。
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # 限时 buff 持久化（todo 26）：未过期效果实例快照 + saved_at 墙钟，登出不清空、
    # 重登按真实时间折损（离线时长计入 remaining_ticks）。结构：
    # {"buffs": [{definition_id, owner_id, remaining_ticks, params, ...}], "saved_at": float}
    buff_json: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default="{}", default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
