"""The hub: four games, one idea - markets are very good at fooling people.

Pure layout: no callbacks and no state. The tile order is shuffled on every
page load so no game gets the first slot for free; each tile's link carries
``src=hub&pos=N`` so the traction report can check for position bias.
"""
from __future__ import annotations

from random import SystemRandom

from dash import dcc, html

PLAY_PATH = '/play'

#: slug, route, icon, title, hook, lesson, minutes, badge
GAMES = (
    ('noise-hunt', '/noise-hunt/', 'chart', 'Noise Hunt',
     'Search a price chart for a winning trading rule. You will find one. '
     'Then bet on whether it is real.',
     'Why most backtests lie', 3, 'DAILY'),
    ('real-or-random', '/real-or-random/', 'shuffle', 'Real or Random?',
     'Ten pairs of charts. One is a real stock, one is a random walk. '
     'Can you beat a coin flip?',
     'How random real prices look', 2, 'DAILY'),
    ('star-manager', '/star-manager/', 'briefcase', 'The Star Manager',
     'Two hundred fund managers, three years of track record. Hire the best, '
     'then watch the next three years.',
     'Skill, luck and fees', 3, 'DAILY'),
    ('portfolio', PLAY_PATH, 'wallet', 'Portfolio Challenge',
     'Three real stocks, $100,000 and a year of history replayed day by day. '
     'Hit the target without breaching your limits.',
     'Risk you cannot sit out', 5, 'REAL HISTORY'),
)

PRINCIPLES = (
    ('shield', 'The answer stays sealed',
     'Whatever you are trying to predict - tomorrow\'s price, the holdout, the '
     'next three years - stays on the server until you commit.'),
    ('chart', 'Scored against chance',
     'Every game ends by asking how often luck alone would have done as well as '
     'you did. Often, the honest answer is: quite often.'),
    ('coins', 'A new puzzle every day',
     'Three games have a daily puzzle that is the same for everyone, with a '
     'result you can share without spoiling it.'),
)

LIMITS = (
    'These are games, not investment advice, and nothing in them is a forecast.',
    'Noise Hunt and The Star Manager use simulated data, on purpose: it lets the '
    'game know the truth, and it cannot be looked up.',
    'Real or Random? and Portfolio Challenge use real adjusted closes for stocks in '
    'the S&P 500 today, so they carry survivorship bias.',
    'Portfolio Challenge is not cheat-proof: a determined player can identify the '
    'stocks and look up what happened next. Rewind and restart mark a run ASSISTED.',
    'We count plays anonymously to learn which games people enjoy: one random '
    'cookie, no names, no emails, no IP addresses, no ad trackers.',
)

_shuffle = SystemRandom().shuffle


def game_card(game, position):
    slug, route, symbol, title, hook, lesson, minutes, badge = game
    href = f'{route}?src=hub&pos={position}'
    body = [
        html.Div([html.Img(src=f'/assets/icons/{symbol}.svg', className='icon icon-lg', alt=''),
                  html.Span(badge, className='game-badge')], className='game-card-head'),
        html.H3(title),
        html.P(hook, className='game-hook'),
        html.Div([html.Span(lesson, className='game-lesson'),
                  html.Span(f'{minutes} MIN', className='game-time')],
                 className='game-meta'),
        html.Span(['PLAY', html.Span('→', className='arrow')], className='game-play'),
    ]
    # Portfolio Challenge lives in this Dash app, so a client-side link keeps a
    # round in progress; the other games are separate apps and need a full load.
    if route == PLAY_PATH:
        return dcc.Link(body, href=href, className='game-card', id=f'card-{slug}')
    return html.A(body, href=href, className='game-card', id=f'card-{slug}')


def home_page():
    games = list(GAMES)
    _shuffle(games)
    return html.Div([
        html.Header([
            html.Div([html.Img(src='/assets/icons/chart.svg', className='icon', alt=''),
                      html.Span('HIND'), html.Span('SIGHT', className='wordmark-alt joined')],
                     className='wordmark'),
            html.Nav([
                html.A('Games', href='#games'),
                html.A('How it works', href='#how'),
                html.A('Honest limits', href='#limits'),
            ], className='home-nav'),
        ], className='home-header'),

        html.Section([
            html.P('HINDSIGHT · GAMES ABOUT HOW MARKETS FOOL YOU', className='eyebrow'),
            html.H1(['Markets are very good at fooling people.', html.Br(),
                     'Find out how good.']),
            html.P('Four short games built on real prices and honest statistics. Find a '
                   'strategy in pure noise, tell a real chart from a random one, hire a '
                   'star fund manager, survive a year of real market history.',
                   className='home-lede'),
            html.P('Free · no sign-up · new daily puzzles at 00:00 UTC', className='home-note'),
        ], className='hero hero-compact'),

        html.Section([
            game_card(game, position) for position, game in enumerate(games, start=1)
        ], className='game-grid', id='games'),

        html.Section([
            html.H2('How it works', id='how'),
            html.Div([
                html.Div([html.Img(src=f'/assets/icons/{symbol}.svg',
                                   className='icon icon-lg', alt=''),
                          html.H3(title), html.P(copy)], className='feature')
                for symbol, title, copy in PRINCIPLES
            ], className='features'),
        ], className='home-section'),

        html.Section([
            html.H2('What this is not', id='limits'),
            html.P('Every game states its own limits. Here they are in one place.',
                   className='section-lede'),
            html.Ul([html.Li(item) for item in LIMITS], className='limits'),
        ], className='home-section'),

        html.Footer([
            html.Span('Which game should we build out next? The ones you play decide.'),
            html.Span([
                html.A('Source on GitHub', href='https://github.com/pankajti/hsights',
                       target='_blank', rel='noopener noreferrer'),
                ' · Market data from Yahoo Finance. For entertainment and education '
                'only; not investment advice.',
            ]),
        ], className='home-footer'),
    ], className='home')
