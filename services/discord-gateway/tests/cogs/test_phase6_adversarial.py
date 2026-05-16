"""Phase 6 Adversarial Tests — Tester-added edge cases and AC-PERF checks.

This file adds:
  - AC-PERF-2: buy_item_autocomplete warm path fires 3 simulated keystrokes with HTTP client
    configured to RAISE if called — verifies 0 HTTP calls on warm cache.
  - bounty_autocomplete tier filter (Silver player sees only Silver bounties) and graceful
    degradation (player cache cold miss → ALL bounties shown, no error).
  - remove_item_autocomplete with unknown target user: player AND inventory both miss, both
    schedule refresh, [] returned without error.
  - job_id_autocomplete empty cache (peek returns None → [] returned, schedule_refresh fired).
  - sell_item_autocomplete with empty inventory list (warm cache returning [] → [] choices,
    no extra refresh scheduled, no error).
  - AC-PERF-3: cold bounty_autocomplete completes in under 20ms (both _bounty_cache and
    player_cache cold).
"""

import asyncio
import os
import sys
import time
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup — must be before any src imports
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")
_all_loggers: dict[str, MagicMock] = {}


def _make_mock_logger(*_args, **_kwargs):
    name = _args[0] if _args else None
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    logger.exception = MagicMock()
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
    """Build a minimal mock interaction."""
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.user.display_name = "TestUser"
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.namespace = MagicMock()
    interaction.namespace.user = None
    return interaction


def _make_player_data(player_id=1, tier="Bronze", credits=1000):
    return {"id": player_id, "tier": tier, "credits": credits}


def _make_shop_item(item_id=1, item_name="TestItem", price=100, tier="Bronze"):
    return {"id": item_id, "item_name": item_name, "price": price, "tier": tier}


def _make_bounty_dict(bounty_id, criminal_name, division, reward=1000):
    return {
        "id": bounty_id,
        "criminal_name": criminal_name,
        "division": division,
        "reward": reward,
        "tech_level": 1,
        "guild_id": 987654321,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_bot():
    bot = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock()
    return bot


@pytest.fixture
def shop_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.shopCog import ShopCog

    cog = ShopCog(mock_bot)
    # Replace HTTP client with a tight mock that RAISES by default.
    # Individual tests must explicitly relax or tighten this as needed.
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


@pytest.fixture
def bounty_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.bountyCog import BountyCog

    cog = BountyCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


@pytest.fixture
def admin_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.adminCog import AdminCog

    cog = AdminCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


@pytest.fixture
def scheduler_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    from cogs.schedulerCog import SchedulerCog

    cog = SchedulerCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


# ---------------------------------------------------------------------------
# AC-PERF-2: buy_item_autocomplete — 3 keystrokes on warm cache, ZERO HTTP
# ---------------------------------------------------------------------------


class TestAcPerf2BuyItemAutocompleteZeroHttp:
    """AC-PERF-2: Hot path must not call the HTTP client at all.

    Strategy: configure http_client to RAISE AssertionError if called, then
    fire three simulated keystrokes against a warm player_cache + shop_cache.
    If any keystroke triggers an HTTP call the test fails immediately.
    """

    def _warm_caches(self, cog, guild_id=987654321, user_id=111111111):
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="p6-player-perf2")
        ac_state.player_cache.set((guild_id, user_id), _make_player_data(tier="Bronze"))

        items = [
            _make_shop_item(item_id=i, item_name=f"Item{i}", price=100 * i)
            for i in range(1, 6)
        ]
        cog._shop_cache.set((guild_id, "Bronze"), items)

    def test_three_keystrokes_on_warm_cache_zero_http(self, shop_cog):
        """AC-PERF-2: 3 simulated keystrokes against warm cache; http_client NEVER called."""
        guild_id = 987654321
        user_id = 111111111

        self._warm_caches(shop_cog, guild_id=guild_id, user_id=user_id)

        # Configure HTTP client to RAISE if any method is invoked.
        shop_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("AC-PERF-2 VIOLATION: HTTP called on warm path")
        )
        shop_cog.http_client.post = AsyncMock(
            side_effect=AssertionError("AC-PERF-2 VIOLATION: HTTP called on warm path")
        )

        keystrokes = ["", "It", "Item3"]
        for keystroke in keystrokes:
            interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)
            result = asyncio.run(shop_cog.buy_item_autocomplete(interaction, keystroke))
            # All keystrokes must return from cache
            assert isinstance(result, list), f"Expected list for keystroke={keystroke!r}, got {type(result)}"

        # Verify no HTTP calls were made at all
        shop_cog.http_client.get.assert_not_called()
        shop_cog.http_client.post.assert_not_called()

    def test_three_keystrokes_return_correct_results(self, shop_cog):
        """AC-PERF-2: Warm cache keystrokes return correct (non-empty) results."""
        guild_id = 987654321
        user_id = 111111111

        self._warm_caches(shop_cog, guild_id=guild_id, user_id=user_id)

        shop_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("AC-PERF-2 VIOLATION: HTTP called on warm path")
        )
        shop_cog.http_client.post = AsyncMock(
            side_effect=AssertionError("AC-PERF-2 VIOLATION: HTTP called on warm path")
        )

        # Empty search should return all 5 items
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)
        result = asyncio.run(shop_cog.buy_item_autocomplete(interaction, ""))
        assert len(result) == 5, f"Expected 5 choices for empty search, got {len(result)}"

        # Filtered search should return matching items
        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)
        result = asyncio.run(shop_cog.buy_item_autocomplete(interaction, "Item3"))
        assert len(result) == 1
        assert result[0].value == 3


