"""Tests for shipsCog — boosting coverage from 0% to 60%+."""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

# ---------------------------------------------------------------------------
# Module-level mock setup — must run before any src imports
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

_module_logger = None


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock with common log-level methods."""
    global _module_logger
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    logger.exception = MagicMock()
    _module_logger = logger
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# ---------------------------------------------------------------------------
# Phase-4 autocomplete_state cache helpers
# These are used by the autocomplete tests to pre-populate the shared caches
# that autocomplete_helpers.py now reads from instead of making HTTP calls.
#
# IMPORTANT: _evict_discord_modules() (called in the module-scoped fixture)
# evicts all utils.* modules from sys.modules, which causes a fresh import of
# autocomplete_state. We must always access autocomplete_state through
# sys.modules["utils.autocomplete_state"] to get the current live reference
# rather than a stale one from a pre-eviction import.
# ---------------------------------------------------------------------------


def _get_all_ac_states_from_cog(cog=None):
    """Return ALL active autocomplete_state module objects.

    CRITICAL: test_setup_adds_cog_to_bot calls _evict_discord_modules() mid-suite,
    which creates a NEW utils.autocomplete_state module object. The OLD one is still
    referenced by the module-scoped mock_ships_cog fixture (via the OLD
    player_ships_autocomplete function's __globals__). We must update BOTH.

    When `cog` is provided, we can find the OLD state via the cog's method:
    cog.setactive_autocomplete is a method that calls player_ships_autocomplete
    (imported into shipsCog's module-scope). The function's __globals__ holds
    the OLD autocomplete_state.

    All found modules are returned deduplicated by identity.
    """
    seen_ids: set[int] = set()
    result = []

    def _add(ac):
        if ac is not None and id(ac) not in seen_ids:
            seen_ids.add(id(ac))
            result.append(ac)

    # Strategy 1: Find via the cog instance (most reliable — reaches into OLD modules)
    if cog is not None:
        # setactive_autocomplete is a method of the OLD ShipsCog class.
        # Its code calls player_ships_autocomplete via shipsCog's globals.
        # To get those globals: cog.setactive_autocomplete.__func__.__globals__
        # is the OLD shipsCog module __dict__, which has player_ships_autocomplete.
        method = getattr(cog, "setactive_autocomplete", None)
        if method is not None:
            fn_globals = getattr(getattr(method, "__func__", None), "__globals__", None)
            if fn_globals is not None:
                # player_ships_autocomplete is in cog's module globals
                psa_fn = fn_globals.get("player_ships_autocomplete")
                if psa_fn is not None and hasattr(psa_fn, "__globals__"):
                    _add(psa_fn.__globals__.get("autocomplete_state"))

    # Strategy 2: Find via any autocomplete_helpers-like module in sys.modules
    for mod_name, mod in list(sys.modules.items()):
        if "autocomplete_helpers" in mod_name:
            _add(getattr(mod, "autocomplete_state", None))

    # Strategy 3: Direct sys.modules lookup
    _add(sys.modules.get("utils.autocomplete_state"))

    return result


def _ac_init_caches_for_cog(cog=None):
    """Initialize real (no-HTTP) autocomplete caches for ALL active state modules."""
    from cogs._shared.autocomplete_cache import AutocompleteCache

    ac_list = _get_all_ac_states_from_cog(cog)
    if not ac_list:
        return  # Can't initialize without the module

    for ac in ac_list:
        ac._initialized = False
        ac._http_client = None
        ac._api_base = None
        ac.player_cache = AutocompleteCache(ttl_seconds=900, name="player")
        ac.inventory_cache = AutocompleteCache(ttl_seconds=600, name="inventory")
        ac.ships_cache = AutocompleteCache(ttl_seconds=600, name="ships")
        ac._initialized = True


def _ac_reset_caches_for_cog(cog=None):
    """Reset ALL active autocomplete_state modules to uninitialized."""
    for ac in _get_all_ac_states_from_cog(cog):
        ac._initialized = False
        ac._http_client = None
        ac._api_base = None
        ac.player_cache = None
        ac.inventory_cache = None
        ac.ships_cache = None


# Keep backward-compat wrappers
def _get_all_ac_states(cog=None):
    return _get_all_ac_states_from_cog(cog)


def _ac_init_caches(cog=None):
    _ac_init_caches_for_cog(cog)


def _ac_reset_caches(cog=None):
    _ac_reset_caches_for_cog(cog)


def _get_ac_state(cog=None):
    """Return the first active autocomplete_state."""
    states = _get_all_ac_states_from_cog(cog)
    return states[0] if states else None


def _ac_ship_nc(ship_id, ship_name, is_active=False, nickname="", weapons=None, modules=None, turrets=None):
    """Build a NormalizedChoice for a ship dict (mirrors the raw dict format used by cogs)."""
    from utils.autocomplete_state import NormalizedChoice as _NC
    from utils.autocomplete_utils import normalize_for_search as _nfs

    raw = {
        "id": ship_id,
        "ship_name": ship_name,
        "is_active": is_active,
        "nickname": nickname or None,
        "weapons": weapons or [],
        "modules": modules or [],
        "turrets": turrets or [],
        "secondary_weapons": [],
    }
    nick = nickname or ""
    label = f"{ship_name} ({nick})" if nick else ship_name
    if is_active:
        label = f"🟢 {label}"
    return _NC(label=label, value=str(ship_id), norm=_nfs(label), raw=raw)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _evict_discord_modules():
    """Remove cached discord/source modules so they re-import with real discord."""
    to_evict = [
        k
        for k in sys.modules
        if k == "discord"
        or k.startswith("discord.")
        or k in ("api", "bot", "utils")
        or k.startswith("api.")
        or k.startswith("utils.")
        or k.startswith("cogs.")
    ]
    for k in to_evict:
        sys.modules.pop(k, None)


_BOT_CORE_URL = "http://bot-core:8000/api/v1"


def _with_real_http_client(cog, request):
    """Replace cog.http_client with a real httpx.AsyncClient for respx interception.

    House pattern — see this file's own `TestShipsCommandRespx._with_real_client`
    and test_adminCog.py's `_with_real_http_client`.
    """
    import httpx

    cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
    return cog


def _create_mock_interaction(user_id=111111111, guild_id=987654321):
    """Build a mock interaction with all needed attributes."""
    interaction = DiscordMockUtils.create_mock_interaction(
        user_id=user_id,
        guild_id=guild_id,
    )
    interaction.guild_id = guild_id
    interaction.user.display_name = "TestUser"
    interaction.user.display_avatar = MagicMock()
    interaction.user.display_avatar.url = "https://example.com/avatar.jpg"
    interaction.user.__str__ = MagicMock(return_value="TestUser#0001")
    return interaction


def _make_ship(
    ship_id=1,
    ship_name="Eagle",
    is_active=True,
    nickname=None,
    weapons=None,
    modules=None,
    turrets=None,
    secondary_weapons=None,
    created_at="2024-01-01T00:00:00",
):
    """Return a minimal ship dict."""
    return {
        "id": ship_id,
        "ship_name": ship_name,
        "is_active": is_active,
        "nickname": nickname,
        "weapons": weapons or ["Laser"],
        "modules": modules or [],
        "turrets": turrets or [],
        "secondary_weapons": secondary_weapons or [],
        "created_at": created_at,
        "player_id": 1,
    }


def _make_loadout(weapons=None, modules=None, turrets=None):
    """Return a minimal loadout dict."""
    weapons = weapons or ["Laser", "Plasma"]
    modules = modules or ["Shield"]
    turrets = turrets or []
    return {
        "weapons": weapons,
        "weapons_count": len(weapons),
        "modules": modules,
        "modules_count": len(modules),
        "turrets": turrets,
        "turrets_count": len(turrets),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_bot():
    """Mock Discord bot for shipsCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.fetch_user = AsyncMock(return_value=MagicMock(display_name="TestUser"))
    return bot


@pytest.fixture(scope="module")
def mock_ships_cog(mock_bot):
    """Create a ShipsCog instance with mocked bot and http_client."""
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.shipsCog import ShipsCog

    cog = ShipsCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestShipsCogInitialization:
    """Tests for ShipsCog initialization."""

    def test_initialization(self, mock_ships_cog, mock_bot):
        """ShipsCog should store bot reference and create http_client."""
        assert mock_ships_cog.bot is mock_bot
        assert mock_ships_cog.http_client is not None

    def test_initialization_logs_debug(self, mock_ships_cog):
        """ShipsCog __init__ should log a debug message."""
        assert _module_logger is not None
        _module_logger.debug.assert_called_with("ShipsCog initialized")


# ---------------------------------------------------------------------------
# cog_unload lifecycle
# ---------------------------------------------------------------------------


class TestCogUnload:
    """Tests for ShipsCog.cog_unload."""

    def test_cog_unload_closes_http_client(self, mock_ships_cog):
        """cog_unload should close the http client."""
        asyncio.run(mock_ships_cog.cog_unload())
        mock_ships_cog.http_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# _get_player_id helper
# ---------------------------------------------------------------------------


class TestGetPlayerIdHelper:
    """Tests for the _get_player_id helper method.

    TRUEUP-01 (R-gw-cogs-0 follow-up): migrated off `make_mock_response`/
    accept-anything AsyncMock to respx, pinned to the real POST /players/ URL.
    """

    def test_get_player_id_success(self, mock_ships_cog, request):
        """_get_player_id should return player ID on success."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 7}))
            result = asyncio.run(mock_ships_cog._get_player_id(111111111, 987654321))

        assert result == 7

    def test_get_player_id_api_error_returns_none(self, mock_ships_cog, request):
        """_get_player_id should return None on API error."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(side_effect=httpx.HTTPError("connection error"))
            result = asyncio.run(mock_ships_cog._get_player_id(111111111, 987654321))

        assert result is None

    def test_get_player_id_calls_correct_url_and_method(self, mock_ships_cog, request):
        """_get_player_id is the single point every ships command depends on for player
        resolution — lock its URL+method contract with respx: POST /api/v1/players/."""
        import httpx
        import respx

        cog = mock_ships_cog
        real_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        cog.http_client = real_client
        request.addfinalizer(lambda: asyncio.run(real_client.aclose()))

        with respx.mock(assert_all_called=True) as mock_router:
            route = mock_router.post("http://bot-core:8000/api/v1/players/").mock(
                return_value=httpx.Response(200, json={"id": 7})
            )
            result = asyncio.run(cog._get_player_id(111111111, 987654321))

        assert result == 7
        assert route.calls.last.request.method == "POST"


# ---------------------------------------------------------------------------
# ships command
# ---------------------------------------------------------------------------


class TestShipsCommand:
    """Tests for the /ships slash command.

    TRUEUP-01 (R-gw-cogs-0 follow-up): migrated off `make_mock_response`/
    accept-anything AsyncMock to respx, pinned to the real POST /players/ and
    GET /ships/player/{id} URLs.
    """

    def test_ships_display_own_ships(self, mock_ships_cog, request):
        """ships should display embed with user's ships."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/player/1").mock(
                return_value=httpx.Response(
                    200, json=[_make_ship(1, "Eagle", is_active=True), _make_ship(2, "Hawk", is_active=False)]
                )
            )
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ships_summary_includes_secondary_count(self, mock_ships_cog, request):
        """ships loadout summary must include the S: (secondary) count alongside W/M/T."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/player/1").mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        _make_ship(
                            1,
                            "Eagle",
                            is_active=True,
                            weapons=["Laser"],
                            secondary_weapons=["Jet Rocket"],
                            modules=["Cabin"],
                            turrets=[],
                        )
                    ],
                )
            )
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        embed = interaction.followup.send.call_args[1]["embed"]
        field_values = " ".join(f.value for f in embed.fields)
        assert "W:1 | S:1 | M:1 | T:0" in field_values

    def test_ships_no_ships_found(self, mock_ships_cog, request):
        """ships should send ephemeral message when player has no ships."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/player/1").mock(return_value=httpx.Response(200, json=[]))
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "no ships" in call_kwargs[0][0].lower()

    def test_ships_player_not_found(self, mock_ships_cog, request):
        """ships should send ephemeral error when player not found."""
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        # _get_player_id will return None; the cog never reaches GET /ships/player/.
        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(side_effect=RuntimeError("player error"))
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_ships_viewing_other_user(self, mock_ships_cog, request):
        """ships should display ships for another user when provided."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction(user_id=111111111)
        other_user = DiscordMockUtils.create_mock_user(user_id=222222222, username="OtherUser")
        other_user.display_name = "OtherUser"
        other_user.display_avatar = MagicMock()
        other_user.display_avatar.url = "https://example.com/other-avatar.jpg"

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 2}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/player/2").mock(
                return_value=httpx.Response(200, json=[_make_ship(3, "Falcon", is_active=True)])
            )
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction, user=other_user))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ships_more_than_10_shows_footer_with_count(self, mock_ships_cog, request):
        """ships with >10 ships should show truncation footer."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        many_ships = [_make_ship(i, f"Ship{i}", is_active=(i == 1)) for i in range(1, 13)]

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/player/1").mock(return_value=httpx.Response(200, json=many_ships))
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()

    def test_ships_http_status_error(self, mock_ships_cog, request):
        """ships should handle HTTPStatusError gracefully."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/player/1").mock(return_value=httpx.Response(500))
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_ships_generic_exception(self, mock_ships_cog, request):
        """ships should handle generic exceptions gracefully."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/player/1").mock(side_effect=RuntimeError("network error"))
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_ships_with_nickname(self, mock_ships_cog, request):
        """ships should display ship nickname when set."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/player/1").mock(
                return_value=httpx.Response(200, json=[_make_ship(1, "Eagle", is_active=True, nickname="StarHunter")])
            )
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()


