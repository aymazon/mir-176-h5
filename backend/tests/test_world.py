"""World core tests: procedural maps, A*, player movement, portals, game loop."""

import asyncio
import math
from collections.abc import Sequence
from typing import Final

import pytest

from app.world import (
    Cell,
    GameLoop,
    GameWorld,
    JitterStats,
    Map,
    MapNotFoundError,
    Player,
    find_path,
    create_map,
)

_OPEN_10: Final[tuple[str, ...]] = (
    "##########",
    "#........#",
    "#........#",
    "#........#",
    "#........#",
    "#........#",
    "#........#",
    "#........#",
    "#........#",
    "##########",
)
_MAZE_5: Final[tuple[str, ...]] = (
    "#####",
    "#...#",
    "#.#.#",
    "#...#",
    "#####",
)
_SPLIT_5: Final[tuple[str, ...]] = (
    "#####",
    "#.#.#",
    "#.#.#",
    "#.#.#",
    "#####",
)
_NO_CUT_2: Final[tuple[str, ...]] = (
    ".#",
    "#.",
)
_CUT_CORRIDOR: Final[tuple[str, ...]] = (
    ".#.",
    "...",
    "...",
)


def _grid(rows: Sequence[str], code: str = "t") -> Map:
    return Map.from_strings(code, list(rows))


def _assert_walkable(grid: Map, path: tuple[Cell, ...], start: Cell, goal: Cell, blocked_ids: frozenset[int]) -> None:
    assert path, "expected a path, got None"
    assert path[0] == start
    assert path[-1] == goal
    for cell in path:
        assert grid.is_walkable(cell.x, cell.y), f"cell {cell} not walkable"
        assert cell.y * grid.width + cell.x not in blocked_ids
    for prev, nxt in zip(path, path[1:]):
        dx, dy = nxt.x - prev.x, nxt.y - prev.y
        assert max(abs(dx), abs(dy)) == 1, f"step {prev} -> {nxt} not adjacent"
        if dx != 0 and dy != 0:
            assert grid.is_walkable(prev.x + dx, prev.y), "corner cut through a wall (horizontal side)"
            assert grid.is_walkable(prev.x, prev.y + dy), "corner cut through a wall (vertical side)"


@pytest.fixture(scope="session")
def world() -> GameWorld:
    return GameWorld()


def test_astar_shortest_path_on_open_grid() -> None:
    grid = _grid(_OPEN_10)
    start, goal = Cell(1, 1), Cell(8, 8)
    path = find_path(grid, start, goal)
    assert path is not None
    assert len(path) - 1 == 7  # Chebyshev distance, all diagonal
    _assert_walkable(grid, path, start, goal, frozenset())


def test_astar_goes_around_blocked_cell() -> None:
    grid = _grid(_MAZE_5)
    start, goal = Cell(1, 1), Cell(3, 3)
    path = find_path(grid, start, goal)
    assert path is not None
    assert len(path) == 5  # wall at (2,2) forces a 4-step detour
    assert Cell(2, 2) not in path
    _assert_walkable(grid, path, start, goal, frozenset())


def test_astar_does_not_cut_corners() -> None:
    grid = _grid(_NO_CUT_2)
    assert find_path(grid, Cell(0, 0), Cell(1, 1)) is None

    corridor = _grid(_CUT_CORRIDOR)
    start, goal = Cell(0, 0), Cell(2, 2)
    path = find_path(corridor, start, goal)
    assert path is not None
    assert len(path) == 4  # direct diagonal (0,0)->(1,1) is a forbidden corner cut
    _assert_walkable(corridor, path, start, goal, frozenset())


def test_astar_unreachable_target_returns_none() -> None:
    grid = _grid(_SPLIT_5)
    assert find_path(grid, Cell(1, 1), Cell(3, 1)) is None


def test_astar_honors_dynamic_blocked_cells() -> None:
    grid = _grid(_OPEN_10)
    start, goal = Cell(1, 1), Cell(4, 4)
    unblocked = find_path(grid, start, goal)
    assert unblocked is not None and len(unblocked) == 4
    blocked = frozenset({Cell(2, 2)})
    detoured = find_path(grid, start, goal, blocked)
    assert detoured is not None
    _assert_walkable(grid, detoured, start, goal, frozenset({2 * 10 + 2}))


