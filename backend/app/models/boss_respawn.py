"""Boss respawn table — the only cross-session world state (per design doc :71)."""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class BossRespawn(Base):
    __tablename__: str = "boss_respawn"

    # gamedata boss id，单行跨实例存活（如沃玛教主 60min 重生）。
    boss_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    map_id: Mapped[str] = mapped_column(String(64), nullable=False)
    killed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    respawn_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
