"""Deterministic 31-instance world generator (W2T3, MVP-DESIGN 5.1/5.2/D9).

Builds every world instance declared in ``maps.json`` into its own
walkability grid:

- 12% random walls, seeded with ``crc32(instance code)`` so three builds of
  the same tables always yield byte-identical grids.
- The map safe zone (doc 5.1) and a 5x5 relative-safe patch around every
  portal cell (doc D9) are carved walkable.
- Every portal cell of an instance is guaranteed reachable from every other
  one: an 8-directional flood fill from the first endpoint is grown
  incrementally, and a forced L-corridor is carved wherever an endpoint
  sits in a disconnected pocket (verified again at the end).

``place_spawns`` then pins each (instance, monster) spawn entry of
``spawns.json`` to a deterministic walkable cell outside safe zones and
portal patches, seeded with ``crc32(f"{code}:spawns")``.
"""

from __future__ import annotations

import random
import zlib
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Final

from app.content.models import (
    MapConnectionsFile,
    MapDef,
    MapsFile,
    MonstersFile,
    SpawnsFile,
)
from app.world.map import WALKABLE, WALL, Map, Portal
from app.world.world import GameWorld, WorldError

__all__ = ["PORTAL_PATCH", "WALL_DENSITY", "WorldContentError", "WorldSpawn", "generate_world", "place_spawns"]

WALL_DENSITY: Final[float] = 0.12
PORTAL_PATCH: Final[int] = 2  # 5x5 relative-safe patch around each portal cell (D9)

_DIR8: Final[tuple[tuple[int, int], ...]] = (
    (-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1),
)


class WorldContentError(WorldError):
    """The content tables describe a world that cannot be generated (dangling reference, out-of-bounds endpoint, unreachable portal cell, exhausted spawn pool)."""


@dataclass(frozen=True, slots=True)
class WorldSpawn:
    """A deterministic spawn position for one (instance, monster) pair from spawns.json."""

    instance_code: str
    monster_code: str
    x: int
    y: int
    max_alive: int
    respawn_s: int


def generate_world(
    maps: MapsFile,
    monsters: MonstersFile,
    spawns: SpawnsFile,
    connections: MapConnectionsFile,
) -> GameWorld:
    """Build the full 31-instance world from validated content tables (deterministic)."""
    _validate(maps, monsters, spawns, connections)
    # Per-instance portal cells (both ends of every connection) for carving.
    endpoint_cells: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for conn in connections.connections:
        for code, x, y in ((conn.from_map, conn.from_x, conn.from_y), (conn.to_map, conn.to_x, conn.to_y)):
            cell = (x, y)
            if cell not in endpoint_cells[code]:
                endpoint_cells[code].append(cell)

    grids: dict[str, Map] = {}
    for m in maps.maps:
        codes = [m.code] if m.layers == 1 else [f"{m.code}-{i}" for i in range(1, m.layers + 1)]
        for code in codes:
            grids[code] = _build_instance(m, code, connections, endpoint_cells[code])
    return GameWorld(maps=grids)


def place_spawns(world: GameWorld, spawns: SpawnsFile) -> tuple[WorldSpawn, ...]:
    """Pin every spawn entry to a deterministic walkable cell outside safe zones and portal 5x5 patches."""
    # Portal cells of an instance: its own walk-throughs plus the landing cells
    # of walk-throughs from other maps (a landing cell is some portal's destination).
    endpoint_cells: dict[str, set[tuple[int, int]]] = {}
    for code in world.codes:
        grid = world.map(code)
        for portal in grid.portals:
            endpoint_cells.setdefault(code, set()).add((portal.x, portal.y))
            endpoint_cells.setdefault(portal.dest_map, set()).add((portal.dest_x, portal.dest_y))

    pools: dict[str, tuple[tuple[int, int], ...]] = {}
    rngs: dict[str, random.Random] = {}
    used: dict[str, set[tuple[int, int]]] = defaultdict(set)
    placed: list[WorldSpawn] = []
    for entry in spawns.spawns:
        code = entry.map_code
        pool = pools.get(code)
        if pool is None:
            pool = _spawn_pool(world, code, endpoint_cells.get(code, set()))
            pools[code] = pool
        rng = rngs.setdefault(code, random.Random(zlib.crc32(f"{code}:spawns".encode())))
        cell = _pick(rng, pool, used[code])
        placed.append(
            WorldSpawn(code, entry.monster_code, cell[0], cell[1], entry.max_alive, entry.respawn_s)
        )
    return tuple(placed)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _validate(maps: MapsFile, monsters: MonstersFile, spawns: SpawnsFile, connections: MapConnectionsFile) -> None:
    """Fail fast on dangling references before any grid work is done."""
    instances = set(maps.instance_codes())
    monster_codes = {m.code for m in monsters.monsters}
    for entry in spawns.spawns:
        if entry.map_code not in instances:
            raise WorldContentError(f"spawn references unknown instance {entry.map_code!r}")
        if entry.monster_code not in monster_codes:
            raise WorldContentError(f"spawn references unknown monster {entry.monster_code!r}")
    dims = {m.code: (m.width, m.height) for m in maps.maps}
    for conn in connections.connections:
        for code, x, y in ((conn.from_map, conn.from_x, conn.from_y), (conn.to_map, conn.to_x, conn.to_y)):
            if code not in instances:
                raise WorldContentError(f"connection references unknown instance {code!r}")
            width, height = dims[code.split("-")[0]]
            if not (0 <= x < width and 0 <= y < height):
                raise WorldContentError(f"connection endpoint ({x},{y}) on {code} is out of bounds")


