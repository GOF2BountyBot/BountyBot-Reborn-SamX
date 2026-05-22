"""Tests for bountyCog — covers /check, /bounties, /route, /criminal-loadout."""

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

_mock_utils = DiscordMockUtils()

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")

_module_logger = None
_all_loggers: dict[str, MagicMock] = {}


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock with common log-level methods."""
    global _module_logger
    name = _args[0] if _args else None
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    logger.exception = MagicMock()
    _module_logger = logger
    if name:
        _all_loggers[name] = logger
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


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


def _make_bounty_public(
    bounty_id=1,
    criminal_name="BlackViper",
    division="bronze",
    reward=5000,
    reward_per_sys=500,
    route=None,
    checked=None,
    status="active",
):
    """Return a minimal BountyPublicResponse dict."""
    if route is None:
        route = ["Alpha", "Beta", "Gamma"]
    if checked is None:
        checked = {}
    return {
        "id": bounty_id,
        "guild_id": 987654321,
        "division": division,
        "criminal_name": criminal_name,
        "criminal_faction": "Outlaws",
        "route": route,
        "reward": reward,
        "reward_per_sys": reward_per_sys,
        "checked": checked,
        "issue_time": "2026-03-14T10:00:00",
        "end_time": "2026-03-15T10:00:00",
        "tech_level": 2,
        "status": status,
    }


def _make_check_response(result="correct", bounty_id=1, message="", division="bronze"):
    """Return a minimal BountyCheckResponse dict.

    Includes division (default "bronze") so _build_check_embed can apply
    tier-based color-coding (Sub-task A). Tests that check specific embed
    colors should match TIER_COLORS[division] for tier-colored results.
    """
    return {
        "result": result,
        "bounty_id": bounty_id,
        "message": message,
        "division": division,
        "outcomes": [
            {
                "result": result,
                "bounty_id": bounty_id,
                "message": message,
                "division": division,
                "criminal_name": "TestCriminal",
                "reward": 500,
                "recently_spotted": False,
            }
        ],
        "result_count": 1,
    }


def _make_route_response(
    bounty_id=1,
    criminal_name="BlackViper",
    route=None,
    checked=None,
    status="active",
    system_statuses=None,
):
    """Return a minimal route response dict."""
    if route is None:
        route = ["Alpha", "Beta", "Gamma"]
    if checked is None:
        checked = {}
    if system_statuses is None:
        system_statuses = {}
    return {
        "bounty_id": bounty_id,
        "criminal_name": criminal_name,
        "route": route,
        "checked": checked,
        "status": status,
        "system_statuses": system_statuses,
    }


def _make_loadout_response(
    bounty_id=1,
    criminal_name="BlackViper",
    tech_level=2,
    ship_name="Viper MkII",
    ship_stats=None,
    weapons=None,
    modules=None,
    turrets=None,
    message=None,
):
    """Return a minimal unified LoadoutResponse dict (subject_kind='criminal')."""
    if ship_stats is None:
        ship_stats = {
            "armour": 150,
            "cargo": 45,
            "handling": 60,
            "hp": 690,
            "dps": 25.0,
            "total_value": 1000,
            "max_primaries": 2,
            "max_secondaries": 0,
            "max_turrets": 0,
            "max_modules": 2,
        }
    if weapons is None:
        weapons = [
            {"name": "Pulse Laser", "emoji": "<:pl:1>", "dps": 10.0, "value": 500},
            {"name": "Beam Laser", "emoji": "<:bl:1>", "dps": 15.0, "value": 700},
        ]
    if modules is None:
        modules = [
            {
                "name": "D'iol Armour",
                "emoji": "<:diol:1>",
                "type": "ArmourModule",
                "value": 500,
                "tech_level": 1,
                "effects": [{"label": "Armour", "value": "160"}],
                "combat_tier": "combat",
            },
            {
                "name": "Particle Shield",
                "emoji": "<:ps:1>",
                "type": "ShieldModule",
                "value": 800,
                "tech_level": 1,
                "effects": [{"label": "Shield", "value": "380"}],
                "combat_tier": "combat",
            },
        ]
    if turrets is None:
        turrets = []
    resp = {
        "subject_kind": "criminal",
        "subject_name": criminal_name,
        "subject_description": "Void Syndicate",
        "bounty_id": bounty_id,
        "tech_level": tech_level,
        "ship_name": ship_name,
        "ship_emoji": "<:viper:1>",
        "ship_icon": "https://cdn/ship.png",
        "thumbnail_url": "https://cdn/criminal.png",
        "ship_stats": ship_stats,
        "weapons": weapons,
        "turrets": turrets,
        "modules": modules,
        "cargo": [],
        "cargo_total_count": 0,
    }
    if message is not None:
        resp["message"] = message
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _close_coro(coro):
    """Close coroutine to prevent 'never awaited' warning."""
    coro.close()
    return MagicMock()


@pytest.fixture(scope="module")
def mock_bot():
    """Mock Discord bot for bountyCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    # loop.create_task is required for the preload scheduling in __init__
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock(side_effect=_close_coro)
    return bot


@pytest.fixture
def mock_bounty_cog(mock_bot):
    """Create a BountyCog instance with mocked bot and http_client."""
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.bountyCog import BountyCog

    cog = BountyCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestBountyCogInitialization:
    """Tests for BountyCog initialization."""

    def test_initialization(self, mock_bounty_cog, mock_bot):
        """BountyCog should store bot reference and create http_client."""
        assert mock_bounty_cog.bot is mock_bot
        assert mock_bounty_cog.http_client is not None

    def test_initialization_has_systems_cache(self, mock_bounty_cog):
        """BountyCog should initialize with a _systems_cache AutocompleteCache."""
        from cogs._shared.autocomplete_cache import AutocompleteCache

        assert hasattr(mock_bounty_cog, "_systems_cache")
        assert isinstance(mock_bounty_cog._systems_cache, AutocompleteCache)

    def test_initialization_bounty_cache_ttl_is_1200(self, mock_bounty_cog):
        """_bounty_cache must be initialized with ttl_seconds=1200.0 (Item A: 60→1200)."""
        from cogs._shared.autocomplete_cache import AutocompleteCache

        assert hasattr(mock_bounty_cog, "_bounty_cache")
        assert isinstance(mock_bounty_cog._bounty_cache, AutocompleteCache)
        assert mock_bounty_cog._bounty_cache._ttl == 1200.0, (
            f"Expected _bounty_cache TTL=1200s, got {mock_bounty_cog._bounty_cache._ttl}"
        )

    def test_initialization_systems_cache_ttl_is_none(self, mock_bounty_cog):
        """_systems_cache must use ttl_seconds=None (never expires — static catalog)."""
        assert mock_bounty_cog._systems_cache._ttl is None, (
            f"Expected _systems_cache TTL=None, got {mock_bounty_cog._systems_cache._ttl}"
        )

    def test_cog_unload_closes_http_client(self, mock_bounty_cog):
        """cog_unload should close the http client."""
        asyncio.run(mock_bounty_cog.cog_unload())
        mock_bounty_cog.http_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Preload
# ---------------------------------------------------------------------------


class TestPreloadData:
    """Tests for _preload_data method.

    B.33 remediation: tests use respx to assert exact URL + HTTP method,
    confirming bountyCog calls GET /about/categories/system/objects (correct
    bot-core route per about.py:85) rather than any wrong URL/method.
    """

    _SYSTEMS_URL = "http://bot-core:8000/api/v1/about/categories/system/objects"

    def _with_real_client(self, cog, request):
        """Replace cog.http_client with a real httpx.AsyncClient for respx interception.

        Registers a pytest finalizer to close the client after the test so no
        httpx.AsyncClient instances are leaked between tests.
        """
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
        return cog

    def test_preload_data_populates_systems(self, mock_bounty_cog, request):
        """_preload_data calls GET /about/categories/system/objects and populates _systems."""
        import httpx
        import respx

        self._with_real_client(mock_bounty_cog, request)
        mock_bounty_cog.bot.wait_until_ready = AsyncMock()

        systems_data = [
            {"name": "Sol", "id": 1},
            {"name": "Alpha Centauri", "id": 2},
            {"name": "Proxima", "id": 3},
        ]

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(self._SYSTEMS_URL).mock(return_value=httpx.Response(200, json=systems_data))
            asyncio.run(mock_bounty_cog._preload_data())

        assert mock_bounty_cog._systems_cache.peek("all") == ["Sol", "Alpha Centauri", "Proxima"]

    def test_preload_data_handles_api_failure_gracefully(self, mock_bounty_cog, request):
        """_preload_data sets _systems to [] after all retries exhausted on 500 errors."""
        import httpx
        import respx

        self._with_real_client(mock_bounty_cog, request)
        mock_bounty_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(self._SYSTEMS_URL).mock(
                return_value=httpx.Response(503, json={"detail": "Service Unavailable"})
            )
            with patch("cogs.bountyCog.asyncio.sleep", new=AsyncMock()) as mock_sleep:
                asyncio.run(mock_bounty_cog._preload_data())

        assert mock_bounty_cog._systems_cache.peek("all") == []
        # Should have slept 5 times (once per retry attempt)
        assert mock_sleep.call_count == 5

    def test_preload_data_retries_on_timeout(self, mock_bounty_cog, request):
        """_preload_data retries on TimeoutException and succeeds on 2nd attempt."""
        import httpx
        import respx

        self._with_real_client(mock_bounty_cog, request)
        mock_bounty_cog.bot.wait_until_ready = AsyncMock()
        attempt_count = {"n": 0}

        async def flaky_handler(request):
            attempt_count["n"] += 1
            if attempt_count["n"] == 1:
                raise httpx.TimeoutException("timeout", request=request)
            return httpx.Response(200, json=[{"name": "Sol", "id": 1}])

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(self._SYSTEMS_URL).mock(side_effect=flaky_handler)
            with patch("cogs.bountyCog.asyncio.sleep", new=AsyncMock()) as mock_sleep:
                asyncio.run(mock_bounty_cog._preload_data())

        assert mock_bounty_cog._systems_cache.peek("all") == ["Sol"]
        # Should have slept once after the first failure
        assert mock_sleep.call_count == 1

    def test_preload_data_retries_correct_delays(self, mock_bounty_cog, request):
        """_preload_data uses exponential backoff delays [5, 10, 20, 40, 60]."""
        import httpx
        import respx

        self._with_real_client(mock_bounty_cog, request)
        mock_bounty_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(self._SYSTEMS_URL).mock(return_value=httpx.Response(500, json={"detail": "error"}))
            with patch("cogs.bountyCog.asyncio.sleep", new=AsyncMock()) as mock_sleep:
                asyncio.run(mock_bounty_cog._preload_data())

        expected_delays = [5, 10, 20, 40, 60]
        actual_delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert actual_delays == expected_delays

    def test_preload_data_logs_warning_on_retry(self, mock_bounty_cog, request):
        """_preload_data logs a warning on each failed attempt and error at terminal failure."""
        import httpx
        import respx

        self._with_real_client(mock_bounty_cog, request)
        mock_bounty_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=False) as mock_router:
            mock_router.get(self._SYSTEMS_URL).mock(return_value=httpx.Response(500, json={"detail": "boom"}))
            with patch("cogs.bountyCog.asyncio.sleep", new=AsyncMock()):
                asyncio.run(mock_bounty_cog._preload_data())

        # Should have logged a warning for each attempt and an error at the end
        # Use named logger lookup to avoid confusion with autocomplete_state logger
        bounty_logger = _all_loggers.get("discord-gateway-BountyCog", _module_logger)
        assert bounty_logger.warning.call_count == 5
        assert bounty_logger.error.call_count >= 1

    def test_preload_data_returns_immediately_on_success(self, mock_bounty_cog, request):
        """_preload_data returns after first successful attempt, no retry sleep."""
        import httpx
        import respx

        self._with_real_client(mock_bounty_cog, request)
        mock_bounty_cog.bot.wait_until_ready = AsyncMock()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(self._SYSTEMS_URL).mock(return_value=httpx.Response(200, json=[{"name": "Sol", "id": 1}]))
            with patch("cogs.bountyCog.asyncio.sleep", new=AsyncMock()) as mock_sleep:
                asyncio.run(mock_bounty_cog._preload_data())

        assert mock_bounty_cog._systems_cache.peek("all") == ["Sol"]
        # No sleep should occur on first-attempt success
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# System autocomplete
# ---------------------------------------------------------------------------


class TestSystemAutocomplete:
    """Tests for system_autocomplete method."""

    def test_system_autocomplete_returns_matching_systems(self, mock_bounty_cog):
        """system_autocomplete should return systems matching current input."""
        mock_bounty_cog._systems_cache.set("all", ["Sol", "Alpha Centauri", "Proxima", "Sirius"])
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_bounty_cog.system_autocomplete(interaction, "sol"))

        assert len(result) == 1
        assert result[0].name == "Sol"
        assert result[0].value == "Sol"

    def test_system_autocomplete_empty_input_returns_all(self, mock_bounty_cog):
        """system_autocomplete with empty input should return all systems (up to 25)."""
        mock_bounty_cog._systems_cache.set("all", ["Sol", "Alpha Centauri", "Proxima"])
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_bounty_cog.system_autocomplete(interaction, ""))

        assert len(result) == 3

    def test_system_autocomplete_max_25_results(self, mock_bounty_cog):
        """system_autocomplete should cap results at 25."""
        mock_bounty_cog._systems_cache.set("all", [f"System{i}" for i in range(50)])
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_bounty_cog.system_autocomplete(interaction, ""))

        assert len(result) == 25

    def test_system_autocomplete_empty_systems_returns_empty(self, mock_bounty_cog):
        """system_autocomplete with empty _systems list should return empty list."""
        mock_bounty_cog._systems_cache.set("all", [])
        interaction = _create_mock_interaction()

        result = asyncio.run(mock_bounty_cog.system_autocomplete(interaction, "Sol"))

        assert result == []