def test_astar_start_equal_goal() -> None:
    grid = _grid(_MAZE_5)
    assert find_path(grid, Cell(1, 1), Cell(1, 1)) == (Cell(1, 1),)


def test_map_generation_is_deterministic_per_code() -> None:
    first, second = create_map("m1"), create_map("m1")
    assert (first.width, first.height) == (60, 60)
    assert first.cells == second.cells
    assert create_map("m2").cells != first.cells


def test_map_sizes_and_wall_boundary() -> None:
    for code, size in (("m1", 60), ("m2", 60), ("m3", 300)):
        grid = create_map(code)
        assert (grid.width, grid.height) == (size, size)
        for axis in range(size):
            assert not grid.is_walkable(axis, 0)
            assert not grid.is_walkable(axis, size - 1)
            assert not grid.is_walkable(0, axis)
            assert not grid.is_walkable(size - 1, axis)


def test_portal_cells_and_neighbors_walkable() -> None:
    for code in ("m1", "m2", "m3"):
        grid = create_map(code)
        for portal in grid.portals:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    assert grid.is_walkable(portal.x + dx, portal.y + dy), (
                        f"neighbor ({portal.x + dx},{portal.y + dy}) of {code} portal not walkable"
                    )


def test_portal_pairs_are_astar_reachable() -> None:
    world_ = GameWorld()
    m1, m2, m3 = world_.map("m1"), world_.map("m2"), world_.map("m3")
    assert m1.spawn == (30, 30) and m2.spawn == (30, 30) and m3.spawn == (150, 150)
    assert find_path(m1, Cell(30, 30), Cell(55, 30)) is not None
    assert find_path(m2, Cell(30, 30), Cell(55, 30)) is not None
    assert find_path(m3, Cell(12, 150), Cell(280, 120)) is not None
    assert find_path(m3, Cell(280, 120), Cell(12, 150)) is not None


def test_safe_zones_per_spec() -> None:
    m1, m2, m3 = create_map("m1"), create_map("m2"), create_map("m3")
    assert m1.is_safe(1, 1) and m1.is_safe(58, 58)
    assert m2.is_safe(1, 1) and m2.is_safe(58, 58)
    assert m3.is_safe(120, 120) and m3.is_safe(180, 180) and m3.is_safe(150, 150)
    assert not m3.is_safe(119, 150) and not m3.is_safe(181, 150)
    assert not m3.is_safe(150, 119) and not m3.is_safe(150, 181)


def test_create_unknown_map_raises() -> None:
    with pytest.raises(MapNotFoundError):
        _ = create_map("m99")


def test_move_rejected_for_invalid_or_unreachable_targets() -> None:
    grid = _grid(_SPLIT_5, "w1")
    world_ = GameWorld({"w1": grid})
    player = world_.add_player("p1", "w1", 1, 1)
    assert world_.move("p1", 3, 1) is False  # unreachable (wall column)
    assert world_.move("p1", 2, 1) is False  # not walkable
    assert world_.move("p1", 0, 0) is False  # wall
    assert world_.move("p1", 9, 1) is False  # out of bounds
    assert world_.move("p1", -1, 1) is False  # out of bounds
    assert world_.move("p1", 1, 3) is True  # reachable
    world_.tick(100_000)
    assert (player.x, player.y) == (1, 3)
    assert world_.move("ghost", 1, 2) is False


def test_move_reachable_advances_one_cell_per_300ms() -> None:
    grid = _grid(_MAZE_5, "w2")
    world_ = GameWorld({"w2": grid})
    player = world_.add_player("p1", "w2", 1, 1)
    assert player.move_ms == 300
    assert world_.move("p1", 3, 2) is True
    assert player.moving
    world_.tick(100)
    world_.tick(100)
    assert (player.x, player.y) == (1, 1)  # 200ms < 300ms per cell
    world_.tick(100)
    assert (player.x, player.y) != (1, 1)  # exactly 300ms -> first cell
    for _ in range(12):
        world_.tick(100)
    assert (player.x, player.y) == (3, 2)
    assert not player.moving


def test_add_player_rejects_invalid_spawn() -> None:
    grid = _grid(_MAZE_5, "w3")
    world_ = GameWorld({"w3": grid})
    with pytest.raises(ValueError):
        _ = world_.add_player("p1", "w3", 0, 1)  # wall
    with pytest.raises(ValueError):
        _ = world_.add_player("p1", "w3", 9, 9)  # out of bounds


