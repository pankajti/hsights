"""Trading rules the player can search over.

Every rule produces a *signal* from prices up to and including day t, and the
position for day t+1 is that signal. The one-day shift is applied centrally in
``positions()`` so an individual rule cannot forget it — the same reason the
portfolio game enters at the next bar's open rather than the signal bar's close.

The search space is deliberately small. The point of the game is that even a
handful of knobs is enough to manufacture an impressive backtest out of noise.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class Rule(Protocol):
    """A rule maps a price path to a desired exposure in {-1, 0, +1}."""

    def signal(self, prices: np.ndarray) -> np.ndarray:
        """Exposure implied by information available *up to and including* each day."""

    @property
    def label(self) -> str:
        """Short human-readable description."""


def _rolling_mean(values, window):
    if window < 1:
        raise ValueError('Window must be at least 1.')
    out = np.full(len(values), np.nan)
    if len(values) < window:
        return out
    cumulative = np.cumsum(np.insert(values, 0, 0.0))
    out[window - 1:] = (cumulative[window:] - cumulative[:-window]) / window
    return out


def _rolling_std(values, window):
    out = np.full(len(values), np.nan)
    if len(values) < window or window < 2:
        return out
    for index in range(window - 1, len(values)):
        out[index] = values[index - window + 1:index + 1].std(ddof=1)
    return out


def positions(rule, prices):
    """Tradeable positions: the signal, shifted forward one day.

    positions[t] is what you hold on day t, decided from data through day t-1.
    positions[0] is always flat because nothing is known before the first day.
    """
    raw = np.asarray(rule.signal(np.asarray(prices, dtype=float)), dtype=float)
    if raw.shape != (len(prices),):
        raise ValueError('A rule must return one signal per price.')
    raw = np.nan_to_num(raw, nan=0.0, posinf=0.0, neginf=0.0)
    held = np.zeros_like(raw)
    held[1:] = raw[:-1]
    return np.clip(held, -1.0, 1.0)


@dataclass(frozen=True)
class MovingAverageCross:
    """Long when the fast average is above the slow one, short when below."""

    fast: int = 10
    slow: int = 50
    allow_short: bool = True

    def __post_init__(self):
        if not 1 <= self.fast < self.slow:
            raise ValueError('Need 1 <= fast < slow.')

    @property
    def label(self):
        return f'MA cross {self.fast}/{self.slow}' + ('' if self.allow_short else ' long-only')

    def signal(self, prices):
        fast = _rolling_mean(prices, self.fast)
        slow = _rolling_mean(prices, self.slow)
        raw = np.sign(fast - slow)
        raw[np.isnan(fast) | np.isnan(slow)] = 0.0
        if not self.allow_short:
            raw = np.maximum(raw, 0.0)
        return raw


@dataclass(frozen=True)
class Breakout:
    """Long on a new N-day high, short on a new N-day low, hold until the other."""

    lookback: int = 20
    allow_short: bool = True

    def __post_init__(self):
        if self.lookback < 2:
            raise ValueError('Lookback must be at least 2.')

    @property
    def label(self):
        return f'Breakout {self.lookback}d' + ('' if self.allow_short else ' long-only')

    def signal(self, prices):
        raw = np.zeros(len(prices))
        state = 0.0
        for index in range(self.lookback, len(prices)):
            window = prices[index - self.lookback:index]
            if prices[index] >= window.max():
                state = 1.0
            elif prices[index] <= window.min():
                state = -1.0 if self.allow_short else 0.0
            raw[index] = state
        return raw


@dataclass(frozen=True)
class MeanReversion:
    """Fade moves beyond a z-score threshold from the rolling mean."""

    lookback: int = 20
    threshold: float = 1.0
    allow_short: bool = True

    def __post_init__(self):
        if self.lookback < 5:
            raise ValueError('Lookback must be at least 5.')
        if self.threshold <= 0:
            raise ValueError('Threshold must be positive.')

    @property
    def label(self):
        return (f'Mean reversion {self.lookback}d @ {self.threshold:g}z'
                + ('' if self.allow_short else ' long-only'))

    def signal(self, prices):
        mean = _rolling_mean(prices, self.lookback)
        deviation = _rolling_std(prices, self.lookback)
        with np.errstate(divide='ignore', invalid='ignore'):
            score = np.where(deviation > 0, (prices - mean) / deviation, 0.0)
        raw = np.zeros(len(prices))
        raw[score >= self.threshold] = -1.0 if self.allow_short else 0.0
        raw[score <= -self.threshold] = 1.0
        raw[np.isnan(mean) | np.isnan(deviation)] = 0.0
        return raw


def describe(rule):
    """Label plus the parameters, for logs and trial tables."""
    return {'label': rule.label, 'type': type(rule).__name__,
            **{key: value for key, value in vars(rule).items()}}
