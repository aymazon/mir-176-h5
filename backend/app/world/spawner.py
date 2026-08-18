"""Monster spawn management (MVP-DESIGN 3.3 refresh rule, 5.4 spawn table).

Each spawn point (instance, monster) keeps up to ``max_alive`` monsters. On
a death a per-monster timer of ``respawn_s`` starts; when it expires the
monster respawns in place (the spawn point cell) as long as the cap allows.
Timers are kept per death, so staggered deaths each respawn on their own
clock. All time flows through explicit ms arguments (fake-clock testable).
"""

from __future__ import annotations

import heapq
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.content.models import MonsterDef
from app.world.mapgen import WorldSpawn
from app.world.monster_ai import Monster

__all__ = ["SpawnPoint", "Spawner", "SpawnerError"]

_MILLIS_PER_SECOND = 1000


class SpawnerError(ValueError):
    """Raised on a malformed spawn table or an invalid death report (unknown point, double death)."""


@dataclass(slots=True)
class SpawnPoint:
    """Runtime state of one (instance, monster) spawn point from spawns.json."""

    spawn: WorldSpawn
    alive: int = 0
    _due_ms: list[int] = field(default_factory=list, repr=False, compare=False)

    @property
    def pending(self) -> int:
        """Deaths waiting on their respawn timer."""
        return len(self._due_ms)


class Spawner:
    """Per-spawn-point respawn scheduler; the spawner of new :class:`Monster` entities."""

    _next_seq: int

    def __init__(self, spawns: Sequence[WorldSpawn], defs: Mapping[str, MonsterDef]) -> None:
        self._defs: dict[str, MonsterDef] = dict(defs)
        self._points: dict[tuple[str, str], SpawnPoint] = {}
        for spawn in spawns:
            if spawn.monster_code not in self._defs:
                raise SpawnerError(f"spawn {spawn.instance_code}/{spawn.monster_code}: unknown monster code")
            self._points[(spawn.instance_code, spawn.monster_code)] = SpawnPoint(spawn)
        self._next_seq = 1

    @property
    def points(self) -> tuple[SpawnPoint, ...]:
        return tuple(self._points.values())

    def initial_populate(self) -> tuple[Monster, ...]:
        """Spawn ``max_alive`` monsters at every spawn point (world start); callable exactly once."""
        if any(point.alive for point in self._points.values()):
            raise SpawnerError("initial_populate: spawn points already populated")
        created: list[Monster] = []
        for point in self._points.values():
            for _ in range(point.spawn.max_alive):
                created.append(self._spawn_one(point))
        return tuple(created)

    def on_death(self, instance_code: str, monster_code: str, now_ms: int) -> None:
        """Report one death: start its own respawn_s timer at the spawn point."""
        point = self._find(instance_code, monster_code)
        if point.alive <= 0:
            raise SpawnerError(f"{instance_code}/{monster_code}: no alive monster to kill")
        point.alive -= 1
        heapq.heappush(point._due_ms, now_ms + point.spawn.respawn_s * _MILLIS_PER_SECOND)

    def tick(self, now_ms: int) -> tuple[Monster, ...]:
        """Fire every due respawn timer; return the freshly spawned monsters (for the AI registry)."""
        created: list[Monster] = []
        for point in self._points.values():
            if not point._due_ms:
                continue
            due = 0
            while point._due_ms and point._due_ms[0] <= now_ms:
                heapq.heappop(point._due_ms)
                due += 1
            slots = point.spawn.max_alive - point.alive
            for _ in range(min(due, slots)):
                created.append(self._spawn_one(point))
        return tuple(created)

    def _spawn_one(self, point: SpawnPoint) -> Monster:
        spawn = point.spawn
        defn = self._defs[spawn.monster_code]
        point.alive += 1
        seq = self._next_seq
        self._next_seq += 1
        return Monster(
            id=f"mon-{seq:04d}",
            defn=defn,
            map_id=spawn.instance_code,
            x=spawn.x,
            y=spawn.y,
            spawn_x=spawn.x,
            spawn_y=spawn.y,
            hp=defn.hp,
        )

    def _find(self, instance_code: str, monster_code: str) -> SpawnPoint:
        point = self._points.get((instance_code, monster_code))
        if point is None:
            raise SpawnerError(f"unknown spawn point {instance_code}/{monster_code}")
        return point
