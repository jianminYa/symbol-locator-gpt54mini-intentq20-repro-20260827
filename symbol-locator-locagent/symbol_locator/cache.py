"""Bounded cache of (query, context) → ranked+scored candidates + pending tail."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional

from .lsp import PlainSymbol


@dataclass
class ScoredSymbol:
    symbol: PlainSymbol
    score: int
    snippet: str


@dataclass
class CacheEntry:
    scored: list[ScoredSymbol]
    pending: list[PlainSymbol]  # not-yet-scored tail
    cursor: int = 0
    created_at: float = field(default_factory=time.time)


class CandidateCache:
    """Simple LRU-by-insertion-order, TTL-aware."""

    def __init__(self, max_entries: int = 512, ttl_s: float = 3600.0):
        self.max_entries = max_entries
        self.ttl_s = ttl_s
        self._store: dict[str, CacheEntry] = {}

    def key(self, workspace: str, name: str, context: Optional[str], session: Optional[str]) -> str:
        h = hashlib.sha1()
        h.update(workspace.encode())
        h.update(b"\0")
        h.update(name.encode())
        h.update(b"\0")
        h.update((context or "").encode())
        h.update(b"\0")
        h.update((session or "").encode())
        return h.hexdigest()

    def get(self, k: str) -> Optional[CacheEntry]:
        e = self._store.get(k)
        if e is None:
            return None
        if time.time() - e.created_at > self.ttl_s:
            self._store.pop(k, None)
            return None
        return e

    def set(self, k: str, scored: list[ScoredSymbol], pending: list[PlainSymbol]) -> None:
        self._store[k] = CacheEntry(scored=scored, pending=pending, cursor=0)
        # eviction
        while len(self._store) > self.max_entries:
            self._store.pop(next(iter(self._store)))

    def advance(self, k: str, n: int) -> list[ScoredSymbol]:
        e = self._store.get(k)
        if e is None:
            return []
        # sort remaining by score desc, keep stable via already-sorted list
        remaining = sorted(e.scored[e.cursor:], key=lambda s: -s.score)
        batch = remaining[:n]
        # write back sorted order + advance cursor
        e.scored = e.scored[:e.cursor] + remaining
        e.cursor += len(batch)
        return batch

    def append(self, k: str, extra: list[ScoredSymbol]) -> None:
        e = self._store.get(k)
        if e is None:
            return
        e.scored.extend(extra)

    def take_pending(self, k: str, n: int) -> list[PlainSymbol]:
        e = self._store.get(k)
        if e is None:
            return []
        head, e.pending = e.pending[:n], e.pending[n:]
        return head

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)


def demo() -> None:
    c = CandidateCache(max_entries=2)
    k1, k2 = c.key("/w", "foo", None, None), c.key("/w", "bar", None, None)
    assert k1 != k2
    from .lsp import PlainSymbol
    s = PlainSymbol("foo", 12, "function", "/a.py", 1, 1)
    c.set(k1, [ScoredSymbol(s, 80, "def foo")], [])
    assert len(c.advance(k1, 1)) == 1
    assert len(c.advance(k1, 1)) == 0  # cursor advanced
    # eviction
    c.set(k2, [], [])
    c.set(c.key("/w", "baz", None, None), [], [])
    assert c.size == 2
    print("cache demo OK")


if __name__ == "__main__":
    demo()
