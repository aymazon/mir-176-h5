"""W2T3 world tables + deterministic 31-instance generator (plan task 15).

Covers the four world content tables (maps / monsters / spawns /
map_connections) through ``app.world.mapgen``: generation determinism
(three runs), all 31 instances (multi-layer maps expand to
``{base}-{layer}`` codes), bidirectional A* reachability of every
connection endpoint with exact portal landing, safe-zone and cave-portal
5x5 exclusions, spawn placement on walkable cells, boss fixed points, and
the data invariants from doc 5.1/5.2/5.3(N20)/5.4.
"""

from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from app.content.loader import Content, load_all_content
from app.content.models import (
    MapConnectionsFile,
    MapsFile,
    MonstersFile,
    SpawnsFile,
)
from app.world.mapgen import WorldContentError, generate_world, place_spawns
from app.world.pathfinding import Cell, find_path
from app.world.world import GameWorld

DATA_DIR = Path(__file__).resolve().parents[1] / "game_data"

#: The 31 world instances: 7 single-layer maps use their bare code, the six
#: multi-layer maps expand to ``{base}-{layer}`` (doc 5.1 layer table).
EXPECTED_INSTANCES: tuple[str, ...] = (
    "m1", "m2", "m3",
    "m4-1", "m4-2",
    "m5-1", "m5-2",
    "m6", "m7", "m8",
    "m9-1", "m9-2", "m9-3",
    "m10",
    "m11-1", "m11-2", "m11-3",
    *(f"m12-{i}" for i in range(1, 8)),
    *(f"m13-{i}" for i in range(1, 8)),
)


def _reachable(grid: object, a: Cell, b: Cell) -> bool:
    """A* reachability with lifted budgets (the 500 ms gameplay budget must not flake tests)."""
    path = find_path(grid, a, b, frozenset(), max_expansions=20_000_000, time_budget_s=60.0)
    return path is not None


def _endpoints_by_instance(content: Content) -> dict[str, list[tuple[int, int]]]:
    by_instance: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for c in content.map_connections.connections:
        by_instance[c.from_map].append((c.from_x, c.from_y))
        by_instance[c.to_map].append((c.to_x, c.to_y))
    return dict(by_instance)


@pytest.fixture(scope="module")
def content() -> Content:
    return load_all_content(DATA_DIR)


@pytest.fixture(scope="module")
def world(content: Content) -> GameWorld:
    return generate_world(content.maps, content.monsters, content.spawns, content.map_connections)


def test_world_contains_exactly_31_instances(content: Content, world: GameWorld) -> None:
    codes = content.maps.instance_codes()
    assert codes == EXPECTED_INSTANCES
    assert len(codes) == 31
    for code in codes:
        grid = world.map(code)
        assert grid.code == code


def test_generation_is_deterministic_across_three_runs(content: Content) -> None:
    def build() -> tuple[GameWorld, object]:
        w = generate_world(content.maps, content.monsters, content.spawns, content.map_connections)
        return w, place_spawns(w, content.spawns)

    runs = [build() for _ in range(3)]
    for (world_a, placed_a), (world_b, placed_b) in itertools.pairwise(runs):
        for code in EXPECTED_INSTANCES:
            assert world_a.map(code).cells == world_b.map(code).cells
        assert [(s.instance_code, s.monster_code, s.x, s.y) for s in placed_a] == [
            (s.instance_code, s.monster_code, s.x, s.y) for s in placed_b
        ]


def test_every_connection_endpoint_is_walkable(content: Content, world: GameWorld) -> None:
    for c in content.map_connections.connections:
        assert world.map(c.from_map).is_walkable(c.from_x, c.from_y)
        assert world.map(c.to_map).is_walkable(c.to_x, c.to_y)


def test_connection_endpoints_bidirectionally_reachable(content: Content, world: GameWorld) -> None:
    for code, cells in _endpoints_by_instance(content).items():
        grid = world.map(code)
        hub = Cell(*cells[0])
        for x, y in cells[1:]:
            cell = Cell(x, y)
            assert _reachable(grid, hub, cell), f"{code}: hub {hub} cannot reach {cell}"
            assert _reachable(grid, cell, hub), f"{code}: {cell} cannot reach hub {hub}"


def test_crossing_every_portal_lands_on_exact_destination(content: Content) -> None:
    world = generate_world(content.maps, content.monsters, content.spawns, content.map_connections)
    for i, conn in enumerate(content.map_connections.connections):
        portal = world.portal_at(conn.from_map, conn.from_x, conn.from_y)
        assert portal is not None
        assert (portal.dest_map, portal.dest_x, portal.dest_y) == (
            conn.to_map, conn.to_x, conn.to_y,
        )
        grid = world.map(conn.from_map)
        neighbor = next(
            (conn.from_x + dx, conn.from_y + dy)
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            if grid.is_walkable(conn.from_x + dx, conn.from_y + dy)
        )
        player = world.add_player(f"walker-{i}", conn.from_map, *neighbor)
        player.start_move([Cell(conn.from_x, conn.from_y)])
        player.advance(1000, world.portal_at)
        assert player.map_id == conn.to_map
        assert (player.x, player.y) == (conn.to_x, conn.to_y)


