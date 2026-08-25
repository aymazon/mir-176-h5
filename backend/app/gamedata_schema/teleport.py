"""teleport.json schema: 传送员费率表（todo 30）。

teleport.json 是 gamedata 根目录下的**单文件**类别（与 levels.json 同模式，
区别于 maps/ 等"一目录多文件"类别）：顶层为 dict（comment 元数据 + routes
地表路线 + cave_layers 洞穴层 + bind_cities 主城清单），loader 以 "table"
单键索引（全库仅一份传送表）。

- routes：地表图 ↔ 图的直达路线费率（双向各录一条）。rate 为**费率系数**，
  实际扣费 = round(rate × balance.TELEPORT_RATE_BASE)；未录入 routes 的邻接边
  按基础费率（系数 1.0）。
- cave_layers：洞穴分层费率档（石墓 1-7 层等）。map_id 是真实 gamedata 图 id
  （MVP 单图代表整洞，depth 仅作费率档位与播报语义，不做分层地图）；
  parent_map_id 为洞穴入口所在的地表/母图；rate 随 depth 递增。
- bind_cities：主城清单——访问即自动更新 character.bind_city（回城卷绑定）。

数值纪律：rate 系数全部为本项目自创（176 社区无公开传送员价目表），
置信度 low，待 todo 37 校准。
"""

from pydantic import Field

from .common import GameDataModel, IdSlug


class TeleportRoute(GameDataModel):
    """地表路线：from → to 的单向费率条目（双向通行需各录一条）。"""

    from_map_id: IdSlug
    to_map_id: IdSlug
    rate: float = Field(ge=0)
    # 可选档位标注（如 express=跨区直达快线），纯数据语义无逻辑消费
    tier: str | None = Field(default=None)


class CaveLayer(GameDataModel):
    """洞穴层费率档：depth 越深 rate 越高（递进费率表）。"""

    map_id: IdSlug
    parent_map_id: IdSlug
    depth: int = Field(ge=1)
    rate: float = Field(ge=0)


class TeleportTable(GameDataModel):
    """teleport.json 顶层结构：来源注释 + 三张子表。"""

    comment: str = Field(min_length=1)
    routes: list[TeleportRoute] = Field(min_length=1)
    cave_layers: list[CaveLayer] = Field(min_length=1)
    bind_cities: list[IdSlug] = Field(min_length=1)
