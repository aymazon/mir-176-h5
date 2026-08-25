"""Gamedata Pydantic schemas（C3a）。

每类静态游戏数据一个模型模块；一切交叉引用走 id（D17），
跨文件引用完整性由 app.gamedata_loader 在加载期校验。
"""

from .common import GameDataModel, IdSlug
from .drops import DropEntry, DropItem
from .items import ItemDef, ItemKind
from .levels import LevelEntry, LevelTable
from .maps import MapDef
from .monsters import MonsterDef
from .npcs import NpcDef, NpcKind, ShopEntry
from .quests import BossInstanceSpec, QuestClass, QuestDef, QuestKind
from .skills import SkillClass, SkillDef, SkillTarget
from .spawns import SpawnEntry, SpawnLocation
from .teleport import CaveLayer, TeleportRoute, TeleportTable

__all__ = [
    "BossInstanceSpec",
    "CaveLayer",
    "DropEntry",
    "DropItem",
    "GameDataModel",
    "IdSlug",
    "ItemDef",
    "ItemKind",
    "LevelEntry",
    "LevelTable",
    "MapDef",
    "MonsterDef",
    "NpcDef",
    "NpcKind",
    "QuestClass",
    "QuestDef",
    "QuestKind",
    "ShopEntry",
    "SkillClass",
    "SkillDef",
    "SkillTarget",
    "SpawnEntry",
    "SpawnLocation",
    "TeleportRoute",
    "TeleportTable",
]
