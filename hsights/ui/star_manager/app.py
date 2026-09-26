"""Dash interface for The Star Manager.

Years 4-6 of every manager's returns, and which managers are skilled, stay in
the server-side game object until the player hires.

Run standalone:
    python -m hsights.ui.star_manager
"""
from __future__ import annotations

import argparse
from pathlib import Path
import secrets
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from dash import Input, Output, State, ctx, dash_table, dcc, html, no_update
import numpy as np
import plotly.graph_objects as go

from hsights.common import analytics
from hsights.common.daily import daily_seed, today
from hsights.common.sessions import SessionStore
from hsights.common.ui import (ACCENT, DIM, DOWN, GRID, INK, MONO, MUTED, SHARE_OUTPUTS, UP,
                               WARN, chart_title, create_game_app, dark, mode_switch,
                               register_share_tracking, share_outputs, share_panel,
                               share_url, tile, topbar)
from hsights.games.star_manager import ACTIVE_FEE, INDEX_FEE, StarManager
from hsights.games.star_manager.game import MAXIMUM_HIRES

GAME = 'star-manager'
SESSION_TTL_SECONDS = 3600
SESSION_LIMIT = 400
PALETTE = (ACCENT, WARN, '#c38bff')

TABLE_STYLE = dict(
    style_table={'overflowX': 'auto'},
    style_header={'backgroundColor': 'transparent', 'color': DIM, 'fontWeight': 600,
                  'fontSize': '9px', 'letterSpacing': '.13em', 'border': 'none',
                  'borderBottom': f'1px solid {GRID}', 'fontFamily': MONO},
    style_cell={'backgroundColor': 'transparent', 'color': INK, 'border': 'none',
                'borderBottom': '1px solid rgba(34,45,73,.5)', 'fontFamily': MONO,
                'fontSize': '12px', 'padding': '8px 8px', 'textAlign': 'right'},
    style_cell_conditional=[{'if': {'column_id': 'name'}, 'textAlign': 'left',
                             'minWidth': '170px'}],
    style_data_conditional=[
        {'if': {'filter_query': '{vs_index} > 0', 'column_id': 'vs_index'}, 'color': UP},
        {'if': {'filter_query': '{vs_index} < 0', 'column_id': 'vs_index'}, 'color': DOWN},
        {'if': {'state': 'selected'}, 'backgroundColor': 'rgba(75,123,255,.12)',
         'border': 'none'},
    ],
)

PERCENT = dash_table.FormatTemplate.percentage(1)
COLUMNS = [
    {'name': 'RANK', 'id': 'rank', 'type': 'numeric'},
    {'name': 'FUND', 'id': 'name'},
    {'name': 'RETURN / YR', 'id': 'ann_return', 'type': 'numeric', 'format': PERCENT},
    {'name': 'VS INDEX', 'id': 'vs_index', 'type': 'numeric', 'format': PERCENT},
    {'name': 'VOLATILITY', 'id': 'volatility', 'type': 'numeric', 'format': PERCENT},
    {'name': 'SHARPE', 'id': 'sharpe', 'type': 'numeric',
     'format': dash_table.Format.Format(precision=2, scheme=dash_table.Format.Scheme.fixed)},
    {'name': 'MAX DRAWDOWN', 'id': 'max_drawdown', 'type': 'numeric', 'format': PERCENT},
]


def growth_figure(game, ids):
    index, paths = game.growth(ids)
    months = np.arange(1, len(index) + 1)
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=months, y=10000 * index, mode='lines', name='Index fund',
                                line=dict(color=MUTED, width=2, dash='dash'),
                                hovertemplate='Index fund · $%{y:,.0f}<extra></extra>'))
    for colour, (name, path) in zip(PALETTE, paths.items()):
        figure.add_trace(go.Scatter(x=months, y=10000 * path, mode='lines', name=name,
                                    line=dict(color=colour, width=2.2),
                                    hovertemplate=f'{name} · $%{{y:,.0f}}<extra></extra>'))
    figure.update_layout(showlegend=True)
    figure = dark(figure, 300)
    figure.update_layout(showlegend=True, legend=dict(orientation='h', y=1.12, x=0,
                                                      font=dict(size=10)))
    figure.update_xaxes(title=dict(text='month', font=dict(size=9)))
    chart_title(figure, '$10,000 OVER YEARS 1-3')
    figure.update_layout(title=dict(y=.98))
    return figure