def _walk_to(world_: GameWorld, player: Player, goal: Cell, max_ticks: int = 120) -> Player:
    assert world_.move(player.id, goal.x, goal.y) is True
    for _ in range(max_ticks):
        world_.tick(100)
        if not player.moving:
            return player
    pytest.fail("player did not stop within the expected number of ticks")


def test_portal_crossing_m1_to_m3_and_back(world: GameWorld) -> None:
    player = world.add_player("a", "m1", 54, 30)
    player = _walk_to(world, player, Cell(55, 30))
    assert (player.map_id, player.x, player.y) == ("m3", 12, 150)
    back = world.add_player("b", "m3", 13, 150)
    back = _walk_to(world, back, Cell(12, 150))
    assert (back.map_id, back.x, back.y) == ("m1", 55, 30)


def test_portal_crossing_m2_to_m3_and_back(world: GameWorld) -> None:
    player = world.add_player("c", "m2", 54, 30)
    player = _walk_to(world, player, Cell(55, 30))
    assert (player.map_id, player.x, player.y) == ("m3", 280, 120)
    back = world.add_player("d", "m3", 281, 120)
    back = _walk_to(world, back, Cell(280, 120))
    assert (back.map_id, back.x, back.y) == ("m2", 55, 30)


def test_portal_landing_does_not_retrigger(world: GameWorld) -> None:
    player = world.add_player("e", "m1", 54, 30)
    player = _walk_to(world, player, Cell(55, 30))
    assert (player.map_id, player.x, player.y) == ("m3", 12, 150)
    world.tick(100)
    world.tick(100)
    assert (player.map_id, player.x, player.y) == ("m3", 12, 150)  # no ping-pong


class _FakeClock:
    """Deterministic monotonic clock in seconds, advanced by the fake sleeper."""

    _now: float

    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _FakeSleeper:
    """Sleep that advances the fake clock by the delay plus a deterministic overshoot."""

    _clock: _FakeClock
    _overshoots_ms: list[float]
    _calls: int

    def __init__(self, clock: _FakeClock, overshoots_ms: Sequence[float]) -> None:
        self._clock = clock
        self._overshoots_ms = list(overshoots_ms)
        self._calls = 0
        self.delays_s: list[float] = []

    async def __call__(self, delay_s: float) -> None:
        self.delays_s.append(delay_s)
        overshoot_s = self._overshoots_ms[self._calls % len(self._overshoots_ms)] / 1000.0
        self._calls += 1
        self._clock.advance(delay_s + overshoot_s)
        await asyncio.sleep(0)


async def test_game_loop_jitter_and_drift_with_injective_clock() -> None:
    world_ = GameWorld({"t": _grid(_OPEN_10, "t")})
    clock = _FakeClock()
    loop = GameLoop(
        world_,
        tick_ms=100,
        clock=clock,
        sleeper=_FakeSleeper(clock, [0.0, 3.0, 6.0, 3.0]),
    )
    task = asyncio.create_task(loop.run())
    deadline = 100_000
    while loop.tick_count < 100:
        assert deadline > 0, "loop made no progress"
        deadline -= 1
        await asyncio.sleep(0)
    loop.stop()
    await task

    assert loop.tick_count >= 100
    times = loop.tick_times_ms[:100]
    stats: JitterStats = loop.jitter_stats()
    assert stats.count >= 99
    assert stats.mean_ms < 20.0
    assert stats.p95_ms < 20.0
    assert stats.max_ms < 20.0
    # Drift correction: each tick stays within one overshoot of the ideal grid,
    # so cumulative scheduling error cannot grow with the tick count.
    max_offset_ms = max(abs(t - n * 100.0) for n, t in enumerate(times))
    assert max_offset_ms <= 6.0 + 1e-6


def test_jitter_stats_p95_math() -> None:
    deltas = [100.0] * 90 + [115.0, 125.0, 135.0, 145.0, 155.0, 165.0, 175.0, 185.0, 195.0]
    stats = JitterStats.p95_of_deltas_ms(deltas, period_ms=100.0)
    assert stats.count == 99
    assert math.isclose(stats.p95_ms, 55.0)
    assert math.isclose(stats.max_ms, 95.0)
    assert math.isclose(stats.mean_ms, 5.0)
