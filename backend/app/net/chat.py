"""Chat rules (MVP-DESIGN D14): length cap, per-character rate limit,
sensitive-word replacement.

The rules run around the existing broadcast path in ``handlers``: disallowed
messages are dropped silently (no error frame, matching the move-reject
behavior) and the sanitized text is what gets broadcast.

The rate-limit map is keyed by ``character_id`` (not by connection), so a
reconnect by the same character cannot reset the limit.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Final

#: Maximum chat length in characters (MVP-DESIGN 3.6 chat row).
CH_MAX_LENGTH: Final[int] = 100

#: Minimum interval between two accepted chats of the same character.
CH_RATE_LIMIT_S: Final[float] = 1.0

#: Every sensitive-word occurrence is replaced by this marker.
CH_REPLACE: Final[str] = "**"


def load_sensitive_words(path: str | Path) -> frozenset[str]:
    """Read the sensitive-word list (one word per line, ``#`` comments allowed).

    A missing or unreadable file yields no words; the chat stays functional.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    words = {
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    return frozenset(words)


class ChatRules:
    """Server-side chat policy: length cap, per-character rate limit, sanitization."""

    def __init__(
        self,
        words: frozenset[str] = frozenset(),
        clock: Callable[[], float] | None = None,
    ) -> None:
        # Longest first so overlapping entries resolve to the longest match.
        self._words: Final[tuple[str, ...]] = tuple(sorted(words, key=len, reverse=True))
        self._clock: Final[Callable[[], float]] = clock if clock is not None else time.monotonic
        self._last_accepted: dict[int, float] = {}

    @property
    def words(self) -> tuple[str, ...]:
        return self._words

    def sanitize(self, text: str) -> str:
        """Replace every sensitive-word occurrence with ``**`` (plain substring match)."""
        for word in self._words:
            if word in text:
                text = text.replace(word, CH_REPLACE)
        return text

    def rate_limited(self, character_id: int) -> bool:
        """True when this character chatted less than CH_RATE_LIMIT_S ago."""
        last = self._last_accepted.get(character_id)
        return last is not None and self._clock() - last < CH_RATE_LIMIT_S

    def process(self, character_id: int, text: str) -> str | None:
        """Broadcast-ready text for an accepted chat, or None when it must be dropped."""
        if len(text) > CH_MAX_LENGTH:
            return None
        if self.rate_limited(character_id):
            return None
        self._last_accepted[character_id] = self._clock()
        return self.sanitize(text)
