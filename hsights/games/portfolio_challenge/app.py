"""Local single-process Dash application with server-owned game sessions.

The browser receives only the current summary and already-played history. Future
market observations stay in server memory and are never serialized.
"""
import argparse
import secrets
from math import ceil, floor
from pathlib import Path
import sys
from threading import RLock
from time import monotonic
from uuid import uuid4

if __package__ in (None, ''):
    # Running this file directly puts only its own directory on sys.path.
    # Add the repository root so the absolute imports below resolve.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
import plotly.graph_objects as go

from hsights.config import HOST, PORT, SESSION_LIMIT, SESSION_TTL_SECONDS
from hsights.games.portfolio_challenge.data import create_round
from hsights.games.portfolio_challenge.engine import Rules, minimum_risk

INK = '#e8eefc'
MUTED = '#7d8db1'
DIM = '#97a6c5'
GRID = '#1c2741'
ZERO = '#2a3755'
ACCENT = '#4b7bff'
UP = '#22d39a'
DOWN = '#ff5670'
WARN = '#ffb443'
MONO = 'ui-monospace, SF Mono, Menlo, Consolas, monospace'

# The risk gauge is drawn on a scale of 1.35x the ceiling, so the ceiling tick
# sits at 1/1.35 = 74.07% of the bar. Keep this in sync with .gauge-mark in CSS.
GAUGE_SCALE = 1.35

TOLERANCE = 1e-9

STEPS = (('01', 'PICK YOUR MARKET'),
         ('02', 'BUILD YOUR MIX'),
         ('03', 'SURVIVE TO THE DEADLINE'))


# --------------------------------------------------------------- presentation

def icon(name, large=False):
    return html.Img(src=f'/assets/icons/{name}.svg',
                    className='icon icon-lg' if large else 'icon', alt='')


def table(headers, rows, empty='Draw a round to populate this panel.'):
    if not rows:
        return html.P(empty, className='empty')
    return html.Table([html.Thead(html.Tr([html.Th(x) for x in headers])),
                       html.Tbody([html.Tr([html.Td(x) for x in row]) for row in rows])])


def segmented(component_id, options, value, class_name='segmented'):
    return dcc.RadioItems(id=component_id, options=options, value=value,
                          className=class_name, persistence=False)


def field(label, control, hint=None):
    children = [html.Span(label), control]
    if hint:
        children.append(html.Small(hint, className='field-hint'))
    return html.Label(children, className='field')


def step_strip(stage=0):
    return [html.Span([html.B(number), label],
                      className=('step is-active' if index == stage else
                                 'step is-done' if index < stage else 'step'))
            for index, (number, label) in enumerate(STEPS)]


def hud_tiles(value='—', total_return='—', risk='—', fees='—',
              return_tone='', risk_tone=''):
    items = [('wallet', 'PORTFOLIO VALUE', value, ''),
             ('chart', 'NET RETURN', total_return, return_tone),
             ('shield', 'ESTIMATED RISK', risk, risk_tone),
             ('coins', 'TRADING COSTS', fees, '')]
    return [html.Div([html.Div([icon(symbol), html.Small(label)], className='tile-head'),
                      html.Strong(text, className=f'tile-value {tone}'.strip())],
                     className='tile')
            for symbol, label, text, tone in items]


