"""共享 fixtures（todo 7）：prod 环境切换 + 可控心跳年龄的角色工厂。

测试直连真实 PG（mir_dev），与 test_auth/test_sse 同一约定：
随机命名避撞唯一约束，收尾清理并 dispose engine 防跨 event loop 复用。
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal, engine


@pytest.fixture
def prod_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """临时把 settings.ENV 切成 prod（自动还原），用于 seed 的 prod 拒绝门禁。"""
    monkeypatch.setattr(settings, "ENV", "prod")


@pytest.fixture
async def make_stale_character() -> AsyncIterator[Callable[..., Awaitable[str]]]:
    """角色工厂：建 player+character 并把 last_active_at 摆到 now()-age_seconds（DB 时钟）。

    返回角色名；收尾按记录的 username 精确删除（FK CASCADE 连带删 character）。
    """
    created_usernames: list[str] = []

    async def _make(age_seconds: float = 120) -> str:
        username = f"seed_{uuid4().hex[:12]}"
        char_name = f"sc_{uuid4().hex[:10]}"
        async with SessionLocal() as db:
            await db.execute(
                text("INSERT INTO player (username, password_hash) VALUES (:u, :p)"),
                {"u": username, "p": "seed-not-a-login"},
            )
            await db.execute(
                text(
                    "INSERT INTO character "
                    "(player_id, name, class, hp, mp, map_id, bind_city, last_active_at) "
                    "SELECT p.id, :n, 'warrior', 0, 0, 'yin-xing-cun', 'yin-xing-cun', "
                    "now() - make_interval(secs => :age) "
                    "FROM player p WHERE p.username = :u"
                ),
                {"n": char_name, "age": age_seconds, "u": username},
            )
            await db.commit()
        created_usernames.append(username)
        return char_name

    yield _make

    for username in created_usernames:
        async with SessionLocal() as db:
            await db.execute(text("DELETE FROM player WHERE username = :u"), {"u": username})
            await db.commit()
    await engine.dispose()
