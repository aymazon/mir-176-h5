"""drops/ schema: 怪物掉落表（加权随机）。

每个文件对应一只怪物的整张掉落表；loader 以 monster_id 为键索引。
"""

from pydantic import Field

from .common import GameDataModel, IdSlug


class DropItem(GameDataModel):
    # item_id=None 表示掉金币（使用 gold_min/gold_max）；否则掉物品（min/max_qty）
    item_id: IdSlug | None
    weight: int = Field(ge=1)
    min_qty: int = Field(default=0, ge=0)
    max_qty: int = Field(default=0, ge=0)
    gold_min: int = Field(default=0, ge=0)
    gold_max: int = Field(default=0, ge=0)


class DropEntry(GameDataModel):
    # 交叉引用（校验期检查）：所属怪物 id
    monster_id: IdSlug
    entries: list[DropItem] = Field(min_length=1)