# ---------------------------------------------------------------------------
# Bounty autocomplete
# ---------------------------------------------------------------------------


def _init_bounty_caches(
    guild_id=987654321, user_id=111111111,
    player_tier="Bronze", player_id=1,
    bounties=None, cog=None,
):
    """Pre-populate caches for bounty autocomplete tests (Phase 6)."""
    import utils.autocomplete_state as ac_state
    from cogs._shared.autocomplete_cache import AutocompleteCache

    if ac_state.player_cache is None:
        ac_state.player_cache = AutocompleteCache(name="player-bounty-test")
    ac_state.player_cache.set((guild_id, user_id), {"id": player_id, "tier": player_tier})

    if cog is not None and bounties is not None:
        cog._bounty_cache.set(guild_id, bounties)


class TestBountyAutocomplete:
    """Tests for bounty_autocomplete method (Phase 6: zero-HTTP, cache-backed)."""

    def test_bounty_autocomplete_returns_formatted_choices(self, mock_bounty_cog):
        """bounty_autocomplete returns formatted bounty choices filtered to player's tier, zero HTTP."""
        guild_id = 987654321
        user_id = 111111111
        bounties = [_make_bounty_public(1, "Falcon-Jones", "gold", reward=5000, reward_per_sys=500)]
        _init_bounty_caches(
            guild_id=guild_id, user_id=user_id, player_tier="Gold", bounties=bounties, cog=mock_bounty_cog
        )

        mock_bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        result = asyncio.run(mock_bounty_cog.bounty_autocomplete(interaction, ""))

        assert len(result) == 1
        assert result[0].value == "1"
        assert "Falcon-Jones" in result[0].name
        assert "Gold" in result[0].name
        assert "5,000cr" in result[0].name

    def test_bounty_autocomplete_filters_by_current_input(self, mock_bounty_cog):
        """bounty_autocomplete filters choices by current input, zero HTTP."""
        guild_id = 987654321
        user_id = 111111111
        # Put both bounties in cache — player is bronze so silver should be filtered
        bounties = [
            _make_bounty_public(1, "BlackViper", "bronze", reward=1000),
            _make_bounty_public(2, "RedFang", "silver", reward=2000),
        ]
        _init_bounty_caches(
            guild_id=guild_id, user_id=user_id, player_tier="Bronze", bounties=bounties, cog=mock_bounty_cog
        )

        mock_bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        result = asyncio.run(mock_bounty_cog.bounty_autocomplete(interaction, "black"))

        assert len(result) == 1
        assert "BlackViper" in result[0].name

    def test_bounty_autocomplete_cold_cache_miss_returns_empty(self, mock_bounty_cog):
        """bounty_autocomplete returns [] on bounty cache cold miss (no HTTP)."""
        guild_id = 987654321
        user_id = 111111111
        _init_bounty_caches(
            guild_id=guild_id, user_id=user_id, player_tier="Bronze", bounties=None, cog=mock_bounty_cog
        )
        mock_bounty_cog._bounty_cache.invalidate(guild_id)

        mock_bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        result = asyncio.run(mock_bounty_cog.bounty_autocomplete(interaction, ""))
        assert result == []

    def test_bounty_autocomplete_filters_by_player_tier(self, mock_bounty_cog):
        """Phase 6: player tier from player_cache used to filter bounties by division, zero HTTP."""
        guild_id = 987654321
        user_id = 111111111
        bounties = [
            _make_bounty_public(1, "SilverFox", "silver", reward=2000),
            _make_bounty_public(2, "GoldEagle", "gold", reward=5000),
        ]
        _init_bounty_caches(
            guild_id=guild_id, user_id=user_id, player_tier="Silver", bounties=bounties, cog=mock_bounty_cog
        )

        mock_bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        result = asyncio.run(mock_bounty_cog.bounty_autocomplete(interaction, ""))

        # Only Silver bounties should appear (player is Silver tier)
        assert len(result) == 1
        assert "SilverFox" in result[0].name

    def test_bounty_autocomplete_falls_back_to_all_bounties_on_player_cache_miss(self, mock_bounty_cog):
        """Phase 6: bounty_autocomplete shows ALL bounties when player cache miss (graceful degradation)."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        guild_id = 987654321
        user_id = 111111111
        bounties = [
            _make_bounty_public(1, "BronzeViper", "bronze", reward=1000),
            _make_bounty_public(2, "GoldHawk", "gold", reward=5000),
        ]
        mock_bounty_cog._bounty_cache.set(guild_id, bounties)

        # Ensure player cache has no entry for this user
        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-bounty-test")
        ac_state.player_cache.invalidate((guild_id, user_id))

        mock_bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        result = asyncio.run(mock_bounty_cog.bounty_autocomplete(interaction, ""))

        # Graceful degradation: ALL bounties shown when player tier unknown
        assert len(result) == 2

    def test_bounty_autocomplete_empty_tier_shows_all_bounties(self, mock_bounty_cog):
        """Phase 6: empty tier in player cache → show ALL bounties (no division filter applied)."""
        guild_id = 987654321
        user_id = 111111111
        bounties = [_make_bounty_public(1, "BronzeViper", "bronze", reward=1000)]
        _init_bounty_caches(
            guild_id=guild_id, user_id=user_id, player_tier="", bounties=bounties, cog=mock_bounty_cog
        )

        mock_bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        result = asyncio.run(mock_bounty_cog.bounty_autocomplete(interaction, ""))

        # Empty tier → no division filter → all bounties shown
        assert len(result) == 1

    def test_bounty_autocomplete_none_tier_shows_all_bounties(self, mock_bounty_cog):
        """Phase 6: None tier in player cache → show ALL bounties (no division filter applied)."""
        guild_id = 987654321
        user_id = 111111111
        bounties = [_make_bounty_public(1, "SilverFox", "silver", reward=2000)]
        _init_bounty_caches(
            guild_id=guild_id, user_id=user_id, player_tier=None, bounties=bounties, cog=mock_bounty_cog
        )

        mock_bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        result = asyncio.run(mock_bounty_cog.bounty_autocomplete(interaction, ""))

        # None tier → no division filter → all bounties shown
        assert len(result) == 1


# ---------------------------------------------------------------------------
# /check command
# ---------------------------------------------------------------------------


class TestCheckCommand:
    """Tests for the /check slash command."""

    @pytest.fixture(autouse=True)
    def _patch_player_id(self, mock_bounty_cog):
        """Patch _get_player_id to return a valid game player ID for all /check tests."""
        mock_bounty_cog._get_player_id = AsyncMock(return_value=42)

    @pytest.fixture(autouse=True)
    def _populate_systems(self, mock_bounty_cog):
        """Populate _systems so resolve_system_name can resolve the test system names."""
        mock_bounty_cog._systems_cache.set("all", ["Alpha", "Beta", "Delta", "Sol"])

    def test_check_correct_result_green_embed(self, mock_bounty_cog, make_mock_response):
        """/check CORRECT (capture) sends a minimal ephemeral text confirmation, not an embed.

        The full payout detail lives in the single public embed posted by
        _post_capture_payout in bot-core; sending an embed here too produced a
        confusing double-embed for the invoker.
        """
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_check_response("correct", bounty_id=1, message="Target neutralised!", division="bronze"))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        call_kwargs = call_args[1]
        # Must be a plain text confirmation (no embed) and ephemeral
        assert "embed" not in call_kwargs
        assert call_kwargs.get("ephemeral") is True
        # Message text is passed as a positional arg
        content = call_args[0][0] if call_args[0] else call_kwargs.get("content", "")
        assert "capture" in content.lower()

    def test_check_not_found_result_orange_embed(self, mock_bounty_cog, make_mock_response):
        """/check NOT_FOUND result should display orange embed."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_check_response("not_found"))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Delta"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        import discord

        assert call_kwargs["embed"].color == discord.Color.orange()

    def test_check_incorrect_result_red_embed(self, mock_bounty_cog, make_mock_response):
        """/check INCORRECT result should display tier-colored embed (Sub-task A)."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_check_response("incorrect", message="Bounty is 2 jumps away.", division="silver"))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Beta"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        from cogs.bountyCog import TIER_COLORS
        assert call_kwargs["embed"].color.value == TIER_COLORS["silver"]

    def test_check_already_checked_result_yellow_embed(self, mock_bounty_cog, make_mock_response):
        """/check ALREADY_CHECKED result should display tier-colored embed (Sub-task A)."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_check_response("already_checked", division="gold"))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        from cogs.bountyCog import TIER_COLORS
        assert call_kwargs["embed"].color.value == TIER_COLORS["gold"]

    def test_check_cooldown_429_response(self, mock_bounty_cog, make_mock_response):
        """/check 429 response should show cooldown message."""
        interaction = _create_mock_interaction()
        resp = make_mock_response({}, status_code=429)
        resp.status_code = 429
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "cooldown" in call_kwargs[0][0].lower()

    def test_check_api_error_handled_gracefully(self, mock_bounty_cog):
        """/check generic exception should show error message."""
        interaction = _create_mock_interaction()
        mock_bounty_cog.http_client.post = AsyncMock(side_effect=RuntimeError("connection refused"))

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error occurred" in call_kwargs[0][0].lower()

    def test_check_http_status_error_handled(self, mock_bounty_cog):
        """/check HTTPStatusError (non-429) should show API error."""
        import httpx

        interaction = _create_mock_interaction()
        error_response = MagicMock()
        error_response.status_code = 500
        http_error = httpx.HTTPStatusError("500 Error", request=MagicMock(), response=error_response)
        mock_bounty_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        # B.31b: helper now sends a sanitized embed instead of a raw URL string.
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")


# ---------------------------------------------------------------------------
# Bug 2A: /check ephemeral behavior — single-outcome path
# ---------------------------------------------------------------------------


class TestCheckCommandEphemeralBehavior:
    """Bug 2A: Verify that capture outcomes are ephemeral, non-capture outcomes are public.

    Single-outcome path rules:
    - result='correct' + combat_won=True  → ephemeral=True  (capture)
    - result='correct' + combat_won=None  → ephemeral=True  (no combat, treat as capture)
    - result='correct' + combat_won=False → ephemeral=False (player lost, not a capture)
    - result='incorrect'                  → ephemeral=False (public)
    - result='not_found'                  → ephemeral=False (public)
    - result='already_checked'            → ephemeral=False (public)
    - result='cooldown'                   → ephemeral=True  (error path, not in scope here)
    Multi-outcome path: always public (ephemeral=False)
    """

    @pytest.fixture(autouse=True)
    def _patch_player_id(self, mock_bounty_cog):
        mock_bounty_cog._get_player_id = AsyncMock(return_value=42)

    @pytest.fixture(autouse=True)
    def _populate_systems(self, mock_bounty_cog):
        mock_bounty_cog._systems_cache.set("all", ["Alpha"])

    def _run_check(self, cog, make_mock_response, response_data, system="Alpha"):
        interaction = _create_mock_interaction()
        resp = make_mock_response(response_data)
        cog.http_client.post = AsyncMock(return_value=resp)
        asyncio.run(cog.check.callback(cog, interaction, system))
        call_kwargs = interaction.followup.send.call_args[1]
        return call_kwargs

    def test_correct_combat_won_true_is_ephemeral(self, mock_bounty_cog, make_mock_response):
        """result='correct' + combat_won=True → single outcome followup is ephemeral."""
        resp_data = {
            "result": "correct",
            "outcomes": [{"result": "correct", "bounty_id": 1, "combat_won": True, "division": "bronze"}],
            "result_count": 1,
        }
        call_kwargs = self._run_check(mock_bounty_cog, make_mock_response, resp_data)
        assert call_kwargs.get("ephemeral") is True, (
            "Capture (result=correct, combat_won=True) must send ephemeral=True"
        )

    def test_correct_combat_won_none_is_ephemeral(self, mock_bounty_cog, make_mock_response):
        """result='correct' + combat_won absent (None) → single outcome followup is ephemeral."""
        resp_data = {
            "result": "correct",
            "outcomes": [{"result": "correct", "bounty_id": 1, "division": "bronze"}],
            "result_count": 1,
        }
        call_kwargs = self._run_check(mock_bounty_cog, make_mock_response, resp_data)
        assert call_kwargs.get("ephemeral") is True, (
            "Capture (result=correct, combat_won absent) must send ephemeral=True"
        )

    def test_correct_combat_won_false_is_not_ephemeral(self, mock_bounty_cog, make_mock_response):
        """result='correct' + combat_won=False → NOT ephemeral (player lost)."""
        resp_data = {
            "result": "correct",
            "outcomes": [{"result": "correct", "bounty_id": 1, "combat_won": False, "division": "bronze"}],
            "result_count": 1,
        }
        call_kwargs = self._run_check(mock_bounty_cog, make_mock_response, resp_data)
        # combat_won=False means player lost — NOT a capture, should be public
        assert not call_kwargs.get("ephemeral"), (
            "Combat loss (result=correct, combat_won=False) must NOT be ephemeral"
        )

    def test_incorrect_result_is_not_ephemeral(self, mock_bounty_cog, make_mock_response):
        """result='incorrect' → NOT ephemeral (wrong system, public feedback)."""
        call_kwargs = self._run_check(
            mock_bounty_cog,
            make_mock_response,
            _make_check_response("incorrect"),
        )
        assert not call_kwargs.get("ephemeral"), "Incorrect result must NOT be ephemeral"

    def test_not_found_result_is_not_ephemeral(self, mock_bounty_cog, make_mock_response):
        """result='not_found' → NOT ephemeral."""
        call_kwargs = self._run_check(
            mock_bounty_cog,
            make_mock_response,
            _make_check_response("not_found"),
        )
        assert not call_kwargs.get("ephemeral"), "not_found result must NOT be ephemeral"

    def test_already_checked_result_is_not_ephemeral(self, mock_bounty_cog, make_mock_response):
        """result='already_checked' → NOT ephemeral."""
        call_kwargs = self._run_check(
            mock_bounty_cog,
            make_mock_response,
            _make_check_response("already_checked"),
        )
        assert not call_kwargs.get("ephemeral"), "already_checked result must NOT be ephemeral"

    def test_multi_outcome_is_not_ephemeral(self, mock_bounty_cog, make_mock_response):
        """Multi-outcome path always stays public (NOT ephemeral)."""
        resp_data = {
            "result": "correct",
            "outcomes": [
                {"result": "correct", "bounty_id": 1, "combat_won": True, "division": "gold"},
                {"result": "correct", "bounty_id": 2, "combat_won": True, "division": "silver"},
            ],
            "result_count": 2,
        }
        interaction = _create_mock_interaction()
        resp = make_mock_response(resp_data)
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)
        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))
        call_kwargs = interaction.followup.send.call_args[1]
        assert not call_kwargs.get("ephemeral"), (
            "Multi-outcome path must stay public (NOT ephemeral)"
        )


