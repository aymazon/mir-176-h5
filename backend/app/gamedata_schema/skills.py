"""skills/ schema: 技能定义。"""

from enum import StrEnum

from pydantic import Field, model_validator

from .common import GameDataModel, IdSlug


class SkillClass(StrEnum):
    warrior = "warrior"
    wizard = "wizard"
    taoist = "taoist"
    all = "all"


class SkillTarget(StrEnum):
    self_ = "self"
    enemy = "enemy"
    ally = "ally"
    ground = "ground"
    aoe = "aoe"


class SkillDef(GameDataModel):
    id: IdSlug
    name: str = Field(min_length=1)
    # JSON 键为 "class"（Python 关键字冲突，字段名用 class_）
    class_: SkillClass = Field(alias="class")
    learn_level: int = Field(ge=0)
    mp_cost: int = Field(ge=0)
    cooldown_seconds: int = Field(default=0, ge=0)
    max_level: int = Field(ge=1)
    # 长度必须 == max_level（0→1 级、1→2 级 ... 各级所需熟练经验）
    level_exp_required: list[int] = Field(min_length=1)
    target: SkillTarget
    # 效果参数包（倍率/dot/召唤等），键为参数名
    effect_params: dict[str, int | float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _level_exp_matches_max_level(self) -> "SkillDef":
        if len(self.level_exp_required) != self.max_level:
            raise ValueError(
                f"level_exp_required length {len(self.level_exp_required)}"
                f" != max_level {self.max_level}"
            )
        return self
