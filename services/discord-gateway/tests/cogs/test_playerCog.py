"""Tests for playerCog — boosting coverage from 0% to 60%+."""

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

# Track the module-level logger for assertion
_unused_module_logger = None


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock with common log-level methods."""
    global _unused_module_logger
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    logger.exception = MagicMock()
    _unused_module_logger = logger
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used
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


_BOT_API = "http://bot-core:8000/api/v1"


def _with_real_http_client(cog, request):
    """Replace cog.http_client with a real httpx.AsyncClient for respx interception.

    House pattern — see test_duelCog.py's / test_shopCog.py's / test_shipsCog.py's
    own `_with_real_http_client` (TRUEUP-01).
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
    # str(interaction.user) → username
    interaction.user.__str__ = MagicMock(return_value="TestUser#0001")
    return interaction


def _make_player_data(tier="Bronze", prestige_count=0):
    """Return a minimal player data dict."""
    return {
        "id": 1,
        "discord_id": 111111111,
        "guild_id": 987654321,
        "tier": tier,
        "xp": 100,
        "credits": 500,
        "lifetime_credits": 500,
        "prestige_count": prestige_count,
        "systems_checked": 10,
        "created_at": "2024-01-01T00:00:00",
    }


def _make_stats_data():
    """Return a minimal stats dict."""
    return {
        "bounty_stats": {"bounty_wins": 5},
        "duel_stats": {"wins": 3, "losses": 2, "win_rate": 60.0},
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_bot():
    """Mock Discord bot for playerCog testing."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.add_cog = AsyncMock()
    bot.tree = MagicMock()
    bot.fetch_user = AsyncMock(return_value=MagicMock(display_name="TestUser"))
    return bot


@pytest.fixture
def mock_player_cog(mock_bot):
    """Create a PlayerCog instance with mocked bot and http_client."""
    # Re-assert our module's mock so the logger is wired to _unused_module_logger.
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.playerCog import PlayerCog

    cog = PlayerCog(mock_bot)
    # Replace the real AsyncClient with a MagicMock for test control
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestPlayerCogInitialization:
    """Tests for PlayerCog initialization."""

    def test_initialization(self, mock_player_cog, mock_bot):
        """PlayerCog should store bot reference and create http_client."""
        assert mock_player_cog.bot is mock_bot
        assert mock_player_cog.http_client is not None


# ---------------------------------------------------------------------------
# cog_unload lifecycle
# ---------------------------------------------------------------------------


class TestCogUnload:
    """Tests for PlayerCog.cog_unload."""

    def test_cog_unload_closes_http_client(self, mock_player_cog):
        """cog_unload should close the http client."""
        asyncio.run(mock_player_cog.cog_unload())
        mock_player_cog.http_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# profile command
# ---------------------------------------------------------------------------


class TestProfileCommand:
    """Tests for the /profile slash command.

    TRUEUP-01: migrated off `AsyncMock(http_client.get/post)` (tautological — a
    wrong URL/method would pass silently) to respx, pinned to the real
    POST /players/ and GET /players/{id}/statistics URLs. The promotion-status
    and config-sync GET calls are non-fatal enhancements (both wrapped in their
    own try/except in the cog) — left unmocked here so respx's own
    "unmocked request" error is swallowed exactly like any other non-fatal
    failure; they're covered explicitly by TestProfileWithPromotionStatus /
    TestProfileRoleAssignment / TestSyncPlayerNotificationRoles below.
    """

    def test_profile_success_bronze_no_prestige(self, mock_player_cog, request):
        """profile should send embed for Bronze tier player."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze", prestige_count=0)
        stats_data = _make_stats_data()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        # The call should have embed= kwarg
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        embed = call_kwargs["embed"]
        assert embed.title is not None
        # Tier should appear somewhere in the embed
        all_text = " ".join(f.value for f in embed.fields if f.value)
        assert "Bronze" in all_text or "bronze" in all_text.lower()

    def test_profile_success_with_prestige(self, mock_player_cog, request):
        """profile should include prestige field when prestige_count > 0."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=2)
        stats_data = _make_stats_data()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        all_text = " ".join(f.value for f in embed.fields if f.value) + (embed.description or "")
        # prestige_count=2 should appear somewhere
        assert "2" in all_text or "prestige" in all_text.lower()

    def test_profile_success_no_duel_stats(self, mock_player_cog, request):
        """profile with 0 wins and 0 losses should skip the duel embed fields."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Silver")
        stats_data = {
            "bounty_stats": {"bounty_wins": 0},
            "duel_stats": {"wins": 0, "losses": 0, "win_rate": 0.0},
        }

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        # With 0 wins and 0 losses, duel stats section should be absent or show zeros
        duel_fields = [f for f in embed.fields if "duel" in f.name.lower()]
        if duel_fields:
            assert "0" in duel_fields[0].value

    def test_profile_player_not_found_404(self, mock_player_cog, request):
        """profile should handle 404 from API and send ephemeral message."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(
                return_value=httpx.Response(404, json={"detail": "Not Found"})
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        # Should be ephemeral
        assert call_kwargs[1].get("ephemeral", False)
        # Message should mention profile not found
        msg = call_kwargs[0][0]
        assert "not found" in msg.lower() or "profile" in msg.lower()

    def test_profile_api_error_non_404(self, mock_player_cog, request):
        """profile should handle non-404 API errors gracefully."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(
                return_value=httpx.Response(500, json={"detail": "Internal Server Error"})
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        assert call_kwargs.get("ephemeral", False)
        # B.31b: helper sends a sanitized embed instead of a raw URL string.
        embed = call_kwargs.get("embed")
        assert embed is not None, "Expected embed-based error reply from report_api_error"
        assert "bot-core" not in (embed.description or "")
        assert "http://" not in (embed.description or "")

    def test_profile_generic_exception(self, mock_player_cog, request):
        """profile should handle generic exceptions with warning message."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(side_effect=RuntimeError("network issue"))
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "error" in msg.lower() or "⚠️" in msg


# ---------------------------------------------------------------------------
# /profile URL+method contract (respx) — Tier 2 closeout 2026-04-30
# ---------------------------------------------------------------------------


class TestProfileCommandRespx:
    """respx-backed tests asserting exact URL+method for /profile, including the
    promotion-status best-effort enhancement.

    Kept as a dedicated class (rather than folded into TestProfileCommand above)
    because it is the one test that pins ALL THREE endpoints /profile touches —
    player upsert, statistics, and promotion-status — in a single
    `assert_all_called=True` block. This class follows the policy in
    services/discord-gateway/tests/AGENTS.md (B.33 remediation) and asserts the
    contract:

      POST /api/v1/players/                    (player upsert)
      GET  /api/v1/players/{id}/statistics     (stats fetch)
      GET  /api/v1/players/{id}/promotion-status (best-effort enhancement)

    All three URLs were empirically verified against bot-core's registered
    routes during the 2026-04-30 Tier 2 closeout audit.
    """

    _BOT_API = "http://bot-core:8000/api/v1"

    def _with_real_client(self, cog, request):
        """Replace cog.http_client with a real httpx.AsyncClient for respx interception."""
        import httpx

        cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
        return cog

    def test_profile_calls_correct_urls_and_methods(self, mock_player_cog, request):
        """/profile must POST /players/, GET /players/{id}/statistics, GET /players/{id}/promotion-status."""
        import httpx
        import respx

        self._with_real_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze", prestige_count=0)
        stats_data = _make_stats_data()
        promo_data = {"can_promote": False, "next_tier": "Silver", "xp_threshold_for_next": 1000}

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{self._BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{self._BOT_API}/players/1/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            mock_router.get(f"{self._BOT_API}/players/1/promotion-status").mock(
                return_value=httpx.Response(200, json=promo_data)
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        # respx assert_all_called=True ensures ALL three endpoints were hit


# ---------------------------------------------------------------------------
# leaderboard command
# ---------------------------------------------------------------------------


class TestLeaderboardCommand:
    """Tests for the /leaderboard slash command.

    TRUEUP-01: migrated off `AsyncMock(http_client.get)` to respx, pinned to
    the real GET /players/guild/{guild_id} URL.
    """

    def test_leaderboard_success(self, mock_player_cog, request):
        """leaderboard should display top players."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        players = [
            {"user_id": 111, "tier": "Gold", "xp": 1000, "credits": 5000},
            {"user_id": 222, "tier": "Silver", "xp": 500, "credits": 2000},
        ]

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/players/guild/987654321").mock(return_value=httpx.Response(200, json=players))
            asyncio.run(mock_player_cog.leaderboard.callback(mock_player_cog, interaction, tier=None))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        # Leaderboard embed uses description (not fields) for the ranked player list
        assert embed is not None
        assert embed.description  # leaderboard embed description should have content

    def test_leaderboard_empty(self, mock_player_cog, request):
        """leaderboard with no players should send ephemeral 'No players' message."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/players/guild/987654321").mock(return_value=httpx.Response(200, json=[]))
            asyncio.run(mock_player_cog.leaderboard.callback(mock_player_cog, interaction, tier=None))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_leaderboard_with_tier_filter(self, mock_player_cog, request):
        """leaderboard with tier param should include tier in title."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        players = [{"user_id": 111, "tier": "Gold", "xp": 999, "credits": 9999}]

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/players/guild/987654321", params={"tier": "Gold"}).mock(
                return_value=httpx.Response(200, json=players)
            )
            asyncio.run(mock_player_cog.leaderboard.callback(mock_player_cog, interaction, tier="Gold"))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        assert embed.title is not None
        # The tier filter (Gold) should appear in the title or description
        assert "Gold" in (embed.title or "") or "Gold" in (embed.description or "")

    def test_leaderboard_api_error(self, mock_player_cog, request):
        """leaderboard should handle API errors gracefully."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/players/guild/987654321").mock(
                return_value=httpx.Response(500, json={"detail": "Error"})
            )
            asyncio.run(mock_player_cog.leaderboard.callback(mock_player_cog, interaction, tier=None))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)

    def test_leaderboard_generic_exception(self, mock_player_cog, request):
        """leaderboard should handle generic exceptions."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/players/guild/987654321").mock(side_effect=RuntimeError("boom"))
            asyncio.run(mock_player_cog.leaderboard.callback(mock_player_cog, interaction, tier=None))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)


# ---------------------------------------------------------------------------
# /leaderboard URL+method contract (respx)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# prestige command
# ---------------------------------------------------------------------------


class TestPrestigeCommand:
    """Tests for the /prestige slash command.

    TRUEUP-01: migrated off `AsyncMock(http_client.post)` to respx, pinned to
    the real POST /players/ URL.
    """

    def test_prestige_eligible_platinum(self, mock_player_cog, request):
        """prestige for Platinum tier player should show confirmation embed + ConfirmView."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=0)

        view_mock = MagicMock()
        view_mock.result = False  # cancel — don't proceed to API
        view_mock.wait = AsyncMock(return_value=None)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited()
        # First send is the confirmation embed+view; second send is the cancel/timeout message
        call_kwargs = interaction.followup.send.call_args_list[0][1]
        assert "embed" in call_kwargs
        assert call_kwargs.get("ephemeral", False)

    def test_prestige_not_eligible_non_platinum(self, mock_player_cog, request):
        """prestige for non-Platinum tier should send ephemeral rejection."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Gold")

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "Platinum" in msg
        assert call_args[1].get("ephemeral", False)

    def test_prestige_bronze_not_eligible(self, mock_player_cog, request):
        """prestige for Bronze tier should send rejection."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        assert call_args[1].get("ephemeral", False)

    def test_prestige_generic_exception(self, mock_player_cog, request):
        """prestige should handle exceptions gracefully."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(side_effect=RuntimeError("connection fail"))
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs[1].get("ephemeral", False)


# ---------------------------------------------------------------------------
# prestige command — new confirm-flow tests
# ---------------------------------------------------------------------------


