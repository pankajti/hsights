"""The Star Manager - hire the best fund manager, then watch them regress.

Two hundred managers run money against the same market for six years. You
see the first three: returns, volatility, Sharpe, drawdown, the lot. You hire
up to three of them. Then the next three years play out.

A handful of managers (sometimes none) have genuine skill. Everyone else is
a market exposure plus noise, and everyone charges 1% a year. The lesson is
how little a three-year track record says about the next three years, and
how hard it is for anyone to beat a cheap index fund after fees.

Pure domain logic: numpy only. Future returns stay inside the game object.
"""

from .game import (ACTIVE_FEE, INDEX_FEE, MANAGERS, Outcome, StarManager, annualised)

__all__ = ['StarManager', 'Outcome', 'MANAGERS', 'ACTIVE_FEE', 'INDEX_FEE', 'annualised']
