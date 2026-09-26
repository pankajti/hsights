# Hindsight

Short games about how markets fool people, built on real prices and honest
statistics. Live at **[hsights.com](https://hsights.com)**.

`/` is the hub: four game tiles, **shuffled on every visit** so no game gets the
top-left slot for free. The point of the hub right now is to find out which game
people actually play, finish, share and come back to, and then build that one
out. See [Measuring traction](#measuring-traction).

| Game | Path | Lesson | Data |
| --- | --- | --- | --- |
| **Noise Hunt** | `/noise-hunt/` | Why most backtests lie | Synthetic, daily puzzle |
| **Real or Random?** | `/real-or-random/` | How random real prices look | Real closes, daily puzzle |
| **The Star Manager** | `/star-manager/` | Skill, luck and fees | Synthetic, daily puzzle |
| **Portfolio Challenge** | `/play` | Risk you cannot sit out | Real history |

Every game follows the same rules: whatever the player is trying to predict stays
on the server until they commit, the ending scores them against what chance alone
would produce, and each game states its own limits.

### Noise Hunt

Search a price series for a trading rule (moving-average cross, breakout, mean
reversion) with a good Sharpe ratio, then **bet on whether the edge is real**
(10-90%) before the last 40% of the series is unsealed. About 4 in 10 series hide
a real edge - short-term reversal or trends that flip every couple of months -
and the rest are pure random walks. The bet is scored with a Brier rule (100 for
a confident correct call, 75 for 50%, 19 for a confident wrong one), and the
reveal compares the best rule against the best Sharpe expected from that many
tries on noise (the deflated Sharpe ratio of Bailey and Lopez de Prado).

The null benchmark caps the spread of trial Sharpes at the sampling error of a
single Sharpe. Without that cap a real edge inflates the spread and raises its
own bar: before the fix a planted edge with an out-of-sample Sharpe of 2 was
reported as found about 2% of the time. With it, a full sweep finds planted
edges about 70% of the time and flags pure noise essentially never.
`tests/test_noise_hunt.py` holds that calibration in place.

### Real or Random?

Ten rounds. Each shows two unlabeled charts rescaled to start at 100: a window of
a real S&P 500 stock, and a random walk with the same average daily return and
volatility. Pick the real one; the stock and dates are revealed after each
guess. The final score says how often a coin would do at least as well.

### The Star Manager

Two hundred simulated fund managers with three years of track record: return,
volatility, Sharpe, drawdown, versus the index. Hire up to three, or buy the
index. Then years 4-6 are unsealed. Zero to five managers have a real 4% a year
edge; everyone charges 1% a year. Over 200 universes, hiring the three best past
performers beats the index about 38% of the time.

### Portfolio Challenge

You are handed three S&P 500 stocks and $100,000, you set the weights, and real
market history replays one trading day per tick. Stay above the loss floor, stay
below the volatility ceiling, and meet the return target before the deadline. A
dashed **SPY** buy-and-hold line runs alongside, revealed one day at a time.
Keyboard: `Space` plays or pauses, `→` steps one day, `S` opens settings, `Esc`
closes the result modal or the settings sheet.

### Daily puzzles and sharing

Noise Hunt, Real or Random? and The Star Manager default to **today's puzzle**:
the same round for everyone, changing at 00:00 UTC, seeded from
`HSIGHTS_DAILY_SALT` + game + date. The source is public, so **set the salt to a
secret in production** or anyone can compute tomorrow's puzzle. A practice mode
draws a fresh random round. Finishing a round shows a spoiler-free result with
**Copy result** and **Post on X** buttons.

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

Every game keeps its sessions in a `SessionStore` (`hsights/common/sessions.py`)
with **one lock per session**. The global lock now guards only the dictionary,
for microseconds, so one player's chart render no longer queues everybody else.
Throughput is still bounded by the GIL and by one process; the figures below
were measured before this change, with a single global lock, and are a floor.

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

## Measuring traction

`hsights/common/analytics.py` records four events per game into one SQLite file:
`view` (with `src=hub&pos=N` when it came from a hub tile), `start`, `complete`
and `share`. Visitors get one random, HttpOnly cookie. No IP addresses, user
agents, names or emails are stored, and obvious bots and headless browsers are
ignored. Recording failures are logged and swallowed; analytics never breaks a
game.

Set `HSIGHTS_ADMIN_TOKEN` and open `/admin/traction?token=...` for the funnel,
per game, over unique visitors:

| Column | Meaning |
| --- | --- |
| HUB CTR | share of hub visitors who clicked this game's tile |
| FINISH % | of those who started a round, how many finished one |
| PLAYS / PLAYER | rounds finished per finishing player |
| DAILY | players who finished today's puzzle rather than practice |
| SHARE % | of finishers, how many copied or posted a result |
| RETURN % | of visitors, how many played on two or more different days |

It also shows click-through by tile *position*. Because the order is shuffled,
a big gap between position 1 and the rest measures position bias rather than
game appeal. Add `&days=7` or `&format=json` to the URL.

What to watch, in order: SHARE % and RETURN % (the two things a launch cannot
fake), then FINISH %, then HUB CTR (which mostly measures the tile copy).

| Variable | Default | Purpose |
| --- | --- | --- |
| `HSIGHTS_EVENTS_DB` | system temp dir | SQLite file. On Render, put it on the persistent disk in `render.yaml` |
| `HSIGHTS_ADMIN_TOKEN` | unset (report off) | unlocks `/admin/traction` |
| `HSIGHTS_ANALYTICS` | `1` | `0` turns recording off |
| `HSIGHTS_DAILY_SALT` | `hsights-dev` | secret that makes daily puzzles unpredictable |
| `HSIGHTS_PUBLIC_URL` | `https://hsights.com` | link used in share text |

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
  server.py                     WSGI entrypoint: mounts every game, /healthz
  home.py                       the hub (pure layout, shuffled tiles)
  common/
    sessions.py                 per-session-locked in-memory store
    analytics.py                traction events, cookie, /admin/traction, /share/x
    daily.py                    salted daily seeds
    ui.py                       shared chrome, figure theme, share panel
    assets/common.css           served at /common/common.css
  games/                        domain logic only: no Dash, no Flask
    noise_hunt/                 series, rules, backtest, stats, session + puzzle
    real_or_random/             real windows vs matched random walks
    star_manager/               managers, track records, the sealed half
    portfolio_challenge/        engine, data and (historically) its Dash app
  ui/                           one Dash app per game
    noise_hunt/  real_or_random/  star_manager/
  data/prices.parquet           bundled adjusted closes
scripts/build_price_panel.py    regenerate the panel
tests/                          domain, HTTP, hub and analytics tests
```

### Routing

Portfolio Challenge and the hub share one Dash app at `/` and `/play`, swapped
client-side by `dcc.Location`, so a round in progress survives a trip back to the
hub. Its router callback also records hub and board views, because client-side
navigation never reaches the server's page-load hook.

Every other game is **its own Dash app mounted on the same Flask server** under
its own path (`Dash(server=..., url_base_pathname='/noise-hunt/')`). Component
ids and callbacks cannot collide between games, and each game can run alone:

```bash
python -m hsights.ui.noise_hunt        # http://127.0.0.1:8092
python -m hsights.ui.real_or_random    # :8093
python -m hsights.ui.star_manager      # :8094
```

Adding a game: put the domain logic in `hsights/games/<name>/`, build a
`create_app(server=None, prefix='/')` in `hsights/ui/<name>/app.py` with
`hsights.common.ui.create_game_app`, add it to `MOUNTED_GAMES` in `server.py`,
to `GAMES` in `home.py` and `analytics.py`, and to `PAGE_ROUTES`.

## Licence

MIT. See [LICENSE](LICENSE).