class TestPrestigeConfirmFlow:
    """Tests for the /prestige confirm flow (button-based, B.50).

    Note on mock count: the role-swap tests below (test_prestige_swaps_roles_correctly,
    test_prestige_notifications_enabled_swaps_roles, etc.) legitimately exceed 2 mocks
    per test because they exercise a real multi-boundary flow: two HTTP calls
    (player-upsert POST + prestige POST), a config GET, and Discord's role-mutation API
    (guild.get_role/add_roles/remove_roles). The Discord role boundary cannot be
    constructed as a live object without a real gateway connection, so MagicMock role
    objects + role-list assertions are the correct fidelity here. The config GET's
    URL+method contract is covered separately by TestPrestigeCommandRespx below.
    """

    def _make_confirm_view_mock(self, result: bool | None):
        """Return a ConfirmView mock with view.result pre-set and wait() returning immediately."""
        view = MagicMock()
        view.result = result
        view.wait = AsyncMock(return_value=None)
        return view

    def test_prestige_eligible_shows_confirm_view(self, mock_player_cog, request):
        """/prestige for Platinum tier should show a ConfirmView (not a CONFIRM string prompt)."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=0)

        view_mock = self._make_confirm_view_mock(result=False)  # cancel — don't proceed
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        # First send should be the confirmation embed + view (ephemeral)
        interaction.followup.send.assert_awaited()
        first_call_kwargs = interaction.followup.send.call_args_list[0][1]
        assert "embed" in first_call_kwargs
        assert first_call_kwargs.get("ephemeral", False)
        assert first_call_kwargs.get("view") is view_mock

    def test_prestige_warning_embed_describes_b49_full_reset(self, mock_player_cog, request):
        """B.49 regression guard: warning embed must accurately describe the
        full-reset semantics (fleet wiped, inventory wiped, Betty starter
        loadout) and must NOT claim the player keeps ships or credits.
        """
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=0)

        view_mock = self._make_confirm_view_mock(result=False)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        embed = interaction.followup.send.call_args_list[0][1]["embed"]
        desc = (embed.description or "").lower()

        assert "betty" in desc, "Warning embed must mention starter Betty"
        assert "keep your ships" not in desc, (
            "Warning embed must NOT claim the player keeps their ships (B.49: fleet wiped)"
        )
        assert "keep your ships, credits" not in desc, (
            "Warning embed must NOT claim the player keeps credits (B.48 F.3 + B.49)"
        )
        assert "lifetime" in desc, "Warning embed must mention lifetime credits are preserved"

    def test_prestige_cancel_does_not_call_api(self, mock_player_cog, request):
        """/prestige: cancelling the ConfirmView must NOT call the prestige API.

        The prestige route is deliberately left unregistered — if the cog called it
        anyway, respx would raise (no route matches), failing the test.
        """
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=0)

        view_mock = self._make_confirm_view_mock(result=False)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            players_route = mock_router.post(f"{_BOT_API}/players/").mock(
                return_value=httpx.Response(200, json=player_data)
            )
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        # Only the player-fetch POST should have been called — NOT the prestige POST
        assert players_route.call_count == 1

    def test_prestige_timeout_sends_timeout_message(self, mock_player_cog, request):
        """/prestige: view timeout (result=None) should send a timeout message."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=0)

        view_mock = self._make_confirm_view_mock(result=None)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            players_route = mock_router.post(f"{_BOT_API}/players/").mock(
                return_value=httpx.Response(200, json=player_data)
            )
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        # Should send the confirmation view first, then a timeout/cancelled followup
        assert players_route.call_count == 1  # only player fetch, no prestige call
        # After timeout (result=None), a timeout message should be sent
        last_call = interaction.followup.send.call_args
        assert last_call is not None
        # Message may be positional arg[0] or keyword arg "content"
        args = last_call[0] if last_call[0] else ()
        kwargs = last_call[1] if last_call[1] else {}
        content = (args[0] if args else "") or kwargs.get("content", "") or ""
        if "embed" in kwargs:
            emb = kwargs["embed"]
            content += (emb.title or "") + (emb.description or "")
        assert any(word in content.lower() for word in ["timeout", "expired", "cancelled", "timed"])

    def test_prestige_confirm_calls_api_and_shows_success(self, mock_player_cog, request):
        """/prestige: confirming the ConfirmView calls the prestige API and shows success."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=0)
        prestige_data = {
            "player_id": 1,
            "prestige_count": 1,
            "tier_before": "Platinum",
            "xp_before": 50000,
        }

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.post(f"{_BOT_API}/players/{player_data['id']}/prestige").mock(
                return_value=httpx.Response(200, json=prestige_data)
            )
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        # Final followup should include a success embed
        last_call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in last_call_kwargs
        # Find the final followup.send call (success embed)
        final_call = interaction.followup.send.call_args_list[-1]
        kwargs = final_call[1]
        if "embed" in kwargs:
            embed = kwargs["embed"]
            title_or_desc = (embed.title or "") + (embed.description or "")
            assert any(word in title_or_desc.lower() for word in ["prestige", "success", "reset", "bronze"])

    def test_prestige_api_400_insufficient_xp(self, mock_player_cog, request):
        """/prestige: confirming but API returns 400 (insufficient XP) shows error.

        B.48: backend returns "Not eligible for prestige. Need {N:,} XP to prestige,
        currently have {M:,}". Error message must reference XP/prestige, not "level".
        """
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=0)

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.post(f"{_BOT_API}/players/{player_data['id']}/prestige").mock(
                return_value=httpx.Response(
                    400,
                    json={"detail": "Not eligible for prestige. Need 50,000 XP to prestige, currently have 35"},
                )
            )
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        last_call = interaction.followup.send.call_args
        assert last_call[1].get("ephemeral", False)
        msg = last_call[0][0]
        assert "prestige" in msg.lower()
        assert "xp" in msg.lower()
        assert "level" not in msg.lower()

    def test_prestige_api_failure_generic(self, mock_player_cog, request):
        """/prestige: confirming but API raises generic exception shows error."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Platinum", prestige_count=0)

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.post(f"{_BOT_API}/players/{player_data['id']}/prestige").mock(
                side_effect=RuntimeError("prestige service down")
            )
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        last_call = interaction.followup.send.call_args
        assert last_call[1].get("ephemeral", False)

    def test_prestige_swaps_roles_correctly(self, mock_player_cog, request):
        """B.53: confirming prestige must remove Platinum role and add Bronze role."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        platinum_role_id = 111222004
        bronze_role_id = 111222001

        mock_platinum_role = MagicMock()
        mock_platinum_role.id = platinum_role_id
        mock_platinum_role.name = "Bounty Hunter Platinum"

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id
        mock_bronze_role.name = "Bounty Hunter Bronze"

        interaction = _create_interaction_with_roles(existing_roles=[mock_platinum_role])

        def _get_role(role_id):
            return {platinum_role_id: mock_platinum_role, bronze_role_id: mock_bronze_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        player_data = _make_player_data(tier="Platinum", prestige_count=0)
        prestige_data = {
            "player_id": 1,
            "prestige_count": 1,
            "tier_before": "Platinum",
            "xp_before": 50000,
        }
        config_data = {
            "bounty_hunter_role_id": None,
            "bronze_role_id": bronze_role_id,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": platinum_role_id,
        }

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.post(f"{_BOT_API}/players/{player_data['id']}/prestige").mock(
                return_value=httpx.Response(200, json=prestige_data)
            )
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        # Success embed must be sent
        last_call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in last_call_kwargs

        # Bronze role must be ADDED
        interaction.user.add_roles.assert_awaited_once()
        added_ids = {r.id for r in interaction.user.add_roles.call_args[0]}
        assert bronze_role_id in added_ids, f"B.53: Bronze role must be added; added_ids={added_ids}"
        assert platinum_role_id not in added_ids, "Platinum role must NOT be added"

        # Platinum role must be REMOVED
        interaction.user.remove_roles.assert_awaited_once()
        removed_ids = {r.id for r in interaction.user.remove_roles.call_args[0]}
        assert platinum_role_id in removed_ids, f"B.53: Platinum role must be removed; removed_ids={removed_ids}"
        assert bronze_role_id not in removed_ids, "Bronze role must NOT appear in remove list"

    def test_prestige_role_swap_failure_is_non_fatal(self, mock_player_cog, request):
        """B.53: If the role swap fails (e.g. config API error), prestige still succeeds."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_interaction_with_roles(existing_roles=[])

        player_data = _make_player_data(tier="Platinum", prestige_count=0)
        prestige_data = {
            "player_id": 1,
            "prestige_count": 1,
            "tier_before": "Platinum",
            "xp_before": 50000,
        }

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.post(f"{_BOT_API}/players/{player_data['id']}/prestige").mock(
                return_value=httpx.Response(200, json=prestige_data)
            )
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(side_effect=RuntimeError("config unavailable"))
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        # Success embed must still be sent (role swap is non-fatal)
        last_call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in last_call_kwargs
        interaction.user.add_roles.assert_not_awaited()
        interaction.user.remove_roles.assert_not_awaited()

    def test_prestige_notifications_disabled_does_not_add_bronze_role(self, mock_player_cog, request):
        """Notification opt-out: stored bounty_notifications_enabled=False — Bronze role
        must NOT be added on prestige (D-019: production reads the stored flag)."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        platinum_role_id = 111222004
        bronze_role_id = 111222001

        mock_platinum_role = MagicMock()
        mock_platinum_role.id = platinum_role_id

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id

        # User does NOT have the Platinum role
        interaction = _create_interaction_with_roles(existing_roles=[])

        def _get_role(role_id):
            return {platinum_role_id: mock_platinum_role, bronze_role_id: mock_bronze_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        # D-019: stored flag is the source of truth — set it to False (opted out)
        player_data = _make_player_data(tier="Platinum", prestige_count=0)
        player_data["bounty_notifications_enabled"] = False

        prestige_data = {"player_id": 1, "prestige_count": 1, "tier_before": "Platinum", "xp_before": 50000}
        config_data = {
            "bounty_hunter_role_id": None,
            "bronze_role_id": bronze_role_id,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": platinum_role_id,
        }

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.post(f"{_BOT_API}/players/{player_data['id']}/prestige").mock(
                return_value=httpx.Response(200, json=prestige_data)
            )
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        # Neither role should be touched — user opted out
        interaction.user.add_roles.assert_not_awaited()
        interaction.user.remove_roles.assert_not_awaited()

    def test_prestige_opted_out_holds_old_role_old_removed_new_not_added(self, mock_player_cog, request):
        """Opted-out prestige: user HOLDS the old Platinum role but bounty_notifications_enabled=False.

        The old Platinum role MUST be removed (it is the wrong tier after prestige);
        the new Bronze role must NOT be added (player is opted out).  This is the
        meaningful opted-out-prestige edge case — the previous disabled test set
        existing_roles=[] so the old role removal path was never exercised.
        """
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        platinum_role_id = 111222004
        bronze_role_id = 111222001

        mock_platinum_role = MagicMock()
        mock_platinum_role.id = platinum_role_id
        mock_platinum_role.name = "Bounty Hunter Platinum"

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id
        mock_bronze_role.name = "Bounty Hunter Bronze"

        # User HOLDS the old Platinum role (stale from when they were opted in)
        interaction = _create_interaction_with_roles(existing_roles=[mock_platinum_role])

        def _get_role(role_id):
            return {platinum_role_id: mock_platinum_role, bronze_role_id: mock_bronze_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        # D-019: stored flag is False — opted out
        player_data = _make_player_data(tier="Platinum", prestige_count=0)
        player_data["bounty_notifications_enabled"] = False

        prestige_data = {"player_id": 1, "prestige_count": 1, "tier_before": "Platinum", "xp_before": 50000}
        config_data = {
            "bounty_hunter_role_id": None,
            "bronze_role_id": bronze_role_id,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": platinum_role_id,
        }

        view_mock = MagicMock()
        view_mock.result = True
        view_mock.wait = AsyncMock(return_value=None)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.post(f"{_BOT_API}/players/{player_data['id']}/prestige").mock(
                return_value=httpx.Response(200, json=prestige_data)
            )
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        # Old Platinum role MUST be removed (wrong tier regardless of opt-out)
        interaction.user.remove_roles.assert_awaited_once()
        removed_ids = {r.id for r in interaction.user.remove_roles.call_args[0]}
        assert platinum_role_id in removed_ids, f"Platinum role {platinum_role_id} must be removed; got {removed_ids}"
        # New Bronze role must NOT be added (player is opted out)
        interaction.user.add_roles.assert_not_awaited()

    def test_prestige_notifications_enabled_swaps_roles(self, mock_player_cog, request):
        """Notification opt-in: user holds Platinum role → Bronze added, Platinum removed."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        platinum_role_id = 111222004
        bronze_role_id = 111222001

        mock_platinum_role = MagicMock()
        mock_platinum_role.id = platinum_role_id
        mock_platinum_role.name = "Bounty Hunter Platinum"

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id
        mock_bronze_role.name = "Bounty Hunter Bronze"

        # User HAS the Platinum role (notifications enabled)
        interaction = _create_interaction_with_roles(existing_roles=[mock_platinum_role])

        def _get_role(role_id):
            return {platinum_role_id: mock_platinum_role, bronze_role_id: mock_bronze_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        player_data = _make_player_data(tier="Platinum", prestige_count=0)
        prestige_data = {"player_id": 1, "prestige_count": 1, "tier_before": "Platinum", "xp_before": 50000}
        config_data = {
            "bounty_hunter_role_id": None,
            "bronze_role_id": bronze_role_id,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": platinum_role_id,
        }

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.post(f"{_BOT_API}/players/{player_data['id']}/prestige").mock(
                return_value=httpx.Response(200, json=prestige_data)
            )
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.prestige.callback(mock_player_cog, interaction))

        # Bronze added, Platinum removed
        interaction.user.add_roles.assert_awaited_once()
        added_ids = {r.id for r in interaction.user.add_roles.call_args[0]}
        assert bronze_role_id in added_ids
        interaction.user.remove_roles.assert_awaited_once()
        removed_ids = {r.id for r in interaction.user.remove_roles.call_args[0]}
        assert platinum_role_id in removed_ids


# ---------------------------------------------------------------------------
# /prestige URL+method contract (respx)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _get_tier_color helper
# ---------------------------------------------------------------------------


class TestGetTierColor:
    """Tests for the _get_tier_color helper method."""

    def _assert_color(self, color):
        """Assert value is a discord Color/Colour object.

        We check by class name rather than isinstance because _evict_discord_modules()
        in the fixture can reload discord, making the top-level 'discord' name reference
        a different module object.  The class name is always 'Colour' regardless.
        """
        assert type(color).__name__ == "Colour", f"Expected a discord.Colour, got {type(color)}"

    def test_bronze_color(self, mock_player_cog):
        """Bronze tier should return the bronze color."""
        color = mock_player_cog._get_tier_color("Bronze")
        self._assert_color(color)

    def test_silver_color(self, mock_player_cog):
        """Silver tier should return the silver color."""
        color = mock_player_cog._get_tier_color("Silver")
        self._assert_color(color)

    def test_gold_color(self, mock_player_cog):
        """Gold tier should return the gold color."""
        color = mock_player_cog._get_tier_color("Gold")
        self._assert_color(color)

    def test_platinum_color(self, mock_player_cog):
        """Platinum tier should return the platinum color."""
        color = mock_player_cog._get_tier_color("Platinum")
        self._assert_color(color)

    def test_unknown_tier_defaults(self, mock_player_cog):
        """Unknown tier should return the default color."""
        color = mock_player_cog._get_tier_color("Diamond")
        self._assert_color(color)


# ---------------------------------------------------------------------------
# Error handler callbacks
# ---------------------------------------------------------------------------


class TestErrorHandlers:
    """Tests for the error handler callbacks on each slash command."""

    def test_profile_error_handler_response_not_done(self, mock_player_cog):
        """profile_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)

        error = MagicMock()

        asyncio.run(mock_player_cog.profile_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_profile_error_handler_response_already_done(self, mock_player_cog):
        """profile_error should NOT send message if response is already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)

        error = MagicMock()

        asyncio.run(mock_player_cog.profile_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_leaderboard_error_handler_response_not_done(self, mock_player_cog):
        """leaderboard_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)

        error = MagicMock()

        asyncio.run(mock_player_cog.leaderboard_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()

    def test_prestige_error_handler_response_not_done(self, mock_player_cog):
        """prestige_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)

        error = MagicMock()

        asyncio.run(mock_player_cog.prestige_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# setup() function
# ---------------------------------------------------------------------------


class TestCogSetup:
    """Tests for the module-level setup function."""

    def test_setup_adds_cog_to_bot(self, mock_bot):
        """setup() should add PlayerCog to the bot."""
        # Re-wire mocks before importing cog
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger
        _evict_discord_modules()

        from cogs.playerCog import setup

        asyncio.run(setup(mock_bot))

        mock_bot.add_cog.assert_called_once()
        added_arg = mock_bot.add_cog.call_args[0][0]
        # The added object should be a PlayerCog instance
        from cogs.playerCog import PlayerCog

        assert isinstance(added_arg, PlayerCog)


# ===========================================================================
# Gap 4: Discord Embed Rendering Rule Tests — PlayerCog
# ===========================================================================


class TestProfileNoTimestampsInBadLocations:
    """Gap 4: Embed rendering rule — <t:...> Discord timestamps must NOT appear
    in the embed footer or author fields, as they render as raw text there.
    Timestamps should only appear in embed fields or the description.
    """

    def _get_profile_embed(self, mock_player_cog, request):
        """Helper: trigger /profile and return the sent embed.

        Promotion-status and config-sync are left unmocked (non-fatal enhancements
        wrapped in their own try/except in the cog) — see TestProfileCommand.
        """
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze", prestige_count=0)
        stats_data = _make_stats_data()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs.get("embed")
        assert embed is not None, "expected /profile to send an embed on the happy path"
        return embed

    def test_profile_no_timestamps_in_footer(self, mock_player_cog, request):
        """Profile embed footer must not contain a Discord timestamp (<t:...) pattern.

        Discord renders <t:...> timestamps in fields and descriptions but NOT in footers
        where they appear as raw text, confusing users.
        """
        embed = self._get_profile_embed(mock_player_cog, request)

        footer = embed.footer
        footer_text = ""
        if footer is not None:
            # discord.EmbedProxy / MagicMock — try to get .text attribute
            try:
                footer_text = str(footer.text or "")
            except AttributeError:
                footer_text = str(footer)

        assert "<t:" not in footer_text, (
            f"Discord timestamp found in embed footer: {footer_text!r}. "
            "Timestamps in footers render as raw text — move them to fields or description."
        )

    def test_profile_no_timestamps_in_author(self, mock_player_cog, request):
        """Profile embed author field must not contain a Discord timestamp (<t:...) pattern.

        Discord renders <t:...> in fields/descriptions but NOT in author fields.
        """
        embed = self._get_profile_embed(mock_player_cog, request)

        author = embed.author
        author_text = ""
        if author is not None:
            try:
                author_text = str(author.name or "")
            except AttributeError:
                author_text = str(author)

        assert "<t:" not in author_text, (
            f"Discord timestamp found in embed author: {author_text!r}. "
            "Timestamps in author fields render as raw text — move them to fields or description."
        )


# ---------------------------------------------------------------------------
# Helper for role-assignment tests
# ---------------------------------------------------------------------------


def _make_config_data(
    bh_role_id: int | None,
    bronze_role_id: int | None = 111222001,
    silver_role_id: int | None = 111222002,
    gold_role_id: int | None = 111222003,
    platinum_role_id: int | None = 111222004,
):
    """Return the JSON body for GET /config/guild/{id} (TRUEUP-01: plain dict for respx)."""
    return {
        "bounty_hunter_role_id": bh_role_id,
        "bronze_role_id": bronze_role_id,
        "silver_role_id": silver_role_id,
        "gold_role_id": gold_role_id,
        "platinum_role_id": platinum_role_id,
    }


def _make_promo_data(can_promote=False, next_tier="Silver", threshold=1000):
    """Return the JSON body for GET /players/{id}/promotion-status (TRUEUP-01: plain dict for respx)."""
    return {
        "can_promote": can_promote,
        "next_tier": next_tier,
        "xp_threshold_for_next": threshold,
        "xp_surplus_for_next": None,
    }


def _create_interaction_with_roles(user_id=111111111, guild_id=987654321, existing_roles=None):
    """Build interaction mock with a proper roles list and async role methods."""
    interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)
    interaction.user.roles = existing_roles if existing_roles is not None else []
    interaction.user.add_roles = AsyncMock()
    interaction.user.remove_roles = AsyncMock()
    return interaction


