"""Quest state table (sparse: only quests with progress get a row)."""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class QuestStatus(str, enum.Enum):
    not_started = "not_started"
    in_progress = "in_progress"
    completed = "completed"


class QuestState(Base):
    __tablename__: str = "quest_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("character.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quest_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[QuestStatus] = mapped_column(
        SAEnum(QuestStatus, name="quest_status", native_enum=True, validate_strings=True),
        nullable=False,
        default=QuestStatus.not_started,
        server_default=QuestStatus.not_started.value,
    )
    # 击杀/收集进度，如 {"killed": 3, "need": 20}。
    progress: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__: tuple[object, ...] = (
        UniqueConstraint("character_id", "quest_id", name="uq_quest_state_character_quest"),
    )
