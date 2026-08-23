"""spawns/ schema: 刷怪点定义。

loader 以 "{map_id}:{monster_id}" 为键索引。
"""

from pydantic import Field

from .common import GameDataModel, IdSlug


class SpawnLocation(GameDataModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class SpawnEntry(GameDataModel):
    # 交叉引用（校验期检查）：地图 id / 怪物 id
    map_id: IdSlug
    monster_id: IdSlug
    density: int = Field(ge=1)
    respawn_seconds: int = Field(ge=0)
    count: int = Field(ge=1)
    locations: list[SpawnLocation] = Field(min_length=1)
