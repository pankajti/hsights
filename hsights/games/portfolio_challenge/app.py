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
from hsights.home import PLAY_PATH, home_page
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

#: The single "what do I do next" button in the header, keyed by game status.
#: It dispatches to the same engine calls as the dock and transport buttons, so
#: every guard still applies; it exists purely so the next move is always visible
#: without scanning to the bottom of the board.
PRIMARY_ACTION = {
    'setup': ('play', 'CONFIRM ALLOCATION', 'go',
              'Commit this mix at today\'s close, then press PLAY.'),
    'paused': ('play', 'PLAY', 'go', 'Space - start the market clock.'),
    'running': ('pause', 'PAUSE', 'hold', 'Space - freeze the market.'),
    'won': ('restart', 'PLAY AGAIN', 'again', 'Replay the same round from day 0.'),
    'lost': ('restart', 'PLAY AGAIN', 'again', 'Replay the same round from day 0.'),
}
NO_ROUND_ACTION = ('shuffle', 'DRAW A ROUND', 'go',
                   'Open settings and draw three assets.')


def primary_action(status=None):
    """(children, className, title) for the header's primary button."""
    symbol, label, variant, hint = (PRIMARY_ACTION.get(status, NO_ROUND_ACTION)
                                    if status else NO_ROUND_ACTION)
    return [icon(symbol, True), label], f'primary-action {variant}', hint


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


def worst_drawdown(values):
    """Deepest peak-to-trough fall in a value series, as a negative fraction."""
    if not values:
        return 0.0
    peak, worst = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return worst


def result_body(snapshot, rules):
    """Contents of the end-of-round modal."""
    won = snapshot['status'] == 'won'
    total_return = snapshot['total_return']
    history = snapshot['history']
    values = [row['value'] for row in history] or [rules.capital]
    returns = [row['total_return'] for row in history] or [0.0]

    if won:
        eyebrow, title = 'CHALLENGE COMPLETE', 'Congratulations'
        lede = ('You cleared the return target and never breached the loss floor '
                'or the risk ceiling.')
    else:
        eyebrow, title = 'ROUND OVER', 'Sorry, not this time'
        lede = ('Every round is one draw from a very noisy process. Rewind a few '
                'days, or restart and try a different mix.')

    tone = 'up' if total_return > 0 else ('down' if total_return < 0 else '')
    stats = [
        ('FINAL VALUE', f"${snapshot['value']:,.0f}", ''),
        ('DAYS SURVIVED', f"{snapshot['day']} / {rules.horizon}", ''),
        ('PEAK RETURN', f'{max(returns):+.2%}', ''),
        ('MAX DRAWDOWN', f'{worst_drawdown(values):.2%}', ''),
        ('TRADING COSTS', f"${snapshot['fees']:,.2f}", ''),
        ('REBALANCES', f"{len(snapshot['trades'])}", ''),
    ]

    children = [
        html.P(eyebrow, className='result-eyebrow'),
        html.H2(title, className='result-title'),
        html.Div([html.Strong(f'{total_return:+.2%}', className=f'result-score {tone}'),
                  html.Span('NET RETURN AFTER COSTS', className='result-score-label')],
                 className='result-score-row'),
    ]
    reference = snapshot.get('benchmark_symbol')
    benchmark_return = snapshot.get('benchmark_return')
    if reference and benchmark_return is not None:
        excess = total_return - benchmark_return
        verdict = 'BEAT' if excess > 0 else ('MATCHED' if excess == 0 else 'TRAILED')
        children.append(html.P([
            html.Span(f'{verdict} {reference}', className=(
                'verdict up' if excess > 0 else 'verdict down' if excess < 0 else 'verdict')),
            html.Span(f'{reference} buy and hold returned {benchmark_return:+.2%} over the '
                      f'same days, so you were {excess:+.2%} against it.'),
        ], className='result-benchmark'))
    children += [
        html.P(lede, className='result-lede'),
        html.P(snapshot['reason'], className='result-reason'),
        html.Div([html.Div([html.Small(label), html.Strong(value, className=f'stat-value {stat_tone}'.strip())],
                           className='result-stat')
                  for label, value, stat_tone in stats], className='result-stats'),
    ]
    if snapshot['assisted']:
        children.append(html.P(
            'This run used rewind or restart, so it is marked ASSISTED and is not '
            'comparable to a clean run.', className='result-assisted'))
    return html.Div(children, className=f"result-inner {'won' if won else 'lost'}")


