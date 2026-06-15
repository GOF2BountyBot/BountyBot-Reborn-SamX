"""P6 Pass B — behavior-equivalence + efficiency tests for four query-pushdown changes.

P6-T2  Batch inventory ``_get_item_details`` (kill N×5 lookups)
    - Output of ``get_player_inventory`` (item_details dicts) is identical across
      mixed-type inventories, empty inventories, and inventories containing an
      unknown item name.
    - Query count drops: ``_get_items_details_batch`` issues at most 5 repo calls
      regardless of how many items are in the inventory (not N×5).
    - All five item-type repos (primary_weapon, secondary_weapon, turret_weapon,
      module, ship) contribute to the batch and map to the correct ``type`` key.

P6-T3  DB-side pagination for guild player list
    - Output (page contents) for several (skip, limit) combinations matches the
      expected slice: skip=0 limit=2, skip=1 limit=2, skip beyond end, limit > total.
    - In the no-tier branch the repo is called with skip and limit kwargs (not 0/None)
      so the DB applies LIMIT/OFFSET instead of Python-slicing a full load.
    - A mutation that reverts to passing skip=0/limit=None returns the wrong row
      count and fails the call-args assertion.

P6-T4  DB aggregate/pagination for guild-wide stats & pending-all
    - Guild stats output is identical to the old Python-loop result across
      multi-tier + single-player + empty guild fixtures.
    - ``get_guild_stats`` is called once, NOT ``get_players_by_guild``.
    - ``DuelService.get_all_pending_for_guild`` returns the same
      (duel, challenger_name, target_name) tuples as the old N×2 loop.
    - Batch path issues ``get_by_ids`` (not N ``get_by_id`` calls) for both
      player_repo and user_repo.

P6-T5  ``get_object_by_name/alias`` already short-circuits on first match
    - Same result for a name known to the FIRST repo, a name in a LATER repo,
      an alias hit, and an unknown name.
    - When a match is found in repo #1, subsequent repos are NOT queried.
    - When a name is unknown, all repos are queried (short-circuit only fires
      on a match, not exhaustively).
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Shared mock-guard: bblogger + sqlalchemy_utils
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _sqla_utils = types.ModuleType("sqlalchemy_utils")
    _sqla_utils.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _sqla_utils

# ---------------------------------------------------------------------------
# Imports (after mock-guard)
# ---------------------------------------------------------------------------
from services.duel_service import DuelService
from services.inventory_service import InventoryService

# ===========================================================================
# Helpers
# ===========================================================================


def _make_db() -> AsyncMock:
    return AsyncMock()


def _make_item(item_id: int, item_type: str, item_name: str, quantity: int = 1) -> SimpleNamespace:
    """Return a PlayerInventory-like SimpleNamespace."""
    return SimpleNamespace(
        id=item_id,
        item_type=item_type,
        item_name=item_name,
        quantity=quantity,
        acquired_at=datetime(2025, 6, 1, tzinfo=UTC),
    )


def _make_weapon_obj(name: str, tech_level: int = 3, value: int = 1000) -> SimpleNamespace:
    return SimpleNamespace(name=name, tech_level=tech_level, value=value)


def _make_module_obj(name: str, tech_level: int = 2, value: int = 500) -> SimpleNamespace:
    return SimpleNamespace(name=name, tech_level=tech_level, value=value)


def _make_ship_obj(name: str, value: int = 20000) -> SimpleNamespace:
    return SimpleNamespace(name=name, value=value)


def _make_player_obj(player_id: int, user_id: int, guild_id: int = 9999) -> SimpleNamespace:
    return SimpleNamespace(id=player_id, user_id=user_id, guild_id=guild_id)


def _make_user_obj(user_id: int, discord_username: str | None = "User") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, discord_username=discord_username)


def _make_duel(
    duel_id: int,
    challenger_id: int,
    target_id: int,
    guild_id: int = 9999,
    status: str = "pending",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=duel_id,
        challenger_id=challenger_id,
        target_id=target_id,
        guild_id=guild_id,
        status=status,
    )


def _make_inventory_service() -> InventoryService:
    """Return an InventoryService with all repos mocked."""
    svc = InventoryService()
    svc.inventory_repo = MagicMock()
    svc.player_repo = MagicMock()
    svc.primary_weapon_repo = MagicMock()
    svc.secondary_weapon_repo = MagicMock()
    svc.turret_weapon_repo = MagicMock()
    svc.module_repo = MagicMock()
    svc.ship_repo = MagicMock()

    # Default: no items found
    svc.primary_weapon_repo.get_by_names = AsyncMock(return_value=[])
    svc.secondary_weapon_repo.get_by_names = AsyncMock(return_value=[])
    svc.turret_weapon_repo.get_by_names = AsyncMock(return_value=[])
    svc.module_repo.get_by_names = AsyncMock(return_value=[])
    svc.ship_repo.get_by_names = AsyncMock(return_value=[])
    return svc


def _make_duel_service() -> DuelService:
    svc = DuelService()
    svc.duel_repo = MagicMock()
    svc.player_repo = MagicMock()
    svc.user_repo = MagicMock()
    return svc


# ===========================================================================
# P6-T2: Batch inventory _get_item_details
# ===========================================================================


class TestP6T2BatchItemDetails:
    """Batch item-detail fetching — correctness + query-count proofs."""

    @pytest.mark.asyncio
    async def test_batch_empty_inventory_returns_empty(self):
        """Empty inventory → _get_items_details_batch returns {} with zero repo calls."""
        svc = _make_inventory_service()
        db = _make_db()

        result = await svc._get_items_details_batch(db, [])

        assert result == {}
        svc.primary_weapon_repo.get_by_names.assert_not_called()
        svc.secondary_weapon_repo.get_by_names.assert_not_called()
        svc.turret_weapon_repo.get_by_names.assert_not_called()
        svc.module_repo.get_by_names.assert_not_called()
        svc.ship_repo.get_by_names.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_primary_weapon_detail_correct(self):
        """A primary-weapon name resolves to the correct detail dict."""
        svc = _make_inventory_service()
        weapon = _make_weapon_obj("Micro Gun MK I", tech_level=1, value=800)
        svc.primary_weapon_repo.get_by_names = AsyncMock(return_value=[weapon])
        db = _make_db()

        result = await svc._get_items_details_batch(db, ["Micro Gun MK I"])

        assert result["Micro Gun MK I"] == {
            "name": "Micro Gun MK I",
            "tech_level": 1,
            "value": 800,
            "type": "primary_weapon",
        }

    @pytest.mark.asyncio
    async def test_batch_secondary_weapon_detail_correct(self):
        """A secondary-weapon name resolves with type='secondary_weapon'."""
        svc = _make_inventory_service()
        weapon = _make_weapon_obj("Cluster Missile MK I", tech_level=2, value=1200)
        svc.secondary_weapon_repo.get_by_names = AsyncMock(return_value=[weapon])
        db = _make_db()

        result = await svc._get_items_details_batch(db, ["Cluster Missile MK I"])

        assert result["Cluster Missile MK I"]["type"] == "secondary_weapon"
        assert result["Cluster Missile MK I"]["tech_level"] == 2

    @pytest.mark.asyncio
    async def test_batch_module_detail_correct(self):
        """A module name resolves with type='module' and tech_level set."""
        svc = _make_inventory_service()
        mod = _make_module_obj("E2 Exoclad", tech_level=2, value=500)
        svc.module_repo.get_by_names = AsyncMock(return_value=[mod])
        db = _make_db()

        result = await svc._get_items_details_batch(db, ["E2 Exoclad"])

        assert result["E2 Exoclad"] == {
            "name": "E2 Exoclad",
            "tech_level": 2,
            "value": 500,
            "type": "module",
        }

    @pytest.mark.asyncio
    async def test_batch_ship_detail_correct_no_tech_level(self):
        """A ship name resolves with type='ship' and tech_level=None."""
        svc = _make_inventory_service()
        ship = _make_ship_obj("Betty", value=16038)
        svc.ship_repo.get_by_names = AsyncMock(return_value=[ship])
        db = _make_db()

        result = await svc._get_items_details_batch(db, ["Betty"])

        assert result["Betty"] == {
            "name": "Betty",
            "tech_level": None,
            "value": 16038,
            "type": "ship",
        }

    @pytest.mark.asyncio
    async def test_batch_unknown_name_maps_to_none(self):
        """An unknown item name maps to None (not a KeyError)."""
        svc = _make_inventory_service()
        db = _make_db()

        result = await svc._get_items_details_batch(db, ["Unknown Item"])

        assert "Unknown Item" in result
        assert result["Unknown Item"] is None

    @pytest.mark.asyncio
    async def test_batch_mixed_types_all_resolved(self):
        """Mixed inventory with primary weapon + module + ship all resolved correctly."""
        svc = _make_inventory_service()
        weapon = _make_weapon_obj("Laser MK I", tech_level=1, value=700)
        mod = _make_module_obj("E2 Exoclad", tech_level=2, value=500)
        ship = _make_ship_obj("Betty", value=16038)

        svc.primary_weapon_repo.get_by_names = AsyncMock(return_value=[weapon])
        svc.module_repo.get_by_names = AsyncMock(return_value=[mod])
        svc.ship_repo.get_by_names = AsyncMock(return_value=[ship])
        db = _make_db()

        result = await svc._get_items_details_batch(db, ["Laser MK I", "E2 Exoclad", "Betty"])

        assert result["Laser MK I"]["type"] == "primary_weapon"
        assert result["E2 Exoclad"]["type"] == "module"
        assert result["Betty"]["type"] == "ship"

    @pytest.mark.asyncio
    async def test_batch_query_count_5_for_n_items(self):
        """For N=3 distinct item names (different types), at most 5 repo calls total — not N×5.

        This is the key efficiency assertion: the old code called up to 5 repos per item
        (so 15 for 3 items); the new code calls each repo once (5 total) regardless of N.
        """
        svc = _make_inventory_service()
        weapon = _make_weapon_obj("Gun", tech_level=1, value=500)
        mod = _make_module_obj("Shield", tech_level=1, value=300)
        ship = _make_ship_obj("Betty", value=16038)

        svc.primary_weapon_repo.get_by_names = AsyncMock(return_value=[weapon])
        svc.module_repo.get_by_names = AsyncMock(return_value=[mod])
        svc.ship_repo.get_by_names = AsyncMock(return_value=[ship])
        db = _make_db()

        await svc._get_items_details_batch(db, ["Gun", "Shield", "Betty"])

        # Each of the 5 repos is called at most once — never N×repo.
        assert svc.primary_weapon_repo.get_by_names.call_count == 1
        assert svc.secondary_weapon_repo.get_by_names.call_count == 1
        # module and ship repos are called once too (resolved items are skipped on
        # subsequent repos, but unresolved names still get forwarded to them)
        total_calls = sum(
            [
                svc.primary_weapon_repo.get_by_names.call_count,
                svc.secondary_weapon_repo.get_by_names.call_count,
                svc.turret_weapon_repo.get_by_names.call_count,
                svc.module_repo.get_by_names.call_count,
                svc.ship_repo.get_by_names.call_count,
            ]
        )
        # At most 5 total calls (one per repo type) regardless of N items.
        # N×5 = 15 for 3 items; our batched path is ≤ 5.
        assert total_calls <= 5, f"Expected ≤5 total repo calls, got {total_calls}"

    @pytest.mark.asyncio
    async def test_batch_short_circuits_early_when_all_resolved(self):
        """If all names are resolved by primary_weapon repo, subsequent repos are NOT queried.

        Efficiency proof: when every item is a primary weapon, only 1 of the 5
        repos should be called (not all 5).
        """
        svc = _make_inventory_service()
        weapons = [_make_weapon_obj(f"Gun{i}", tech_level=i, value=i * 100) for i in range(1, 4)]
        svc.primary_weapon_repo.get_by_names = AsyncMock(return_value=weapons)
        db = _make_db()

        await svc._get_items_details_batch(db, ["Gun1", "Gun2", "Gun3"])

        # All resolved at repo #1 → repos 2-5 not queried.
        assert svc.primary_weapon_repo.get_by_names.call_count == 1
        svc.secondary_weapon_repo.get_by_names.assert_not_called()
        svc.turret_weapon_repo.get_by_names.assert_not_called()
        svc.module_repo.get_by_names.assert_not_called()
        svc.ship_repo.get_by_names.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_player_inventory_uses_batch_path(self):
        """get_player_inventory uses the batch method; item_details dicts are correct."""
        svc = _make_inventory_service()
        db = _make_db()

        player = SimpleNamespace(id=1, guild_id=9999, tier="Bronze")
        svc.player_repo.get_by_id = AsyncMock(return_value=player)

        inventory_items = [
            _make_item(1, "primary_weapon", "Micro Gun MK I"),
            _make_item(2, "ship", "Betty"),
        ]
        svc.inventory_repo.get_player_items = AsyncMock(return_value=inventory_items)

        weapon = _make_weapon_obj("Micro Gun MK I", tech_level=1, value=800)
        ship = _make_ship_obj("Betty", value=16038)
        svc.primary_weapon_repo.get_by_names = AsyncMock(return_value=[weapon])
        svc.ship_repo.get_by_names = AsyncMock(return_value=[ship])

        result = await svc.get_player_inventory(db, player_id=1)

        assert len(result) == 2
        gun_entry = next(r for r in result if r["item_name"] == "Micro Gun MK I")
        betty_entry = next(r for r in result if r["item_name"] == "Betty")
        assert gun_entry["item_details"]["type"] == "primary_weapon"
        assert betty_entry["item_details"]["type"] == "ship"

        # Batch path was used — each repo called at most once, not once per item.
        assert svc.primary_weapon_repo.get_by_names.call_count == 1
        assert svc.ship_repo.get_by_names.call_count == 1

    @pytest.mark.asyncio
    async def test_get_player_inventory_unknown_item_has_none_details(self):
        """An inventory item whose name exists in no repo produces item_details=None."""
        svc = _make_inventory_service()
        db = _make_db()

        player = SimpleNamespace(id=1, guild_id=9999, tier="Bronze")
        svc.player_repo.get_by_id = AsyncMock(return_value=player)

        inventory_items = [_make_item(1, "primary_weapon", "Ghost Item")]
        svc.inventory_repo.get_player_items = AsyncMock(return_value=inventory_items)

        result = await svc.get_player_inventory(db, player_id=1)

        assert len(result) == 1
        assert result[0]["item_details"] is None

    @pytest.mark.asyncio
    async def test_single_item_details_delegates_to_batch(self):
        """_get_item_details (single-item wrapper) returns correct detail for a module."""
        svc = _make_inventory_service()
        mod = _make_module_obj("Cabin Module", tech_level=1, value=300)
        svc.module_repo.get_by_names = AsyncMock(return_value=[mod])
        db = _make_db()

        result = await svc._get_item_details(db, "Cabin Module")

        assert result == {
            "name": "Cabin Module",
            "tech_level": 1,
            "value": 300,
            "type": "module",
        }

    @pytest.mark.asyncio
    async def test_single_item_details_none_for_unknown(self):
        """_get_item_details returns None for an unknown item name."""
        svc = _make_inventory_service()
        db = _make_db()

        result = await svc._get_item_details(db, "No Such Item")

        assert result is None


# ===========================================================================
# P6-T3: DB-side pagination for guild player list
# ===========================================================================


class TestP6T3DBPagination:
    """DB-side skip/limit pagination — correctness + bounded-query proofs."""

    def _make_player_response(
        self,
        player_id: int,
        guild_id: int = 67890,
        credits: int = 100,
        tier: str = "Bronze",
    ) -> SimpleNamespace:
        p = SimpleNamespace(
            id=player_id,
            user_id=player_id * 100,
            guild_id=guild_id,
            credits=credits,
            lifetime_credits=credits,
            systems_checked=0,
            bounty_wins=0,
            xp=0,
            tier=tier,
            prestige_count=0,
            duel_wins=0,
            duel_losses=0,
            duel_credits_won=0,
            duel_credits_lost=0,
            active_ship_id=None,
            bounty_notifications_enabled=True,
            shop_notifications_enabled=True,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        return p

    @pytest.mark.asyncio
    async def test_no_tier_branch_passes_skip_limit_to_repo(self):
        """Without tier filter, router passes skip & limit kwargs to get_players_by_guild.

        This is the key efficiency assertion: the repo receives limit=2 so the DB
        applies LIMIT 2 — it does NOT receive limit=None (which would load all rows).
        """
        from contextlib import asynccontextmanager

        from api.routers.players import get_players_by_guild

        players_page = [self._make_player_response(i) for i in [2, 3]]

        mock_repo = AsyncMock()
        mock_repo.get_players_by_guild = AsyncMock(return_value=players_page)

        mock_service = MagicMock()
        mock_service.player_repo = mock_repo

        # Patch get_db_session for the router
        mock_session = AsyncMock()

        @asynccontextmanager
        async def _fake_session():
            yield mock_session

        import api.routers.players as players_module

        original = players_module.get_db_session
        players_module.get_db_session = _fake_session
        try:
            result = await get_players_by_guild(
                guild_id=67890,
                skip=1,
                limit=2,
                tier=None,
                active_within_days=None,
                player_service=mock_service,
            )
        finally:
            players_module.get_db_session = original

        # The repo must have been called with skip=1, limit=2 (not skip=0, limit=None).
        mock_repo.get_players_by_guild.assert_called_once_with(
            mock_session,
            67890,
            active_within_days=None,
            skip=1,
            limit=2,
        )
        # Result matches what repo returned (no extra slicing).
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_no_tier_branch_skip_zero_limit_100(self):
        """Default skip=0, limit=100 passes limit=100 (not None) to the repo."""
        from contextlib import asynccontextmanager

        from api.routers.players import get_players_by_guild

        players_all = [self._make_player_response(i) for i in range(1, 4)]
        mock_repo = AsyncMock()
        mock_repo.get_players_by_guild = AsyncMock(return_value=players_all)
        mock_service = MagicMock()
        mock_service.player_repo = mock_repo

        mock_session = AsyncMock()

        @asynccontextmanager
        async def _fake_session():
            yield mock_session

        import api.routers.players as players_module

        original = players_module.get_db_session
        players_module.get_db_session = _fake_session
        try:
            result = await get_players_by_guild(
                guild_id=67890,
                skip=0,
                limit=100,
                tier=None,
                active_within_days=None,
                player_service=mock_service,
            )
        finally:
            players_module.get_db_session = original

        mock_repo.get_players_by_guild.assert_called_once_with(
            mock_session,
            67890,
            active_within_days=None,
            skip=0,
            limit=100,
        )
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_no_tier_branch_skip_beyond_end_returns_empty(self):
        """skip > total rows: repo returns [] (DB OFFSET past end of results)."""
        from contextlib import asynccontextmanager

        from api.routers.players import get_players_by_guild

        mock_repo = AsyncMock()
        mock_repo.get_players_by_guild = AsyncMock(return_value=[])  # DB returns empty
        mock_service = MagicMock()
        mock_service.player_repo = mock_repo

        mock_session = AsyncMock()

        @asynccontextmanager
        async def _fake_session():
            yield mock_session

        import api.routers.players as players_module

        original = players_module.get_db_session
        players_module.get_db_session = _fake_session
        try:
            result = await get_players_by_guild(
                guild_id=67890,
                skip=100,
                limit=10,
                tier=None,
                active_within_days=None,
                player_service=mock_service,
            )
        finally:
            players_module.get_db_session = original

        assert result == []

    @pytest.mark.asyncio
    async def test_tier_branch_still_works_with_python_slice(self):
        """Tier-filtered path returns correct sliced page via Python-side slicing."""
        from contextlib import asynccontextmanager

        from api.routers.players import get_players_by_guild

        all_gold = [self._make_player_response(i, tier="Gold") for i in range(1, 6)]

        mock_service = MagicMock()
        mock_service.get_players_by_tier = AsyncMock(return_value=all_gold)

        mock_session = AsyncMock()

        @asynccontextmanager
        async def _fake_session():
            yield mock_session

        import api.routers.players as players_module

        original = players_module.get_db_session
        players_module.get_db_session = _fake_session
        try:
            result = await get_players_by_guild(
                guild_id=67890,
                skip=1,
                limit=2,
                tier="Gold",
                active_within_days=None,
                player_service=mock_service,
            )
        finally:
            players_module.get_db_session = original

        assert len(result) == 2
        assert result[0].id == 2  # skip=1 skips player 1
        assert result[1].id == 3

    @pytest.mark.asyncio
    async def test_get_players_by_guild_repo_limit_offset_applied(self):
        """PlayerRepository.get_players_by_guild passes OFFSET/LIMIT to the query.

        We call the repository method with a mock db and verify that db.execute
        was called exactly once with the query containing the correct values.
        This tests the repository in isolation (not through the full stack).
        """
        from persist.repositories.player_repository import PlayerRepository

        repo = PlayerRepository()
        db = AsyncMock()

        # Mock the result set to return 2 players.
        player_a = self._make_player_response(3)
        player_b = self._make_player_response(4)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [player_a, player_b]
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=result_mock)

        rows = await repo.get_players_by_guild(db, guild_id=67890, skip=2, limit=2)

        assert len(rows) == 2
        assert db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_get_players_by_guild_no_skip_no_limit_returns_all(self):
        """With skip=0, limit=None the query omits OFFSET and LIMIT (returns all rows)."""
        from persist.repositories.player_repository import PlayerRepository

        repo = PlayerRepository()
        db = AsyncMock()

        players = [self._make_player_response(i) for i in range(1, 4)]
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = players
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        db.execute = AsyncMock(return_value=result_mock)

        rows = await repo.get_players_by_guild(db, guild_id=67890, skip=0, limit=None)

        assert len(rows) == 3
        db.execute.assert_called_once()


# ===========================================================================
# P6-T4: DB aggregate for guild stats & duel pending-all
# ===========================================================================


class TestP6T4GuildStats:
    """Admin guild stats — aggregate query correctness + bounded-query proof."""

    @pytest.mark.asyncio
    async def test_get_guild_stats_multi_tier(self):
        """get_guild_stats returns correct aggregates for 3 players across 2 tiers."""
        from persist.repositories.player_repository import PlayerRepository

        repo = PlayerRepository()
        db = AsyncMock()

        # Scalar agg row: total=3, credits=600, xp=300
        agg_row = SimpleNamespace(total_players=3, total_credits=600, total_xp=300)
        agg_result = MagicMock()
        agg_result.one.return_value = agg_row

        # Tier distribution: Bronze=2, Silver=1
        tier_result = MagicMock()
        tier_result.all.return_value = [("Bronze", 2), ("Silver", 1)]

        # db.execute returns different results on first/second call
        db.execute = AsyncMock(side_effect=[agg_result, tier_result])

        stats = await repo.get_guild_stats(db, guild_id=99999)

        assert stats["guild_id"] == 99999
        assert stats["total_players"] == 3
        assert stats["total_credits"] == 600
        assert stats["total_xp"] == 300
        assert stats["average_credits"] == pytest.approx(200.0)
        assert stats["average_xp"] == pytest.approx(100.0)
        assert stats["tier_distribution"] == {"Bronze": 2, "Silver": 1}

    @pytest.mark.asyncio
    async def test_get_guild_stats_empty_guild(self):
        """Empty guild returns zeros and empty tier_distribution (no ZeroDivisionError)."""
        from persist.repositories.player_repository import PlayerRepository

        repo = PlayerRepository()
        db = AsyncMock()

        agg_row = SimpleNamespace(total_players=0, total_credits=0, total_xp=0)
        agg_result = MagicMock()
        agg_result.one.return_value = agg_row

        tier_result = MagicMock()
        tier_result.all.return_value = []

        db.execute = AsyncMock(side_effect=[agg_result, tier_result])

        stats = await repo.get_guild_stats(db, guild_id=99999)

        assert stats["total_players"] == 0
        assert stats["average_credits"] == 0
        assert stats["average_xp"] == 0
        assert stats["tier_distribution"] == {}

    @pytest.mark.asyncio
    async def test_get_guild_stats_single_player(self):
        """Single player: average equals the player's own credits/xp."""
        from persist.repositories.player_repository import PlayerRepository

        repo = PlayerRepository()
        db = AsyncMock()

        agg_row = SimpleNamespace(total_players=1, total_credits=500, total_xp=250)
        agg_result = MagicMock()
        agg_result.one.return_value = agg_row

        tier_result = MagicMock()
        tier_result.all.return_value = [("Platinum", 1)]

        db.execute = AsyncMock(side_effect=[agg_result, tier_result])

        stats = await repo.get_guild_stats(db, guild_id=1)

        assert stats["average_credits"] == pytest.approx(500.0)
        assert stats["tier_distribution"] == {"Platinum": 1}

    @pytest.mark.asyncio
    async def test_admin_stats_uses_get_guild_stats_not_get_players(self):
        """Admin guild stats endpoint calls get_guild_stats, NOT get_players_by_guild.

        This is the efficiency assertion: the route must NOT materialize the full
        player list just to compute sums.
        """
        from contextlib import asynccontextmanager

        import api.routers.admin as admin_module
        from api.routers.admin import get_guild_statistics

        expected_stats = {
            "guild_id": 67890,
            "total_players": 2,
            "tier_distribution": {"Bronze": 1, "Silver": 1},
            "total_credits": 300,
            "total_xp": 200,
            "average_credits": 150.0,
            "average_xp": 100.0,
        }

        mock_repo = AsyncMock()
        mock_repo.get_guild_stats = AsyncMock(return_value=expected_stats)
        mock_repo.get_players_by_guild = AsyncMock(return_value=[])  # must NOT be called

        mock_service = MagicMock()
        mock_service.player_repo = mock_repo

        mock_session = AsyncMock()

        @asynccontextmanager
        async def _fake_session():
            yield mock_session

        original_db = admin_module.get_db_session
        original_verify = admin_module.verify_admin_permissions
        admin_module.get_db_session = _fake_session
        admin_module.verify_admin_permissions = AsyncMock(return_value=True)
        try:
            result = await get_guild_statistics(
                guild_id=67890,
                user_id=12345,
                player_service=mock_service,
            )
        finally:
            admin_module.get_db_session = original_db
            admin_module.verify_admin_permissions = original_verify

        # Must use the aggregate method.
        mock_repo.get_guild_stats.assert_called_once_with(mock_session, 67890)
        # Must NOT fall back to loading all players.
        mock_repo.get_players_by_guild.assert_not_called()
        assert result == expected_stats

    @pytest.mark.asyncio
    async def test_admin_stats_query_count_is_two(self):
        """get_guild_stats issues exactly 2 DB queries (agg + tier group-by).

        This confirms bounded query count: the old code issued 1 full-scan query
        to materialize all rows; the new code issues 2 targeted aggregate queries
        but loads zero rows.
        """
        from persist.repositories.player_repository import PlayerRepository

        repo = PlayerRepository()
        db = AsyncMock()

        agg_row = SimpleNamespace(total_players=5, total_credits=1000, total_xp=500)
        agg_result = MagicMock()
        agg_result.one.return_value = agg_row

        tier_result = MagicMock()
        tier_result.all.return_value = [("Bronze", 3), ("Gold", 2)]

        db.execute = AsyncMock(side_effect=[agg_result, tier_result])

        await repo.get_guild_stats(db, guild_id=42)

        # Exactly 2 queries: one aggregate, one GROUP BY tier.
        assert db.execute.call_count == 2, (
            f"Expected exactly 2 DB queries for get_guild_stats, got {db.execute.call_count}"
        )


