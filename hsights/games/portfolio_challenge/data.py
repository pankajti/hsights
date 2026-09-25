"""Price loading and seeded round selection.

Prices come from a parquet panel bundled with the package. Nothing on the
request path touches the network: the live yfinance and constituent-list
paths exist only for local development and for regenerating the panel with
``scripts/build_price_panel.py``.

Missing data is always an error. The game never forward fills, never
interpolates, and never picks a different trio because one had gaps in the
future, because that would let future data availability influence selection.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from functools import lru_cache
from io import StringIO
from itertools import combinations
from pathlib import Path
from random import Random
from time import time
from urllib.request import Request, urlopen
import hashlib
import os
import tempfile

import numpy as np
import pandas as pd

from .engine import Game, Rules, minimum_risk

# A deliberately small, disclosed present-day survivor universe, all USD US listings.
UNIVERSE = ('AAPL', 'MSFT', 'IBM', 'JPM', 'KO', 'PG', 'JNJ', 'WMT', 'XOM', 'CVX', 'CAT', 'MCD')

CONSTITUENTS_URL = 'https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv'

PACKAGE_DATA = Path(__file__).resolve().parents[2] / 'data'
PANEL_PATH = Path(os.environ.get('HSIGHTS_PRICE_PANEL', PACKAGE_DATA / 'prices.parquet'))

# Cap the feasibility search so a tight risk ceiling cannot spin through
# thousands of SLSQP solves while the server lock is held.
MAXIMUM_TRIO_ATTEMPTS = 200
CANDIDATE_BATCH = 30


@lru_cache(maxsize=1)
def price_panel():
    """The bundled date x symbol panel of adjusted closes, or None if absent."""
    if not PANEL_PATH.exists():
        return None
    panel = pd.read_parquet(PANEL_PATH)
    panel.index = pd.to_datetime(panel.index).tz_localize(None).normalize()
    panel = panel.sort_index().astype(float)
    if not panel.index.is_unique:
        raise ValueError('Bundled price panel has duplicate dates.')
    return panel


def panel_span():
    """(first, last) date available offline, or None when there is no panel."""
    panel = price_panel()
    return None if panel is None else (panel.index[0].date(), panel.index[-1].date())


def sp500_symbols():
    """Current constituents from the web. Used to rebuild the panel, not at runtime."""
    cache = Path(os.environ.get('PORTFOLIO_GAME_CACHE',
                               str(Path(tempfile.gettempdir()) / 'hsights-cache')))
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / 'sp500-constituents.csv'
    cached = path.exists() and time() - path.stat().st_mtime < 86400
    if cached:
        text = path.read_text()
    else:
        try:
            with urlopen(Request(CONSTITUENTS_URL, headers={'User-Agent': 'hsights/0.1'}),
                         timeout=20) as response:
                text = response.read().decode('utf-8')
        except OSError as exc:
            if path.exists():          # stale beats unavailable
                text = path.read_text()
            else:
                raise ValueError('Could not load the S&P 500 list. Check connectivity '
                                 'and retry.') from exc
    frame = pd.read_csv(StringIO(text))
    if 'Symbol' not in frame or not 450 <= len(frame) <= 550:
        raise ValueError('Unexpected S&P 500 constituent list; please retry later.')
    symbols = tuple(sorted(set(frame['Symbol'].str.strip().str.replace('.', '-', regex=False))))
    if not cached:
        path.write_text(text)
    return symbols


def available_symbols(universe='sp500'):
    """The pool a round may draw from, restricted to what we actually hold."""
    if universe not in ('curated', 'sp500'):
        raise ValueError('Unknown stock universe.')
    panel = price_panel()
    if universe == 'curated':
        pool = UNIVERSE
    elif panel is not None:
        pool = tuple(panel.columns)
    else:
        pool = sp500_symbols()
    if panel is not None:
        pool = tuple(symbol for symbol in pool if symbol in panel.columns)
    if len(pool) < 3:
        raise ValueError('The bundled price panel does not cover this universe.')
    return pool


def load_prices(start, rules=Rules(), refresh=False, cache_dir=None, tickers=UNIVERSE,
                end_date=None):
    """Adjusted closes for ``tickers``, from the bundled panel when available."""
    start = pd.Timestamp(start).normalize()
    begin = start - pd.Timedelta(days=rules.lookback * 2 + 30)
    end = (pd.Timestamp(end_date) + pd.Timedelta(days=1) if end_date
           else start + pd.Timedelta(days=rules.horizon * 2 + 30))

    panel = price_panel()
    if panel is not None and not refresh:
        missing = [symbol for symbol in tickers if symbol not in panel.columns]
        if missing:
            raise ValueError(f'No bundled history for {", ".join(sorted(missing)[:5])}.')
        window = panel.loc[(panel.index >= begin) & (panel.index < end), list(tickers)]
        if window.empty:
            raise ValueError('The bundled panel does not cover that date range. '
                             f'Available: {panel.index[0].date()} to {panel.index[-1].date()}.')
        return window.dropna(axis=0, how='any')

    return _download_prices(begin, end, tickers, cache_dir, refresh)


def _download_prices(begin, end, tickers, cache_dir, refresh):
    """Live yfinance path. Development and panel rebuilds only."""
    import yfinance as yf
    cache = Path(cache_dir or os.environ.get('PORTFOLIO_GAME_CACHE',
                                             str(Path(tempfile.gettempdir()) / 'hsights-cache')))
    cache.mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(cache / 'yfinance'))
    key = hashlib.sha256(
        f'{tuple(tickers)}|{begin.date()}|{end.date()}|adjusted-v1'.encode()).hexdigest()[:20]
    path = cache / f'{key}.csv'
    if path.exists() and not refresh:
        return pd.read_csv(path, index_col=0, parse_dates=True)
    raw = yf.download(list(tickers), start=str(begin.date()), end=str(end.date()),
                      auto_adjust=True, actions=False, progress=False, threads=False,
                      group_by='column', multi_level_index=True, timeout=20)
    if raw is None or raw.empty:
        raise ValueError('Yahoo returned no data. Check connectivity or retry after '
                         'rate limiting.')
    prices = raw['Close'].reindex(columns=list(tickers))
    prices.index = pd.to_datetime(prices.index).tz_localize(None).normalize()
    prices = prices.sort_index()
    if not prices.index.is_unique:
        raise ValueError('Yahoo returned duplicate daily rows.')
    temporary = path.with_suffix('.tmp')
    prices.to_csv(temporary)
    temporary.replace(path)
    return prices


def create_round(start='2022-01-03', seed=42, rules=Rules(), refresh=False,
                 universe='curated', end_date=None, exclude=()):
    start = pd.Timestamp(date.fromisoformat(str(start)))
    if start.date() >= date.today():
        raise ValueError('Start must be a historical date.')
    if end_date:
        end_date = date.fromisoformat(str(end_date)).isoformat()
        if pd.Timestamp(end_date) <= start or pd.Timestamp(end_date).date() >= date.today():
            raise ValueError('End must be after start and before today; future prices '
                             'are unavailable.')
    pool = available_symbols(universe)
    candidates = [symbol for symbol in pool if symbol not in exclude]
    if len(candidates) < 3:
        raise ValueError('Not enough different assets available.')
    # A random batch keeps the feasibility search small even for a 443-name pool.
    tickers = Random(seed).sample(candidates, min(CANDIDATE_BATCH, len(candidates)))
    prices = load_prices(start, rules, refresh, tickers=tickers, end_date=end_date)
    if end_date:
        prices = prices.loc[:end_date]
        boundary = int(prices.index.searchsorted(start))
        horizon = len(prices) - 1 - boundary
        if horizon < 1:
            raise ValueError('The date range must include at least one return after the start.')
        rules = replace(rules, horizon=horizon)
    boundary = int(prices.index.searchsorted(pd.Timestamp(start)))
    if boundary < rules.lookback or boundary + rules.horizon >= len(prices):
        raise ValueError('Choose an older date with enough prior history and a complete '
                         'future horizon.')
    # Admission uses availability plus pre-start statistics, never future returns.
    historical = prices.iloc[boundary - rules.lookback:boundary + 1]
    eligible = [ticker for ticker in prices if np.isfinite(historical[ticker]).all()
                and (historical[ticker] > 0).all()]
    choices = list(combinations(eligible, 3))
    Random(seed).shuffle(choices)
    for attempt, tickers in enumerate(choices):
        if attempt >= MAXIMUM_TRIO_ATTEMPTS:
            break
        sample = prices.loc[:, list(tickers)]
        past = sample.iloc[boundary - rules.lookback:boundary + 1]
        _, risk = minimum_risk(past.pct_change(fill_method=None).iloc[1:].cov().to_numpy())
        if risk > rules.risk_cap + 1e-10:
            continue
        # Missing future data is an error, not a reason to cherry-pick another trio.
        sample = sample.iloc[boundary - rules.lookback:boundary + rules.horizon + 1]
        if sample.isna().any().any():
            raise ValueError('Selected assets have missing prices in this round. No prices '
                             'were filled. Try another date.')
        return Game(sample, start, rules)
    raise ValueError('No eligible trio in this draw meets the risk ceiling. Draw new '
                     'assets, raise the ceiling or change the date.')
