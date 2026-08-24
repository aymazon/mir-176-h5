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


class ShopEntry(GameDataModel):
    """商店单品条目（task 20 新增）：在 shop_items（纯 id 列表）之上补充售卖规则。

    price 缺省时回退物品 buy_price；min_city_level 为 §8 城市等级售卖规则：
    仅城市等级 ≥ 该值的主城商店出售（高级药只在高级城）。
    """

    item_id: IdSlug
    price: int = Field(default=0, ge=0)
    stock: int | None = None
    min_city_level: int = Field(default=1, ge=1)


class NpcDef(GameDataModel):
    id: IdSlug
    name: str = Field(min_length=1)
    # 交叉引用（校验期检查）：所在地图 id
    map_id: IdSlug
    kind: NpcKind
    # 城市等级（task 20 新增，可选）：§8 售卖规则用；非城镇 NPC 可省略
    city_level: int | None = Field(default=None, ge=1)
    # 坐标（task 20 新增，可选）：静态区域图上的展示点 [x, y]
    coords: tuple[int, int] | None = None
    # 服务列表（task 20 新增，可选）：dialog/shop/sell/buy/repair/teleport 等
    services: list[str] = Field(default_factory=list)
    # 交叉引用（校验期检查）：商店售卖的物品 id 列表（非 vendor 一般为空）
    shop_items: list[IdSlug] = Field(default_factory=list)
    # 商店售卖规则明细（task 20 新增，可选）：与 shop_items 保持同 id 集
    shop_entries: list[ShopEntry] = Field(default_factory=list)
    dialogue: list[str] = Field(default_factory=list)