def persistence_figure(outcome):
    before = np.array(outcome.before) * 100
    after = np.array(outcome.after) * 100
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=before, y=after, mode='markers', name='managers',
                                marker=dict(size=6, color='rgba(151,166,197,.45)'),
                                hovertemplate='years 1-3 %{x:+.1f}% · years 4-6 %{y:+.1f}%'
                                              '<extra></extra>'))
    skilled = [row['id'] for row in outcome.skilled]
    if skilled:
        figure.add_trace(go.Scatter(x=before[skilled], y=after[skilled], mode='markers',
                                    name='genuinely skilled',
                                    marker=dict(size=11, color=UP, symbol='star'),
                                    hovertemplate='skilled<extra></extra>'))
    if outcome.hired:
        hired = list(outcome.hired)
        figure.add_trace(go.Scatter(x=before[hired], y=after[hired], mode='markers',
                                    name='your hires',
                                    marker=dict(size=13, color='rgba(0,0,0,0)',
                                                line=dict(color=WARN, width=2.5)),
                                    hovertemplate='your hire<extra></extra>'))
    figure.add_hline(y=0, line_color=DIM, line_width=1)
    figure.add_vline(x=0, line_color=DIM, line_width=1)
    figure = dark(figure, 320)
    figure.update_layout(showlegend=True, legend=dict(orientation='h', y=1.1, x=0,
                                                      font=dict(size=10)))
    figure.update_xaxes(title=dict(text='vs index, years 1-3 (%/yr)', font=dict(size=9)))
    figure.update_yaxes(title=dict(text='vs index, years 4-6 (%/yr)', font=dict(size=9)))
    chart_title(figure, 'DOES A GOOD TRACK RECORD PREDICT THE NEXT ONE?')
    figure.update_layout(title=dict(y=.98))
    return figure


def share_text(outcome, size, mode, day):
    title = (f'The Star Manager · {day.isoformat()}' if mode == 'daily'
             else 'The Star Manager · practice')
    if outcome.hired:
        tops = ', '.join(f"#{row['rank_before']}" for row in outcome.hires)
        hired = f'Hired {len(outcome.hired)} (ranked {tops} of {size} in years 1-3)'
    else:
        hired = 'Hired nobody. Bought the index.'
    mark = '✅' if outcome.beat_index else ('➖' if not outcome.hired else '❌')
    return '\n'.join([
        title, hired,
        f'Years 4-6: {outcome.portfolio_return:+.1%}/yr vs index '
        f'{outcome.index_return:+.1%}/yr {mark}',
        f'Top 10 that stayed top 10: {outcome.top10_stayed}. '
        f'Real skill: {len(outcome.skilled)} of {size}.',
        share_url(GAME)])


