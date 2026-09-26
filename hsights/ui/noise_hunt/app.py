"""Dash interface for Noise Hunt.

The session lives server-side for two reasons: the holdout must not reach the
browser before the reveal, and neither may the hidden coin that decided
whether this series has an edge at all. Everything else here is presentation.

Run standalone:
    python -m hsights.ui.noise_hunt
"""
from __future__ import annotations

import argparse
from pathlib import Path
import secrets
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from dash import Input, Output, State, ctx, dcc, html, no_update
import numpy as np
import plotly.graph_objects as go

from hsights.common import analytics
from hsights.common.daily import daily_seed, today
from hsights.common.sessions import SessionStore
from hsights.common.ui import (ACCENT, DOWN, MUTED, SHARE_OUTPUTS, UP, WARN, chart_title,
                               create_game_app, dark, mode_switch, register_share_tracking,
                               share_outputs, share_panel, share_url, tile, topbar)
from hsights.games.noise_hunt import (BELIEFS, EDGE_NAMES, Breakout, MeanReversion,
                                      MovingAverageCross, puzzle)

GAME = 'noise-hunt'
SESSION_TTL_SECONDS = 3600
SESSION_LIMIT = 300

RULE_LABELS = {
    'ma': ('Fast window', 'Slow window'),
    'breakout': ('Lookback days', ''),
    'reversion': ('Lookback days', 'Z-score threshold'),
}

SWEEP = ([MovingAverageCross(f, s, short)
          for f in (5, 10, 20) for s in (30, 50, 100) for short in (True, False)]
         + [Breakout(n, short) for n in (10, 20, 40, 60) for short in (True, False)]
         + [MeanReversion(n, z) for n in (10, 20, 40) for z in (0.5, 1.0, 1.5)])


def build_rule(kind, first, second, allow_short):
    """Translate the control values into a domain rule."""
    first = int(first or 0)
    if kind == 'ma':
        return MovingAverageCross(first, int(second or 0), bool(allow_short))
    if kind == 'breakout':
        return Breakout(first, bool(allow_short))
    if kind == 'reversion':
        return MeanReversion(first, float(second or 0), bool(allow_short))
    raise ValueError('Unknown rule type.')


def price_figure(prices, equity=None):
    figure = go.Figure()
    figure.add_trace(go.Scatter(y=prices, mode='lines', name='price',
                                line=dict(color=MUTED, width=1.6),
                                hovertemplate='day %{x} · %{y:.2f}<extra></extra>'))
    chart_title(figure, 'THE SERIES YOU CAN SEE · THE REST IS SEALED UNTIL THE REVEAL')
    if equity is not None:
        scaled = np.asarray(equity) * prices[0]
        colour = UP if scaled[-1] >= prices[0] else DOWN
        figure.add_trace(go.Scatter(y=scaled, mode='lines', name='best rule',
                                    line=dict(color=colour, width=2.2),
                                    hovertemplate='best rule %{y:.2f}<extra></extra>'))
    return dark(figure, 300)


