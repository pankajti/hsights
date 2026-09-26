"""Chrome shared by the games: palette, figure theme, header, share panel.

Each game is its own Dash app mounted under its own path on one Flask server,
so component ids never collide between games and each keeps its own callbacks.
The shared stylesheet is served once, at ``/common/common.css``.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlencode

from dash import Dash, Input, Output, dcc, html
from flask import send_from_directory

from hsights.common import analytics

INK = '#e8eefc'
MUTED = '#a2afca'
DIM = '#97a6c5'
GRID = '#1c2741'
ACCENT = '#4b7bff'
UP = '#22d39a'
DOWN = '#ff5670'
WARN = '#ffb443'
MONO = 'ui-monospace, SF Mono, Menlo, Consolas, monospace'

COMMON_ASSETS = Path(__file__).with_name('assets')
PUBLIC_URL = os.environ.get('HSIGHTS_PUBLIC_URL', 'https://hsights.com').rstrip('/')


def ensure_common_route(server):
    """Serve the shared stylesheet at /common/<file>, once per server."""
    if 'hsights_common' in server.view_functions:
        return

    def hsights_common(filename):
        return send_from_directory(COMMON_ASSETS, filename, max_age=3600)

    server.add_url_rule('/common/<path:filename>', 'hsights_common', hsights_common)


def create_game_app(import_name, title, assets_folder, server=None, prefix='/'):
    """A Dash app for one game, standalone or mounted on a shared server."""
    app = Dash(import_name, server=server if server is not None else True,
               url_base_pathname=prefix, assets_folder=str(assets_folder),
               external_stylesheets=['/common/common.css'], update_title=None,
               title=title)
    app.index_string = app.index_string.replace(
        '{%metas%}', '{%metas%}<meta name="viewport" content="width=device-width, '
                     'initial-scale=1">')
    ensure_common_route(app.server)
    analytics.install(app.server)
    return app


def dark(figure, height=None):
    figure.update_layout(template='plotly_dark', autosize=True, showlegend=False,
                         margin=dict(l=44, r=14, t=30, b=32),
                         paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                         font=dict(family=MONO, color=MUTED, size=10.5),
                         hoverlabel=dict(bgcolor='#10172a', bordercolor=GRID,
                                         font=dict(color=INK, family=MONO, size=11)))
    if height:
        figure.update_layout(height=height)
    figure.update_xaxes(gridcolor=GRID, zerolinecolor=GRID)
    figure.update_yaxes(gridcolor=GRID, zerolinecolor=GRID)
    return figure


def chart_title(figure, text):
    figure.update_layout(title=dict(text=text, x=0, xanchor='left',
                                    font=dict(size=10, color=DIM, family=MONO)))
    return figure


def tile(label, value, tone=''):
    return html.Div([html.Small(label), html.Strong(value, className=f'v {tone}'.strip())],
                    className='tile')


def topbar(first, second, lede, *actions):
    """Game header with a way back to the hub."""
    return html.Header([
        html.A([html.Span('←', className='arrow'), 'HINDSIGHT'], href='/',
               className='hub-link', title='All games'),
        html.Div([html.Span(first), html.Span(second, className='alt')],
                 className='wordmark'),
        html.P(lede, className='lede'),
        *actions,
    ], className='top')


def mode_switch(component_id='mode', value='daily'):
    return dcc.RadioItems(
        id=component_id, value=value, className='seg mode',
        options=[{'label': "TODAY'S PUZZLE", 'value': 'daily'},
                 {'label': 'PRACTICE', 'value': 'practice'}])


def share_url(game):
    return f'{PUBLIC_URL}/{game}'


def share_panel():
    """Copy-to-clipboard and post-to-X for a finished round.

    Static in every game's layout so the tracking callback always has its
    input; the game fills it with :func:`share_outputs` when a round ends.
    """
    return html.Div([
        html.P('SHARE YOUR RESULT', className='cap'),
        html.Pre('', id='share-text', className='share-text'),
        html.Div([
            html.Div([dcc.Clipboard(id='share-copy', target_id='share-text',
                                    className='clip'), html.Span('COPY RESULT')],
                     className='share-button', title='Copy the result to your clipboard'),
            html.A('POST ON X', id='share-x', className='share-button', href='#',
                   target='_blank', rel='noopener noreferrer'),
        ], className='share-actions'),
    ], id='share', className='share', style={'display': 'none'})


#: The outputs :func:`share_outputs` fills, in order.
SHARE_OUTPUTS = (('share', 'style'), ('share-text', 'children'), ('share-x', 'href'))


def share_outputs(game, text):
    """Values for SHARE_OUTPUTS: shown with ``text``, or hidden when None."""
    if not text:
        return {'display': 'none'}, '', '#'
    return ({'display': 'block'}, text,
            '/share/x?' + urlencode({'game': game, 'text': text}))


def register_share_tracking(app, game):
    """Count clipboard copies as shares. Needs ``share-copy`` in the layout."""

    @app.callback(Output('share-copy', 'title'), Input('share-copy', 'n_clicks'),
                  prevent_initial_call=True)
    def _copied(clicks):
        if clicks:
            analytics.record(game, 'share', channel='copy')
        return 'Copied'