def verdict_panel(outcome, size):
    excess = outcome.portfolio_return - outcome.index_return
    if not outcome.hired:
        title, tone = 'You bought the index. Most professionals would not have beaten you.', 'won'
    elif outcome.beat_index:
        title, tone = f'Your managers beat the index by {excess:+.1%} a year.', 'won'
    else:
        title, tone = f'Your stars trailed the index by {-excess:.1%} a year.', 'lost'
    skilled = len(outcome.skilled)
    if skilled:
        ranks = ', '.join(f"#{row['rank_before']}" for row in outcome.skilled)
        skill_line = (f'{skilled} of {size} managers had genuine skill. After three years '
                      f'they ranked {ranks}. You hired {outcome.hired_skilled} of them.')
    else:
        skill_line = (f'None of the {size} managers had any skill this time. Every track '
                      'record you studied was market exposure plus luck.')
    rows = [html.Tr([html.Td(row['name'] + (' ★' if row['skilled'] else '')),
                     html.Td(f"#{row['rank_before']}"), html.Td(f"{row['return_before']:+.1%}"),
                     html.Td(f"#{row['rank_after']}"),
                     html.Td(f"{row['return_after']:+.1%}",
                             className='up' if row['return_after'] > outcome.index_return
                             else 'down')])
            for row in outcome.hires]
    table = (html.Table([html.Thead(html.Tr([html.Th('YOUR HIRE'), html.Th('RANK Y1-3'),
                                             html.Th('RETURN Y1-3'), html.Th('RANK Y4-6'),
                                             html.Th('RETURN Y4-6')])),
                         html.Tbody(rows)]) if rows else None)
    return html.Div([
        html.P('YEARS 4-6, UNSEALED', className='cap'),
        html.H2(title),
        html.P(skill_line, className='headline'),
        html.P(f'Of the ten best managers in years 1-3, {outcome.top10_stayed} were in the '
               f'top ten again for years 4-6 (pure chance would keep about half of one). '
               f'Only {outcome.share_beating_index:.0%} of all managers beat the index over '
               f'years 4-6 after their {ACTIVE_FEE:.0%} fee.', className='headline'),
        html.Div([
            tile('YOUR RETURN Y4-6', f'{outcome.portfolio_return:+.1%}/yr',
                 'up' if outcome.portfolio_return >= outcome.index_return else 'down'),
            tile('INDEX FUND Y4-6', f'{outcome.index_return:+.1%}/yr'),
            tile('TOP 10 STAYED', str(outcome.top10_stayed), 'warn'),
            tile('SKILLED MANAGERS', f'{skilled} / {size}'),
        ], className='tiles wide'),
        table,
        dcc.Graph(figure=persistence_figure(outcome), config={'displayModeBar': False}),
    ], className=f'verdict {tone}')