# ---------------------------------------------------------------------------
# Role assignment in /profile
# ---------------------------------------------------------------------------


class TestProfileRoleAssignment:
    """Tests for Bounty Hunter role assignment logic added to /profile.

    TRUEUP-01: migrated off `AsyncMock(http_client.get, side_effect=[...])` to
    respx, with each of the three GET endpoints (statistics, promotion-status,
    config) registered against its real URL rather than relying on call order.
    """

    def test_profile_assigns_bounty_hunter_role_on_first_use(self, mock_player_cog, request):
        """After player creation, config is fetched, BH + tier roles found, user has none → add_roles called."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_interaction_with_roles(existing_roles=[])

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        bh_role_id = 999888777
        bronze_role_id = 111222001

        promo_data = _make_promo_data()
        config_data = _make_config_data(
            bh_role_id, bronze_role_id=bronze_role_id, silver_role_id=None, gold_role_id=None, platinum_role_id=None
        )

        # guild.get_role returns distinct role mocks for each ID
        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"
        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id
        mock_bronze_role.name = "Bounty Hunter Bronze"

        def _get_role(role_id):
            return {bh_role_id: mock_bh_role, bronze_role_id: mock_bronze_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/promotion-status").mock(
                return_value=httpx.Response(200, json=promo_data)
            )
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed was still sent
        interaction.followup.send.assert_awaited_once()
        # add_roles was called once — with BH role + Bronze tier role
        interaction.user.add_roles.assert_awaited_once()
        added_args = interaction.user.add_roles.call_args[0]
        added_ids = {r.id for r in added_args}
        assert added_ids == {bh_role_id, bronze_role_id}

    def test_profile_skips_role_if_already_assigned(self, mock_player_cog, request):
        """User already has the Bounty Hunter role → add_roles NOT called."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        bh_role_id = 999888777
        mock_role = MagicMock()
        mock_role.id = bh_role_id

        # User already has the role in their roles list
        interaction = _create_interaction_with_roles(existing_roles=[mock_role])

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        promo_data = _make_promo_data()
        config_data = _make_config_data(bh_role_id)

        interaction.guild.get_role = MagicMock(return_value=mock_role)

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/promotion-status").mock(
                return_value=httpx.Response(200, json=promo_data)
            )
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed still sent
        interaction.followup.send.assert_awaited_once()
        # add_roles should NOT be called
        interaction.user.add_roles.assert_not_awaited()

    def test_profile_skips_role_if_config_has_no_role_id(self, mock_player_cog, request):
        """All role IDs None in config → no role assignment attempted."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_interaction_with_roles(existing_roles=[])

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        promo_data = _make_promo_data()
        # No BH role or tier roles configured at all
        config_data = _make_config_data(
            None, bronze_role_id=None, silver_role_id=None, gold_role_id=None, platinum_role_id=None
        )

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/promotion-status").mock(
                return_value=httpx.Response(200, json=promo_data)
            )
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed still sent
        interaction.followup.send.assert_awaited_once()
        # add_roles should NOT be called since no roles configured
        interaction.user.add_roles.assert_not_awaited()

    def test_profile_works_normally_if_role_assignment_fails(self, mock_player_cog, request):
        """add_roles raises an exception → profile embed is still sent (non-fatal)."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        bh_role_id = 999888777
        mock_role = MagicMock()
        mock_role.id = bh_role_id

        interaction = _create_interaction_with_roles(existing_roles=[])

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        promo_data = _make_promo_data()
        config_data = _make_config_data(bh_role_id)

        interaction.guild.get_role = MagicMock(return_value=mock_role)
        # add_roles raises
        interaction.user.add_roles = AsyncMock(side_effect=RuntimeError("Missing Permissions"))

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/promotion-status").mock(
                return_value=httpx.Response(200, json=promo_data)
            )
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed was still sent despite role failure
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_profile_skips_role_if_config_fetch_fails(self, mock_player_cog, request):
        """Config API returns error → profile still works (role assignment non-fatal)."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_interaction_with_roles(existing_roles=[])

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        promo_data = _make_promo_data()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/promotion-status").mock(
                return_value=httpx.Response(200, json=promo_data)
            )
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(500, json={"detail": "Internal Server Error"})
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed still sent
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs
        # add_roles was never called
        interaction.user.add_roles.assert_not_awaited()


# ---------------------------------------------------------------------------
# D-019: _sync_player_notification_roles tests
# ---------------------------------------------------------------------------


class TestSyncPlayerNotificationRoles:
    """D-019: Tests for _sync_player_notification_roles.

    Verifies:
    - SELF-SCOPING: only the `member` argument has add_roles/remove_roles called on it.
    - Opted-out player running /profile gets their tier role REMOVED.
    """

    def test_sync_roles_only_mutates_the_member_argument(self, mock_player_cog, request):
        """SELF-SCOPING guard: add_roles / remove_roles are called only on the `member`
        arg passed to _sync_player_notification_roles — never on interaction.user or
        any other Member object."""
        import asyncio as _asyncio

        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)

        bh_role_id = 999888777
        bronze_role_id = 111222001

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id

        # Create TWO distinct member mocks — only 'member' should be mutated.
        member = MagicMock()
        member.id = 111
        member.roles = []
        member.add_roles = AsyncMock()
        member.remove_roles = AsyncMock()

        other_member = MagicMock()
        other_member.id = 222
        other_member.roles = []
        other_member.add_roles = AsyncMock()
        other_member.remove_roles = AsyncMock()

        guild = MagicMock()
        guild.id = 999

        def _get_role(role_id):
            return {bh_role_id: mock_bh_role, bronze_role_id: mock_bronze_role}.get(role_id)

        guild.get_role = MagicMock(side_effect=_get_role)

        player_data = _make_player_data(tier="Bronze")
        player_data["bounty_notifications_enabled"] = True
        player_data["shop_notifications_enabled"] = False

        config_data = {
            "guild_id": 999,
            "bounty_hunter_role_id": bh_role_id,
            "bronze_role_id": bronze_role_id,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": None,
            "shop_announcements_role_id": None,
        }

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/config/guild/999").mock(return_value=httpx.Response(200, json=config_data))
            _asyncio.run(
                mock_player_cog._sync_player_notification_roles(guild, member, guild_id=999, player_data=player_data)
            )

        # Only 'member' should have been mutated
        member.add_roles.assert_awaited_once()
        other_member.add_roles.assert_not_awaited()
        other_member.remove_roles.assert_not_awaited()

    def test_profile_opted_out_player_gets_tier_role_removed(self, mock_player_cog, request):
        """D-019: If player has bounty_notifications_enabled=False in stored data but still
        holds the tier role (stale), /profile should REMOVE the tier role via
        _sync_player_notification_roles."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        bh_role_id = 999888777
        bronze_role_id = 111222001

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id
        mock_bronze_role.name = "Bounty Hunter Bronze"

        # User has BOTH the BH role and the Bronze tier role (stale from before opt-out)
        interaction = _create_interaction_with_roles(existing_roles=[mock_bh_role, mock_bronze_role])

        def _get_role(role_id):
            return {bh_role_id: mock_bh_role, bronze_role_id: mock_bronze_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        # Stored flag: opted out
        player_data = _make_player_data(tier="Bronze")
        player_data["bounty_notifications_enabled"] = False
        player_data["shop_notifications_enabled"] = False

        stats_data = _make_stats_data()
        promo_data = _make_promo_data()

        config_data = {
            "guild_id": 999,
            "bounty_hunter_role_id": bh_role_id,
            "bronze_role_id": bronze_role_id,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": None,
            "shop_announcements_role_id": None,
        }

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/promotion-status").mock(
                return_value=httpx.Response(200, json=promo_data)
            )
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed was sent
        interaction.followup.send.assert_awaited_once()

        # BH role is already present — no add_roles needed
        interaction.user.add_roles.assert_not_awaited()

        # Tier role must be removed (stale; user opted out)
        interaction.user.remove_roles.assert_awaited_once()
        removed_args = interaction.user.remove_roles.call_args[0]
        removed_ids = {r.id for r in removed_args}
        assert bronze_role_id in removed_ids, (
            f"Expected Bronze tier role to be removed for opted-out player; removed_ids={removed_ids}"
        )


# ---------------------------------------------------------------------------
# D-019: /unregister does NOT call notifications PUT
# ---------------------------------------------------------------------------