# ---------------------------------------------------------------------------
# /check URL+method contract (respx) — Tier 2 closeout 2026-04-30
# ---------------------------------------------------------------------------


class TestCheckCommandRespx:
    """respx-backed URL+method contract test for /check happy path.

    Verifies that /check hits the 2 expected bot-core routes:
      POST /api/v1/players/         (player upsert)
      POST /api/v1/bounties/check   (bounty system check)

    Both URLs were verified against bot-core's registered routes during the
    2026-04-30 Tier 2 audit. Follows the policy in
    services/discord-gateway/tests/AGENTS.md (B.33 followup).
    """

    _BOT_API = "http://bot-core:8000/api/v1"

    @pytest.fixture(autouse=True)
    def _populate_systems(self, mock_bounty_cog):
        """Populate _systems so resolve_system_name can resolve 'Sol'."""
        mock_bounty_cog._systems_cache.set("all", ["Sol"])

    def _with_real_client(self, cog, request):
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
        return cog

    def test_check_calls_correct_urls(self, mock_bounty_cog, request):
        """/check must POST /players/ and POST /bounties/check."""
        import httpx
        import respx

        self._with_real_client(mock_bounty_cog, request)
        interaction = _create_mock_interaction()

        check_response = {
            "result": "incorrect",
            "message": "No bounty in this system.",
            "outcomes": [{"result": "incorrect", "bounty_id": None}],
        }

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{self._BOT_API}/players/").mock(return_value=httpx.Response(200, json={"id": 1}))
            mock_router.post(f"{self._BOT_API}/bounties/check").mock(
                return_value=httpx.Response(200, json=check_response)
            )

            asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, system="Sol"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# /bounties command
# ---------------------------------------------------------------------------


class TestBountiesCommand:
    """Tests for the /bounties slash command."""

    def test_bounties_lists_active_bounties(self, mock_bounty_cog, make_mock_response):
        """/bounties (default) should list active bounties for the player's tier."""
        interaction = _create_mock_interaction()
        bounty_list = [
            _make_bounty_public(1, "BlackViper", "bronze"),
        ]
        player_resp = make_mock_response({"id": 1, "tier": "Bronze"})
        bounty_resp = make_mock_response(bounty_list)
        mock_bounty_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=bounty_resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_bounties_no_active_bounties_shows_empty_message(self, mock_bounty_cog, make_mock_response):
        """/bounties with no bounties for the player's tier should show 'No active bounties'."""
        interaction = _create_mock_interaction()
        player_resp = make_mock_response({"id": 1, "tier": "Bronze"})
        bounty_resp = make_mock_response([])
        mock_bounty_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=bounty_resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "no active bounties" in embed.description.lower()

    def test_bounties_default_filters_to_player_tier(self, mock_bounty_cog, make_mock_response):
        """/bounties default (show_all=False) should pass the player's tier as division param."""
        interaction = _create_mock_interaction()
        bounty_list = [_make_bounty_public(1, "GoldHawk", "gold")]
        player_resp = make_mock_response({"id": 1, "tier": "Gold"})
        bounty_resp = make_mock_response(bounty_list)
        mock_bounty_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=bounty_resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        get_call_kwargs = mock_bounty_cog.http_client.get.call_args[1]
        assert get_call_kwargs["params"].get("division") == "gold"
        embed = interaction.followup.send.call_args[1]["embed"]
        assert "Gold" in embed.title

    def test_bounties_show_all_omits_division_filter(self, mock_bounty_cog, make_mock_response):
        """/bounties show_all=True should NOT pass division to API and show all tiers in title."""
        interaction = _create_mock_interaction()
        bounty_list = [
            _make_bounty_public(1, "BlackViper", "bronze"),
            _make_bounty_public(2, "GoldHawk", "gold"),
        ]
        resp = make_mock_response(bounty_list)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction, show_all=True))

        get_call_kwargs = mock_bounty_cog.http_client.get.call_args[1]
        assert "division" not in get_call_kwargs["params"]
        embed = interaction.followup.send.call_args[1]["embed"]
        assert "All Tiers" in embed.title

    def test_bounties_api_error_handled(self, mock_bounty_cog, make_mock_response):
        """/bounties generic exception on GET should show error message."""
        interaction = _create_mock_interaction()
        player_resp = make_mock_response({"id": 1, "tier": "Bronze"})
        mock_bounty_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=RuntimeError("boom"))

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error occurred" in call_kwargs[0][0].lower()


class TestBountiesCommandCachePeekFirst:
    """/bounties command reads from _bounty_cache.peek() when cache is warm (Item A).

    The Item A overhaul adds _bounty_cache.peek(guild_id) as the primary read path
    before falling back to HTTP. These tests verify the cache-first behavior.
    """

    def _setup_player_cache(self, guild_id, user_id, tier="Bronze"):
        """Pre-populate the shared player cache so /bounties skips the HTTP player upsert."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test-bounties")
        ac_state.player_cache.set((guild_id, user_id), {"id": 1, "tier": tier})

    def test_bounties_reads_from_bounty_cache_no_http_get(self, mock_bounty_cog, make_mock_response):
        """/bounties uses _bounty_cache.peek() when warm — no GET to bot-core.

        When the bounty cache is warm AND the player cache knows the tier, /bounties
        must serve entirely from cache without any HTTP call to bot-core.
        """
        guild_id = 987654321
        user_id = 111111111
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        # Pre-populate player cache so no POST /players/ needed
        self._setup_player_cache(guild_id, user_id, tier="Bronze")

        # Pre-populate bounty cache
        bounties = [_make_bounty_public(1, "TestViper", "bronze")]
        mock_bounty_cog._bounty_cache.set(guild_id, bounties)

        # HTTP must NOT be called at all — both caches are warm
        mock_bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP POST must not be called"))
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP GET must not be called"))

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed in successful cache-hit path"

    def test_bounties_falls_back_to_http_when_bounty_cache_cold(self, mock_bounty_cog, make_mock_response):
        """/bounties falls back to HTTP GET when _bounty_cache is cold (no cached bounties).

        Player cache is warm (no POST), but bounty cache is cold → GET /bounties/ is made.
        """
        guild_id = 987654321
        user_id = 111111111
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        # Player cache warm, bounty cache cold
        self._setup_player_cache(guild_id, user_id, tier="Bronze")
        mock_bounty_cog._bounty_cache.invalidate(guild_id)

        # HTTP GET will be called for the fallback
        bounty_resp = make_mock_response([_make_bounty_public(1, "FallbackViper", "bronze")])
        mock_bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP POST must not be called"))
        mock_bounty_cog.http_client.get = AsyncMock(return_value=bounty_resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        mock_bounty_cog.http_client.get.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()

    def test_bounties_show_all_warm_cache_no_http(self, mock_bounty_cog):
        """/bounties show_all=True with warm cache → no HTTP calls at all."""
        guild_id = 987654321
        user_id = 111111111
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        # Pre-populate bounty cache with mixed-tier bounties
        bounties = [
            _make_bounty_public(1, "BronzeViper", "bronze"),
            _make_bounty_public(2, "GoldHawk", "gold"),
        ]
        mock_bounty_cog._bounty_cache.set(guild_id, bounties)

        # Neither HTTP POST nor GET should be called
        mock_bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("No POST expected"))
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=AssertionError("No GET expected"))

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction, show_all=True))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert "All Tiers" in embed.title

    def test_bounties_cache_filters_by_tier_when_warm(self, mock_bounty_cog):
        """/bounties with warm cache filters bounties by player tier client-side."""
        guild_id = 987654321
        user_id = 111111111
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        # Player cache warm with Silver tier
        self._setup_player_cache(guild_id, user_id, tier="Silver")

        # Bounty cache has both bronze and silver bounties
        bounties = [
            _make_bounty_public(1, "BronzeViper", "bronze"),
            _make_bounty_public(2, "SilverFox", "silver"),
        ]
        mock_bounty_cog._bounty_cache.set(guild_id, bounties)

        mock_bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("No POST expected"))
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=AssertionError("No GET expected"))

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        # Only Silver bounty should appear in the title
        assert "Silver" in embed.title


# ---------------------------------------------------------------------------
# /route command
# ---------------------------------------------------------------------------


class TestRouteCommand:
    """Tests for the /route slash command."""

    def test_route_displays_checked_and_unchecked_systems(self, mock_bounty_cog, make_mock_response):
        """/route should show strikethrough for checked systems."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(
            _make_route_response(
                route=["Alpha", "Beta", "Gamma"],
                checked={"Alpha": 1},
                # B.24: system_statuses is the new rendering source; provide it
                system_statuses={"Alpha": "checked"},
            )
        )
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "1"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        # The field value should contain strikethrough for Alpha
        field_value = embed.fields[0].value
        assert "~~Alpha~~" in field_value
        assert "Beta" in field_value
        assert "Gamma" in field_value

    def test_route_invalid_bounty_string_shows_error(self, mock_bounty_cog):
        """/route with non-numeric bounty string should show error message."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "not-a-number"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "invalid" in call_kwargs[0][0].lower()

    def test_route_404_shows_bounty_not_found(self, mock_bounty_cog):
        """/route 404 should send bounty not found message."""
        import httpx

        interaction = _create_mock_interaction()
        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=error_response)
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "999"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "not found" in call_kwargs[0][0].lower()

    def test_route_api_error_handled(self, mock_bounty_cog):
        """/route generic exception should show error message."""
        interaction = _create_mock_interaction()
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=RuntimeError("boom"))

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error occurred" in call_kwargs[0][0].lower()

    def test_route_shows_division_in_description(self, mock_bounty_cog, make_mock_response):
        """/route should show the bounty's division (tier) in the embed description."""
        interaction = _create_mock_interaction()
        route_data = _make_route_response(route=["Alpha", "Beta"])
        route_data["division"] = "gold"
        resp = make_mock_response(route_data)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert "Gold" in embed.description

    def test_route_no_division_in_description_when_not_present(self, mock_bounty_cog, make_mock_response):
        """/route description should not include tier when division is absent."""
        interaction = _create_mock_interaction()
        route_data = _make_route_response(route=["A", "B"])
        # No division key in response
        resp = make_mock_response(route_data)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert "Tier:" not in embed.description

    def test_route_recently_spotted_uses_bold_strikethrough(self, mock_bounty_cog, make_mock_response):
        """B.24: /route renders recently_spotted systems with **~~bold strikethrough~~** + 🔍."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(
            _make_route_response(
                route=["Alpha", "Beta", "Gamma"],
                checked={"Alpha": 1, "Beta": 2},
                system_statuses={"Alpha": "checked", "Beta": "recently_spotted"},
            )
        )
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        field_value = embed.fields[0].value
        # Alpha is checked — plain strikethrough
        assert "~~Alpha~~" in field_value
        # Alpha should NOT have bold
        assert "**~~Alpha~~**" not in field_value
        # Beta is recently spotted — bold + strikethrough + 🔍
        assert "**~~Beta~~**" in field_value
        assert "🔍" in field_value
        # Gamma is unchecked — plain
        assert "~~Gamma~~" not in field_value
        assert "Gamma" in field_value

    def test_route_checked_system_uses_strikethrough_checkmark(self, mock_bounty_cog, make_mock_response):
        """B.24: /route renders checked (not recently spotted) systems with ~~strikethrough~~ ✅."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(
            _make_route_response(
                route=["Proxima", "Tau"],
                checked={"Proxima": 1},
                system_statuses={"Proxima": "checked"},
            )
        )
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        field_value = interaction.followup.send.call_args[1]["embed"].fields[0].value
        assert "~~Proxima~~" in field_value
        assert "✅" in field_value
        assert "**~~Proxima~~**" not in field_value  # not recently spotted

    def test_route_unchecked_system_is_plain(self, mock_bounty_cog, make_mock_response):
        """B.24: /route renders unchecked systems as plain text (no markdown)."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(
            _make_route_response(
                route=["Sol", "Proxima"],
                checked={},
                system_statuses={},
            )
        )
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        field_value = interaction.followup.send.call_args[1]["embed"].fields[0].value
        # Both systems unchecked — plain text, no strikethrough
        assert "~~Sol~~" not in field_value
        assert "Sol" in field_value
        assert "~~Proxima~~" not in field_value
        assert "Proxima" in field_value

    def test_route_backward_compat_no_system_statuses_field(self, mock_bounty_cog, make_mock_response):
        """B.24: /route falls back gracefully when system_statuses field is missing (API backward compat)."""
        interaction = _create_mock_interaction()
        # API response WITHOUT system_statuses (older API version simulation)
        route_data = {
            "bounty_id": 1,
            "criminal_name": "OldBounty",
            "route": ["Alpha", "Beta"],
            "checked": {"Alpha": 1},
            "status": "active",
        }
        resp = make_mock_response(route_data)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "1"))

        # Should not crash — should still send an embed
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs


# ---------------------------------------------------------------------------
# /criminal-loadout command
# ---------------------------------------------------------------------------


class TestCriminalLoadoutCommand:
    """Tests for the /criminal-loadout slash command (shared embed builder consumer)."""

    def test_criminal_loadout_sends_embed_public(self, mock_bounty_cog, make_mock_response):
        """Happy path: response sent public (not ephemeral) with shared embed."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_loadout_response())
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "1"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        # Success path is public (no ephemeral flag)
        assert call_kwargs.get("ephemeral") is not True
        assert "embed" in call_kwargs

    def test_criminal_loadout_embed_has_sections(self, mock_bounty_cog, make_mock_response):
        """Embed contains Active Ship + Ship Stats + Primary Weapons + Modules + Cargo Hold fields."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_loadout_response())
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "1"))

        embed = interaction.followup.send.call_args[1]["embed"]
        names = [f.name for f in embed.fields]
        assert "Active Ship" in names
        assert "Ship Stats" in names
        assert any(n.startswith("Primary Weapons") for n in names)
        assert any(n.startswith("Modules") for n in names)
        # Criminal path ALWAYS shows Cargo Hold <0/M>
        assert any(n.startswith("Cargo Hold") for n in names)

    def test_criminal_loadout_cargo_hold_shows_capacity(self, mock_bounty_cog, make_mock_response):
        """Cargo Hold header format is '<0/M>' where M = ship_stats.cargo."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_loadout_response())  # default cargo=45
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "1"))

        embed = interaction.followup.send.call_args[1]["embed"]
        cargo_field = next(f for f in embed.fields if f.name.startswith("Cargo Hold"))
        assert cargo_field.name == "Cargo Hold <0/45>"

    def test_criminal_loadout_thumbnail_is_criminal_icon(self, mock_bounty_cog, make_mock_response):
        """Thumbnail uses Criminal.icon (thumbnail_url), not ship_icon."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_loadout_response())
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "1"))

        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed.thumbnail.url == "https://cdn/criminal.png"

    def test_criminal_loadout_missing_criminal_ship_sends_ephemeral_error(self, mock_bounty_cog, make_mock_response):
        """message='Criminal ship data unavailable' → red error embed, ephemeral."""
        interaction = _create_mock_interaction()
        data = _make_loadout_response(message="Criminal ship data unavailable")
        resp = make_mock_response(data)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "1"))

        call_kwargs = interaction.followup.send.call_args[1]
        # Errors always ephemeral
        assert call_kwargs.get("ephemeral") is True
        embed = call_kwargs["embed"]
        assert "Criminal ship data unavailable" in (embed.description or "")

    def test_criminal_loadout_invalid_bounty_string_shows_error(self, mock_bounty_cog):
        """/criminal-loadout with non-numeric bounty string should show error."""
        interaction = _create_mock_interaction()

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "not-a-number"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "invalid" in call_kwargs[0][0].lower()

    def test_criminal_loadout_404_shows_not_found(self, mock_bounty_cog):
        """/criminal-loadout 404 should send bounty not found message."""
        import httpx

        interaction = _create_mock_interaction()
        error_response = MagicMock()
        error_response.status_code = 404
        http_error = httpx.HTTPStatusError("404 Not Found", request=MagicMock(), response=error_response)
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "999"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "not found" in call_kwargs[0][0].lower()

    def test_criminal_loadout_api_error_handled(self, mock_bounty_cog):
        """/criminal-loadout generic exception should show error message."""
        interaction = _create_mock_interaction()
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=RuntimeError("boom"))

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "error occurred" in call_kwargs[0][0].lower()

    def test_criminal_loadout_ship_stats_includes_hp_and_dps(self, mock_bounty_cog, make_mock_response):
        """Ship Stats field includes HP, Armour, Handling, DPS (but NOT cargo)."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_loadout_response())
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "1"))

        embed = interaction.followup.send.call_args[1]["embed"]
        stats_field = next(f for f in embed.fields if f.name == "Ship Stats")
        v = stats_field.value
        assert "Armour: **150**" in v
        assert "HP: **690**" in v
        assert "DPS: **25**" in v
        # Cargo value of 45 must NOT appear in Ship Stats (it's in the Cargo Hold header)
        assert "Cargo" not in v


