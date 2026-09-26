"""WSGI entrypoint.

Gunicorn serves ``hsights.server:application``. Game state lives in the
process, so the service must run a single worker with threads — never
multiple workers, and never more than one instance behind a load balancer
without sticky sessions.

One Flask server hosts every game:

    /                  the hub (shares a Dash app with Portfolio Challenge)
    /play              Portfolio Challenge, swapped in client-side
    /noise-hunt/       Noise Hunt
    /real-or-random/   Real or Random?
    /star-manager/     The Star Manager
    /admin/traction    per-game funnel, when HSIGHTS_ADMIN_TOKEN is set

Each game after the first is its own Dash app under its own path, so component
ids and callbacks never collide and a game can be added or removed without
touching the others.
"""
from __future__ import annotations

import logging
from pathlib import Path
import sys

if __package__ in (None, ''):
    # Running this file directly (python hsights/server.py, or the green arrow
    # in an IDE) puts only this directory on sys.path. Add the repository root
    # so the absolute imports below resolve without an editable install.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hsights.common import analytics
from hsights.common.ui import ensure_common_route
from hsights.config import HOST, PORT, SINGLE_INSTANCE
from hsights.games.portfolio_challenge.app import create_app
from hsights.games.portfolio_challenge.data import panel_span, price_panel
from hsights.ui.noise_hunt.app import create_app as create_noise_hunt
from hsights.ui.real_or_random.app import create_app as create_real_or_random
from hsights.ui.star_manager.app import create_app as create_star_manager

#: Games mounted beside the hub, by path. Order does not matter: the hub
#: shuffles its tiles on every visit.
MOUNTED_GAMES = (
    ('/noise-hunt/', create_noise_hunt),
    ('/real-or-random/', create_real_or_random),
    ('/star-manager/', create_star_manager),
)

LOGGER = logging.getLogger(__name__)


def create_server():
    """Return the Flask WSGI application with every game mounted."""
    dash_app = create_app()
    server = dash_app.server
    ensure_common_route(server)
    analytics.install(server)
    for prefix, factory in MOUNTED_GAMES:
        factory(server=server, prefix=prefix)

        # /noise-hunt without the slash would otherwise fall through to the
        # hub's catch-all and render the home page.
        def slash(prefix=prefix):
            from flask import redirect, request
            query = request.query_string.decode()
            return redirect(prefix + (f'?{query}' if query else ''), code=308)
        server.add_url_rule(prefix.rstrip('/'), f'slash{prefix.strip("/")}', slash)

    @server.get('/healthz')
    def healthz():
        """Render's health check. Confirms the price panel actually loaded."""
        span = panel_span()
        if span is None:
            return {'status': 'degraded', 'detail': 'no bundled price panel'}, 503
        panel = price_panel()
        return {'status': 'ok',
                'symbols': int(panel.shape[1]),
                'rows': int(panel.shape[0]),
                'first': str(span[0]),
                'last': str(span[1])}, 200

    span = panel_span()
    if span is None:
        LOGGER.warning('No bundled price panel found. The app will fall back to live '
                       'yfinance downloads, which are rate limited from datacenter IPs. '
                       'Run scripts/build_price_panel.py.')
    else:
        LOGGER.info('Price panel loaded: %s to %s', span[0], span[1])
    if SINGLE_INSTANCE:
        LOGGER.info('Sessions are in-process. Run one worker, one instance, no autoscaling.')
    return server


application = create_server()
server = application          # gunicorn hsights.server:server also works


def main():
    """Development server. Production uses gunicorn against ``application``."""
    logging.basicConfig(level=logging.INFO)
    application.run(host=HOST, port=PORT, debug=False)


if __name__ == '__main__':
    main()
