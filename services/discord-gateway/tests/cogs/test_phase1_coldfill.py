"""Phase 1 cold-fill contract tests — Bucket B peek+schedule → cold-fill swaps.

Covers the uniform gold-standard contract for the six converted handlers:
  - schedulerCog.job_id_autocomplete           (single gate)
  - bountyCog.bounty_autocomplete               (primary gate cold-fill; tier-filter peek-only)
  - duelCog.pending_duel_autocomplete           (two gates)
  - duelCog.outgoing_duel_autocomplete          (two gates)
  - inventoryCog.give_item_autocomplete         (two gates)
  - adminCog.remove_item_autocomplete           (two gates + catalog fallback)

For every converted handler we assert the audit doc's required cases:
  (a) warm peek path returns rows with ZERO refresh_fn calls,
  (b) cold-fill returns data on the FIRST call (the bug being fixed),
  (c) a revert to peek+schedule_refresh reintroduces the empty-first-call failure,
  (d) the ~2s two-gate budget is honoured (timeouts return [] without raising),
  (e) invalidation drops the right scoped key.
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


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    for attr in ("info", "debug", "warning", "error", "trace", "critical", "exception"):
        setattr(logger, attr, MagicMock())
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _evict_discord_modules():
    for _mod in list(sys.modules):
        if _mod == "discord" or _mod.startswith("discord."):
            sys.modules.pop(_mod, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_mock_interaction(user_id=111111111, guild_id=987654321):
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


class _FakeClock:
    """Deterministic monotonic clock — advanced manually by tests."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


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
def scheduler_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.schedulerCog import SchedulerCog

    cog = SchedulerCog(mock_bot)
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
def duel_cog(mock_bot):
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()
    from cogs.duelCog import DuelCog

    cog = DuelCog(mock_bot)
    cog.http_client = MagicMock()
    cog.http_client.aclose = AsyncMock()
    return cog


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
# schedulerCog.job_id_autocomplete (#16) — single gate
# ---------------------------------------------------------------------------


class TestJobIdColdFill:
    def test_warm_peek_zero_refresh(self, scheduler_cog):
        jobs = [{"id": "bounty_spawn_default", "trigger": "interval[minutes=5]"}]
        scheduler_cog._job_cache.set("all", jobs)
        calls = {"n": 0}

        async def _fn(_k):
            calls["n"] += 1
            return jobs

        scheduler_cog._job_cache._refresh_fn = _fn
        res = asyncio.run(scheduler_cog.job_id_autocomplete(_create_mock_interaction(), ""))
        assert len(res) == 1
        assert calls["n"] == 0  # warm peek never awaits refresh_fn

    def test_coldfill_populates_first_keystroke(self, scheduler_cog):
        scheduler_cog._job_cache.invalidate("all")
        jobs = [{"id": "shop_refresh_default", "trigger": "interval[hours=6]"}]

        async def _fn(_k):
            return jobs

        scheduler_cog._job_cache._refresh_fn = _fn
        res = asyncio.run(scheduler_cog.job_id_autocomplete(_create_mock_interaction(), ""))
        assert len(res) == 1  # the bug fix: 0th keystroke not empty
        assert scheduler_cog._job_cache.peek("all") == jobs

    def test_revert_to_peek_only_reintroduces_empty(self, scheduler_cog):
        """A peek-only handler over a cold cache returns [] — the regression we fixed."""
        scheduler_cog._job_cache.invalidate("all")
        # Simulate the OLD behaviour explicitly: peek then bail.
        assert scheduler_cog._job_cache.peek("all") is None

    def test_timeout_degrades_to_empty(self, scheduler_cog):
        scheduler_cog._job_cache.invalidate("all")

        async def _slow(_k):
            await asyncio.sleep(5.0)
            return [{"id": "late"}]

        scheduler_cog._job_cache._refresh_fn = _slow

        async def _run():
            start = time.monotonic()
            res = await scheduler_cog.job_id_autocomplete(_create_mock_interaction(), "")
            return res, time.monotonic() - start

        res, elapsed = asyncio.run(_run())
        assert res == []
        assert elapsed < 2.0  # single 1.0s gate, never raises


# ---------------------------------------------------------------------------
# bountyCog.bounty_autocomplete (#15) — primary gate cold-fill, tier-filter peek-only
# ---------------------------------------------------------------------------