# ---------------------------------------------------------------------------
# Division autocomplete
# ---------------------------------------------------------------------------


class TestBountiesShowAllParam:
    """Tests for the show_all parameter on /bounties."""

    def test_bounties_show_all_false_title_contains_tier(self, mock_bounty_cog, make_mock_response):
        """/bounties default embed title should contain the player's tier name."""
        interaction = _create_mock_interaction()
        player_resp = make_mock_response({"id": 1, "tier": "Silver"})
        bounty_resp = make_mock_response([_make_bounty_public(1, "SilverFox", "silver")])
        mock_bounty_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=bounty_resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        embed = interaction.followup.send.call_args[1]["embed"]
        assert "Silver Tier" in embed.title

    def test_bounties_show_all_true_title_contains_all_tiers(self, mock_bounty_cog, make_mock_response):
        """/bounties show_all=True embed title should indicate all tiers."""
        interaction = _create_mock_interaction()
        resp = make_mock_response([_make_bounty_public(1, "SilverFox", "silver")])
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction, show_all=True))

        embed = interaction.followup.send.call_args[1]["embed"]
        assert "All Tiers" in embed.title

    def test_bounties_show_all_true_does_not_call_players_endpoint(self, mock_bounty_cog, make_mock_response):
        """/bounties show_all=True must NOT call the /players/ endpoint."""
        interaction = _create_mock_interaction()
        resp = make_mock_response([])
        mock_bounty_cog.http_client.post = AsyncMock()
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction, show_all=True))

        mock_bounty_cog.http_client.post.assert_not_awaited()


# ---------------------------------------------------------------------------
# Error handler callbacks
# ---------------------------------------------------------------------------


