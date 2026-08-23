"""npcs/ schema: NPC 定义。"""

from enum import StrEnum

from pydantic import Field

from .common import GameDataModel, IdSlug


class NpcKind(StrEnum):
    vendor = "vendor"
    quest = "quest"
    task = "task"
    storage = "storage"
    teleport = "teleport"


class NpcDef(GameDataModel):
    id: IdSlug
    name: str = Field(min_length=1)
    # 交叉引用（校验期检查）：所在地图 id
    map_id: IdSlug
    kind: NpcKind
    # 交叉引用（校验期检查）：商店售卖的物品 id 列表（非 vendor 一般为空）
    shop_items: list[IdSlug] = Field(default_factory=list)
    dialogue: list[str] = Field(default_factory=list)
