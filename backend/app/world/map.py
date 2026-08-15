"""Deterministic procedural grid maps (MVP-DESIGN D9).

Each map is a fixed-size walkability grid with an impassable outer wall.
The layout is fully determined by the map code: the RNG is seeded with a
stable hash of the code string, so the same code always yields the same
map. Portal cells, their neighbors, the spawn cell, and the safe zone are
carved walkable, and every portal is guaranteed reachable from the spawn
via a carved corridor.
"""

from __future__ import annotations

import random
import zlib
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

WALKABLE: Final[int] = 1
WALL: Final[int] = 0

_DIR8: Final[tuple[tuple[int, int], ...]] = (
    (-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1),
)


class MapNotFoundError(KeyError):
    """Raised when a map code has no registered spec."""

    code: str

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Portal:
    """One directed walk-through: stepping on (map_id, x, y) lands on (dest_map, dest_x, dest_y)."""

    map_id: str
    x: int
    y: int
    dest_map: str
    dest_x: int
    dest_y: int


@dataclass(frozen=True, slots=True)
class MapSpec:
    """Static definition of one map (MVP-DESIGN 5.1/5.2, D9).

    ``safe_zone`` is an inclusive (x0, y0, x1, y1) rectangle, or None when
    the whole map is safe. ``wall_density`` is the per-cell probability of a
    random interior wall.
    """

    code: str
    width: int
    height: int
    spawn: tuple[int, int]
    safe_zone: tuple[int, int, int, int] | None
    wall_density: float


MAP_SPECS: Final[dict[str, MapSpec]] = {
    "m1": MapSpec("m1", 60, 60, (30, 30), None, 0.10),
    "m2": MapSpec("m2", 60, 60, (30, 30), None, 0.10),
    "m3": MapSpec("m3", 300, 300, (150, 150), (120, 120, 180, 180), 0.12),
}

# Walking exits/entries for the first three maps (MVP-DESIGN 5.2).
MAP_PORTALS: Final[tuple[Portal, ...]] = (
    Portal(map_id="m1", x=55, y=30, dest_map="m3", dest_x=12, dest_y=150),
    Portal(map_id="m3", x=12, y=150, dest_map="m1", dest_x=55, dest_y=30),
    Portal(map_id="m2", x=55, y=30, dest_map="m3", dest_x=280, dest_y=120),
    Portal(map_id="m3", x=280, y=120, dest_map="m2", dest_x=55, dest_y=30),
)


class Map:
    """Immutable walkability grid of one map instance."""

    code: str
    width: int
    height: int
    cells: bytes
    safe_zone: tuple[int, int, int, int] | None
    spawn: tuple[int, int]
    portals: tuple[Portal, ...]

    def __init__(
        self,
        code: str,
        width: int,
        height: int,
        cells: bytes,
        safe_zone: tuple[int, int, int, int] | None,
        spawn: tuple[int, int],
        portals: tuple[Portal, ...],
    ) -> None:
        self.code = code
        self.width = width
        self.height = height
        self.cells = cells
        self.safe_zone = safe_zone
        self.spawn = spawn
        self.portals = portals

    @classmethod
    def from_strings(cls, code: str, rows: Sequence[str]) -> Map:
        """Build a map from ASCII rows: '.' is walkable, anything else is a wall."""
        height = len(rows)
        if height == 0:
            raise ValueError("map needs at least one row")
        width = len(rows[0])
        cells = bytearray(width * height)
        for y, row in enumerate(rows):
            if len(row) != width:
                raise ValueError("map rows must all have the same width")
            for x, ch in enumerate(row):
                if ch == ".":
                    cells[y * width + x] = WALKABLE
        return cls(code, width, height, bytes(cells), None, (1, 1), ())

    def is_in_bounds(self, x: int, y: int) -> bool:
        """True when (x, y) lies inside the grid."""
        return 0 <= x < self.width and 0 <= y < self.height

    def is_walkable(self, x: int, y: int) -> bool:
        """True when (x, y) is inside the grid and not a wall."""
        return self.is_in_bounds(x, y) and self.cells[y * self.width + x] == WALKABLE

    def is_safe(self, x: int, y: int) -> bool:
        """True when (x, y) lies in the map's safe zone (no active monsters)."""
        zone = self.safe_zone
        if zone is None:
            return self.is_in_bounds(x, y)
        x0, y0, x1, y1 = zone
        return x0 <= x <= x1 and y0 <= y <= y1


def create_map(code: str) -> Map:
    """Generate the deterministic map for ``code``; raise MapNotFoundError if unknown."""
    spec = MAP_SPECS.get(code)
    if spec is None:
        raise MapNotFoundError(code)
    rng = random.Random(zlib.crc32(code.encode("utf-8")))
    width, height = spec.width, spec.height
    cells = bytearray(width * height)
    for y in range(1, height - 1):
        row = y * width
        for x in range(1, width - 1):
            cells[row + x] = WALL if rng.random() < spec.wall_density else WALKABLE
    if spec.safe_zone is not None:
        _carve_rect(cells, width, height, spec.safe_zone)
    portals = tuple(portal for portal in MAP_PORTALS if portal.map_id == code)
    for portal in portals:
        _carve_patch(cells, width, height, portal.x, portal.y)
    _carve_patch(cells, width, height, spec.spawn[0], spec.spawn[1])
    _connect_portals(cells, width, height, spec.spawn, portals)
    return Map(code, width, height, bytes(cells), spec.safe_zone, spec.spawn, portals)


def _carve_rect(cells: bytearray, width: int, height: int, rect: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = rect
    for y in range(max(1, y0), min(height - 1, y1 + 1)):
        row = y * width
        for x in range(max(1, x0), min(width - 1, x1 + 1)):
            cells[row + x] = WALKABLE


def _carve_patch(cells: bytearray, width: int, height: int, cx: int, cy: int) -> None:
    """Carve the 3x3 patch around (cx, cy), keeping the outer wall intact."""
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            x, y = cx + dx, cy + dy
            if 1 <= x < width - 1 and 1 <= y < height - 1:
                cells[y * width + x] = WALKABLE


def _connect_portals(
    cells: bytearray,
    width: int,
    height: int,
    anchor: tuple[int, int],
    portals: tuple[Portal, ...],
) -> None:
    reached = _flood_fill(cells, width, height, anchor[0], anchor[1])
    for portal in portals:
        if (portal.x, portal.y) not in reached:
            _carve_corridor(cells, width, portal.x, portal.y, anchor)


def _carve_corridor(cells: bytearray, width: int, x0: int, y0: int, anchor: tuple[int, int]) -> None:
    ax, ay = anchor
    for x in range(min(x0, ax), max(x0, ax) + 1):
        cells[y0 * width + x] = WALKABLE
    for y in range(min(y0, ay), max(y0, ay) + 1):
        cells[y * width + ax] = WALKABLE


def _flood_fill(cells: bytearray, width: int, height: int, x0: int, y0: int) -> frozenset[tuple[int, int]]:
    """All cells reachable from (x0, y0) under 8-directional, no-corner-cutting movement."""
    seen: set[tuple[int, int]] = {(x0, y0)}
    queue: deque[tuple[int, int]] = deque([(x0, y0)])
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
    return frozenset(seen)
