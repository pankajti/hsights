"""First-party traction analytics: which game do people play, finish, share
and come back to?

Deliberately small. One SQLite file, one anonymous random cookie, no IP
addresses, no user agents, no third parties. Events:

    view      a page of a game (or the hub) was loaded; ``src``/``pos`` say
              whether it came from a hub tile and in which position
    start     the player took a first real action in a round
    complete  the player reached the end of a round
    share     the player copied or posted a result

Everything here is best effort: a failure to record is logged and swallowed,
because analytics must never break a game.

Settings:
    HSIGHTS_ANALYTICS=0          turn recording off
    HSIGHTS_EVENTS_DB=path       SQLite file (put it on a persistent disk)
    HSIGHTS_ADMIN_TOKEN=secret   enables /admin/traction?token=secret
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
from threading import Lock

LOGGER = logging.getLogger(__name__)

COOKIE = 'hs_vid'
COOKIE_MAX_AGE = 400 * 24 * 3600
EVENTS = ('view', 'start', 'complete', 'share')
BOT = re.compile(r'bot|crawl|spider|slurp|preview|monitor|headless|curl|wget|python-requests',
                 re.IGNORECASE)

#: Human names and hub routes for every game, in no particular order.
GAMES = {
    'noise-hunt': 'Noise Hunt',
    'real-or-random': 'Real or Random?',
    'star-manager': 'The Star Manager',
    'portfolio': 'Portfolio Challenge',
}

_write_lock = Lock()


def enabled():
    return os.environ.get('HSIGHTS_ANALYTICS', '1') != '0'


def database_path():
    return Path(os.environ.get('HSIGHTS_EVENTS_DB')
                or Path(tempfile.gettempdir()) / 'hsights-events.sqlite3')


def _connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.execute('''CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, day TEXT NOT NULL, visitor TEXT NOT NULL,
        game TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}')''')
    connection.execute('CREATE INDEX IF NOT EXISTS events_game_day ON events (game, day)')
    return connection


# ------------------------------------------------------------------ visitor

def _request():
    try:
        from flask import has_request_context, request
    except ImportError:          # pragma: no cover - flask ships with dash
        return None
    return request if has_request_context() else None


def is_bot():
    request = _request()
    if request is None:
        return False
    return bool(BOT.search(request.headers.get('User-Agent', '') or ''))


def visitor_id():
    """The anonymous id for the current request, or 'anonymous' outside one."""
    request = _request()
    if request is None:
        return 'anonymous'
    from flask import g
    known = request.cookies.get(COOKIE)
    if known and re.fullmatch(r'[A-Za-z0-9_-]{8,64}', known):
        return known
    if not getattr(g, 'hs_vid', None):
        g.hs_vid = secrets.token_urlsafe(12)
    return g.hs_vid


# ------------------------------------------------------------------- record

def record(game, event, **detail):
    """Record one event. Never raises."""
    if not enabled() or event not in EVENTS or is_bot():
        return False
    try:
        now = datetime.now(timezone.utc)
        payload = json.dumps(detail, default=str, sort_keys=True)[:2000]
        with _write_lock:
            connection = _connect()
            try:
                connection.execute(
                    'INSERT INTO events (ts, day, visitor, game, event, detail) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (now.isoformat(timespec='seconds'), now.date().isoformat(),
                     visitor_id(), str(game), event, payload))
                connection.commit()
            finally:
                connection.close()
        return True
    except (sqlite3.Error, OSError) as exc:
        LOGGER.warning('analytics: could not record %s/%s: %s', game, event, exc)
        return False


# ---------------------------------------------------------------- reporting

def _rows(days):
    since = (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
    connection = _connect()
    try:
        return connection.execute(
            'SELECT day, visitor, game, event, detail FROM events WHERE day >= ?',
            (since,)).fetchall()
    finally:
        connection.close()


def summary(days=30):
    """Per-game funnel over the last ``days`` days.

    Rates are over *unique visitors*, so one enthusiast replaying forty rounds
    counts once in the funnel and shows up in ``plays_per_player`` instead.
    """
    rows = _rows(days)
    hub_visitors = set()
    tile_impressions = 0
    by_game = {game: dict(visitors=set(), from_hub=set(), started=set(), completed=set(),
                          sharers=set(), shares=0, completions=0, daily=set(),
                          days=defaultdict(set))
               for game in GAMES}
    positions = defaultdict(lambda: [0, 0])      # position -> [clicks, impressions]
    for day, visitor, game, event, detail in rows:
        try:
            extra = json.loads(detail or '{}')
        except ValueError:
            extra = {}
        if game == 'hub' and event == 'view':
            # Every hub view shows every tile once, in a shuffled order.
            hub_visitors.add(visitor)
            tile_impressions += len(GAMES)
            for position in range(1, len(GAMES) + 1):
                positions[position][1] += 1
            continue
        stats = by_game.get(game)
        if stats is None:
            continue
        stats['visitors'].add(visitor)
        stats['days'][visitor].add(day)
        if event == 'view' and extra.get('src') == 'hub':
            stats['from_hub'].add(visitor)
            position = extra.get('pos')
            if str(position).isdigit():
                positions[int(position)][0] += 1
        elif event == 'start':
            stats['started'].add(visitor)
        elif event == 'complete':
            stats['completed'].add(visitor)
            stats['completions'] += 1
            if extra.get('mode') == 'daily':
                stats['daily'].add(visitor)
        elif event == 'share':
            stats['sharers'].add(visitor)
            stats['shares'] += 1

    def rate(part, whole):
        return (len(part) / len(whole)) if whole else None

    games = []
    for game, stats in by_game.items():
        returning = {visitor for visitor, seen in stats['days'].items() if len(seen) >= 2}
        games.append(dict(
            game=game, name=GAMES[game],
            visitors=len(stats['visitors']),
            hub_clicks=len(stats['from_hub']),
            click_through=rate(stats['from_hub'], hub_visitors),
            started=len(stats['started']),
            completed=len(stats['completed']),
            completion_rate=rate(stats['completed'], stats['started']),
            plays_per_player=(stats['completions'] / len(stats['completed'])
                              if stats['completed'] else None),
            daily_players=len(stats['daily']),
            sharers=len(stats['sharers']), shares=stats['shares'],
            share_rate=rate(stats['sharers'], stats['completed']),
            returning=len(returning),
            return_rate=rate(returning, stats['visitors']),
        ))
    games.sort(key=lambda row: (-row['completed'], -row['visitors']))
    return dict(days=days, hub_visitors=len(hub_visitors),
                tile_impressions=tile_impressions,
                position_click_through={
                    position: (clicks / shown if shown else None)
                    for position, (clicks, shown) in sorted(positions.items())},
                games=games)


def _percent(value):
    return '—' if value is None else f'{value:.0%}'


def render_summary_html(report):
    from html import escape
    head = ('GAME', 'VISITORS', 'HUB CTR', 'STARTED', 'FINISHED', 'FINISH %',
            'PLAYS / PLAYER', 'DAILY', 'SHARE %', 'RETURNED', 'RETURN %')
    body = ''.join(
        '<tr>' + ''.join(f'<td>{escape(str(cell))}</td>' for cell in (
            row['name'], row['visitors'], _percent(row['click_through']), row['started'],
            row['completed'], _percent(row['completion_rate']),
            '—' if row['plays_per_player'] is None else f"{row['plays_per_player']:.1f}",
            row['daily_players'], _percent(row['share_rate']), row['returning'],
            _percent(row['return_rate']))) + '</tr>'
        for row in report['games'])
    position = ' · '.join(f'#{key}: {_percent(value)}'
                          for key, value in report['position_click_through'].items()) or '—'
    return f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Traction</title><link rel="stylesheet" href="/common/common.css"></head>
<body><div class="shell"><div class="top"><div class="wordmark"><span>HINDSIGHT</span>
<span class="alt">TRACTION</span></div><p class="lede">Last {report['days']} days ·
{report['hub_visitors']} unique hub visitors. Tile order is shuffled on every
visit, so click-through is not biased by position.</p></div>
<div class="panel"><table><thead><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr></thead>
<tbody>{body}</tbody></table></div>
<p class="hint">Click-through by tile position: {escape(position)}.
If position 1 is far above the rest, position bias is real and the shuffle matters.
Rates are over unique visitors. RETURNED = played on two or more different days.
Add <code>&amp;days=7</code> or <code>&amp;format=json</code> to the URL.</p>
</div></body></html>'''


# ------------------------------------------------------------------ install

#: Page routes whose loads count as views. Callback POSTs are not views. The hub
#: and Portfolio Challenge are one Dash app that switches pages client-side, so
#: their views are recorded by its router callback instead of here.
PAGE_ROUTES = {
    '/noise-hunt': 'noise-hunt',
    '/real-or-random': 'real-or-random',
    '/star-manager': 'star-manager',
}


def install(server):
    """Attach the visitor cookie, page-view logging and the admin report.

    Idempotent, so a game running standalone and the combined server can both
    call it on the same Flask app.
    """
    from flask import abort, g, jsonify, redirect, request

    if server.config.get('HSIGHTS_ANALYTICS_INSTALLED'):
        return server
    server.config['HSIGHTS_ANALYTICS_INSTALLED'] = True

    @server.after_request
    def _after(response):
        try:
            if request.method == 'GET' and response.status_code == 200:
                path = request.path.rstrip('/') or '/'
                game = PAGE_ROUTES.get(path)
                if game:
                    record(game, 'view', src=request.args.get('src', ''),
                           pos=request.args.get('pos', ''),
                           mode=request.args.get('mode', ''))
            if getattr(g, 'hs_vid', None) and not request.cookies.get(COOKIE):
                response.set_cookie(COOKIE, g.hs_vid, max_age=COOKIE_MAX_AGE,
                                    httponly=True, samesite='Lax',
                                    secure=request.is_secure)
        except Exception:        # noqa: BLE001 - analytics must never break a page
            LOGGER.exception('analytics: after_request failed')
        return response

    @server.get('/share/x')
    def _share_to_x():
        """Record a share, then hand off to X's compose page."""
        from urllib.parse import urlencode
        game = request.args.get('game', '')
        text = request.args.get('text', '')[:600]
        if game in GAMES:
            record(game, 'share', channel='x')
        return redirect('https://x.com/intent/post?' + urlencode({'text': text}), code=302)

    @server.get('/admin/traction')
    def _traction():
        token = os.environ.get('HSIGHTS_ADMIN_TOKEN', '')
        supplied = request.args.get('token', '')
        if not token or not hmac.compare_digest(token.encode(), supplied.encode()):
            abort(404)
        try:
            days = max(1, min(365, int(request.args.get('days', 30))))
        except ValueError:
            days = 30
        report = summary(days)
        if request.args.get('format') == 'json':
            return jsonify(report)
        return render_summary_html(report)

    return server
