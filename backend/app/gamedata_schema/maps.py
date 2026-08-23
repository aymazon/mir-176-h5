"""maps/ schema: 地图定义。"""

from pydantic import Field

from .common import GameDataModel, IdSlug


class MapDef(GameDataModel):
    id: IdSlug
    name: str = Field(min_length=1)
    level_min: int = Field(ge=0)
    level_max: int = Field(ge=0)
    region: str = Field(min_length=1)
    # 交叉引用（校验期检查）：相邻地图 id 列表
    adjacent: list[IdSlug] = Field(default_factory=list)
    # 交叉引用（校验期检查）：本图 BOSS 怪 id 列表
    boss_spawns: list[IdSlug] = Field(default_factory=list)
