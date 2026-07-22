"""API-test-package conftest: pin one discord module generation per test.

Why this exists
---------------
The refactored tests/api/ suite runs the REAL routers, converters and helpers
(`resolve_bot`, `get_entity_or_404`, `handle_discord_exception`) against
spec'd mocks, so it depends on discord *class identity*: an
``except discord.NotFound`` in a router only catches the exception raised by
the same imported discord module the test built its mocks from.

Under xdist every worker imports (collects) the WHOLE suite before running its
share. Some cogs test modules pop ``discord``/``discord.ext``/... out of
``sys.modules`` at import time (the "ensure real discord" idiom), so any module
imported after them binds a *re-imported* second generation of discord classes.
This package's test modules import discord during collection BEFORE those
evictions (api < cogs alphabetically), so their module globals hold the first
generation — while at execution time ``sys.modules`` may hold a later one.
Mixed generations make real isinstance/except dispatch fail with 500s even
though every file passes in isolation.

The autouse fixture below forces ``sys.modules`` back to the generation this
package was imported under for the duration of each api test, and purges the
``api``/``utils`` source modules so routers and helpers re-import against that
pinned generation. Everything is restored afterwards, so cogs/root tests keep
their existing (root-conftest snapshot) semantics.
"""

import sys
from collections.abc import Generator

import discord as _api_discord
import discord.app_commands as _api_app_commands
import discord.ext as _api_discord_ext
import discord.ext.commands as _api_discord_ext_commands
import pytest

_PINNED = {
    "discord": _api_discord,
    "discord.ext": _api_discord_ext,
    "discord.ext.commands": _api_discord_ext_commands,
    "discord.app_commands": _api_app_commands,
}


def _volatile(name: str) -> bool:
    """Module names whose cached import may be bound to another discord generation."""
    return (
        name == "discord"
        or name.startswith("discord.")
        or name in ("api", "utils")
        or name.startswith(("api.", "utils."))
    )


@pytest.fixture(autouse=True)
def _pin_real_discord_generation() -> Generator[None]:
    saved = {k: m for k, m in sys.modules.items() if _volatile(k)}
    # Drop every volatile module, then install the pinned discord generation.
    for k in list(sys.modules):
        if _volatile(k):
            del sys.modules[k]
    sys.modules.update(_PINNED)
    try:
        yield
    finally:
        for k in [k for k in sys.modules if _volatile(k)]:
            del sys.modules[k]
        sys.modules.update(saved)
