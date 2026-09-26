"""User interfaces.

Each game's presentation layer lives here, separately from its domain logic in
``hsights.games``. The dependency runs one way only: a UI package imports its
game's public interface, and no game package imports anything from here.
"""
