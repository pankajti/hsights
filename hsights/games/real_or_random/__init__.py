"""Real or Random? - can you tell a real stock chart from a random walk?

Each round shows two price paths. One is a window of a real S&P 500 stock's
adjusted closes; the other is a geometric random walk with the same average
daily return and the same volatility. Both are rescaled to start at 100 and
carry no dates or names, so the only tells are the ones real markets really
have: fat tails and volatility that clusters.

Pure domain logic: numpy, pandas and scipy only. Which side is real stays in
the game object until the player has guessed.
"""

from .game import ROUNDS, Pair, RealOrRandom, coin_probability

__all__ = ['RealOrRandom', 'Pair', 'ROUNDS', 'coin_probability']