# ---------------------------------------------------------------------------
# bounty_autocomplete tier filter: Silver player + cold player cache
# ---------------------------------------------------------------------------


class TestBountyAutocompleteTierFilter:
    """Phase 6: bounty_autocomplete tier filter + graceful degradation.

    These tests specifically verify:
    - When player_cache is warm and returns tier "Silver", only Silver bounties appear.
    - When player_cache is cold, ALL bounties appear (graceful degradation).
    """

    def _setup_warm_bounty_cache(self, cog, bounties, guild_id=987654321):
        cog._bounty_cache.set(guild_id, bounties)

    def _setup_warm_player_cache(self, tier, guild_id=987654321, user_id=111111111):
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="p6-player-tier-test")
        ac_state.player_cache.set((guild_id, user_id), {"id": 1, "tier": tier})

    def test_silver_player_sees_only_silver_bounties(self, bounty_cog):
        """When player_cache is warm with tier='Silver', only Silver division bounties appear."""
        guild_id = 987654321
        user_id = 111111111

        bounties = [
            _make_bounty_dict(1, "SilverFox", "silver", reward=2000),
            _make_bounty_dict(2, "GoldEagle", "gold", reward=5000),
            _make_bounty_dict(3, "BronzeViper", "bronze", reward=500),
        ]
        self._setup_warm_bounty_cache(bounty_cog, bounties, guild_id=guild_id)
        self._setup_warm_player_cache("Silver", guild_id=guild_id, user_id=user_id)

        # No HTTP should be called
        bounty_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("HTTP must not be called on warm path")
        )
        bounty_cog.http_client.post = AsyncMock(
            side_effect=AssertionError("HTTP must not be called on warm path")
        )

        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)
        result = asyncio.run(bounty_cog.bounty_autocomplete(interaction, ""))

        # Only the Silver bounty should appear
        assert len(result) == 1, f"Expected 1 Silver bounty, got {len(result)}: {[c.name for c in result]}"
        assert "SilverFox" in result[0].name
        assert result[0].value == "1"

    def test_cold_player_cache_shows_all_bounties_graceful_degradation(self, bounty_cog):
        """When player_cache is cold, ALL bounties appear (graceful degradation, no error)."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        guild_id = 987654321
        user_id = 222222222  # Different user ID to guarantee cache cold miss

        bounties = [
            _make_bounty_dict(1, "BronzeViper", "bronze", reward=500),
            _make_bounty_dict(2, "SilverFox", "silver", reward=2000),
            _make_bounty_dict(3, "GoldEagle", "gold", reward=5000),
        ]
        bounty_cog._bounty_cache.set(guild_id, bounties)

        # Ensure player cache is cold for this user
        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="p6-player-cold")
        ac_state.player_cache.invalidate((guild_id, user_id))

        # No HTTP should be called (cold miss returns [], no HTTP per-keystroke)
        bounty_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("HTTP must not be called on warm-bounty / cold-player path")
        )
        bounty_cog.http_client.post = AsyncMock(
            side_effect=AssertionError("HTTP must not be called on warm-bounty / cold-player path")
        )

        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)
        result = asyncio.run(bounty_cog.bounty_autocomplete(interaction, ""))

        # Graceful degradation: ALL 3 bounties shown (no tier filter when player cache misses)
        assert len(result) == 3, (
            f"Expected all 3 bounties on player cache miss, got {len(result)}: {[c.name for c in result]}"
        )


# ---------------------------------------------------------------------------
# remove_item_autocomplete — unknown target user (both caches miss)
# ---------------------------------------------------------------------------


class TestRemoveItemAutocompleteUnknownTargetUser:
    """remove_item_autocomplete with unknown target user.

    When the target Discord member is not in player_cache:
    - player_cache.peek() returns None → schedule_refresh called → [] returned
    - inventory_cache.peek() is never reached (player_id unknown)
    - No error is raised
    - [] is returned immediately
    """

    def test_unknown_target_user_both_caches_miss_returns_empty(self, admin_cog):
        """Unknown target user: player cache misses → schedule refresh → [] returned, no error."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        guild_id = 987654321
        unknown_user_id = 999999999  # Not in any cache

        target_user = MagicMock()
        target_user.id = unknown_user_id

        interaction = _create_mock_interaction(guild_id=guild_id)
        interaction.namespace = MagicMock()
        interaction.namespace.user = target_user

        # Ensure player_cache exists but has NO entry for this user
        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="p6-admin-player-unknown")
        ac_state.player_cache.invalidate((guild_id, unknown_user_id))

        # Ensure inventory_cache exists but has no entries
        if ac_state.inventory_cache is None:
            ac_state.inventory_cache = AutocompleteCache(name="p6-admin-inventory-unknown")

        # HTTP must not be called — this is the zero-HTTP path
        admin_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("HTTP must not be called on hot path")
        )
        admin_cog.http_client.post = AsyncMock(
            side_effect=AssertionError("HTTP must not be called on hot path")
        )

        # Set up catalog as empty so fallback returns [] (not catalog items)
        # The fallback uses _item_catalog which is an in-memory dict-like structure
        for cat in ("primary_weapon", "secondary_weapon", "turret_weapon", "module"):
            admin_cog._item_catalog.set(cat, [])

        result = asyncio.run(admin_cog.remove_item_autocomplete(interaction, ""))

        # Must return [] without raising any exception
        assert isinstance(result, list), f"Expected list, got {type(result)}"
        assert result == [], f"Expected empty list for unknown user, got {result}"

    def test_unknown_target_user_no_error_raised(self, admin_cog):
        """Unknown target user gracefully returns [] without any exception propagating."""
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        guild_id = 123456789
        unknown_user_id = 777777777

        target_user = MagicMock()
        target_user.id = unknown_user_id

        interaction = _create_mock_interaction(guild_id=guild_id)
        interaction.namespace = MagicMock()
        interaction.namespace.user = target_user

        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="p6-admin-player-unknown2")
        ac_state.player_cache.invalidate((guild_id, unknown_user_id))

        if ac_state.inventory_cache is None:
            ac_state.inventory_cache = AutocompleteCache(name="p6-admin-inventory-unknown2")

        # Don't raise on HTTP — just track calls
        call_count = {"get": 0, "post": 0}

        async def _get(*args, **kwargs):
            call_count["get"] += 1
            raise AssertionError("HTTP GET must not be called")

        async def _post(*args, **kwargs):
            call_count["post"] += 1
            raise AssertionError("HTTP POST must not be called")

        admin_cog.http_client.get = _get
        admin_cog.http_client.post = _post

        for cat in ("primary_weapon", "secondary_weapon", "turret_weapon", "module"):
            admin_cog._item_catalog.set(cat, [])

        # Must not raise
        result = asyncio.run(admin_cog.remove_item_autocomplete(interaction, ""))

        assert isinstance(result, list)
        assert call_count["get"] == 0, "HTTP GET was unexpectedly called"
        assert call_count["post"] == 0, "HTTP POST was unexpectedly called"