def graph(snapshot, rules):
    """Two stacked panels: net return with target/floor bands, risk with ceiling."""
    from plotly.subplots import make_subplots

    rows = snapshot['history'] or [dict(day=0, total_return=0.0, risk=0.0)]
    days = [row['day'] for row in rows]
    returns = [row['total_return'] * 100 for row in rows]
    risks = [row['risk'] * 100 for row in rows]

    # Keep scales stable between ticks; expand only in five-point increments.
    return_min = 5 * floor(min([rules.floor * 100 - 2, 0] + returns) / 5)
    return_max = 5 * ceil(max([rules.target * 100 + 2, 5] + returns) / 5)
    risk_max = 5 * ceil(max([rules.risk_cap * 100 + 2, 5] + risks) / 5)

    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.12,
                           row_heights=[.63, .37],
                           subplot_titles=('NET RETURN AFTER COSTS',
                                           'ESTIMATED ANNUALISED VOLATILITY'))

    latest = returns[-1]
    trend = UP if latest >= 0 else DOWN
    shade = 'rgba(34,211,154,.13)' if latest >= 0 else 'rgba(255,86,112,.13)'

    figure.add_hrect(y0=return_min, y1=rules.floor * 100, row=1, col=1, layer='below',
                     fillcolor='rgba(255,86,112,.10)', line_width=0)
    figure.add_hrect(y0=rules.target * 100, y1=return_max, row=1, col=1, layer='below',
                     fillcolor='rgba(34,211,154,.09)', line_width=0)
    figure.add_hrect(y0=rules.risk_cap * 100, y1=risk_max, row=2, col=1, layer='below',
                     fillcolor='rgba(255,86,112,.10)', line_width=0)

    figure.add_trace(go.Scatter(x=days, y=returns, mode='lines', fill='tozeroy',
                                fillcolor=shade, line=dict(color=trend, width=2.4),
                                hovertemplate='%{y:.2f}%<extra>net return</extra>'),
                     row=1, col=1)
    figure.add_trace(go.Scatter(x=days, y=risks, mode='lines',
                                line=dict(color=WARN, width=2.4),
                                hovertemplate='%{y:.2f}%<extra>risk</extra>'),
                     row=2, col=1)
    figure.add_trace(go.Scatter(x=days[-1:], y=returns[-1:], mode='markers',
                                marker=dict(color=trend, size=9,
                                            line=dict(color='#080b14', width=2)),
                                hoverinfo='skip'), row=1, col=1)
    figure.add_trace(go.Scatter(x=days[-1:], y=risks[-1:], mode='markers',
                                marker=dict(color=WARN, size=9,
                                            line=dict(color='#080b14', width=2)),
                                hoverinfo='skip'), row=2, col=1)

    for value, row, label, colour in [(rules.target, 1, 'TARGET', UP),
                                      (rules.floor, 1, 'FLOOR', DOWN),
                                      (rules.risk_cap, 2, 'CEILING', DOWN)]:
        figure.add_hline(y=value * 100, row=row, col=1, line_dash='dot', line_width=1.4,
                         line_color=colour, annotation_text=label,
                         annotation_position='top left',
                         annotation_font=dict(size=9, color=colour, family=MONO))

    figure.update_layout(autosize=True, showlegend=False, template='plotly_dark',
                         margin=dict(l=48, r=16, t=26, b=26), hovermode='x unified',
                         paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                         font=dict(family=MONO, color=MUTED, size=10.5),
                         hoverlabel=dict(bgcolor='#10172a', bordercolor=GRID,
                                         font=dict(color=INK, family=MONO, size=11)))
    # The first two annotations are the subplot titles created above.
    for annotation in figure.layout.annotations[:2]:
        annotation.update(x=0, xanchor='left',
                          font=dict(size=10, color=DIM, family=MONO))
    figure.update_xaxes(range=[0, rules.horizon], autorange=False, gridcolor=GRID,
                        zerolinecolor=ZERO, showspikes=True, spikecolor=ZERO,
                        spikethickness=1, spikedash='dot', spikemode='across')
    figure.update_xaxes(title_text='TRADING DAY', row=2, col=1,
                        title_font=dict(size=9.5, color=DIM, family=MONO))
    figure.update_yaxes(ticksuffix='%', autorange=False, automargin=False,
                        gridcolor=GRID, zerolinecolor=ZERO)
    figure.update_yaxes(range=[return_min, return_max], row=1, col=1)
    figure.update_yaxes(range=[0, risk_max], row=2, col=1)
    return figure


def to_percent(raw):
    """Round three weights to whole percents that total exactly 100."""
    scaled = [int(round(value)) for value in raw]
    largest = max(range(3), key=lambda index: scaled[index])
    scaled[largest] += 100 - sum(scaled)
    return tuple(min(100, max(0, value)) for value in scaled)