def _build_instance(
    m: MapDef,
    code: str,
    connections: MapConnectionsFile,
    endpoints: list[tuple[int, int]],
) -> Map:
    width, height = m.width, m.height
    rng = random.Random(zlib.crc32(code.encode("utf-8")))
    cells = bytearray(width * height)  # outer ring stays WALL
    for y in range(1, height - 1):
        row = y * width
        for x in range(1, width - 1):
            cells[row + x] = WALL if rng.random() < WALL_DENSITY else WALKABLE
    safe_zone = m.safe_zone
    if safe_zone is not None:
        _carve_rect(cells, width, height, safe_zone)
    for px, py in endpoints:
        _carve_patch(cells, width, height, px, py)
    if endpoints:
        _ensure_connected(cells, width, height, endpoints)
    spawn = _instance_spawn(width, height, safe_zone, endpoints)
    portals = tuple(
        Portal(code, conn.from_x, conn.from_y, conn.to_map, conn.to_x, conn.to_y)
        for conn in connections.connections
        if conn.from_map == code
    )
    safe_all = m.safe_all
    return Map(code, width, height, bytes(cells), safe_zone, spawn, portals, safe_all=safe_all)


def _instance_spawn(
    width: int,
    height: int,
    safe_zone: tuple[int, int, int, int] | None,
    endpoints: list[tuple[int, int]],
) -> tuple[int, int]:
    if safe_zone is not None:
        x0, y0, x1, y1 = safe_zone
        return ((x0 + x1) // 2, (y0 + y1) // 2)
    if endpoints:
        return endpoints[0]
    return (width // 2, height // 2)


def _carve_rect(cells: bytearray, width: int, height: int, rect: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = rect
    for y in range(max(1, y0), min(height - 1, y1 + 1)):
        row = y * width
        for x in range(max(1, x0), min(width - 1, x1 + 1)):
            cells[row + x] = WALKABLE


def _carve_patch(cells: bytearray, width: int, height: int, cx: int, cy: int) -> None:
    """Carve the 5x5 patch around (cx, cy), keeping the outer wall intact."""
    for dy in range(-PORTAL_PATCH, PORTAL_PATCH + 1):
        for dx in range(-PORTAL_PATCH, PORTAL_PATCH + 1):
            x, y = cx + dx, cy + dy
            if 1 <= x < width - 1 and 1 <= y < height - 1:
                cells[y * width + x] = WALKABLE


def _ensure_connected(cells: bytearray, width: int, height: int, endpoints: list[tuple[int, int]]) -> None:
    """Grow one flood fill from the first endpoint; carve an L-corridor to the anchor for any endpoint stuck in a pocket, then re-verify."""
    anchor = endpoints[0]
    seen: set[tuple[int, int]] = set()
    queue: deque[tuple[int, int]] = deque()

    def grow(starts: list[tuple[int, int]]) -> None:
        for start in starts:
            if start not in seen:
                seen.add(start)
                queue.append(start)
        while queue:
            x, y = queue.popleft()
            for dx, dy in _DIR8:
                nx, ny = x + dx, y + dy
                if nx < 0 or nx >= width or ny < 0 or ny >= height:
                    continue
                if (nx, ny) in seen or cells[ny * width + nx] != WALKABLE:
                    continue
                if dx != 0 and dy != 0 and (
                    cells[y * width + nx] != WALKABLE or cells[ny * width + x] != WALKABLE
                ):
                    continue
                seen.add((nx, ny))
                queue.append((nx, ny))

    grow([anchor])
    for px, py in endpoints[1:]:
        if (px, py) in seen:
            continue
        corridor = _carve_corridor(cells, width, px, py, anchor)
        grow(corridor)
    missing = [cell for cell in endpoints if cell not in seen]
    if missing:
        raise WorldContentError(f"{width}x{height} grid: portal cells {missing} remain unreachable after corridor carving")


def _carve_corridor(
    cells: bytearray, width: int, x0: int, y0: int, anchor: tuple[int, int]
) -> list[tuple[int, int]]:
    """Carve an L-shaped corridor from (x0, y0) to the anchor; return the carved cells."""
    ax, ay = anchor
    corridor: list[tuple[int, int]] = []
    for x in range(min(x0, ax), max(x0, ax) + 1):
        cells[y0 * width + x] = WALKABLE
        corridor.append((x, y0))
    for y in range(min(y0, ay), max(y0, ay) + 1):
        cells[y * width + ax] = WALKABLE
        corridor.append((ax, y))
    return corridor

def _spawn_pool(
    world: GameWorld,
    code: str,
    endpoints: set[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    grid = world.map(code)
    excluded: set[tuple[int, int]] = set()
    zone = grid.safe_zone
    if zone is not None:
        x0, y0, x1, y1 = zone
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                excluded.add((x, y))
    for px, py in endpoints:
        for dy in range(-PORTAL_PATCH, PORTAL_PATCH + 1):
            for dx in range(-PORTAL_PATCH, PORTAL_PATCH + 1):
                excluded.add((px + dx, py + dy))
    pool = tuple(
        (x, y)
        for y in range(grid.height)
        for x in range(grid.width)
        if grid.is_walkable(x, y) and (x, y) not in excluded
    )
    if not pool:
        raise WorldContentError(f"{code}: no walkable cells left for spawns after exclusions")
    return pool


def _pick(
    rng: random.Random,
    pool: tuple[tuple[int, int], ...],
    used: set[tuple[int, int]],
) -> tuple[int, int]:
    limit = len(pool) * 4
    for _ in range(limit):
        cell = rng.choice(pool)
        if cell not in used:
            used.add(cell)
            return cell
    raise WorldContentError("spawn cell selection exhausted the retry budget")
