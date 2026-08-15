"""Fixed-timestep game loop (MVP-DESIGN 3.3: 100 ms tick).

The loop ticks the world on an anchored time grid: each next target is the
loop start plus tick_count x tick period, never "previous wake + period",
so sleep overshoot produces bounded jitter but can never accumulate into
drift. Clock and sleeper are injectable so the scheduling math can be
tested deterministically without real-time sleeps.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Final

from app.config import Settings
from app.world.world import GameWorld

_JITTER_WINDOW: Final[int] = 1024


@dataclass(frozen=True, slots=True)
class JitterStats:
    """Inter-tick jitter (deviation of each tick interval from the nominal period), in ms."""

    count: int
    mean_ms: float
    p95_ms: float
    max_ms: float

    @classmethod
    def p95_of_deltas_ms(cls, deltas_ms: Sequence[float], period_ms: float) -> JitterStats:
        """Aggregate raw inter-tick deltas into jitter stats relative to the nominal period."""
        jitter = sorted(abs(delta - period_ms) for delta in deltas_ms)
        if not jitter:
            return cls(count=0, mean_ms=0.0, p95_ms=0.0, max_ms=0.0)
        p95_index = max(0, math.ceil(0.95 * len(jitter)) - 1)
        return cls(
            count=len(jitter),
            mean_ms=sum(jitter) / len(jitter),
            p95_ms=jitter[p95_index],
            max_ms=jitter[-1],
        )


class GameLoop:
    """Asyncio task that advances the world every tick_ms with drift-corrected scheduling."""

    _world: GameWorld
    _tick_ms: int
    _clock: Callable[[], float]
    _sleeper: Callable[[float], Awaitable[None]]
    _running: bool
    _tick_count: int
    _tick_times_ms: deque[float]
    _deltas_ms: deque[float]

    def __init__(
        self,
        world: GameWorld,
        *,
        tick_ms: int | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._world = world
        self._tick_ms = tick_ms if tick_ms is not None else Settings().tick_rate_ms
        self._clock = clock if clock is not None else time.monotonic
        self._sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._running = False
        self._tick_count = 0
        self._tick_times_ms = deque(maxlen=_JITTER_WINDOW)
        self._deltas_ms = deque(maxlen=_JITTER_WINDOW)

    async def run(self) -> None:
        """Tick the world until stop() is called; the first tick fires immediately."""
        if self._running:
            raise RuntimeError("game loop is already running")
        self._running = True
        start_s = self._clock()
        target_s = start_s
        try:
            while self._running:
                delay_s = target_s - self._clock()
                if delay_s > 0:
                    await self._sleeper(delay_s)
                self._record_tick((self._clock() - start_s) * 1000.0)
                self._world.tick(self._tick_ms)
                self._tick_count += 1
                target_s += self._tick_ms / 1000.0
        finally:
            self._running = False

    def stop(self) -> None:
        """Ask the loop to exit after the in-flight tick completes."""
        self._running = False

    @property
    def tick_count(self) -> int:
        return self._tick_count

    @property
    def tick_times_ms(self) -> tuple[float, ...]:
        """Tick timestamps in ms relative to loop start (most recent last)."""
        return tuple(self._tick_times_ms)

    def jitter_stats(self) -> JitterStats:
        """Sampled inter-tick jitter over the retained window."""
        return JitterStats.p95_of_deltas_ms(tuple(self._deltas_ms), float(self._tick_ms))

    def _record_tick(self, elapsed_ms: float) -> None:
        self._tick_times_ms.append(elapsed_ms)
        if len(self._tick_times_ms) > 1:
            self._deltas_ms.append(elapsed_ms - self._tick_times_ms[-2])
