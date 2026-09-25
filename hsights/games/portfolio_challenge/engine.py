"""Deterministic accounting and rules. Never serializes future market observations."""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from scipy.optimize import minimize


@dataclass(frozen=True)
class Rules:
    lookback: int = 252
    horizon: int = 252
    target: float = .10
    floor: float = -.08
    risk_cap: float = .20
    capital: float = 100000
    cost_bps: float = 10

    def __post_init__(self):
        if not isinstance(self.lookback, int) or self.lookback < 2:
            raise ValueError('Lookback must be an integer of at least 2 days.')
        if not isinstance(self.horizon, int) or self.horizon < 1:
            raise ValueError('Horizon must be a positive integer.')
        if not all(np.isfinite(x) for x in (self.target, self.floor, self.risk_cap,
                                            self.capital, self.cost_bps)):
            raise ValueError('Rules must be finite.')
        if not (-1 < self.floor < 0 <= self.target and self.risk_cap > 0
                and self.capital > 0 and 0 <= self.cost_bps <= 100):
            raise ValueError('Invalid target, loss floor, risk ceiling, capital or fee.')


def volatility(weights, covariance):
    return float(np.sqrt(max(0, 252 * np.asarray(weights) @ covariance @ weights)))


def minimum_risk(covariance):
    result = minimize(lambda w: w @ covariance @ w * 252,
                      np.full(len(covariance), 1 / len(covariance)),
                      bounds=[(0, 1)] * len(covariance),
                      constraints=[{'type': 'eq', 'fun': lambda w: w.sum()-1}],
                      method='SLSQP', options={'ftol': 1e-12, 'maxiter': 500})
    if not result.success:
        raise ValueError('Could not determine a feasible starting allocation.')
    weights = np.maximum(result.x, 0)
    weights /= weights.sum()
    return weights, volatility(weights, covariance)


