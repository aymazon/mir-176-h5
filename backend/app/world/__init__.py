"""World simulation core: procedural maps, A*, entities, game loop."""

from app.world.loop import GameLoop, JitterStats
from app.world.map import (
    MAP_PORTALS,
    MAP_SPECS,
    Map,
    MapNotFoundError,
    MapSpec,
    Portal,
    create_map,
)
from app.world.pathfinding import Cell, find_path
from app.world.world import (
    GameWorld,
    InvalidPositionError,
    Player,
    PlayerAlreadyExistsError,
)

__all__ = [
    "MAP_PORTALS",
    "MAP_SPECS",
    "Cell",
    "GameLoop",
    "GameWorld",
    "InvalidPositionError",
    "JitterStats",
    "Map",
    "MapNotFoundError",
    "MapSpec",
    "Player",
    "PlayerAlreadyExistsError",
    "Portal",
    "create_map",
    "find_path",
]
