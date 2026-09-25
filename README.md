# Hindsight

Playable market simulations built on real historical data. Live at **[hsights.com](https://hsights.com)**.

`/` is the landing page — what the game is, how to play, the rules, and the
honest limits. `/play` is the board; the wordmark in its header goes back home
and your round is kept while you are away.

Hindsight is the hub; each game lives under `hsights/games/`. The first is
**Portfolio Challenge**: you are handed three anonymous-until-drawn equities and
$100,000, you set the weights, and then real market history replays one trading
day per tick. Stay above the loss floor, stay below the volatility ceiling, and
meet the return target before the deadline.

The header carries one context-aware primary button that always states the next
move: **Confirm allocation → Play → Pause → Play again**. It dispatches to the same
engine calls as the dock and transport rows, so every guard still applies; it is a
shortcut for new players, not a second code path. Confirm deliberately *also* stays
beside the allocation sliders, where proximity makes it obvious what it commits.

A dashed **SPY** buy-and-hold line is drawn on the return chart, with a `VS SPY`
tile in the header showing your excess return. Like the prices, the benchmark is
held server-side and revealed one day at a time through the played history, so it
cannot leak a value you have not reached. It is gross of trading costs, where your
own return is net of them. Override the symbol with `HSIGHTS_BENCHMARK`; a
per-player choice can be layered on later without touching the engine.

When the round ends, a result modal rises from the centre of the screen with the
final score, a peak and drawdown summary, and three ways out: **Play again**
(restart the same round), **New round** (open settings), **View board** (dismiss
and inspect the final chart). It appears once per game over and stays dismissed.

Keyboard: `Space` plays or pauses, `→` steps one day, `S` opens settings, `Esc`
closes the result modal or the settings sheet. Shortcuts click the real buttons,
so a disabled button stays inert and neither sheet leaks keys to the board.

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
| Language | **Python 3** (Render may auto-detect Go — change it) |
| Build command | `pip install -r requirements.txt && python -c "import gunicorn"` |
| Start command | `python -m gunicorn hsights.server:application --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120` |
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

### `gunicorn: command not found` (exit 127)

The build succeeded but the deployed commit's `requirements.txt` had no
`gunicorn` in it. gunicorn *runs* the app rather than being imported by it, so
nothing in the dependency graph pulls it in and a freeze of a development
environment will omit it unless it happens to be installed there.

Check the commit SHA on the Render deploy against your pushed `HEAD` — the usual
cause is deploying before pushing the fix. The build command above now imports
gunicorn explicitly so this fails at build time with a clear message instead of
restart-looping, and the start command uses `python -m gunicorn` so it does not
depend on the console script being on `PATH`.

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

`--extra-symbols` defaults to `SPY` and fetches it separately, because SPY is an
ETF rather than an index member and so never appears in a panel built from a
constituent list. Pass `--extra-symbols` with no values to build without it; the
chart then simply has no reference line rather than the round failing.

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

### Routing

One Dash app serves both pages. `dcc.Location` drives a callback that swaps a
class on two wrappers that are *both* permanently mounted, rather than rebuilding
the layout per route. That is deliberate: every game component id stays in the
DOM, so no callback needs `suppress_callback_exceptions`, and `dcc.Link`
navigation never tears down a round in progress. The cost is that the landing
page ships the game's DOM with it — fine for now, and the reason `hsights/home.py`
is pure layout with no callbacks, so it can be lifted to a static CDN page later
without touching the game.

The router also stops the market clock whenever the board is off screen, and
picks it back up if the round was still running. It does not mutate the game, so
the status pill stays truthful.

Adding a game: create `hsights/games/<name>/`, build its layout, mount it as a
third page, and extend `is_play_path`. Longer term `create_app` belongs in
`hsights/server.py` with each game exposing `layout()` and
`register_callbacks(app)`; it lives in the game module today because there is
only one game.

## Licence

MIT. See [LICENSE](LICENSE).