class Game:
    def __init__(self, prices, start, rules=Rules(), benchmark=None, benchmark_symbol=None):
        self.rules = rules
        self.prices = prices.copy().astype(float)
        if (len(prices.columns) != 3 or not prices.index.is_unique
                or not prices.index.is_monotonic_increasing
                or not np.isfinite(self.prices.to_numpy()).all()
                or (self.prices <= 0).any().any()):
            raise ValueError('Need three positive, aligned price series without missing dates/values.')
        self.returns = self.prices.pct_change(fill_method=None)
        self.start = int(self.prices.index.searchsorted(pd.Timestamp(start)))
        if self.start < rules.lookback or self.start + rules.horizon >= len(prices):
            raise ValueError('Insufficient lookback or future coverage for this challenge.')
        # Buy-and-hold reference, held in full server-side and revealed only one
        # day at a time through the recorded history, exactly like the prices.
        self.benchmark_symbol = benchmark_symbol
        self._benchmark = None
        if benchmark is not None and benchmark_symbol:
            series = np.asarray(benchmark, dtype=float)
            if (len(series) == len(self.prices) and np.isfinite(series).all()
                    and (series > 0).all()):
                self._benchmark = series
            else:
                # A cosmetic overlay must never stop a round from being playable.
                self.benchmark_symbol = None
        elif benchmark is None:
            self.benchmark_symbol = None
        self.assisted = False
        self._states = {}
        self._reset()
        self.suggested, risk = minimum_risk(self.covariance())
        if risk > rules.risk_cap + 1e-10:
            raise ValueError('No starting allocation meets the risk ceiling.')

    def _reset(self):
        self.position = self.start
        self.holdings = np.zeros(3)
        self.status = 'setup'
        self.reason = 'Choose an allocation and begin.'
        self.fees = 0.
        self.history = []
        self.trades = []
        self.daily_returns = []
        self._states = {}

    def covariance(self):
        return self.returns.iloc[self.position-self.rules.lookback+1:self.position+1].cov().to_numpy()

    @property
    def value(self):
        return float(self.holdings.sum()) if self.status != 'setup' else self.rules.capital

    @property
    def weights(self):
        return self.holdings / self.holdings.sum() if self.holdings.sum() else self.suggested.copy()

    def preview(self, weights):
        w = np.asarray(weights, dtype=float)
        if w.shape != (3,) or not np.isfinite(w).all() or (w < 0).any() or abs(w.sum()-1) > 1e-8:
            raise ValueError('Three nonnegative allocations must sum to 100%.')
        w = w / w.sum()
        risk = volatility(w, self.covariance())
        # Solve cost = fee_rate * total absolute dollar purchases and sales.
        # This includes both sides of a rotation and the initial purchase.
        old = self.holdings
        total = self.value
        rate = self.rules.cost_bps / 10000
        low, high = 0., total
        for _ in range(60):
            net = (low+high)/2
            cost = rate * np.abs(w*net-old).sum()
            if net+cost > total:
                high = net
            else:
                low = net
        net = (low+high)/2
        cost = float(rate * np.abs(w*net-old).sum())
        return w, risk, cost, net

    def allocate(self, weights):
        if self.status not in ('setup', 'paused'):
            raise ValueError('Pause before rebalancing. Ended games cannot be changed.')
        w, risk, cost, net = self.preview(weights)
        if risk > self.rules.risk_cap + 1e-10:
            raise ValueError('Proposed allocation exceeds the risk ceiling.')
        self.holdings = w * net
        self.fees += cost
        self.status = 'paused'
        self.trades.append(dict(day=self.position-self.start, date=str(self.prices.index[self.position].date()),
                                weights=w.tolist(), cost=cost))
        self.evaluate()
        self.record()

    def evaluate(self):
        reward = self.value/self.rules.capital-1
        risk = volatility(self.weights, self.covariance())
        if reward < self.rules.floor - 1e-10:
            self.status, self.reason = 'lost', 'Return fell below the loss floor.'
        elif risk > self.rules.risk_cap + 1e-10:
            self.status, self.reason = 'lost', 'Estimated annualized volatility exceeded the ceiling.'
        elif self.position-self.start >= self.rules.horizon:
            won = reward >= self.rules.target - 1e-10
            self.status = 'won' if won else 'lost'
            self.reason = 'Return target reached at the deadline.' if won else 'Deadline reached below the return target.'
        else:
            self.reason = 'Stay above the loss floor and below the risk ceiling.'

    @property
    def benchmark_return(self):
        """Buy-and-hold return of the reference from day 0 to today, gross of costs."""
        if self._benchmark is None:
            return None
        return float(self._benchmark[self.position]/self._benchmark[self.start]-1)

    def record(self):
        row = dict(day=self.position-self.start, date=str(self.prices.index[self.position].date()),
                   value=self.value, total_return=self.value/self.rules.capital-1,
                   risk=volatility(self.weights, self.covariance()), weights=self.weights.tolist(),
                   fees=self.fees, benchmark_return=self.benchmark_return)
        if self.history and self.history[-1]['day'] == row['day']:
            self.history[-1] = row
        else:
            self.history.append(row)
        self._checkpoint()

    def _checkpoint(self):
        """Store an O(1) restore point for the current day.

        Only lengths and the final history row are kept, so a 2,520-day round
        costs bounded memory rather than a copy of the whole history per day.
        """
        self._states[self.position] = dict(
            holdings=self.holdings.copy(), status=self.status, reason=self.reason,
            fees=self.fees, history_length=len(self.history),
            trades_length=len(self.trades),
            last_row=dict(self.history[-1]) if self.history else None)

    @property
    def can_rewind(self):
        return any(position < self.position for position in self._states)

    def rewind(self, days=5):
        """Step back to an earlier recorded day. Marks the run as assisted."""
        if not isinstance(days, int) or days < 1:
            raise ValueError('Rewind must be a positive whole number of days.')
        if self.status == 'setup' or not self._states:
            raise ValueError('Confirm an allocation before rewinding.')
        target = max(self.start, self.position - days)
        earlier = [position for position in self._states if position <= target]
        if not earlier or max(earlier) == self.position:
            raise ValueError('Already at the earliest recorded day of this round.')
        position = max(earlier)
        state = self._states[position]
        self.position = position
        self.holdings = state['holdings'].copy()
        self.fees = state['fees']
        del self.history[state['history_length']:]
        if state['last_row'] is not None and self.history:
            self.history[-1] = dict(state['last_row'])
        del self.trades[state['trades_length']:]
        for stale in [key for key in self._states if key > position]:
            del self._states[stale]
        # Always land paused: the player must re-commit before time moves again.
        self.status = 'paused'
        self.assisted = True
        self.reason = (f'Rewound to day {position-self.start}. '
                       'This run is marked assisted.')
        self._checkpoint()

    def restart(self):
        """Replay the same assets and dates from day 0."""
        played = self.position > self.start or bool(self.history)
        self._reset()
        if played:
            # Replaying a round the player has already seen is an information
            # advantage, so it is disclosed the same way a rewind is.
            self.assisted = True
            self.reason = 'Round restarted. This run is marked assisted.'

    def step(self):
        if self.status != 'running':
            return
        self.holdings *= 1+self.returns.iloc[self.position+1].to_numpy()
        self.position += 1
        self.evaluate()
        self.record()

    def toggle(self):
        if self.status == 'paused':
            self.status = 'running'
        elif self.status == 'running':
            self.status = 'paused'
        else:
            raise ValueError('Confirm an allocation first, or create a new round after game over.')

    def snapshot(self):
        covariance = self.covariance()
        window = self.returns.iloc[self.position-self.rules.lookback+1:self.position+1]
        return dict(status=self.status, reason=self.reason,
                    assisted=self.assisted, can_rewind=self.can_rewind,
                    benchmark_symbol=self.benchmark_symbol,
                    benchmark_return=self.benchmark_return,
                    day=self.position-self.start, date=str(self.prices.index[self.position].date()),
                    tickers=list(self.prices.columns), weights=self.weights.tolist(), value=self.value,
                    total_return=self.value/self.rules.capital-1,
                    risk=volatility(self.weights, covariance), fees=self.fees,
                    historical_return=((1+window).prod()-1).tolist(),
                    covariance=covariance.tolist(), correlation=window.corr().fillna(0).to_numpy().tolist(),
                    history=[dict(row) for row in self.history], trades=[dict(row) for row in self.trades])