class TestErrorHandlers:
    """Tests for the error handler callbacks."""

    def test_check_error_handler_response_not_done(self, mock_bounty_cog):
        """check_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_bounty_cog.check_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_bounties_error_handler_response_not_done(self, mock_bounty_cog):
        """bounties_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_bounty_cog.bounties_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_route_error_handler_response_not_done(self, mock_bounty_cog):
        """route_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_bounty_cog.route_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_criminal_loadout_error_handler_response_not_done(self, mock_bounty_cog):
        """criminal_loadout_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_bounty_cog.criminal_loadout_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_check_error_handler_response_already_done(self, mock_bounty_cog):
        """check_error should NOT send message if response already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_bounty_cog.check_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()


# ===========================================================================
# Gap 4: Discord Embed Rendering Rule Tests — BountyCog
# ===========================================================================


class TestBountiesNoTimestampsInBadLocations:
    """Gap 4: Embed rendering rule — <t:...> Discord timestamps must NOT appear
    in the embed footer or author fields for the /bounties command.
    """

    def _get_bounties_embed(self, mock_bounty_cog, make_mock_response):
        """Helper: trigger /bounties (show_all=True to avoid player resolve call) and return embed."""
        interaction = _create_mock_interaction()
        bounty_list = [
            _make_bounty_public(1, "BlackViper", "bronze"),
        ]
        resp = make_mock_response(bounty_list)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction, show_all=True))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        return call_kwargs.get("embed")

    def test_bounties_no_timestamps_in_footer(self, mock_bounty_cog, make_mock_response):
        """The /bounties embed footer must not contain a Discord timestamp (<t:...) pattern.

        Discord renders <t:...> timestamps in fields and descriptions but NOT in footer
        text where they appear as raw code, confusing users.
        """
        embed = self._get_bounties_embed(mock_bounty_cog, make_mock_response)
        if embed is None:
            return  # embed not sent — skip

        footer = embed.footer
        footer_text = ""
        if footer is not None:
            try:
                footer_text = str(footer.text or "")
            except AttributeError:
                footer_text = str(footer)

        assert "<t:" not in footer_text, (
            f"Discord timestamp found in /bounties embed footer: {footer_text!r}. "
            "Timestamps in footers render as raw text — move them to fields or description."
        )

    def test_bounties_no_timestamps_in_author(self, mock_bounty_cog, make_mock_response):
        """The /bounties embed author field must not contain a Discord timestamp (<t:...) pattern."""
        embed = self._get_bounties_embed(mock_bounty_cog, make_mock_response)
        if embed is None:
            return

        author = embed.author
        author_text = ""
        if author is not None:
            try:
                author_text = str(author.name or "")
            except AttributeError:
                author_text = str(author)

        assert "<t:" not in author_text, (
            f"Discord timestamp found in /bounties embed author: {author_text!r}. "
            "Timestamps in author fields render as raw text."
        )


# ===========================================================================
# _format_combat_summary — unit tests
# ===========================================================================


def _make_combat_result(
    s1_name="Betty",
    s1_raw_hp=95,
    s1_varied_hp=93,
    s1_raw_dps=5.2,
    s1_ttk=18.2,
    s2_name="Pirate Bob",
    s2_raw_hp=120,
    s2_varied_hp=118,
    s2_raw_dps=6.0,
    s2_ttk=15.8,
    is_stalemate=False,
    winner_name="Betty",
    loser_name="Pirate Bob",
):
    """Build a minimal combat_result dict."""
    return {
        "winner_name": winner_name,
        "loser_name": loser_name,
        "is_stalemate": is_stalemate,
        "ship1_stats": {
            "ship_name": s1_name,
            "raw_hp": s1_raw_hp,
            "varied_hp": s1_varied_hp,
            "raw_dps": s1_raw_dps,
            "ttk": s1_ttk,
        },
        "ship2_stats": {
            "ship_name": s2_name,
            "raw_hp": s2_raw_hp,
            "varied_hp": s2_varied_hp,
            "raw_dps": s2_raw_dps,
            "ttk": s2_ttk,
        },
        "variance_percent": 0.05,
    }


class TestFormatCombatSummary:
    """Unit tests for BountyCog._format_combat_summary()."""

    @pytest.fixture(autouse=True)
    def _import_cog(self, mock_bounty_cog):
        """Ensure cog is imported for static method access."""
        self.cog = mock_bounty_cog

    def test_contains_player_ship_name(self):
        """Summary should include the player ship name."""
        combat = _make_combat_result(s1_name="StarFighter")
        result = self.cog._format_combat_summary(combat)
        assert "StarFighter" in result

    def test_contains_criminal_ship_name(self):
        """Summary should include the criminal ship name."""
        combat = _make_combat_result(s2_name="DeathBringer")
        result = self.cog._format_combat_summary(combat)
        assert "DeathBringer" in result

    def test_contains_player_hp_values(self):
        """Summary should include raw_hp and varied_hp for player ship."""
        combat = _make_combat_result(s1_raw_hp=200, s1_varied_hp=195)
        result = self.cog._format_combat_summary(combat)
        assert "200" in result
        assert "195" in result

    def test_contains_criminal_hp_values(self):
        """Summary should include raw_hp and varied_hp for criminal ship."""
        combat = _make_combat_result(s2_raw_hp=300, s2_varied_hp=290)
        result = self.cog._format_combat_summary(combat)
        assert "300" in result
        assert "290" in result

    def test_contains_player_dps(self):
        """Summary should include DPS for player ship formatted to 1 decimal."""
        combat = _make_combat_result(s1_raw_dps=12.5)
        result = self.cog._format_combat_summary(combat)
        assert "12.5" in result

    def test_contains_criminal_dps(self):
        """Summary should include DPS for criminal ship formatted to 1 decimal."""
        combat = _make_combat_result(s2_raw_dps=8.3)
        result = self.cog._format_combat_summary(combat)
        assert "8.3" in result

    def test_contains_player_ttk(self):
        """Summary should show time to kill for player ship."""
        combat = _make_combat_result(s1_ttk=18.2)
        result = self.cog._format_combat_summary(combat)
        assert "18.2s" in result

    def test_contains_criminal_ttk(self):
        """Summary should show time to kill for criminal ship."""
        combat = _make_combat_result(s2_ttk=15.8)
        result = self.cog._format_combat_summary(combat)
        assert "15.8s" in result

    def test_ttk_none_shown_as_infinity(self):
        """When ttk is None (can never kill), summary should show '∞'."""
        combat = _make_combat_result(s1_ttk=None, s2_ttk=None)
        result = self.cog._format_combat_summary(combat)
        assert "∞" in result

    def test_stalemate_shows_stalemate_result(self):
        """When is_stalemate is True, summary should include 'Stalemate'."""
        combat = _make_combat_result(is_stalemate=True)
        result = self.cog._format_combat_summary(combat)
        assert "Stalemate" in result

    def test_no_stalemate_text_when_not_stalemate(self):
        """When is_stalemate is False, summary should NOT include 'Stalemate'."""
        combat = _make_combat_result(is_stalemate=False)
        result = self.cog._format_combat_summary(combat)
        assert "Stalemate" not in result

    def test_empty_combat_dict_returns_string(self):
        """_format_combat_summary with an empty dict should return a string (no crash)."""
        result = self.cog._format_combat_summary({})
        assert isinstance(result, str)
        # Should show '?' for unknown ship names
        assert "?" in result

    def test_missing_ship_stats_uses_defaults(self):
        """When ship_stats dicts are missing, defaults (0, '?') should be used."""
        combat = {"is_stalemate": False}
        result = self.cog._format_combat_summary(combat)
        # HP/DPS defaults to 0
        assert "0" in result
        # Ship name defaults to '?'
        assert "?" in result

    def test_both_ships_labelled(self):
        """Summary should label 'Your Ship' and 'Criminal Ship'."""
        combat = _make_combat_result()
        result = self.cog._format_combat_summary(combat)
        assert "Your Ship" in result
        assert "Criminal Ship" in result


# ===========================================================================
# _build_check_embed — new result types
# ===========================================================================


class TestBuildCheckEmbedNewResultTypes:
    """Tests for _build_check_embed() with the new combat result types."""

    @pytest.fixture(autouse=True)
    def _import_cog(self, mock_bounty_cog):
        self.cog = mock_bounty_cog

    def _call(self, data: dict):
        """Call _build_check_embed with given data dict."""
        return self.cog._build_check_embed(data)

    # --- "captured" (Bronze) ---

    def test_captured_bonus_won_green_embed(self):
        """'captured' with bonus_won=True should produce a tier-colored embed (Sub-task A)."""
        from cogs.bountyCog import TIER_COLORS

        embed = self._call(
            {
                "result": "captured",
                "criminal_name": "Pirate Bob",
                "reward": 500,
                "total_reward": 1000,
                "bonus_won": True,
                "combat_result": None,
                "division": "bronze",
            }
        )
        assert embed.color.value == TIER_COLORS["bronze"]

    def test_captured_bonus_won_shows_2x_reward(self):
        """'captured' with bonus_won=True should show total_reward with '2×' label."""
        embed = self._call(
            {
                "result": "captured",
                "criminal_name": "Pirate Bob",
                "reward": 500,
                "total_reward": 1000,
                "bonus_won": True,
                "combat_result": None,
            }
        )
        reward_field = next(f for f in embed.fields if "Reward" in f.name)
        assert "1,000" in reward_field.value
        assert "2×" in reward_field.value

    def test_captured_no_bonus_shows_base_reward_only(self):
        """'captured' with bonus_won=False should show base reward without 2× label."""
        embed = self._call(
            {
                "result": "captured",
                "criminal_name": "Pirate Bob",
                "reward": 500,
                "total_reward": 500,
                "bonus_won": False,
                "combat_result": None,
            }
        )
        reward_field = next(f for f in embed.fields if "Reward" in f.name)
        assert "500" in reward_field.value
        assert "2×" not in reward_field.value

    def test_captured_with_combat_result_shows_combat_summary(self):
        """'captured' with combat_result should include a Combat Summary field."""
        combat = _make_combat_result()
        embed = self._call(
            {
                "result": "captured",
                "criminal_name": "Pirate Bob",
                "reward": 500,
                "total_reward": 500,
                "bonus_won": False,
                "combat_result": combat,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert any("Combat Summary" in n for n in field_names)

    def test_captured_without_combat_result_no_combat_summary(self):
        """'captured' without combat_result should NOT include a Combat Summary field."""
        embed = self._call(
            {
                "result": "captured",
                "criminal_name": "Pirate Bob",
                "reward": 500,
                "total_reward": 500,
                "bonus_won": False,
                "combat_result": None,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert not any("Combat Summary" in n for n in field_names)

    def test_captured_title_says_bounty_captured(self):
        """'captured' embed title should be '🎯 Bounty Captured!'."""
        embed = self._call({"result": "captured", "criminal_name": "Pirate Bob", "reward": 500, "bonus_won": False})
        assert "Bounty Captured" in embed.title

    def test_captured_description_includes_criminal_name(self):
        """'captured' embed description should mention the criminal name."""
        embed = self._call({"result": "captured", "criminal_name": "Warlord Kane", "reward": 500, "bonus_won": False})
        assert "Warlord Kane" in embed.description

    # --- "combat_win" (Silver+) ---

    def test_combat_win_green_embed(self):
        """'combat_win' should produce a tier-colored embed (Sub-task A)."""
        from cogs.bountyCog import TIER_COLORS

        embed = self._call(
            {
                "result": "combat_win",
                "division": "silver",
                "criminal_name": "Pirate Bob",
                "reward": 2000,
                "combat_result": None,
            }
        )
        assert embed.color.value == TIER_COLORS["silver"]

    def test_combat_win_title(self):
        """'combat_win' embed title should say 'Combat Victory!'."""
        embed = self._call(
            {"result": "combat_win", "criminal_name": "Pirate Bob", "reward": 2000, "combat_result": None}
        )
        assert "Combat Victory" in embed.title

    def test_combat_win_shows_reward(self):
        """'combat_win' should show the reward amount."""
        embed = self._call(
            {"result": "combat_win", "criminal_name": "Pirate Bob", "reward": 2000, "combat_result": None}
        )
        reward_field = next(f for f in embed.fields if "Reward" in f.name)
        assert "2,000" in reward_field.value

    def test_combat_win_description_includes_criminal_name(self):
        """'combat_win' description should mention the criminal name."""
        embed = self._call(
            {"result": "combat_win", "criminal_name": "Shadow Wing", "reward": 3000, "combat_result": None}
        )
        assert "Shadow Wing" in embed.description

    def test_combat_win_with_combat_result_shows_summary(self):
        """'combat_win' with combat_result should include Combat Summary field."""
        combat = _make_combat_result()
        embed = self._call(
            {"result": "combat_win", "criminal_name": "Pirate Bob", "reward": 2000, "combat_result": combat}
        )
        field_names = [f.name for f in embed.fields]
        assert any("Combat Summary" in n for n in field_names)

    def test_combat_win_without_combat_result_no_summary(self):
        """'combat_win' without combat_result should NOT include Combat Summary field."""
        embed = self._call(
            {"result": "combat_win", "criminal_name": "Pirate Bob", "reward": 2000, "combat_result": None}
        )
        field_names = [f.name for f in embed.fields]
        assert not any("Combat Summary" in n for n in field_names)

    # --- "combat_loss" (Silver+) ---

    def test_combat_loss_dark_red_embed(self):
        """'combat_loss' should produce a dark_red embed."""
        import discord

        embed = self._call(
            {
                "result": "combat_loss",
                "division": "silver",
                "criminal_name": "Pirate Bob",
                "combat_result": None,
            }
        )
        assert embed.color == discord.Color.dark_red()

    def test_combat_loss_title(self):
        """'combat_loss' embed title should say 'Combat Defeat!'."""
        embed = self._call({"result": "combat_loss", "criminal_name": "Pirate Bob", "combat_result": None})
        assert "Combat Defeat" in embed.title

    def test_combat_loss_description_includes_criminal_name(self):
        """'combat_loss' description should mention the criminal and note reset."""
        embed = self._call({"result": "combat_loss", "criminal_name": "Iron Fist", "combat_result": None})
        assert "Iron Fist" in embed.description
        assert "reset" in embed.description.lower()

    def test_combat_loss_with_combat_result_shows_summary(self):
        """'combat_loss' with combat_result should include Combat Summary field."""
        combat = _make_combat_result()
        embed = self._call({"result": "combat_loss", "criminal_name": "Pirate Bob", "combat_result": combat})
        field_names = [f.name for f in embed.fields]
        assert any("Combat Summary" in n for n in field_names)

    def test_combat_loss_without_combat_result_no_summary(self):
        """'combat_loss' without combat_result should NOT include Combat Summary field."""
        embed = self._call({"result": "combat_loss", "criminal_name": "Pirate Bob", "combat_result": None})
        field_names = [f.name for f in embed.fields]
        assert not any("Combat Summary" in n for n in field_names)

    def test_combat_loss_no_reward_field(self):
        """'combat_loss' should NOT include a Reward field (player earned nothing)."""
        embed = self._call({"result": "combat_loss", "criminal_name": "Pirate Bob", "combat_result": None})
        field_names = [f.name for f in embed.fields]
        assert not any("Reward" in n for n in field_names)

    # --- legacy result types still work ---

    def test_existing_correct_result_still_works(self):
        """Legacy 'correct' result uses tier color (bronze when division provided)."""
        from cogs.bountyCog import TIER_COLORS

        embed = self._call({"result": "correct", "system_name": "Sol", "message": "Found!", "division": "bronze"})
        assert embed.color.value == TIER_COLORS["bronze"]

    def test_existing_incorrect_result_still_works(self):
        """Legacy 'incorrect' result uses tier color (silver when division provided)."""
        from cogs.bountyCog import TIER_COLORS

        embed = self._call({"result": "incorrect", "system_name": "Sol", "message": "Not here.", "division": "silver"})
        assert embed.color.value == TIER_COLORS["silver"]

    def test_existing_already_checked_result_still_works(self):
        """Legacy 'already_checked' result uses tier color (gold when division provided)."""
        from cogs.bountyCog import TIER_COLORS

        embed = self._call(
            {"result": "already_checked", "system_name": "Sol", "message": "Already done.", "division": "gold"}
        )
        assert embed.color.value == TIER_COLORS["gold"]

    def test_existing_unknown_result_still_works(self):
        """Unknown result type should fall back to orange embed."""
        import discord

        embed = self._call({"result": "some_unknown_result", "system_name": "Sol", "message": ""})
        assert embed.color == discord.Color.orange()


# ===========================================================================
# /check command — new combat result types end-to-end
# ===========================================================================


class TestCheckCommandCombatResults:
    """Tests for /check command with new combat result types."""

    @pytest.fixture(autouse=True)
    def _patch_player_id(self, mock_bounty_cog):
        """Patch _get_player_id to return a valid game player ID."""
        mock_bounty_cog._get_player_id = AsyncMock(return_value=42)

    @pytest.fixture(autouse=True)
    def _populate_systems(self, mock_bounty_cog):
        """Populate _systems so resolve_system_name can resolve 'Alpha'."""
        mock_bounty_cog._systems_cache.set("all", ["Alpha"])

    def _make_full_check_response(self, **kwargs):
        """Build a complete check response dict with sensible defaults."""
        base = {
            "result": "captured",
            "division": "bronze",
            "criminal_name": "Pirate Bob",
            "reward": 500,
            "total_reward": 500,
            "bonus_won": False,
            "combat_result": None,
            "message": "",
        }
        base.update(kwargs)
        return base

    def test_check_captured_bonus_won_sends_green_embed(self, mock_bounty_cog, make_mock_response):
        """/check with result='captured' and bonus_won=True sends tier-colored embed (Sub-task A)."""
        from cogs.bountyCog import TIER_COLORS

        interaction = _create_mock_interaction()
        resp = make_mock_response(
            self._make_full_check_response(
                result="captured",
                bonus_won=True,
                total_reward=1000,
                reward=500,
            )
        )
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        # division="bronze" from _make_full_check_response defaults
        assert embed.color.value == TIER_COLORS["bronze"]


# ===========================================================================
# Tests: _build_check_embed — result="correct" with combat_won field
# (The primary fix: bronze/silver/gold/platinum captures all return result="correct")
# ===========================================================================


class TestBuildCheckEmbedCorrectResultWithCombatWon:
    """Tests for _build_check_embed() with result='correct' and combat_won field.

    Bot-core always returns result='correct' for a successful system check.
    The combat_won field distinguishes:
    - combat_won=True (or None): capture succeeded
    - combat_won=False: player lost, criminal escaped
    """

    @pytest.fixture(autouse=True)
    def _import_cog(self, mock_bounty_cog):
        self.cog = mock_bounty_cog

    def _call(self, data: dict):
        """Call _build_check_embed with given data dict."""
        return self.cog._build_check_embed(data)

    # --- combat_won=True: Successful capture ---

    def test_correct_combat_won_true_green_embed(self):
        """result='correct' + combat_won=True → tier-colored embed (Sub-task A)."""
        from cogs.bountyCog import TIER_COLORS

        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1000,
                "combat_won": True,
                "division": "gold",
            }
        )
        assert embed.color.value == TIER_COLORS["gold"]

    def test_correct_combat_won_true_title_says_bounty_captured(self):
        """result='correct' + combat_won=True → title contains 'Bounty Captured'."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1000,
                "combat_won": True,
            }
        )
        assert "Bounty Captured" in embed.title

    def test_correct_combat_won_true_description_includes_criminal_name(self):
        """result='correct' + combat_won=True → description mentions criminal."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "reward": 2000,
                "combat_won": True,
            }
        )
        assert "Iron Fist" in embed.description

    def test_correct_combat_won_true_shows_base_reward(self):
        """result='correct' + combat_won=True, no bonus → shows base reward only."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1500,
                "combat_won": True,
                "bonus_won": False,
            }
        )
        reward_field = next(f for f in embed.fields if "Reward" in f.name)
        assert "1,500" in reward_field.value
        assert "2×" not in reward_field.value

    def test_correct_combat_won_true_with_bonus_shows_doubled_reward(self):
        """result='correct' + combat_won=True + bonus_won=True → shows total_reward with 2× label."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 500,
                "total_reward": 1000,
                "combat_won": True,
                "bonus_won": True,
            }
        )
        reward_field = next(f for f in embed.fields if "Reward" in f.name)
        assert "1,000" in reward_field.value
        assert "2×" in reward_field.value

    def test_correct_combat_won_true_with_combat_result_shows_summary(self):
        """result='correct' + combat_won=True + combat_result → shows Combat Summary field."""
        combat = _make_combat_result()
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1000,
                "combat_won": True,
                "combat_result": combat,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert any("Combat Summary" in n for n in field_names)

    def test_correct_combat_won_true_without_combat_result_no_summary(self):
        """result='correct' + combat_won=True, no combat_result → no Combat Summary field."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1000,
                "combat_won": True,
                "combat_result": None,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert not any("Combat Summary" in n for n in field_names)

    # --- combat_won=False: Criminal escaped ---

    def test_correct_combat_won_false_dark_red_embed(self):
        """result='correct' + combat_won=False → dark_red embed (player lost)."""
        import discord

        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "combat_won": False,
            }
        )
        assert embed.color == discord.Color.dark_red()

    def test_correct_combat_won_false_title_says_combat_defeat(self):
        """result='correct' + combat_won=False → title contains 'Combat Defeat'."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "combat_won": False,
            }
        )
        assert "Combat Defeat" in embed.title

    def test_correct_combat_won_false_description_includes_criminal_name(self):
        """result='correct' + combat_won=False → description mentions criminal."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Shadow Wing",
                "combat_won": False,
            }
        )
        assert "Shadow Wing" in embed.description

    def test_correct_combat_won_false_description_mentions_reset(self):
        """result='correct' + combat_won=False → description mentions checks reset."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "combat_won": False,
            }
        )
        assert "reset" in embed.description.lower()

    def test_correct_combat_won_false_no_reward_field(self):
        """result='correct' + combat_won=False → no Reward field shown."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "combat_won": False,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert not any("Reward" in n for n in field_names)

    def test_correct_combat_won_false_with_combat_result_shows_summary(self):
        """result='correct' + combat_won=False + combat_result → shows Combat Summary field."""
        combat = _make_combat_result()
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "combat_won": False,
                "combat_result": combat,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert any("Combat Summary" in n for n in field_names)

    def test_correct_combat_won_false_without_combat_result_no_summary(self):
        """result='correct' + combat_won=False, no combat_result → no Combat Summary."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Iron Fist",
                "combat_won": False,
                "combat_result": None,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert not any("Combat Summary" in n for n in field_names)

    # --- combat_won=None (missing): No combat → treat as successful capture ---

    def test_correct_no_combat_won_field_shows_green_embed(self):
        """result='correct' without combat_won → defaults to capture (tier-colored embed, Sub-task A)."""
        from cogs.bountyCog import TIER_COLORS

        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1000,
                "division": "platinum",
            }
        )
        assert embed.color.value == TIER_COLORS["platinum"]

    def test_correct_no_combat_won_shows_reward_field(self):
        """result='correct' without combat_won → shows Reward field."""
        embed = self._call(
            {
                "result": "correct",
                "criminal_name": "Pirate Bob",
                "reward": 1000,
            }
        )
        field_names = [f.name for f in embed.fields]
        assert any("Reward" in n for n in field_names)


# ===========================================================================
# Tests: autocomplete functions with normalize_for_search
# ===========================================================================


class TestBountyCogAutocompleteNormalization:
    """Tests for autocomplete functions using normalize_for_search."""

    @pytest.fixture(autouse=True)
    def _import_cog(self, mock_bounty_cog):
        self.cog = mock_bounty_cog

    def test_system_autocomplete_matches_accented_name(self):
        """system_autocomplete should match unaccented input against accented system names."""
        self.cog._systems_cache.set("all", ["Behén", "N'saan", "Alpha Centauri"])
        choices = asyncio.run(self.cog.system_autocomplete(MagicMock(), "behen"))
        assert any(c.name == "Behén" for c in choices)

    def test_system_autocomplete_preserves_original_name_in_choices(self):
        """system_autocomplete should preserve accented names in Choice.name."""
        self.cog._systems_cache.set("all", ["Behén", "Normal"])
        choices = asyncio.run(self.cog.system_autocomplete(MagicMock(), "behen"))
        # Choice.name should be original with accent; value should also be original
        assert all(c.name == c.value for c in choices)
        matching = [c for c in choices if c.name == "Behén"]
        assert len(matching) == 1

    def test_system_autocomplete_no_match_returns_empty(self):
        """system_autocomplete with unmatched query returns empty list."""
        self.cog._systems_cache.set("all", ["Alpha", "Beta"])
        choices = asyncio.run(self.cog.system_autocomplete(MagicMock(), "zzzzz"))
        assert choices == []


# ===========================================================================
# Tests: /check — cooldown_until timestamp and recently_spotted
# ===========================================================================


class TestCheckCommandCooldownAndRecentlySpotted:
    """Tests for cooldown_until and recently_spotted handling in /check."""

    @pytest.fixture(autouse=True)
    def _patch_player_id(self, mock_bounty_cog):
        mock_bounty_cog._get_player_id = AsyncMock(return_value=42)

    @pytest.fixture(autouse=True)
    def _populate_systems(self, mock_bounty_cog):
        """Populate _systems so resolve_system_name can resolve the test system names."""
        mock_bounty_cog._systems_cache.set("all", ["Sol", "Alpha"])

    def _make_on_cooldown_response(self, cooldown_until=None, message="On cooldown"):
        return {
            "result": "on_cooldown",
            "message": message,
            "cooldown_until": cooldown_until,
        }

    def test_on_cooldown_with_cooldown_until_uses_discord_timestamp(self, mock_bounty_cog, make_mock_response):
        """When result=on_cooldown and cooldown_until is set, message uses <t:X:R> format."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(self._make_on_cooldown_response(cooldown_until=1700000000))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Sol"))

        interaction.followup.send.assert_awaited_once()
        sent_msg = interaction.followup.send.call_args[0][0]
        assert "<t:1700000000:R>" in sent_msg
        assert "check again" in sent_msg.lower()

    def test_on_cooldown_without_cooldown_until_falls_back_to_message(self, mock_bounty_cog, make_mock_response):
        """When result=on_cooldown with no cooldown_until, fallback to the message string."""
        interaction = _create_mock_interaction()
        resp = make_mock_response(
            self._make_on_cooldown_response(cooldown_until=None, message="On cooldown for 60 more seconds")
        )
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Sol"))

        interaction.followup.send.assert_awaited_once()
        sent_msg = interaction.followup.send.call_args[0][0]
        assert "60 more seconds" in sent_msg

    def test_recently_spotted_incorrect_shows_orange_embed(self, mock_bounty_cog, make_mock_response):
        """When result=incorrect and recently_spotted=True, embed uses tier color (Sub-task A)."""
        from cogs.bountyCog import TIER_COLORS

        interaction = _create_mock_interaction()
        resp = make_mock_response(
            {
                "result": "incorrect",
                "message": "Recently spotted here!",
                "recently_spotted": True,
                "division": "bronze",
                "outcomes": [
                    {"result": "incorrect", "recently_spotted": True, "division": "bronze", "criminal_name": "Bob"}
                ],
                "result_count": 1,
            }
        )
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        # Tier color for recently-spotted bronze
        assert embed.color.value == TIER_COLORS["bronze"]
        assert "Recently Spotted" in embed.title

    def test_recently_spotted_false_shows_red_embed(self, mock_bounty_cog, make_mock_response):
        """When result=incorrect and recently_spotted=False, embed uses tier color (Sub-task A)."""
        from cogs.bountyCog import TIER_COLORS

        interaction = _create_mock_interaction()
        resp = make_mock_response(
            {
                "result": "incorrect",
                "message": "No sign of criminal",
                "recently_spotted": False,
                "division": "silver",
                "outcomes": [
                    {"result": "incorrect", "recently_spotted": False, "division": "silver", "criminal_name": "Bob"}
                ],
                "result_count": 1,
            }
        )
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed.color.value == TIER_COLORS["silver"]

    def test_recently_spotted_missing_defaults_to_false(self, mock_bounty_cog, make_mock_response):
        """When recently_spotted key is absent, embed uses tier color (falls back to blue if no division)."""
        import discord

        interaction = _create_mock_interaction()
        resp = make_mock_response(
            {
                "result": "incorrect",
                "message": "No sign of criminal",
                "outcomes": [{"result": "incorrect", "recently_spotted": False, "criminal_name": "Bob"}],
                "result_count": 1,
            }
        )
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        # No division → blue fallback
        assert embed.color.value == discord.Color.blue().value


