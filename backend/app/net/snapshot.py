"""Vision snapshot building and the D5 size cascade (MVP-DESIGN 3.3/3.4, D5)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from app.net.protocol import EntityState, SnapshotMsg, encode_message
from app.world.world import GameWorld, Player

VISION_RADIUS: Final[int] = 12  # sync radius in cells (design 3.3)
SNAPSHOT_MAX_BYTES: Final[int] = 4096  # per-snapshot wire budget (design 3.4)


@dataclass(frozen=True, slots=True)
class EntityIdentity:
    """Wire identity of a visible player: character id plus display name."""

    character_id: int
    name: str


def chebyshev_distance(ax: int, ay: int, bx: int, by: int) -> int:
    """8-directional grid distance (one move step covers all 8 neighbors)."""
    return max(abs(ax - bx), abs(ay - by))


def vision_entities(
    world: GameWorld,
    viewer: Player,
    identity_of: Callable[[str], EntityIdentity | None],
) -> list[EntityState]:
    """Other live players within VISION_RADIUS cells on the viewer's map."""
    entities: list[EntityState] = []
    for player in world.players.values():
        if player.id == viewer.id or player.map_id != viewer.map_id:
            continue
        if chebyshev_distance(viewer.x, viewer.y, player.x, player.y) > VISION_RADIUS:
            continue
        identity = identity_of(player.id)
        if identity is None:
            continue
        entities.append(
            EntityState(id=identity.character_id, name=identity.name, x=player.x, y=player.y)
        )
    return entities


def trim_snapshot(snapshot: SnapshotMsg, limit_bytes: int = SNAPSHOT_MAX_BYTES) -> SnapshotMsg:
    """Return a snapshot within the byte budget, applying the D5 cascade.

    Cascade order: effect anchors, then drops, then the farthest edge entities.
    W1 carries players only, so the effect/drop stages are no-ops; entities are
    dropped farthest-from-viewer first until the frame fits.
    """
    trimmed = snapshot
    while len(encode_message(trimmed).encode("utf-8")) > limit_bytes:
        entities = trimmed.entities
        if not entities:
            return trimmed
        farthest_index = max(
            range(len(entities)),
            key=lambda i: chebyshev_distance(
                trimmed.char.x, trimmed.char.y, entities[i].x, entities[i].y
            ),
        )
        kept = entities[:farthest_index] + entities[farthest_index + 1 :]
        trimmed = trimmed.model_copy(update={"entities": kept})
    return trimmed
