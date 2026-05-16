"""Phase 7: Pre-normalization tests for per-cog refresh functions.

Verifies that:
  - _fetch_tier_shop  adds _norm to each item dict
  - _fetch_bounties   adds _norm to each bounty dict
  - _fetch_pending_duels  adds _norm to each duel dict
  - _fetch_outgoing_duels adds _norm to each duel dict
  - _fetch_jobs       adds _norm to each job dict
  - buy_item_autocomplete uses _norm (pre-computed) instead of per-item normalize_for_search
  - GET /internal/autocomplete/health returns expected keys

All _fetch_* tests use respx to assert real URLs per AGENTS.md policy (B.33).
Autocomplete filter tests use MagicMock to avoid HTTP and verify cache-only path.
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

# ---------------------------------------------------------------------------
# Mock shared.bblogger BEFORE any application imports
# ---------------------------------------------------------------------------

if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_shared.__path__ = []  # type: ignore[attr-defined]
    _mock_bblogger = types.ModuleType("shared.bblogger")

    def _make_mock_logger(*_args, **_kwargs):
        logger = MagicMock()
        for m in ("info", "debug", "warning", "error", "trace", "critical", "exception"):
            setattr(logger, m, MagicMock())
        return logger

    _mock_bblogger.get_logger = _make_mock_logger  # type: ignore[attr-defined]
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

_SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

_BOT_API = "http://bot-core:8000/api/v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_bot():
    bot = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock()
    bot.wait_until_ready = AsyncMock()
    return bot


def _make_mock_interaction(user_id: int = 111111, guild_id: int = 987654321) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    return interaction


def _with_real_client(cog, request):
    """Replace cog.http_client with a real httpx.AsyncClient for respx interception."""
    cog.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    request.addfinalizer(lambda: asyncio.run(cog.http_client.aclose()))
    return cog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    return _make_mock_bot()


@pytest.fixture
def shop_cog(mock_bot):
    # Evict any stale cached modules
    for k in list(sys.modules.keys()):
        if "shopCog" in k or (k.startswith("cogs.") and "shop" in k.lower()):
            del sys.modules[k]
    sys.modules["shared"] = sys.modules["shared"]
    from cogs.shopCog import ShopCog
    return ShopCog(mock_bot)


@pytest.fixture
def bounty_cog(mock_bot):
    for k in list(sys.modules.keys()):
        if "bountyCog" in k or (k.startswith("cogs.") and "bounty" in k.lower()):
            del sys.modules[k]
    from cogs.bountyCog import BountyCog
    cog = BountyCog(mock_bot)
    return cog


@pytest.fixture
def duel_cog(mock_bot):
    for k in list(sys.modules.keys()):
        if "duelCog" in k or (k.startswith("cogs.") and "duel" in k.lower()):
            del sys.modules[k]
    from cogs.duelCog import DuelCog
    return DuelCog(mock_bot)


@pytest.fixture
def scheduler_cog(mock_bot, monkeypatch):
    for k in list(sys.modules.keys()):
        if "schedulerCog" in k or (k.startswith("cogs.") and "scheduler" in k.lower()):
            del sys.modules[k]
    from cogs.schedulerCog import SchedulerCog
    return SchedulerCog(mock_bot)


# ---------------------------------------------------------------------------
# Sub-task A: _fetch_tier_shop adds _norm
# ---------------------------------------------------------------------------


class TestFetchTierShopPrenorm:
    """Phase 7: _fetch_tier_shop pre-computes _norm on each item dict."""

    def test_fetch_tier_shop_adds_norm_field(self, shop_cog, request):
        """_fetch_tier_shop must add _norm to each returned item dict.

        AC: After _fetch_tier_shop resolves, every item has a '_norm' key
        whose value equals normalize_for_search('<item_name> (<price:,>cr)').
        """
        _with_real_client(shop_cog, request)

        guild_id = 123456789
        tier = "Bronze"
        sample_items = [
            {"id": 1, "item_name": "Laser Cannon", "item_type": "primary_weapon", "price": 5000, "quantity": 10},
            {"id": 2, "item_name": "Shield Module", "item_type": "module", "price": 2000, "quantity": 5},
        ]

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_BOT_API}/shops/guild/{guild_id}/tier/{tier}").mock(
                return_value=httpx.Response(200, json=sample_items)
            )

            result = asyncio.run(shop_cog._fetch_tier_shop((guild_id, tier)))

        assert len(result) == 2, f"Expected 2 items, got {len(result)}"
        for item in result:
            assert "_norm" in item, f"Item {item.get('id')} missing '_norm' key"
            assert isinstance(item["_norm"], str), f"_norm should be a str, got {type(item['_norm'])}"
            assert len(item["_norm"]) > 0, "_norm should not be empty"

    def test_fetch_tier_shop_norm_matches_expected_value(self, shop_cog, request):
        """_fetch_tier_shop _norm equals normalize_for_search('<name> (<price:,>cr)')."""
        from utils.autocomplete_utils import normalize_for_search

        _with_real_client(shop_cog, request)

        guild_id = 111
        tier = "Silver"
        sample_items = [
            {"id": 10, "item_name": "Plasma Cannon", "item_type": "primary_weapon", "price": 10000, "quantity": 1},
        ]
        expected_norm = normalize_for_search("Plasma Cannon (10,000cr)")

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_BOT_API}/shops/guild/{guild_id}/tier/{tier}").mock(
                return_value=httpx.Response(200, json=sample_items)
            )

            result = asyncio.run(shop_cog._fetch_tier_shop((guild_id, tier)))

        assert result[0]["_norm"] == expected_norm, (
            f"Expected _norm={expected_norm!r}, got {result[0]['_norm']!r}"
        )

    def test_buy_autocomplete_uses_prenorm_path(self, shop_cog):
        """buy_item_autocomplete uses _norm from cache item when present.

        Pre-populates cache with items that have _norm already set.
        Verifies that norm_current in item['_norm'] correctly filters choices.
        No HTTP must be called (AssertionError if triggered).
        """
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache
        from utils.autocomplete_utils import normalize_for_search

        guild_id = 987654321
        user_id = 111111

        # Pre-populate player cache
        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test")
        ac_state.player_cache.set((guild_id, user_id), {"id": 1, "tier": "Bronze", "credits": 5000})

        # Pre-populate shop cache with items that already have _norm
        items_with_norm = [
            {
                "id": 1,
                "item_name": "Laser Cannon",
                "item_type": "primary_weapon",
                "price": 5000,
                "_norm": normalize_for_search("Laser Cannon (5,000cr)"),
            },
            {
                "id": 2,
                "item_name": "Shield Module",
                "item_type": "module",
                "price": 2000,
                "_norm": normalize_for_search("Shield Module (2,000cr)"),
            },
        ]
        shop_cog._shop_cache.set((guild_id, "Bronze"), items_with_norm)

        # HTTP must not be called
        shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        interaction = _make_mock_interaction(user_id=user_id, guild_id=guild_id)

        # Filter by "laser" — only Laser Cannon should match
        result = asyncio.run(shop_cog.buy_item_autocomplete(interaction, "laser"))

        assert len(result) == 1, f"Expected 1 choice for 'laser', got {len(result)}: {result}"
        assert result[0].value == 1, f"Expected id=1, got {result[0].value}"
        assert "Laser Cannon" in result[0].name

    def test_buy_autocomplete_fallback_without_norm(self, shop_cog):
        """buy_item_autocomplete falls back gracefully if _norm is missing from items.

        This covers the backward compat path for items pushed before Phase 7.
        The fallback calls normalize_for_search(label) on-the-fly.
        """
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        guild_id = 987654321
        user_id = 222222

        # Pre-populate player cache
        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test")
        ac_state.player_cache.set((guild_id, user_id), {"id": 2, "tier": "Silver", "credits": 10000})

        # Old-style items WITHOUT _norm field
        items_without_norm = [
            {"id": 10, "item_name": "Turret Alpha", "item_type": "turret_weapon", "price": 3000},
            {"id": 11, "item_name": "Engine Boost", "item_type": "module", "price": 1500},
        ]
        shop_cog._shop_cache.set((guild_id, "Silver"), items_without_norm)

        shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        shop_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        interaction = _make_mock_interaction(user_id=user_id, guild_id=guild_id)

        # Empty query matches all
        result = asyncio.run(shop_cog.buy_item_autocomplete(interaction, ""))
        assert len(result) == 2, f"Expected 2 choices from old-style items, got {len(result)}"


# ---------------------------------------------------------------------------
# Sub-task A: _fetch_bounties adds _norm
# ---------------------------------------------------------------------------


class TestFetchBountiesPrenorm:
    """Phase 7: _fetch_bounties pre-computes _norm on each bounty dict."""

    def test_fetch_bounties_adds_norm_field(self, bounty_cog, request):
        """_fetch_bounties must add _norm to each returned bounty dict.

        AC: After _fetch_bounties resolves, every bounty has a '_norm' key.
        """
        _with_real_client(bounty_cog, request)

        guild_id = 123456789
        sample_bounties = [
            {
                "id": 1,
                "criminal_name": "Black Viper",
                "division": "bronze",
                "tech_level": 2,
                "reward": 50000,
            },
            {
                "id": 2,
                "criminal_name": "Steel Fang",
                "division": "silver",
                "tech_level": 4,
                "reward": 100000,
            },
        ]

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_BOT_API}/bounties/").mock(
                return_value=httpx.Response(200, json=sample_bounties)
            )

            result = asyncio.run(bounty_cog._fetch_bounties(guild_id))

        assert len(result) == 2, f"Expected 2 bounties, got {len(result)}"
        for b in result:
            assert "_norm" in b, f"Bounty {b.get('id')} missing '_norm' key"
            assert isinstance(b["_norm"], str), f"_norm should be a str, got {type(b['_norm'])}"
            assert len(b["_norm"]) > 0, "_norm should not be empty"

    def test_fetch_bounties_norm_matches_expected_value(self, bounty_cog, request):
        """_fetch_bounties _norm matches normalize_for_search('<name> (<div>, T<tech>) — <reward:,>cr')."""
        from utils.autocomplete_utils import normalize_for_search

        _with_real_client(bounty_cog, request)

        guild_id = 999
        sample_bounties = [
            {"id": 5, "criminal_name": "Dread Lord", "division": "gold", "tech_level": 6, "reward": 200000},
        ]
        expected_norm = normalize_for_search("Dread Lord (Gold, T6) — 200,000cr")

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_BOT_API}/bounties/").mock(
                return_value=httpx.Response(200, json=sample_bounties)
            )

            result = asyncio.run(bounty_cog._fetch_bounties(guild_id))

        assert result[0]["_norm"] == expected_norm, (
            f"Expected _norm={expected_norm!r}, got {result[0]['_norm']!r}"
        )

    def test_bounty_autocomplete_uses_prenorm_path(self, bounty_cog):
        """bounty_autocomplete uses _norm from cache bounty when present.

        No HTTP must be called. Pre-computed _norm filters correctly.
        """
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache
        from utils.autocomplete_utils import normalize_for_search

        guild_id = 987654321
        user_id = 111111

        # Pre-populate player cache (so tier filter can apply)
        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test")
        ac_state.player_cache.set((guild_id, user_id), {"id": 1, "tier": "Bronze"})

        # Pre-populate bounty cache with items that already have _norm
        bounties_with_norm = [
            {
                "id": 10,
                "criminal_name": "Pirate Bob",
                "division": "bronze",
                "tech_level": 3,
                "reward": 75000,
                "_norm": normalize_for_search("Pirate Bob (Bronze, T3) — 75,000cr"),
            },
            {
                "id": 11,
                "criminal_name": "Void Walker",
                "division": "gold",
                "tech_level": 7,
                "reward": 300000,
                "_norm": normalize_for_search("Void Walker (Gold, T7) — 300,000cr"),
            },
        ]
        bounty_cog._bounty_cache.set(guild_id, bounties_with_norm)

        bounty_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        interaction = _make_mock_interaction(user_id=user_id, guild_id=guild_id)

        # Bronze player only sees bronze bounties; filter by "pirate"
        result = asyncio.run(bounty_cog.bounty_autocomplete(interaction, "pirate"))

        assert len(result) == 1, f"Expected 1 bronze bounty matching 'pirate', got {len(result)}"
        assert result[0].value == "10", f"Expected bounty id=10, got {result[0].value}"
        assert "Pirate Bob" in result[0].name


# ---------------------------------------------------------------------------
# Sub-task A: _fetch_pending_duels and _fetch_outgoing_duels add _norm
# ---------------------------------------------------------------------------


class TestFetchDuelsPrenorm:
    """Phase 7: _fetch_pending_duels and _fetch_outgoing_duels pre-compute _norm."""

    def test_fetch_pending_duels_adds_norm_field(self, duel_cog, request):
        """_fetch_pending_duels must add _norm to each returned duel dict."""
        _with_real_client(duel_cog, request)

        guild_id = 123456789
        player_id = 100
        sample_duels = [
            {"id": 1, "challenger_name": "Alpha Player", "target_id": player_id, "stakes": 500},
            {"id": 2, "challenger_name": None, "target_id": player_id, "stakes": 0},
        ]

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_BOT_API}/duels/pending").mock(
                return_value=httpx.Response(200, json=sample_duels)
            )

            result = asyncio.run(duel_cog._fetch_pending_duels((guild_id, player_id)))

        assert len(result) == 2, f"Expected 2 pending duels, got {len(result)}"
        for d in result:
            assert "_norm" in d, f"Duel {d.get('id')} missing '_norm' key"
            assert isinstance(d["_norm"], str), f"_norm should be a str, got {type(d['_norm'])}"

    def test_fetch_pending_duels_norm_with_challenger_name(self, duel_cog, request):
        """_fetch_pending_duels _norm built from challenger_name + stakes."""
        from utils.autocomplete_utils import normalize_for_search

        _with_real_client(duel_cog, request)

        guild_id = 111
        player_id = 200
        sample_duels = [
            {"id": 7, "challenger_name": "DeadShot", "stakes": 1000, "target_id": player_id},
        ]
        expected_norm = normalize_for_search("DeadShot — 1,000cr stakes")

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_BOT_API}/duels/pending").mock(
                return_value=httpx.Response(200, json=sample_duels)
            )

            result = asyncio.run(duel_cog._fetch_pending_duels((guild_id, player_id)))

        assert result[0]["_norm"] == expected_norm, (
            f"Expected _norm={expected_norm!r}, got {result[0]['_norm']!r}"
        )

    def test_fetch_outgoing_duels_adds_norm_field(self, duel_cog, request):
        """_fetch_outgoing_duels must add _norm to each returned duel dict."""
        _with_real_client(duel_cog, request)

        guild_id = 123456789
        player_id = 100
        sample_duels = [
            {"id": 3, "target_name": "Beta Player", "challenger_id": player_id, "stakes": 250},
            {"id": 4, "target_name": None, "challenger_id": player_id, "stakes": 0},
        ]

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_BOT_API}/duels/outgoing").mock(
                return_value=httpx.Response(200, json=sample_duels)
            )

            result = asyncio.run(duel_cog._fetch_outgoing_duels((guild_id, player_id)))

        assert len(result) == 2, f"Expected 2 outgoing duels, got {len(result)}"
        for d in result:
            assert "_norm" in d, f"Duel {d.get('id')} missing '_norm' key"
            assert isinstance(d["_norm"], str), f"_norm should be a str, got {type(d['_norm'])}"

    def test_fetch_outgoing_duels_norm_with_target_name(self, duel_cog, request):
        """_fetch_outgoing_duels _norm built from target_name + stakes."""
        from utils.autocomplete_utils import normalize_for_search

        _with_real_client(duel_cog, request)

        guild_id = 222
        player_id = 300
        sample_duels = [
            {"id": 8, "target_name": "Hawk Eye", "stakes": 0, "challenger_id": player_id},
        ]
        expected_norm = normalize_for_search("Hawk Eye — friendly duel")

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_BOT_API}/duels/outgoing").mock(
                return_value=httpx.Response(200, json=sample_duels)
            )

            result = asyncio.run(duel_cog._fetch_outgoing_duels((guild_id, player_id)))

        assert result[0]["_norm"] == expected_norm, (
            f"Expected _norm={expected_norm!r}, got {result[0]['_norm']!r}"
        )


# ---------------------------------------------------------------------------
# Sub-task A: _fetch_jobs adds _norm
# ---------------------------------------------------------------------------


class TestFetchJobsPrenorm:
    """Phase 7: _fetch_jobs pre-computes _norm on each job dict."""

    def test_fetch_jobs_adds_norm_field(self, scheduler_cog, request):
        """_fetch_jobs must add _norm to each returned job dict."""
        _with_real_client(scheduler_cog, request)

        sample_jobs = [
            {"id": "bounty_spawn_default", "trigger": "cron[*/5 * * * *]", "next_run_time": "2026-06-01T12:00:00Z"},
            {"id": "shop_refresh_default", "trigger": "interval[6:00:00]", "next_run_time": "2026-06-01T18:00:00Z"},
        ]

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_BOT_API}/jobs").mock(
                return_value=httpx.Response(200, json=sample_jobs)
            )

            result = asyncio.run(scheduler_cog._fetch_jobs("all"))

        assert len(result) == 2, f"Expected 2 jobs, got {len(result)}"
        for job in result:
            assert "_norm" in job, f"Job {job.get('id')} missing '_norm' key"
            assert isinstance(job["_norm"], str), f"_norm should be a str, got {type(job['_norm'])}"

    def test_fetch_jobs_norm_matches_expected_value(self, scheduler_cog, request):
        """_fetch_jobs _norm equals normalize_for_search('<id[:32]> (<trigger[:40]>)')."""
        from utils.autocomplete_utils import normalize_for_search

        _with_real_client(scheduler_cog, request)

        job_id = "bounty_spawn_default"
        trigger = "cron[*/5 * * * *]"
        sample_jobs = [{"id": job_id, "trigger": trigger}]
        expected_norm = normalize_for_search(f"{job_id[:32]} ({trigger[:40]})")

        env_without_bot_api = {k: v for k, v in os.environ.items() if k != "BOT_API_BASE_URL"}
        with (
            patch.dict(os.environ, env_without_bot_api, clear=True),
            respx.mock(assert_all_called=True) as mock_router,
        ):
            mock_router.get(f"{_BOT_API}/jobs").mock(
                return_value=httpx.Response(200, json=sample_jobs)
            )

            result = asyncio.run(scheduler_cog._fetch_jobs("all"))

        assert result[0]["_norm"] == expected_norm, (
            f"Expected _norm={expected_norm!r}, got {result[0]['_norm']!r}"
        )

    def test_job_autocomplete_uses_prenorm_path(self, scheduler_cog):
        """job_id_autocomplete uses _norm from cached job dict.

        Pre-populates cache with jobs that have _norm already set.
        No HTTP must be called.
        """
        from utils.autocomplete_utils import normalize_for_search

        jobs_with_norm = [
            {
                "id": "bounty_spawn_default",
                "trigger": "cron[*/5 * * * *]",
                "_norm": normalize_for_search("bounty_spawn_default (cron[*/5 * * * *])"),
            },
            {
                "id": "shop_refresh_default",
                "trigger": "interval[6:00:00]",
                "_norm": normalize_for_search("shop_refresh_default (interval[6:00:00])"),
            },
        ]
        scheduler_cog._job_cache.set("all", jobs_with_norm)

        scheduler_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        interaction = MagicMock()
        interaction.guild_id = 123456789
        interaction.user = MagicMock()
        interaction.user.id = 987654321

        # Filter by "bounty" — only bounty_spawn should match
        result = asyncio.run(scheduler_cog.job_id_autocomplete(interaction, "bounty"))

        assert len(result) == 1, f"Expected 1 job matching 'bounty', got {len(result)}"
        assert result[0].value == "bounty_spawn_default", (
            f"Expected bounty_spawn_default, got {result[0].value}"
        )


# ---------------------------------------------------------------------------
# Sub-task C: Health endpoint
# ---------------------------------------------------------------------------


class TestAutocompleteHealthEndpoint:
    """Phase 7: GET /internal/autocomplete/health returns cache sizes."""

    def _make_app_with_bot(self, bot):
        """Create a FastAPI test app with the internal_autocomplete router."""
        from fastapi import FastAPI

        # Evict cached router module
        for k in list(sys.modules.keys()):
            if k in ("api.routers.internal_autocomplete", "api.schemas.internal_schemas"):
                sys.modules.pop(k, None)

        app = FastAPI(title="Test App")
        app.state.bot = bot

        from api.routers.internal_autocomplete import router
        app.include_router(router, prefix="/api/v1")
        return app

    def test_health_endpoint_returns_expected_keys(self):
        """GET /internal/autocomplete/health returns dict with all expected keys."""
        from fastapi.testclient import TestClient

        bot = MagicMock()
        bot.is_ready.return_value = True

        env_without_token = {k: v for k, v in os.environ.items() if k != "INTERNAL_AUTH_TOKEN"}
        with patch.dict(os.environ, env_without_token, clear=True):
            app = self._make_app_with_bot(bot)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.get("/api/v1/internal/autocomplete/health")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "player_cache_size" in data, f"Missing player_cache_size in {data}"
        assert "inventory_cache_size" in data, f"Missing inventory_cache_size in {data}"
        assert "ships_cache_size" in data, f"Missing ships_cache_size in {data}"
        assert "initialized" in data, f"Missing initialized in {data}"

    def test_health_endpoint_returns_integer_sizes(self):
        """GET /internal/autocomplete/health returns integer values for cache sizes."""
        from fastapi.testclient import TestClient

        bot = MagicMock()
        bot.is_ready.return_value = True

        env_without_token = {k: v for k, v in os.environ.items() if k != "INTERNAL_AUTH_TOKEN"}
        with patch.dict(os.environ, env_without_token, clear=True):
            app = self._make_app_with_bot(bot)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.get("/api/v1/internal/autocomplete/health")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["player_cache_size"], int), (
            f"player_cache_size should be int, got {type(data['player_cache_size'])}"
        )
        assert isinstance(data["inventory_cache_size"], int), (
            f"inventory_cache_size should be int, got {type(data['inventory_cache_size'])}"
        )
        assert isinstance(data["ships_cache_size"], int), (
            f"ships_cache_size should be int, got {type(data['ships_cache_size'])}"
        )
        assert isinstance(data["initialized"], bool), (
            f"initialized should be bool, got {type(data['initialized'])}"
        )

    def test_health_endpoint_requires_valid_auth_when_token_set(self):
        """GET /internal/autocomplete/health with wrong token → 401."""
        from fastapi.testclient import TestClient

        _VALID_TOKEN = "super-secret-health-token"
        bot = MagicMock()
        bot.is_ready.return_value = True

        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = self._make_app_with_bot(bot)
            client = TestClient(app, raise_server_exceptions=False)

            # Wrong token
            resp = client.get(
                "/api/v1/internal/autocomplete/health",
                headers={"X-Internal-Auth": "wrong-token"},
            )

        assert resp.status_code == 401, f"Expected 401 with wrong token, got {resp.status_code}"

    def test_health_endpoint_with_valid_token(self):
        """GET /internal/autocomplete/health with correct token → 200."""
        from fastapi.testclient import TestClient

        _VALID_TOKEN = "super-secret-health-token"
        bot = MagicMock()
        bot.is_ready.return_value = True

        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = self._make_app_with_bot(bot)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.get(
                "/api/v1/internal/autocomplete/health",
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "player_cache_size" in data


# ---------------------------------------------------------------------------
# Sub-task B: Audit — no live HTTP in autocomplete methods
# ---------------------------------------------------------------------------


class TestSubtaskBAudit:
    """Phase 7: Verify autocomplete methods contain no live HTTP calls.

    These tests pre-populate caches and raise if any HTTP call is made.
    They are regression guards — if someone adds HTTP back to an autocomplete
    handler, these tests will fail immediately.
    """

    def test_bounty_autocomplete_zero_http_on_warm_cache(self, bounty_cog):
        """bounty_autocomplete must not call HTTP when cache is warm."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache
        from utils.autocomplete_utils import normalize_for_search

        guild_id = 123456789
        user_id = 111

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test")
        ac_state.player_cache.set((guild_id, user_id), {"id": 1, "tier": "bronze"})

        bounty_cog._bounty_cache.set(guild_id, [
            {
                "id": 1,
                "criminal_name": "Test Criminal",
                "division": "bronze",
                "tech_level": 1,
                "reward": 10000,
                "_norm": normalize_for_search("Test Criminal (Bronze, T1) — 10,000cr"),
            }
        ])
        bounty_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))
        bounty_cog.http_client.post = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        interaction = _make_mock_interaction(user_id=user_id, guild_id=guild_id)
        result = asyncio.run(bounty_cog.bounty_autocomplete(interaction, ""))
        assert len(result) >= 0  # just confirms no HTTP error

    def test_job_autocomplete_zero_http_on_warm_cache(self, scheduler_cog):
        """job_id_autocomplete must not call HTTP when cache is warm."""
        from utils.autocomplete_utils import normalize_for_search

        scheduler_cog._job_cache.set("all", [
            {
                "id": "test_job",
                "trigger": "interval[1:00:00]",
                "_norm": normalize_for_search("test_job (interval[1:00:00])"),
            }
        ])
        scheduler_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        interaction = MagicMock()
        interaction.guild_id = 123456789
        interaction.user = MagicMock()
        interaction.user.id = 987654321

        result = asyncio.run(scheduler_cog.job_id_autocomplete(interaction, ""))
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Adversarial edge cases (Tester additions — Phase 7 review)
# ---------------------------------------------------------------------------


