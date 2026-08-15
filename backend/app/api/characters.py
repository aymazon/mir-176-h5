"""Character CRUD endpoints: list, create, and confirm-by-name soft delete.

All routes require a valid account JWT (design 3.5). Level-1 base HP/MP per
class come from design 4.2; new characters spawn at the Biqi City center on
m3 (decision D23); each account is capped at three non-deleted characters
(decision D11). Deletes are soft: only ``deleted_at`` is set.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, ClassVar, Final

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import JwtClaims, require_account
from app.db.pool import get_pool

MAX_CHARACTERS_PER_ACCOUNT: Final[int] = 3
DEFAULT_BOUND_CITY: Final[str] = "bichon"
DEFAULT_MAP_ID: Final[str] = "m3"
DEFAULT_SPAWN_X: Final[int] = 150
DEFAULT_SPAWN_Y: Final[int] = 150

_CHARACTER_COLUMNS: Final[str] = (
    "id, name, class, level, exp, hp, mp, gold, bound_city, map_id, x, y"
)

_LIST_QUERY: Final[str] = (
    f"SELECT {_CHARACTER_COLUMNS} FROM character "
    "WHERE account_id = $1 AND deleted_at IS NULL ORDER BY id"
)

_CREATE_QUERY: Final[str] = (
    "INSERT INTO character (account_id, name, class, level, exp, hp, mp, gold, "
    f"bound_city, map_id, x, y) "
    "VALUES ($1, $2, $3, 1, 0, $4, $5, 0, $6, $7, $8, $9) "
    f"RETURNING {_CHARACTER_COLUMNS}"
)

_ACTIVE_COUNT_QUERY: Final[str] = (
    "SELECT count(*) FROM character WHERE account_id = $1 AND deleted_at IS NULL"
)

_FETCH_OWNED_QUERY: Final[str] = (
    "SELECT name FROM character WHERE id = $1 AND account_id = $2 AND deleted_at IS NULL"
)

_SOFT_DELETE_QUERY: Final[str] = "UPDATE character SET deleted_at = now() WHERE id = $1"


class CharacterClass(StrEnum):
    """The three 1.76 playable classes (design 1.1)."""

    WARRIOR = "warrior"
    MAGE = "mage"
    TAOIST = "taoist"


@dataclass(frozen=True, slots=True)
class BaseStats:
    """Level-1 base HP/MP for one class (design 4.2)."""

    hp: int
    mp: int


# Level-1 base stats per class (design 4.2: warrior 33/11, mage 11/33, taoist 22/22).
_LEVEL1_BASE_STATS: Final[dict[CharacterClass, BaseStats]] = {
    CharacterClass.WARRIOR: BaseStats(hp=33, mp=11),
    CharacterClass.MAGE: BaseStats(hp=11, mp=33),
    CharacterClass.TAOIST: BaseStats(hp=22, mp=22),
}


class CharacterCreateRequest(BaseModel):
    """Create payload: a 1-16 char name plus one of the three classes."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, populate_by_name=True)

    name: str = Field(min_length=1, max_length=16)
    character_class: CharacterClass = Field(alias="class")


class CharacterDeleteRequest(BaseModel):
    """Confirm-by-name payload (design 3.5: the client types the character name)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, populate_by_name=True)

    name: str = Field(min_length=1, max_length=16)


class CharacterOut(BaseModel):
    """Public character view returned by list and create."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, populate_by_name=True)

    id: int
    name: str
    character_class: CharacterClass = Field(alias="class")
    level: int
    exp: int
    hp: int
    mp: int
    gold: int
    bound_city: str
    map_id: str
    x: int
    y: int


def _character_from_row(row: asyncpg.Record) -> CharacterOut:
    """Build the public view from a character row (boundary: DB value -> typed)."""
    return CharacterOut.model_validate(
        {
            "id": int(row["id"]),
            "name": row["name"],
            "class": CharacterClass(row["class"]),
            "level": int(row["level"]),
            "exp": int(row["exp"]),
            "hp": int(row["hp"]),
            "mp": int(row["mp"]),
            "gold": int(row["gold"]),
            "bound_city": row["bound_city"],
            "map_id": row["map_id"],
            "x": int(row["x"]),
            "y": int(row["y"]),
        }
    )


router = APIRouter()


@router.get("/characters", response_model=list[CharacterOut])
async def list_characters(
    claims: Annotated[JwtClaims, Depends(require_account)],
) -> list[CharacterOut]:
    """List the account's non-deleted characters, ordered by id."""
    pool = get_pool()
    rows = await pool.fetch(_LIST_QUERY, claims.account_id)
    return [_character_from_row(row) for row in rows]


@router.post("/characters", status_code=201, response_model=CharacterOut)
async def create_character(
    body: CharacterCreateRequest,
    claims: Annotated[JwtClaims, Depends(require_account)],
) -> CharacterOut:
    """Create a level-1 character at the Biqi City center (D23).

    409 when the account already has three non-deleted characters (D11) or
    the name is already taken.
    """
    pool = get_pool()
    active_count: int = await pool.fetchval(_ACTIVE_COUNT_QUERY, claims.account_id)
    if active_count >= MAX_CHARACTERS_PER_ACCOUNT:
        raise HTTPException(status_code=409, detail="character limit reached (3)")
    base = _LEVEL1_BASE_STATS[body.character_class]
    try:
        row = await pool.fetchrow(
            _CREATE_QUERY,
            claims.account_id,
            body.name,
            body.character_class.value,
            base.hp,
            base.mp,
            DEFAULT_BOUND_CITY,
            DEFAULT_MAP_ID,
            DEFAULT_SPAWN_X,
            DEFAULT_SPAWN_Y,
        )
    except asyncpg.UniqueViolationError as error:
        raise HTTPException(status_code=409, detail="character name already taken") from error
    return _character_from_row(row)


@router.delete("/characters/{character_id}")
async def delete_character(
    character_id: int,
    body: CharacterDeleteRequest,
    claims: Annotated[JwtClaims, Depends(require_account)],
) -> None:
    """Soft-delete a character with confirm-by-name (design 3.5).

    404 when the id is unknown or belongs to another account; 400 when the
    confirm name does not match the stored name. The row is kept, with
    ``deleted_at`` set.
    """
    pool = get_pool()
    row = await pool.fetchrow(_FETCH_OWNED_QUERY, character_id, claims.account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="character not found")
    if row["name"] != body.name:
        raise HTTPException(status_code=400, detail="character name does not match")
    await pool.execute(_SOFT_DELETE_QUERY, character_id)
