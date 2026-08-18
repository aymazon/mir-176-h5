"""W2T5 monster AI + spawn management tests (plan task 17, doc 3.3/5.3/5.4).

Fake clock: every time flows through explicit ``now_ms``/``dt_ms`` arguments;
the tests advance the clock by hand (no real time, no sleeping).

Acceptance coverage:
1. player entering the 6-cell aggro ring triggers pursuit;
2. leaving beyond 12 cells disengages, returns home, and fully heals;
3. BOSS never disengages (keeps pursuing or holds ground; no return, no heal);
4. respawn timing is exact to respawn_s and never overpopulates past max_alive;
5. a blocked/unreachable return path heals in place (stuck-proof, no loops).
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import pytest

from app.content.loader import Content, load_all_content
from app.content.models import MonsterDef
from app.world.map import Map, MapNotFoundError
from app.world.mapgen import WorldSpawn
from app.world.monster_ai import (
    AGGRO_RADIUS,
    DISENGAGE_RADIUS,
    Monster,
    MonsterAI,
    MonsterState,
    chebyshev,
)
from app.world.spawner import Spawner, SpawnerError
from app.world.world import InvalidPositionError, Player

DATA_DIR = Path(__file__).resolve().parents[1] / "game_data"

_IDS = itertools.count(1)

# 25x7 open field (interior x 1..23, y 1..5).
_FIELD: Final[tuple[str, ...]] = (
    "#########################",
    "#.......................#",
    "#.......................#",
    "#.......................#",
    "#.......................#",
    "#.......................#",
    "#########################",
)


def _grid(rows: Sequence[str], code: str = "t") -> Map:
    return Map.from_strings(code, list(rows))


def _defn(**overrides: object) -> MonsterDef:
    data: dict[str, object] = {
        "code": "t_skel",
        "name": "skel",
        "level": 1,
        "hp": 100,
        "attack_min": 5,
        "attack_max": 10,
        "defense": 0,
        "magic_def": 0,
        "exp": 10,
        "gold_min": 0,
        "gold_max": 0,
        "aggressive": True,
        "map_codes": ["m1"],
        "is_boss": False,
        "attack_ms": 1000,
        "move_ms": 450,
        "attack_range": 1,
        "undead": False,
    }
    data.update(overrides)
    return MonsterDef.model_validate(data)


def _monster(
    ai: MonsterAI,
    defn: MonsterDef,
    x: int,
    y: int,
    spawn: tuple[int, int] | None = None,
) -> Monster:
    sx, sy = spawn if spawn is not None else (x, y)
    monster = Monster(
        id=f"mon-{next(_IDS):04d}",
        defn=defn,
        map_id="t",
        x=x,
        y=y,
        spawn_x=sx,
        spawn_y=sy,
        hp=defn.hp,
    )
    ai.add(monster)
    return monster


def _player(x: int, y: int, pid: str = "p1") -> Player:
    return Player(id=pid, map_id="t", x=x, y=y)


def _chase_setup(monster_x: int = 3, target_x: int = 9) -> tuple[MonsterAI, Monster, Player]:
    """Aggressive monster at (monster_x, 3), player at (target_x, 3) on the open field."""
    ai = MonsterAI({"t": _grid(_FIELD)})
    m = _monster(ai, _defn(), monster_x, 3)
    return ai, m, _player(target_x, 3)


@pytest.fixture(scope="module")
def content() -> Content:
    return load_all_content(DATA_DIR)


# ---------------------------------------------------------------------------
# Criterion 1: aggro ring + pursuit
# ---------------------------------------------------------------------------


def test_passive_monster_never_initiates() -> None:
    ai = MonsterAI({"t": _grid(_FIELD)})
    m = _monster(ai, _defn(aggressive=False), 3, 3)
    p = _player(3, 3)  # standing on the passive monster
    ai.tick(0, 0, [p])
    ai.tick(5000, 450, [p])
    assert m.state is MonsterState.IDLE
    assert m.target is None
    assert not m.moving
    assert (m.x, m.y) == (3, 3)


def test_aggro_requires_the_six_cell_ring() -> None:
    ai, m, p = _chase_setup()
    p.x = 10  # Chebyshev 7: outside the ring
    ai.tick(0, 0, [p])
    assert m.state is MonsterState.IDLE
    p.x = 9  # Chebyshev 6: inside
    ai.tick(1000, 0, [p])
    assert m.state is MonsterState.CHASE
    assert m.target == "p1"
    assert AGGRO_RADIUS == 6, "doc 3.3 aggro ring is 6 cells"


def test_chase_walks_toward_player_then_holds_in_reach() -> None:
    ai, m, p = _chase_setup(3, 9)
    ai.tick(0, 450, [p])  # aggro + first step
    assert m.state is MonsterState.CHASE
    for i in range(1, 12):
        ai.tick(i * 1000, 450, [p])
    assert chebyshev(m.x, m.y, p.x, p.y) <= m.defn.attack_range
    assert not m.moving, "in reach: hold ground for the strike (task 20 attacks)"
    assert m.x > 3, "monster must walk toward the player"


def test_movement_steps_once_per_def_move_ms() -> None:
    def steps_after_450ms(move_ms: int) -> int:
        ai = MonsterAI({"t": _grid(_FIELD)})
        m = _monster(ai, _defn(move_ms=move_ms), 3, 3)
        p = _player(9, 3)
        ai.tick(0, 450, [p])
        return m.x - 3

    assert steps_after_450ms(450) == 1
    assert steps_after_450ms(200) == 2, "speed comes from MonsterDef.move_ms, not a constant"


def test_partial_progress_accumulates_across_ticks() -> None:
    ai, m, p = _chase_setup(3, 9)
    ai.tick(0, 0, [p])  # aggro, zero movement
    for i in range(1, 5):
        ai.tick(i * 100, 100, [p])
    assert (m.x, m.y) == (3, 3), "400 ms < 450 ms move_ms: no full cell yet"
    ai.tick(500, 100, [p])
    assert (m.x, m.y) == (4, 3)


def test_attack_range_from_def_stops_the_chase() -> None:
    ai = MonsterAI({"t": _grid(_FIELD)})
    m = _monster(ai, _defn(attack_range=3), 3, 3)
    p = _player(9, 3)
    ai.tick(0, 450, [p])
    for i in range(1, 8):
        ai.tick(i * 1000, 450, [p])
    assert chebyshev(m.x, m.y, p.x, p.y) == 3
    assert not m.moving


# ---------------------------------------------------------------------------
# Criterion 2: disengage beyond 12 -> return home -> full heal
# ---------------------------------------------------------------------------


def test_disengage_beyond_12_returns_home_full_healed() -> None:
    ai, m, p = _chase_setup(3, 9)
    ai.tick(0, 450, [p])  # (4,3)
    ai.tick(1000, 450, [p])  # (5,3)
    m.hp = 40
    p.x = 22  # Chebyshev 17 > 12
    ai.tick(2000, 0, [p])
    assert m.state is MonsterState.RETURN
    assert m.target is None
    ai.tick(3000, 450, [p])  # (4,3)
    ai.tick(4000, 450, [p])  # (3,3) home
    assert (m.x, m.y) == (3, 3)
    assert m.hp == 100, "back home: fully healed"
    assert m.state is MonsterState.IDLE
    assert not m.moving


def test_disengage_boundary_is_twelve_cells() -> None:
    ai, m, p = _chase_setup(3, 9)
    ai.tick(0, 450, [p])  # aggro + first step: (4,3)
    ai.tick(1000, 450, [p])  # second step: (5,3), away from home
    assert (m.x, m.y) == (5, 3)
    p.x = 17  # Chebyshev 12: exactly on the boundary - still pursuing
    ai.tick(2000, 0, [p])
    assert m.state is MonsterState.CHASE
    p.x = 18  # 13: disengage
    ai.tick(3000, 0, [p])
    assert m.state is MonsterState.RETURN
    assert DISENGAGE_RADIUS == 12, "doc 3.3 disengage radius is 12 cells"


def test_normal_monster_target_gone_returns_home() -> None:
    ai, m, p = _chase_setup(3, 9)
    ai.tick(0, 450, [p])  # (4,3)
    ai.tick(1000, 450, [p])  # (5,3)
    m.hp = 40
    ai.tick(2000, 0, [])  # the player left the map
    assert m.state is MonsterState.RETURN
    ai.tick(3000, 450, [])
    ai.tick(4000, 450, [])
    assert (m.x, m.y) == (3, 3)
    assert m.hp == 100
    assert m.state is MonsterState.IDLE


# ---------------------------------------------------------------------------
# Criterion 3: BOSS has no disengage
# ---------------------------------------------------------------------------


def test_boss_keeps_chasing_beyond_12_cells() -> None:
    ai = MonsterAI({"t": _grid(_FIELD)})
    m = _monster(ai, _defn(is_boss=True, move_ms=400), 3, 3)
    p = _player(9, 3)
    ai.tick(0, 400, [p])
    p.x = 22  # 18 cells away: a normal monster would disengage here
    for i in range(1, 10):
        ai.tick(i * 1000, 400, [p])
    assert m.state is MonsterState.CHASE
    assert m.target == "p1"
    assert m.x > 4, "BOSS keeps pursuing beyond the disengage radius"


def test_boss_target_gone_holds_ground_without_returning() -> None:
    ai = MonsterAI({"t": _grid(_FIELD)})
    m = _monster(ai, _defn(is_boss=True, move_ms=400), 3, 3)
    p = _player(9, 3)
    ai.tick(0, 400, [p])
    for i in range(1, 6):
        ai.tick(i * 1000, 400, [p])  # pursues to (8,3), then holds in reach
    m.hp = 30
    ai.tick(6000, 0, [])  # the player left the map
    assert m.state is MonsterState.IDLE, "standby, not disengaged home"
    assert (m.x, m.y) == (8, 3), "holds ground: no return to spawn"
    assert m.hp == 30, "no heal while the target is gone"
    for i in range(7, 12):
        ai.tick(i * 1000, 400, [])
    assert (m.x, m.y) == (8, 3)
    assert m.hp == 30
    assert m.state is MonsterState.IDLE


# ---------------------------------------------------------------------------
# Criterion 5: blocked/unreachable return -> heal in place, no loop
# ---------------------------------------------------------------------------


def test_return_blocked_by_other_monster_full_heals_in_place() -> None:
    ai = MonsterAI({"t": _grid(_FIELD)})
    blocker = _monster(ai, _defn(code="t_deer", aggressive=False), 3, 3)  # sits on A's spawn cell
    a = _monster(ai, _defn(), 3, 3)
    p = _player(9, 3)
    ai.tick(0, 450, [p])  # A: (4,3)
    ai.tick(1000, 450, [p])  # A: (5,3)
    a.hp = 40
    p.x = 22
    ai.tick(2000, 0, [p])
    assert a.state is MonsterState.IDLE, "unreachable home: heal in place instead of looping"
    assert (a.x, a.y) == (5, 3), "stays where it disengaged"
    assert a.hp == 100
    for i in range(3, 13):
        ai.tick(i * 1000, 450, [p])  # stays put, never re-loops
    assert (a.x, a.y) == (5, 3)
    assert a.hp == 100
    assert a.state is MonsterState.IDLE
    assert not a.moving
    assert (blocker.x, blocker.y) == (3, 3)


# ---------------------------------------------------------------------------
# Spawner: respawn timers, caps, in-place respawn
# ---------------------------------------------------------------------------


def _spawner(max_alive: int = 1, respawn_s: int = 60) -> Spawner:
    spawns = [WorldSpawn("m1", "t_skel", 5, 5, max_alive, respawn_s)]
    return Spawner(spawns, {"t_skel": _defn(hp=50)})


def test_initial_populate_fills_max_alive_in_place() -> None:
    spawner = _spawner(max_alive=3)
    point = spawner.points[0]
    created = spawner.initial_populate()
    assert len(created) == 3
    assert len({m.id for m in created}) == 3
    for monster in created:
        assert (monster.x, monster.y) == (5, 5)
        assert monster.map_id == "m1"
        assert monster.hp == 50
    assert point.alive == 3


def test_respawn_timing_is_exact_to_respawn_s() -> None:
    spawner = _spawner(max_alive=1, respawn_s=60)
    point = spawner.points[0]
    spawner.initial_populate()
    spawner.on_death("m1", "t_skel", 1000)
    assert spawner.tick(60_999) == (), "one ms before respawn_s: still dead"
    respawned = spawner.tick(61_000)
    assert len(respawned) == 1
    assert (respawned[0].x, respawned[0].y) == (5, 5), "respawns in place at the spawn point"
    assert respawned[0].hp == 50
    assert point.alive == 1
    assert spawner.tick(121_000) == (), "no duplicate respawn without a new death"


def test_max_alive_cap_blocks_overpopulation() -> None:
    spawner = _spawner(max_alive=2, respawn_s=60)
    point = spawner.points[0]
    spawner.initial_populate()
    spawner.on_death("m1", "t_skel", 0)
    assert spawner.tick(59_999) == ()
    assert len(spawner.tick(60_000)) == 1
    assert point.alive == 2
    spawner.on_death("m1", "t_skel", 61_000)
    assert spawner.tick(120_999) == ()
    assert len(spawner.tick(121_000)) == 1
    assert point.alive == 2


def test_staggered_deaths_respawn_on_their_own_timers() -> None:
    spawner = _spawner(max_alive=2, respawn_s=60)
    point = spawner.points[0]
    spawner.initial_populate()
    spawner.on_death("m1", "t_skel", 0)
    spawner.on_death("m1", "t_skel", 30_000)
    assert len(spawner.tick(60_000)) == 1
    assert spawner.tick(89_999) == ()
    assert len(spawner.tick(90_000)) == 1
    assert point.alive == 2
    assert spawner.tick(90_001) == ()


def test_cohort_death_spawns_all_when_due() -> None:
    spawner = _spawner(max_alive=2, respawn_s=60)
    point = spawner.points[0]
    spawner.initial_populate()
    spawner.on_death("m1", "t_skel", 0)
    spawner.on_death("m1", "t_skel", 0)
    assert spawner.tick(59_999) == ()
    assert len(spawner.tick(60_000)) == 2
    assert point.alive == 2


def test_spawner_rejects_unknown_monster_code() -> None:
    with pytest.raises(SpawnerError):
        Spawner([WorldSpawn("m1", "nope", 5, 5, 1, 60)], {"t_skel": _defn()})


def test_spawner_rejects_double_death() -> None:
    spawner = _spawner(max_alive=1)
    spawner.initial_populate()
    spawner.on_death("m1", "t_skel", 0)
    with pytest.raises(SpawnerError):
        spawner.on_death("m1", "t_skel", 1000)


def test_spawner_rejects_unknown_spawn_point() -> None:
    spawner = _spawner(max_alive=1)
    spawner.initial_populate()
    with pytest.raises(SpawnerError):
        spawner.on_death("m2", "t_skel", 0)


# ---------------------------------------------------------------------------
# Data pinning: respawn/respawn timing is data-driven from game_data
# ---------------------------------------------------------------------------


def test_respawn_and_move_ms_are_data_driven(content: Content) -> None:
    boss_codes = {m.code for m in content.monsters.monsters if m.is_boss}
    normal_codes = {m.code for m in content.monsters.monsters if not m.is_boss}
    boss_rows = [s for s in content.spawns.spawns if s.monster_code in boss_codes]
    normal_rows = [s for s in content.spawns.spawns if s.monster_code in normal_codes]
    assert len(boss_rows) == 7
    assert all(s.respawn_s == 7200 and s.max_alive == 1 for s in boss_rows), "BOSS: fixed 120 min, single slot"
    assert all(60 <= s.respawn_s <= 90 for s in normal_rows), "normal monsters: 60~90 s respawns"
    moves = {m.code: m.move_ms for m in content.monsters.monsters}
    assert {moves[c] for c in boss_codes} == {400}
    assert {moves[c] for c in normal_codes} == {450}


# ---------------------------------------------------------------------------
# Registry validation
# ---------------------------------------------------------------------------


def test_add_monster_rejects_invalid_cells() -> None:
    ai = MonsterAI({"t": _grid(_FIELD)})
    with pytest.raises(InvalidPositionError):
        ai.add(Monster(id="bad-cell", defn=_defn(), map_id="t", x=0, y=3, spawn_x=3, spawn_y=3, hp=100))
    with pytest.raises(MapNotFoundError):
        ai.add(Monster(id="bad-map", defn=_defn(), map_id="nope", x=3, y=3, spawn_x=3, spawn_y=3, hp=100))