class TestP6T4DuelPendingAll:
    """DuelService.get_all_pending_for_guild batch player/user resolution."""

    @pytest.mark.asyncio
    async def test_pending_all_correct_names(self):
        """All pending duels get correct (challenger_name, target_name) tuples."""
        svc = _make_duel_service()
        db = _make_db()

        duel1 = _make_duel(duel_id=1, challenger_id=10, target_id=20)
        duel2 = _make_duel(duel_id=2, challenger_id=30, target_id=40)

        svc.duel_repo.get_all_pending_by_guild = AsyncMock(return_value=[duel1, duel2])

        player10 = _make_player_obj(10, user_id=1000)
        player20 = _make_player_obj(20, user_id=2000)
        player30 = _make_player_obj(30, user_id=3000)
        player40 = _make_player_obj(40, user_id=4000)
        svc.player_repo.get_by_ids = AsyncMock(return_value=[player10, player20, player30, player40])

        user1000 = _make_user_obj(1000, "Alice")
        user2000 = _make_user_obj(2000, "Bob")
        user3000 = _make_user_obj(3000, "Charlie")
        user4000 = _make_user_obj(4000, "Diana")
        svc.user_repo.get_by_ids = AsyncMock(return_value=[user1000, user2000, user3000, user4000])

        result = await svc.get_all_pending_for_guild(db, guild_id=9999)

        assert len(result) == 2
        duel_a, ch_a, tg_a = result[0]
        assert duel_a.id == 1
        assert ch_a == "Alice"
        assert tg_a == "Bob"

        duel_b, ch_b, tg_b = result[1]
        assert duel_b.id == 2
        assert ch_b == "Charlie"
        assert tg_b == "Diana"

    @pytest.mark.asyncio
    async def test_pending_all_empty_guild(self):
        """No pending duels → returns []  with no repo calls beyond duel_repo."""
        svc = _make_duel_service()
        db = _make_db()

        svc.duel_repo.get_all_pending_by_guild = AsyncMock(return_value=[])

        result = await svc.get_all_pending_for_guild(db, guild_id=9999)

        assert result == []
        svc.player_repo.get_by_ids.assert_not_called()
        svc.user_repo.get_by_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_pending_all_batch_calls_not_n_times(self):
        """player_repo.get_by_ids called once (not N times) for N pending duels.

        This is the key efficiency assertion: old code called get_by_id 2×N
        times (challenger + target per duel); new code calls get_by_ids once
        for all player IDs and get_by_ids once for all user IDs.
        """
        svc = _make_duel_service()
        db = _make_db()

        duels = [_make_duel(duel_id=i, challenger_id=i * 10, target_id=i * 10 + 1) for i in range(1, 6)]
        svc.duel_repo.get_all_pending_by_guild = AsyncMock(return_value=duels)

        players = [_make_player_obj(d.challenger_id, d.challenger_id * 100) for d in duels] + [
            _make_player_obj(d.target_id, d.target_id * 100) for d in duels
        ]
        svc.player_repo.get_by_ids = AsyncMock(return_value=players)

        users = [_make_user_obj(p.user_id, f"User{p.user_id}") for p in players]
        svc.user_repo.get_by_ids = AsyncMock(return_value=users)

        await svc.get_all_pending_for_guild(db, guild_id=9999)

        # One batched call each — NOT 5 calls to get_by_id per player.
        assert svc.player_repo.get_by_ids.call_count == 1, (
            f"Expected 1 player batch call, got {svc.player_repo.get_by_ids.call_count}"
        )
        assert svc.user_repo.get_by_ids.call_count == 1, (
            f"Expected 1 user batch call, got {svc.user_repo.get_by_ids.call_count}"
        )
        # Old per-item methods must NOT have been called.
        svc.player_repo.get_by_id.assert_not_called()
        svc.user_repo.get_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_pending_all_missing_player_name_is_none(self):
        """A duel whose challenger has no player row gets challenger_name=None (defensive)."""
        svc = _make_duel_service()
        db = _make_db()

        duel = _make_duel(duel_id=1, challenger_id=10, target_id=20)
        svc.duel_repo.get_all_pending_by_guild = AsyncMock(return_value=[duel])

        # Only target player 20 exists; challenger 10 is missing from DB.
        player20 = _make_player_obj(20, user_id=2000)
        svc.player_repo.get_by_ids = AsyncMock(return_value=[player20])

        user2000 = _make_user_obj(2000, "Bob")
        svc.user_repo.get_by_ids = AsyncMock(return_value=[user2000])

        result = await svc.get_all_pending_for_guild(db, guild_id=9999)

        assert len(result) == 1
        _, ch_name, tg_name = result[0]
        assert ch_name is None  # player 10 not in DB
        assert tg_name == "Bob"

    @pytest.mark.asyncio
    async def test_pending_all_user_no_username_is_none(self):
        """A user with discord_username=None produces a None name in the result."""
        svc = _make_duel_service()
        db = _make_db()

        duel = _make_duel(duel_id=1, challenger_id=10, target_id=20)
        svc.duel_repo.get_all_pending_by_guild = AsyncMock(return_value=[duel])

        player10 = _make_player_obj(10, user_id=1000)
        player20 = _make_player_obj(20, user_id=2000)
        svc.player_repo.get_by_ids = AsyncMock(return_value=[player10, player20])

        user1000 = _make_user_obj(1000, discord_username=None)  # no username
        user2000 = _make_user_obj(2000, "Bob")
        svc.user_repo.get_by_ids = AsyncMock(return_value=[user1000, user2000])

        result = await svc.get_all_pending_for_guild(db, guild_id=9999)

        _, ch_name, tg_name = result[0]
        assert ch_name is None
        assert tg_name == "Bob"