# ------------------------------------------------------------------ callbacks

OUTPUT_SPEC = (
    ('error', 'children'), ('round-chip', 'children'), ('clock-label', 'children'),
    ('progress-fill', 'style'), ('status-pill', 'children'), ('status-pill', 'className'),
    ('assisted', 'children'), ('steps', 'children'), ('reason', 'children'),
    ('hud', 'children'), ('chart', 'figure'),
    ('statistics', 'children'), ('covariance', 'children'), ('trades', 'children'),
    ('clock', 'disabled'), ('game-revision', 'data'), ('toggle', 'children'),
    ('toggle', 'disabled'), ('step', 'disabled'), ('rewind', 'disabled'),
    ('restart-button', 'disabled'),
    ('w0', 'value'), ('w1', 'value'), ('w2', 'value'),
    ('tick0', 'children'), ('tick1', 'children'), ('tick2', 'children'),
    ('seed', 'value'), ('setup', 'className'),
)


def create_app(round_factory=create_round):
    app = Dash(__name__, assets_folder=str(Path(__file__).with_name('assets')),
               update_title=None)
    app.title = 'Portfolio Lab'
    sessions = {}
    lock = RLock()

    def setup_sheet():
        return html.Div(html.Div([
            html.Div([
                html.Div([html.P('THE HISTORICAL MARKET CHALLENGE', className='eyebrow'),
                          html.H2('Three assets. Your timeline. Your decisions.')]),
                html.Button('CLOSE', id='close-setup', className='ghost',
                            title='Escape - close this panel and return to the board.'),
            ], className='sheet-head'),
            html.P('Replay real markets one trading day per tick. Stay above the loss '
                   'floor and below the risk ceiling, and meet the return target at the '
                   'deadline. Estimated risk is the annualised volatility of your current '
                   'allocation, not a maximum possible loss.', className='lede'),
            html.Div([
                field('HISTORICAL START', dcc.Input(id='start', value='2022-01-03', type='text'),
                      'Any past date with a year of prior history.'),
                field('RANDOM SEED', dcc.Input(id='seed', value=42, type='number', step=1),
                      'Same seed and settings reproduce the same draw.'),
                field('STOCK UNIVERSE', segmented('universe', [
                    {'label': 'S&P 500', 'value': 'sp500'},
                    {'label': 'CURATED 12', 'value': 'curated'}], 'sp500'),
                    'Current constituents, so results carry survivorship bias.'),
                field('PLAY UNTIL', segmented('period-mode', [
                    {'label': 'DURATION', 'value': 'duration'},
                    {'label': 'END DATE', 'value': 'end'}], 'duration'),
                    'Only the mode you pick here is used.'),
                field('DURATION (TRADING DAYS)',
                      dcc.Input(id='duration', value=252, type='number', min=1, max=2520, step=1),
                      '252 is roughly one trading year.'),
                field('HISTORICAL END DATE', dcc.Input(id='end-date', value='2023-01-03', type='text'),
                      'Used only when PLAY UNTIL is set to END DATE.'),
                field('RETURN TARGET (%)', dcc.Input(id='target', value=10, type='number'),
                      'Total for the whole period; it is not annualised.'),
                field('LOSS FLOOR (%)', dcc.Input(id='floor', value=-8, type='number'),
                      'Falling below this ends the round immediately.'),
                field('RISK CEILING (% ANNUALISED)', dcc.Input(id='cap', value=20, type='number'),
                      'Going above this ends the round immediately.'),
            ], className='field-grid'),
            html.Div([
                html.Button([icon('shuffle', True), 'DRAW 3 NEW ASSETS'], id='randomize',
                            className='primary',
                            title='Fresh seed, excludes your three current stocks.'),
                html.Button('LOAD ROUND WITH SEED', id='new',
                            title='Use the seed exactly as entered, with no exclusions.'),
            ], className='sheet-actions'),
            html.P('$100,000 capital, 252-day rolling covariance, 0.10% cost per dollar '
                   'bought or sold. All capital sits in three equities: there is no cash '
                   'allocation, shorting or leverage, so a market shock can make risk '
                   'unavoidable. Rewinding or restarting a round marks the run ASSISTED.',
                   className='disclosure'),
        ], className='sheet'), id='setup', className='overlay is-open')

    def controls():
        return html.Section([
            html.Div([
                html.P('TIME MACHINE', className='group-label'),
                html.Div([
                    html.Button([icon('rewind', True), 'REWIND'], id='rewind',
                                className='btn-xl', disabled=True,
                                title='Step back to an earlier day and resume from there. '
                                      'Marks the run as ASSISTED.'),
                    segmented('rewind-days', [{'label': '1D', 'value': 1},
                                              {'label': '5D', 'value': 5},
                                              {'label': '20D', 'value': 20}], 5,
                              'segmented compact'),
                    dcc.ConfirmDialogProvider(
                        html.Button([icon('restart', True), 'RESTART'], id='restart-button',
                                    className='btn-xl', disabled=True,
                                    title='Replay the same assets and dates from day 0. '
                                          'Marks the run as ASSISTED.'),
                        id='restart',
                        message='Restart this round from day 0?\n\n'
                                'Same assets, same dates, capital back to $100,000. '
                                'The run will be marked ASSISTED.'),
                ], className='group-row'),
                html.P('Undo days, or replay the whole round. Either one flags the run '
                       'ASSISTED, because you have already seen those prices.',
                       className='group-hint'),
            ], className='control-group'),
            html.Div([
                html.P('PLAYBACK', className='group-label'),
                html.Div([
                    html.Button([icon('play', True), 'PLAY'], id='toggle',
                                className='btn-xl primary play', disabled=True,
                                title='Space - start or pause the market clock.'),
                    html.Button([icon('step', True), 'STEP 1 DAY'], id='step',
                                className='btn-xl', disabled=True,
                                title='Right arrow - advance exactly one trading day while paused.'),
                ], className='group-row'),
                html.P('One tick is one trading day. Pause any time to rebalance; a '
                       'breach ends the round the moment it happens.',
                       className='group-hint'),
            ], className='control-group'),
            html.Div([
                html.P('SPEED', className='group-label'),
                segmented('speed', [{'label': '1X', 'value': 1000},
                                    {'label': '2X', 'value': 500},
                                    {'label': '4X', 'value': 250}], 1000),
                html.P([html.Kbd('Space'), ' play · ', html.Kbd('→'), ' step · ',
                        html.Kbd('S'), ' setup · ', html.Kbd('Esc'), ' close'],
                       className='group-hint'),
            ], className='control-group speed-group'),
        ], className='controls')

    def dock():
        return html.Footer([
            html.Div([
                html.P('ALLOCATION', className='dock-title'),
                *[html.Div([
                    html.Span(f'ASSET {index + 1}', id=f'tick{index}', className='alloc-ticker'),
                    dcc.Slider(id=f'w{index}', min=0, max=100, step=1,
                               value=[34, 33, 33][index], marks=None, updatemode='drag'),
                    html.Span('—', id=f'w{index}-out', className='alloc-value'),
                ], className='alloc-row',
                    title='Drag to set this asset\'s share of the portfolio.')
                  for index in range(3)],
                html.Div([
                    html.Button('EQUAL', id='preset-equal', title='One third each.'),
                    html.Button('MIN RISK', id='preset-min',
                                title='Lowest-volatility mix from the 252-day covariance.'),
                    html.Button('MOMENTUM', id='preset-mom',
                                title='Tilt toward positive trailing returns. Not a forecast.'),
                    html.Button('NORMALISE', id='preset-norm',
                                title='Rescale your current numbers to total 100%.'),
                ], className='preset-row'),
                html.P('The three weights must total 100%. Presets are starting points, '
                       'not advice.', className='group-hint'),
            ]),
            html.Div([
                html.Div([html.P('PROJECTED RISK', className='dock-title'),
                          html.Span('—', id='remaining', className='remaining bad')],
                         className='gauge-head'),
                html.Div(html.Span('—', id='gauge-text', className='gauge-text')),
                html.Div([html.Div(id='gauge-fill', className='gauge-fill',
                                   style={'width': '0%', 'background': UP}),
                          html.Div(className='gauge-mark')], className='gauge',
                         title='Annualised volatility of the mix you are proposing, '
                               'against the round\'s ceiling.'),
                html.Div([html.Span('0%'), html.Span('CEILING'), html.Span('MAX')],
                         className='gauge-legend'),
                html.P('—', id='cost-text', className='cost-text'),
                html.Button('CONFIRM ALLOCATION', id='allocate',
                            className='primary btn-xl confirm', disabled=True,
                            title='Execute this mix at today\'s close, then press PLAY.'),
            ], className='gauge-column'),
        ], className='dock')

    def layout():
        return html.Div([
            dcc.Store(id='session', data=str(uuid4())),
            dcc.Store(id='game-revision', data=None),
            dcc.Interval(id='clock', interval=1000, disabled=True),
            html.Header([
                html.Div([icon('chart'), html.Span('PORTFOLIO'),
                          html.Span('LAB', className='wordmark-alt')], className='wordmark'),
                html.Div('NO ROUND LOADED', id='round-chip', className='round-chip'),
                html.Div([html.Div('DAY 0 / 0', id='clock-label', className='clock-label'),
                          html.Div(html.Div(id='progress-fill', className='progress-fill',
                                            style={'width': '0%'}), className='progress')],
                         className='progress-wrap'),
                html.Div('', id='assisted', className='badge',
                         title='This run used rewind or restart, so it is not comparable '
                               'to a clean run.'),
                html.Div('SETUP', id='status-pill', className='pill'),
                html.Button([icon('shuffle'), 'SETUP'], id='open-setup', className='ghost',
                            title='S - open the round settings panel.'),
            ], className='topbar'),
            html.Div([
                html.Div(step_strip(0), id='steps', className='steps'),
                html.Div('Draw a round to begin. No future prices are sent to this page.',
                         id='reason', className='reason'),
            ], className='guide'),
            html.Div(id='error', role='alert', className='error'),
            html.Div(hud_tiles(), id='hud', className='hud'),
            html.Div([
                html.Section(dcc.Graph(id='chart', figure=go.Figure(), responsive=True,
                                       config={'displayModeBar': False}),
                             className='chart-panel'),
                html.Aside([
                    segmented('intel-tab', [{'label': 'HOLDINGS', 'value': 'holdings'},
                                            {'label': 'MATRIX', 'value': 'matrix'},
                                            {'label': 'LOG', 'value': 'log'}],
                              'holdings', 'segmented tabs'),
                    html.Div(html.Div(id='statistics'), id='panel-holdings',
                             className='panel is-active'),
                    html.Div(html.Div(id='covariance'), id='panel-matrix', className='panel'),
                    html.Div(html.Div(id='trades'), id='panel-log', className='panel'),
                ], className='intel'),
            ], className='board'),
            controls(),
            dock(),
            setup_sheet(),
        ], className='shell')

    app.layout = layout

    @app.callback(*[Output(component, prop) for component, prop in OUTPUT_SPEC],
                  Input('new', 'n_clicks'), Input('randomize', 'n_clicks'),
                  Input('allocate', 'n_clicks'), Input('toggle', 'n_clicks'),
                  Input('step', 'n_clicks'), Input('clock', 'n_intervals'),
                  Input('rewind', 'n_clicks'), Input('restart', 'submit_n_clicks'),
                  Input('open-setup', 'n_clicks'), Input('close-setup', 'n_clicks'),
                  State('session', 'data'), State('start', 'value'), State('seed', 'value'),
                  State('target', 'value'), State('floor', 'value'), State('cap', 'value'),
                  State('universe', 'value'), State('period-mode', 'value'),
                  State('duration', 'value'), State('end-date', 'value'),
                  State('rewind-days', 'value'),
                  *[State(f'w{index}', 'value') for index in range(3)],
                  prevent_initial_call=True)
    def interact(new, randomize, allocate, toggle, step, tick, rewind, restart,
                 open_setup, close_setup, sid, start, seed, target, floor_pct, cap,
                 universe, period_mode, duration, end_date, rewind_days, *weights):
        trigger = ctx.triggered_id
        out = {key: no_update for key in OUTPUT_SPEC}
        out[('error', 'children')] = ''
        with lock:
            now = monotonic()
            for stale in [key for key, value in sessions.items()
                          if now - value['touched'] > SESSION_TTL_SECONDS]:
                del sessions[stale]
            entry = sessions.get(sid)
            try:
                if trigger == 'open-setup':
                    if entry and entry['game'].status == 'running':
                        entry['game'].toggle()
                    out[('setup', 'className')] = 'overlay is-open'
                elif trigger == 'close-setup':
                    out[('setup', 'className')] = 'overlay'
                elif trigger in ('new', 'randomize'):
                    if trigger == 'randomize':
                        seed = secrets.randbelow(2 ** 31)
                    if seed is None or int(seed) != seed:
                        raise ValueError('Seed must be a whole number.')
                    if period_mode == 'duration':
                        if duration is None or int(duration) != duration or not 1 <= duration <= 2520:
                            raise ValueError('Duration must be 1-2520 whole trading days.')
                        horizon = int(duration)
                    elif period_mode == 'end':
                        if not end_date:
                            raise ValueError('Enter a historical end date.')
                        horizon = 252
                    else:
                        raise ValueError('Choose duration or end date.')
                    rules = Rules(horizon=horizon, target=float(target) / 100,
                                  floor=float(floor_pct) / 100, risk_cap=float(cap) / 100)
                    excluded = tuple(entry['game'].prices.columns) if entry and trigger == 'randomize' else ()
                    game = round_factory(start, int(seed), rules, universe=universe,
                                         end_date=end_date if period_mode == 'end' else None,
                                         exclude=excluded)
                    out[('seed', 'value')] = int(seed)
                    if len(sessions) >= SESSION_LIMIT and sid not in sessions:
                        # Evict finished rounds before live ones: a paused game goes
                        # stale while the player thinks, and must not be the first
                        # thing dropped.
                        def eviction_key(key):
                            status = sessions[key]['game'].status
                            return (status in ('setup', 'paused', 'running'),
                                    sessions[key]['touched'])
                        del sessions[min(sessions, key=eviction_key)]
                    entry = dict(game=game, touched=now, tick=tick or 0)
                    sessions[sid] = entry
                    suggested = to_percent([weight * 100 for weight in game.suggested])
                    for index in range(3):
                        out[(f'w{index}', 'value')] = suggested[index]
                    out[('setup', 'className')] = 'overlay'
                else:
                    if not entry:
                        raise ValueError('Draw a round first. Sessions expire after two hours of inactivity.')
                    game = entry['game']
                    if trigger == 'allocate':
                        game.allocate([float(value or 0) / 100 for value in weights])
                    elif trigger == 'toggle':
                        game.toggle()
                    elif trigger == 'rewind':
                        game.rewind(int(rewind_days or 5))
                    elif trigger == 'restart':
                        game.restart()
                        suggested = to_percent([weight * 100 for weight in game.suggested])
                        for index in range(3):
                            out[(f'w{index}', 'value')] = suggested[index]
                    elif trigger == 'step':
                        if game.status != 'paused':
                            raise ValueError('Pause before stepping.')
                        game.toggle()
                        game.step()
                        if game.status == 'running':
                            game.toggle()
                    elif trigger == 'clock' and (tick or 0) > entry['tick']:
                        game.step()
                    entry['tick'] = max(entry['tick'], tick or 0)
                if entry:
                    entry['touched'] = now
            except (ValueError, TypeError, OverflowError, OSError) as exc:
                out[('error', 'children')] = str(exc)

            out[('game-revision', 'data')] = str(uuid4())
            if not entry:
                out[('clock', 'disabled')] = True
                return [out[key] for key in OUTPUT_SPEC]

            game = entry['game']
            snapshot, rules = game.snapshot(), game.rules
            status = snapshot['status']
            day, horizon = snapshot['day'], rules.horizon
            total_return, risk = snapshot['total_return'], snapshot['risk']

            out[('round-chip', 'children')] = [
                html.Span([html.B(ticker), html.Span(f'{weight:.0%}')], className='asset-chip')
                for ticker, weight in zip(snapshot['tickers'], snapshot['weights'])]
            out[('clock-label', 'children')] = (
                f"DAY {day} / {horizon} · {snapshot['date']} · "
                f"ENDS {game.prices.index[game.start + horizon].date()}")
            out[('progress-fill', 'style')] = {'width': f'{min(100, day / max(horizon, 1) * 100):.1f}%'}
            out[('status-pill', 'children')] = status.upper()
            out[('status-pill', 'className')] = f'pill {status}'
            out[('assisted', 'children')] = 'ASSISTED' if snapshot['assisted'] else ''
            out[('steps', 'children')] = step_strip(1 if status == 'setup' else 2)

            reason = snapshot['reason']
            if status in ('won', 'lost'):
                reason += f' Final score: {total_return:.2%} net return.'
                reason += ' Rewind or restart to try again.'
            else:
                reason += (f' Target {rules.target:.1%} · floor {rules.floor:.1%} · '
                           f'risk ceiling {rules.risk_cap:.1%}.')
            out[('reason', 'children')] = reason

            ceiling = rules.risk_cap
            out[('hud', 'children')] = hud_tiles(
                f"${snapshot['value']:,.0f}", f'{total_return:+.2%}', f'{risk:.2%}',
                f"${snapshot['fees']:,.2f}",
                'up' if total_return > 0 else ('down' if total_return < 0 else ''),
                'down' if risk > ceiling else ('warn' if risk > ceiling * .8 else 'up'))
            out[('chart', 'figure')] = graph(snapshot, rules)
            out[('statistics', 'children')] = table(
                ['ASSET', 'WEIGHT', 'TRAILING 252D'],
                [[html.Span([html.Span(ticker[:2], className='ticker-icon'), ticker],
                            className='ticker-name'), f'{weight:.2%}', f'{trailing:+.2%}']
                 for ticker, weight, trailing in zip(snapshot['tickers'], snapshot['weights'],
                                                     snapshot['historical_return'])])
            out[('covariance', 'children')] = table(
                ['ASSET'] + snapshot['tickers'],
                [[ticker] + [f'{value:.6f}' for value in row]
                 for ticker, row in zip(snapshot['tickers'], snapshot['covariance'])])
            out[('trades', 'children')] = table(
                ['DATE', 'ALLOCATION', 'COST'],
                [[trade['date'], ' / '.join(f'{weight:.0%}' for weight in trade['weights']),
                  f"${trade['cost']:,.2f}"] for trade in snapshot['trades']],
                empty='No rebalances yet. Confirm an allocation to start.')
            for index, ticker in enumerate(snapshot['tickers']):
                out[(f'tick{index}', 'children')] = ticker

            running = status == 'running'
            out[('clock', 'disabled')] = not running
            out[('toggle', 'children')] = [icon('pause', True), 'PAUSE'] if running else \
                                          [icon('play', True), 'PLAY']
            out[('toggle', 'disabled')] = status not in ('paused', 'running')
            out[('step', 'disabled')] = status != 'paused'
            out[('rewind', 'disabled')] = not snapshot['can_rewind']
            out[('restart-button', 'disabled')] = False
            return [out[key] for key in OUTPUT_SPEC]

    @app.callback(Output('remaining', 'children'), Output('remaining', 'className'),
                  Output('gauge-fill', 'style'), Output('gauge-text', 'children'),
                  Output('cost-text', 'children'),
                  Output('allocate', 'disabled'),
                  *[Output(f'w{index}-out', 'children') for index in range(3)],
                  *[Input(f'w{index}', 'value') for index in range(3)],
                  Input('game-revision', 'data'),
                  State('session', 'data'), prevent_initial_call=True)
    def live_allocation(*arguments):
        *values, revision, sid = arguments
        values = [float(value or 0) for value in values]
        readouts = [f'{value:.0f}%' for value in values]
        remaining = 100 - sum(values)
        balanced = abs(remaining) <= TOLERANCE
        badge = 'BALANCED' if balanced else f'{remaining:+.0f}%'
        badge_class = 'remaining ok' if balanced else 'remaining bad'
        blank = ({'width': '0%', 'background': DIM}, '—',
                 'Weights must total 100% before you can confirm.', True, *readouts)
        with lock:
            entry = sessions.get(sid)
            if entry is None or not balanced:
                return (badge, badge_class, *blank)
            game = entry['game']
            try:
                _, risk, cost, net = game.preview([value / 100 for value in values])
            except (ValueError, TypeError):
                return (badge, badge_class, *blank)
            ceiling = game.rules.risk_cap
            over = risk > ceiling + 1e-10
            colour = DOWN if over else (WARN if risk > ceiling * .8 else UP)
            width = min(100.0, risk / (ceiling * GAUGE_SCALE) * 100) if ceiling > 0 else 0.0
            text = f'{risk:.2%} vs {ceiling:.0%} ceiling' + (' · OVER' if over else '')
            cost_text = (f'Fee ${cost:,.2f} · net ${net:,.0f} after costs.' if not over else
                         'Above the ceiling. Lower the riskiest weight to continue.')
            blocked = over or game.status not in ('setup', 'paused')
            return (badge, badge_class, {'width': f'{width:.1f}%', 'background': colour},
                    text, cost_text, blocked, *readouts)

    @app.callback(*[Output(f'w{index}', 'value', allow_duplicate=True) for index in range(3)],
                  Input('preset-equal', 'n_clicks'), Input('preset-min', 'n_clicks'),
                  Input('preset-mom', 'n_clicks'), Input('preset-norm', 'n_clicks'),
                  State('session', 'data'),
                  *[State(f'w{index}', 'value') for index in range(3)],
                  prevent_initial_call=True)
    def apply_preset(equal, minimum, momentum, normalise, sid, *weights):
        trigger = ctx.triggered_id
        values = [float(value or 0) for value in weights]
        if trigger == 'preset-equal':
            return 34, 33, 33
        if trigger == 'preset-norm':
            total = sum(values)
            return to_percent([value / total * 100 for value in values]) if total > 0 else (34, 33, 33)
        with lock:
            entry = sessions.get(sid)
            if entry is None:
                return no_update, no_update, no_update
            game = entry['game']
            if trigger == 'preset-min':
                weights, _ = minimum_risk(game.covariance())
                return to_percent([weight * 100 for weight in weights])
            # Momentum tilt: weight by positive trailing return only; never a forecast.
            trailing = [max(value, 0.0) + 1e-6 for value in game.snapshot()['historical_return']]
            total = sum(trailing)
            return to_percent([value / total * 100 for value in trailing])

    @app.callback(Output('panel-holdings', 'className'), Output('panel-matrix', 'className'),
                  Output('panel-log', 'className'), Input('intel-tab', 'value'))
    def select_panel(value):
        return tuple('panel is-active' if key == value else 'panel'
                     for key in ('holdings', 'matrix', 'log'))

    @app.callback(Output('clock', 'interval'), Input('speed', 'value'),
                  prevent_initial_call=True)
    def set_speed(value):
        return int(value or 1000)

    return app


def main():
    """Run this game alone. The deployed service uses hsights.server instead."""
    parser = argparse.ArgumentParser(description='Portfolio Challenge')
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--host', default=HOST,
                        help='Defaults to 0.0.0.0, which Render requires. Use '
                             '127.0.0.1 to keep a local run off your network.')
    arguments = parser.parse_args()
    create_app().run(host=arguments.host, port=arguments.port, debug=False)


if __name__ == '__main__':
    main()