def create_app(server=None, prefix='/'):
    app = create_game_app(__name__, 'The Star Manager · Hindsight',
                          Path(__file__).with_name('assets'), server=server, prefix=prefix)
    sessions = SessionStore(SESSION_TTL_SECONDS, SESSION_LIMIT)
    app.sessions = sessions

    def new_entry(mode):
        seed = daily_seed(GAME) if mode == 'daily' else secrets.randbelow(2 ** 31)
        return dict(game=StarManager(seed), mode=mode, day=today(), started=False)

    app.layout = lambda: html.Div([
        dcc.Store(id='sid', data=secrets.token_urlsafe(16)),
        topbar('STAR', 'MANAGER',
               'Two hundred fund managers, three years of track record each. Hire up to '
               'three, or buy the index. Then the next three years play out.',
               mode_switch('mode'),
               html.Button('NEW UNIVERSE', id='new', className='ghost',
                           title='Practice: 200 fresh managers. Daily: start today\'s over.')),
        html.Div(id='error', className='error'),
        html.Div([
            html.Section(dcc.Graph(id='growth', config={'displayModeBar': False}),
                         className='panel'),
            html.Aside([
                html.P('YOUR SHORTLIST', className='cap'),
                html.Div(id='picked', className='picked'),
                html.Div(id='tiles', className='tiles'),
                html.Button('HIRE THESE MANAGERS', id='hire', className='primary',
                            disabled=True),
                html.Button('JUST BUY THE INDEX FUND', id='index'),
                html.P(f'Tick up to {MAXIMUM_HIRES} funds in the table. Your money is split '
                       f'equally. Active funds charge {ACTIVE_FEE:.0%} a year, the index '
                       f'fund {INDEX_FEE:.2%}. All figures are after fees.', className='hint'),
            ], className='side'),
        ], className='board'),
        html.Div(id='verdict'),
        share_panel(),
        html.Div(dash_table.DataTable(
            id='table', columns=COLUMNS, data=[], row_selectable='multi',
            selected_row_ids=[], sort_action='native',
            sort_by=[{'column_id': 'rank', 'direction': 'asc'}],
            page_size=15, **TABLE_STYLE), className='panel table-wrap'),
        html.P('Managers are simulated so nothing can be looked up and today\'s universe '
               'is the same for everyone. Each is the market times a beta between 0.8 and '
               '1.2, plus noise; zero to five of them also have a real edge of 4% a year '
               'before fees. Not investment advice.', className='footer-note'),
    ], className='shell')

    @app.callback(
        Output('error', 'children'), Output('table', 'data'),
        Output('table', 'selected_row_ids'), Output('table', 'row_selectable'),
        Output('verdict', 'children'), Output('index', 'disabled'),
        *[Output(component, prop) for component, prop in SHARE_OUTPUTS],
        Input('new', 'n_clicks'), Input('mode', 'value'), Input('hire', 'n_clicks'),
        Input('index', 'n_clicks'), State('table', 'selected_row_ids'), State('sid', 'data'))
    def interact(new, mode, hire, index, selected, sid):
        trigger = ctx.triggered_id
        mode = mode if mode in ('daily', 'practice') else 'daily'
        slot = sessions.get(sid)
        fresh = trigger in ('new', 'mode') or slot is None
        if fresh:
            slot = sessions.put(sid, new_entry(mode))
        with slot.lock:
            entry = slot.value
            game = entry['game']
            error, verdict, shared = '', no_update, (no_update,) * len(SHARE_OUTPUTS)
            try:
                if trigger in ('hire', 'index'):
                    ids = [] if trigger == 'index' else list(selected or [])
                    outcome = game.hire(ids)
                    verdict = verdict_panel(outcome, game.size)
                    shared = share_outputs(GAME, share_text(outcome, game.size,
                                                            entry['mode'], entry['day']))
                    if not entry['started']:
                        entry['started'] = True
                        analytics.record(GAME, 'start', mode=entry['mode'])
                    analytics.record(GAME, 'complete', mode=entry['mode'], hires=len(ids),
                                     beat_index=outcome.beat_index)
            except ValueError as exc:
                error = str(exc)
            if fresh:
                verdict, shared = None, share_outputs(GAME, None)
            outputs = (error, game.screen() if fresh else no_update,
                       [] if fresh else no_update,
                       False if game.revealed else 'multi', verdict, game.revealed, *shared)
        slot.touch()
        return outputs

    @app.callback(Output('growth', 'figure'), Output('picked', 'children'),
                  Output('tiles', 'children'), Output('hire', 'disabled'),
                  Input('table', 'selected_row_ids'), Input('table', 'data'),
                  Input('verdict', 'children'), State('sid', 'data'))
    def shortlist(selected, data, verdict, sid):
        selected = [int(i) for i in (selected or [])]
        slot = sessions.get(sid)
        if slot is None:
            return go.Figure(), [], [], True
        with slot.lock:
            entry = slot.value
            game = entry['game']
            if selected and not entry['started']:
                # Shortlisting a manager is the first real move of a round.
                entry['started'] = True
                analytics.record(GAME, 'start', mode=entry['mode'])
            rows = {row['id']: row for row in game.screen()}
            index = game.index_stats()
            figure = growth_figure(game, selected[:MAXIMUM_HIRES])
            revealed = game.revealed
        picked = [html.Div([html.Span(rows[i]['name']),
                            html.Span(f"{rows[i]['ann_return']:+.1%}/yr")], className='row')
                  for i in selected if i in rows] or [html.P('Nobody yet.', className='empty')]
        tiles = [tile('INDEX FUND Y1-3', f"{index['ann_return']:+.1%}/yr"),
                 tile('PICKED', f'{len(selected)} / {MAXIMUM_HIRES}',
                      'down' if len(selected) > MAXIMUM_HIRES else ''),
                 tile('MANAGERS', str(game.size))]
        blocked = revealed or not 1 <= len(selected) <= MAXIMUM_HIRES
        return figure, picked, tiles, blocked

    register_share_tracking(app, GAME)
    return app


def main():
    parser = argparse.ArgumentParser(description='The Star Manager')
    parser.add_argument('--port', type=int, default=8094)
    parser.add_argument('--host', default='127.0.0.1')
    arguments = parser.parse_args()
    create_app().run(host=arguments.host, port=arguments.port, debug=False)


if __name__ == '__main__':
    main()
