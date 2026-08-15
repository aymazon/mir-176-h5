"""In-memory game world: maps, players, movement intents, portal switching.

Pure simulation core (no FastAPI, no DB). A later milestone wires this into
the server: WS intents call ``GameWorld.move``, the game loop calls
``GameWorld.tick``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from app.world.map import MAP_SPECS, Map, MapNotFoundError, Portal, create_map
from app.world.pathfinding import Cell, find_path


class WorldError(ValueError):
    """Base class for world validation errors."""


class InvalidPositionError(WorldError):
    """Raised when an entity is placed at an out-of-bounds or non-walkable cell."""

    map_id: str
    x: int
    y: int
    reason: str

    def __init__(self, map_id: str, x: int, y: int, reason: str) -> None:
        super().__init__(f"{map_id}({x},{y}): {reason}")
        self.map_id = map_id
        self.x = x
        self.y = y
        self.reason = reason


class PlayerAlreadyExistsError(WorldError):
    """Raised when adding a player whose id is already in the world."""

    player_id: str

    def __init__(self, player_id: str) -> None:
        super().__init__(f"player {player_id} is already in the world")
        self.player_id = player_id


@dataclass(slots=True)
class Player:
    """A player entity; mutable because the game loop updates position and movement state per tick."""

    id: str
    map_id: str
    x: int
    y: int
    move_ms: int = 300
    _path: deque[Cell] = field(default_factory=deque, repr=False, compare=False)
    _progress_ms: float = field(default=0.0, repr=False, compare=False)

    @property
    def moving(self) -> bool:
        return bool(self._path)

    def start_move(self, path: Sequence[Cell]) -> None:
        """Begin walking the given cells (first cell must be adjacent to the current one)."""
        self._path = deque(path)
        self._progress_ms = 0.0

    def cancel_move(self) -> None:
        self._path.clear()
        self._progress_ms = 0.0

    def advance(self, dt_ms: float, portal_at: Callable[[str, int, int], Portal | None]) -> None:
        """Advance up to the accumulated time, one cell per move_ms, firing portal crossings."""
        if not self._path:
            return
        self._progress_ms += dt_ms
        while self._path and self._progress_ms >= self.move_ms:
            self._progress_ms -= self.move_ms
            cell = self._path.popleft()
            self.x, self.y = cell.x, cell.y
            portal = portal_at(self.map_id, self.x, self.y)
            if portal is None:
                continue
            self._path.clear()
            self._progress_ms = 0.0
            self.map_id = portal.dest_map
            self.x, self.y = portal.dest_x, portal.dest_y
            break


class GameWorld:
    """All loaded maps and the entities in them; the unit of simulation."""

    def __init__(self, maps: Mapping[str, Map] | None = None) -> None:
        if maps is None:
            maps = {code: create_map(code) for code in MAP_SPECS}
        self._maps: dict[str, Map] = dict(maps)
        self._players: dict[str, Player] = {}
        self._portals: dict[tuple[str, int, int], Portal] = {
            (portal.map_id, portal.x, portal.y): portal
            for grid in self._maps.values()
            for portal in grid.portals
        }

    def map(self, code: str) -> Map:
        """Return the loaded map for ``code``; raise MapNotFoundError if absent."""
        try:
            return self._maps[code]
        except KeyError:
            raise MapNotFoundError(code) from None

    @property
    def players(self) -> Mapping[str, Player]:
        return self._players

    def add_player(
        self,
        player_id: str,
        map_id: str,
        x: int,
        y: int,
        move_ms: int = 300,
    ) -> Player:
        grid = self.map(map_id)
        if not grid.is_in_bounds(x, y):
            raise InvalidPositionError(map_id, x, y, "out of bounds")
        if not grid.is_walkable(x, y):
            raise InvalidPositionError(map_id, x, y, "not walkable")
        if player_id in self._players:
            raise PlayerAlreadyExistsError(player_id)
        player = Player(id=player_id, map_id=map_id, x=x, y=y, move_ms=move_ms)
        self._players[player_id] = player
        return player

    def get_player(self, player_id: str) -> Player | None:
        return self._players.get(player_id)

    def portal_at(self, map_id: str, x: int, y: int) -> Portal | None:
        """Return the portal on the given cell, if any."""
        return self._portals.get((map_id, x, y))

    def move(self, player_id: str, x: int, y: int) -> bool:
        """Click-to-move intent (D15/D21): validate the target, start walking; False = rejected, no state change."""
        player = self._players.get(player_id)
        if player is None:
            return False
        grid = self._maps[player.map_id]
        if not grid.is_walkable(x, y):
            return False
        path = find_path(grid, Cell(player.x, player.y), Cell(x, y))
        if path is None:
            return False
        player.start_move(path[1:])
        return True

    def tick(self, dt_ms: float) -> None:
        """Advance all moving players by dt_ms; detect portal crossings on cell arrival."""
        for player in self._players.values():
            if player.moving:
                player.advance(dt_ms, self.portal_at)