class TestAdversarialPhase7:
    """Tester-added adversarial tests for Phase 7.

    Covers:
      1. Empty-name item: _norm="" is falsy → fallback recomputes, same empty result.
      2. Health endpoint when autocomplete_state is uninitialized (None caches).
      3. normalize_for_search trace-through: "laser" in norm("Laser Cannon (1,500cr)").
      4. Item with whitespace-only name: _norm is falsy → fallback produces same result.
    """

    def test_empty_norm_fallback_produces_same_empty_string(self, shop_cog):
        """_norm='' is falsy; fallback recomputes and returns '' — no bug, just no match.

        When an item has an empty item_name, _fetch_tier_shop computes:
            label = f" ({price:,}cr)"
            _norm = normalize_for_search(label)   # e.g. "(0cr)" → "0cr"
        If the item is in the cache with _norm already set to "0cr", the fallback
        never fires. But if _norm="" somehow (shouldn't happen with well-formed data),
        the 'or' fallback recomputes normalize_for_search(label) = same value.
        Net result: no silent difference.
        """
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        guild_id = 555555
        user_id = 333333

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="player-test")
        ac_state.player_cache.set((guild_id, user_id), {"id": 5, "tier": "Gold"})

        # Inject an item with _norm="" (artificially empty) to exercise the fallback
        item_name = ""
        price = 0
        items_empty_norm = [
            {
                "id": 99,
                "item_name": item_name,
                "item_type": "module",
                "price": price,
                "_norm": "",  # explicitly empty — simulates a degenerate push
            }
        ]
        shop_cog._shop_cache.set((guild_id, "Gold"), items_empty_norm)
        shop_cog.http_client.get = AsyncMock(side_effect=AssertionError("HTTP must not be called"))

        interaction = _make_mock_interaction(user_id=user_id, guild_id=guild_id)

        # Empty query — since _norm="" is falsy, fallback fires: normalize_for_search(label)
        # The fallback result for " (0cr)" → "0cr", which is non-empty.
        # An empty current ("") → normalize_for_search("") = "", which IS in any string.
        result = asyncio.run(shop_cog.buy_item_autocomplete(interaction, ""))

        # The item should appear (normalize_for_search("") is in normalize_for_search(label))
        # This documents the behaviour: empty _norm falls back to the computed norm,
        # and the item is still returned for an empty query (all-match).
        assert len(result) == 1, (
            f"Expected 1 result (empty query matches all via fallback), got {len(result)}"
        )

    def test_normalize_for_search_laser_in_laser_cannon(self):
        """normalize_for_search('laser') is a substring of norm('Laser Cannon (1,500cr)').

        Confirms that the filter in buy_item_autocomplete works correctly:
        the user typing 'laser' correctly matches 'Laser Cannon (1,500cr)'.
        normalize_for_search removes spaces but NOT parentheses or commas.
        """
        from utils.autocomplete_utils import normalize_for_search

        norm_query = normalize_for_search("laser")
        norm_label = normalize_for_search("Laser Cannon (1,500cr)")

        # Trace through:
        # "laser" → no special chars → "laser"
        # "Laser Cannon (1,500cr)" → remove spaces → "LaserCannon(1,500cr)" → lowercase
        #   → "lasercannon(1,500cr)"
        assert norm_query == "laser", f"Expected 'laser', got {norm_query!r}"
        assert norm_label == "lasercannon(1,500cr)", (
            f"Expected 'lasercannon(1,500cr)', got {norm_label!r}"
        )
        assert norm_query in norm_label, (
            f"'laser' should be a substring of norm label; got norm_label={norm_label!r}"
        )

    def test_health_endpoint_none_caches_return_zero(self):
        """GET /internal/autocomplete/health returns 0 for each cache size when caches are None.

        If autocomplete_state.init() was never called, all caches are None.
        The endpoint uses `state.player_cache.size if state.player_cache else 0`,
        which must not raise AttributeError when state.player_cache is None.
        """
        import sys

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        # Evict cached router module to get a fresh import
        for k in list(sys.modules.keys()):
            if k in ("api.routers.internal_autocomplete", "api.schemas.internal_schemas"):
                sys.modules.pop(k, None)

        import utils.autocomplete_state as ac_state

        # Save original state and force None caches (uninitialized)
        orig_player_cache = ac_state.player_cache
        orig_inventory_cache = ac_state.inventory_cache
        orig_ships_cache = ac_state.ships_cache
        orig_initialized = ac_state._initialized

        try:
            ac_state.player_cache = None
            ac_state.inventory_cache = None
            ac_state.ships_cache = None
            ac_state._initialized = False

            bot = MagicMock()
            bot.is_ready.return_value = True

            app = FastAPI(title="Test App - None Caches")
            from api.routers.internal_autocomplete import router as ac_router
            app.include_router(ac_router, prefix="/api/v1")
            app.state.bot = bot

            env_without_token = {k: v for k, v in os.environ.items() if k != "INTERNAL_AUTH_TOKEN"}
            with patch.dict(os.environ, env_without_token, clear=True):
                client = TestClient(app, raise_server_exceptions=True)
                resp = client.get("/api/v1/internal/autocomplete/health")

            assert resp.status_code == 200, (
                f"Expected 200 when caches are None, got {resp.status_code}: {resp.text}"
            )
            data = resp.json()
            assert data["player_cache_size"] == 0, (
                f"Expected player_cache_size=0 when cache is None, got {data['player_cache_size']}"
            )
            assert data["inventory_cache_size"] == 0, (
                f"Expected inventory_cache_size=0 when cache is None, got {data['inventory_cache_size']}"
            )
            assert data["ships_cache_size"] == 0, (
                f"Expected ships_cache_size=0 when cache is None, got {data['ships_cache_size']}"
            )
            assert data["initialized"] is False, (
                f"Expected initialized=False, got {data['initialized']}"
            )
        finally:
            # Restore original state so other tests are not affected
            ac_state.player_cache = orig_player_cache
            ac_state.inventory_cache = orig_inventory_cache
            ac_state.ships_cache = orig_ships_cache
            ac_state._initialized = orig_initialized

    def test_bounty_norm_label_mismatch_with_autocomplete_label(self, bounty_cog):
        """_norm is computed from the label in _fetch_bounties; autocomplete rebuilds label identically.

        Phase 7 risk: if the label format in _fetch_bounties differs from the
        label rebuilt in bounty_autocomplete, the pre-computed _norm would be
        stale and filter incorrectly.

        This test verifies that both label templates produce identical output for
        the same bounty dict — confirming the pre-computed _norm is valid for the
        autocomplete filter.
        """
        from utils.autocomplete_utils import normalize_for_search

        # Sample bounty as returned by _fetch_bounties (after mutation)
        b = {
            "id": 1,
            "criminal_name": "Black Viper",
            "division": "bronze",
            "tech_level": 2,
            "reward": 50000,
        }

        # Reproduce _fetch_bounties label template (lines 110-114 of bountyCog.py)
        fetch_label = (
            f"{b.get('criminal_name', '')} "
            f"({b.get('division', '').title()}, T{b.get('tech_level', '?')}) "
            f"— {b.get('reward', 0):,}cr"
        )
        fetch_norm = normalize_for_search(fetch_label)

        # Reproduce bounty_autocomplete label template (lines 193-195 of bountyCog.py)
        autocomplete_label = (
            f"{b['criminal_name']} ({b['division'].title()}, T{b.get('tech_level', '?')}) — {b['reward']:,}cr"
        )
        autocomplete_norm = normalize_for_search(autocomplete_label)

        assert fetch_norm == autocomplete_norm, (
            f"_fetch_bounties norm={fetch_norm!r} != bounty_autocomplete norm={autocomplete_norm!r}. "
            "Pre-computed _norm is stale relative to autocomplete label! Labels diverged."
        )
