# Hindsight

Playable market simulations built on real historical data. Live at **[hsights.com](https://hsights.com)**.

Hindsight is the hub; each game lives under `hsights/games/`. The first is
**Portfolio Challenge**: you are handed three anonymous-until-drawn equities and
$100,000, you set the weights, and then real market history replays one trading
day per tick. Stay above the loss floor, stay below the volatility ceiling, and
meet the return target before the deadline.

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m hsights                      # http://127.0.0.1:8051
```

`python -m hsights` binds `0.0.0.0` by default because that is what Render
requires. To keep a local run off your network:

```bash
HOST=127.0.0.1 python -m hsights
```

Run the tests:

```bash
python -m unittest discover -s tests -t . -v
```

Every module uses absolute imports and adds the repository root to `sys.path`
when run as a script, so `python hsights/server.py` and an IDE's run button
work without an editable install.

### Pinning the environment

`requirements.txt` is curated by hand — it is the minimum the web service needs,
and it deliberately omits `yfinance`. For an exact reproduction of a working
environment:

```bash
./scripts/freeze.sh                     # -> requirements-lock.txt
```

Do not use plain `pip freeze` for this. With an editable install it writes
`-e /Users/you/dev/git/hsights` and `hsights @ file:///Users/you/...`, and in a
conda environment it writes locally built wheels as
`pandas @ file:///opt/anaconda3/conda-bld/...`. All three are absolute paths to
your machine and all three break the Render build. `scripts/freeze.sh` uses
`pip list --format=freeze`, which always emits `name==version`, so conda-built
packages stay as real pins instead of being silently dropped — and it exits
non-zero if any local path survives.

---

## Deploying to Render

The repo ships a `render.yaml` blueprint. Either point Render at it, or set
these by hand:

| Setting | Value |
| --- | --- |
| Runtime | Python 3.12 |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn hsights.server:application --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120` |
| Health check path | `/healthz` |
| Instances | **1** |
| Plan | Starter or above |

### Three constraints that are not style preferences

**One worker, one instance.** Game state lives in a plain dict inside the
process. A second gunicorn worker gets its own empty session table, and Render
has no sticky sessions, so a second instance would serve half of a player's
requests from a process that has never heard of their game. Both look like
"my game randomly disappeared". Autoscaling must stay off until sessions move
to a shared store such as Render Key Value.

**Not the free plan.** Free web services spin down after 15 minutes of
inactivity, and because rounds are held in RAM, every spin-down silently
deletes every game in progress.

**Bind `0.0.0.0` and read `$PORT`.** Render injects `PORT` and fails the deploy
if it cannot detect a bound port. `hsights/config.py` reads both from the
environment.

### Capacity

Measured on one core: the per-tick callback costs about 38 ms and 20 KB, and a
single global lock serialises every request, so throughput does not improve with
more cores in the process. That works out to roughly **26 concurrent players at
1x speed, 13 at 2x, 7 at 4x**. Before expecting more, in order of payoff:
shrink the per-tick payload, narrow the lock to per-session, then move sessions
out of process.

---

## The price panel

`hsights/data/prices.parquet` holds 443 symbols of split- and dividend-adjusted
daily closes, 2011-07-21 to 2026-07-21 (7.9 MB). It is committed deliberately:
**the deployed app must never call Yahoo on the request path.** That call would
sit inside the session lock behind a 20-second timeout, and datacenter egress
gets rate limited far more aggressively than a home connection, so one throttled
request would freeze every player's clock.

Rebuild it with:

```bash
pip install -e '.[data]'
python scripts/build_price_panel.py --download --years 15
# or from a long-format CSV of date, symbol, close:
python scripts/build_price_panel.py --csv path/to/history.csv.gz
```

`HSIGHTS_PRICE_PANEL` overrides the path. If no panel is present the app falls
back to live yfinance downloads and `/healthz` reports `degraded`.

### Provenance and caveats

- Source is Yahoo Finance via `yfinance`, an unofficial endpoint. Check Yahoo's
  terms before redistributing this file in a public repository; if that is a
  concern, gitignore `hsights/data/prices.parquet` and build it during deploy,
  or move to an explicitly redistributable source.
- The symbol list reflects **current** index membership, so results carry
  survivorship bias. This is disclosed in the app.
- Adjusted closes approximate reinvested total returns. They are not historical
  executable prices.

---

## Honest limits

The game discloses these in the UI, and they belong here too.

- **Rewind and restart exist, and both flag the run `ASSISTED`.** Replaying
  prices you have already seen is an information advantage, so an assisted score
  is not comparable to a clean one.
- **Historical play is not cheat-proof.** Rebasing or normalising prices does not
  hide which stock it is: daily returns are invariant under rescaling, so a
  correlation match against public data recovers the ticker and the dates in
  about a second, with 100% accuracy, even for a 20-day round. A leaderboard
  needs either stitched synthetic series or server-side replay of the submitted
  decision log.
- **Estimated risk** is the annualised volatility of the current allocation. It
  is not a maximum possible loss, and not the realised volatility of a player's
  whole strategy.
- Thresholds are illustrative and not difficulty-calibrated. With no cash,
  shorting or leverage, a market shock can make a breach unavoidable.

---

## Layout

```
hsights/
  config.py                     environment-driven settings
  server.py                     WSGI entrypoint, /healthz, game mounting
  data/prices.parquet           bundled adjusted closes
  games/portfolio_challenge/
    engine.py                   accounting, risk, rules, rewind checkpoints
    data.py                     panel loading and seeded round selection
    app.py                      Dash layout and callbacks
    assets/                     stylesheet, keyboard transport, icons
scripts/build_price_panel.py    regenerate the panel
tests/                          engine, data and HTTP tests
```

Adding a game: create `hsights/games/<name>/`, build its Dash app, and mount it
in `hsights/server.py`. Portfolio Challenge sits at `/` while it is the only
one; moving it under a prefix means giving its Dash app a matching
`requests_pathname_prefix` so its assets still resolve.

## Licence

MIT. See [LICENSE](LICENSE).