class TestShipsCommandRespx:
    """respx-backed URL+method contract test for /ships happy path.

    TestShipsCommand above uses AsyncMock(http_client.get/post) which accepts
    ANY url/method — no test anywhere asserted the /ships GET contract. Locks:

      POST /api/v1/players/                     (player upsert)
      GET  /api/v1/ships/player/{player_id}    (ships list)
    """

    _BOT_API = "http://bot-core:8000/api/v1"

    def _with_real_client(self, cog, request):
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
        return cog

    def test_ships_calls_correct_urls(self, mock_ships_cog, request):
        """/ships must POST /players/ then GET /ships/player/{player_id}."""
        import httpx
        import respx

        self._with_real_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{self._BOT_API}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{self._BOT_API}/ships/player/1").mock(
                return_value=httpx.Response(200, json=[_make_ship(1, "Eagle", is_active=True)])
            )
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# Error handler callbacks
# ---------------------------------------------------------------------------


class TestErrorHandlers:
    """Tests for the error handler callbacks."""

    def test_ships_error_handler_response_not_done(self, mock_ships_cog):
        """ships_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_ships_cog.ships_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_ships_error_handler_response_already_done(self, mock_ships_cog):
        """ships_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_ships_cog.ships_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_ship_error_handler_response_not_done(self, mock_ships_cog):
        """ship_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_ships_cog.ship_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_setactive_error_handler_response_not_done(self, mock_ships_cog):
        """setactive_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_ships_cog.setactive_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_nickname_error_handler_response_not_done(self, mock_ships_cog):
        """nickname_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_ships_cog.nickname_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# setup() function
# ---------------------------------------------------------------------------


class TestCogSetup:
    """Tests for the module-level setup function."""

    def test_setup_adds_cog_to_bot(self, mock_bot):
        """setup() should add ShipsCog to the bot."""
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()

        from cogs.shipsCog import setup

        asyncio.run(setup(mock_bot))

        mock_bot.add_cog.assert_called_once()
        added_arg = mock_bot.add_cog.call_args[0][0]
        from cogs.shipsCog import ShipsCog

        assert isinstance(added_arg, ShipsCog)


# ---------------------------------------------------------------------------
# /ship command (detailed view)
# ---------------------------------------------------------------------------


class TestShipCommand:
    """Tests for the /ship slash command.

    TRUEUP-01 (R-gw-cogs-0 follow-up): migrated off `make_mock_response`/
    accept-anything AsyncMock to respx, pinned to the real GET /ships/{id},
    POST /players/, and GET /ships/{id}/loadout URLs (three distinct routes,
    so no `side_effect=[...]` list is needed the way the old shared-mock
    responder required — each route gets its own real response).
    """

    def test_ship_success_active_with_nickname(self, mock_ships_cog, request):
        """ship should display detailed embed for an active ship with nickname."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/1").mock(
                return_value=httpx.Response(
                    200, json=_make_ship(ship_id=1, ship_name="Eagle", is_active=True, nickname="StarHunter")
                )
            )
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/1/loadout").mock(
                return_value=httpx.Response(
                    200, json=_make_loadout(weapons=["Laser", "Plasma"], modules=["Shield"], turrets=["Flak"])
                )
            )
            asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ship_success_inactive_no_nickname(self, mock_ships_cog, request):
        """ship should display correct embed for inactive ship without nickname."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/2").mock(
                return_value=httpx.Response(
                    200, json=_make_ship(ship_id=2, ship_name="Hawk", is_active=False, nickname=None)
                )
            )
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/2/loadout").mock(
                return_value=httpx.Response(200, json=_make_loadout(weapons=[], modules=[], turrets=[]))
            )
            asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="2"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ship_not_owned_by_user(self, mock_ships_cog, request):
        """ship should deny access when ship belongs to another player."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        ship_data = _make_ship(ship_id=1)
        # Ship has player_id=1 but we'll return player_id=99 for the user
        ship_data["player_id"] = 99

        # The ownership check fails before the loadout GET is ever reached.
        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/1").mock(return_value=httpx.Response(200, json=ship_data))
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "don't own" in call_args[0][0]
        assert call_args[1].get("ephemeral", False)

    def test_ship_http_status_error_404(self, mock_ships_cog, request):
        """ship should show 'not found' on 404 HTTPStatusError."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/999").mock(return_value=httpx.Response(404))
            asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="999"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "not found" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)

    def test_ship_http_status_error_500(self, mock_ships_cog, request):
        """ship should show API error on non-404 HTTPStatusError."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/1").mock(return_value=httpx.Response(500))
            asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")

    def test_ship_generic_exception(self, mock_ships_cog, request):
        """ship should handle generic exceptions gracefully."""
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/1").mock(side_effect=RuntimeError("unexpected"))
            asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "error occurred" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)

    def test_ship_loadout_with_many_weapons(self, mock_ships_cog, request):
        """ship should truncate weapons list when >10 items."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        many_weapons = [f"Weapon{i}" for i in range(15)]

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/1").mock(
                return_value=httpx.Response(200, json=_make_ship(ship_id=1, is_active=True))
            )
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/1/loadout").mock(
                return_value=httpx.Response(200, json=_make_loadout(weapons=many_weapons, modules=[], turrets=[]))
            )
            asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ship_loadout_with_many_modules(self, mock_ships_cog, request):
        """ship should truncate modules list when >10 items."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        many_modules = [f"Module{i}" for i in range(12)]

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/1").mock(
                return_value=httpx.Response(200, json=_make_ship(ship_id=1, is_active=True))
            )
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/1/loadout").mock(
                return_value=httpx.Response(
                    200, json=_make_loadout(weapons=["Laser"], modules=many_modules, turrets=[])
                )
            )
            asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.followup.send.assert_awaited_once()

    def test_ship_loadout_with_many_turrets(self, mock_ships_cog, request):
        """ship should truncate turrets list when >10 items."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        many_turrets = [f"Turret{i}" for i in range(11)]

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/1").mock(
                return_value=httpx.Response(200, json=_make_ship(ship_id=1, is_active=True))
            )
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/1/loadout").mock(
                return_value=httpx.Response(200, json=_make_loadout(weapons=[], modules=[], turrets=many_turrets))
            )
            asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.followup.send.assert_awaited_once()


class TestShipCommandRespx:
    """respx-backed URL+method contract test for /ship happy path.

    TestShipCommand above uses AsyncMock(http_client.get/post) which accepts
    ANY url/method — no test anywhere asserted the /ship GET contract. Locks:

      GET  /api/v1/ships/{ship_id}             (ship detail)
      POST /api/v1/players/                    (ownership check player lookup)
      GET  /api/v1/ships/{ship_id}/loadout     (detailed loadout)
    """

    _BOT_API = "http://bot-core:8000/api/v1"

    def _with_real_client(self, cog, request):
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
        return cog

    def test_ship_calls_correct_urls(self, mock_ships_cog, request):
        """/ship must GET /ships/{id}, POST /players/, GET /ships/{id}/loadout."""
        import httpx
        import respx

        self._with_real_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{self._BOT_API}/ships/1").mock(
                return_value=httpx.Response(200, json=_make_ship(1, "Eagle", is_active=True))
            )
            mock_router.post(f"{self._BOT_API}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{self._BOT_API}/ships/1/loadout").mock(
                return_value=httpx.Response(200, json=_make_loadout())
            )
            asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="1"))

        interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# /setactive command
# ---------------------------------------------------------------------------


class TestSetActiveCommand:
    """Tests for the /setactive slash command.

    TRUEUP-01 (R-gw-cogs-0 follow-up): migrated off `make_mock_response`/
    accept-anything AsyncMock to respx, pinned to the real POST /players/ and
    PUT /ships/{id}/set-active URLs.
    """

    def test_setactive_success(self, mock_ships_cog, request):
        """setactive should set ship as active and send success embed."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.put(f"{_BOT_CORE_URL}/ships/5/set-active").mock(
                return_value=httpx.Response(
                    200, json={"id": 5, "ship_name": "Eagle", "nickname": None, "is_active": True}
                )
            )
            asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=5))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        assert call_kwargs.get("ephemeral", False), "/setactive success response must be ephemeral"

    def test_setactive_success_with_nickname(self, mock_ships_cog, request):
        """setactive should include nickname in success message when ship has one."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.put(f"{_BOT_CORE_URL}/ships/5/set-active").mock(
                return_value=httpx.Response(
                    200, json={"id": 5, "ship_name": "Eagle", "nickname": "StarHunter", "is_active": True}
                )
            )
            asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=5))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_setactive_player_not_found(self, mock_ships_cog, request):
        """setactive should send error when player is not found."""
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(side_effect=RuntimeError("player error"))
            asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=5))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "Player not found" in call_args[0][0]
        assert call_args[1].get("ephemeral", False)

    def test_setactive_http_status_error_400(self, mock_ships_cog, request):
        """setactive should show invalid ship on 400 HTTPStatusError."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.put(f"{_BOT_CORE_URL}/ships/5/set-active").mock(return_value=httpx.Response(400))
            asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=5))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "Invalid ship" in call_args[0][0] or "don't own" in call_args[0][0]
        assert call_args[1].get("ephemeral", False)

    def test_setactive_http_status_error_404(self, mock_ships_cog, request):
        """setactive should show 'not found' on 404 HTTPStatusError."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.put(f"{_BOT_CORE_URL}/ships/999/set-active").mock(return_value=httpx.Response(404))
            asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=999))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "not found" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)

    def test_setactive_http_status_error_500(self, mock_ships_cog, request):
        """setactive should show API error on non-400/404 HTTPStatusError."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.put(f"{_BOT_CORE_URL}/ships/5/set-active").mock(return_value=httpx.Response(500))
            asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=5))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")

    def test_setactive_generic_exception(self, mock_ships_cog, request):
        """setactive should handle generic exceptions gracefully."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.put(f"{_BOT_CORE_URL}/ships/5/set-active").mock(side_effect=RuntimeError("unexpected"))
            asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id=5))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "error occurred" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)


# ---------------------------------------------------------------------------
# /setactive URL+method contract (respx) — Tier 2 closeout 2026-04-30
# ---------------------------------------------------------------------------


class TestSetActiveCommandRespx:
    """respx-backed URL+method contract test for /setactive happy path.

    Verifies that /setactive hits the 2 expected bot-core routes:
      POST /api/v1/players/                       (player upsert)
      PUT  /api/v1/ships/{ship_id}/set-active     (active-ship update)

    Both URLs were verified against bot-core's registered routes during the
    2026-04-30 Tier 2 audit. Follows the policy in
    services/discord-gateway/tests/AGENTS.md (B.33 followup).
    """

    _BOT_API = "http://bot-core:8000/api/v1"

    def _with_real_client(self, cog, request):
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
        return cog

    def test_setactive_calls_correct_urls(self, mock_ships_cog, request):
        """/setactive must POST /players/ and PUT /ships/{ship_id}/set-active."""
        import httpx
        import respx

        self._with_real_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        ship_data = {
            "id": 5,
            "ship_name": "Eagle",
            "nickname": "Speedy",
            "is_active": True,
            "weapons": [],
            "modules": [],
            "turrets": [],
        }

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{self._BOT_API}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.put(f"{self._BOT_API}/ships/5/set-active").mock(
                return_value=httpx.Response(200, json=ship_data)
            )

            asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, "5"))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# /nickname command
# ---------------------------------------------------------------------------


class TestNicknameCommand:
    """Tests for the /nickname slash command.

    TRUEUP-01 (R-gw-cogs-0 follow-up): migrated off `make_mock_response`/
    accept-anything AsyncMock to respx, pinned to the real GET /ships/{id},
    POST /players/, and PUT /ships/{id}/nickname URLs.
    """

    def test_nickname_success(self, mock_ships_cog, request):
        """nickname should update ship nickname and send success embed."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/1").mock(
                return_value=httpx.Response(200, json=_make_ship(ship_id=1, is_active=True))
            )
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.put(f"{_BOT_CORE_URL}/ships/1/nickname").mock(
                return_value=httpx.Response(
                    200, json={"id": 1, "ship_name": "Eagle", "nickname": "NewName", "is_active": True}
                )
            )
            asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="1", nickname="NewName"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_nickname_success_inactive_ship(self, mock_ships_cog, request):
        """nickname should show inactive status for inactive ships."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/1").mock(
                return_value=httpx.Response(200, json=_make_ship(ship_id=1, is_active=False))
            )
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.put(f"{_BOT_CORE_URL}/ships/1/nickname").mock(
                return_value=httpx.Response(
                    200, json={"id": 1, "ship_name": "Eagle", "nickname": "MyShip", "is_active": False}
                )
            )
            asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="1", nickname="MyShip"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_nickname_too_long(self, mock_ships_cog):
        """nickname should reject nicknames longer than 50 characters."""
        interaction = _create_mock_interaction()

        long_name = "A" * 51

        asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="1", nickname=long_name))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "50 characters" in call_args[0][0]
        assert call_args[1].get("ephemeral", False)

    def test_nickname_not_owned(self, mock_ships_cog, request):
        """nickname should deny access when ship belongs to another player."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        ship_data = _make_ship(ship_id=1)
        ship_data["player_id"] = 99  # different from logged-in player

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/1").mock(return_value=httpx.Response(200, json=ship_data))
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="1", nickname="Test"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "don't own" in call_args[0][0]
        assert call_args[1].get("ephemeral", False)

    def test_nickname_http_status_error_404(self, mock_ships_cog, request):
        """nickname should show 'not found' on 404 HTTPStatusError."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/999").mock(return_value=httpx.Response(404))
            asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="999", nickname="Test"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "not found" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)

    def test_nickname_http_status_error_500(self, mock_ships_cog, request):
        """nickname should show API error on non-404 HTTPStatusError."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/1").mock(return_value=httpx.Response(500))
            asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="1", nickname="Test"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")

    def test_nickname_generic_exception(self, mock_ships_cog, request):
        """nickname should handle generic exceptions gracefully."""
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/1").mock(side_effect=RuntimeError("unexpected"))
            asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="1", nickname="Test"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "error occurred" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)


