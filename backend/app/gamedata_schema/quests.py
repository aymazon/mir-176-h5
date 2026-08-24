"""quests/ schema: 任务定义。"""

from enum import StrEnum

from pydantic import Field

from .common import GameDataModel, IdSlug


class QuestKind(StrEnum):
    kill = "kill"
    collect = "collect"
    dialogue = "dialogue"
    profession = "profession"


class QuestClass(StrEnum):
    """职业限定（可选）：None 表示不限职业。与 skills.SkillClass 值域一致，
    但独立定义以免 schema 互相 import（task 4 约定）。"""

    warrior = "warrior"
    wizard = "wizard"
    taoist = "taoist"
    all = "all"


class BossInstanceSpec(GameDataModel):
    """职业专属 BOSS 挑战参数（如命运之刃：挑战指定分区的弱化分身）。

    zone_id 指向任务战斗发生的分区地图 id；石墓等分区由 todo 21 落地，
    故 loader 不做 maps 交叉引用（避免阻塞数据先行录入）。
    """

    zone_id: IdSlug
    # True = 挑战的是弱化分身（非本体 BOSS，不掉落本体掉落表）
    weak_clone: bool = False


class QuestDef(GameDataModel):
    id: IdSlug
    name: str = Field(min_length=1)
    kind: QuestKind
    level_req: int = Field(default=0, ge=0)
    # 职业限定（task 20 新增，可选）：命运之刃=warrior、35 级技能书引导按职业
    required_class: QuestClass | None = None
    # 交叉引用（校验期按 kind 检查）：kill→monster, collect→item,
    # dialogue→npc, profession→skill
    target_id: IdSlug
    target_count: int = Field(default=1, ge=1)
    reward_gold: int = Field(default=0, ge=0)
    reward_exp: int = Field(default=0, ge=0)
    # 交叉引用（校验期检查）：奖励物品 id 列表
    reward_items: list[IdSlug] = Field(default_factory=list)
    # 阶段文案：accept/progress/complete 等
    dialogue: dict[str, str] = Field(default_factory=dict)
    # 职业专属 BOSS 挑战参数（task 20 新增，可选；命运之刃专用）
    boss_instance: BossInstanceSpec | None = None
