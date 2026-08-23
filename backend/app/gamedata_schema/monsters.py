"""monsters/ schema: 怪物定义。"""

from pydantic import Field

from .common import GameDataModel, IdSlug


class MonsterDef(GameDataModel):
    id: IdSlug
    name: str = Field(min_length=1)
    level: int = Field(ge=1)
    hp: int = Field(ge=1)
    attack: int = Field(ge=0)
    defense: int = Field(ge=0)
    magic_defense: int = Field(ge=0)
    accuracy: int = Field(ge=0)
    agility: int = Field(ge=0)
    exp: int = Field(ge=0)
    attack_speed: int = Field(ge=0)
    move_speed: int = Field(ge=0)
    undead: bool
    boss: bool
    # None = 不重生（如任务怪）；boss 通常由 spawns 或 boss 计时器控制
    respawn_seconds: int | None = Field(default=None, ge=0)
