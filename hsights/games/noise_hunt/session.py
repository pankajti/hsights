"""The game session: search in-sample, then face the holdout.

The holdout slice is held inside the session and never returned by any method
except ``reveal()``. Rules are evaluated against the in-sample slice only, so
a player cannot tune against the data that will judge them — the same seal the
portfolio game puts between today and tomorrow.

Typical use:

    session = HuntSession(seed=7)
    session.try_rule(MovingAverageCross(10, 50))
    session.try_rule(Breakout(20))
    ...
    verdict = session.reveal(belief=0.3)     # "30% sure there is a real edge"

``puzzle(seed)`` builds the game's rounds: a hidden coin decides whether the
series carries a real edge, so the player's job is to tell which, and the
belief they state before the reveal is scored against the truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from hsights.games.noise_hunt.backtest import Backtest, run_backtest
from hsights.games.noise_hunt.rules import describe
from hsights.games.noise_hunt.series import generate_series
from hsights.games.noise_hunt.stats import (deflated_sharpe, expected_maximum_sharpe,
                                            null_sharpe_std)

DEFAULT_HOLDOUT = 0.4
MAXIMUM_TRIALS = 2000

#: Share of puzzles that hide a real edge. Calibrated so a full sweep of the
#: built-in rules detects a planted edge roughly 60-80% of the time while pure
#: noise is flagged well under 5% of the time. See tests/test_noise_hunt.py.
EDGE_PROBABILITY = 0.4
PUZZLE_LENGTH = 900

#: Beliefs the player can state, as probabilities that an edge exists.
BELIEFS = (0.1, 0.3, 0.5, 0.7, 0.9)


@dataclass(frozen=True)
class Trial:
    """One rule the player tested, scored in-sample."""

    index: int
    rule: Any
    label: str
    parameters: dict
    sharpe: float
    total_return: float
    max_drawdown: float
    trades: int


@dataclass(frozen=True)
class Reveal:
    """What the search actually found, once the holdout is opened."""

    trials: int
    trial_sharpes: tuple
    best_label: str
    in_sample_sharpe: float
    holdout_sharpe: float
    sharpe_std_across_trials: float
    expected_best_under_null: float
    deflated_sharpe_probability: float
    series_had_signal: bool
    edge_kind: Any
    verdict: str
    headline: str
    belief: Any = None
    belief_points: Any = None
    called_it: Any = None
    in_sample_backtest: Backtest = field(repr=False, default=None)
    holdout_backtest: Backtest = field(repr=False, default=None)


class HuntSession:
    """A search for a strategy, with the holdout sealed until the end."""

    def __init__(self, seed=0, length=750, holdout_fraction=DEFAULT_HOLDOUT,
                 cost_bps=2.0, drift=0.0, autocorrelation=0.0, daily_vol=0.012,
                 regime_drift=0.0, regime_length=60):
        if not 0.1 <= holdout_fraction <= 0.7:
            raise ValueError('Holdout fraction must lie in [0.1, 0.7].')
        self.series = generate_series(seed=seed, length=length, daily_vol=daily_vol,
                                      drift=drift, autocorrelation=autocorrelation,
                                      regime_drift=regime_drift,
                                      regime_length=regime_length)
        self.cost_bps = float(cost_bps)
        self._split = int(round(len(self.series) * (1.0 - holdout_fraction)))
        self._in_sample = self.series.slice(0, self._split)
        self._holdout = self.series.slice(self._split, len(self.series))
        self._trials: list[Trial] = []
        self._backtests: dict[int, Backtest] = {}
        self._revealed = False

    # ---------------------------------------------------------------- exposure

    @property
    def visible_prices(self):
        """The only prices a player or agent may see before revealing."""
        return self._in_sample.prices.copy()

    @property
    def trials(self):
        return tuple(self._trials)

    @property
    def revealed(self):
        return self._revealed

    @property
    def best(self):
        return max(self._trials, key=lambda trial: trial.sharpe, default=None)

    def backtest_for(self, trial):
        """In-sample backtest behind a trial, for charting."""
        return self._backtests[trial.index]

    # ------------------------------------------------------------------ search

    def try_rule(self, rule):
        """Score one rule in-sample and record the attempt."""
        if self._revealed:
            raise ValueError('This hunt is over. Start a new session to search again.')
        if len(self._trials) >= MAXIMUM_TRIALS:
            raise ValueError(f'Trial limit of {MAXIMUM_TRIALS} reached.')
        result = run_backtest(rule, self._in_sample, self.cost_bps)
        trial = Trial(index=len(self._trials), rule=rule, label=rule.label,
                      parameters=describe(rule), sharpe=result.sharpe,
                      total_return=result.total_return,
                      max_drawdown=result.max_drawdown, trades=result.trades)
        self._trials.append(trial)
        self._backtests[trial.index] = result
        return trial

    def search(self, rules):
        """Convenience for scoring many rules at once."""
        return [self.try_rule(rule) for rule in rules]

    # ------------------------------------------------------------------ reveal

    def reveal(self, belief=None):
        """Open the holdout and score the search against the null.

        ``belief`` is the player's stated probability, before seeing anything
        sealed, that the series contains a real edge. It is scored with a
        Brier rule: 100 for a confident correct call, 75 for a shrug (50%),
        and as low as 19 for a confident wrong one, so honest uncertainty
        beats bluffing on average.
        """
        if not self._trials:
            raise ValueError('Test at least one rule before revealing.')
        if belief is not None:
            belief = float(belief)
            if not 0.0 <= belief <= 1.0:
                raise ValueError('Belief must be a probability between 0 and 1.')
        self._revealed = True
        best = self.best
        in_sample = self._backtests[best.index]
        holdout = run_backtest(best.rule, self._holdout, self.cost_bps)

        sharpes = np.array([trial.sharpe for trial in self._trials], dtype=float)
        spread = float(sharpes.std(ddof=1)) if len(sharpes) > 1 else 0.0
        # The spread of trial Sharpes stands in for their spread under the null.
        # When the series really has an edge, rules that ride it score high and
        # rules that fight it score very low, so the observed spread balloons
        # and raises the bar the edge has to clear - a real Sharpe of 2 out of
        # sample was being called noise. No null spread can exceed the sampling
        # error of a single Sharpe estimate, so cap it there.
        spread = min(spread, null_sharpe_std(len(in_sample.returns)))
        expected_best = expected_maximum_sharpe(len(self._trials), spread)
        probability = deflated_sharpe(best.sharpe, len(self._trials), spread,
                                      in_sample.returns)

        beat_the_null = best.sharpe > expected_best and probability >= 0.95
        survived = holdout.sharpe > 0

        if self.series.has_signal:
            verdict = 'signal_found' if beat_the_null and survived else 'signal_missed'
        else:
            verdict = 'fooled' if not beat_the_null else 'lucky'

        truth = 1.0 if self.series.has_signal else 0.0
        points = called = None
        if belief is not None:
            points = int(round(100 * (1 - (belief - truth) ** 2)))
            called = None if belief == 0.5 else (belief > 0.5) == bool(truth)

        return Reveal(
            trials=len(self._trials),
            trial_sharpes=tuple(float(value) for value in sharpes),
            best_label=best.label,
            in_sample_sharpe=best.sharpe,
            holdout_sharpe=holdout.sharpe,
            sharpe_std_across_trials=spread,
            expected_best_under_null=expected_best,
            deflated_sharpe_probability=probability,
            series_had_signal=self.series.has_signal,
            edge_kind=self.series.edge_kind,
            verdict=verdict,
            headline=_headline(verdict, best, expected_best, holdout, len(self._trials)),
            belief=belief,
            belief_points=points,
            called_it=called,
            in_sample_backtest=in_sample,
            holdout_backtest=holdout,
        )


EDGE_NAMES = {
    'trend': 'trends that flip every couple of months',
    'reversal': 'short-term reversal: up days tend to be followed by down days',
    'momentum': 'short-term momentum',
    'drift': 'a steady drift',
}


def _headline(verdict, best, expected_best, holdout, trials):
    if verdict == 'fooled':
        return (f'You tested {trials} rules on a coin flip. The best scored '
                f'{best.sharpe:.2f}, and pure chance predicts {expected_best:.2f} '
                f'from that many tries. Out of sample it made {holdout.sharpe:.2f}.')
    if verdict == 'lucky':
        return (f'Your best of {trials} scored {best.sharpe:.2f} against an expected '
                f'{expected_best:.2f}. There was still nothing to find — the series '
                f'had no signal — so this is the tail of the null, not an edge.')
    if verdict == 'signal_found':
        return (f'This series did contain an edge, and you found it: {best.sharpe:.2f} '
                f'in sample against an expected {expected_best:.2f} from {trials} tries, '
                f'and {holdout.sharpe:.2f} out of sample.')
    return (f'This series contained a real edge and {trials} attempts did not '
            f'separate it from noise. Out of sample: {holdout.sharpe:.2f}.')


def puzzle(seed, edge_probability=EDGE_PROBABILITY, length=PUZZLE_LENGTH,
           holdout_fraction=DEFAULT_HOLDOUT, cost_bps=2.0):
    """A game round: a hidden coin decides whether the series has a real edge.

    The coin, the kind of edge and its size all come from ``seed``, so the
    daily puzzle is identical for everyone and reproducible for tests. Nothing
    about the coin is exposed except through ``session.series`` on the server.

    Edge sizes are strong enough for the built-in rules to catch most of the
    time and no stronger: short-term reversal (daily autocorrelation between
    -0.28 and -0.35) or trends that flip about every 60 days (0.32-0.40% a day).
    """
    chooser = np.random.default_rng([int(seed), 7])
    series_seed = int(chooser.integers(0, 2 ** 31 - 1))
    options = dict(seed=series_seed, length=length, holdout_fraction=holdout_fraction,
                   cost_bps=cost_bps)
    if chooser.random() < edge_probability:
        if chooser.random() < 0.5:
            options['autocorrelation'] = -float(chooser.uniform(0.28, 0.35))
        else:
            options['regime_drift'] = float(chooser.uniform(0.0032, 0.0040))
            options['regime_length'] = 60
    return HuntSession(**options)
