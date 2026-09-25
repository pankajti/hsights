"""Runtime configuration read from the environment.

Render injects PORT and expects the process to bind 0.0.0.0. Everything else
has a local-development default.
"""
from __future__ import annotations

import os

#: Render sets PORT; 8051 keeps the historical local default.
PORT = int(os.environ.get('PORT', 8051))

#: Render requires 0.0.0.0. Override to 127.0.0.1 for a private local run.
HOST = os.environ.get('HOST', '0.0.0.0')

#: Sessions live in process memory, so the service must stay single-instance.
#: This flag only controls whether the banner warns about it.
SINGLE_INSTANCE = os.environ.get('HSIGHTS_SINGLE_INSTANCE', '1') == '1'

#: Idle sessions are dropped after this many seconds.
SESSION_TTL_SECONDS = int(os.environ.get('HSIGHTS_SESSION_TTL', 7200))

#: Hard cap on concurrent in-memory games.
SESSION_LIMIT = int(os.environ.get('HSIGHTS_SESSION_LIMIT', 100))
