"""WSGI entrypoint.

Gunicorn serves ``hsights.server:application``. Game state lives in the
process, so the service must run a single worker with threads — never
multiple workers, and never more than one instance behind a load balancer
without sticky sessions.

Adding a second game: build its Dash app the same way and mount it with
DispatcherMiddleware. Portfolio Challenge is at ``/`` today because it is
the only game; moving it under a prefix means giving its Dash app a matching
``requests_pathname_prefix`` so its assets resolve.
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

from hsights.config import HOST, PORT, SINGLE_INSTANCE
from hsights.games.portfolio_challenge.app import create_app
from hsights.games.portfolio_challenge.data import panel_span, price_panel

LOGGER = logging.getLogger(__name__)


def create_server():
    """Return the Flask WSGI application with every game mounted."""
    dash_app = create_app()
    server = dash_app.server

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
