"""Apply a rule to a price path and measure what happened.

Costs are charged on every change in position, which matters more than it
looks: a rule that flips daily can show a fine gross Sharpe and a hopeless
net one, and that gap is half the lesson.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from hsights.games.noise_hunt.rules import positions
from hsights.games.noise_hunt.stats import TRADING_DAYS, max_drawdown, sharpe_ratio


@dataclass(frozen=True)
class Backtest:
    """Outcome of one rule on one series."""

    equity: np.ndarray
    returns: np.ndarray
    positions: np.ndarray
    sharpe: float
    total_return: float
    max_drawdown: float
    trades: int
    exposure: float
    cost_bps: float

    @property
    def summary(self):
        return {'sharpe': self.sharpe, 'total_return': self.total_return,
                'max_drawdown': self.max_drawdown, 'trades': self.trades,
                'exposure': self.exposure}


def run_backtest(rule, series, cost_bps=2.0, periods=TRADING_DAYS):
    """Run ``rule`` over ``series`` and return the resulting track record."""
    prices = np.asarray(series.prices, dtype=float)
    if len(prices) < 3:
        raise ValueError('Need at least three prices to backtest.')
    held = positions(rule, prices)
    asset_returns = prices[1:] / prices[:-1] - 1.0

    # Position for day t earns the return from t-1 to t; held[0] is flat.
    active = held[1:]
    gross = active * asset_returns

    turnover = np.abs(np.diff(np.concatenate(([0.0], active))))
    costs = turnover * (cost_bps / 10_000.0)
    net = gross - costs

    equity = np.concatenate(([1.0], np.cumprod(1.0 + net)))
    return Backtest(
        equity=equity,
        returns=net,
        positions=held,
        sharpe=sharpe_ratio(net, periods),
        total_return=float(equity[-1] - 1.0),
        max_drawdown=max_drawdown(equity),
        trades=int(np.count_nonzero(turnover > 1e-12)),
        exposure=float(np.mean(np.abs(active))),
        cost_bps=float(cost_bps),
    )
