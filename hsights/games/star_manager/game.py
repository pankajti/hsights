"""Managers, track records, hiring and the sealed second half."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MANAGERS = 200
MONTHS_SEEN = 36
MONTHS_HIDDEN = 36
MAXIMUM_HIRES = 3

ACTIVE_FEE = 0.010             # per year, every active manager
INDEX_FEE = 0.0005             # per year, the index fund
MARKET_RETURN = 0.085          # per year, arithmetic
MARKET_VOL = 0.15              # per year
SKILL_ALPHA = 0.04             # per year before fees, for the rare skilled manager
MAXIMUM_SKILLED = 5

_FIRST = ('Harbor', 'Summit', 'Granite', 'Beacon', 'Meridian', 'Northwind', 'Cedar',
          'Keystone', 'Riverbend', 'Ironwood', 'Silverline', 'Bluewater', 'Oakmont',
          'Redstone', 'Clearview', 'Highland', 'Lakeshore', 'Crescent', 'Pinnacle', 'Falcon')
_SECOND = ('Growth', 'Value', 'Opportunity', 'Select', 'Focus', 'Quality', 'Dynamic',
           'Core', 'Equity', 'Alpha')


def annualised(monthly):
    """Compound annual growth rate of a series of monthly simple returns."""
    monthly = np.asarray(monthly, dtype=float)
    if len(monthly) == 0:
        return 0.0
    growth = float(np.prod(1.0 + monthly))
    return growth ** (12.0 / len(monthly)) - 1.0


def _volatility(monthly):
    return float(np.std(monthly, ddof=1) * np.sqrt(12)) if len(monthly) > 1 else 0.0


def _max_drawdown(monthly):
    equity = np.cumprod(1.0 + np.asarray(monthly, dtype=float))
    peak = np.maximum.accumulate(np.concatenate(([1.0], equity)))[1:]
    return float(min(0.0, (equity / peak - 1.0).min()))


def _stats(monthly, index_monthly):
    ann = annualised(monthly)
    vol = _volatility(monthly)
    return dict(ann_return=ann, volatility=vol,
                sharpe=(float(np.mean(monthly)) * 12 / vol) if vol > 0 else 0.0,
                max_drawdown=_max_drawdown(monthly),
                vs_index=ann - annualised(index_monthly))


def _rank(values):
    """1 = best. Ties broken by position, which is random anyway."""
    order = np.argsort(-np.asarray(values), kind='stable')
    ranks = np.empty(len(values), dtype=int)
    ranks[order] = np.arange(1, len(values) + 1)
    return ranks


@dataclass(frozen=True)
class Outcome:
    """What happened after the hire. Built only by :meth:`StarManager.hire`."""

    hired: tuple
    portfolio_return: float          # annualised, years 4-6, after fees
    index_return: float              # annualised, years 4-6, after its fee
    beat_index: bool
    hires: tuple                     # per hire: name, rank before, rank after, returns
    skilled: tuple                   # per skilled manager: name, rank before, rank after
    hired_skilled: int
    top10_stayed: int                # of the top 10 in years 1-3, how many again in 4-6
    share_beating_index: float       # of all managers, years 4-6, after fees
    before: tuple                    # every manager's excess vs index, years 1-3
    after: tuple                     # every manager's excess vs index, years 4-6


class StarManager:
    """One universe of managers. Years 4-6 stay sealed until :meth:`hire`."""

    def __init__(self, seed=0, managers=MANAGERS):
        if managers < 20:
            raise ValueError('Need at least 20 managers.')
        rng = np.random.default_rng([int(seed), 23])
        months = MONTHS_SEEN + MONTHS_HIDDEN
        market = rng.normal(MARKET_RETURN / 12, MARKET_VOL / np.sqrt(12), size=months)
        beta = rng.uniform(0.8, 1.2, size=managers)
        tracking = rng.uniform(0.03, 0.12, size=managers) / np.sqrt(12)
        alpha = np.zeros(managers)
        count = int(rng.integers(0, MAXIMUM_SKILLED + 1))
        skilled = rng.choice(managers, size=count, replace=False) if count else np.array([], int)
        alpha[skilled] = SKILL_ALPHA / 12
        # Skill here means a steady edge, so the skilled run tighter books.
        tracking[skilled] = rng.uniform(0.03, 0.05, size=count) / np.sqrt(12)
        noise = rng.normal(0.0, 1.0, size=(managers, months)) * tracking[:, None]
        self._returns = (beta[:, None] * market[None, :] + alpha[:, None]
                         - ACTIVE_FEE / 12 + noise)
        self._index = market - INDEX_FEE / 12
        self._skilled = frozenset(int(i) for i in skilled)
        self.names = self._names(rng, managers)
        self.hired = None

    @staticmethod
    def _names(rng, count):
        pairs = [f'{first} {second}' for first in _FIRST for second in _SECOND]
        chosen = rng.choice(len(pairs), size=min(count, len(pairs)), replace=False)
        names = [pairs[i] for i in chosen]
        while len(names) < count:          # only for very large universes
            names.append(f'Fund {len(names) + 1}')
        return tuple(names)

    @property
    def size(self):
        return len(self.names)

    @property
    def revealed(self):
        return self.hired is not None

    # ------------------------------------------------------------ years 1-3

    def screen(self):
        """Track records for years 1-3: everything a fund screener would show."""
        seen = self._returns[:, :MONTHS_SEEN]
        index = self._index[:MONTHS_SEEN]
        rows = [dict(id=i, name=self.names[i], **_stats(seen[i], index))
                for i in range(self.size)]
        ranks = _rank([row['ann_return'] for row in rows])
        for row, rank in zip(rows, ranks):
            row['rank'] = int(rank)
        return rows

    def index_stats(self):
        return _stats(self._index[:MONTHS_SEEN], self._index[:MONTHS_SEEN])

    def growth(self, manager_ids):
        """Growth of 1 over years 1-3 for the given managers and the index."""
        paths = {self.names[i]: np.cumprod(1.0 + self._returns[i, :MONTHS_SEEN])
                 for i in manager_ids}
        return np.cumprod(1.0 + self._index[:MONTHS_SEEN]), paths

    # --------------------------------------------------------------- reveal

    def hire(self, manager_ids):
        """Hire up to three managers, equally weighted, or none for the index."""
        if self.revealed:
            raise ValueError('You have already hired. Start a new universe.')
        ids = tuple(sorted({int(i) for i in manager_ids}))
        if len(ids) > MAXIMUM_HIRES:
            raise ValueError(f'Hire at most {MAXIMUM_HIRES} managers.')
        if any(not 0 <= i < self.size for i in ids):
            raise ValueError('Unknown manager.')
        self.hired = ids

        seen, hidden = self._returns[:, :MONTHS_SEEN], self._returns[:, MONTHS_SEEN:]
        index_seen, index_hidden = self._index[:MONTHS_SEEN], self._index[MONTHS_SEEN:]
        before = np.array([annualised(row) for row in seen]) - annualised(index_seen)
        after = np.array([annualised(row) for row in hidden]) - annualised(index_hidden)
        rank_before, rank_after = _rank(before), _rank(after)

        index_return = annualised(index_hidden)
        if ids:
            portfolio = hidden[list(ids)].mean(axis=0)
            portfolio_return = annualised(portfolio)
        else:
            portfolio_return = index_return
        top10 = set(np.argsort(-before)[:10].tolist())
        top10_after = set(np.argsort(-after)[:10].tolist())

        def describe(i):
            return dict(id=i, name=self.names[i], rank_before=int(rank_before[i]),
                        rank_after=int(rank_after[i]),
                        return_before=annualised(seen[i]), return_after=annualised(hidden[i]),
                        skilled=i in self._skilled)

        return Outcome(
            hired=ids,
            portfolio_return=portfolio_return,
            index_return=index_return,
            beat_index=bool(ids) and portfolio_return > index_return,
            hires=tuple(describe(i) for i in sorted(ids, key=lambda j: rank_before[j])),
            skilled=tuple(describe(i) for i in sorted(self._skilled,
                                                      key=lambda j: rank_before[j])),
            hired_skilled=sum(1 for i in ids if i in self._skilled),
            top10_stayed=len(top10 & top10_after),
            share_beating_index=float(np.mean(after > 0)),
            before=tuple(float(x) for x in before),
            after=tuple(float(x) for x in after),
        )
