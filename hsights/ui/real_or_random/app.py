"""Dash interface for Real or Random?

Which chart is real stays on the server until the player has guessed. The
browser receives two unlabeled paths per round and nothing else.

Run standalone:
    python -m hsights.ui.real_or_random
"""
from __future__ import annotations

import argparse
from pathlib import Path
import secrets
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from dash import Input, Output, State, ctx, dcc, html
import plotly.graph_objects as go

from hsights.common import analytics
from hsights.common.daily import daily_seed, today
from hsights.common.sessions import SessionStore
from hsights.common.ui import (DIM, MUTED, SHARE_OUTPUTS, UP, chart_title, create_game_app,
                               dark, mode_switch, register_share_tracking, share_outputs,
                               share_panel, share_url, tile, topbar)
from hsights.games.real_or_random import RealOrRandom

GAME = 'real-or-random'
SESSION_TTL_SECONDS = 3600
SESSION_LIMIT = 400


def default_panel():
    from hsights.games.portfolio_challenge.data import price_panel
    return price_panel()


def path_figure(path, colour=MUTED, title=''):
    figure = go.Figure(go.Scatter(y=path, mode='lines', line=dict(color=colour, width=1.8),
                                  hovertemplate='day %{x} · %{y:.1f}<extra></extra>'))
    figure.update_xaxes(title=dict(text='trading days', font=dict(size=9)))
    chart_title(figure, title)
    return dark(figure, 270)


def dots(game):
    marks = []
    for index in range(game.rounds):
        if index < len(game.results):
            right = game.results[index]['correct']
            marks.append(html.Span('✓' if right else '✗',
                                   className=f"dot {'right' if right else 'wrong'}"))
        else:
            now = index == len(game.results) and not game.finished
            marks.append(html.Span(str(index + 1), className=f"dot {'now' if now else ''}"))
    return marks


def chance(probability):
    """A coin-flip probability, with a decimal where rounding would mislead."""
    return f'{probability:.1%}' if probability < .10 else f'{probability:.0%}'


def shown_round(game):
    """The round on screen: the one just answered, or the one being asked."""
    answered = game.awaiting_next or game.finished
    return min(len(game.results) + (0 if answered else 1), game.rounds)


def share_text(game, mode, day):
    score = game.score()
    title = (f'Real or Random? · {day.isoformat()}' if mode == 'daily'
             else 'Real or Random? · practice')
    squares = ''.join('🟩' if result['correct'] else '🟥' for result in game.results)
    return '\n'.join([
        title,
        f"{squares} {score['correct']}/{score['rounds']}",
        f"A coin does at least this well {chance(score['coin_probability'])} of the time.",
        share_url(GAME)])


def verdict_panel(game):
    score = game.score()
    correct, rounds, odds = score['correct'], score['rounds'], score['coin_probability']
    if score['verdict'] == 'skill':
        title, tone = f'{correct}/{rounds}. You can genuinely tell.', 'won'
        body = (f'Pure guessing does this well only {chance(odds)} of the time. You are '
                'picking up something real markets do that a random walk does not.')
    elif score['verdict'] == 'coin':
        title, tone = f'{correct}/{rounds}. A coin could have done that.', 'lost'
        body = (f'Guessing scores at least {correct}/{rounds} {chance(odds)} of the time, so '
                'this is not evidence you can tell real from random. Most people cannot, '
                'which is the point: over weeks and months, real prices look a lot like '
                'a random walk.')
    else:
        title, tone = f'{correct}/{rounds}. The random walks fooled you.', 'lost'
        body = ('You picked the random walk more often than the real stock. Real charts '
                'often look less "chart-like" than people expect, and noise often looks '
                'more like a story.')
    return html.Div([
        html.P('FINAL SCORE', className='cap'),
        html.H2(title),
        html.P(body, className='headline'),
        html.P('What gives real prices away, when anything does: calm stretches followed '
               'by wild ones (volatility clusters) and the occasional enormous single-day '
               'move (fat tails). The random walks here have the same average return and '
               'volatility as their real twin, but neither of those habits.',
               className='headline'),
    ], className=f'verdict {tone}')


