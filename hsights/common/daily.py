"""Daily challenges: the same round for everyone, changing at 00:00 UTC.

The source is public, so an unsalted seed would let anyone compute tomorrow's
puzzle today. Set ``HSIGHTS_DAILY_SALT`` to a secret in production.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import os

SALT = os.environ.get('HSIGHTS_DAILY_SALT', 'hsights-dev')


def today():
    """The current puzzle date (UTC)."""
    return datetime.now(timezone.utc).date()


def daily_seed(game, day=None):
    """A stable 31-bit seed for ``game`` on ``day``."""
    day = day or today()
    if not isinstance(day, date):
        raise TypeError('day must be a date.')
    digest = hashlib.sha256(f'{SALT}|{game}|{day.isoformat()}'.encode()).digest()
    return int.from_bytes(digest[:4], 'big') & 0x7FFFFFFF