# ---------------------------------------------------------------------------
# job_id_autocomplete — empty (cold) cache
# ---------------------------------------------------------------------------


class TestJobIdAutocompleteColdCache:
    """job_id_autocomplete: cold _job_cache returns [] and schedules refresh.

    Verifies peek("all") returns None on cold cache → [] returned
    and schedule_refresh("all") fired (without HTTP per keystroke).
    """

    def test_cold_cache_returns_empty_list(self, scheduler_cog):
        """Cold _job_cache (peek returns None) → [] returned immediately."""
        # Ensure cache is empty
        scheduler_cog._job_cache.invalidate("all")

        scheduler_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("HTTP must not be called per-keystroke on cold miss")
        )

        interaction = _create_mock_interaction()
        result = asyncio.run(scheduler_cog.job_id_autocomplete(interaction, ""))

        assert result == [], f"Expected [] on cold cache, got {result}"

    def test_cold_cache_schedules_refresh(self, scheduler_cog):
        """Cold _job_cache fires schedule_refresh('all') without HTTP per keystroke."""
        from unittest.mock import patch

        scheduler_cog._job_cache.invalidate("all")

        scheduler_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("HTTP must not be called per-keystroke on cold miss")
        )

        # Patch schedule_refresh to track calls
        with patch.object(scheduler_cog._job_cache, "schedule_refresh") as mock_refresh:
            interaction = _create_mock_interaction()
            result = asyncio.run(scheduler_cog.job_id_autocomplete(interaction, "bounty"))

            assert result == []
            mock_refresh.assert_called_once_with("all")

    def test_warm_cache_with_jobs_returns_choices(self, scheduler_cog):
        """Warm _job_cache returns matching choices without HTTP."""
        jobs = [
            {"id": "bounty_spawn_default", "trigger": "interval[minutes=5]"},
            {"id": "shop_refresh_default", "trigger": "interval[hours=6]"},
        ]
        scheduler_cog._job_cache.set("all", jobs)

        scheduler_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("HTTP must not be called on warm path")
        )

        interaction = _create_mock_interaction()
        result = asyncio.run(scheduler_cog.job_id_autocomplete(interaction, "bounty"))

        assert len(result) == 1
        assert result[0].value == "bounty_spawn_default"


