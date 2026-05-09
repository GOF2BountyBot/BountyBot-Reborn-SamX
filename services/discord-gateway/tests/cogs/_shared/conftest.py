"""Per-test isolation for cogs._shared tests.

Some sibling test modules replace ``sys.modules["discord"]`` with a
MagicMock at import time (see tests/api/*.py). That substitution persists
across the session, so by the time pytest reaches tests that need the real
``discord`` module (like test_embed_pagination.py and test_loadout_embed.py
which call ``discord.Embed(...)`` directly) the module has been swapped.

The root conftest caches real references; we re-assert them here on every
test in this package so results don't depend on collection order.
"""

from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def _restore_real_discord():
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    if _cm is None:
        yield
        return
    # Re-assert the real discord modules cached by the root conftest.
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    # Drop any cached import of the helpers under test so they re-bind
    # ``discord`` to the real module on the next import.
    for _mod in ("cogs._shared.embed_pagination", "cogs._shared.loadout_embed"):
        if _mod in sys.modules:
            importlib.reload(sys.modules[_mod])
    yield