class TestNicknameCommandRespx:
    """respx-backed URL+method contract test for /nickname happy path.

    TestNicknameCommand above uses AsyncMock(http_client.get/post/put) which
    accepts ANY url/method — no test anywhere asserted the /nickname contract.
    Locks:

      GET  /api/v1/ships/{ship_id}              (ownership check)
      POST /api/v1/players/                     (player lookup for ownership check)
      PUT  /api/v1/ships/{ship_id}/nickname     (nickname update) — payload: {"nickname": ...}
    """

    _BOT_API = "http://bot-core:8000/api/v1"

    def _with_real_client(self, cog, request):
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
        return cog

    def test_nickname_calls_correct_urls_and_payload(self, mock_ships_cog, request):
        """/nickname must GET /ships/{id}, POST /players/, PUT /ships/{id}/nickname with payload."""
        import httpx
        import respx

        self._with_real_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{self._BOT_API}/ships/1").mock(
                return_value=httpx.Response(200, json=_make_ship(1, "Eagle"))
            )
            mock_router.post(f"{self._BOT_API}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            nick_route = mock_router.put(f"{self._BOT_API}/ships/1/nickname").mock(
                return_value=httpx.Response(200, json=_make_ship(1, "Eagle", nickname="StarHunter"))
            )
            asyncio.run(
                mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="1", nickname="StarHunter")
            )

        import json as _json

        body = _json.loads(nick_route.calls.last.request.content)
        assert body == {"nickname": "StarHunter"}
        interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# Additional error handler branches (response already done)