# ---------------------------------------------------------------------------
# sell_item_autocomplete — warm cache with empty inventory list
# ---------------------------------------------------------------------------


class TestSellItemAutocompleteEmptyInventory:
    """sell_item_autocomplete: warm inventory cache returning [] (empty list).

    When the cache is warm but returns an empty list (not None), the handler
    must return [] without scheduling another refresh and without raising.
    Key difference from a cold miss (None): empty list = valid cache hit, no refresh.
    """

    def _setup_caches_with_empty_inventory(self, cog, guild_id=987654321, user_id=111111111):
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        player_id = 1
        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="p6-player-sell-empty")
        ac_state.player_cache.set((guild_id, user_id), {"id": player_id, "tier": "Bronze"})

        if ac_state.inventory_cache is None:
            ac_state.inventory_cache = AutocompleteCache(name="p6-inventory-sell-empty")
        # Set EMPTY list (not None) — this is a warm-but-empty cache hit
        ac_state.inventory_cache.set((guild_id, player_id), [])

    def test_warm_empty_inventory_returns_empty_choices(self, shop_cog):
        """sell_item_autocomplete with warm but empty inventory → [] choices without error."""
        guild_id = 987654321
        user_id = 111111111

        self._setup_caches_with_empty_inventory(shop_cog, guild_id=guild_id, user_id=user_id)

        shop_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("HTTP must not be called: inventory is warm (empty list)")
        )
        shop_cog.http_client.post = AsyncMock(
            side_effect=AssertionError("HTTP must not be called: inventory is warm (empty list)")
        )

        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)
        result = asyncio.run(shop_cog.sell_item_autocomplete(interaction, ""))

        assert result == [], f"Expected [] for empty inventory, got {result}"

    def test_warm_empty_inventory_no_refresh_scheduled(self, shop_cog):
        """sell_item_autocomplete with warm empty inventory does NOT schedule a refresh."""
        from unittest.mock import patch

        guild_id = 987654321
        user_id = 111111111

        self._setup_caches_with_empty_inventory(shop_cog, guild_id=guild_id, user_id=user_id)

        shop_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("HTTP must not be called")
        )
        shop_cog.http_client.post = AsyncMock(
            side_effect=AssertionError("HTTP must not be called")
        )

        import utils.autocomplete_state as ac_state

        with patch.object(ac_state.inventory_cache, "schedule_refresh") as mock_refresh:
            interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)
            result = asyncio.run(shop_cog.sell_item_autocomplete(interaction, ""))

            # Empty list is a valid cache HIT — no refresh should be scheduled
            assert result == []
            mock_refresh.assert_not_called()

    def test_warm_empty_inventory_with_filter_still_returns_empty(self, shop_cog):
        """sell_item_autocomplete: warm empty inventory with text filter → [] choices."""
        guild_id = 987654321
        user_id = 111111111

        self._setup_caches_with_empty_inventory(shop_cog, guild_id=guild_id, user_id=user_id)

        shop_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("HTTP must not be called")
        )
        shop_cog.http_client.post = AsyncMock(
            side_effect=AssertionError("HTTP must not be called")
        )

        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)
        result = asyncio.run(shop_cog.sell_item_autocomplete(interaction, "Laser"))

        assert result == [], f"Expected [] for empty inventory with filter, got {result}"


