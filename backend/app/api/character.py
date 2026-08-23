"""角色建号（战/法/道三职业）与查询：单账号单角色。

建号默认值：level=1 / exp=gold=0 / bag_capacity=40 走 models 默认；
hp/mp 模型未给默认，此处显式置 0；bind_city 与出生地图 = ``config.BIRTH_MAP_ID``
（稳定常量 "yin-xing-cun"，禁止环境变量覆盖）；last_active_at 建号即刷新。
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPlayerId, DbSession
from app.core.config import BIRTH_MAP_ID
from app.models.character import Character, CharacterClass

router = APIRouter(prefix="/api/character", tags=["character"])


class CreateCharacterIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=32)
    # 上游契约 JSON 键为 "class"（Python 关键字），与 gamedata_schema 同一处理方式。
    class_: CharacterClass = Field(alias="class")


class CharacterOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: int
    name: str
    class_: CharacterClass = Field(alias="class")
    level: int
    exp: int
    hp: int
    mp: int
    gold: int
    map_id: str
    bind_city: str
    bag_capacity: int


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_character(
    payload: CreateCharacterIn, db: DbSession, player_id: CurrentPlayerId
) -> CharacterOut:
    # 单账号单角色：player_id 无 DB 唯一约束（todo 2 迁移仅普通索引），应用层预检。
    existing = (
        await db.execute(select(Character.id).where(Character.player_id == player_id))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="该账号已拥有角色"
        )

    character = Character(
        player_id=player_id,
        name=payload.name,
        class_=payload.class_,
        hp=0,
        mp=0,
        map_id=BIRTH_MAP_ID,
        bind_city=BIRTH_MAP_ID,
        last_active_at=func.now(),
    )
    db.add(character)
    try:
        await db.commit()
    except IntegrityError as exc:  # uq_character_name（重复角色名）
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="角色名已被占用"
        ) from exc
    await db.refresh(character)  # 取回 server_default / SQL 表达式列
    return CharacterOut.model_validate(character)


@router.get("/me")
async def get_my_character(db: DbSession, player_id: CurrentPlayerId) -> CharacterOut:
    character = (
        await db.execute(select(Character).where(Character.player_id == player_id))
    ).scalar_one_or_none()
    if character is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="尚未建号")
    return CharacterOut.model_validate(character)