def create_app(server=None, prefix='/', panel_loader=default_panel):
    app = create_game_app(__name__, 'Real or Random? · Hindsight',
                          Path(__file__).with_name('assets'), server=server, prefix=prefix)
    sessions = SessionStore(SESSION_TTL_SECONDS, SESSION_LIMIT)
    app.sessions = sessions

    def new_entry(mode):
        seed = daily_seed(GAME) if mode == 'daily' else secrets.randbelow(2 ** 31)
        return dict(game=RealOrRandom(panel_loader(), seed=seed), mode=mode, day=today(),
                    started=False, completed=False)

    app.layout = lambda: html.Div([
        dcc.Store(id='sid', data=secrets.token_urlsafe(16)),
        topbar('REAL', 'OR RANDOM?',
               'One chart is a real S&P 500 stock. The other is a random walk with the '
               'same average return and volatility. Ten rounds. Can you beat a coin?',
               mode_switch('mode'),
               html.Button('NEW GAME', id='new', className='ghost',
                           title='Practice: ten fresh pairs. Daily: start today\'s over.')),
        html.Div(id='error', className='error'),
        html.Div([html.Div(id='dots', className='dots'),
                  html.Div(id='tiles', className='tiles')], className='status'),
        html.P(id='prompt', className='prompt'),
        html.Div([
            html.Div([dcc.Graph(id='chart-left', config={'displayModeBar': False}),
                      html.Button('THIS ONE IS REAL', id='pick-left', className='primary')],
                     id='panel-left', className='panel'),
            html.Div([dcc.Graph(id='chart-right', config={'displayModeBar': False}),
                      html.Button('THIS ONE IS REAL', id='pick-right', className='primary')],
                     id='panel-right', className='panel'),
        ], className='pair'),
        html.Div([html.P(id='feedback'),
                  html.Button('NEXT PAIR →', id='next', className='primary')],
                 id='feedback-row', className='feedback', style={'display': 'none'}),
        html.Div(id='verdict'),
        share_panel(),
        html.P('Real paths are split- and dividend-adjusted daily closes of stocks in the '
               'S&P 500 today (so survivors only), rescaled to start at 100. The stock and '
               'dates are shown after each guess. Today\'s puzzle is the same ten pairs for '
               'everyone. Not investment advice.', className='footer-note'),
    ], className='shell')

    @app.callback(
        Output('error', 'children'), Output('dots', 'children'), Output('tiles', 'children'),
        Output('prompt', 'children'), Output('chart-left', 'figure'),
        Output('chart-right', 'figure'), Output('panel-left', 'className'),
        Output('panel-right', 'className'), Output('pick-left', 'disabled'),
        Output('pick-right', 'disabled'), Output('feedback', 'children'),
        Output('feedback-row', 'style'), Output('next', 'style'),
        Output('verdict', 'children'),
        *[Output(component, prop) for component, prop in SHARE_OUTPUTS],
        Input('new', 'n_clicks'), Input('mode', 'value'), Input('pick-left', 'n_clicks'),
        Input('pick-right', 'n_clicks'), Input('next', 'n_clicks'), State('sid', 'data'))
    def interact(new, mode, left, right, advance, sid):
        trigger = ctx.triggered_id
        mode = mode if mode in ('daily', 'practice') else 'daily'
        error = ''
        slot = sessions.get(sid)
        try:
            if trigger in ('new', 'mode') or slot is None:
                slot = sessions.put(sid, new_entry(mode))
        except ValueError as exc:
            blank = path_figure([100.0])
            return (str(exc), [], [], '', blank, blank, 'panel', 'panel', True, True, '',
                    {'display': 'none'}, {}, None, *share_outputs(GAME, None))
        with slot.lock:
            entry = slot.value
            game = entry['game']
            try:
                if trigger in ('pick-left', 'pick-right'):
                    game.guess(trigger.split('-')[1])
                    if not entry['started']:
                        entry['started'] = True
                        analytics.record(GAME, 'start', mode=entry['mode'])
                elif trigger == 'next':
                    game.advance()
            except ValueError as exc:
                error = str(exc)
            outputs = render(game, entry, error)
        slot.touch()
        return outputs

    def render(game, entry, error):
        score = game.score()
        tiles = [tile('SCORE', f"{score['correct']}/{score['answered']}",
                      'up' if score['answered'] and score['correct'] * 2 > score['answered']
                      else ''),
                 tile('ROUND', f'{shown_round(game)}/{game.rounds}'),
                 tile('A COIN DOES THIS WELL', chance(score['coin_probability'])
                      if score['answered'] else '—', 'warn')]
        answered = game.awaiting_next or game.finished
        if answered:
            result = game.results[-1]
            pair = game.last_pair()
            real_title = f"REAL · {result['symbol']} · {result['start']} → {result['end']}"
            figures, classes = [], []
            for side, path in (('left', pair.left), ('right', pair.right)):
                real = side == pair.real_side
                figures.append(path_figure(path, UP if real else DIM,
                                           real_title if real else 'RANDOM WALK'))
                classes.append('panel is-real' if real else 'panel is-fake')
            verdict_word = 'Right.' if result['correct'] else 'Wrong.'
            feedback = [html.B(verdict_word, className='up-text' if result['correct']
                               else 'down-text'),
                        f" The real one was {result['symbol']} over {result['days']} trading "
                        f"days, {result['start']} to {result['end']}."]
            prompt = [html.B(f"Round {result['round']} of {game.rounds}"), ' answered.']
        else:
            current = game.current()
            figures = [path_figure(current['left'], title='A'),
                       path_figure(current['right'], title='B')]
            classes = ['panel', 'panel']
            feedback = ''
            prompt = [html.B(f"Round {current['round']} of {current['rounds']}"),
                      f" · {current['days']} trading days. One of these is a real stock. "
                      'Which one?']

        verdict, shared = None, share_outputs(GAME, None)
        if game.finished:
            verdict = verdict_panel(game)
            shared = share_outputs(GAME, share_text(game, entry['mode'], entry['day']))
            if not entry['completed']:
                entry['completed'] = True
                analytics.record(GAME, 'complete', mode=entry['mode'],
                                 correct=score['correct'], rounds=score['rounds'])
        feedback_style = {'display': 'flex'} if answered else {'display': 'none'}
        # After the last guess there is no next pair; the verdict appears below.
        next_style = {'display': 'none'} if game.finished else {}
        return (error, dots(game), tiles, prompt, figures[0], figures[1], classes[0],
                classes[1], answered, answered, feedback, feedback_style,
                next_style, verdict, *shared)

    register_share_tracking(app, GAME)
    return app


def main():
    parser = argparse.ArgumentParser(description='Real or Random?')
    parser.add_argument('--port', type=int, default=8093)
    parser.add_argument('--host', default='127.0.0.1')
    arguments = parser.parse_args()
    create_app().run(host=arguments.host, port=arguments.port, debug=False)


if __name__ == '__main__':
    main()
