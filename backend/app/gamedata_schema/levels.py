"""levels.json schema: 等级经验表（todo 17 双源比对录入）。

levels.json 是 gamedata 根目录下的**单文件**类别（区别于 maps/ 等"一目录多文件"
类别）：顶层为 dict（comment 元数据 + levels 有序列表），loader 以 str(level)
为键索引。主源 = GeeM2 Exps.ini；对照源 = 经典官方 1-62 表（双源比对见
scripts/compare_exp_sources.py 与 docs/combat-formulas.md §0.1）。
"""

from pydantic import Field

from .common import GameDataModel


class LevelEntry(GameDataModel):
    """单级经验条目。

    exp_to_next = 从本级升到下一级所需经验；0 = 满级哨兵
    （当前 MVP 等级上限 40，40 级不再有下一级）。
    """

    level: int = Field(ge=1)
    exp_to_next: int = Field(ge=0)


class LevelTable(GameDataModel):
    """levels.json 顶层结构：来源注释 + 按等级升序的条目列表。"""

    comment: str = Field(min_length=1)
    levels: list[LevelEntry] = Field(min_length=1)
