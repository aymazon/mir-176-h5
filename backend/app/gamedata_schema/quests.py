"""quests/ schema: 任务定义。"""

from enum import StrEnum

from pydantic import Field

from .common import GameDataModel, IdSlug


class QuestKind(StrEnum):
    kill = "kill"
    collect = "collect"
    dialogue = "dialogue"
    profession = "profession"


class QuestDef(GameDataModel):
    id: IdSlug
    name: str = Field(min_length=1)
    kind: QuestKind
    level_req: int = Field(default=0, ge=0)
    # 交叉引用（校验期按 kind 检查）：kill→monster, collect→item,
    # dialogue→npc, profession→skill
    target_id: IdSlug
    target_count: int = Field(default=1, ge=1)
    reward_gold: int = Field(default=0, ge=0)
    reward_exp: int = Field(default=0, ge=0)
    # 交叉引用（校验期检查）：奖励物品 id 列表
    reward_items: list[IdSlug] = []
    # 阶段文案：accept/progress/complete 等
    dialogue: dict[str, str] = {}
