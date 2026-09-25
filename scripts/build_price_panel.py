"""Build the bundled adjusted-close panel used by Portfolio Challenge.

The deployed app must never call Yahoo on the request path: that call sits
inside the server's session lock, and datacenter egress is rate limited far
more aggressively than a home connection. So prices are baked into a parquet
at build time and shipped with the package.

Two sources are supported:

    # from an existing long-format CSV (date, symbol, close)
    python scripts/build_price_panel.py --csv path/to/history.csv.gz

    # or straight from yfinance
    python scripts/build_price_panel.py --download --years 15

Both write hsights/data/prices.parquet as a date x symbol matrix of
split- and dividend-adjusted closes.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import json
import sys

import numpy as np
import pandas as pd

# Run directly (python scripts/build_price_panel.py) without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / 'hsights' / 'data' / 'prices.parquet'
MINIMUM_SYMBOLS = 40
MINIMUM_ROWS = 400


def from_csv(path: Path, coverage: float) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=['date', 'symbol', 'close'])
    panel = frame.pivot(index='date', columns='symbol', values='close').sort_index()
    panel.index = pd.to_datetime(panel.index).tz_localize(None).normalize()
    # Keep symbols that are present for nearly the whole window; the game needs
    # a complete series for the trio it selects and refuses to fill gaps.
    keep = panel.notna().mean() >= coverage
    panel = panel.loc[:, keep].dropna(axis=0, how='any')
    return panel


def from_yfinance(symbols: list[str], years: int) -> pd.DataFrame:
    import yfinance as yf
    end = pd.Timestamp(date.today())
    start = end - pd.Timedelta(days=int(years * 365.25) + 40)
    raw = yf.download(symbols, start=str(start.date()), end=str(end.date()),
                      auto_adjust=True, actions=False, progress=False,
                      threads=False, group_by='column', multi_level_index=True)
    if raw is None or raw.empty:
        raise SystemExit('Yahoo returned no data.')
    panel = raw['Close']
    panel.index = pd.to_datetime(panel.index).tz_localize(None).normalize()
    return panel.sort_index().dropna(axis=1, how='all').dropna(axis=0, how='any')


def validate(panel: pd.DataFrame) -> None:
    if panel.shape[1] < MINIMUM_SYMBOLS:
        raise SystemExit(f'Only {panel.shape[1]} symbols survived; need {MINIMUM_SYMBOLS}.')
    if len(panel) < MINIMUM_ROWS:
        raise SystemExit(f'Only {len(panel)} rows; need {MINIMUM_ROWS}.')
    if not panel.index.is_unique or not panel.index.is_monotonic_increasing:
        raise SystemExit('Dates must be unique and sorted.')
    values = panel.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise SystemExit('Panel must be strictly positive and finite.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--csv', type=Path, help='Long-format CSV with date, symbol, close.')
    source.add_argument('--download', action='store_true', help='Fetch from yfinance instead.')
    parser.add_argument('--symbols', nargs='*', help='Symbols to download (default: curated pool).')
    parser.add_argument('--years', type=int, default=15)
    parser.add_argument('--coverage', type=float, default=.98,
                        help='Minimum fraction of dates a symbol must cover to be kept.')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    if arguments.csv:
        panel = from_csv(arguments.csv, arguments.coverage)
        source_label = f'csv:{arguments.csv.name}'
    else:
        from hsights.games.portfolio_challenge.data import UNIVERSE
        panel = from_yfinance(list(arguments.symbols or UNIVERSE), arguments.years)
        source_label = 'yfinance'

    validate(panel)
    panel = panel.astype('float32')
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(arguments.output, compression='zstd')

    manifest = {
        'source': source_label,
        'built': date.today().isoformat(),
        'symbols': int(panel.shape[1]),
        'rows': int(panel.shape[0]),
        'start': str(panel.index[0].date()),
        'end': str(panel.index[-1].date()),
        'prices_auto_adjusted': True,
        'caveats': [
            'Yahoo/yfinance is an unofficial data source.',
            'A current S&P membership list introduces survivorship bias.',
            'Adjusted closes approximate reinvested total returns, not executable prices.',
        ],
    }
    arguments.output.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n')
    size = arguments.output.stat().st_size / 1024 ** 2
    print(f'wrote {arguments.output} — {panel.shape[1]} symbols x {panel.shape[0]} rows, {size:.1f} MB')
    print(f'      {panel.index[0].date()} .. {panel.index[-1].date()}')


if __name__ == '__main__':
    main()