def hud_tiles(value='—', total_return='—', risk='—', fees='—',
              return_tone='', risk_tone='', excess=None, reference=None):
    items = [('wallet', 'PORTFOLIO VALUE', value, ''),
             ('chart', 'NET RETURN', total_return, return_tone),
             ('shield', 'ESTIMATED RISK', risk, risk_tone),
             ('coins', 'TRADING COSTS', fees, '')]
    if reference and excess is not None:
        items.append(('briefcase', f'VS {reference}', f'{excess:+.2%}',
                      'up' if excess > 0 else ('down' if excess < 0 else '')))
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
    # Only days already played carry a benchmark value, so the reference line
    # can never reveal a price the player has not reached yet.
    reference = snapshot.get('benchmark_symbol')
    benchmark = [row.get('benchmark_return') for row in rows] if reference else []
    benchmark = ([value * 100 for value in benchmark]
                 if benchmark and all(value is not None for value in benchmark) else [])

    # Keep scales stable between ticks; expand only in five-point increments.
    return_min = 5 * floor(min([rules.floor * 100 - 2, 0] + returns + benchmark) / 5)
    return_max = 5 * ceil(max([rules.target * 100 + 2, 5] + returns + benchmark) / 5)
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

    if benchmark:
        figure.add_trace(go.Scatter(x=days, y=benchmark, mode='lines', name=reference,
                                    line=dict(color=MUTED, width=1.8, dash='dash'),
                                    hovertemplate='%{y:.2f}%'
                                                  f'<extra>{reference} buy and hold</extra>'),
                         row=1, col=1)
    figure.add_trace(go.Scatter(x=days, y=returns, mode='lines', fill='tozeroy',
                                name='YOU', fillcolor=shade,
                                line=dict(color=trend, width=2.4),
                                hovertemplate='%{y:.2f}%<extra>net return</extra>'),
                     row=1, col=1)
    figure.add_trace(go.Scatter(x=days, y=risks, mode='lines', showlegend=False,
                                line=dict(color=WARN, width=2.4),
                                hovertemplate='%{y:.2f}%<extra>risk</extra>'),
                     row=2, col=1)
    figure.add_trace(go.Scatter(x=days[-1:], y=returns[-1:], mode='markers',
                                showlegend=False,
                                marker=dict(color=trend, size=9,
                                            line=dict(color='#080b14', width=2)),
                                hoverinfo='skip'), row=1, col=1)
    figure.add_trace(go.Scatter(x=days[-1:], y=risks[-1:], mode='markers',
                                showlegend=False,
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

    figure.update_layout(autosize=True, showlegend=bool(benchmark), template='plotly_dark',
                         legend=dict(orientation='h', xanchor='right', x=1,
                                     yanchor='top', y=1.13, bgcolor='rgba(0,0,0,0)',
                                     font=dict(size=9.5, color=MUTED, family=MONO)),
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
    ('result', 'className'), ('result-body', 'children'),
    ('primary', 'children'), ('primary', 'className'), ('primary', 'title'),
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

    def result_sheet():
        """End-of-round modal. The action buttons live in the static layout so
        their ids always exist for the callback; only the body is rendered."""
        return html.Div(html.Div([
            html.Div(id='result-body'),
            html.Div([
                html.Button([icon('restart', True), 'PLAY AGAIN'], id='result-restart',
                            className='btn-xl primary',
                            title='Replay the same assets and dates from day 0.'),
                html.Button([icon('shuffle', True), 'NEW ROUND'], id='result-new',
                            className='btn-xl',
                            title='Open settings and draw different assets.'),
                html.Button('VIEW BOARD', id='close-result', className='btn-xl ghost',
                            title='Escape - dismiss this and inspect the final chart.'),
            ], className='result-actions'),
        ], className='result-card'), id='result', className='overlay result-overlay')

    def layout():
        # Both pages stay mounted and are swapped with a class, rather than the
        # layout being rebuilt per route. That keeps every game component id in
        # the DOM, so the callbacks below need no suppress_callback_exceptions
        # and dcc.Link navigation never tears down a round in progress.
        return html.Div([
            dcc.Location(id='url', refresh=False),
            html.Div(home_page(), id='page-home', className='page is-active'),
            html.Div(game_shell(), id='page-game', className='page'),
        ], className='router')

    def game_shell():
        initial_primary = primary_action()
        return html.Div([
            dcc.Store(id='session', data=str(uuid4())),
            dcc.Store(id='game-revision', data=None),
            dcc.Interval(id='clock', interval=1000, disabled=True),
            html.Header([
                dcc.Link([icon('chart'), html.Span('PORTFOLIO'),
                          html.Span('LAB', className='wordmark-alt'),
                          html.Span('HOME', className='home-hint')],
                         href='/', className='wordmark wordmark-link',
                         title='Back to the Hindsight home page. Your round is kept.'),
                html.Div('NO ROUND LOADED', id='round-chip', className='round-chip'),
                html.Div([html.Div('DAY 0 / 0', id='clock-label', className='clock-label'),
                          html.Div(html.Div(id='progress-fill', className='progress-fill',
                                            style={'width': '0%'}), className='progress')],
                         className='progress-wrap'),
                html.Div('', id='assisted', className='badge',
                         title='This run used rewind or restart, so it is not comparable '
                               'to a clean run.'),
                html.Div('SETUP', id='status-pill', className='pill'),
                html.Button(initial_primary[0], id='primary',
                            className=initial_primary[1], title=initial_primary[2]),
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
            result_sheet(),
        ], className='shell')

    app.layout = layout

    @app.callback(*[Output(component, prop) for component, prop in OUTPUT_SPEC],
                  Input('new', 'n_clicks'), Input('randomize', 'n_clicks'),
                  Input('allocate', 'n_clicks'), Input('toggle', 'n_clicks'),
                  Input('step', 'n_clicks'), Input('clock', 'n_intervals'),
                  Input('rewind', 'n_clicks'), Input('restart', 'submit_n_clicks'),
                  Input('open-setup', 'n_clicks'), Input('close-setup', 'n_clicks'),
                  Input('close-result', 'n_clicks'), Input('result-restart', 'n_clicks'),
                  Input('result-new', 'n_clicks'), Input('primary', 'n_clicks'),
                  State('session', 'data'), State('start', 'value'), State('seed', 'value'),
                  State('target', 'value'), State('floor', 'value'), State('cap', 'value'),
                  State('universe', 'value'), State('period-mode', 'value'),
                  State('duration', 'value'), State('end-date', 'value'),
                  State('rewind-days', 'value'),
                  *[State(f'w{index}', 'value') for index in range(3)],
                  prevent_initial_call=True)
    def interact(new, randomize, allocate, toggle, step, tick, rewind, restart,
                 open_setup, close_setup, close_result, result_restart, result_new,
                 primary, sid, start, seed, target, floor_pct, cap,
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
                elif trigger == 'primary' and not entry:
                    out[('setup', 'className')] = 'overlay is-open'
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
                    elif trigger == 'primary':
                        # Dispatches to the same engine calls as the dock and
                        # transport buttons; it is a shortcut, not a second path.
                        if game.status == 'setup':
                            game.allocate([float(value or 0) / 100 for value in weights])
                        elif game.status in ('paused', 'running'):
                            game.toggle()
                        else:
                            game.restart()
                            entry['result_dismissed'] = False
                            suggested = to_percent([w * 100 for w in game.suggested])
                            for index in range(3):
                                out[(f'w{index}', 'value')] = suggested[index]
                    elif trigger == 'toggle':
                        game.toggle()
                    elif trigger == 'rewind':
                        game.rewind(int(rewind_days or 5))
                    elif trigger in ('restart', 'result-restart'):
                        game.restart()
                        entry['result_dismissed'] = False
                        suggested = to_percent([weight * 100 for weight in game.suggested])
                        for index in range(3):
                            out[(f'w{index}', 'value')] = suggested[index]
                    elif trigger == 'close-result':
                        entry['result_dismissed'] = True
                    elif trigger == 'result-new':
                        entry['result_dismissed'] = True
                        out[('setup', 'className')] = 'overlay is-open'
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
                (out[('primary', 'children')], out[('primary', 'className')],
                 out[('primary', 'title')]) = primary_action()
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
            reference = snapshot.get('benchmark_symbol')
            benchmark_return = snapshot.get('benchmark_return')
            excess = (total_return - benchmark_return) if benchmark_return is not None else None
            out[('hud', 'children')] = hud_tiles(
                f"${snapshot['value']:,.0f}", f'{total_return:+.2%}', f'{risk:.2%}',
                f"${snapshot['fees']:,.2f}",
                'up' if total_return > 0 else ('down' if total_return < 0 else ''),
                'down' if risk > ceiling else ('warn' if risk > ceiling * .8 else 'up'),
                excess=excess, reference=reference)
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

            (out[('primary', 'children')], out[('primary', 'className')],
             out[('primary', 'title')]) = primary_action(status)

            running = status == 'running'
            out[('clock', 'disabled')] = not running
            out[('toggle', 'children')] = [icon('pause', True), 'PAUSE'] if running else \
                                          [icon('play', True), 'PLAY']
            out[('toggle', 'disabled')] = status not in ('paused', 'running')
            out[('step', 'disabled')] = status != 'paused'
            out[('rewind', 'disabled')] = not snapshot['can_rewind']
            out[('restart-button', 'disabled')] = False

            # The end-of-round modal shows once per game over. Dismissing it sets a
            # flag so reopening settings does not resurrect it; leaving the terminal
            # state (rewind, restart, new round) clears the flag again.
            terminal = status in ('won', 'lost')
            if not terminal:
                entry['result_dismissed'] = False
            if terminal and not entry.get('result_dismissed'):
                out[('result', 'className')] = 'overlay result-overlay is-open'
                out[('result-body', 'children')] = result_body(snapshot, rules)
            else:
                out[('result', 'className')] = 'overlay result-overlay'
            return [out[key] for key in OUTPUT_SPEC]

    @app.callback(Output('remaining', 'children'), Output('remaining', 'className'),
                  Output('gauge-fill', 'style'), Output('gauge-text', 'children'),
                  Output('cost-text', 'children'),
                  Output('allocate', 'disabled'), Output('primary', 'disabled'),
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
        with lock:
            entry = sessions.get(sid)
            game = entry['game'] if entry else None

            def primary_blocked(confirm_blocked):
                # The header button only means Confirm during setup. Once the round
                # is live it means PLAY/PAUSE, which unbalanced sliders must not lock.
                return confirm_blocked if game is not None and game.status == 'setup' else False

            def blank(confirm_blocked=True):
                return (badge, badge_class, {'width': '0%', 'background': DIM}, '—',
                        'Weights must total 100% before you can confirm.',
                        confirm_blocked, primary_blocked(confirm_blocked), *readouts)

            if game is None or not balanced:
                return blank()
            try:
                _, risk, cost, net = game.preview([value / 100 for value in values])
            except (ValueError, TypeError):
                return blank()
            ceiling = game.rules.risk_cap
            over = risk > ceiling + 1e-10
            colour = DOWN if over else (WARN if risk > ceiling * .8 else UP)
            width = min(100.0, risk / (ceiling * GAUGE_SCALE) * 100) if ceiling > 0 else 0.0
            text = f'{risk:.2%} vs {ceiling:.0%} ceiling' + (' · OVER' if over else '')
            cost_text = (f'Fee ${cost:,.2f} · net ${net:,.0f} after costs.' if not over else
                         'Above the ceiling. Lower the riskiest weight to continue.')
            blocked = over or game.status not in ('setup', 'paused')
            return (badge, badge_class, {'width': f'{width:.1f}%', 'background': colour},
                    text, cost_text, blocked, primary_blocked(blocked), *readouts)

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

    @app.callback(Output('page-home', 'className'), Output('page-game', 'className'),
                  Output('clock', 'disabled', allow_duplicate=True),
                  Input('url', 'pathname'), State('session', 'data'),
                  prevent_initial_call='initial_duplicate')
    def route(pathname, sid):
        """Swap pages, and freeze the market whenever the board is not on screen.

        The game is not mutated, so the status pill stays truthful; the browser
        simply stops ticking while you are reading the home page, and picks the
        clock back up if the round was still running when you left.
        """
        if not is_play_path(pathname):
            return 'page is-active', 'page', True
        with lock:
            entry = sessions.get(sid)
            running = bool(entry and entry['game'].status == 'running')
        return 'page', 'page is-active', not running

    return app


def is_play_path(pathname):
    """True for the game route. Adding a second game means extending this."""
    cleaned = (pathname or '/').rstrip('/') or '/'
    return cleaned in (PLAY_PATH, f'{PLAY_PATH}/portfolio-challenge')


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
