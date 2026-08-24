"""items/ schema: 物品定义。"""

from enum import StrEnum

from pydantic import Field

from .common import GameDataModel, IdSlug


class ItemKind(StrEnum):
    weapon = "weapon"
    armor = "armor"
    helmet = "helmet"
    necklace = "necklace"
    bracelet = "bracelet"
    ring = "ring"
    belt = "belt"
    boots = "boots"
    potion = "potion"
    scroll = "scroll"
    material = "material"
    book = "book"


class ItemDef(GameDataModel):
    id: IdSlug
    name: str = Field(min_length=1)
    kind: ItemKind
    need_level: int = Field(default=0, ge=0)
    need_strength: int = Field(default=0, ge=0)
    need_magic: int = Field(default=0, ge=0)
    need_taoism: int = Field(default=0, ge=0)
    weight: int = Field(default=0, ge=0)
    stackable: bool
    # 0 = 无耐久概念（药品/卷轴/材料）
    max_durability: int = Field(default=0, ge=0)
    sell_price: int = Field(ge=0)
    buy_price: int = Field(ge=0)
    # 自由属性包（dc_min/dc_max/mc_min/... 等战斗修正），键为属性名
    stats: dict[str, int] = Field(default_factory=dict)
    # 极品词缀上限条数
    prefix_max: int = Field(default=0, ge=0)
