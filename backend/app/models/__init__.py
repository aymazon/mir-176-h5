"""DB models package — importing this module registers all tables on Base.metadata."""

from .base import Base
from .boss_respawn import BossRespawn
from .character import Character, CharacterClass
from .equipment_slot import EquipmentSlot, EquipmentSlotType
from .item_instance import ItemInstance
from .player import Player
from .quest_state import QuestState, QuestStatus
from .skill_state import SkillState

__all__ = [
    "Base",
    "Player",
    "Character",
    "CharacterClass",
    "EquipmentSlot",
    "EquipmentSlotType",
    "ItemInstance",
    "SkillState",
    "QuestState",
    "QuestStatus",
    "BossRespawn",
]
