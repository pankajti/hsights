"""Rounds, guesses and the honest score for Real or Random?"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import binom

ROUNDS = 10
WINDOWS = (60, 120, 250)          # trading days: about a quarter, half, a year
SIDES = ('left', 'right')


@dataclass(frozen=True)
class Pair:
    """One round. ``real_side`` must never be sent before the guess."""

    left: np.ndarray
    right: np.ndarray
    real_side: str
    symbol: str
    start: str
    end: str

    @property
    def days(self):
        return len(self.left) - 1


def coin_probability(correct, rounds):
    """Chance that pure guessing scores at least ``correct`` of ``rounds``."""
    if not 0 <= correct <= rounds:
        raise ValueError('Score out of range.')
    return float(binom.sf(correct - 1, rounds, 0.5))


def _synthetic_twin(real, rng):
    """A random walk with the real path's mean and volatility of log returns."""
    log_returns = np.diff(np.log(real))
    shocks = rng.normal(log_returns.mean(), log_returns.std(ddof=1), size=len(log_returns))
    return 100.0 * np.exp(np.concatenate(([0.0], np.cumsum(shocks))))


def build_pairs(panel, seed, rounds=ROUNDS):
    """Draw ``rounds`` pairs from a date x symbol panel of positive closes."""
    if panel is None or panel.empty:
        raise ValueError('No price history is available for Real or Random.')
    rng = np.random.default_rng([int(seed), 11])
    symbols = list(panel.columns)
    pairs = []
    attempts = 0
    while len(pairs) < rounds:
        attempts += 1
        if attempts > rounds * 50:
            raise ValueError('Could not draw clean real windows from this price history.')
        window = int(rng.choice(WINDOWS))
        if len(panel) <= window + 1:
            continue
        symbol = symbols[int(rng.integers(len(symbols)))]
        begin = int(rng.integers(0, len(panel) - window - 1))
        values = panel[symbol].to_numpy(dtype=float)[begin:begin + window + 1]
        if not np.isfinite(values).all() or (values <= 0).any():
            continue
        # Skip flat stretches (halts, stale quotes): a dead line is a giveaway.
        if np.count_nonzero(np.diff(values) == 0) > window * 0.05:
            continue
        real = 100.0 * values / values[0]
        fake = _synthetic_twin(real, rng)
        real_side = SIDES[int(rng.integers(2))]
        left, right = (real, fake) if real_side == 'left' else (fake, real)
        pairs.append(Pair(left=left, right=right, real_side=real_side, symbol=str(symbol),
                          start=str(panel.index[begin].date()),
                          end=str(panel.index[begin + window].date())))
    return pairs


class RealOrRandom:
    """A game of ``rounds`` guesses. The answer is revealed one round at a time."""

    def __init__(self, panel, seed=0, rounds=ROUNDS):
        self._pairs = build_pairs(panel, seed, rounds)
        self.rounds = rounds
        self.index = 0
        self.results = []           # one dict per answered round
        self.awaiting_next = False

    @property
    def finished(self):
        return len(self.results) >= self.rounds

    @property
    def correct(self):
        return sum(1 for result in self.results if result['correct'])

    def current(self):
        """What the browser may see for the current round: two unlabeled paths."""
        if self.finished:
            return None
        pair = self._pairs[self.index]
        return dict(round=self.index + 1, rounds=self.rounds, days=pair.days,
                    left=pair.left.copy(), right=pair.right.copy())

    def guess(self, side):
        """Pick which side is real. Returns the answer for this round."""
        if self.finished:
            raise ValueError('This game is over. Start a new one.')
        if self.awaiting_next:
            raise ValueError('Press NEXT for the following pair.')
        if side not in SIDES:
            raise ValueError('Pick left or right.')
        pair = self._pairs[self.index]
        result = dict(round=self.index + 1, picked=side, real_side=pair.real_side,
                      correct=side == pair.real_side, symbol=pair.symbol,
                      start=pair.start, end=pair.end, days=pair.days)
        self.results.append(result)
        self.awaiting_next = not self.finished
        return result

    def advance(self):
        if not self.awaiting_next:
            raise ValueError('Guess first.')
        self.awaiting_next = False
        self.index += 1

    def last_pair(self):
        """The pair just answered, for drawing the reveal. Only after a guess."""
        if not self.results:
            raise ValueError('Nothing has been answered yet.')
        return self._pairs[self.results[-1]['round'] - 1]

    def score(self):
        """Final tally and how often a coin would do at least as well."""
        answered = len(self.results)
        probability = coin_probability(self.correct, answered) if answered else 1.0
        if not self.finished:
            verdict = 'playing'
        elif probability < 0.05:
            verdict = 'skill'
        elif self.correct * 2 >= answered:
            verdict = 'coin'
        else:
            verdict = 'fooled'
        return dict(correct=self.correct, answered=answered, rounds=self.rounds,
                    coin_probability=probability, verdict=verdict)
