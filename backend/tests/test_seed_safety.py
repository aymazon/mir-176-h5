"""seed_character 安全门禁三用例（todo 7 验收 6/7/8）：

1. ENV=prod → 拒绝、退出码非 0、输出 "refusing to run in prod"；
2. last_active_at 30s 前（在线/宽限期内）→ 拒绝、退出码非 0；
3. last_active_at 120s 前 → 放行，且 DB 里 level/gold 断言生效。

心跳年龄全部用 DB 时钟摆位（now() - make_interval），与生产判据同源。
"""

from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.character import Character
from scripts import seed_character


async def _run_seed(argv: list[str]) -> int:
    args = seed_character.build_parser().parse_args(argv)
    return await seed_character.seed(args)


@pytest.mark.usefixtures("prod_env")
async def test_prod_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    rc = await _run_seed(["--name", "whatever", "--level", "1"])
    assert rc == seed_character.EXIT_PROD
    assert rc != 0
    assert "refusing to run in prod" in capsys.readouterr().err


async def test_recent_heartbeat_refused(
    make_stale_character: Callable[..., Awaitable[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    name = await make_stale_character(age_seconds=30)
    rc = await _run_seed(["--name", name, "--level", "35"])
    assert rc == seed_character.EXIT_HEARTBEAT
    assert rc != 0
    assert "角色在线或刚过宽限期" in capsys.readouterr().err


async def test_old_heartbeat_allowed(
    make_stale_character: Callable[..., Awaitable[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    name = await make_stale_character(age_seconds=120)
    rc = await _run_seed(["--name", name, "--level", "35", "--gold", "100000"])
    assert rc == seed_character.EXIT_OK
    assert "门禁通过" in capsys.readouterr().out

    # 与验收 psql 等价：select level, gold from character where name = ...
    async with SessionLocal() as db:
        character = (
            await db.execute(select(Character).where(Character.name == name))
        ).scalar_one()
    assert character.level == 35
    assert character.gold == 100000