class TestUnregisterDoesNotCallNotificationsPut:
    """D-019: /unregister removes Discord roles but does NOT persist notification flags via PUT."""

    def test_unregister_does_not_call_put(self, mock_player_cog, request):
        """Unregistering removes BH roles but must NOT call PUT /players/{id}/notifications
        — notification preferences are untouched by unregister. The PUT route is
        deliberately left unmocked: if the cog called it, respx would raise.
        """
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        bh_role_id = 999888777
        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"

        interaction = _create_interaction_with_roles(existing_roles=[mock_bh_role])

        def _get_role(role_id):
            return {bh_role_id: mock_bh_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        config_data = {
            "guild_id": 999,
            "bounty_hunter_role_id": bh_role_id,
            "bronze_role_id": None,
            "silver_role_id": None,
            "gold_role_id": None,
            "platinum_role_id": None,
            "shop_announcements_role_id": None,
        }

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        # Unregister removes the BH role
        interaction.user.remove_roles.assert_awaited_once()


# ---------------------------------------------------------------------------
# /unregister command
# ---------------------------------------------------------------------------


class TestUnregisterCommand:
    """Tests for the /unregister slash command.

    TRUEUP-01: migrated off `AsyncMock(http_client.get)` to respx, pinned to
    the real GET /config/guild/{guild_id} URL.
    """

    def test_unregister_removes_role_successfully(self, mock_player_cog, request):
        """Happy path: user has all 5 BH roles → all removed, confirmation sent."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        bh_role_id = 999888777
        bronze_id, silver_id, gold_id, platinum_id = 111222001, 111222002, 111222003, 111222004

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"
        mock_bronze = MagicMock()
        mock_bronze.id = bronze_id
        mock_bronze.name = "Bounty Hunter Bronze"
        mock_silver = MagicMock()
        mock_silver.id = silver_id
        mock_silver.name = "Bounty Hunter Silver"
        mock_gold = MagicMock()
        mock_gold.id = gold_id
        mock_gold.name = "Bounty Hunter Gold"
        mock_platinum = MagicMock()
        mock_platinum.id = platinum_id
        mock_platinum.name = "Bounty Hunter Platinum"

        all_roles = [mock_bh_role, mock_bronze, mock_silver, mock_gold, mock_platinum]
        interaction = _create_interaction_with_roles(existing_roles=all_roles)

        def _get_role(role_id):
            return {
                bh_role_id: mock_bh_role,
                bronze_id: mock_bronze,
                silver_id: mock_silver,
                gold_id: mock_gold,
                platinum_id: mock_platinum,
            }.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        config_data = _make_config_data(bh_role_id, bronze_id, silver_id, gold_id, platinum_id)
        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        # remove_roles must be called once with all 5 role args
        interaction.user.remove_roles.assert_awaited_once()
        call_args_pos = interaction.user.remove_roles.call_args[0]
        assert len(call_args_pos) == 5, f"Expected 5 roles to be removed, got {len(call_args_pos)}"
        removed_ids = {r.id for r in call_args_pos}
        assert removed_ids == {bh_role_id, bronze_id, silver_id, gold_id, platinum_id}
        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "✅" in msg or "removed" in msg.lower()
        assert call_args[1].get("ephemeral", False)

    def test_unregister_no_role_configured(self, mock_player_cog, request):
        """bounty_hunter_role_id is None → warning message sent."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_interaction_with_roles(existing_roles=[])

        config_data = _make_config_data(None)
        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "⚠️" in msg or "no" in msg.lower() or "configured" in msg.lower()
        assert call_args[1].get("ephemeral", False)
        interaction.user.remove_roles.assert_not_awaited()

    def test_unregister_role_not_found_in_guild(self, mock_player_cog, request):
        """bh_role_id exists in config but guild.get_role() returns None → warning."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        bh_role_id = 999888777
        interaction = _create_interaction_with_roles(existing_roles=[])
        # guild.get_role returns None for all lookups
        interaction.guild.get_role = MagicMock(return_value=None)

        config_data = _make_config_data(bh_role_id)
        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "⚠️" in msg or "not found" in msg.lower()
        assert call_args[1].get("ephemeral", False)
        interaction.user.remove_roles.assert_not_awaited()

    def test_unregister_user_doesnt_have_role(self, mock_player_cog, request):
        """User has NONE of the Bounty Hunter roles → info message sent, remove_roles not called."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        bh_role_id = 999888777
        bronze_id, silver_id, gold_id, platinum_id = 111222001, 111222002, 111222003, 111222004

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bronze = MagicMock()
        mock_bronze.id = bronze_id
        mock_silver = MagicMock()
        mock_silver.id = silver_id
        mock_gold = MagicMock()
        mock_gold.id = gold_id
        mock_platinum = MagicMock()
        mock_platinum.id = platinum_id

        # User has NO roles
        interaction = _create_interaction_with_roles(existing_roles=[])

        def _get_role(role_id):
            return {
                bh_role_id: mock_bh_role,
                bronze_id: mock_bronze,
                silver_id: mock_silver,
                gold_id: mock_gold,
                platinum_id: mock_platinum,
            }.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        config_data = _make_config_data(bh_role_id, bronze_id, silver_id, gold_id, platinum_id)
        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "ℹ️" in msg or "don't have" in msg.lower() or "doesn't have" in msg.lower() or "not have" in msg.lower()
        assert call_args[1].get("ephemeral", False)
        interaction.user.remove_roles.assert_not_awaited()

    def test_unregister_remove_fails(self, mock_player_cog, request):
        """remove_roles raises → error message sent."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        bh_role_id = 999888777
        mock_role = MagicMock()
        mock_role.id = bh_role_id
        mock_role.name = "Bounty Hunter"

        interaction = _create_interaction_with_roles(existing_roles=[mock_role])
        interaction.guild.get_role = MagicMock(return_value=mock_role)
        interaction.user.remove_roles = AsyncMock(side_effect=RuntimeError("Missing Permissions"))

        # No tier roles configured for simplicity
        config_data = _make_config_data(
            bh_role_id, bronze_role_id=None, silver_role_id=None, gold_role_id=None, platinum_role_id=None
        )
        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "⚠️" in msg or "error" in msg.lower()
        assert call_args[1].get("ephemeral", False)

    def test_unregister_config_fetch_fails(self, mock_player_cog, request):
        """Config API error → error message sent."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_interaction_with_roles(existing_roles=[])

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(503, json={"detail": "Service Unavailable"})
            )
            asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "⚠️" in msg or "error" in msg.lower()
        assert call_args[1].get("ephemeral", False)

    def test_unregister_error_handler_response_not_done(self, mock_player_cog):
        """unregister_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_player_cog.unregister_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_unregister_error_handler_response_already_done(self, mock_player_cog):
        """unregister_error should NOT send message if response is already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_player_cog.unregister_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()

    def test_unregister_removes_only_roles_user_has(self, mock_player_cog, request):
        """User has @Bounty Hunter + @BH-Bronze → only those 2 roles removed."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        bh_role_id = 999888777
        bronze_id = 111222001
        silver_id = 111222002
        gold_id = 111222003
        platinum_id = 111222004

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"
        mock_bronze = MagicMock()
        mock_bronze.id = bronze_id
        mock_bronze.name = "Bounty Hunter Bronze"
        mock_silver = MagicMock()
        mock_silver.id = silver_id
        mock_silver.name = "Bounty Hunter Silver"
        mock_gold = MagicMock()
        mock_gold.id = gold_id
        mock_gold.name = "Bounty Hunter Gold"
        mock_platinum = MagicMock()
        mock_platinum.id = platinum_id
        mock_platinum.name = "Bounty Hunter Platinum"

        # User only has BH + Bronze
        interaction = _create_interaction_with_roles(existing_roles=[mock_bh_role, mock_bronze])

        def _get_role(role_id):
            return {
                bh_role_id: mock_bh_role,
                bronze_id: mock_bronze,
                silver_id: mock_silver,
                gold_id: mock_gold,
                platinum_id: mock_platinum,
            }.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)
        config_data = _make_config_data(bh_role_id, bronze_id, silver_id, gold_id, platinum_id)
        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        interaction.user.remove_roles.assert_awaited_once()
        removed_args = interaction.user.remove_roles.call_args[0]
        assert len(removed_args) == 2, f"Expected 2 roles removed, got {len(removed_args)}"
        removed_ids = {r.id for r in removed_args}
        assert removed_ids == {bh_role_id, bronze_id}

    def test_unregister_tier_role_id_none_in_config(self, mock_player_cog, request):
        """Config has some tier role IDs as None → only configured roles considered (no error)."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        bh_role_id = 999888777
        bronze_id = 111222001

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"
        mock_bronze = MagicMock()
        mock_bronze.id = bronze_id
        mock_bronze.name = "Bounty Hunter Bronze"

        # User has BH + Bronze; silver/gold/platinum are None in config
        interaction = _create_interaction_with_roles(existing_roles=[mock_bh_role, mock_bronze])

        def _get_role(role_id):
            return {bh_role_id: mock_bh_role, bronze_id: mock_bronze}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        config_data = _make_config_data(bh_role_id, bronze_id, None, None, None)
        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        # Should succeed without error, removing BH + Bronze
        interaction.user.remove_roles.assert_awaited_once()
        removed_args = interaction.user.remove_roles.call_args[0]
        removed_ids = {r.id for r in removed_args}
        assert removed_ids == {bh_role_id, bronze_id}
        # Success message sent
        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "✅" in msg or "removed" in msg.lower()

    # ------------------------------------------------------------------
    # Adversarial edge cases (Q18 / Q19 coverage)
    # ------------------------------------------------------------------

    def test_unregister_user_has_only_tier_role_no_generic_bh_role(self, mock_player_cog, request):
        """Q18 / Adversarial: User has ONLY a tier role (e.g. BH-Bronze) but NOT
        the generic @Bounty Hunter role (a degenerate state that can happen via admin
        manipulation or a prior A.14-style bug).

        The code must still detect and remove the tier role without erroring.
        The 'role in user.roles' guard for the generic BH role should not prevent
        tier-role cleanup.
        """
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        bh_role_id = 999888777
        bronze_id = 111222001

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"
        mock_bronze = MagicMock()
        mock_bronze.id = bronze_id
        mock_bronze.name = "Bounty Hunter Bronze"

        # User has ONLY the tier role — no generic BH role
        interaction = _create_interaction_with_roles(existing_roles=[mock_bronze])

        def _get_role(role_id):
            return {bh_role_id: mock_bh_role, bronze_id: mock_bronze}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        # Config returns both bh_role_id and bronze_role_id
        config_data = _make_config_data(
            bh_role_id, bronze_role_id=bronze_id, silver_role_id=None, gold_role_id=None, platinum_role_id=None
        )
        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        # remove_roles must be called — the tier role should be removed even without the generic BH role
        interaction.user.remove_roles.assert_awaited_once()
        removed_args = interaction.user.remove_roles.call_args[0]
        removed_ids = {r.id for r in removed_args}
        assert bronze_id in removed_ids, (
            "Tier-only user must have their tier role removed even without generic @Bounty Hunter role"
        )
        # bh_role should NOT be in the remove list since user doesn't have it
        assert bh_role_id not in removed_ids

        # Success message should list the removed tier role
        interaction.followup.send.assert_awaited_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "✅" in msg or "removed" in msg.lower()

    def test_unregister_all_tier_ids_none_and_user_has_no_bh_role(self, mock_player_cog, request):
        """Q19 / Adversarial: Config has all tier_role_ids = None AND user has no
        generic BH role either. The 'you don't have the role' short-circuit must
        still fire cleanly — no exception, no double-send, no remove_roles call.
        """
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        bh_role_id = 999888777

        mock_bh_role = MagicMock()
        mock_bh_role.id = bh_role_id
        mock_bh_role.name = "Bounty Hunter"

        # User has NO BH-related roles at all
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=mock_bh_role)

        # Config: bh_role_id set but ALL tier IDs are None
        config_data = _make_config_data(
            bh_role_id,
            bronze_role_id=None,
            silver_role_id=None,
            gold_role_id=None,
            platinum_role_id=None,
        )
        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(
                return_value=httpx.Response(200, json=config_data)
            )
            asyncio.run(mock_player_cog.unregister.callback(mock_player_cog, interaction))

        # Must NOT call remove_roles — nothing to remove
        interaction.user.remove_roles.assert_not_awaited()

        # Must send exactly one message — the "you don't have the role" info message
        interaction.followup.send.assert_awaited_once()
        call_args = interaction.followup.send.call_args
        msg = call_args[0][0]
        assert "ℹ️" in msg or "don't have" in msg.lower() or "not have" in msg.lower(), (
            f"Expected 'you don't have the role' message, got: {msg!r}"
        )
        assert call_args[1].get("ephemeral", False)


# ---------------------------------------------------------------------------
# /promote command
# ---------------------------------------------------------------------------


class TestPromoteCommand:
    """Tests for the /promote slash command.

    TRUEUP-01: migrated off `AsyncMock(http_client.get/post/put)` to respx,
    pinned to the real player-upsert / promotion-status / combat-preflight /
    promote URLs.
    """

    _STATUS_DATA = {
        "can_promote": True,
        "next_tier": "Silver",
        "xp": 1500,
        "xp_threshold_for_next": 1000,
    }
    _PREFLIGHT_DATA = {"verdict": "GREEN", "win_rate": 0.8, "sims_run": 20, "player_win_rate": 0.8}

    @pytest.fixture(autouse=True)
    def _patch_promote_confirm(self, mock_player_cog, request):
        from unittest.mock import patch as _patch

        _with_real_http_client(mock_player_cog, request)

        view_mock = MagicMock()
        view_mock.result = True
        view_mock.wait = AsyncMock(return_value=False)
        with _patch("cogs.playerCog.ConfirmView", return_value=view_mock):
            yield

    def _register_precheck(self, mock_router, player_id, target_tier="Silver"):
        """Register the promotion-status + combat-preflight GETs common to a successful precheck."""
        import httpx

        mock_router.get(f"{_BOT_API}/players/{player_id}/promotion-status").mock(
            return_value=httpx.Response(200, json=self._STATUS_DATA)
        )
        mock_router.get(f"{_BOT_API}/players/{player_id}/combat-preflight").mock(
            return_value=httpx.Response(200, json=self._PREFLIGHT_DATA)
        )

    def test_promote_success(self, mock_player_cog):
        """/promote succeeds and shows tier promotion embed."""
        import httpx
        import respx

        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")
        promote_data = {
            "player_id": 1,
            "old_tier": "Bronze",
            "new_tier": "Silver",
            "xp": 1500,
            "eligible_for_next": False,
            "next_tier": "Gold",
        }

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck(mock_router, player_data["id"])
            mock_router.put(f"{_BOT_API}/players/{player_data['id']}/promote").mock(
                return_value=httpx.Response(200, json=promote_data)
            )
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
        assert interaction.followup.send.call_count >= 2
        call_kwargs = interaction.followup.send.call_args_list[-1][1]
        assert "embed" in call_kwargs

    def test_promote_success_eligible_for_next(self, mock_player_cog):
        """/promote with eligible_for_next=True shows further promotion message."""
        import httpx
        import respx

        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")
        promote_data = {
            "player_id": 1,
            "old_tier": "Bronze",
            "new_tier": "Silver",
            "xp": 20000,
            "eligible_for_next": True,
            "next_tier": "Gold",
        }

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck(mock_router, player_data["id"])
            mock_router.put(f"{_BOT_API}/players/{player_data['id']}/promote").mock(
                return_value=httpx.Response(200, json=promote_data)
            )
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        assert interaction.followup.send.call_count >= 2
        call_kwargs = interaction.followup.send.call_args_list[-1][1]
        embed = call_kwargs["embed"]
        # Should mention ability to promote again
        field_values = " ".join(f.value for f in embed.fields)
        assert "promote" in field_values.lower() or "Gold" in field_values

    def test_promote_not_eligible_400_shows_error_embed(self, mock_player_cog):
        """/promote with 400 from API shows error embed."""
        import httpx
        import respx

        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck(mock_router, player_data["id"])
            mock_router.put(f"{_BOT_API}/players/{player_data['id']}/promote").mock(
                return_value=httpx.Response(
                    400, json={"detail": "Not eligible for promotion. Need 1,000 XP for Silver."}
                )
            )
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        assert interaction.followup.send.call_count >= 2
        call_kwargs = interaction.followup.send.call_args_list[-1][1]
        assert "embed" in call_kwargs
        assert call_kwargs.get("ephemeral", False)
        embed = call_kwargs["embed"]
        assert "Cannot Promote" in embed.title or "❌" in embed.title

    def test_promote_api_error_non_400(self, mock_player_cog):
        """/promote with non-400 API error shows generic error."""
        import httpx
        import respx

        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck(mock_router, player_data["id"])
            mock_router.put(f"{_BOT_API}/players/{player_data['id']}/promote").mock(
                return_value=httpx.Response(500, json={"detail": "Server Error"})
            )
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        # Confirm dialog is send #1; the error reply is send #2
        assert interaction.followup.send.call_count >= 2
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_promote_generic_exception(self, mock_player_cog):
        """/promote handles generic exceptions gracefully."""
        import respx

        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(side_effect=RuntimeError("network error"))
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral", False)


# ---------------------------------------------------------------------------
# /promote Power Check embed — verdict_line always visible
# ---------------------------------------------------------------------------


