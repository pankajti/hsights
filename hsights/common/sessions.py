"""Server-owned game sessions with one lock per session.

Every game keeps something the browser must not see yet (tomorrow's prices, a
holdout, the managers' future returns), so state lives in this process. The
store used to be a plain dict behind one global lock, which serialised every
player behind whoever was rendering a chart. Now the global lock guards only
the dictionary itself, for microseconds, and each session carries its own lock
for the slow work.

Usage::

    slot = store.get(sid)
    if slot is None:
        slot = store.put(sid, dict(game=new_game()))
    with slot.lock:
        ...work on slot.value...
    slot.touch()

This is still in-process state: one worker, one instance. Moving to a shared
store is the next step, and every game already goes through this interface.
"""
from __future__ import annotations

from threading import Lock, RLock
from time import monotonic


class Slot:
    """One player's state plus the lock that guards it."""

    __slots__ = ('value', 'touched', 'lock')

    def __init__(self, value):
        self.value = value
        self.touched = monotonic()
        self.lock = RLock()

    def touch(self):
        self.touched = monotonic()


class SessionStore:
    """A bounded, expiring map of session id to :class:`Slot`."""

    def __init__(self, ttl_seconds, limit, keep_first=None):
        """``keep_first(value)`` returns True for sessions to evict last."""
        if ttl_seconds <= 0 or limit < 1:
            raise ValueError('TTL and limit must be positive.')
        self.ttl = ttl_seconds
        self.limit = limit
        self._keep_first = keep_first or (lambda value: False)
        self._slots: dict[str, Slot] = {}
        self._lock = Lock()

    def _purge(self, now):
        for key in [key for key, slot in self._slots.items()
                    if now - slot.touched > self.ttl]:
            del self._slots[key]

    def get(self, sid):
        """The live slot for ``sid``, or None. Never creates one."""
        with self._lock:
            self._purge(monotonic())
            return self._slots.get(sid)

    def put(self, sid, value):
        """Store ``value`` for ``sid``, replacing any previous state.

        When full, the least recently used session is dropped, preferring ones
        for which ``keep_first`` is False (finished games before live ones).
        """
        with self._lock:
            self._purge(monotonic())
            slot = self._slots.get(sid)
            if slot is not None:
                slot.value = value
                slot.touch()
                return slot
            while len(self._slots) >= self.limit:
                victim = min(self._slots, key=lambda key: (
                    bool(self._keep_first(self._slots[key].value)),
                    self._slots[key].touched))
                del self._slots[victim]
            slot = self._slots[sid] = Slot(value)
            return slot

    # Mapping-style reads, for tests and diagnostics.
    def __len__(self):
        with self._lock:
            return len(self._slots)

    def __contains__(self, sid):
        with self._lock:
            return sid in self._slots

    def __getitem__(self, sid):
        with self._lock:
            return self._slots[sid].value

    def values(self):
        with self._lock:
            return [slot.value for slot in self._slots.values()]
