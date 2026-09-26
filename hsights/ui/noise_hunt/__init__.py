"""Dash interface for Noise Hunt.

Imports only the public surface of ``hsights.games.noise_hunt``. Nothing in
this package knows how a series is generated, how a rule is evaluated, or
where the holdout is kept — it calls ``try_rule`` and ``reveal`` and renders
what comes back.
"""

TITLE = 'Noise Hunt'
SLUG = 'noise-hunt'
