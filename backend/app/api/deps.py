"""Shared FastAPI dependencies: DB session + cookie-backed auth session.

The auth session table is intentionally in-memory (``dict`` keyed by uuid
session_id). Process restart invalidates sessions — acceptable for MVP.
todo 5 (SSE) reuses :func:`get_session` to resolve the connected player.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, TypedDict
from uuid import uuid4

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal

SESSION_COOKIE_NAME = "session_id"


class AuthSession(TypedDict):
    player_id: int
    created_at: datetime


# 纯内存 session 表：{session_id: {player_id, created_at}}（MVP，进程重启失效可接受）。
_auth_sessions: dict[str, AuthSession] = {}


def create_auth_session(player_id: int) -> str:
    """登录成功后登记服务端 session，返回要写进 HttpOnly cookie 的 session_id。"""
    session_id = uuid4().hex
    _auth_sessions[session_id] = {"player_id": player_id, "created_at": datetime.now(UTC)}
    return session_id


def delete_auth_session(session_id: str | None) -> None:
    if session_id is not None:
        _auth_sessions.pop(session_id, None)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_session(request: Request) -> int:
    """从 cookie 解析服务端 session，只传递其中已绑定的 player_id；无效一律 401。

    伪造 / 不存在的 session_id 不在内存表中 → 401。
    """
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    entry = _auth_sessions.get(session_id) if session_id is not None else None
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或会话已失效",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return entry["player_id"]


DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentPlayerId = Annotated[int, Depends(get_session)]