# ===========================================================================
# B.12 — Multi-bounty consolidated /check reply tests
# ===========================================================================
#
# Verifies the cog handles the new list-shaped response from bot-core (B.12)
# by emitting a single consolidated embed listing each bounty's outcome.


def _make_multi_check_response(outcomes: list[dict], cooldown_until: int | None = None) -> dict:
    """Build a multi-bounty BountyCheckResponse-like dict (B.12 wire shape)."""
    first = outcomes[0] if outcomes else {}
    return {
        "outcomes": outcomes,
        "result_count": len(outcomes),
        # Legacy top-level fields mirror outcomes[0] (for back-compat)
        "result": first.get("result", "not_found"),
        "bounty_id": first.get("bounty_id"),
        "message": first.get("message", ""),
        "new_tier": first.get("new_tier"),
        "division": first.get("division"),
        "criminal_name": first.get("criminal_name"),
        "reward": first.get("reward"),
        "combat_result": first.get("combat_result"),
        "combat_won": first.get("combat_won"),
        "bonus_won": first.get("bonus_won", False),
        "total_reward": first.get("total_reward"),
        "criminal_ship": first.get("criminal_ship"),
        "recently_spotted": first.get("recently_spotted", False),
        "cooldown_until": cooldown_until,
    }


class TestCheckMultiBountyResponse:
    """B.12: /check handles multi-bounty list-shaped responses with a consolidated embed."""

    @pytest.fixture(autouse=True)
    def _patch_player_id(self, mock_bounty_cog):
        mock_bounty_cog._get_player_id = AsyncMock(return_value=42)

    @pytest.fixture(autouse=True)
    def _populate_systems(self, mock_bounty_cog):
        """Populate _systems so resolve_system_name can resolve 'Sol'."""
        mock_bounty_cog._systems_cache.set("all", ["Sol"])

    def test_check_multi_outcomes_renders_consolidated_embed(self, mock_bounty_cog, make_mock_response):
        """When response has >1 outcome, exactly one embed is sent with N fields."""
        interaction = _create_mock_interaction()
        outcomes = [
            {
                "result": "correct",
                "bounty_id": 10,
                "criminal_name": "Alice",
                "combat_won": True,
                "reward": 1000,
                "total_reward": 1000,
                "bonus_won": False,
            },
            {
                "result": "incorrect",
                "bounty_id": 11,
                "criminal_name": "Bob",
                "recently_spotted": False,
                "message": "No sign of Bob at Sol",
            },
            {
                "result": "already_checked",
                "bounty_id": 12,
                "criminal_name": "Carol",
                "message": "System Sol already checked",
            },
        ]
        resp = make_mock_response(_make_multi_check_response(outcomes))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Sol"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        # One field per bounty outcome (combat_result was absent so no extra fields)
        names = [f.name for f in embed.fields]
        assert any("Alice" in n for n in names)
        assert any("Bob" in n for n in names)
        assert any("Carol" in n for n in names)
        # Exactly 3 outcome fields
        assert len(embed.fields) == 3

    def test_check_multi_outcomes_capture_uses_green_color(self, mock_bounty_cog, make_mock_response):
        """Multi-outcome embed with at least one capture is green."""
        import discord

        interaction = _create_mock_interaction()
        outcomes = [
            {
                "result": "correct",
                "bounty_id": 20,
                "criminal_name": "Boss",
                "combat_won": True,
                "reward": 5000,
                "total_reward": 5000,
            },
            {
                "result": "incorrect",
                "bounty_id": 21,
                "criminal_name": "Lackey",
                "recently_spotted": False,
            },
        ]
        resp = make_mock_response(_make_multi_check_response(outcomes))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Sol"))

        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed.color == discord.Color.green()
        # Description mentions the system and outcome count
        assert "Sol" in (embed.description or "")
        assert "2" in (embed.description or "")

    def test_check_multi_outcomes_lists_each_bounty_credit_amount(self, mock_bounty_cog, make_mock_response):
        """Multi-bounty capture embed surfaces each bounty's credit reward independently."""
        interaction = _create_mock_interaction()
        outcomes = [
            {
                "result": "correct",
                "bounty_id": 30,
                "criminal_name": "AlphaCrim",
                "combat_won": True,
                "reward": 1234,
                "total_reward": 1234,
            },
            {
                "result": "correct",
                "bounty_id": 31,
                "criminal_name": "BetaCrim",
                "combat_won": True,
                "reward": 5678,
                "total_reward": 5678,
            },
        ]
        resp = make_mock_response(_make_multi_check_response(outcomes))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Sol"))

        embed = interaction.followup.send.call_args[1]["embed"]
        # Both reward amounts must appear in the embed (formatted with thousands sep)
        all_text = " ".join(f.value for f in embed.fields if f.value)
        assert "1,234" in all_text
        assert "5,678" in all_text

    def test_check_single_outcome_capture_sends_text_confirmation(self, mock_bounty_cog, make_mock_response):
        """Single-outcome capture sends a minimal ephemeral text confirmation, not an embed.

        The full payout detail lives in the public embed posted by _post_capture_payout
        in bot-core; showing an embed here too produced a double-embed for the invoker.
        """
        interaction = _create_mock_interaction()
        outcomes = [
            {
                "result": "correct",
                "bounty_id": 40,
                "criminal_name": "Solo",
                "combat_won": True,
                "reward": 500,
                "total_reward": 500,
                "division": "gold",
            },
        ]
        resp = make_mock_response(_make_multi_check_response(outcomes))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Sol"))

        call_args = interaction.followup.send.call_args
        call_kwargs = call_args[1]
        # Must be plain text (no embed) and ephemeral
        assert "embed" not in call_kwargs
        assert call_kwargs.get("ephemeral") is True
        # Message text is passed as a positional arg
        content = call_args[0][0] if call_args[0] else call_kwargs.get("content", "")
        assert "Solo" in content  # criminal name present in confirmation
        assert "capture" in content.lower()


# ===========================================================================
# DEF-BOUNTY-002 — /check empty _systems passthrough (guard fix)
# ===========================================================================
#
# When _systems is empty (preload failed / not yet complete), the /check handler
# must NOT return "Unknown system" — it must pass the typed value through to
# bot-core unchanged and let bot-core decide whether the name is valid.


