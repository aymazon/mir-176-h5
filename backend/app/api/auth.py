"""注册 / 登录 / 登出：HttpOnly cookie 会话 + 标准库 pbkdf2_hmac 口令散列。

计划约束：禁用 JWT、禁用 bcrypt/passlib —— 散列只用 ``hashlib.pbkdf2_hmac``，
会话只用服务端内存表（见 :mod:`app.api.deps`），零新增依赖。
"""

import hashlib
import hmac
import secrets

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    SESSION_COOKIE_NAME,
    CurrentPlayerId,
    DbSession,
    create_auth_session,
    delete_auth_session,
)
from app.models.character import Character
from app.models.player import Player
from app.sim.tick_loop import shutdown_session

router = APIRouter(prefix="/api/auth", tags=["auth"])

_PBKDF2_ITERATIONS = 260_000
_SALT_BYTES = 16
_HASH_ALGO = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    """格式：``pbkdf2_sha256$<iterations>$<salt-hex>$<digest-hex>``（自描述，便于未来调参）。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"{_HASH_ALGO}${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations_text, salt_hex, digest_hex = stored.split("$")
        iterations = int(iterations_text)
    except (ValueError, TypeError):
        return False
    if algo != _HASH_ALGO:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), iterations)
    return hmac.compare_digest(digest.hex(), digest_hex)


class CredentialsIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class PlayerOut(BaseModel):
    id: int
    username: str


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(payload: CredentialsIn, db: DbSession) -> PlayerOut:
    player = Player(username=payload.username, password_hash=hash_password(payload.password))
    db.add(player)
    try:
        await db.commit()
    except IntegrityError as exc:  # uq_player_username
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="用户名已被注册"
        ) from exc
    return PlayerOut(id=player.id, username=player.username)


@router.post("/login")
async def login(payload: CredentialsIn, db: DbSession, response: Response) -> PlayerOut:
    result = await db.execute(select(Player).where(Player.username == payload.username))
    player = result.scalar_one_or_none()
    # 用户不存在与密码错误统一 401，不泄露账号存在性。
    if player is None or not verify_password(payload.password, player.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    session_id = create_auth_session(player.id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return PlayerOut(id=player.id, username=player.username)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request, response: Response, db: DbSession, player_id: CurrentPlayerId
) -> None:
    # todo 15：显式登出 = 取消 tick task + 离图 + 心跳刷新 + 注销会话（宽限期不适用）
    character_id = (
        await db.execute(select(Character.id).where(Character.player_id == player_id))
    ).scalar_one_or_none()
    if character_id is not None:
        await shutdown_session(character_id)
    delete_auth_session(request.cookies.get(SESSION_COOKIE_NAME))
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