# ---------------------------------------------------------------------------
# AC-PERF-3: cold bounty_autocomplete must complete in under 20ms
# ---------------------------------------------------------------------------


class TestAcPerf3BountyAutocompleteColdTiming:
    """AC-PERF-3: Cold bounty_autocomplete (both caches cold) must return in < 20ms.

    Uses time.perf_counter to measure wall-clock time of the autocomplete call.
    Both _bounty_cache and player_cache are cold to exercise the cold-miss path.
    """

    def _ensure_caches_cold(self, cog, guild_id=987654321, user_id=111111111):
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        # Cold out bounty cache
        cog._bounty_cache.invalidate(guild_id)

        # Cold out player cache
        if ac_state.player_cache is None:
            ac_state.player_cache = AutocompleteCache(name="p6-player-perf3")
        ac_state.player_cache.invalidate((guild_id, user_id))

    def test_cold_miss_returns_empty_and_under_20ms(self, bounty_cog):
        """AC-PERF-3: Cold bounty_autocomplete returns [] in under 20ms."""
        guild_id = 987654321
        user_id = 111111111

        self._ensure_caches_cold(bounty_cog, guild_id=guild_id, user_id=user_id)

        # HTTP should NOT be called (cold miss is immediate [] + background refresh)
        bounty_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("AC-PERF-3: HTTP must not be called per-keystroke on cold miss")
        )
        bounty_cog.http_client.post = AsyncMock(
            side_effect=AssertionError("AC-PERF-3: HTTP must not be called per-keystroke on cold miss")
        )

        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        t0 = time.perf_counter()
        result = asyncio.run(bounty_cog.bounty_autocomplete(interaction, ""))
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert result == [], f"AC-PERF-3: Expected [] on cold cache, got {result}"
        assert elapsed_ms < 20, (
            f"AC-PERF-3 VIOLATION: Cold bounty_autocomplete took {elapsed_ms:.2f}ms "
            f"(threshold: 20ms). Hot path must not block."
        )

    def test_cold_miss_both_bounty_and_player_returns_empty(self, bounty_cog):
        """AC-PERF-3: With BOTH bounty cache AND player cache cold, returns [] immediately."""
        guild_id = 555555555
        user_id = 444444444

        self._ensure_caches_cold(bounty_cog, guild_id=guild_id, user_id=user_id)

        bounty_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("HTTP must not be called synchronously")
        )
        bounty_cog.http_client.post = AsyncMock(
            side_effect=AssertionError("HTTP must not be called synchronously")
        )

        interaction = _create_mock_interaction(user_id=user_id, guild_id=guild_id)

        t0 = time.perf_counter()
        result = asyncio.run(bounty_cog.bounty_autocomplete(interaction, "BlackViper"))
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert result == []
        assert elapsed_ms < 20, (
            f"AC-PERF-3 VIOLATION: Cold bounty_autocomplete took {elapsed_ms:.2f}ms > 20ms"
        )