def test_spawn_placement_walkable_and_outside_excluded_cells(content: Content, world: GameWorld) -> None:
    placed = place_spawns(world, content.spawns)
    assert len(placed) == len(content.spawns.spawns) == 101
    portal_cells = _endpoints_by_instance(content)
    for sp in placed:
        grid = world.map(sp.instance_code)
        assert grid.is_walkable(sp.x, sp.y)
        zone = grid.safe_zone
        if zone is not None:
            x0, y0, x1, y1 = zone
            assert not (x0 <= sp.x <= x1 and y0 <= sp.y <= y1), f"spawn in safe zone of {sp.instance_code}"
        for px, py in portal_cells.get(sp.instance_code, ()):
            assert abs(sp.x - px) > 2 or abs(sp.y - py) > 2, f"spawn inside portal 5x5 of {sp.instance_code}"


def test_spawn_placement_preserves_caps_per_instance(content: Content, world: GameWorld) -> None:
    placed = place_spawns(world, content.spawns)
    data = Counter((s.map_code, s.monster_code) for s in content.spawns.spawns)
    placed_pairs = Counter((sp.instance_code, sp.monster_code) for sp in placed)
    assert placed_pairs == data
    data_caps: dict[str, int] = defaultdict(int)
    for s in content.spawns.spawns:
        data_caps[s.map_code] += s.max_alive
    placed_caps: dict[str, int] = defaultdict(int)
    for sp in placed:
        placed_caps[sp.instance_code] += sp.max_alive
    assert placed_caps == dict(data_caps)


def test_boss_counts_and_fixed_points(content: Content, world: GameWorld) -> None:
    bosses = [m for m in content.monsters.monsters if m.is_boss]
    assert len(bosses) == 7
    boss_codes = {m.code for m in bosses}
    boss_spawn_entries = {s.monster_code: s for s in content.spawns.spawns if s.monster_code in boss_codes}
    placed = place_spawns(world, content.spawns)
    for boss in bosses:
        assert boss.move_ms == 400
        entry = boss_spawn_entries[boss.code]
        assert entry.map_code == boss.map_codes[0]
        assert entry.max_alive == 1
        assert entry.respawn_s == 7200
        spot = next(sp for sp in placed if sp.monster_code == boss.code)
        assert world.map(spot.instance_code).is_walkable(spot.x, spot.y)


def test_monster_and_spawn_data_invariants(content: Content) -> None:
    maps: MapsFile = content.maps
    monsters: MonstersFile = content.monsters
    spawns: SpawnsFile = content.spawns
    connections: MapConnectionsFile = content.map_connections
    instances = set(maps.instance_codes())
    monster_codes = {m.code for m in monsters.monsters}
    bosses = {m.code for m in monsters.monsters if m.is_boss}
    for m in monsters.monsters:
        assert m.move_ms == (400 if m.is_boss else 450), f"{m.code} move_ms"
        assert 800 <= m.attack_ms <= 1200 and m.attack_ms == 1000, f"{m.code} attack_ms"
    for s in spawns.spawns:
        assert s.map_code in instances
        assert s.monster_code in monster_codes
        if s.monster_code in bosses:
            assert s.respawn_s == 7200
        else:
            assert 60 <= s.respawn_s <= 90
    assert len({(s.map_code, s.monster_code) for s in spawns.spawns}) == len(spawns.spawns)
    base_dims = {m.code: (m.width, m.height) for m in maps.maps}
    for ctn in connections.connections:
        for code, x, y in ((ctn.from_map, ctn.from_x, ctn.from_y), (ctn.to_map, ctn.to_x, ctn.to_y)):
            assert code in instances
            w, h = base_dims[code.split("-")[0]]
            assert 0 <= x < w and 0 <= y < h, f"endpoint {code}({x},{y}) out of bounds"


def test_multilayer_maps_have_up_return_points(content: Content) -> None:
    pairs = {(k.from_map, k.to_map) for k in content.map_connections.connections}
    for m in content.maps.maps:
        for layer in range(2, m.layers + 1):
            assert (f"{m.code}-{layer}", f"{m.code}-{layer - 1}") in pairs, (
                f"{m.code} layer {layer} lacks an up-return point to layer {layer - 1}"
            )


def test_safe_zones_walkable_and_consistent(content: Content, world: GameWorld) -> None:
    for code in ("m3", "m10"):
        grid = world.map(code)
        x0, y0, x1, y1 = grid.safe_zone
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                assert grid.is_walkable(x, y)
        assert grid.is_safe((x0 + x1) // 2, (y0 + y1) // 2)
        assert not grid.is_safe(10, 10)
    assert world.map("m1").is_safe(30, 30)  # safe_all: whole map safe
    assert world.map("m2").is_safe(1, 1)


def test_generate_world_rejects_invalid_content(content: Content) -> None:
    first = content.spawns.spawns[0]
    bad_spawn = content.spawns.model_copy(update={"spawns": [first.model_copy(update={"monster_code": "no_such_monster"})]})
    with pytest.raises(WorldContentError):
        generate_world(content.maps, content.monsters, bad_spawn, content.map_connections)
    bad_instance = content.spawns.model_copy(update={"spawns": [first.model_copy(update={"map_code": "m99"})]})
    with pytest.raises(WorldContentError):
        generate_world(content.maps, content.monsters, bad_instance, content.map_connections)
    first_conn = content.map_connections.connections[0]
    bad_conn = content.map_connections.model_copy(update={"connections": [first_conn.model_copy(update={"from_x": 99_999})]})
    with pytest.raises(WorldContentError):
        generate_world(content.maps, content.monsters, content.spawns, bad_conn)