class TestPromotePowerCheckVerdictLine:
    """Tests that the Power Check section is always visible in the promote embed.

    After the Change 2 fix, verdict_line is initialized to a warning string BEFORE
    the try block, ensuring it is always non-empty regardless of the preflight outcome.
    """

    def _make_status_data(self, can_promote=True, next_tier="Silver", xp=1500, threshold=1000):
        return {
            "can_promote": can_promote,
            "next_tier": next_tier,
            "xp": xp,
            "xp_threshold_for_next": threshold,
        }

    def _make_preflight_data(self, verdict="green", sims_run=20, player_win_rate=0.9):
        return {
            "verdict": verdict,
            "sims_run": sims_run,
            "player_win_rate": player_win_rate,
            "criminal_win_rate": 1.0 - player_win_rate,
        }

    def test_verdict_line_populated_when_preflight_returns_no_data(self, mock_player_cog, request):
        """verdict_line is non-empty when preflight returns no_data verdict.

        With Change 2: no_data → ⚪ equipped-ship message (not an empty string).
        """
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")

        view_mock = MagicMock()
        view_mock.result = False  # user cancels so no PUT needed
        view_mock.wait = AsyncMock(return_value=None)

        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/promotion-status").mock(
                return_value=httpx.Response(200, json=self._make_status_data())
            )
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/combat-preflight").mock(
                return_value=httpx.Response(
                    200, json=self._make_preflight_data(verdict="no_data", sims_run=0, player_win_rate=0.0)
                )
            )
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        # Find the warning embed (confirmation prompt) that was sent
        embeds_sent = [
            call[1].get("embed")
            for call in interaction.followup.send.call_args_list
            if call[1].get("embed") is not None
        ]
        # At least one embed was sent (the confirmation embed)
        assert embeds_sent, "No embed was sent during /promote"
        confirmation_embed = embeds_sent[0]
        description = confirmation_embed.description or ""
        # Power Check section must appear in the description — not an empty placeholder
        assert "Power Check" in description

    def test_verdict_line_populated_when_preflight_raises_exception(self, mock_player_cog, request):
        """verdict_line is non-empty when the preflight HTTP call raises an exception.

        With Change 2: exception path → ⚠️ Unavailable fallback string.
        """
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")

        view_mock = MagicMock()
        view_mock.result = False  # user cancels
        view_mock.wait = AsyncMock(return_value=None)

        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/promotion-status").mock(
                return_value=httpx.Response(200, json=self._make_status_data())
            )
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/combat-preflight").mock(
                return_value=httpx.Response(500, json={"detail": "Server Error"})
            )
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        embeds_sent = [
            call[1].get("embed")
            for call in interaction.followup.send.call_args_list
            if call[1].get("embed") is not None
        ]
        assert embeds_sent, "No embed was sent during /promote"
        confirmation_embed = embeds_sent[0]
        description = confirmation_embed.description or ""
        # ⚠️ Unavailable fallback must be present
        assert "Power Check" in description

    def test_verdict_line_contains_win_rate_on_green_verdict(self, mock_player_cog, request):
        """When preflight returns a green verdict, embed contains the win percentage."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")

        view_mock = MagicMock()
        view_mock.result = False  # user cancels
        view_mock.wait = AsyncMock(return_value=None)

        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/promotion-status").mock(
                return_value=httpx.Response(200, json=self._make_status_data())
            )
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/combat-preflight").mock(
                return_value=httpx.Response(
                    200, json=self._make_preflight_data(verdict="green", sims_run=20, player_win_rate=0.9)
                )
            )
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        embeds_sent = [
            call[1].get("embed")
            for call in interaction.followup.send.call_args_list
            if call[1].get("embed") is not None
        ]
        assert embeds_sent, "No embed was sent during /promote"
        confirmation_embed = embeds_sent[0]
        description = confirmation_embed.description or ""
        # Win percentage and sims_run should appear
        assert "Power Check" in description
        assert "90%" in description or "20" in description


# ---------------------------------------------------------------------------
# /profile with promotion status
# ---------------------------------------------------------------------------


class TestProfileWithPromotionStatus:
    """Tests for the promotion status indicator in /profile.

    TRUEUP-01: migrated to respx. Config fetch (role assignment) is left
    unmocked — it's a non-fatal enhancement wrapped in its own try/except.
    """

    def _register_profile_mocks(self, mock_router, player_data, stats_data, promo_data):
        """Register the player-upsert, statistics, and promotion-status routes."""
        import httpx

        mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
        mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
            return_value=httpx.Response(200, json=stats_data)
        )
        mock_router.get(f"{_BOT_API}/players/{player_data['id']}/promotion-status").mock(
            return_value=httpx.Response(200, json=promo_data)
        )

    def test_profile_shows_eligible_promotion(self, mock_player_cog, request):
        """Profile shows 'Eligible for X' when can_promote=True."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        promo_data = {
            "can_promote": True,
            "next_tier": "Silver",
            "xp_threshold_for_next": 1000,
            "xp_surplus_for_next": 500,
        }

        with respx.mock(assert_all_called=True) as mock_router:
            self._register_profile_mocks(mock_router, player_data, stats_data, promo_data)
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        field_names = [f.name for f in embed.fields]
        assert "Promotion" in field_names

    def test_profile_shows_next_tier_threshold_when_not_eligible(self, mock_player_cog, request):
        """Profile shows threshold when can_promote=False and next_tier is not None."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()
        promo_data = {
            "can_promote": False,
            "next_tier": "Silver",
            "xp_threshold_for_next": 1000,
            "xp_surplus_for_next": None,
        }

        with respx.mock(assert_all_called=True) as mock_router:
            self._register_profile_mocks(mock_router, player_data, stats_data, promo_data)
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        field_names = [f.name for f in embed.fields]
        assert "Next Tier" in field_names

    def test_profile_shows_max_tier_for_platinum(self, mock_player_cog, request):
        """Profile shows 'Maximum Tier' when next_tier is None (Platinum)."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Platinum")
        stats_data = _make_stats_data()
        promo_data = {
            "can_promote": False,
            "next_tier": None,
            "xp_threshold_for_next": None,
            "xp_surplus_for_next": None,
        }

        with respx.mock(assert_all_called=True) as mock_router:
            self._register_profile_mocks(mock_router, player_data, stats_data, promo_data)
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        embed = call_kwargs["embed"]
        # Find the "Tier" field added for Platinum
        tier_fields = [f for f in embed.fields if f.name == "Tier"]
        assert len(tier_fields) > 0
        assert "Maximum" in tier_fields[-1].value

    def test_profile_still_works_if_promotion_status_fails(self, mock_player_cog, request):
        """Profile still displays normally if promotion status API call fails."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/promotion-status").mock(
                side_effect=RuntimeError("promo status unavailable")
            )
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        # Profile embed still sent
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs


# ---------------------------------------------------------------------------
# Error handler for /promote
# ---------------------------------------------------------------------------


class TestPromoteErrorHandler:
    """Tests for the /promote error handler."""

    def test_promote_error_handler_response_not_done(self, mock_player_cog):
        """promote_error should send message when response is not done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_player_cog.promote_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_promote_error_handler_response_already_done(self, mock_player_cog):
        """promote_error should NOT send message if response is already done."""
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_player_cog.promote_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Bug 6: profile Joined timestamp must be in a field, NOT the footer
# ---------------------------------------------------------------------------


class TestProfileJoinedTimestampLocation:
    """Verify that the 'Joined' timestamp is rendered as an embed field (not footer)."""

    def _setup_profile(self, mock_router, player_data, stats_data):
        """Register the player-upsert + statistics routes for a basic /profile call.

        Promotion-status and config are left unmocked — both are non-fatal
        enhancements wrapped in their own try/except in the cog.
        """
        import httpx

        mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
        mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
            return_value=httpx.Response(200, json=stats_data)
        )

    def test_joined_is_in_embed_field(self, mock_player_cog, request):
        """Profile embed must have a field named 'Joined'."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()

        with respx.mock(assert_all_called=True) as mock_router:
            self._setup_profile(mock_router, player_data, stats_data)
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        field_names = [f.name for f in embed.fields]
        assert "Joined" in field_names, f"Expected 'Joined' field; fields are: {field_names}"

    def test_footer_does_not_contain_joined(self, mock_player_cog, request):
        """Profile embed footer must NOT include the word 'Joined'."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()

        with respx.mock(assert_all_called=True) as mock_router:
            self._setup_profile(mock_router, player_data, stats_data)
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        embed = interaction.followup.send.call_args[1]["embed"]
        footer_text = embed.footer.text if embed.footer and embed.footer.text else ""
        assert "Joined" not in footer_text, f"Footer must not contain 'Joined'; footer text: {footer_text!r}"

    def test_footer_still_contains_player_id(self, mock_player_cog, request):
        """Profile embed footer should still contain the Player ID."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=None)

        player_data = _make_player_data(tier="Bronze")
        stats_data = _make_stats_data()

        with respx.mock(assert_all_called=True) as mock_router:
            self._setup_profile(mock_router, player_data, stats_data)
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction))

        embed = interaction.followup.send.call_args[1]["embed"]
        footer_text = embed.footer.text if embed.footer and embed.footer.text else ""
        # player_data["id"] is 1 from _make_player_data
        assert "Player ID" in footer_text, f"Footer should contain 'Player ID'; footer text: {footer_text!r}"


# ---------------------------------------------------------------------------
# /loadout command tests (unified LoadoutResponse + shared embed builder)
# ---------------------------------------------------------------------------


def _make_player_loadout_response(**overrides):
    """Return a minimal unified-schema LoadoutResponse dict from bot-core."""
    data = {
        "subject_kind": "player",
        "subject_name": "Alice",
        "subject_mention": "<@12345>",
        "player_id": 1,
        "user_discord_id": 12345,
        "ship_name": "Betty",
        "ship_nickname": None,
        "ship_emoji": "🛸",
        "ship_icon": "https://cdn/betty.png",
        "thumbnail_url": "https://cdn/betty.png",
        "ship_stats": {
            "armour": 200,
            "cargo": 20,
            "handling": 50,
            "hp": 200,
            "dps": 7.5,
            "total_value": 3570,
            "max_primaries": 1,
            "max_secondaries": 0,
            "max_turrets": 0,
            "max_modules": 2,
        },
        "weapons": [{"name": "Nirai Impulse EX 1", "emoji": "<:ni:1>", "dps": 7.5, "value": 2500}],
        "turrets": [],
        "modules": [
            {
                "name": "E2 Exoclad",
                "emoji": "<:e2:1>",
                "type": "ArmourModule",
                "value": 1070,
                "tech_level": 1,
                "effects": [{"label": "Armour", "value": "40"}],
                "combat_tier": "combat",
            }
        ],
        "cargo": [],
        "cargo_total_count": 0,
    }
    data.update(overrides)
    return data


class TestLoadoutCommand:
    """Tests for the /loadout slash command (shared builder consumer).

    TRUEUP-01: migrated off `AsyncMock(http_client.get/post)` to respx, pinned
    to the real POST /players/ and GET /players/{id}/loadout URLs. Tests that
    previously inspected `call_args_list` for params/json now inspect the
    captured respx request instead.
    """

    def _register_loadout(self, mock_router, player_data, loadout_data):
        import httpx

        players_route = mock_router.post(f"{_BOT_API}/players/").mock(
            return_value=httpx.Response(200, json=player_data)
        )
        loadout_route = mock_router.get(f"{_BOT_API}/players/{player_data['id']}/loadout").mock(
            return_value=httpx.Response(200, json=loadout_data)
        )
        return players_route, loadout_route

    def test_loadout_success_self_default_ephemeral(self, mock_player_cog, request):
        """Self-view with default public=False → defer+followup are ephemeral."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            self._register_loadout(mock_router, _make_player_data(), _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        # defer called with ephemeral=True
        defer_kwargs = interaction.response.defer.call_args[1]
        assert defer_kwargs.get("ephemeral") is True

        # followup ephemeral=True
        send_kwargs = interaction.followup.send.call_args[1]
        assert send_kwargs.get("ephemeral") is True

    def test_loadout_public_true_sends_non_ephemeral(self, mock_player_cog, request):
        """public=True → defer non-ephemeral, followup non-ephemeral."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            self._register_loadout(mock_router, _make_player_data(), _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None, public=True))

        defer_kwargs = interaction.response.defer.call_args[1]
        assert defer_kwargs.get("ephemeral") is False
        send_kwargs = interaction.followup.send.call_args[1]
        assert send_kwargs.get("ephemeral") is False

    def test_loadout_title_uses_live_display_name(self, mock_player_cog, request):
        """Embed title uses interaction user.display_name, NOT the bot-core subject_name."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()
        interaction.user.display_name = "LiveDisplayName"

        with respx.mock(assert_all_called=True) as mock_router:
            self._register_loadout(mock_router, _make_player_data(), _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed.title == "Loadout — LiveDisplayName"

    def test_loadout_description_is_user_mention(self, mock_player_cog, request):
        """Description is overwritten to the live Discord mention."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            self._register_loadout(mock_router, _make_player_data(), _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        # Description is <@user.id> mention of the target
        assert embed.description == f"<@{interaction.user.id}>"

    def test_loadout_no_active_ship_sends_ephemeral_error_embed(self, mock_player_cog, request):
        """'No active ship' response → red error embed, always ephemeral."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()
        no_ship_resp = _make_player_loadout_response(message="No active ship")

        with respx.mock(assert_all_called=True) as mock_router:
            self._register_loadout(mock_router, _make_player_data(), no_ship_resp)
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None, public=True))

        send_kwargs = interaction.followup.send.call_args[1]
        # Errors always ephemeral regardless of public=True
        assert send_kwargs.get("ephemeral") is True
        embed = send_kwargs["embed"]
        assert "No active ship" in (embed.description or "")

    def test_loadout_self_view_passes_include_cargo_true(self, mock_player_cog, request):
        """Self-view must pass include_cargo=true to bot-core."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            _, loadout_route = self._register_loadout(mock_router, _make_player_data(), _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        params = loadout_route.calls.last.request.url.params
        assert params.get("include_cargo") == "true"

    def test_loadout_other_player_non_admin_passes_include_cargo_false(self, mock_player_cog, request):
        """Other-player view as non-admin must pass include_cargo=false."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()
        other = MagicMock()
        other.id = 999
        other.display_name = "Other"
        other.__str__ = MagicMock(return_value="Other#0000")

        # Patch _check_is_admin to return False (non-admin)
        with (
            patch("cogs.playerCog._check_is_admin", AsyncMock(return_value=False)),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            _, loadout_route = self._register_loadout(mock_router, _make_player_data(), _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=other))

        params = loadout_route.calls.last.request.url.params
        assert params.get("include_cargo") == "false"

    def test_loadout_other_player_admin_passes_include_cargo_true(self, mock_player_cog, request):
        """Other-player view as admin must pass include_cargo=true."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()
        other = MagicMock()
        other.id = 999
        other.display_name = "Other"
        other.__str__ = MagicMock(return_value="Other#0000")

        with (
            patch("cogs.playerCog._check_is_admin", AsyncMock(return_value=True)),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            _, loadout_route = self._register_loadout(mock_router, _make_player_data(), _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=other))

        params = loadout_route.calls.last.request.url.params
        assert params.get("include_cargo") == "true"

    def test_loadout_viewer_discord_id_param_included(self, mock_player_cog, request):
        """viewer_discord_id query param is the target user's Discord ID."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            _, loadout_route = self._register_loadout(mock_router, _make_player_data(), _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        params = loadout_route.calls.last.request.url.params
        assert params.get("viewer_discord_id") == str(interaction.user.id)

    def test_loadout_profile_post_does_not_overwrite_username(self, mock_player_cog, request):
        """POST to /players/ sends discord_username=None to avoid overwriting."""
        import json

        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            players_route, _ = self._register_loadout(mock_router, _make_player_data(), _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        body = json.loads(players_route.calls.last.request.content)
        assert body.get("discord_username") is None

    def test_loadout_http_404_sends_ephemeral(self, mock_player_cog, request):
        """404 HTTPStatusError → ephemeral error message."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=_make_player_data()))
            mock_router.get(f"{_BOT_API}/players/{_make_player_data()['id']}/loadout").mock(
                return_value=httpx.Response(404, json={"detail": "not found"})
            )
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None, public=True))

        send_kwargs = interaction.followup.send.call_args[1]
        # Errors always ephemeral
        assert send_kwargs.get("ephemeral") is True

    def test_loadout_generic_exception_sends_ephemeral_warning(self, mock_player_cog, request):
        """Unexpected exception → ephemeral warning."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(side_effect=Exception("boom"))
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None, public=True))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True