# ---------------------------------------------------------------------------


class TestErrorHandlersAlreadyDone:
    """Tests for error handler callbacks when response is already done."""

    def test_ship_error_handler_response_already_done(self, mock_ships_cog):
        """ship_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_ships_cog.ship_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_setactive_error_handler_response_already_done(self, mock_ships_cog):
        """setactive_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_ships_cog.setactive_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_nickname_error_handler_response_already_done(self, mock_ships_cog):
        """nickname_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_ships_cog.nickname_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Additional ships command branch coverage
# ---------------------------------------------------------------------------


class TestShipsCommandAdditionalBranches:
    """Additional tests for /ships covering remaining branches.

    TRUEUP-01 (R-gw-cogs-0 follow-up): migrated off `make_mock_response`/
    accept-anything AsyncMock to respx.
    """

    def test_ships_with_null_weapons_modules_turrets(self, mock_ships_cog, request):
        """ships should handle None weapons/modules/turrets gracefully."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/player/1").mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {
                            "id": 1,
                            "ship_name": "Eagle",
                            "is_active": True,
                            "nickname": None,
                            "weapons": None,
                            "modules": None,
                            "turrets": None,
                            "created_at": "2024-01-01T00:00:00",
                            "player_id": 1,
                        }
                    ],
                )
            )
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ships_exactly_10_shows_standard_footer(self, mock_ships_cog, request):
        """ships with exactly 10 ships should show standard footer (not truncation)."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        ten_ships = [_make_ship(i, f"Ship{i}", is_active=(i == 1)) for i in range(1, 11)]

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/player/1").mock(return_value=httpx.Response(200, json=ten_ships))
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs


# ---------------------------------------------------------------------------
# Permission check tests — /ships with user= parameter
# ---------------------------------------------------------------------------


class TestShipsPermissionChecks:
    """Tests verifying admin permission enforcement when viewing another user's ships.

    TRUEUP-01 (R-gw-cogs-0 follow-up): migrated off `make_mock_response`/
    accept-anything AsyncMock to respx.
    """

    def test_ships_own_user_no_admin_check_needed(self, mock_ships_cog, request):
        """Viewing own ships requires no admin permission — always succeeds."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction(user_id=111111111)

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/player/1").mock(
                return_value=httpx.Response(200, json=[_make_ship(1, "Eagle", is_active=True)])
            )
            # No user= argument: viewing own ships — no admin check performed
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ships_other_user_admin_allowed(self, mock_ships_cog, request):
        """Admin users can view another user's ships without error."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction(user_id=111111111)
        other_user = DiscordMockUtils.create_mock_user(user_id=222222222, username="OtherUser")
        other_user.display_name = "OtherUser"
        other_user.display_avatar = MagicMock()
        other_user.display_avatar.url = "https://example.com/other.jpg"

        with (
            respx.mock(assert_all_called=True) as mock_router,
            # Patch _check_is_admin to return True (user is admin)
            patch("cogs.adminCog._check_is_admin", new=AsyncMock(return_value=True)),
        ):
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 2}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/player/2").mock(
                return_value=httpx.Response(200, json=[_make_ship(3, "Falcon", is_active=True)])
            )
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction, user=other_user))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_ships_other_user_non_admin_denied(self, mock_ships_cog):
        """Non-admin users cannot view another user's ships — get ephemeral error."""
        from unittest.mock import patch

        interaction = _create_mock_interaction(user_id=111111111)
        other_user = DiscordMockUtils.create_mock_user(user_id=222222222, username="OtherUser")
        other_user.display_name = "OtherUser"
        other_user.display_avatar = MagicMock()
        other_user.display_avatar.url = "https://example.com/other.jpg"

        # Patch _check_is_admin to return False (user is NOT admin)
        with patch("cogs.adminCog._check_is_admin", new=AsyncMock(return_value=False)):
            asyncio.run(mock_ships_cog.ships.callback(mock_ships_cog, interaction, user=other_user))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert call_args[1].get("ephemeral", False)
        assert "admin" in call_args[0][0].lower()


