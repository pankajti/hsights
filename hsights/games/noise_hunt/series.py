"""Price paths with a known, controllable amount of signal.

The default is a driftless geometric random walk: no trend, no autocorrelation,
no regime structure. Anything a rule appears to find in it is an artefact of
having looked many times.

``drift``, ``autocorrelation`` and ``regime_drift`` exist so a series can
genuinely contain an edge. That matters: a game that always says
"you found nothing" proves nothing. The search has to be capable of finding a
real signal when one is present, or the reveal is rigged.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

TRADING_DAYS = 252


@dataclass(frozen=True)
class NoiseSeries:
    """A price path plus the parameters that generated it."""

    prices: np.ndarray
    seed: int
    daily_vol: float
    drift: float
    autocorrelation: float
    regime_drift: float = 0.0
    regime_length: int = 0

    def __post_init__(self):
        if self.prices.ndim != 1 or len(self.prices) < 50:
            raise ValueError('Need a one-dimensional path of at least 50 points.')
        if not np.isfinite(self.prices).all() or (self.prices <= 0).any():
            raise ValueError('Prices must be finite and strictly positive.')

    def __len__(self):
        return len(self.prices)

    @property
    def has_signal(self):
        """True when the generator was given a real edge to find."""
        return (self.drift != 0.0 or self.autocorrelation != 0.0
                or self.regime_drift != 0.0)

    @property
    def edge_kind(self):
        """Plain-English name of the planted edge, or None for pure noise."""
        if self.regime_drift:
            return 'trend'
        if self.autocorrelation < 0:
            return 'reversal'
        if self.autocorrelation > 0:
            return 'momentum'
        if self.drift:
            return 'drift'
        return None

    def returns(self):
        """Simple returns, length len(self) - 1."""
        return self.prices[1:] / self.prices[:-1] - 1.0

    def slice(self, start, stop):
        return NoiseSeries(prices=self.prices[start:stop], seed=self.seed,
                           daily_vol=self.daily_vol, drift=self.drift,
                           autocorrelation=self.autocorrelation)


def generate_series(seed=0, length=750, daily_vol=0.012, start=100.0,
                    drift=0.0, autocorrelation=0.0, regime_drift=0.0, regime_length=60):
    """Generate a price path.

    With the defaults the log returns are i.i.d. mean-zero, so the expected
    Sharpe of every rule is zero and the only thing separating rules is luck.

    ``regime_drift`` adds a trend of that size per day whose sign flips at
    random, on average every ``regime_length`` days: the kind of structure a
    moving-average or breakout rule can genuinely exploit. The regimes use
    their own random stream, so the noise underneath is the same path the
    seed would produce without them.
    """
    if length < 50:
        raise ValueError('Length must be at least 50.')
    if daily_vol <= 0:
        raise ValueError('Daily volatility must be positive.')
    if not -0.95 <= autocorrelation <= 0.95:
        raise ValueError('Autocorrelation must lie in [-0.95, 0.95].')
    if regime_drift and regime_length < 2:
        raise ValueError('Regimes must last at least two days on average.')

    generator = np.random.default_rng(seed)
    shocks = generator.normal(0.0, daily_vol, size=length - 1)
    if autocorrelation:
        # AR(1) in log returns, rescaled so realised volatility is unchanged.
        correlated = np.empty_like(shocks)
        previous = 0.0
        for index, shock in enumerate(shocks):
            previous = autocorrelation * previous + shock
            correlated[index] = previous
        shocks = correlated * np.sqrt(1.0 - autocorrelation ** 2)
    log_returns = shocks + drift
    if regime_drift:
        regimes = np.random.default_rng([seed, 1])
        flips = regimes.random(length - 1) < 1.0 / regime_length
        sign = regimes.choice((-1.0, 1.0)) * np.where(np.cumsum(flips) % 2, -1.0, 1.0)
        log_returns = log_returns + regime_drift * sign
    prices = np.empty(length, dtype=float)
    prices[0] = start
    prices[1:] = start * np.exp(np.cumsum(log_returns))
    return NoiseSeries(prices=prices, seed=int(seed), daily_vol=float(daily_vol),
                       drift=float(drift), autocorrelation=float(autocorrelation),
                       regime_drift=float(regime_drift),
                       regime_length=int(regime_length) if regime_drift else 0)