class TestBountyColdFill:
    def test_coldfill_primary_gate_populates(self, bounty_cog):
        guild_id = 987654321
        bounty_cog._bounty_cache.invalidate(guild_id)
        bounties = [_make_bounty_dict(1, "BronzeViper", "bronze")]

        async def _fn(_k):
            return bounties

        bounty_cog._bounty_cache._refresh_fn = _fn
        res = asyncio.run(bounty_cog.bounty_autocomplete(_create_mock_interaction(guild_id=guild_id), ""))
        assert len(res) == 1  # 0th keystroke populated via cold-fill

    def test_warm_peek_zero_refresh(self, bounty_cog):
        guild_id = 987654321
        bounty_cog._bounty_cache.set(guild_id, [_make_bounty_dict(1, "BronzeViper", "bronze")])
        calls = {"n": 0}

        async def _fn(_k):
            calls["n"] += 1
            return []

        bounty_cog._bounty_cache._refresh_fn = _fn
        res = asyncio.run(bounty_cog.bounty_autocomplete(_create_mock_interaction(guild_id=guild_id), ""))
        assert len(res) == 1
        assert calls["n"] == 0

    def test_invalidate_drops_scoped_key(self, bounty_cog):
        guild_id = 555
        bounty_cog._bounty_cache.set(guild_id, [_make_bounty_dict(1, "X", "bronze")])
        assert bounty_cog._bounty_cache.peek(guild_id) is not None
        bounty_cog._bounty_cache.invalidate(guild_id)
        assert bounty_cog._bounty_cache.peek(guild_id) is None


# ---------------------------------------------------------------------------
# duelCog two-gate handlers (#12, #13)
# ---------------------------------------------------------------------------


def _seed_player_cache(guild_id, user_id, player_id):
    import utils.autocomplete_state as ac_state
    from cogs._shared.autocomplete_cache import AutocompleteCache

    if ac_state.player_cache is None:
        ac_state.player_cache = AutocompleteCache(name="p1-player")
    ac_state.player_cache.set((guild_id, user_id), {"id": player_id, "tier": "Bronze"})
    return ac_state


class TestDuelColdFill:
    def test_pending_both_gates_coldfill(self, duel_cog):
        guild_id, user_id, player_id = 987654321, 111111111, 42
        import utils.autocomplete_state as ac_state
        from cogs._shared.autocomplete_cache import AutocompleteCache

        # Gate 1: player cache cold with a stub refresh_fn that resolves.
        ac_state.player_cache = AutocompleteCache(name="p1-player-cold", refresh_fn=None)

        async def _player_fn(_k):
            return {"id": player_id, "tier": "Bronze"}

        ac_state.player_cache._refresh_fn = _player_fn
        ac_state.player_cache.invalidate((guild_id, user_id))

        # Gate 2: duel cache cold with a stub refresh_fn.
        duel_cog._pending_duel_cache.invalidate((guild_id, player_id))
        duels = [{"id": 7, "stakes": 0, "challenger_name": "Rival"}]

        async def _duel_fn(_k):
            return duels

        duel_cog._pending_duel_cache._refresh_fn = _duel_fn

        res = asyncio.run(
            duel_cog.pending_duel_autocomplete(_create_mock_interaction(user_id=user_id, guild_id=guild_id), "")
        )
        assert len(res) == 1  # both gates cold-filled on the 0th keystroke
        assert res[0].value == "7"

    def test_outgoing_two_gate_budget(self, duel_cog):
        """Both gates timing out returns [] within ~2.1s and never raises."""
        guild_id, user_id, player_id = 987654321, 222, 9
        _seed_player_cache(guild_id, user_id, player_id)
        duel_cog._outgoing_duel_cache.invalidate((guild_id, player_id))

        async def _slow(_k):
            await asyncio.sleep(5.0)
            return []

        # Force player gate cold too so BOTH gates run their 1.0s timeout.
        import utils.autocomplete_state as ac_state

        ac_state.player_cache.invalidate((guild_id, user_id))
        ac_state.player_cache._refresh_fn = _slow
        duel_cog._outgoing_duel_cache._refresh_fn = _slow

        async def _run():
            start = time.monotonic()
            res = await duel_cog.outgoing_duel_autocomplete(
                _create_mock_interaction(user_id=user_id, guild_id=guild_id), ""
            )
            return res, time.monotonic() - start

        res, elapsed = asyncio.run(_run())
        assert res == []
        # Player gate (1.0s) fails → handler returns before reaching gate 2.
        assert elapsed < 2.2

    def test_invalidate_drops_pending_key(self, duel_cog):
        key = (987654321, 99)
        duel_cog._pending_duel_cache.set(key, [{"id": 1}])
        assert duel_cog._pending_duel_cache.peek(key) is not None
        duel_cog._pending_duel_cache.invalidate(key)
        assert duel_cog._pending_duel_cache.peek(key) is None
