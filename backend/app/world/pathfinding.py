"""A* pathfinding over grid maps (MVP-DESIGN D4).

8-directional movement on walkable cells, unit cell cost, Manhattan
heuristic, corner-cutting prevention (a diagonal step is only allowed when
both orthogonal cells beside it are walkable), and dynamic blocked cells
(monster/player positions, which block entry to the cell itself). The search
is bounded by both an expansion budget and a 500 ms wall-clock budget, per
the "recompute <= 500 ms" decision; when either budget is exhausted or the
goal is unreachable, the function returns None.
"""

from __future__ import annotations

import heapq
import itertools
import time
from typing import Final, NamedTuple

from app.world.map import Map, WALL

MAX_EXPANSIONS: Final[int] = 300_000
TIME_BUDGET_S: Final[float] = 0.5
_BUDGET_CHECK_EVERY: Final[int] = 4096

_DIRS: Final[tuple[tuple[int, int], ...]] = (
    (-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1),
)


class Cell(NamedTuple):
    """A single grid position (x, y)."""

    x: int
    y: int


def find_path(
    grid: Map,
    start: Cell,
    goal: Cell,
    blocked: frozenset[Cell] | None = None,
    *,
    max_expansions: int = MAX_EXPANSIONS,
    time_budget_s: float = TIME_BUDGET_S,
) -> tuple[Cell, ...] | None:
    """A* from start to goal over walkable cells; None when unreachable or over budget."""
    if blocked is None:
        blocked = frozenset()
    if not grid.is_walkable(start.x, start.y) or not grid.is_walkable(goal.x, goal.y):
        return None
    if start == goal:
        return (start,)
    width, height = grid.width, grid.height
    cells = grid.cells
    blocked_ids = {cell.y * width + cell.x for cell in blocked}
    goal_id = goal.y * width + goal.x
    gx, gy = goal.x, goal.y

    def h(x: int, y: int) -> int:
        return abs(x - gx) + abs(y - gy)

    start_id = start.y * width + start.x
    g_score: dict[int, int] = {start_id: 0}
    came_from: dict[int, int] = {}
    closed: set[int] = set()
    order = itertools.count()
    open_heap: list[tuple[int, int, int]] = [(h(start.x, start.y), next(order), start_id)]
    deadline = time.monotonic() + time_budget_s

    while open_heap:
        _, _, current = heapq.heappop(open_heap)
        if current in closed:
            continue
        closed.add(current)
        if current == goal_id:
            return _reconstruct(came_from, width, start, current)
        if len(closed) >= max_expansions:
            return None
        if len(closed) % _BUDGET_CHECK_EVERY == 0 and time.monotonic() > deadline:
            return None
        x = current % width
        y = current // width
        step_cost = g_score[current] + 1
        for dx, dy in _DIRS:
            nx, ny = x + dx, y + dy
            if nx < 0 or nx >= width or ny < 0 or ny >= height:
                continue
            if dx != 0 and dy != 0 and (
                cells[y * width + nx] == WALL or cells[ny * width + x] == WALL
            ):
                continue
            neighbor_id = ny * width + nx
            if cells[neighbor_id] == WALL or neighbor_id in blocked_ids or neighbor_id in closed:
                continue
            previous = g_score.get(neighbor_id)
            if previous is None or step_cost < previous:
                g_score[neighbor_id] = step_cost
                came_from[neighbor_id] = current
                heapq.heappush(open_heap, (step_cost + h(nx, ny), next(order), neighbor_id))
    return None


def _reconstruct(
    came_from: dict[int, int],
    width: int,
    start: Cell,
    goal_id: int,
) -> tuple[Cell, ...]:
    chain: list[int] = [goal_id]
    while chain[-1] != start.y * width + start.x:
        chain.append(came_from[chain[-1]])
    chain.reverse()
    return tuple(Cell(cid % width, cid // width) for cid in chain)