class TestCheckCommandEmptySystemsPassthrough:
    """DEF-BOUNTY-002: /check with empty _systems list passes typed value through to bot-core.

    Before the fix, resolve_system_name was called unconditionally; with an empty
    systems list it returned None, causing the handler to immediately reject the
    typed value with "Unknown system". After the fix, the handler guards with
    `if self._systems:` and only calls resolve_system_name when the list is
    populated. When empty, the typed value passes through unmodified.
    """

    @pytest.fixture(autouse=True)
    def _patch_player_id(self, mock_bounty_cog):
        """Patch _get_player_id to return a valid game player ID."""
        mock_bounty_cog._get_player_id = AsyncMock(return_value=42)

    @pytest.fixture(autouse=True)
    def _empty_systems(self, mock_bounty_cog):
        """Ensure _systems is empty (simulates failed preload)."""
        mock_bounty_cog._systems_cache.set("all", [])

    def test_check_empty_systems_does_not_send_unknown_system_error(self, mock_bounty_cog, make_mock_response):
        """/check with _systems=[] must NOT return the 'Unknown system' ephemeral error.

        The guard `if self._systems:` prevents resolve_system_name from running when
        the list is empty, so the typed value passes through to bot-core instead of
        being rejected client-side.
        """
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_check_response("not_found"))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "SomeSystem"))

        # Must have sent exactly one followup — the bot-core response embed, NOT an error.
        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        # The "Unknown system" error path sends a plain string ephemerally;
        # the passthrough path sends an embed (any result type from bot-core).
        assert call_args[1].get("embed") is not None, (
            "Expected an embed from bot-core response, but got an ephemeral plain-text error. "
            "The empty _systems guard is not working — 'Unknown system' was returned instead of passthrough."
        )

    def test_check_empty_systems_does_not_call_resolve_system_name(self, mock_bounty_cog, make_mock_response):
        """/check with _systems=[] must skip the resolve_system_name call entirely.

        Verifies the guard `if self._systems:` prevents the resolution function
        from being called when the systems list has not been loaded.
        """
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_check_response("incorrect"))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        with patch("cogs.bountyCog.resolve_system_name") as mock_resolve:
            asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "AnySystem"))

        # resolve_system_name must NOT have been called when _systems is empty.
        mock_resolve.assert_not_called()

    def test_check_empty_systems_passes_typed_value_to_bot_core(self, mock_bounty_cog, make_mock_response):
        """/check with _systems=[] passes the user's typed string unchanged to the API.

        Verifies that the POST to /bounties/check carries exactly the system name
        the user typed, not a resolved canonical form and not empty/None.
        """
        interaction = _create_mock_interaction()
        resp = make_mock_response(_make_check_response("not_found"))
        mock_bounty_cog.http_client.post = AsyncMock(return_value=resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "UnknownStarSystem"))

        # Verify the POST was called and the typed name was forwarded.
        mock_bounty_cog.http_client.post.assert_awaited_once()
        call_kwargs = mock_bounty_cog.http_client.post.call_args[1]
        assert call_kwargs["json"]["system_name"] == "UnknownStarSystem", (
            f"Expected system_name='UnknownStarSystem' in POST body but got: {call_kwargs['json']!r}"
        )


# ===========================================================================
# /check — guild_not_configured and player_id=None paths
# ===========================================================================


class TestCheckCommandGuildAndPlayerErrors:
    """Tests for /check guild-not-configured and player_id=None error paths."""

    @pytest.fixture(autouse=True)
    def _populate_systems(self, mock_bounty_cog):
        """Populate _systems so resolve_system_name can resolve the test system names."""
        mock_bounty_cog._systems_cache.set("all", ["Alpha"])

    def test_check_guild_not_configured_shows_setup_message(self, mock_bounty_cog):
        """/check guild-not-configured 400 should show setup message."""
        import httpx

        interaction = _create_mock_interaction()
        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Guild not configured"}
        http_error = httpx.HTTPStatusError("400", request=MagicMock(), response=error_response)
        mock_bounty_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "admin_setup" in msg.lower() or "set up" in msg.lower()

    def test_check_player_id_none_shows_player_not_found(self, mock_bounty_cog):
        """/check when _get_player_id returns None should show player not found."""
        interaction = _create_mock_interaction()
        mock_bounty_cog._get_player_id = AsyncMock(return_value=None)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "player" in msg.lower() or "profile" in msg.lower()

    def test_check_unknown_system_when_systems_loaded(self, mock_bounty_cog):
        """/check with unknown system (not in _systems) shows error when _systems is loaded."""
        interaction = _create_mock_interaction()
        # _systems is populated but 'Zeta' is not in it
        mock_bounty_cog._get_player_id = AsyncMock(return_value=42)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Zeta"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "unknown system" in call_kwargs[0][0].lower()

    def test_check_429_status_code_on_response(self, mock_bounty_cog, make_mock_response):
        """/check with HTTP 429 (rate-limited) from the bounties/check POST shows cooldown."""
        import httpx

        interaction = _create_mock_interaction()
        mock_bounty_cog._get_player_id = AsyncMock(return_value=42)

        error_response = MagicMock()
        error_response.status_code = 429
        error_response.json.return_value = {}
        http_error = httpx.HTTPStatusError("429 Too Many Requests", request=MagicMock(), response=error_response)
        mock_bounty_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Alpha"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        assert "cooldown" in call_kwargs[0][0].lower()


# ===========================================================================
# /check — new_tier role update path
# ===========================================================================


class TestCheckCommandTierRoleUpdate:
    """Tests for /check tier role update path (when new_tier is returned)."""

    @pytest.fixture(autouse=True)
    def _patch_player_id(self, mock_bounty_cog):
        """Patch _get_player_id to return a valid game player ID."""
        mock_bounty_cog._get_player_id = AsyncMock(return_value=42)

    @pytest.fixture(autouse=True)
    def _populate_systems(self, mock_bounty_cog):
        """Populate _systems so resolve_system_name can resolve the test system names."""
        mock_bounty_cog._systems_cache.set("all", ["Sol"])

    def test_check_new_tier_triggers_role_update_call(self, mock_bounty_cog, make_mock_response):
        """/check when new_tier is in response should attempt to fetch guild config."""
        interaction = _create_mock_interaction()
        # Set up guild and user with roles
        interaction.guild = MagicMock()
        interaction.guild.get_role = MagicMock(return_value=None)
        interaction.user.roles = []
        interaction.user.add_roles = AsyncMock()
        interaction.user.remove_roles = AsyncMock()

        check_resp_data = {
            "result": "correct",
            "bounty_id": 1,
            "message": "",
            "new_tier": "silver",
            "criminal_name": "Pirate",
            "reward": 500,
            "combat_won": True,
        }
        check_resp = make_mock_response(check_resp_data)

        config_resp_data = {
            "bronze_role_id": 101,
            "silver_role_id": 102,
            "gold_role_id": 103,
            "platinum_role_id": 104,
        }
        config_resp = make_mock_response(config_resp_data)

        mock_bounty_cog.http_client.post = AsyncMock(return_value=check_resp)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Sol"))

        # Verify the config GET was called to look up role IDs
        mock_bounty_cog.http_client.get.assert_awaited_once()
        get_call_url = str(mock_bounty_cog.http_client.get.call_args[0][0])
        assert "config/guild" in get_call_url

    def test_check_new_tier_adds_new_role_when_found(self, mock_bounty_cog, make_mock_response):
        """/check tier update adds new role when it exists and isn't already assigned."""
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()

        new_role = MagicMock()
        new_role.__eq__ = lambda s, other: s is other

        def get_role_side_effect(role_id):
            if role_id == 102:
                return new_role
            return None

        interaction.guild.get_role = MagicMock(side_effect=get_role_side_effect)
        interaction.user.roles = []  # new_role not in roles yet
        interaction.user.add_roles = AsyncMock()
        interaction.user.remove_roles = AsyncMock()

        check_resp_data = {
            "result": "correct",
            "bounty_id": 1,
            "message": "",
            "new_tier": "silver",
            "criminal_name": "Pirate",
            "reward": 500,
            "combat_won": True,
        }
        check_resp = make_mock_response(check_resp_data)
        config_resp = make_mock_response(
            {"silver_role_id": 102, "bronze_role_id": 101, "gold_role_id": 103, "platinum_role_id": 104}
        )

        mock_bounty_cog.http_client.post = AsyncMock(return_value=check_resp)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=config_resp)

        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Sol"))

        interaction.user.add_roles.assert_awaited_once_with(new_role, reason="BountyBot promoted to silver")

    def test_check_new_tier_config_failure_doesnt_crash(self, mock_bounty_cog, make_mock_response):
        """/check when config fetch fails during tier role update should not crash."""
        interaction = _create_mock_interaction()
        interaction.guild = MagicMock()
        interaction.user.roles = []
        interaction.user.add_roles = AsyncMock()

        check_resp_data = {
            "result": "correct",
            "bounty_id": 1,
            "message": "",
            "new_tier": "silver",
            "criminal_name": "Pirate",
            "reward": 500,
            "combat_won": True,
        }
        check_resp = make_mock_response(check_resp_data)

        mock_bounty_cog.http_client.post = AsyncMock(return_value=check_resp)
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=RuntimeError("config service down"))

        # Should not raise — error is swallowed with a warning log
        asyncio.run(mock_bounty_cog.check.callback(mock_bounty_cog, interaction, "Sol"))

        interaction.followup.send.assert_awaited_once()


# ===========================================================================
# /bounties — HTTP error paths
# ===========================================================================


class TestBountiesCommandHttpErrors:
    """Tests for /bounties HTTP error paths."""

    def test_bounties_guild_not_configured_shows_setup_message(self, mock_bounty_cog, make_mock_response):
        """/bounties guild-not-configured 400 (from player resolve) should show setup message."""
        import httpx

        interaction = _create_mock_interaction()
        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Guild not configured"}
        http_error = httpx.HTTPStatusError("400", request=MagicMock(), response=error_response)
        # POST /players/ raises the guild-not-configured error
        mock_bounty_cog.http_client.post = AsyncMock(side_effect=http_error)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "admin_setup" in msg.lower() or "set up" in msg.lower()

    def test_bounties_http_status_error_non_guild_uses_report_api_error(self, mock_bounty_cog, make_mock_response):
        """/bounties non-guild-config HTTP error on GET should send embed via report_api_error."""
        import httpx

        interaction = _create_mock_interaction()
        player_resp = make_mock_response({"id": 1, "tier": "Bronze"})
        mock_bounty_cog.http_client.post = AsyncMock(return_value=player_resp)
        error_response = MagicMock()
        error_response.status_code = 500
        error_response.json.return_value = {}
        http_error = httpx.HTTPStatusError("500 Server Error", request=MagicMock(), response=error_response)
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        embed = call_kwargs.get("embed")
        assert embed is not None
        assert "bot-core" not in (embed.description or "")

    def test_bounties_embed_contains_bounty_fields(self, mock_bounty_cog, make_mock_response):
        """/bounties embed fields include criminal name and reward details."""
        interaction = _create_mock_interaction()
        bounty_list = [
            _make_bounty_public(1, "VoidShadow", "gold", reward=9999, reward_per_sys=1000),
        ]
        player_resp = make_mock_response({"id": 1, "tier": "Gold"})
        bounty_resp = make_mock_response(bounty_list)
        mock_bounty_cog.http_client.post = AsyncMock(return_value=player_resp)
        mock_bounty_cog.http_client.get = AsyncMock(return_value=bounty_resp)

        asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        embed = interaction.followup.send.call_args[1]["embed"]
        field_names = [f.name for f in embed.fields]
        assert any("VoidShadow" in n for n in field_names)


# ===========================================================================
# _is_guild_not_configured helper
# ===========================================================================


class TestIsGuildNotConfigured:
    """Tests for the module-level _is_guild_not_configured helper in bountyCog."""

    def test_returns_true_for_not_configured_400(self, mock_bounty_cog):
        """_is_guild_not_configured returns True for a 400 with 'not configured' detail."""
        import httpx
        from cogs.bountyCog import _is_guild_not_configured

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Guild not configured"}
        exc = httpx.HTTPStatusError("400", request=MagicMock(), response=error_response)
        assert _is_guild_not_configured(exc) is True

    def test_returns_true_for_admin_setup_message(self, mock_bounty_cog):
        """_is_guild_not_configured returns True for a 400 mentioning admin_setup."""
        import httpx
        from cogs.bountyCog import _is_guild_not_configured

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Run /admin_setup first"}
        exc = httpx.HTTPStatusError("400", request=MagicMock(), response=error_response)
        assert _is_guild_not_configured(exc) is True

    def test_returns_false_for_non_400(self, mock_bounty_cog):
        """_is_guild_not_configured returns False for non-400 errors."""
        import httpx
        from cogs.bountyCog import _is_guild_not_configured

        error_response = MagicMock()
        error_response.status_code = 500
        exc = httpx.HTTPStatusError("500", request=MagicMock(), response=error_response)
        assert _is_guild_not_configured(exc) is False

    def test_returns_false_for_other_400(self, mock_bounty_cog):
        """_is_guild_not_configured returns False for 400 without config message."""
        import httpx
        from cogs.bountyCog import _is_guild_not_configured

        error_response = MagicMock()
        error_response.status_code = 400
        error_response.json.return_value = {"detail": "Insufficient credits"}
        exc = httpx.HTTPStatusError("400", request=MagicMock(), response=error_response)
        assert _is_guild_not_configured(exc) is False


# ===========================================================================
# _summarize_outcome_line — all code paths
# ===========================================================================