# ---------------------------------------------------------------------------
# Setactive autocomplete
# ---------------------------------------------------------------------------


class TestSetactiveAutocomplete:
    """Tests for the setactive_autocomplete method.

    Phase 4: autocomplete reads from autocomplete_state caches instead of HTTP.
    Tests pre-populate the cache using _ac_init_caches() and the ship NormalizedChoice
    helpers. user_id=111111111, guild_id=987654321, player_id=1 are used throughout
    to match _create_mock_interaction().
    """

    @pytest.fixture(autouse=True)
    def _setup_ac_caches(self, mock_ships_cog):
        """Initialize autocomplete_state caches before each test (uses mock_ships_cog to find OLD state)."""
        _ac_init_caches(mock_ships_cog)
        for ac in _get_all_ac_states(mock_ships_cog):
            ac.player_cache.set((987654321, 111111111), {"id": 1})
        yield
        _ac_reset_caches(mock_ships_cog)

    def test_setactive_autocomplete_returns_player_ships(self, mock_ships_cog, make_mock_response):
        """setactive_autocomplete should list player's ships as choices (Phase 4: from cache)."""
        interaction = _create_mock_interaction()
        ships = [
            _ac_ship_nc(1, "Eagle", is_active=True),
            _ac_ship_nc(2, "Mako"),
        ]
        for ac in _get_all_ac_states(mock_ships_cog):
            ac.ships_cache.set((987654321, 1), ships)

        choices = asyncio.run(mock_ships_cog.setactive_autocomplete(interaction, ""))

        assert len(choices) == 2
        active_choice = next((c for c in choices if c.value == "1"), None)
        assert active_choice is not None
        assert "🟢" in active_choice.name

    def test_setactive_autocomplete_filters_by_current_input(self, mock_ships_cog, make_mock_response):
        """setactive_autocomplete should filter ships by current input (Phase 4: from cache)."""
        interaction = _create_mock_interaction()
        ships = [
            _ac_ship_nc(1, "Eagle"),
            _ac_ship_nc(2, "Mako"),
            _ac_ship_nc(3, "Viper"),
        ]
        for ac in _get_all_ac_states(mock_ships_cog):
            ac.ships_cache.set((987654321, 1), ships)

        choices = asyncio.run(mock_ships_cog.setactive_autocomplete(interaction, "Ma"))

        names = [c.name for c in choices]
        assert any("Mako" in n for n in names)
        assert not any("Eagle" in n for n in names)
        assert not any("Viper" in n for n in names)

    def test_setactive_autocomplete_shows_nickname(self, mock_ships_cog, make_mock_response):
        """setactive_autocomplete should show nickname in choice label (Phase 4: from cache)."""
        interaction = _create_mock_interaction()
        ships = [_ac_ship_nc(1, "Eagle", nickname="StarHunter")]
        for ac in _get_all_ac_states(mock_ships_cog):
            ac.ships_cache.set((987654321, 1), ships)

        choices = asyncio.run(mock_ships_cog.setactive_autocomplete(interaction, ""))

        assert len(choices) == 1
        assert "StarHunter" in choices[0].name

    def test_setactive_autocomplete_returns_empty_on_api_failure(self, mock_ships_cog):
        """setactive_autocomplete should return [] on cold cache miss (Phase 4)."""
        # ships_cache was just initialized but NOT populated → cold miss returns []
        interaction = _create_mock_interaction()
        choices = asyncio.run(mock_ships_cog.setactive_autocomplete(interaction, ""))
        assert choices == []


