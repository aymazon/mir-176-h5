"""Monster AI (MVP-DESIGN 3.3): native aggro, A* pursuit, disengage, and return-home.

Passive monsters (chicken/deer/scarecrow) never initiate. Aggressive monsters
pursue a player that enters the 6-cell aggro ring (Chebyshev distance),
disengage and walk home fully healed once the target is beyond 12 cells, and
hold ground in reach for the strike. BOSSes never disengage: they keep
pursuing while the target is on the map and hold ground (standby, no return,
no heal) once it is gone. Pursuit walks the A* path one cell per
``MonsterDef.move_ms``; other monsters block path cells (D4). All time flows
through explicit ms arguments so the logic is fake-clock testable.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from app.content.models import MonsterDef
from app.world.map import Map, MapNotFoundError
from app.world.pathfinding import Cell, find_path
from app.world.world import InvalidPositionError, Player

__all__ = [
    "AGGRO_RADIUS",
    "DISENGAGE_RADIUS",
    "REPATH_EVERY_MS",
    "Monster",
    "MonsterAI",
    "MonsterState",
    "chebyshev",
]

#: doc 3.3: aggressive monsters pursue a player within 6 cells.
AGGRO_RADIUS: Final[int] = 6
#: doc 3.3: disengage and return home healed beyond 12 cells; BOSSes ignore this.
DISENGAGE_RADIUS: Final[int] = 12
#: D4: pursuit paths recompute at most every 500 ms.
REPATH_EVERY_MS: Final[int] = 500


class MonsterState(StrEnum):
    """Native AI state of one monster (doc 3.3)."""

    IDLE = "idle"
    CHASE = "chase"
    RETURN = "return"


def chebyshev(ax: int, ay: int, bx: int, by: int) -> int:
    """Chebyshev cell distance (the same geometry the vision sync uses)."""
    return max(abs(ax - bx), abs(ay - by))


@dataclass(slots=True)
class Monster:
    """A monster entity; mutable because the game loop updates position and state per tick."""

    id: str
    defn: MonsterDef
    map_id: str
    x: int
    y: int
    spawn_x: int
    spawn_y: int
    hp: int
    state: MonsterState = MonsterState.IDLE
    target: str | None = None
    _path: deque[Cell] = field(default_factory=deque, repr=False, compare=False)
    _progress_ms: float = field(default=0.0, repr=False, compare=False)
    _last_repath_ms: int = field(default=0, repr=False, compare=False)

    @property
    def moving(self) -> bool:
        return bool(self._path)

    def cancel_move(self) -> None:
        self._path.clear()
        self._progress_ms = 0.0

    def advance(self, dt_ms: float) -> None:
        """Advance up to dt_ms, one cell per ``defn.move_ms`` (monsters walk only: no portals)."""
        if not self._path:
            return
        self._progress_ms += dt_ms
        while self._path and self._progress_ms >= self.defn.move_ms:
            self._progress_ms -= self.defn.move_ms
            cell = self._path.popleft()
            self.x, self.y = cell.x, cell.y


class MonsterAI:
    """Monster registry + per-tick native AI driver; task 20's loop calls :meth:`tick`."""

    def __init__(self, maps: Mapping[str, Map]) -> None:
        self._maps: dict[str, Map] = dict(maps)
        self._monsters: dict[str, Monster] = {}

    @property
    def monsters(self) -> Mapping[str, Monster]:
        return self._monsters

    def add(self, monster: Monster) -> Monster:
        """Register a monster; both its current cell and its spawn cell must be walkable."""
        grid = self._maps.get(monster.map_id)
        if grid is None:
            raise MapNotFoundError(monster.map_id)
        if not grid.is_walkable(monster.x, monster.y):
            raise InvalidPositionError(monster.map_id, monster.x, monster.y, "monster cell not walkable")
        if not grid.is_walkable(monster.spawn_x, monster.spawn_y):
            raise InvalidPositionError(monster.map_id, monster.spawn_x, monster.spawn_y, "spawn cell not walkable")
        if monster.id in self._monsters:
            raise ValueError(f"monster {monster.id} is already in the world")
        self._monsters[monster.id] = monster
        return monster

    def remove(self, monster_id: str) -> Monster | None:
        """Remove a dead/retired monster; return it or None if absent."""
        monster = self._monsters.pop(monster_id, None)
        if monster is not None:
            monster.cancel_move()
        return monster

    def tick(self, now_ms: int, dt_ms: float, players: Iterable[Player]) -> None:
        """One AI step: decide (aggro/chase/disengage/return) then advance movement by dt_ms."""
        by_id = {player.id: player for player in players}
        for monster in self._monsters.values():
            self._tick_one(monster, now_ms, dt_ms, by_id)

    def _tick_one(self, m: Monster, now_ms: int, dt_ms: float, players: Mapping[str, Player]) -> None:
        grid = self._maps[m.map_id]
        blocked = frozenset(
            Cell(o.x, o.y) for o in self._monsters.values() if o is not m and o.map_id == m.map_id
        )
        target = players.get(m.target) if m.target is not None else None
        if target is not None and target.map_id != m.map_id:
            target = None
        match m.state:
            case MonsterState.IDLE:
                self._try_aggro(m, now_ms, players, blocked)
            case MonsterState.CHASE:
                self._chase(m, grid, now_ms, target, blocked)
            case MonsterState.RETURN:
                pass  # the path was computed at the transition; just walk it out
        if m._path:
            m.advance(dt_ms)
        if m.state is MonsterState.RETURN and not m._path:
            self._arrive_home(m)

    def _try_aggro(self, m: Monster, now_ms: int, players: Mapping[str, Player], blocked: frozenset[Cell]) -> None:
        if not m.defn.aggressive:
            return
        nearest = self._nearest_player(m, players, AGGRO_RADIUS)
        if nearest is None:
            return
        m.target = nearest.id
        m.state = MonsterState.CHASE
        if chebyshev(m.x, m.y, nearest.x, nearest.y) > m.defn.attack_range:
            self._path_toward(m, self._maps[m.map_id], nearest.x, nearest.y, now_ms, blocked)

    def _chase(
        self,
        m: Monster,
        grid: Map,
        now_ms: int,
        target: Player | None,
        blocked: frozenset[Cell],
    ) -> None:
        if target is None:
            if m.defn.is_boss:
                # BOSS has no disengage: standby in place - no return home, no heal.
                m.target = None
                m.state = MonsterState.IDLE
                m.cancel_move()
            else:
                self._begin_return(m, grid, blocked)
            return
        dist = chebyshev(m.x, m.y, target.x, target.y)
        if not m.defn.is_boss and dist > DISENGAGE_RADIUS:
            self._begin_return(m, grid, blocked)
            return
        if dist <= m.defn.attack_range:
            m.cancel_move()  # in reach: hold ground for the strike (task 20 attacks)
            return
        if not m._path or now_ms - m._last_repath_ms >= REPATH_EVERY_MS:
            self._path_toward(m, grid, target.x, target.y, now_ms, blocked)

    def _begin_return(self, m: Monster, grid: Map, blocked: frozenset[Cell]) -> None:
        m.state = MonsterState.RETURN
        m.target = None
        path = find_path(grid, Cell(m.x, m.y), Cell(m.spawn_x, m.spawn_y), blocked)
        if path is None or len(path) < 2:
            # Home unreachable or already at home: heal in place instead of looping (stuck-proof).
            self._arrive_home(m)
            return
        m._path = deque(path[1:])
        m._progress_ms = 0.0

    def _path_toward(
        self,
        m: Monster,
        grid: Map,
        tx: int,
        ty: int,
        now_ms: int,
        blocked: frozenset[Cell],
    ) -> None:
        m._last_repath_ms = now_ms  # a failed search is paced too: retry after REPATH_EVERY_MS
        path = find_path(grid, Cell(m.x, m.y), Cell(tx, ty), blocked)
        if path is None:
            m.cancel_move()  # stand and wait for the next re-path
            return
        m._path = deque(path[1:])
        # No _progress_ms reset: a re-plan must not wipe a partial step already in flight.

    def _arrive_home(self, m: Monster) -> None:
        m.cancel_move()  # required: clear the stale chase/return path, else an IDLE monster keeps walking it
        m.hp = m.defn.hp
        m.state = MonsterState.IDLE
        m.target = None

    @staticmethod
    def _nearest_player(m: Monster, players: Mapping[str, Player], radius: int) -> Player | None:
        nearest: Player | None = None
        nearest_dist = radius + 1
        for player in players.values():
            if player.map_id != m.map_id:
                continue
            dist = chebyshev(m.x, m.y, player.x, player.y)
            if dist <= radius and dist < nearest_dist:
                nearest, nearest_dist = player, dist
        return nearest
