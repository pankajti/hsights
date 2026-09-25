"""The hub landing page.

Pure layout: no callbacks and no state, so it can be lifted out to a static
CDN-hosted page later without touching the game. That is still the right end
state — a landing page should not share a process with the simulator when a
traffic spike arrives — but while there is one game it lives in the same Dash
app so there is only one thing to deploy.
"""
from __future__ import annotations

from dash import dcc, html

PLAY_PATH = '/play'

STEPS = (
    ('01', 'Pick your market',
     'Choose a start date anywhere in the last fifteen years and a length of run. '
     'The game deals you three random S&P 500 names that were actually trading '
     'then, and shows you nothing about what happens next.'),
    ('02', 'Build your mix',
     'Split $100,000 across the three. A live gauge shows the annualised '
     'volatility of the mix you are proposing and the fee it will cost, before '
     'you commit to it.'),
    ('03', 'Survive to the deadline',
     'Real prices replay one trading day per tick. Pause whenever you like to '
     'rebalance. Drift alone can push you through a limit, so watching is not '
     'the same as doing nothing.'),
)

RULES = (
    ('$100,000', 'Starting capital', 'Split across exactly three equities.'),
    ('+10%', 'Return target', 'Total for the run, not annualised. Meet it at the deadline to win.'),
    ('−8%', 'Loss floor', 'Fall below and the round ends immediately.'),
    ('20%', 'Risk ceiling', 'Annualised volatility of your current mix. Go above and the round ends.'),
    ('0.10%', 'Trading cost', 'Per dollar bought or sold, including the opening purchase.'),
    ('252 days', 'Default run', 'Roughly one trading year, adjustable from 1 to 2,520.'),
)

FEATURES = (
    ('chart', 'Real prices, not a simulation',
     'Fifteen years of split- and dividend-adjusted daily closes for 443 companies. '
     'Every round is a window of market history that genuinely happened.'),
    ('shield', 'Constraints that bind',
     'No cash, no shorting, no leverage. When a shock arrives you cannot hide in '
     'the sidelines, and sometimes there is no allocation that keeps you safe.'),
    ('briefcase', 'Measured against the market',
     'A dashed SPY buy-and-hold line runs alongside yours the whole way, so the '
     'question is never just "did I make money" but "did holding the index beat me".'),
)

LIMITS = (
    'This is a game, not investment advice, and nothing in it is a forecast.',
    'Rounds are drawn from companies in the index today, so results carry '
    'survivorship bias: the names that went to zero are not in the deck.',
    'Estimated risk is the volatility of your current allocation. It is not a '
    'maximum possible loss.',
    'Rewind and restart exist, and any run that uses them is labelled ASSISTED, '
    'because replaying prices you have already seen is an information advantage.',
    'Historical play is not cheat-proof. A determined player can identify the '
    'stocks from the return series and look up what happened next.',
)


def home_page():
    return html.Div([
        html.Header([
            html.Div([html.Img(src='/assets/icons/chart.svg', className='icon', alt=''),
                      html.Span('PORTFOLIO'), html.Span('LAB', className='wordmark-alt')],
                     className='wordmark'),
            html.Nav([
                html.A('How it works', href='#how'),
                html.A('Rules', href='#rules'),
                html.A('Honest limits', href='#limits'),
                dcc.Link('Play', href=PLAY_PATH, className='nav-play'),
            ], className='home-nav'),
        ], className='home-header'),

        html.Section([
            html.P('HINDSIGHT · THE HISTORICAL MARKET CHALLENGE', className='eyebrow'),
            html.H1(['Trade real market history,', html.Br(), 'one day at a time.']),
            html.P('You get three stocks, $100,000 and a year of real prices you have '
                   'never seen play out. Stay above the loss floor, stay below the risk '
                   'ceiling, and beat the target before the clock runs out. Hindsight is '
                   'the one thing you do not get.', className='home-lede'),
            html.Div([
                dcc.Link(['PLAY PORTFOLIO CHALLENGE', html.Span('→', className='arrow')],
                         href=PLAY_PATH, className='cta'),
                html.A('HOW IT WORKS', href='#how', className='cta ghost-cta'),
            ], className='cta-row'),
            html.P('Free · no sign-up · runs in your browser', className='home-note'),
        ], className='hero'),

        html.Section([
            html.Div([html.Img(src=f'/assets/icons/{symbol}.svg', className='icon icon-lg', alt=''),
                      html.H3(title), html.P(copy)], className='feature')
            for symbol, title, copy in FEATURES
        ], className='features'),

        html.Section([
            html.H2('How to play', id='how'),
            html.Div([
                html.Div([html.B(number), html.H3(title), html.P(copy)], className='how-step')
                for number, title, copy in STEPS
            ], className='how-grid'),
        ], className='home-section'),

        html.Section([
            html.H2('The rules', id='rules'),
            html.P('Defaults, all adjustable before you draw a round.',
                   className='section-lede'),
            html.Div([
                html.Div([html.Strong(value), html.Span(label), html.Small(copy)],
                         className='rule-card')
                for value, label, copy in RULES
            ], className='rules-grid'),
        ], className='home-section'),

        html.Section([
            html.H2('What this is not', id='limits'),
            html.P('The game states its own limits in the interface. They belong here too.',
                   className='section-lede'),
            html.Ul([html.Li(item) for item in LIMITS], className='limits'),
        ], className='home-section'),

        html.Section([
            html.H2('Ready?'),
            html.P('One round takes a couple of minutes at 4x speed.',
                   className='section-lede'),
            dcc.Link(['PLAY PORTFOLIO CHALLENGE', html.Span('→', className='arrow')],
                     href=PLAY_PATH, className='cta'),
        ], className='home-section closing'),

        html.Footer([
            html.Span('More games are on the way.'),
            html.Span([
                html.A('Source on GitHub', href='https://github.com/pankajti/hsights',
                       target='_blank', rel='noopener noreferrer'),
                ' · Market data from Yahoo Finance. For entertainment and education '
                'only; not investment advice.',
            ]),
        ], className='home-footer'),
    ], className='home')