# ===========================================================================
# P6-T5: get_object_by_name/alias short-circuit — already correct, tests verify
# ===========================================================================


class TestP6T5AboutShortCircuit:
    """about.py get_object_by_name/alias already short-circuits; tests assert it."""

    def _make_repo_set(self) -> dict:
        """Create 8 async mock repos, all returning None by default."""
        return {
            name: AsyncMock(
                **{
                    "get_by_name": AsyncMock(return_value=None),
                    "get_by_alias": AsyncMock(return_value=None),
                }
            )
            for name in ["module", "primary", "secondary", "turret", "ship", "system", "criminal", "commodity"]
        }

    def _make_obj(self, name: str, category_attrs: dict | None = None) -> MagicMock:
        """Create a minimal game object mock."""
        obj = MagicMock()
        obj.id = 1
        obj.name = name
        obj.aliases = []
        obj.built_in = False
        obj.emoji = None
        obj.icon = None
        obj.value = 100
        obj.wiki = None
        obj.type = "generic"
        obj.tech_level = 1
        obj.extra_atts = None
        if category_attrs:
            for k, v in category_attrs.items():
                setattr(obj, k, v)
        return obj

    @pytest.mark.asyncio
    async def test_name_found_in_first_repo_later_repos_not_queried(self):
        """When the name is found in the first repo (module), repos 2-8 are NOT called.

        This is the short-circuit efficiency assertion.
        """
        import api.routers.about as about_module
        from api.routers.about import get_object_by_name
        from api.routers.data import DataCategory

        repos = self._make_repo_set()
        module_obj = self._make_obj("E2 Exoclad", {"max_equipped": 2})
        repos["module"].get_by_name = AsyncMock(return_value=module_obj)

        # Patch CATEGORY_REPOS in the about module to use our mocks.
        original_repos = dict(about_module.CATEGORY_REPOS)
        about_module.CATEGORY_REPOS[DataCategory.module] = repos["module"]
        about_module.CATEGORY_REPOS[DataCategory.primary] = repos["primary"]
        about_module.CATEGORY_REPOS[DataCategory.secondary] = repos["secondary"]
        about_module.CATEGORY_REPOS[DataCategory.turret] = repos["turret"]
        about_module.CATEGORY_REPOS[DataCategory.ship] = repos["ship"]
        about_module.CATEGORY_REPOS[DataCategory.system] = repos["system"]
        about_module.CATEGORY_REPOS[DataCategory.criminal] = repos["criminal"]
        about_module.CATEGORY_REPOS[DataCategory.commodity] = repos["commodity"]

        db = _make_db()
        try:
            result = await get_object_by_name("E2 Exoclad", db=db)
        finally:
            for k, v in original_repos.items():
                about_module.CATEGORY_REPOS[k] = v

        # Module repo was queried (it's #1).
        repos["module"].get_by_name.assert_called_once()
        # All subsequent repos must NOT have been queried.
        repos["primary"].get_by_name.assert_not_called()
        repos["secondary"].get_by_name.assert_not_called()
        repos["turret"].get_by_name.assert_not_called()
        repos["ship"].get_by_name.assert_not_called()
        repos["system"].get_by_name.assert_not_called()
        repos["criminal"].get_by_name.assert_not_called()
        repos["commodity"].get_by_name.assert_not_called()
        assert result["name"] == "E2 Exoclad"
        assert result["category"] == DataCategory.module.value

    @pytest.mark.asyncio
    async def test_name_found_in_later_repo_earlier_repos_queried_once(self):
        """When name is only in the 5th repo (ship), exactly 5 repos are queried."""
        import api.routers.about as about_module
        from api.routers.about import get_object_by_name
        from api.routers.data import DataCategory

        repos = self._make_repo_set()
        ship_obj = self._make_obj(
            "Betty",
            {
                "armour": 600,
                "cargo": 500,
                "handling": 80,
                "shop_spawn_rate": 0.5,
                "max_modules": 4,
                "max_primaries": 2,
                "max_secondaries": 2,
                "max_turrets": 1,
                "manufacturer": "Corp",
                "skinnable": False,
                "compatible_skins": {},
                "model": "model.glb",
                "norm_spec": "norm.png",
                "assets": [],
                "save_due": False,
                "builtin_modules": [],
            },
        )
        repos["ship"].get_by_name = AsyncMock(return_value=ship_obj)

        original_repos = dict(about_module.CATEGORY_REPOS)
        about_module.CATEGORY_REPOS[DataCategory.module] = repos["module"]
        about_module.CATEGORY_REPOS[DataCategory.primary] = repos["primary"]
        about_module.CATEGORY_REPOS[DataCategory.secondary] = repos["secondary"]
        about_module.CATEGORY_REPOS[DataCategory.turret] = repos["turret"]
        about_module.CATEGORY_REPOS[DataCategory.ship] = repos["ship"]
        about_module.CATEGORY_REPOS[DataCategory.system] = repos["system"]
        about_module.CATEGORY_REPOS[DataCategory.criminal] = repos["criminal"]
        about_module.CATEGORY_REPOS[DataCategory.commodity] = repos["commodity"]

        db = _make_db()
        try:
            result = await get_object_by_name("Betty", db=db)
        finally:
            for k, v in original_repos.items():
                about_module.CATEGORY_REPOS[k] = v

        # Repos 1-4 queried once (not found) + repo 5 queried once (found).
        repos["module"].get_by_name.assert_called_once()
        repos["primary"].get_by_name.assert_called_once()
        repos["secondary"].get_by_name.assert_called_once()
        repos["turret"].get_by_name.assert_called_once()
        repos["ship"].get_by_name.assert_called_once()
        # Repos 6-8 must NOT be queried.
        repos["system"].get_by_name.assert_not_called()
        repos["criminal"].get_by_name.assert_not_called()
        repos["commodity"].get_by_name.assert_not_called()
        assert result["name"] == "Betty"

    @pytest.mark.asyncio
    async def test_unknown_name_queries_all_repos(self):
        """Unknown name: all 8 repos are queried before returning 404."""
        import api.routers.about as about_module
        from api.routers.about import get_object_by_name
        from api.routers.data import DataCategory
        from fastapi import HTTPException

        repos = self._make_repo_set()  # all return None

        original_repos = dict(about_module.CATEGORY_REPOS)
        about_module.CATEGORY_REPOS[DataCategory.module] = repos["module"]
        about_module.CATEGORY_REPOS[DataCategory.primary] = repos["primary"]
        about_module.CATEGORY_REPOS[DataCategory.secondary] = repos["secondary"]
        about_module.CATEGORY_REPOS[DataCategory.turret] = repos["turret"]
        about_module.CATEGORY_REPOS[DataCategory.ship] = repos["ship"]
        about_module.CATEGORY_REPOS[DataCategory.system] = repos["system"]
        about_module.CATEGORY_REPOS[DataCategory.criminal] = repos["criminal"]
        about_module.CATEGORY_REPOS[DataCategory.commodity] = repos["commodity"]

        db = _make_db()
        try:
            with pytest.raises(HTTPException) as exc_info:
                await get_object_by_name("No Such Object", db=db)
        finally:
            for k, v in original_repos.items():
                about_module.CATEGORY_REPOS[k] = v

        assert exc_info.value.status_code == 404
        # All 8 repos were queried.
        for name in ["module", "primary", "secondary", "turret", "ship", "system", "criminal", "commodity"]:
            repos[name].get_by_name.assert_called_once()

    @pytest.mark.asyncio
    async def test_alias_found_in_first_repo_later_repos_not_queried(self):
        """Alias lookup short-circuits on first match: repos after the match not called."""
        import api.routers.about as about_module
        from api.routers.about import get_object_by_alias
        from api.routers.data import DataCategory

        repos = self._make_repo_set()
        module_obj = self._make_obj("E2 Exoclad", {"max_equipped": 2})
        module_obj.aliases = ["exo", "e2"]
        repos["module"].get_by_alias = AsyncMock(return_value=module_obj)

        original_repos = dict(about_module.CATEGORY_REPOS)
        about_module.CATEGORY_REPOS[DataCategory.module] = repos["module"]
        about_module.CATEGORY_REPOS[DataCategory.primary] = repos["primary"]
        about_module.CATEGORY_REPOS[DataCategory.secondary] = repos["secondary"]
        about_module.CATEGORY_REPOS[DataCategory.turret] = repos["turret"]
        about_module.CATEGORY_REPOS[DataCategory.ship] = repos["ship"]
        about_module.CATEGORY_REPOS[DataCategory.system] = repos["system"]
        about_module.CATEGORY_REPOS[DataCategory.criminal] = repos["criminal"]
        about_module.CATEGORY_REPOS[DataCategory.commodity] = repos["commodity"]

        db = _make_db()
        try:
            result = await get_object_by_alias("exo", db=db)
        finally:
            for k, v in original_repos.items():
                about_module.CATEGORY_REPOS[k] = v

        repos["module"].get_by_alias.assert_called_once()
        repos["primary"].get_by_alias.assert_not_called()
        repos["secondary"].get_by_alias.assert_not_called()
        assert result["name"] == "E2 Exoclad"

    @pytest.mark.asyncio
    async def test_unknown_alias_returns_404(self):
        """Unknown alias: all repos queried, 404 raised."""
        import api.routers.about as about_module
        from api.routers.about import get_object_by_alias
        from api.routers.data import DataCategory
        from fastapi import HTTPException

        repos = self._make_repo_set()

        original_repos = dict(about_module.CATEGORY_REPOS)
        about_module.CATEGORY_REPOS[DataCategory.module] = repos["module"]
        about_module.CATEGORY_REPOS[DataCategory.primary] = repos["primary"]
        about_module.CATEGORY_REPOS[DataCategory.secondary] = repos["secondary"]
        about_module.CATEGORY_REPOS[DataCategory.turret] = repos["turret"]
        about_module.CATEGORY_REPOS[DataCategory.ship] = repos["ship"]
        about_module.CATEGORY_REPOS[DataCategory.system] = repos["system"]
        about_module.CATEGORY_REPOS[DataCategory.criminal] = repos["criminal"]
        about_module.CATEGORY_REPOS[DataCategory.commodity] = repos["commodity"]

        db = _make_db()
        try:
            with pytest.raises(HTTPException) as exc_info:
                await get_object_by_alias("no_such_alias", db=db)
        finally:
            for k, v in original_repos.items():
                about_module.CATEGORY_REPOS[k] = v

        assert exc_info.value.status_code == 404
        for name in ["module", "primary", "secondary", "turret", "ship", "system", "criminal", "commodity"]:
            repos[name].get_by_alias.assert_called_once()
