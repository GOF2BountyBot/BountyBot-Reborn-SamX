"""Phase 2 Bucket-C tests — new caches + cache-reuse for the 3 live-HTTP handlers.

Covers:
  - combatLogCog._combatlog_cache: cold-fill on 0th keystroke, LRU eviction at
    max_entries, invalidate drops the per-user key (next keystroke cold-fills).
  - adminCog.admin_duel_autocomplete: "Cancel ALL" sentinel always first (even cold),
    cold-fill, invalidate drops the guild key.
  - adminCog.player_ship_autocomplete: warm ships_cache hit → no HTTP, cold target →
    cold-fill, uncached/unresolvable target → catalog fallback (not empty).
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_a, **_k):
    lg = MagicMock()
    for m in ("info", "debug", "warning", "error", "trace", "critical", "exception"):
        setattr(lg, m, MagicMock())
    return lg


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _evict_discord_modules():
    for _mod in list(sys.modules):
        if _mod == "discord" or _mod.startswith("discord."):
            sys.modules.pop(_mod, None)


def _mock_interaction(user_id=111, guild_id=987654321, namespace_user=None):
    it = MagicMock()
    it.guild_id = guild_id
    it.user = MagicMock()
    it.user.id = user_id
    it.namespace = MagicMock()
    it.namespace.user = namespace_user
    return it


@pytest.fixture(scope="module")
def mock_bot():
    bot = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock()
    return bot


# ---------------------------------------------------------------------------
# combatLogCog._combatlog_cache
# ---------------------------------------------------------------------------


@pytest.fixture
def combatlog_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.combatLogCog import CombatLogCog

    cog = CombatLogCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


def _combat_item(row_id, opponent="Foe", ordinal=1):
    return {
        "id": row_id,
        "guild_id": 987654321,
        "context": "duel",
        "opponent_name": opponent,
        "combatant1_name": "Me",
        "combatant2_name": opponent,
        "outcome": "won",
        "created_at": "2026-06-03T12:00:00+00:00",
        "ordinal": ordinal,
    }


class TestCombatLogCache:
    def test_coldfill_first_keystroke_not_empty(self, combatlog_cog):
        items = [_combat_item(1, "General_Failure")]

        async def _fetch(_key):
            return items

        combatlog_cog._combatlog_cache._refresh_fn = _fetch
        res = asyncio.run(combatlog_cog.battle_autocomplete(_mock_interaction(), ""))
        assert len(res) == 1  # cold-fill populated the 0th keystroke
        assert res[0].value == 1

    def test_warm_peek_zero_refresh(self, combatlog_cog):
        key = (987654321, 111)
        combatlog_cog._combatlog_cache.set(key, [_combat_item(1, "Foo")])
        calls = {"n": 0}

        async def _fetch(_key):
            calls["n"] += 1
            return []

        combatlog_cog._combatlog_cache._refresh_fn = _fetch
        res = asyncio.run(combatlog_cog.battle_autocomplete(_mock_interaction(), ""))
        assert len(res) == 1
        assert calls["n"] == 0  # warm peek never awaited refresh_fn

    def test_invalidate_drops_user_key_then_coldfills(self, combatlog_cog):
        key = (987654321, 111)
        combatlog_cog._combatlog_cache.set(key, [_combat_item(1, "Old")])
        combatlog_cog._combatlog_cache.invalidate(key)
        assert combatlog_cog._combatlog_cache.peek(key) is None

        async def _fetch(_key):
            return [_combat_item(2, "New")]

        combatlog_cog._combatlog_cache._refresh_fn = _fetch
        res = asyncio.run(combatlog_cog.battle_autocomplete(_mock_interaction(), ""))
        assert len(res) == 1
        assert res[0].value == 2  # post-invalidate cold-fill served fresh data

    def test_lru_eviction_at_max_entries(self):
        from cogs._shared.autocomplete_cache import AutocompleteCache

        clock = {"t": 0.0}
        cache = AutocompleteCache(name="combatlog-lru", max_entries=2, _monotonic=lambda: clock["t"])
        cache.set((1, 1), ["a"])
        clock["t"] += 1
        cache.set((1, 2), ["b"])
        clock["t"] += 1
        cache.set((1, 3), ["c"])  # evicts oldest (1,1)
        assert cache.size == 2
        assert cache.peek((1, 1)) is None
        assert cache.peek((1, 2)) == ["b"]
        assert cache.peek((1, 3)) == ["c"]


# ---------------------------------------------------------------------------
# adminCog.admin_duel + player_ship
# ---------------------------------------------------------------------------


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


class TestAdminDuelCache:
    def test_sentinel_first_even_on_cold_empty(self, admin_cog):
        admin_cog._admin_pending_duel_cache.invalidate(987654321)

        async def _fetch(_g):
            return []

        admin_cog._admin_pending_duel_cache._refresh_fn = _fetch
        res = asyncio.run(admin_cog.admin_duel_autocomplete(_mock_interaction(), ""))
        assert len(res) == 1
        assert res[0].value == "all"
        assert res[0].name.startswith("⚠️")

    def test_coldfill_populates_duels_with_sentinel(self, admin_cog):
        admin_cog._admin_pending_duel_cache.invalidate(987654321)
        duels = [{"id": 5, "challenger_name": "A", "target_name": "B", "stakes": 100}]

        async def _fetch(_g):
            return duels

        admin_cog._admin_pending_duel_cache._refresh_fn = _fetch
        res = asyncio.run(admin_cog.admin_duel_autocomplete(_mock_interaction(), ""))
        assert res[0].value == "all"  # sentinel first
        assert any(c.value == "5" for c in res)

    def test_invalidate_drops_guild_key(self, admin_cog):
        admin_cog._admin_pending_duel_cache.set(987654321, [{"id": 1}])
        assert admin_cog._admin_pending_duel_cache.peek(987654321) is not None
        admin_cog._admin_pending_duel_cache.invalidate(987654321)
        assert admin_cog._admin_pending_duel_cache.peek(987654321) is None

    def test_sentinel_preserved_on_exception_mid_loop(self, admin_cog):
        """An exception AFTER the sentinel is built (e.g. in normalize_for_search)
        must still return the Cancel-ALL sentinel, never an empty list."""
        # Warm cache so the handler proceeds past the cold-fill into the loop.
        admin_cog._admin_pending_duel_cache.set(
            987654321, [{"id": 5, "challenger_name": "A", "target_name": "B", "stakes": 100}]
        )
        with patch("cogs.adminCog.normalize_for_search", side_effect=RuntimeError("boom")):
            res = asyncio.run(admin_cog.admin_duel_autocomplete(_mock_interaction(), "x"))
        assert len(res) == 1
        assert res[0].value == "all"


class TestPlayerShipReuse:
    def test_warm_ships_cache_hit_no_http(self, admin_cog):
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache
        from utils.autocomplete_state import NormalizedChoice

        guild_id, target_uid, player_id = 987654321, 222, 7
        ac_state.player_cache = AutocompleteCache(name="p2-player")
        ac_state.player_cache.set((guild_id, target_uid), {"id": player_id})
        ac_state.ships_cache = AutocompleteCache(name="p2-ships")
        ac_state.ships_cache.set(
            (guild_id, player_id),
            [NormalizedChoice(label="Betty", value="1", norm="betty", raw={"ship_name": "Betty"})],
        )

        # HTTP GET must NOT be hit on a warm ships_cache (resolve_player_id reads
        # player_cache via peek; ships come from ships_cache).
        admin_cog.http_client.get = AsyncMock(
            side_effect=AssertionError("HTTP must not be called on warm ships_cache path")
        )

        target = MagicMock()
        target.id = target_uid
        res = asyncio.run(
            admin_cog.player_ship_autocomplete(_mock_interaction(guild_id=guild_id, namespace_user=target), "")
        )
        assert [c.value for c in res] == ["Betty"]

    def test_uncached_target_falls_back_to_catalog(self, admin_cog):
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        guild_id, target_uid = 987654321, 333
        # player_cache cold (no entry) and no refresh client → resolve_player_id None.
        ac_state.player_cache = AutocompleteCache(name="p2-player-cold")
        ac_state.ships_cache = AutocompleteCache(name="p2-ships-cold")

        # Catalog fallback returns ships from _ship_catalog.
        admin_cog._ship_catalog.set("all", ["Avenger", "Betty"])

        target = MagicMock()
        target.id = target_uid
        res = asyncio.run(
            admin_cog.player_ship_autocomplete(_mock_interaction(guild_id=guild_id, namespace_user=target), "")
        )
        names = [c.value for c in res]
        # Falls back to the catalog (not empty) — intended degrade path.
        assert "Avenger" in names and "Betty" in names