class TestSummarizeOutcomeLine:
    """Tests for BountyCog._summarize_outcome_line() covering all branches."""

    @pytest.fixture(autouse=True)
    def _import_cog(self, mock_bounty_cog):
        self.cog = mock_bounty_cog

    def test_correct_capture_with_bonus(self):
        """correct + bonus_won=True returns '2× combat bonus!' label."""
        outcome = {
            "result": "correct",
            "criminal_name": "Pirate Bob",
            "combat_won": True,
            "reward": 500,
            "total_reward": 1000,
            "bonus_won": True,
        }
        title, value = self.cog._summarize_outcome_line(outcome)
        assert "Pirate Bob" in title
        assert "2× combat bonus" in value

    def test_correct_capture_without_bonus(self):
        """correct + combat_won=True + no bonus returns reward line without 2×."""
        outcome = {
            "result": "correct",
            "criminal_name": "Pirate Bob",
            "combat_won": True,
            "reward": 500,
            "total_reward": 500,
            "bonus_won": False,
        }
        title, value = self.cog._summarize_outcome_line(outcome)
        assert "Pirate Bob" in title
        assert "500" in value
        assert "2×" not in value

    def test_correct_combat_loss_returns_defeat_line(self):
        """correct + combat_won=False returns combat loss / reset message."""
        outcome = {
            "result": "correct",
            "criminal_name": "Iron Fist",
            "combat_won": False,
            "reward": 0,
        }
        title, value = self.cog._summarize_outcome_line(outcome)
        assert "Iron Fist" in title
        assert "combat loss" in value.lower() or "reset" in value.lower()

    def test_incorrect_recently_spotted(self):
        """incorrect + recently_spotted=True returns 'recently spotted' message."""
        outcome = {
            "result": "incorrect",
            "criminal_name": "Shadow Wing",
            "recently_spotted": True,
        }
        title, value = self.cog._summarize_outcome_line(outcome)
        assert "Shadow Wing" in title
        assert "spotted" in value.lower() or "close" in value.lower()

    def test_incorrect_not_recently_spotted(self):
        """incorrect + recently_spotted=False returns 'bounty not here' message."""
        outcome = {
            "result": "incorrect",
            "criminal_name": "Shadow Wing",
            "recently_spotted": False,
        }
        _title, value = self.cog._summarize_outcome_line(outcome)
        assert "not here" in value.lower() or "checked" in value.lower()

    def test_already_checked(self):
        """already_checked returns 'already checked' message."""
        outcome = {
            "result": "already_checked",
            "criminal_name": "BigBoss",
        }
        title, value = self.cog._summarize_outcome_line(outcome)
        assert "BigBoss" in title
        assert "already" in value.lower() or "checked" in value.lower()

    def test_unknown_result_falls_back_to_criminal_name(self):
        """Unknown result type falls back to criminal name + message."""
        outcome = {
            "result": "some_unknown_result",
            "criminal_name": "Mysterious Villain",
            "bounty_id": 99,
            "message": "Status unknown",
        }
        title, value = self.cog._summarize_outcome_line(outcome)
        assert "Mysterious Villain" in title
        assert "Status unknown" in value or "No bounty here" in value

    def test_unknown_result_without_message_uses_default(self):
        """Unknown result without message uses 'No bounty here.' fallback."""
        outcome = {
            "result": "some_unknown",
            "criminal_name": "X",
            "bounty_id": 1,
        }
        _title, value = self.cog._summarize_outcome_line(outcome)
        assert "No bounty here" in value or value != ""

    def test_missing_criminal_name_falls_back_to_bounty_id(self):
        """When criminal_name is absent, falls back to 'Bounty #{id}' in title."""
        outcome = {
            "result": "incorrect",
            "bounty_id": 42,
            "recently_spotted": False,
        }
        title, _value = self.cog._summarize_outcome_line(outcome)
        # Should include bounty ID fallback
        assert "42" in title or "Bounty" in title


# ===========================================================================
# _build_multi_check_embed — with combat results
# ===========================================================================


class TestBuildMultiCheckEmbedWithCombat:
    """Tests for _build_multi_check_embed() with combat_result in outcomes."""

    @pytest.fixture(autouse=True)
    def _import_cog(self, mock_bounty_cog):
        self.cog = mock_bounty_cog

    def test_multi_embed_with_combat_result_adds_combat_field(self):
        """_build_multi_check_embed adds a combat field for outcomes with combat_result."""
        combat = {
            "winner_name": "Betty",
            "loser_name": "EvilShip",
            "is_stalemate": False,
            "ship1_stats": {
                "ship_name": "Betty",
                "raw_hp": 100,
                "varied_hp": 95,
                "raw_dps": 10.0,
                "ttk": 9.5,
            },
            "ship2_stats": {
                "ship_name": "EvilShip",
                "raw_hp": 80,
                "varied_hp": 75,
                "raw_dps": 8.0,
                "ttk": 12.5,
            },
        }
        outcomes = [
            {
                "result": "correct",
                "bounty_id": 10,
                "criminal_name": "BigBoss",
                "combat_won": True,
                "reward": 1000,
                "total_reward": 1000,
                "bonus_won": False,
                "combat_result": combat,
            }
        ]
        embed = self.cog._build_multi_check_embed("Sol", outcomes)
        field_names = [f.name for f in embed.fields]
        # There should be a combat field for BigBoss
        assert any("Combat" in n for n in field_names)
        assert any("BigBoss" in n for n in field_names)

    def test_multi_embed_any_loss_uses_dark_red(self):
        """_build_multi_check_embed uses dark_red color when any outcome is a combat loss."""
        import discord

        outcomes = [
            {
                "result": "correct",
                "bounty_id": 10,
                "criminal_name": "BigBoss",
                "combat_won": False,
                "reward": 0,
                "combat_result": None,
            },
            {
                "result": "incorrect",
                "bounty_id": 11,
                "criminal_name": "SmallFry",
                "recently_spotted": False,
            },
        ]
        embed = self.cog._build_multi_check_embed("Sol", outcomes)
        assert embed.color == discord.Color.dark_red()

    def test_multi_embed_all_incorrect_uses_blue(self):
        """_build_multi_check_embed uses blue color when no captures or losses."""
        import discord

        outcomes = [
            {
                "result": "incorrect",
                "bounty_id": 10,
                "criminal_name": "Alpha",
                "recently_spotted": False,
            },
            {
                "result": "already_checked",
                "bounty_id": 11,
                "criminal_name": "Beta",
            },
        ]
        embed = self.cog._build_multi_check_embed("Proxima", outcomes)
        assert embed.color == discord.Color.blue()


# ===========================================================================
# /route — HTTP error (non-404)
# ===========================================================================


class TestRouteCommandHttpErrors:
    """Tests for /route non-404 HTTP error path."""

    def test_route_http_status_error_non_404_uses_report_api_error(self, mock_bounty_cog):
        """/route non-404 HTTP error should send embed via report_api_error."""
        import httpx

        interaction = _create_mock_interaction()
        error_response = MagicMock()
        error_response.status_code = 500
        error_response.json.return_value = {}
        http_error = httpx.HTTPStatusError("500 Server Error", request=MagicMock(), response=error_response)
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        embed = call_kwargs.get("embed")
        assert embed is not None
        assert "bot-core" not in (embed.description or "")


# ===========================================================================
# /criminal-loadout — non-404 HTTP error
# ===========================================================================


class TestCriminalLoadoutHttpErrors:
    """Tests for /criminal-loadout non-404 HTTP error path."""

    def test_criminal_loadout_http_status_error_non_404_uses_report_api_error(self, mock_bounty_cog):
        """/criminal-loadout non-404 HTTP error should send embed via report_api_error."""
        import httpx

        interaction = _create_mock_interaction()
        error_response = MagicMock()
        error_response.status_code = 500
        error_response.json.return_value = {}
        http_error = httpx.HTTPStatusError("500 Server Error", request=MagicMock(), response=error_response)
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=http_error)

        asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "1"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        embed = call_kwargs.get("embed")
        assert embed is not None
        assert "bot-core" not in (embed.description or "")


# ===========================================================================
# BountyCog setup function
# ===========================================================================


class TestBountyCogSetup:
    """Tests for the setup function."""

    def test_setup_function(self, mock_bot):
        """setup function should add BountyCog to bot."""
        from cogs.bountyCog import setup

        mock_bot.add_cog = AsyncMock()

        asyncio.run(setup(mock_bot))

        mock_bot.add_cog.assert_awaited_once()


# ===========================================================================
# check_error — sends followup when response already done
# ===========================================================================


class TestCheckErrorHandlerResponseDone:
    """Tests for check_error fallback when response IS already done."""

    def test_check_error_handler_response_already_done_sends_followup(self, mock_bounty_cog):
        """check_error should send followup when response is already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_bounty_cog.check_error(interaction, error))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)


# ===========================================================================
# URL-contract tests using respx — /bounties, /route, /criminal-loadout
# (S9 fix / B.33 remediation)
# ===========================================================================


class TestBountyCommandsRespx:
    """respx-backed URL+method contract tests for bountyCog HTTP calls.

    Verifies the exact bot-core URLs and HTTP methods used by the commands
    that were previously only covered with AsyncMock on http_client.
    Follows the policy in services/discord-gateway/tests/AGENTS.md (B.33 followup).

    URLs verified against bot-core registered routes:
      GET  /api/v1/bounties/               — /bounties list (with ?guild_id= and optional ?division=)
      GET  /api/v1/bounties/{id}/route     — /route bounty route
      GET  /api/v1/bounties/{id}/loadout   — /criminal-loadout criminal loadout
    """

    _BOT_API = "http://bot-core:8000/api/v1"

    def _with_real_client(self, cog, request):
        """Replace cog.http_client with a real httpx.AsyncClient for respx interception.

        Registers a pytest finalizer to close the client after the test so no
        httpx.AsyncClient instances are leaked between tests.
        """
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
        return cog

    # ------------------------------------------------------------------
    # 1. /bounties → GET /api/v1/bounties/ with ?guild_id= param
    # ------------------------------------------------------------------

    def test_bounties_calls_correct_url_default(self, mock_bounty_cog, request):
        """/bounties default must POST /players/ then GET /bounties/?division=<tier>."""
        import httpx
        import respx

        self._with_real_client(mock_bounty_cog, request)
        interaction = _create_mock_interaction(guild_id=987654321)

        player_data = {"id": 1, "tier": "Silver"}
        bounty_list = [_make_bounty_public(1, "SilverViper", "silver")]

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{self._BOT_API}/players/").mock(
                return_value=httpx.Response(200, json=player_data)
            )
            mock_router.get(f"{self._BOT_API}/bounties/").mock(
                return_value=httpx.Response(200, json=bounty_list)
            )

            asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert "Silver Tier" in embed.title

    def test_bounties_calls_correct_url_show_all(self, mock_bounty_cog, request):
        """/bounties show_all=True must GET /bounties/ with NO division param (no POST to /players/)."""
        import httpx
        import respx

        self._with_real_client(mock_bounty_cog, request)
        interaction = _create_mock_interaction(guild_id=987654321)

        bounty_list = [_make_bounty_public(2, "GoldHawk", "gold")]

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{self._BOT_API}/bounties/").mock(return_value=httpx.Response(200, json=bounty_list))

            asyncio.run(mock_bounty_cog.bounties.callback(mock_bounty_cog, interaction, show_all=True))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert "All Tiers" in embed.title

    # ------------------------------------------------------------------
    # 2. /route → GET /api/v1/bounties/{id}/route
    # ------------------------------------------------------------------

    def test_route_calls_correct_url(self, mock_bounty_cog, request):
        """/route must GET /bounties/{id}/route."""
        import httpx
        import respx

        self._with_real_client(mock_bounty_cog, request)
        interaction = _create_mock_interaction()

        route_data = _make_route_response(
            bounty_id=42,
            criminal_name="RouteViper",
            route=["Alpha", "Beta"],
            system_statuses={"Alpha": "checked"},
        )

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{self._BOT_API}/bounties/42/route").mock(
                return_value=httpx.Response(200, json=route_data)
            )

            asyncio.run(mock_bounty_cog.route.callback(mock_bounty_cog, interaction, "42"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert "RouteViper" in embed.title

    # ------------------------------------------------------------------
    # 3. /criminal-loadout → GET /api/v1/bounties/{id}/loadout
    # ------------------------------------------------------------------

    def test_criminal_loadout_calls_correct_url(self, mock_bounty_cog, request):
        """/criminal-loadout must GET /bounties/{id}/loadout."""
        import httpx
        import respx

        self._with_real_client(mock_bounty_cog, request)
        interaction = _create_mock_interaction()

        loadout_data = _make_loadout_response(bounty_id=99, criminal_name="LoadoutBoss")

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{self._BOT_API}/bounties/99/loadout").mock(
                return_value=httpx.Response(200, json=loadout_data)
            )

            asyncio.run(mock_bounty_cog.criminal_loadout.callback(mock_bounty_cog, interaction, "99"))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        # Public (not ephemeral) response for successful loadout
        assert call_kwargs.get("ephemeral") is not True

    # ------------------------------------------------------------------
    # 4. bounty_autocomplete → POST /players/ then GET /bounties/?division=<tier>
    # ------------------------------------------------------------------

    def test_bounty_autocomplete_zero_http_from_cache(self, mock_bounty_cog, request):
        """Phase 6: bounty_autocomplete serves from cache — zero HTTP calls."""
        interaction = _create_mock_interaction()
        guild_id = interaction.guild_id
        user_id = interaction.user.id

        bounty_list = [_make_bounty_public(1, "AutoViper", "bronze")]
        _init_bounty_caches(
            guild_id=guild_id, user_id=user_id, player_tier="Bronze",
            bounties=bounty_list, cog=mock_bounty_cog
        )

        # HTTP must NOT be called — all data from cache
        mock_bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        mock_bounty_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        result = asyncio.run(mock_bounty_cog.bounty_autocomplete(interaction, ""))

        assert len(result) == 1
        assert "AutoViper" in result[0].name
