"""Noise Hunt — find a beautiful trading strategy in data that has no signal.

The player searches a price series for a rule with a good Sharpe ratio. They
will find one. The series is a driftless random walk, so by construction there
is nothing to find: what they discovered is the best of N draws from a null
distribution, and the reveal shows exactly that.

This package is pure domain logic. It imports numpy, scipy and nothing else —
no Dash, no Flask, no I/O. The user interface lives in ``hsights.ui.noise_hunt``
and depends on this package, never the reverse.

Public interface
----------------
    generate_series(seed)           -> NoiseSeries
    MovingAverageCross(fast, slow)  -> Rule
    Breakout(lookback)              -> Rule
    MeanReversion(lookback, z)      -> Rule
    HuntSession(seed)               -> .try_rule(rule) / .best / .reveal(belief)
    puzzle(seed)                    -> HuntSession that may or may not hide an edge

Every rule is evaluated on the in-sample slice only. The holdout stays sealed
inside the session until ``reveal()`` is called, the same discipline the
portfolio game applies to future prices.
"""

from .series import NoiseSeries, generate_series
from .rules import Breakout, MeanReversion, MovingAverageCross, Rule, describe
from .backtest import Backtest, run_backtest
from .stats import (deflated_sharpe, expected_maximum_sharpe, max_drawdown,
                    sharpe_ratio)
from .session import BELIEFS, EDGE_NAMES, HuntSession, Reveal, Trial, puzzle

__all__ = [
    'NoiseSeries', 'generate_series',
    'Rule', 'MovingAverageCross', 'Breakout', 'MeanReversion', 'describe',
    'Backtest', 'run_backtest',
    'sharpe_ratio', 'max_drawdown', 'expected_maximum_sharpe', 'deflated_sharpe',
    'HuntSession', 'Trial', 'Reveal', 'puzzle', 'BELIEFS', 'EDGE_NAMES',
]
