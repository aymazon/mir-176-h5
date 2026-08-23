"""Shared primitives for gamedata schemas."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# 一切 id 均为 slug（小写字母/数字/中划线/下划线），交叉引用只允许使用 id（D17）。
IdSlug = Annotated[str, Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")]


class GameDataModel(BaseModel):
    """Base for all gamedata schema models: forbid undeclared fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