def sharpe_figure(reveal):
    sharpes = reveal.trial_sharpes
    figure = go.Figure()
    figure.add_trace(go.Histogram(x=sharpes, nbinsx=max(8, min(30, len(sharpes) // 2)),
                                  showlegend=False,
                                  marker=dict(color='rgba(75,123,255,.45)',
                                              line=dict(color=ACCENT, width=1)),
                                  hovertemplate='%{x:.2f}<extra>%{y} rules</extra>'))
    # The three markers are often within a fraction of a Sharpe of each other,
    # so they are named in a legend rather than labelled on the chart.
    markers = (('expected best from chance', reveal.expected_best_under_null, WARN, 'dot'),
               ('your best, in sample', reveal.in_sample_sharpe, UP, 'solid'),
               ('your best, out of sample', reveal.holdout_sharpe, DOWN, 'solid'))
    for name, value, colour, dash in markers:
        figure.add_vline(x=value, line_color=colour, line_dash=dash, line_width=2)
        figure.add_trace(go.Scatter(x=[None], y=[None], mode='lines', name=f'{name} {value:+.2f}',
                                    line=dict(color=colour, dash=dash, width=2)))
    chart_title(figure, 'SHARPE OF EVERY RULE YOU TRIED')
    figure = dark(figure, 300)
    figure.update_layout(showlegend=True, margin=dict(t=56),
                         legend=dict(orientation='h', x=0, y=1.13, font=dict(size=10)))
    return figure


def trials_table(trials, best_index):
    if not trials:
        return html.P('No rules tested yet. Build one on the right, or sweep them all.',
                      className='empty')
    rows = sorted(trials, key=lambda item: -item.sharpe)[:12]
    return html.Table([
        html.Thead(html.Tr([html.Th('RULE'), html.Th('SHARPE'),
                            html.Th('RETURN'), html.Th('TRADES')])),
        html.Tbody([
            html.Tr([html.Td(item.label), html.Td(f'{item.sharpe:+.2f}'),
                     html.Td(f'{item.total_return:+.1%}'), html.Td(str(item.trades))],
                    className='best' if item.index == best_index else '')
            for item in rows])])


def belief_label(belief):
    return f'{belief:.0%}'


def share_text(result, mode, day):
    title = f'Noise Hunt · {day.isoformat()}' if mode == 'daily' else 'Noise Hunt · practice'
    truth = ('Real edge' if result.series_had_signal else 'Pure noise')
    lines = [title, f'{truth}. ']
    if result.belief is not None:
        mark = {True: '✅', False: '❌', None: '🤷'}[result.called_it]
        lines[1] += f'I said {belief_label(result.belief)} edge {mark} {result.belief_points}/100'
    lines.append(f'{result.trials} rules tested · best Sharpe {result.in_sample_sharpe:+.2f} '
                 f'· chance predicts {result.expected_best_under_null:+.2f}')
    lines.append(f'Out of sample: {result.holdout_sharpe:+.2f}')
    lines.append(share_url(GAME))
    return '\n'.join(lines)


def verdict_panel(result):
    if result.series_had_signal:
        title = 'This series had a real edge.'
        truth = f'It was built with {EDGE_NAMES.get(result.edge_kind, "a real edge")}.'
    else:
        title = 'There was nothing to find.'
        truth = 'It was a pure random walk: no trend, no memory, no edge.'
    if result.belief is None:
        call = None
    else:
        tone = ('up-text' if result.called_it else
                'down-text' if result.called_it is False else 'warn-text')
        verb = {True: 'You called it.', False: 'You called it wrong.',
                None: 'You sat on the fence.'}[result.called_it]
        call = html.P([html.Span(verb, className=tone), ' You said ',
                       html.B(belief_label(result.belief)),
                       ' chance of a real edge: ',
                       html.B(f'{result.belief_points}/100', className=tone), ' points.'],
                      className='score')
    good = bool(result.called_it) or (result.called_it is None and result.verdict != 'fooled')
    return html.Div([
        html.P('THE REVEAL', className='cap'),
        html.H2(title),
        html.P(f'{truth} {result.headline}', className='headline'),
        call,
        html.Div([
            tile('RULES TRIED', str(result.trials)),
            tile('BEST IN SAMPLE', f'{result.in_sample_sharpe:+.2f}'),
            tile('EXPECTED FROM CHANCE', f'{result.expected_best_under_null:+.2f}', 'warn'),
            tile('OUT OF SAMPLE', f'{result.holdout_sharpe:+.2f}',
                 'up' if result.holdout_sharpe > 0 else 'down'),
            tile('DEFLATED SHARPE', f'{result.deflated_sharpe_probability:.0%}',
                 'up' if result.deflated_sharpe_probability >= .95 else 'down'),
        ], className='tiles wide'),
        html.P('A backtest has to beat what chance alone would produce from the same '
               'number of attempts. Below 95% on the deflated Sharpe means it does not. '
               'About 4 in 10 series hide a real edge; the rest are noise.',
               className='hint'),
    ], className=f'verdict {"won" if good else "lost"}')


def create_app(server=None, prefix='/'):
    app = create_game_app(__name__, 'Noise Hunt · Hindsight',
                          Path(__file__).with_name('assets'), server=server, prefix=prefix)
    sessions = SessionStore(SESSION_TTL_SECONDS, SESSION_LIMIT)

    # Exposed so tests can reach the sealed holdout and assert it never shipped.
    # Nothing in the callbacks reads it through this handle.
    app.sessions = sessions

    def new_entry(mode):
        if mode == 'daily':
            day, seed = today(), daily_seed(GAME)
        else:
            day, seed = today(), secrets.randbelow(2 ** 31)
        return dict(session=puzzle(seed), mode=mode, day=day, started=False)

    app.layout = lambda: html.Div([
        dcc.Store(id='sid', data=secrets.token_urlsafe(16)),
        topbar('NOISE', 'HUNT',
               'Search this price series for a trading rule with a good Sharpe. '
               'You will find one. Then decide: is the edge real, or did you just look '
               'hard enough?',
               mode_switch('mode'),
               html.Button('NEW SERIES', id='new', className='ghost',
                           title='Practice: a fresh random series. Daily: start today\'s over.')),
        html.Div(id='error', className='error'),
        html.Div([
            html.Section([dcc.Graph(id='chart', config={'displayModeBar': False}),
                          dcc.Graph(id='reveal-chart', config={'displayModeBar': False},
                                    style={'display': 'none'})], className='panel'),
            html.Aside([
                html.P('1 · BUILD A RULE', className='cap'),
                dcc.RadioItems(id='kind', className='seg', value='ma',
                               options=[{'label': 'MA CROSS', 'value': 'ma'},
                                        {'label': 'BREAKOUT', 'value': 'breakout'},
                                        {'label': 'REVERSION', 'value': 'reversion'}]),
                html.Label([html.Span('Fast window', id='label-a'),
                            dcc.Input(id='param-a', type='number', value=10, min=2, step=1)]),
                html.Label([html.Span('Slow window', id='label-b'),
                            dcc.Input(id='param-b', type='number', value=50, min=0.1)],
                           id='param-b-wrap'),
                dcc.Checklist(id='short', className='check', value=['on'],
                              options=[{'label': ' Allow short positions', 'value': 'on'}]),
                html.Button('TEST THIS RULE', id='test', className='primary'),
                html.Button(f'SWEEP ALL {len(SWEEP)} BUILT-IN RULES', id='sweep'),
                html.Div(id='tiles', className='tiles'),
                html.P('2 · IS THERE A REAL EDGE?', className='cap', style={'marginTop': '8px'}),
                dcc.RadioItems(id='belief', className='choices', value=None,
                               options=[{'label': belief_label(b), 'value': b}
                                        for b in BELIEFS]),
                html.Div([html.Span('SURELY NOISE'), html.Span('SURELY REAL')],
                         className='choice-legend'),
                html.Button('3 · REVEAL THE TRUTH', id='reveal', className='danger',
                            disabled=True),
                html.P('The last 40% of the series stays sealed on the server until you '
                       'reveal. Test at least one rule and place your call first.',
                       className='hint'),
            ], className='side'),
        ], className='board'),
        html.Div(id='verdict'),
        share_panel(),
        html.Div(trials_table([], -1), id='trials', className='panel'),
        html.P('Series are synthetic, so nothing here can be looked up, and today\'s '
               'puzzle is the same for everyone. Scoring uses the Brier rule: 100 for a '
               'confident correct call, 75 for 50%, as low as 19 for a confident wrong one. '
               'Not investment advice.', className='footer-note'),
    ], className='shell')

    @app.callback(Output('label-a', 'children'), Output('label-b', 'children'),
                  Output('param-b-wrap', 'style'), Output('param-b', 'value'),
                  Input('kind', 'value'), prevent_initial_call=True)
    def relabel(kind):
        first, second = RULE_LABELS.get(kind, RULE_LABELS['ma'])
        style = {'display': 'none'} if not second else {}
        default = {'ma': 50, 'reversion': 1.0}.get(kind, no_update)
        return first, second, style, default

    @app.callback(
        Output('error', 'children'), Output('chart', 'figure'),
        Output('tiles', 'children'), Output('trials', 'children'),
        Output('reveal', 'disabled'), Output('verdict', 'children'),
        Output('reveal-chart', 'figure'), Output('reveal-chart', 'style'),
        Output('belief', 'value'),
        *[Output(component, prop) for component, prop in SHARE_OUTPUTS],
        Input('new', 'n_clicks'), Input('mode', 'value'), Input('test', 'n_clicks'),
        Input('sweep', 'n_clicks'), Input('reveal', 'n_clicks'), Input('belief', 'value'),
        State('sid', 'data'), State('kind', 'value'), State('param-a', 'value'),
        State('param-b', 'value'), State('short', 'value'))
    def interact(new, mode, test, sweep, reveal, belief, sid, kind, first, second, short):
        trigger = ctx.triggered_id
        mode = mode if mode in ('daily', 'practice') else 'daily'
        error, verdict = '', no_update
        reveal_figure, reveal_style, belief_out = no_update, no_update, no_update
        shared = (no_update,) * len(SHARE_OUTPUTS)

        slot = sessions.get(sid)
        if trigger in ('new', 'mode') or slot is None:
            slot = sessions.put(sid, new_entry(mode))
            verdict, reveal_style, belief_out = None, {'display': 'none'}, None
            shared = share_outputs(GAME, None)
        with slot.lock:
            entry = slot.value
            session = entry['session']
            try:
                if trigger == 'test':
                    session.try_rule(build_rule(kind, first, second, short))
                elif trigger == 'sweep':
                    session.search(SWEEP)
                elif trigger == 'reveal':
                    if belief not in BELIEFS:
                        raise ValueError('Make your call first: how likely is a real edge?')
                    result = session.reveal(belief=belief)
                    verdict = verdict_panel(result)
                    reveal_figure = sharpe_figure(result)
                    reveal_style = {'display': 'block'}
                    shared = share_outputs(GAME, share_text(result, entry['mode'],
                                                            entry['day']))
                    analytics.record(GAME, 'complete', mode=entry['mode'],
                                     verdict=result.verdict, trials=result.trials,
                                     points=result.belief_points)
                if trigger in ('test', 'sweep') and not entry['started']:
                    entry['started'] = True
                    analytics.record(GAME, 'start', mode=entry['mode'])
            except (ValueError, TypeError) as exception:
                error = str(exception)

            best = session.best
            equity = session.backtest_for(best).equity if best else None
            tiles = [tile('RULES TRIED', str(len(session.trials))),
                     tile('BEST SHARPE', f'{best.sharpe:+.2f}' if best else '—',
                          'up' if best and best.sharpe > 0 else ''),
                     tile('BEST RETURN', f'{best.total_return:+.1%}' if best else '—')]
            # A new session clears the call, so the old one no longer counts.
            call = None if belief_out is None else belief
            reveal_blocked = (not session.trials or session.revealed
                              or call not in BELIEFS)
            outputs = (error, price_figure(session.visible_prices, equity), tiles,
                       trials_table(session.trials, best.index if best else -1),
                       reveal_blocked, verdict, reveal_figure, reveal_style, belief_out,
                       *shared)
        slot.touch()
        return outputs

    register_share_tracking(app, GAME)
    return app


def main():
    parser = argparse.ArgumentParser(description='Noise Hunt')
    parser.add_argument('--port', type=int, default=8092)
    parser.add_argument('--host', default='127.0.0.1')
    arguments = parser.parse_args()
    create_app().run(host=arguments.host, port=arguments.port, debug=False)


if __name__ == '__main__':
    main()