# ---------------------------------------------------------------------------
# Setactive — invalid ship_id validation
# ---------------------------------------------------------------------------


class TestSetactiveInvalidShipId:
    """Tests for the /setactive invalid ship_id handling."""

    def test_setactive_invalid_non_numeric_string_shows_error(self, mock_ships_cog):
        """setactive should show error message for non-numeric ship_id."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id="not-a-number"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert call_args[1].get("ephemeral", False)
        assert "invalid" in call_args[0][0].lower()

    def test_setactive_numeric_string_is_accepted(self, mock_ships_cog, request):
        """setactive should accept a numeric string like '5' (from autocomplete value)."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.put(f"{_BOT_CORE_URL}/ships/5/set-active").mock(
                return_value=httpx.Response(
                    200, json={"id": 5, "ship_name": "Eagle", "nickname": None, "is_active": True}
                )
            )
            asyncio.run(mock_ships_cog.setactive.callback(mock_ships_cog, interaction, ship_id="5"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs


# ---------------------------------------------------------------------------
# A.29 new autocomplete coverage: /ship and /nickname
# ---------------------------------------------------------------------------


class TestShipAutocomplete:
    """Tests for the new ship_autocomplete method (used by /ship and /nickname).

    Phase 4: autocomplete reads from autocomplete_state caches instead of HTTP.
    """

    @pytest.fixture(autouse=True)
    def _setup_ac_caches(self, mock_ships_cog):
        """Initialize autocomplete_state caches before each test (uses mock_ships_cog to find OLD state)."""
        _ac_init_caches(mock_ships_cog)
        for ac in _get_all_ac_states(mock_ships_cog):
            ac.player_cache.set((987654321, 111111111), {"id": 1})
        yield
        _ac_reset_caches(mock_ships_cog)

    def test_ship_autocomplete_returns_player_ships_without_active_prefix(self, mock_ships_cog, make_mock_response):
        """A.34a: ship_autocomplete should NOT show 🟢 prefix (Phase 4: from cache).

        The active-ship indicator is suppressed for selection-only dropdowns to avoid
        cluttering the autocomplete list. /setactive still shows the indicator.
        """
        interaction = _create_mock_interaction()
        ships = [
            _ac_ship_nc(7, "Eagle", is_active=True),
            _ac_ship_nc(8, "Mako"),
        ]
        for ac in _get_all_ac_states(mock_ships_cog):
            ac.ships_cache.set((987654321, 1), ships)

        choices = asyncio.run(mock_ships_cog.ship_autocomplete(interaction, ""))

        assert len(choices) == 2
        active_choice = next((c for c in choices if c.value == "7"), None)
        assert active_choice is not None
        assert active_choice.value == "7"  # values are strings per the design
        # A.34a: active ship must NOT show 🟢 in /ship and /nickname autocomplete
        assert "🟢" not in active_choice.name, "ship_autocomplete must not show active indicator (A.34a)"
        assert active_choice.name == "Eagle"

        inactive_choice = next((c for c in choices if c.value == "8"), None)
        assert inactive_choice is not None
        assert inactive_choice.name == "Mako"

    def test_ship_autocomplete_returns_empty_on_failure(self, mock_ships_cog):
        """ship_autocomplete should return [] on cold cache miss (Phase 4)."""
        # ships_cache was just initialized but NOT populated → cold miss returns []
        interaction = _create_mock_interaction()
        choices = asyncio.run(mock_ships_cog.ship_autocomplete(interaction, ""))
        assert choices == []


class TestShipCommandStrParamHandling:
    """Tests for /ship — ship_id parameter is now str (was int)."""

    def test_ship_accepts_numeric_string(self, mock_ships_cog, request):
        """/ship with '42' (str) should call bot-core with /api/v1/ships/42."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        # respx only registers a route at /ships/42 (int path) — if the cog sent a
        # differently-formatted URL (e.g. the raw "42" string mangled), this route
        # simply would not match and the real client would raise, failing the test.
        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/42").mock(
                return_value=httpx.Response(200, json=_make_ship(ship_id=42, is_active=True))
            )
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.get(f"{_BOT_CORE_URL}/ships/42/loadout").mock(
                return_value=httpx.Response(200, json=_make_loadout())
            )
            asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="42"))

    def test_ship_rejects_non_numeric_string(self, mock_ships_cog):
        """/ship with non-numeric ship_id shows a friendly error and does not call API."""
        interaction = _create_mock_interaction()
        mock_ships_cog.http_client.get = AsyncMock()
        mock_ships_cog.http_client.post = AsyncMock()

        asyncio.run(mock_ships_cog.ship.callback(mock_ships_cog, interaction, ship_id="notanumber"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "invalid" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)
        mock_ships_cog.http_client.get.assert_not_called()


class TestNicknameCommandStrParamHandling:
    """Tests for /nickname — ship_id parameter is now str (was int)."""

    def test_nickname_accepts_numeric_string(self, mock_ships_cog, request):
        """/nickname with '42' (str) should call bot-core with /api/v1/ships/42."""
        import httpx
        import respx

        _with_real_http_client(mock_ships_cog, request)
        interaction = _create_mock_interaction()

        # respx only registers a route at /ships/42 — verifies the ship lookup used
        # the parsed int in the path, same guarantee as the old url.endswith() assert.
        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_CORE_URL}/ships/42").mock(
                return_value=httpx.Response(200, json=_make_ship(ship_id=42, is_active=True))
            )
            mock_router.post(f"{_BOT_CORE_URL}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.put(f"{_BOT_CORE_URL}/ships/42/nickname").mock(
                return_value=httpx.Response(
                    200, json={"id": 42, "ship_name": "Eagle", "nickname": "MyShip", "is_active": True}
                )
            )
            asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="42", nickname="MyShip"))

    def test_nickname_rejects_non_numeric_string(self, mock_ships_cog):
        """/nickname with non-numeric ship_id shows friendly error and no API call."""
        interaction = _create_mock_interaction()
        mock_ships_cog.http_client.get = AsyncMock()

        asyncio.run(mock_ships_cog.nickname.callback(mock_ships_cog, interaction, ship_id="bogus", nickname="Test"))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert "invalid" in call_args[0][0].lower()
        assert call_args[1].get("ephemeral", False)
        mock_ships_cog.http_client.get.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