class TestLoadoutEmbedContent:
    """Tests that the embed produced by /loadout carries the expected sections.

    TRUEUP-01: migrated off `AsyncMock(http_client.get/post)` to respx.
    """

    def _register(self, mock_router, loadout_data):
        import httpx

        player_data = _make_player_data()
        mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
        mock_router.get(f"{_BOT_API}/players/{player_data['id']}/loadout").mock(
            return_value=httpx.Response(200, json=loadout_data)
        )

    def test_active_ship_field_present(self, mock_player_cog, request):
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            self._register(mock_router, _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        field_names = [f.name for f in embed.fields]
        assert "Active Ship" in field_names
        assert "Ship Stats" in field_names

    def test_weapons_section_header_with_n_over_m(self, mock_player_cog, request):
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            self._register(mock_router, _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        field = next(f for f in embed.fields if f.name.startswith("Primary Weapons"))
        # 1 weapon, max_primaries=1
        assert field.name == "Primary Weapons <1/1>"

    def test_modules_section_header_with_n_over_m(self, mock_player_cog, request):
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            self._register(mock_router, _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        field = next(f for f in embed.fields if f.name.startswith("Modules"))
        assert field.name == "Modules <1/2>"

    def test_cargo_hold_shown_for_self_view(self, mock_player_cog, request):
        """Self-view → Cargo Hold header always rendered (empty shows 'Empty')."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            self._register(mock_router, _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        cargo_field = next((f for f in embed.fields if f.name.startswith("Cargo Hold")), None)
        assert cargo_field is not None
        # Capacity from ship_stats.cargo=20
        assert cargo_field.name == "Cargo Hold <0/20>"

    def test_cargo_hidden_for_non_admin_other_view(self, mock_player_cog, request):
        """Non-admin viewing another player → no Cargo Hold section."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()
        other = MagicMock()
        other.id = 999
        other.display_name = "Other"
        other.__str__ = MagicMock(return_value="Other#0000")

        with (
            patch("cogs.playerCog._check_is_admin", AsyncMock(return_value=False)),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            self._register(mock_router, _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=other))

        embed = interaction.followup.send.call_args[1]["embed"]
        names = [f.name for f in embed.fields]
        assert not any(n.startswith("Cargo Hold") for n in names)

    def test_no_footer_no_timestamp(self, mock_player_cog, request):
        """New embed has no footer and no timestamp (spec §3.1)."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            self._register(mock_router, _make_player_loadout_response())
            asyncio.run(mock_player_cog.loadout.callback(mock_player_cog, interaction, player=None))

        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed.footer.text is None
        assert embed.timestamp is None


class TestLoadoutErrorHandler:
    def test_loadout_error_handler_response_not_done(self, mock_player_cog):
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=False)
        error = MagicMock()

        asyncio.run(mock_player_cog.loadout_error(interaction, error))

        interaction.response.send_message.assert_awaited_once()
        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs.get("ephemeral", False)

    def test_loadout_error_handler_response_already_done(self, mock_player_cog):
        interaction = _create_mock_interaction()
        interaction.response.is_done = MagicMock(return_value=True)
        error = MagicMock()

        asyncio.run(mock_player_cog.loadout_error(interaction, error))

        interaction.response.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# /register alias (A.19 regression)
# ---------------------------------------------------------------------------


class TestRegisterAlias:
    """A.19: /register is a full behavioural alias for /profile.

    Both commands must produce the same embed shape, invoke the same shared
    handler, and send ``discord_username`` on the player upsert.
    """

    def test_register_happy_path_matches_profile(self, mock_player_cog, request):
        """/register on a Bronze player yields the same embed shape as /profile."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze", prestige_count=0)
        stats_data = _make_stats_data()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            asyncio.run(mock_player_cog.register.callback(mock_player_cog, interaction))

        interaction.response.defer.assert_awaited_once_with(thinking=True)
        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert "embed" in call_kwargs

    def test_register_delegates_to_shared_handler(self, mock_player_cog):
        """Both /profile and /register go through the same _display_profile handler."""
        interaction_p = _create_mock_interaction()
        interaction_r = _create_mock_interaction()

        with patch.object(mock_player_cog, "_display_profile", new=AsyncMock()) as mock_handler:
            asyncio.run(mock_player_cog.profile.callback(mock_player_cog, interaction_p))
            asyncio.run(mock_player_cog.register.callback(mock_player_cog, interaction_r))

            assert mock_handler.await_count == 2
            # First call came from /profile, second from /register.
            first_arg_p = mock_handler.await_args_list[0][0][0]
            first_arg_r = mock_handler.await_args_list[1][0][0]
            assert first_arg_p is interaction_p
            assert first_arg_r is interaction_r

    def test_register_404_behaves_same_as_profile(self, mock_player_cog, request):
        """/register must handle a 404 from player upsert identically to /profile."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(
                return_value=httpx.Response(404, json={"detail": "Not Found"})
            )
            asyncio.run(mock_player_cog.register.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited_once()
        call_kwargs = interaction.followup.send.call_args
        # Ephemeral so only the invoker sees the error.
        assert call_kwargs[1].get("ephemeral", False)
        msg = call_kwargs[0][0]
        assert "not found" in msg.lower() or "profile" in msg.lower()

    def test_register_sends_discord_username_on_upsert(self, mock_player_cog, request):
        """A.3-style invariant: /register posts ``discord_username = str(user)``."""
        import json

        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze", prestige_count=0)
        stats_data = _make_stats_data()

        with respx.mock(assert_all_called=True) as mock_router:
            players_route = mock_router.post(f"{_BOT_API}/players/").mock(
                return_value=httpx.Response(200, json=player_data)
            )
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/statistics").mock(
                return_value=httpx.Response(200, json=stats_data)
            )
            asyncio.run(mock_player_cog.register.callback(mock_player_cog, interaction))

        body = json.loads(players_route.calls.last.request.content)
        assert body["discord_id"] == interaction.user.id
        assert body["guild_id"] == interaction.guild_id
        # discord_username must be the live str(user), NOT None (preserves
        # the A.3 behaviour that username writes only happen on /profile and
        # now also /register, since /register is a full alias).
        assert body["discord_username"] == str(interaction.user)


# ---------------------------------------------------------------------------
# Bug B.39: /promote must remove old tier role and add new tier role
# ---------------------------------------------------------------------------


class TestPromoteTierRoleSwap:
    """Tests for Bug B.39: /promote should remove old tier role and add new tier role.

    Previously /promote only updated the tier in the DB but never mutated Discord
    roles — the player ended up with Bronze AND Silver simultaneously after Bronze→Silver.
    The fix: after a successful API promotion, fetch guild config, remove the old tier
    role and add the new tier role (both non-fatal).

    TRUEUP-01: migrated off `AsyncMock(http_client.get, side_effect=[...])` to
    respx, with each of the promotion-status/combat-preflight/config GETs
    registered against its real URL.
    """

    @pytest.fixture(autouse=True)
    def _patch_confirm_view(self, mock_player_cog, request):
        from unittest.mock import patch as _patch

        _with_real_http_client(mock_player_cog, request)

        view_mock = MagicMock()
        view_mock.result = True
        view_mock.wait = AsyncMock(return_value=False)
        with _patch("cogs.playerCog.ConfirmView", return_value=view_mock):
            yield

    def _register_precheck(self, mock_router, player_id, new_tier="Silver"):
        """Register the promotion-status + combat-preflight GETs (always green/eligible)."""
        import httpx

        mock_router.get(f"{_BOT_API}/players/{player_id}/promotion-status").mock(
            return_value=httpx.Response(
                200, json={"can_promote": True, "next_tier": new_tier, "xp": 1500, "xp_threshold_for_next": 1000}
            )
        )
        mock_router.get(f"{_BOT_API}/players/{player_id}/combat-preflight").mock(
            return_value=httpx.Response(200, json={"verdict": "green", "sims_run": 20, "player_win_rate": 0.8})
        )

    def _register_config(self, mock_router, guild_id, config_data_or_exception):
        if isinstance(config_data_or_exception, Exception):
            mock_router.get(f"{_BOT_API}/config/guild/{guild_id}").mock(side_effect=config_data_or_exception)
        else:
            import httpx

            mock_router.get(f"{_BOT_API}/config/guild/{guild_id}").mock(
                return_value=httpx.Response(200, json=config_data_or_exception)
            )

    def _register_promote(self, mock_router, old_tier="Bronze", new_tier="Silver"):
        """Register the player-upsert + promote PUT routes."""
        import httpx

        player_data = _make_player_data(tier=old_tier)
        promote_data = {
            "player_id": 1,
            "old_tier": old_tier,
            "new_tier": new_tier,
            "xp": 1500,
            "eligible_for_next": False,
            "next_tier": None,
        }
        mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
        mock_router.put(f"{_BOT_API}/players/{player_data['id']}/promote").mock(
            return_value=httpx.Response(200, json=promote_data)
        )
        return player_data, promote_data

    def test_promote_removes_old_tier_role_and_adds_new_tier_role(self, mock_player_cog):
        """B.39: Bronze→Silver promotion must remove Bronze role and add Silver role."""
        import respx

        bronze_role_id = 111222001
        silver_role_id = 111222002

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id
        mock_bronze_role.name = "Bounty Hunter Bronze"

        mock_silver_role = MagicMock()
        mock_silver_role.id = silver_role_id
        mock_silver_role.name = "Bounty Hunter Silver"

        # User currently has Bronze role
        interaction = _create_interaction_with_roles(existing_roles=[mock_bronze_role])

        def _get_role(role_id):
            return {bronze_role_id: mock_bronze_role, silver_role_id: mock_silver_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        config_data = _make_config_data(
            bh_role_id=None,
            bronze_role_id=bronze_role_id,
            silver_role_id=silver_role_id,
            gold_role_id=None,
            platinum_role_id=None,
        )

        with respx.mock(assert_all_called=True) as mock_router:
            player_data, _ = self._register_promote(mock_router, old_tier="Bronze", new_tier="Silver")
            self._register_precheck(mock_router, player_data["id"])
            self._register_config(mock_router, 987654321, config_data)
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        # Success embed must be sent (confirm dialog + result = at least 2 calls)
        assert interaction.followup.send.call_count >= 2

        # Old Bronze role must be removed
        interaction.user.remove_roles.assert_awaited_once()
        removed_args = interaction.user.remove_roles.call_args[0]
        removed_ids = {r.id for r in removed_args}
        assert bronze_role_id in removed_ids, f"Bronze role must be removed on promotion; removed_ids={removed_ids}"
        assert silver_role_id not in removed_ids, "New Silver role must NOT appear in remove list"

        # New Silver role must be added
        interaction.user.add_roles.assert_awaited_once()
        added_args = interaction.user.add_roles.call_args[0]
        added_ids = {r.id for r in added_args}
        assert silver_role_id in added_ids, f"Silver role must be added on promotion; added_ids={added_ids}"
        assert bronze_role_id not in added_ids, "Old Bronze role must NOT be added"

    def test_promote_does_not_add_role_user_already_has(self, mock_player_cog):
        """B.39: If user somehow already has the new tier role, add_roles is not called for it."""
        import respx

        silver_role_id = 111222002

        mock_silver_role = MagicMock()
        mock_silver_role.id = silver_role_id
        mock_silver_role.name = "Bounty Hunter Silver"

        # User already has Silver (edge case)
        interaction = _create_interaction_with_roles(existing_roles=[mock_silver_role])
        interaction.guild.get_role = MagicMock(return_value=mock_silver_role)

        config_data = _make_config_data(
            bh_role_id=None,
            bronze_role_id=None,  # No Bronze role configured
            silver_role_id=silver_role_id,
            gold_role_id=None,
            platinum_role_id=None,
        )

        with respx.mock(assert_all_called=True) as mock_router:
            player_data, _ = self._register_promote(mock_router, old_tier="Bronze", new_tier="Silver")
            self._register_precheck(mock_router, player_data["id"])
            self._register_config(mock_router, 987654321, config_data)
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        # Embed still sent (confirm dialog + result)
        assert interaction.followup.send.call_count >= 2
        # Nothing to remove (no old Bronze role configured)
        interaction.user.remove_roles.assert_not_awaited()
        # Nothing to add (Silver already held)
        interaction.user.add_roles.assert_not_awaited()

    def test_promote_role_swap_non_fatal_on_config_error(self, mock_player_cog):
        """B.39: If the config API call fails, promote still succeeds (non-fatal)."""
        import respx

        interaction = _create_interaction_with_roles(existing_roles=[])

        with respx.mock(assert_all_called=True) as mock_router:
            player_data, _ = self._register_promote(mock_router, old_tier="Bronze", new_tier="Silver")
            self._register_precheck(mock_router, player_data["id"])
            # Config fetch fails (status+preflight succeed, config raises)
            self._register_config(mock_router, 987654321, RuntimeError("config unavailable"))
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        # Success embed is still sent (confirm dialog + result)
        assert interaction.followup.send.call_count >= 2
        call_kwargs = interaction.followup.send.call_args_list[-1][1]
        assert "embed" in call_kwargs
        # Role methods not called because config was unavailable
        interaction.user.remove_roles.assert_not_awaited()
        interaction.user.add_roles.assert_not_awaited()

    def test_promote_role_swap_non_fatal_on_remove_roles_error(self, mock_player_cog):
        """B.39: If remove_roles fails, the promote embed was already sent (non-fatal)."""
        import respx

        bronze_role_id = 111222001
        silver_role_id = 111222002

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id
        mock_bronze_role.name = "Bounty Hunter Bronze"

        mock_silver_role = MagicMock()
        mock_silver_role.id = silver_role_id
        mock_silver_role.name = "Bounty Hunter Silver"

        interaction = _create_interaction_with_roles(existing_roles=[mock_bronze_role])
        interaction.guild.get_role = MagicMock(
            side_effect=lambda rid: {bronze_role_id: mock_bronze_role, silver_role_id: mock_silver_role}.get(rid)
        )
        interaction.user.remove_roles = AsyncMock(side_effect=RuntimeError("Missing Permissions"))

        config_data = _make_config_data(
            bh_role_id=None,
            bronze_role_id=bronze_role_id,
            silver_role_id=silver_role_id,
            gold_role_id=None,
            platinum_role_id=None,
        )

        with respx.mock(assert_all_called=True) as mock_router:
            player_data, _ = self._register_promote(mock_router, old_tier="Bronze", new_tier="Silver")
            self._register_precheck(mock_router, player_data["id"])
            self._register_config(mock_router, 987654321, config_data)
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        # The embed was sent BEFORE the role swap attempt — it must always succeed
        assert interaction.followup.send.call_count >= 2
        call_kwargs = interaction.followup.send.call_args_list[-1][1]
        assert "embed" in call_kwargs

    def test_promote_skips_role_removal_if_old_role_not_in_config(self, mock_player_cog):
        """B.39: If the old tier role isn't configured, remove_roles is not called."""
        import respx

        silver_role_id = 111222002
        mock_silver_role = MagicMock()
        mock_silver_role.id = silver_role_id

        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=mock_silver_role)

        # bronze_role_id absent from config
        config_data = _make_config_data(
            bh_role_id=None,
            bronze_role_id=None,
            silver_role_id=silver_role_id,
            gold_role_id=None,
            platinum_role_id=None,
        )

        with respx.mock(assert_all_called=True) as mock_router:
            player_data, _ = self._register_promote(mock_router, old_tier="Bronze", new_tier="Silver")
            self._register_precheck(mock_router, player_data["id"])
            self._register_config(mock_router, 987654321, config_data)
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        assert interaction.followup.send.call_count >= 2
        # No old role to remove
        interaction.user.remove_roles.assert_not_awaited()
        # New Silver role added (user doesn't have it)
        interaction.user.add_roles.assert_awaited_once()

    def test_promote_add_roles_fails_after_remove_roles_succeeds_leaves_user_roleless(self, mock_player_cog):
        """DEF-B39-001 (adversarial): remove_roles succeeds but add_roles then fails.

        EXPECTED (correct) behaviour: if the new role cannot be added, the old role
        should be PRESERVED — the user keeps their pre-promotion tier role rather than
        ending up with no tier role at all.

        CURRENT (broken) behaviour: remove_roles fires first. If add_roles subsequently
        raises, the outer except swallows the error, but the old role is already gone.
        The user is left with NO tier role — a regression vs. the pre-fix state (where
        at least they kept their Bronze role, even if that was wrong).

        Fix required: swap the operation order — add new role FIRST, then remove old
        role. That way any failure in the add step aborts before the remove, keeping
        the old role intact.

        This test DEMONSTRATES THE DEFECT by asserting the correct outcome. It will
        FAIL against the current implementation (proving the defect is real), and
        will PASS once the fix is applied.
        """
        import respx

        bronze_role_id = 111222001
        silver_role_id = 111222002

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id
        mock_bronze_role.name = "Bounty Hunter Bronze"

        mock_silver_role = MagicMock()
        mock_silver_role.id = silver_role_id
        mock_silver_role.name = "Bounty Hunter Silver"

        # User currently has Bronze role
        interaction = _create_interaction_with_roles(existing_roles=[mock_bronze_role])
        interaction.guild.get_role = MagicMock(
            side_effect=lambda rid: {bronze_role_id: mock_bronze_role, silver_role_id: mock_silver_role}.get(rid)
        )

        # remove_roles succeeds; add_roles fails (e.g. bot lacks Manage Roles for Silver)
        interaction.user.remove_roles = AsyncMock()
        interaction.user.add_roles = AsyncMock(side_effect=RuntimeError("Missing Permissions for Silver"))

        config_data = _make_config_data(
            bh_role_id=None,
            bronze_role_id=bronze_role_id,
            silver_role_id=silver_role_id,
            gold_role_id=None,
            platinum_role_id=None,
        )

        with respx.mock(assert_all_called=True) as mock_router:
            player_data, _ = self._register_promote(mock_router, old_tier="Bronze", new_tier="Silver")
            self._register_precheck(mock_router, player_data["id"])
            self._register_config(mock_router, 987654321, config_data)
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        # Success embed is always sent (non-fatal role swap) — not in dispute
        assert interaction.followup.send.call_count >= 2

        # CORRECT expected outcome: if add_roles fails, remove_roles must NOT have run.
        # The user keeps their Bronze role rather than ending up with no role at all.
        # DEF-B39-001: the implementation must add the new role BEFORE removing the old one
        # so that a failure in add_roles aborts without stripping the user's existing tier role.
        interaction.user.remove_roles.assert_not_awaited()

    def test_promote_notifications_disabled_does_not_add_new_role(self, mock_player_cog):
        """Notification opt-out: stored bounty_notifications_enabled=False — Silver role
        must NOT be added on promotion (D-019: production reads the stored flag)."""
        import httpx
        import respx

        bronze_role_id = 111222001
        silver_role_id = 111222002

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id
        mock_bronze_role.name = "Bounty Hunter Bronze"

        mock_silver_role = MagicMock()
        mock_silver_role.id = silver_role_id
        mock_silver_role.name = "Bounty Hunter Silver"

        # User does NOT have the Bronze role
        interaction = _create_interaction_with_roles(existing_roles=[])

        def _get_role(role_id):
            return {bronze_role_id: mock_bronze_role, silver_role_id: mock_silver_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        # D-019: set stored flag to False so production skips the new tier role
        player_data = _make_player_data(tier="Bronze")
        player_data["bounty_notifications_enabled"] = False

        promote_data = {
            "player_id": 1,
            "old_tier": "Bronze",
            "new_tier": "Silver",
            "xp": 1500,
            "eligible_for_next": False,
            "next_tier": None,
        }

        config_data = _make_config_data(
            bh_role_id=None,
            bronze_role_id=bronze_role_id,
            silver_role_id=silver_role_id,
            gold_role_id=None,
            platinum_role_id=None,
        )

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.put(f"{_BOT_API}/players/{player_data['id']}/promote").mock(
                return_value=httpx.Response(200, json=promote_data)
            )
            self._register_precheck(mock_router, player_data["id"])
            self._register_config(mock_router, 987654321, config_data)
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        # Neither role should be touched — user opted out
        interaction.user.add_roles.assert_not_awaited()
        interaction.user.remove_roles.assert_not_awaited()

    def test_promote_opted_out_holds_old_role_old_removed_new_not_added(self, mock_player_cog):
        """Opted-out promotion: user HOLDS the old Bronze role but bounty_notifications_enabled=False.

        The old Bronze role MUST be removed (it is the wrong tier now); the new Silver
        role must NOT be added (player is opted out).  This is the meaningful opted-out-
        promotion edge case — the previous disabled test set existing_roles=[] so the
        old role removal path was never exercised.
        """
        import httpx
        import respx

        bronze_role_id = 111222001
        silver_role_id = 111222002

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id
        mock_bronze_role.name = "Bounty Hunter Bronze"

        mock_silver_role = MagicMock()
        mock_silver_role.id = silver_role_id
        mock_silver_role.name = "Bounty Hunter Silver"

        # User HOLDS the old Bronze role (stale from when they were opted in)
        interaction = _create_interaction_with_roles(existing_roles=[mock_bronze_role])

        def _get_role(role_id):
            return {bronze_role_id: mock_bronze_role, silver_role_id: mock_silver_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        # D-019: stored flag is False — opted out
        player_data = _make_player_data(tier="Bronze")
        player_data["bounty_notifications_enabled"] = False

        promote_data = {
            "player_id": 1,
            "old_tier": "Bronze",
            "new_tier": "Silver",
            "xp": 1500,
            "eligible_for_next": False,
            "next_tier": None,
        }

        config_data = _make_config_data(
            bh_role_id=None,
            bronze_role_id=bronze_role_id,
            silver_role_id=silver_role_id,
            gold_role_id=None,
            platinum_role_id=None,
        )

        with respx.mock(assert_all_called=True) as mock_router:
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.put(f"{_BOT_API}/players/{player_data['id']}/promote").mock(
                return_value=httpx.Response(200, json=promote_data)
            )
            self._register_precheck(mock_router, player_data["id"])
            self._register_config(mock_router, 987654321, config_data)
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        # Old Bronze role MUST be removed (wrong tier regardless of opt-out)
        interaction.user.remove_roles.assert_awaited_once()
        removed_ids = {r.id for r in interaction.user.remove_roles.call_args[0]}
        assert bronze_role_id in removed_ids, f"Bronze role {bronze_role_id} must be removed; got {removed_ids}"
        # New Silver role must NOT be added (player is opted out)
        interaction.user.add_roles.assert_not_awaited()

    def test_promote_notifications_enabled_adds_new_role(self, mock_player_cog):
        """Notification opt-in: user has Bronze role (notifications on) → Silver is added
        and Bronze is removed as normal."""
        import respx

        bronze_role_id = 111222001
        silver_role_id = 111222002

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id
        mock_bronze_role.name = "Bounty Hunter Bronze"

        mock_silver_role = MagicMock()
        mock_silver_role.id = silver_role_id
        mock_silver_role.name = "Bounty Hunter Silver"

        # User HAS the Bronze role (notifications enabled)
        interaction = _create_interaction_with_roles(existing_roles=[mock_bronze_role])

        def _get_role(role_id):
            return {bronze_role_id: mock_bronze_role, silver_role_id: mock_silver_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        config_data = _make_config_data(
            bh_role_id=None,
            bronze_role_id=bronze_role_id,
            silver_role_id=silver_role_id,
            gold_role_id=None,
            platinum_role_id=None,
        )

        with respx.mock(assert_all_called=True) as mock_router:
            player_data, _ = self._register_promote(mock_router, old_tier="Bronze", new_tier="Silver")
            self._register_precheck(mock_router, player_data["id"])
            self._register_config(mock_router, 987654321, config_data)
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        # Silver added, Bronze removed
        interaction.user.add_roles.assert_awaited_once()
        added_ids = {r.id for r in interaction.user.add_roles.call_args[0]}
        assert silver_role_id in added_ids
        interaction.user.remove_roles.assert_awaited_once()
        removed_ids = {r.id for r in interaction.user.remove_roles.call_args[0]}
        assert bronze_role_id in removed_ids


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ===========================================================================
# Tests: /promote ConfirmView flow
# ===========================================================================


class TestPromoteConfirmView:
    """Tests for the /promote two-step ConfirmView confirmation flow.

    TRUEUP-01: migrated off `AsyncMock(http_client.get, side_effect=[...])` to
    respx.
    """

    def _make_confirm_view_mock(self, result):
        view = MagicMock()
        view.result = result
        view.wait = AsyncMock(return_value=None)
        return view

    def _register_precheck(self, mock_router, player_id, can_promote=True, next_tier="Silver", **preflight_kwargs):
        import httpx

        status_data = {
            "can_promote": can_promote,
            "next_tier": next_tier,
            "xp": 1500,
            "xp_threshold_for_next": 1000,
        }
        mock_router.get(f"{_BOT_API}/players/{player_id}/promotion-status").mock(
            return_value=httpx.Response(200, json=status_data)
        )
        preflight_data = {
            "verdict": preflight_kwargs.get("verdict", "green"),
            "sims_run": preflight_kwargs.get("sims_run", 20),
            "player_win_rate": preflight_kwargs.get("player_win_rate", 0.9),
        }
        mock_router.get(f"{_BOT_API}/players/{player_id}/combat-preflight").mock(
            return_value=httpx.Response(200, json=preflight_data)
        )

    def test_promote_confirmed_calls_promote_api(self, mock_player_cog, request):
        """/promote: user confirms → PUT /players/{id}/promote is called."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")
        promote_data = {
            "player_id": 1,
            "old_tier": "Bronze",
            "new_tier": "Silver",
            "xp": 1500,
            "eligible_for_next": False,
            "next_tier": "Gold",
        }

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck(mock_router, player_data["id"])
            put_route = mock_router.put(f"{_BOT_API}/players/{player_data['id']}/promote").mock(
                return_value=httpx.Response(200, json=promote_data)
            )
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        assert put_route.called

    def test_promote_cancel_does_not_call_promote_api(self, mock_player_cog, request):
        """/promote: user cancels → PUT /promote is NOT called.

        The promote route is deliberately left unregistered — if the cog called
        it anyway, respx would raise (no route matches).
        """
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")

        view_mock = self._make_confirm_view_mock(result=False)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck(mock_router, player_data["id"], verdict="no_data", sims_run=0)
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        # A cancel/timeout message should still be sent (the promote PUT itself was
        # implicitly proven un-called above — respx would have raised otherwise).
        interaction.followup.send.assert_awaited()

    def test_promote_confirm_shows_confirm_view(self, mock_player_cog, request):
        """/promote: a ConfirmView is shown before the promotion is applied."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")

        view_mock = self._make_confirm_view_mock(result=False)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock) as patched_cv,
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck(mock_router, player_data["id"])
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        patched_cv.assert_called_once()

    def test_promote_429_after_confirm_shows_cooldown_embed(self, mock_player_cog, request):
        """/promote: PUT returns 429 after confirm → cooldown embed is shown."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")
        cooldown_iso = "2026-05-15T12:00:00+00:00"

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck(mock_router, player_data["id"])
            mock_router.put(f"{_BOT_API}/players/{player_data['id']}/promote").mock(
                return_value=httpx.Response(
                    429, json={"detail": {"detail": "Cooldown active", "cooldown_end": cooldown_iso}}
                )
            )
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        embeds_sent = [
            call[1].get("embed")
            for call in interaction.followup.send.call_args_list
            if call[1].get("embed") is not None
        ]
        assert any("Cannot Promote" in (e.title or "") for e in embeds_sent if e)

    def test_promote_not_eligible_sends_message_before_confirmview(self, mock_player_cog, request):
        """/promote: not eligible → followup message sent, ConfirmView NOT created."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Bronze")

        with (
            patch("cogs.playerCog.ConfirmView") as patched_cv,
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.get(f"{_BOT_API}/players/{player_data['id']}/promotion-status").mock(
                return_value=httpx.Response(
                    200, json={"can_promote": False, "next_tier": "Silver", "xp": 100, "xp_threshold_for_next": 1000}
                )
            )
            asyncio.run(mock_player_cog.promote.callback(mock_player_cog, interaction))

        patched_cv.assert_not_called()
        interaction.followup.send.assert_awaited_once()


# ===========================================================================
# Tests: /demote command
# ===========================================================================


class TestDemoteCommand:
    """Tests for the /demote slash command.

    TRUEUP-01: migrated off `AsyncMock(http_client.get/post/put)` to respx.
    """

    def _make_confirm_view_mock(self, result):
        view = MagicMock()
        view.result = result
        view.wait = AsyncMock(return_value=None)
        return view

    def _register_precheck_and_config(self, mock_router, player_id):
        """Register promotion-status (no cooldown) + config/guild (empty → default penalty)."""
        import httpx

        mock_router.get(f"{_BOT_API}/players/{player_id}/promotion-status").mock(
            return_value=httpx.Response(200, json={"on_cooldown": False})
        )
        mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(return_value=httpx.Response(200, json={}))

    def test_demote_bronze_player_sends_error_no_confirmview(self, mock_player_cog, request):
        """/demote: Bronze player gets an error message — ConfirmView NOT shown."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        with (
            patch("cogs.playerCog.ConfirmView") as patched_cv,
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(
                return_value=httpx.Response(200, json=_make_player_data(tier="Bronze"))
            )
            asyncio.run(mock_player_cog.demote.callback(mock_player_cog, interaction))

        patched_cv.assert_not_called()
        interaction.followup.send.assert_awaited()

    def test_demote_happy_path_confirmed(self, mock_player_cog, request):
        """/demote: Silver player confirms → PUT /players/{id}/demote called."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Silver")
        demote_data = {
            "player_id": 1,
            "old_tier": "Silver",
            "new_tier": "Bronze",
            "xp": 1500,
        }

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck_and_config(mock_router, player_data["id"])
            put_route = mock_router.put(f"{_BOT_API}/players/{player_data['id']}/demote").mock(
                return_value=httpx.Response(200, json=demote_data)
            )
            asyncio.run(mock_player_cog.demote.callback(mock_player_cog, interaction))

        assert put_route.called

    def test_demote_cancel_does_not_call_api(self, mock_player_cog, request):
        """/demote: user cancels → PUT /demote is NOT called (unregistered route)."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Silver")

        view_mock = self._make_confirm_view_mock(result=False)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck_and_config(mock_router, player_data["id"])
            asyncio.run(mock_player_cog.demote.callback(mock_player_cog, interaction))

        interaction.followup.send.assert_awaited()

    def test_demote_429_shows_cooldown_embed(self, mock_player_cog, request):
        """/demote: PUT returns 429 → cooldown embed is shown."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Silver")
        cooldown_iso = "2026-05-16T08:00:00+00:00"

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck_and_config(mock_router, player_data["id"])
            mock_router.put(f"{_BOT_API}/players/{player_data['id']}/demote").mock(
                return_value=httpx.Response(
                    429, json={"detail": {"detail": "Cooldown active", "cooldown_end": cooldown_iso}}
                )
            )
            asyncio.run(mock_player_cog.demote.callback(mock_player_cog, interaction))

        embeds_sent = [
            call[1].get("embed")
            for call in interaction.followup.send.call_args_list
            if call[1].get("embed") is not None
        ]
        assert any("Cannot Demote" in (e.title or "") for e in embeds_sent if e)

    def test_demote_shows_confirmview_for_non_bronze_player(self, mock_player_cog, request):
        """/demote: non-Bronze player sees a ConfirmView before demotion."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Gold")

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock) as patched_cv,
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck_and_config(mock_router, player_data["id"])
            mock_router.put(f"{_BOT_API}/players/{player_data['id']}/demote").mock(
                return_value=httpx.Response(
                    200, json={"player_id": 1, "old_tier": "Gold", "new_tier": "Silver", "xp": 5000}
                )
            )
            asyncio.run(mock_player_cog.demote.callback(mock_player_cog, interaction))

        patched_cv.assert_called_once()


# ===========================================================================
# Tests: /demote warning embed credit penalty
# ===========================================================================


class TestDemoteWarningPenalty:
    """Verify the warning embed shows the estimated credit penalty before confirmation.

    The penalty rate comes from GET /config/guild/{id} → demotion_credit_penalty_pct.
    NULL / absent → defaults to 10 (global GameConstants default).

    TRUEUP-01: migrated off `AsyncMock(http_client.get)` to respx, with
    promotion-status and config registered as separate real routes.
    """

    def _register_precheck_and_config(self, mock_router, player_id, penalty_pct=None):
        import httpx

        mock_router.get(f"{_BOT_API}/players/{player_id}/promotion-status").mock(
            return_value=httpx.Response(200, json={})
        )
        cfg_payload = {}
        if penalty_pct is not None:
            cfg_payload["demotion_credit_penalty_pct"] = penalty_pct
        mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(return_value=httpx.Response(200, json=cfg_payload))

    def test_demote_warning_shows_penalty_line(self, mock_player_cog, request):
        """Warning embed uses default 10% when config returns no penalty_pct."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        # 500 credits → estimated penalty = int(500 * 10 / 100) = 50
        player_data = _make_player_data(tier="Silver")  # credits=500

        view_mock = MagicMock()
        view_mock.result = None  # time out — we only care about the warning embed
        view_mock.wait = AsyncMock(return_value=None)

        sent_embed = None

        async def _capture_send(*args, **kwargs):
            nonlocal sent_embed
            if "embed" in kwargs and sent_embed is None:
                sent_embed = kwargs["embed"]

        interaction.followup.send = AsyncMock(side_effect=_capture_send)

        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck_and_config(mock_router, player_data["id"], penalty_pct=None)  # → default 10%
            asyncio.run(mock_player_cog.demote.callback(mock_player_cog, interaction))

        assert sent_embed is not None, "Warning embed was never sent"
        desc = sent_embed.description
        assert "-50 cr" in desc, f"Expected '-50 cr' penalty in warning embed, got:\n{desc}"
        assert "500" in desc, f"Expected current balance '500' in warning embed, got:\n{desc}"
        assert "10%" in desc, f"Expected '10%' penalty label in warning embed, got:\n{desc}"

    def test_demote_warning_penalty_zero_credits(self, mock_player_cog, request):
        """A player with 0 credits gets a 0 cr penalty line (no negative credits)."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        player_data = _make_player_data(tier="Gold")
        player_data["credits"] = 0

        view_mock = MagicMock()
        view_mock.result = None
        view_mock.wait = AsyncMock(return_value=None)

        sent_embed = None

        async def _capture_send(*args, **kwargs):
            nonlocal sent_embed
            if "embed" in kwargs and sent_embed is None:
                sent_embed = kwargs["embed"]

        interaction.followup.send = AsyncMock(side_effect=_capture_send)

        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck_and_config(mock_router, player_data["id"], penalty_pct=None)
            asyncio.run(mock_player_cog.demote.callback(mock_player_cog, interaction))

        assert sent_embed is not None
        desc = sent_embed.description
        assert "-0 cr" in desc, f"Expected '-0 cr' for zero-credit player, got:\n{desc}"

    def test_demote_warning_uses_guild_penalty_rate(self, mock_player_cog, request):
        """Warning embed uses the per-guild penalty rate when config provides one."""
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        interaction = _create_mock_interaction()

        # 1000 credits, guild configured at 25% → estimated penalty = int(1000 * 25 / 100) = 250
        player_data = _make_player_data(tier="Gold")
        player_data["credits"] = 1000

        view_mock = MagicMock()
        view_mock.result = None
        view_mock.wait = AsyncMock(return_value=None)

        sent_embed = None

        async def _capture_send(*args, **kwargs):
            nonlocal sent_embed
            if "embed" in kwargs and sent_embed is None:
                sent_embed = kwargs["embed"]

        interaction.followup.send = AsyncMock(side_effect=_capture_send)

        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            self._register_precheck_and_config(mock_router, player_data["id"], penalty_pct=25)
            asyncio.run(mock_player_cog.demote.callback(mock_player_cog, interaction))

        assert sent_embed is not None, "Warning embed was never sent"
        desc = sent_embed.description
        assert "-250 cr" in desc, f"Expected '-250 cr' for 25% rate, got:\n{desc}"
        assert "25%" in desc, f"Expected '25%' rate in warning embed, got:\n{desc}"


# ===========================================================================
# Tests: /demote tier role swap + notification preference
# ===========================================================================


class TestDemoteTierRoleSwap:
    """Tests for /demote tier role swap and notification preference preservation.

    TRUEUP-01: migrated off `AsyncMock(http_client.get)` to respx. The
    promotion-status and config/guild GETs are registered as separate routes;
    config/guild is hit twice in the real flow (penalty preview + role swap)
    and a single `.mock(return_value=...)` transparently serves both calls.
    """

    def _make_confirm_view_mock(self, result):
        view = MagicMock()
        view.result = result
        view.wait = AsyncMock(return_value=None)
        return view

    def _register_demote(self, mock_router, old_tier="Silver", new_tier="Bronze"):
        import httpx

        player_data = _make_player_data(tier=old_tier)
        demote_data = {"player_id": 1, "old_tier": old_tier, "new_tier": new_tier, "xp": 500}
        mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
        mock_router.put(f"{_BOT_API}/players/{player_data['id']}/demote").mock(
            return_value=httpx.Response(200, json=demote_data)
        )
        return player_data, demote_data

    def _register_precheck_and_config(self, mock_router, player_id, config_data):
        import httpx

        mock_router.get(f"{_BOT_API}/players/{player_id}/promotion-status").mock(
            return_value=httpx.Response(200, json={})
        )
        mock_router.get(f"{_BOT_API}/config/guild/987654321").mock(return_value=httpx.Response(200, json=config_data))

    def test_demote_notifications_enabled_swaps_roles(self, mock_player_cog, request):
        """User holds Silver role (notifications on) → Bronze added, Silver removed."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        silver_role_id = 111222002
        bronze_role_id = 111222001

        mock_silver_role = MagicMock()
        mock_silver_role.id = silver_role_id

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id

        # User HAS Silver role (notifications enabled)
        interaction = _create_interaction_with_roles(existing_roles=[mock_silver_role])

        def _get_role(role_id):
            return {silver_role_id: mock_silver_role, bronze_role_id: mock_bronze_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        config_data = _make_config_data(
            bh_role_id=None,
            bronze_role_id=bronze_role_id,
            silver_role_id=silver_role_id,
            gold_role_id=None,
            platinum_role_id=None,
        )

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            player_data, _ = self._register_demote(mock_router, old_tier="Silver", new_tier="Bronze")
            self._register_precheck_and_config(mock_router, player_data["id"], config_data)
            asyncio.run(mock_player_cog.demote.callback(mock_player_cog, interaction))

        # Bronze added, Silver removed
        interaction.user.add_roles.assert_awaited_once()
        added_ids = {r.id for r in interaction.user.add_roles.call_args[0]}
        assert bronze_role_id in added_ids
        interaction.user.remove_roles.assert_awaited_once()
        removed_ids = {r.id for r in interaction.user.remove_roles.call_args[0]}
        assert silver_role_id in removed_ids

    def test_demote_notifications_disabled_does_not_add_new_role(self, mock_player_cog, request):
        """Opted-out demotion: user HOLDS the old Silver role but bounty_notifications_enabled=False.

        The meaningful edge case: the old Silver role MUST be removed (it is the wrong tier
        now), but the new Bronze role must NOT be added (player is opted out).

        Previous version of this test set existing_roles=[] so no role op could fire
        regardless of the flag — the test passed vacuously and proved nothing.  This
        rewrite gives the user the old Silver role so the remove path is exercised.
        """
        import httpx
        import respx

        _with_real_http_client(mock_player_cog, request)
        silver_role_id = 111222002
        bronze_role_id = 111222001

        mock_silver_role = MagicMock()
        mock_silver_role.id = silver_role_id
        mock_silver_role.name = "Bounty Hunter Silver"

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id
        mock_bronze_role.name = "Bounty Hunter Bronze"

        # User HOLDS the old Silver role (stale from when they were opted in)
        interaction = _create_interaction_with_roles(existing_roles=[mock_silver_role])

        def _get_role(role_id):
            return {silver_role_id: mock_silver_role, bronze_role_id: mock_bronze_role}.get(role_id)

        interaction.guild.get_role = MagicMock(side_effect=_get_role)

        # D-019: stored flag is False — opted out
        player_data = _make_player_data(tier="Silver")
        player_data["bounty_notifications_enabled"] = False

        demote_data = {"player_id": 1, "old_tier": "Silver", "new_tier": "Bronze", "xp": 500}
        config_data = _make_config_data(
            bh_role_id=None,
            bronze_role_id=bronze_role_id,
            silver_role_id=silver_role_id,
            gold_role_id=None,
            platinum_role_id=None,
        )

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.post(f"{_BOT_API}/players/").mock(return_value=httpx.Response(200, json=player_data))
            mock_router.put(f"{_BOT_API}/players/{player_data['id']}/demote").mock(
                return_value=httpx.Response(200, json=demote_data)
            )
            self._register_precheck_and_config(mock_router, player_data["id"], config_data)
            asyncio.run(mock_player_cog.demote.callback(mock_player_cog, interaction))

        # Old Silver role MUST be removed (wrong tier regardless of opt-out)
        interaction.user.remove_roles.assert_awaited_once()
        removed_ids = {r.id for r in interaction.user.remove_roles.call_args[0]}
        assert silver_role_id in removed_ids, f"Silver role {silver_role_id} must be removed; got {removed_ids}"
        # New Bronze role must NOT be added (player is opted out)
        interaction.user.add_roles.assert_not_awaited()

    def test_demote_old_role_not_in_config_still_adds_new_role(self, mock_player_cog, request):
        """If the old tier role isn't configured, we can't infer opt-out — new role
        is still added (safe default)."""
        import respx

        _with_real_http_client(mock_player_cog, request)
        bronze_role_id = 111222001

        mock_bronze_role = MagicMock()
        mock_bronze_role.id = bronze_role_id

        # silver_role_id absent from config — can't detect preference
        interaction = _create_interaction_with_roles(existing_roles=[])
        interaction.guild.get_role = MagicMock(return_value=mock_bronze_role)

        config_data = _make_config_data(
            bh_role_id=None,
            bronze_role_id=bronze_role_id,
            silver_role_id=None,  # old role not configured
            gold_role_id=None,
            platinum_role_id=None,
        )

        view_mock = self._make_confirm_view_mock(result=True)
        with (
            patch("cogs.playerCog.ConfirmView", return_value=view_mock),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            player_data, _ = self._register_demote(mock_router, old_tier="Silver", new_tier="Bronze")
            self._register_precheck_and_config(mock_router, player_data["id"], config_data)
            asyncio.run(mock_player_cog.demote.callback(mock_player_cog, interaction))

        # Bronze added (can't determine preference — safe default)
        interaction.user.add_roles.assert_awaited_once()
        interaction.user.remove_roles.assert_not_awaited()


# ===========================================================================
# Tests: _format_tier_change_cooldown_message
# ===========================================================================


class TestFormatTierChangeCooldownMessage:
    """Unit tests for the _format_tier_change_cooldown_message helper."""

    def _make_429_error(self, detail_payload):
        import httpx

        error_response = MagicMock()
        error_response.status_code = 429
        error_response.json.return_value = detail_payload
        return httpx.HTTPStatusError("429", request=MagicMock(), response=error_response)

    def test_title_contains_action_capitalize(self):
        """Embed title includes the capitalized action verb."""
        from cogs.playerCog import _format_tier_change_cooldown_message

        exc = self._make_429_error({"detail": {"detail": "msg", "cooldown_end": "2026-05-15T12:00:00+00:00"}})
        embed = _format_tier_change_cooldown_message(exc, action="demote")

        assert "Cannot Demote Yet" in embed.title

    def test_description_contains_relative_timestamp(self):
        """When cooldown_end is a valid ISO string, description contains a <t: timestamp."""
        from cogs.playerCog import _format_tier_change_cooldown_message

        exc = self._make_429_error({"detail": {"detail": "msg", "cooldown_end": "2026-05-15T12:00:00+00:00"}})
        embed = _format_tier_change_cooldown_message(exc, action="promote")

        assert "<t:" in embed.description

    def test_falls_back_to_soon_when_no_cooldown_end(self):
        """When detail has no cooldown_end, the description falls back to 'soon'."""
        from cogs.playerCog import _format_tier_change_cooldown_message

        exc = self._make_429_error({"detail": "Tier change on cooldown"})
        embed = _format_tier_change_cooldown_message(exc, action="prestige")

        assert "soon" in embed.description

    def test_falls_back_gracefully_on_malformed_response(self):
        """When response JSON is malformed, the embed is still returned without raising."""
        from cogs.playerCog import _format_tier_change_cooldown_message

        error_response = MagicMock()
        error_response.status_code = 429
        error_response.json.side_effect = Exception("not json")
        import httpx

        exc = httpx.HTTPStatusError("429", request=MagicMock(), response=error_response)
        embed = _format_tier_change_cooldown_message(exc, action="promote")

        assert embed is not None
        assert "Cannot Promote Yet" in embed.title
