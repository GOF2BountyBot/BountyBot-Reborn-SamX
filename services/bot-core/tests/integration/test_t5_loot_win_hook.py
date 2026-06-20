"""T5 — loot-core win-branch hook + write (LOOT_JOURNAL §5.2-§5.9, §7.6).

The most delicate integration in the PvC looting feature: loot is written ONLY
on a player COMBAT WIN, as its OWN player-FOR-UPDATE-locked transaction that is
failure-isolated from (and non-atomic with) the bounty rewards/XP.

Three layers:

* **Trigger-exclusion** (``TestWinBranchTrigger``) — drives the real
  ``_process_single_bounty_check`` with combat + reward deps stubbed and
  ``_apply_loot_on_win`` replaced by a SPY, asserting loot fires on a proper
  combat win ONLY (Bronze bonus win / Silver+ ``winner_side==1``) and NOT on the
  bare capture, a loss, a stalemate, or the no-ship shortcut.
* **Loot routine** (``TestApplyLootOnWin``) — calls ``_apply_loot_on_win``
  directly against the seeded throwaway Postgres with a REAL LootService cache +
  REAL inventory write: tractor gate, M-1 cargo-full gate, §5.4 clamp
  (partial/full), success/fail (seeded/stubbed rng), concrete item_type, own
  commit + failure isolation, and the free-cargo-read-before-write lock order.
* **Fold-in** (``TestTractorMapRekey``) — the M-5 tractor-map override re-key no
  longer drops a beam when two tiers share a default chance VALUE.

Mock budget: ≤2 mocks per test, real objects preferred.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest
from persist.models.player import Player
from persist.models.player_inventory import PlayerInventory
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from services.bounty_service import BountyService, CheckResult, LootOutcome
from services.combat_models import ModuleStats, ShipLoadout
from services.game_constants import GameConstants
from services.loot_service import LootService
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# pg_env lives in tests/ (one level up).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pg_env import PG_ASYNC_URL, pg_skip_reason

_PG_SKIP = pg_skip_reason()

# Isolation constants that cannot collide with production rows.
_TEST_GUILD = 999_888_555_050
_TEST_USER = 999_888_555_051

# Seed-data anchors (verified present in the throwaway PG at head 0022).
_SHIP_NAME = "Betty"  # ship table row, cargo = 25
_SHIP_CARGO = 25
_RETRACTOR = 'AB-1 "Retractor"'  # TractorBeamModule, TL4 → 20% chance
_COMMODITY = "Vulpes Soup"  # a real booze commodity (T1 made commodity writable)
_WEAPON = "Micro Gun MK I"  # a real primary weapon
_INV_TYPE_WEAPON = "primary_weapon"


# ===========================================================================
# Fold-in (no DB): tractor-map override re-key (M-5)
# ===========================================================================


def _module_row(name: str, kind: str, tl: int) -> SimpleNamespace:
    return SimpleNamespace(name=name, type=kind, tech_level=tl, value=1000)


class TestTractorMapRekey:
    """The override re-key keys by TIER, not by chance VALUE — no beam dropped."""

    async def _loaded_service(self, modules) -> LootService:
        svc = LootService()
        svc.module_repo = AsyncMock()
        svc.module_repo.list_all.return_value = modules
        svc.primary_weapon_repo = AsyncMock()
        svc.primary_weapon_repo.list_all.return_value = []
        svc.secondary_weapon_repo = AsyncMock()
        svc.secondary_weapon_repo.list_all.return_value = []
        svc.turret_weapon_repo = AsyncMock()
        svc.turret_weapon_repo.list_all.return_value = []
        svc.commodity_repo = AsyncMock()
        svc.commodity_repo.list_all.return_value = []
        await svc.preload_static_data(MagicMock())
        return svc

    async def test_tier_map_built_alongside_chance_map(self) -> None:
        svc = await self._loaded_service(
            [
                _module_row(_RETRACTOR, "TractorBeamModule", 4),
                _module_row('AB-2 "Glue Gun"', "TractorBeamModule", 5),
                _module_row('AB-3 "Kingfisher"', "TractorBeamModule", 7),
                _module_row('AB-4 "Octopus"', "TractorBeamModule", 8),
            ]
        )
        assert svc._tractor_tier_map == {
            _RETRACTOR: 1,
            'AB-2 "Glue Gun"': 2,
            'AB-3 "Kingfisher"': 3,
            'AB-4 "Octopus"': 4,
        }

    async def test_value_collision_does_not_drop_a_beam(self) -> None:
        """Two tiers tuned to the SAME chance value still resolve to distinct knobs.

        A guild config sets T2 and T3 BOTH to 40 (a value collision).  The old
        value-keyed override lookup would collapse them into one map entry; the
        re-key by tier keeps every beam on its own knob — all four beams resolve.
        """
        svc = await self._loaded_service(
            [
                _module_row(_RETRACTOR, "TractorBeamModule", 4),
                _module_row('AB-2 "Glue Gun"', "TractorBeamModule", 5),
                _module_row('AB-3 "Kingfisher"', "TractorBeamModule", 7),
                _module_row('AB-4 "Octopus"', "TractorBeamModule", 8),
            ]
        )
        cfg = SimpleNamespace(
            loot_chance_tractor_t1=10,
            loot_chance_tractor_t2=40,
            loot_chance_tractor_t3=40,  # COLLISION with t2
            loot_chance_tractor_t4=90,
        )
        resolved = svc.resolve_tractor_chance_map(cfg)
        # No beam dropped — all four present, each on its own knob.
        assert resolved == {
            _RETRACTOR: 10,
            'AB-2 "Glue Gun"': 40,
            'AB-3 "Kingfisher"': 40,
            'AB-4 "Octopus"': 90,
        }
        # And loot_chance returns the right per-beam value despite the collision.
        assert svc.loot_chance(['AB-3 "Kingfisher"'], guild_config=cfg) == 40
        assert svc.loot_chance(['AB-4 "Octopus"'], guild_config=cfg) == 90

    async def test_default_chances_unchanged(self) -> None:
        """Behaviour at the 20/40/60/80 defaults is identical to pre-fold-in."""
        svc = await self._loaded_service(
            [
                _module_row(_RETRACTOR, "TractorBeamModule", 4),
                _module_row('AB-2 "Glue Gun"', "TractorBeamModule", 5),
                _module_row('AB-3 "Kingfisher"', "TractorBeamModule", 7),
                _module_row('AB-4 "Octopus"', "TractorBeamModule", 8),
            ]
        )
        assert svc.resolve_tractor_chance_map(None) == {
            _RETRACTOR: 20,
            'AB-2 "Glue Gun"': 40,
            'AB-3 "Kingfisher"': 60,
            'AB-4 "Octopus"': 80,
        }

    async def test_equipped_tractor_name_resolves_first_beam(self) -> None:
        svc = await self._loaded_service([_module_row(_RETRACTOR, "TractorBeamModule", 4)])
        assert svc.equipped_tractor_name(["E2 Exoclad", _RETRACTOR]) == _RETRACTOR
        assert svc.equipped_tractor_name(["E2 Exoclad", "Beamshield II"]) is None
        assert svc.equipped_tractor_name([]) is None


# ===========================================================================
# Trigger-exclusion: which branches fire loot (SQLite-free; combat/reward mocked)
# ===========================================================================


def _fight(winner_side, is_stalemate=False):
    return SimpleNamespace(winner_side=winner_side, is_stalemate=is_stalemate)


def _trigger_service():
    """A BountyService with the win-branch surroundings stubbed and loot SPIED.

    Stubs (repo/reward/combat boundary) so we exercise ONLY the trigger guards
    in ``_process_single_bounty_check``; ``_apply_loot_on_win`` is replaced by an
    AsyncMock spy so a call == "loot fired".  ``fight_ships`` is the second mock
    when combat must run.  (≤2 mocks of interest per test — the rest are inert
    plumbing stubs shared by the fixture.)
    """
    svc = BountyService()
    # Bounty lock returns a ready, active, correct-answer bounty carrying cargo.
    bounty = SimpleNamespace(
        id=4242,
        status="active",
        checked={},
        answer="SOL",
        criminal_name="Viper",
        criminal_ship={
            "ship_name": "Betty",
            "cargo": {"item_type": "commodity", "item_name": _COMMODITY, "quantity": 5},
        },
        reward_per_sys=10,
        route=["SOL"],
    )
    svc.bounty_repo = MagicMock()
    svc.bounty_repo.get_by_id_for_update = AsyncMock(return_value=bounty)
    svc.bounty_repo.update = AsyncMock()
    svc.calc_rewards = AsyncMock(return_value=[SimpleNamespace(credits_earned=100, is_winner=True)])
    svc.distribute_rewards = AsyncMock()
    svc._build_payout_breakdown = AsyncMock(return_value=[])
    svc._award_combat_bonus = AsyncMock()
    svc._reset_bounty_checks = AsyncMock()
    # SPY: a call here means loot fired.
    svc._apply_loot_on_win = AsyncMock(return_value=LootOutcome(outcome="looted"))
    return svc, bounty


def _player(*, classic_mode=False, has_ship=True):
    return SimpleNamespace(
        id=7,
        user_id=_TEST_USER,
        guild_id=_TEST_GUILD,
        classic_mode=classic_mode,
        active_ship_id=99 if has_ship else None,
        display_name="Tester",
    )


def _loadout_with_beam():
    return ShipLoadout(ship_name=_SHIP_NAME, base_armour=100, modules=[ModuleStats(name=_RETRACTOR)])


async def _run_single_check(svc, player, *, division, db):
    from datetime import UTC, datetime

    return await svc._process_single_bounty_check(
        db,
        player=player,
        player_id=player.id,
        bounty=svc.bounty_repo.get_by_id_for_update.return_value,
        system_name="SOL",
        division=division,
        now=datetime.now(UTC),
        cfg=None,
    )


@asynccontextmanager
async def _patch_combat_and_loadout(svc, fight_results, has_ship=True):
    """Patch fight_ships + LoadoutBuilder so the branch runs without DB combat.

    ``LoadoutBuilder`` is imported function-locally inside
    ``_process_single_bounty_check``, so it must be patched at its source module
    (``services.loadout_builder``) — not as a ``bounty_service`` attribute.
    """
    import services.loadout_builder as lb

    from services import bounty_service as bs

    svc.combat_service = MagicMock()
    svc.combat_service.fight_ships = AsyncMock(return_value=fight_results)
    real_from_player = lb.LoadoutBuilder.from_player
    real_from_criminal = lb.LoadoutBuilder.from_criminal_ship
    real_serialize = bs._serialize_fight_results
    lb.LoadoutBuilder.from_player = AsyncMock(return_value=_loadout_with_beam())
    lb.LoadoutBuilder.from_criminal_ship = MagicMock(return_value=ShipLoadout(ship_name="Betty", base_armour=90))
    # Our _fight() stand-in is a minimal SimpleNamespace (only winner_side/
    # is_stalemate matter to the trigger guards); stub the full serializer so the
    # post-hook response build doesn't need the entire FightResults shape.
    bs._serialize_fight_results = MagicMock(return_value={"winner_side": fight_results.winner_side})
    try:
        yield
    finally:
        lb.LoadoutBuilder.from_player = real_from_player
        lb.LoadoutBuilder.from_criminal_ship = real_from_criminal
        bs._serialize_fight_results = real_serialize


class TestWinBranchTrigger:
    """Loot fires on a proper combat WIN only — exhaustive branch coverage."""

    async def test_bronze_bonus_win_fires_loot(self) -> None:
        svc, _ = _trigger_service()
        db = AsyncMock()
        async with _patch_combat_and_loadout(svc, _fight(winner_side=1)):
            outcome, _ = await _run_single_check(svc, _player(classic_mode=True), division="bronze", db=db)
        assert outcome.result == CheckResult.CORRECT
        svc._apply_loot_on_win.assert_awaited_once()  # loot fired on the bonus win
        assert outcome.loot is not None

    async def test_bronze_bonus_loss_no_loot(self) -> None:
        svc, _ = _trigger_service()
        db = AsyncMock()
        async with _patch_combat_and_loadout(svc, _fight(winner_side=2)):
            outcome, _ = await _run_single_check(svc, _player(classic_mode=True), division="bronze", db=db)
        # Capture still succeeds (bronze auto-capture) but the bonus fight was lost.
        assert outcome.result == CheckResult.CORRECT
        svc._apply_loot_on_win.assert_not_awaited()
        assert outcome.loot is None

    async def test_bronze_bonus_stalemate_no_loot(self) -> None:
        svc, _ = _trigger_service()
        db = AsyncMock()
        async with _patch_combat_and_loadout(svc, _fight(winner_side=None, is_stalemate=True)):
            outcome, _ = await _run_single_check(svc, _player(classic_mode=True), division="bronze", db=db)
        svc._apply_loot_on_win.assert_not_awaited()  # draw = no loot (§5.2)
        assert outcome.loot is None

    async def test_bronze_no_ship_no_loot(self) -> None:
        svc, _ = _trigger_service()
        db = AsyncMock()
        # No fight runs (no ship) → combat_player_won never set → no loot.
        async with _patch_combat_and_loadout(svc, _fight(winner_side=1)):
            outcome, _ = await _run_single_check(
                svc, _player(classic_mode=True, has_ship=False), division="bronze", db=db
            )
        assert outcome.result == CheckResult.CORRECT  # bare auto-capture
        svc._apply_loot_on_win.assert_not_awaited()
        assert outcome.loot is None

    async def test_silver_win_fires_loot(self) -> None:
        svc, _ = _trigger_service()
        db = AsyncMock()
        async with _patch_combat_and_loadout(svc, _fight(winner_side=1)):
            outcome, _ = await _run_single_check(svc, _player(), division="silver", db=db)
        assert outcome.result == CheckResult.CORRECT
        svc._apply_loot_on_win.assert_awaited_once()
        assert outcome.loot is not None

    async def test_silver_loss_no_loot(self) -> None:
        svc, _ = _trigger_service()
        db = AsyncMock()
        async with _patch_combat_and_loadout(svc, _fight(winner_side=2)):
            outcome, _ = await _run_single_check(svc, _player(), division="silver", db=db)
        assert outcome.combat_won is False
        svc._apply_loot_on_win.assert_not_awaited()

    async def test_silver_stalemate_no_loot(self) -> None:
        svc, _ = _trigger_service()
        db = AsyncMock()
        async with _patch_combat_and_loadout(svc, _fight(winner_side=None, is_stalemate=True)):
            outcome, _ = await _run_single_check(svc, _player(), division="silver", db=db)
        assert outcome.combat_won is False
        svc._apply_loot_on_win.assert_not_awaited()

    async def test_silver_no_ship_shortcut_no_loot(self) -> None:
        """No-ship Silver+ sets duel_won=True with fight_results=None → NO loot."""
        svc, _ = _trigger_service()
        db = AsyncMock()
        # has_ship=False ⇒ the `_no_ship` shortcut: duel_won=True, fight_results=None.
        async with _patch_combat_and_loadout(svc, _fight(winner_side=1), has_ship=False):
            outcome, _ = await _run_single_check(svc, _player(has_ship=False), division="silver", db=db)
        assert outcome.result == CheckResult.CORRECT  # non-kill capture still rewards
        svc._apply_loot_on_win.assert_not_awaited()  # fight_results is None → excluded
        assert outcome.loot is None


# ===========================================================================
# Loot routine against the seeded throwaway Postgres
# ===========================================================================


@asynccontextmanager
async def _pg():
    engine = create_async_engine(PG_ASYNC_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _cleanup(factory) -> None:
    async with factory() as db, db.begin():
        # Break the players → active_ship_id FK before deleting player_ships.
        await db.execute(text(f"UPDATE players SET active_ship_id = NULL WHERE guild_id = {_TEST_GUILD}"))
        await db.execute(
            text(
                "DELETE FROM player_inventories WHERE player_id IN "
                f"(SELECT id FROM players WHERE guild_id = {_TEST_GUILD})"
            )
        )
        await db.execute(
            text(f"DELETE FROM player_ships WHERE player_id IN (SELECT id FROM players WHERE guild_id = {_TEST_GUILD})")
        )
        await db.execute(text(f"DELETE FROM players WHERE guild_id = {_TEST_GUILD}"))
        await db.execute(text(f"DELETE FROM users WHERE id = {_TEST_USER}"))


async def _seed_player_with_ship(factory, *, equip_beam: bool, cargo_load: int) -> int:
    """Seed a player on the active ``_SHIP_NAME`` (cargo 25), optionally with the
    Retractor equipped, pre-loaded with ``cargo_load`` units of the test weapon."""
    async with factory() as db, db.begin():
        if await db.get(User, _TEST_USER) is None:
            db.add(User(id=_TEST_USER, discord_username="t5tester"))
            await db.flush()
        player = Player(user_id=_TEST_USER, guild_id=_TEST_GUILD, credits=10_000, tier="Bronze", classic_mode=False)
        db.add(player)
        await db.flush()
        ship = PlayerShip(
            player_id=player.id,
            ship_name=_SHIP_NAME,
            is_active=True,
            modules=[_RETRACTOR] if equip_beam else [],
            weapons=[],
            turrets=[],
            secondary_weapons=[],
        )
        db.add(ship)
        await db.flush()
        player.active_ship_id = ship.id
        if cargo_load > 0:
            db.add(
                PlayerInventory(player_id=player.id, item_type=_INV_TYPE_WEAPON, item_name=_WEAPON, quantity=cargo_load)
            )
        await db.flush()
        return player.id


async def _inv_qty(factory, player_id: int, item_name: str) -> int:
    async with factory() as db:
        res = await db.execute(
            select(PlayerInventory.quantity).where(
                PlayerInventory.player_id == player_id, PlayerInventory.item_name == item_name
            )
        )
        return res.scalars().first() or 0


async def _player_credits(factory, player_id: int) -> int:
    async with factory() as db:
        return (await db.execute(select(Player.credits).where(Player.id == player_id))).scalars().first()


def _bounty_with_cargo(item_type: str, item_name: str, quantity: int):
    return SimpleNamespace(
        id=5151,
        criminal_name="Viper",
        criminal_ship={
            "ship_name": _SHIP_NAME,
            "cargo": {"item_type": item_type, "item_name": item_name, "quantity": quantity},
        },
    )


def _loadout(beam: bool):
    mods = [ModuleStats(name=_RETRACTOR)] if beam else [ModuleStats(name="E2 Exoclad")]
    return ShipLoadout(ship_name=_SHIP_NAME, base_armour=100, modules=mods)


async def _fresh_service(factory) -> BountyService:
    """A real BountyService with a warm LootService cache (real preload from PG)."""
    svc = BountyService()
    async with factory() as db:
        await svc.loot_service.preload_static_data(db)
    return svc


@pytest.mark.skipif(_PG_SKIP is not None, reason=_PG_SKIP or "")
class TestApplyLootOnWin:
    async def test_no_beam_outcome_none_no_write(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player_with_ship(factory, equip_beam=False, cargo_load=0)
                svc = await _fresh_service(factory)
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_with_cargo("commodity", _COMMODITY, 5),
                        player_loadout=_loadout(beam=False),
                        cfg=None,
                    )
                assert outcome.outcome == "none"
                assert await _inv_qty(factory, pid, _COMMODITY) == 0
            finally:
                await _cleanup(factory)

    async def test_absent_cargo_outcome_none(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player_with_ship(factory, equip_beam=True, cargo_load=0)
                svc = await _fresh_service(factory)
                bounty = SimpleNamespace(id=1, criminal_name="V", criminal_ship={"ship_name": _SHIP_NAME})  # no cargo
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(
                        db, player=player, player_id=pid, bounty=bounty, player_loadout=_loadout(beam=True), cfg=None
                    )
                assert outcome.outcome == "none"
            finally:
                await _cleanup(factory)

    async def test_cargo_full_skips_roll(self) -> None:
        """At cap (load == ship cargo) → outcome cargo_full, no roll, no write."""
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player_with_ship(factory, equip_beam=True, cargo_load=_SHIP_CARGO)  # full
                svc = await _fresh_service(factory)
                # Stub the success roll so a leaked roll would be a write — proving skip.
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_with_cargo("commodity", _COMMODITY, 5),
                        player_loadout=_loadout(beam=True),
                        cfg=None,
                    )
                assert outcome.outcome == "cargo_full"
                assert outcome.cargo_current == _SHIP_CARGO and outcome.cargo_max == _SHIP_CARGO
                svc.loot_service.roll_loot_success.assert_not_called()  # roll skipped (M-1)
                assert await _inv_qty(factory, pid, _COMMODITY) == 0
            finally:
                await _cleanup(factory)

    async def test_success_full_haul_looted(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player_with_ship(factory, equip_beam=True, cargo_load=0)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_with_cargo("commodity", _COMMODITY, 6),
                        player_loadout=_loadout(beam=True),
                        cfg=None,
                    )
                assert outcome.outcome == "looted"
                assert outcome.qty_looted == 6 and outcome.qty_total == 6
                assert outcome.tractor_name == _RETRACTOR
                assert await _inv_qty(factory, pid, _COMMODITY) == 6  # committed
            finally:
                await _cleanup(factory)

    async def test_clamp_partial_when_room_less_than_haul(self) -> None:
        """Free cargo M < criminal N → exactly M looted, outcome partial."""
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                # Load 21 of 25 → free = 4. Criminal carries 10 → take 4, partial.
                pid = await _seed_player_with_ship(factory, equip_beam=True, cargo_load=21)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_with_cargo("commodity", _COMMODITY, 10),
                        player_loadout=_loadout(beam=True),
                        cfg=None,
                    )
                assert outcome.outcome == "partial"
                assert outcome.qty_looted == 4 and outcome.qty_total == 10
                assert await _inv_qty(factory, pid, _COMMODITY) == 4  # only what fit
            finally:
                await _cleanup(factory)

    async def test_fail_roll_outcome_failed_no_write(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player_with_ship(factory, equip_beam=True, cargo_load=0)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=False)  # tractor miss
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_with_cargo("commodity", _COMMODITY, 5),
                        player_loadout=_loadout(beam=True),
                        cfg=None,
                    )
                assert outcome.outcome == "failed"
                assert outcome.qty_looted == 0 and outcome.qty_total == 5
                assert await _inv_qty(factory, pid, _COMMODITY) == 0
            finally:
                await _cleanup(factory)

    async def test_concrete_item_type_used_for_weapon_loot(self) -> None:
        """A looted weapon is written under its concrete type, never the alias."""
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player_with_ship(factory, equip_beam=True, cargo_load=0)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_with_cargo("primary_weapon", _WEAPON, 2),
                        player_loadout=_loadout(beam=True),
                        cfg=None,
                    )
                assert outcome.outcome == "looted"
                async with factory() as db:
                    row = (
                        (
                            await db.execute(
                                select(PlayerInventory.item_type).where(
                                    PlayerInventory.player_id == pid, PlayerInventory.item_name == _WEAPON
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                assert row == "primary_weapon"  # concrete, never "weapon"
            finally:
                await _cleanup(factory)

    async def test_failure_isolation_does_not_fail_or_roll_back(self) -> None:
        """A forced loot-write exception → outcome none, NO raise, rewards intact.

        We commit a reward credit, then force ``add_item_to_inventory`` to raise.
        ``_apply_loot_on_win`` must swallow it (return outcome none) and the prior
        committed credit must survive (the loot rollback only undoes the loot txn).
        """
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player_with_ship(factory, equip_beam=True, cargo_load=0)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)
                # Simulate distribute_rewards having ALREADY committed a reward.
                async with factory() as db, db.begin():
                    p = await db.get(Player, pid)
                    p.credits = 55_555
                # Force the loot write to blow up.
                svc.inventory_service.add_item_to_inventory = AsyncMock(side_effect=RuntimeError("boom"))
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(  # must NOT raise
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_with_cargo("commodity", _COMMODITY, 5),
                        player_loadout=_loadout(beam=True),
                        cfg=None,
                    )
                assert outcome.outcome == "none"  # benign degrade, no crash
                assert await _player_credits(factory, pid) == 55_555  # reward preserved
                assert await _inv_qty(factory, pid, _COMMODITY) == 0
            finally:
                await _cleanup(factory)

    async def test_bronze_same_session_bonus_survives_loot_failure(self) -> None:
        """REWORK BLOCKER repro: the Bronze combat bonus (credits+XP) must survive
        a loot-write failure even when awarded in the SAME live session as the loot
        hook (§7.6 / §5.5 C-3b).

        Production order at Bronze: distribute_rewards COMMITS base rewards →
        _award_combat_bonus mutates player.credits/player.xp → _apply_loot_on_win
        runs.  Before the fix, _award_combat_bonus left those deltas PENDING; the
        loot hook's first get_by_id_for_update autoflushed them into its txn, and a
        loot-write failure's rollback silently undid the 2x bonus + XP.  The fix
        commits the bonus inside _award_combat_bonus, so the loot rollback has
        nothing of ours left to undo.

        This test exercises that exact same-session sequence with the loot write
        FORCED to raise, and asserts BOTH bonus credits AND bonus XP survive in the
        FINAL committed DB state, /check is unaffected (no raise), and loot degrades
        to outcome "none".  It FAILS against pre-fix code and PASSES after the fix.
        """
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player_with_ship(factory, equip_beam=True, cargo_load=0)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)
                # Force the loot write to blow up so its handler rolls back.
                svc.inventory_service.add_item_to_inventory = AsyncMock(side_effect=RuntimeError("boom"))

                base_credits = 10_000  # seeded by _seed_player_with_ship
                base_xp = 0  # fresh player
                bonus = 250
                expected_bonus_xp = int(bonus * GameConstants.BOUNTY_REWARD_TO_XP_GAIN_MULT)

                # SINGLE live session reproducing the production Bronze sequence.
                async with factory() as db:
                    player = await db.get(Player, pid)
                    # 1) base rewards already committed (distribute_rewards) — simulate.
                    player.credits = base_credits
                    player.xp = base_xp
                    await db.commit()
                    # 2) award the combat bonus (now commits internally per the fix),
                    #    leaving credits/xp deltas that pre-fix would be PENDING.
                    await svc._award_combat_bonus(db, pid, bonus)
                    # 3) loot hook fires in the SAME session and FAILS → its handler
                    #    rolls back.  Pre-fix, autoflush had pulled the bonus into the
                    #    loot txn, so this rollback wiped it out.
                    outcome = await svc._apply_loot_on_win(  # must NOT raise
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_with_cargo("commodity", _COMMODITY, 5),
                        player_loadout=_loadout(beam=True),
                        cfg=None,
                    )

                # /check unaffected: no raise, loot degraded to none.
                assert outcome.outcome == "none"
                assert await _inv_qty(factory, pid, _COMMODITY) == 0
                # FINAL committed DB state: BOTH bonus credits AND bonus XP survive.
                async with factory() as verify:
                    final = await verify.get(Player, pid)
                    assert final.credits == base_credits + bonus
                    assert final.xp == base_xp + expected_bonus_xp
            finally:
                await _cleanup(factory)

    async def test_free_cargo_read_before_write_under_lock(self) -> None:
        """The free-cargo read happens BEFORE the inventory write (lock-order intent).

        ``_player_free_cargo`` (the clamp read) must be invoked before
        ``add_item_to_inventory`` (the write), so both occur inside the held
        player FOR UPDATE window.  We record call order via wrapping spies.
        """
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player_with_ship(factory, equip_beam=True, cargo_load=0)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)
                order: list[str] = []
                real_free = svc._player_free_cargo
                real_add = svc.inventory_service.add_item_to_inventory

                async def _spy_free(*a, **k):
                    order.append("read")
                    return await real_free(*a, **k)

                async def _spy_add(*a, **k):
                    order.append("write")
                    return await real_add(*a, **k)

                svc._player_free_cargo = _spy_free
                svc.inventory_service.add_item_to_inventory = _spy_add
                async with factory() as db:
                    player = await db.get(Player, pid)
                    await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_with_cargo("commodity", _COMMODITY, 3),
                        player_loadout=_loadout(beam=True),
                        cfg=None,
                    )
                assert order == ["read", "write"]  # clamp read precedes the write
            finally:
                await _cleanup(factory)

    async def test_each_beam_tier_chance_via_loot_chance(self) -> None:
        """Each tractor tier resolves to its correct loot chance (gate wiring)."""
        async with _pg() as factory:
            svc = await _fresh_service(factory)
            assert svc.loot_service.loot_chance([_RETRACTOR]) == 20
            assert svc.loot_service.loot_chance(['AB-2 "Glue Gun"']) == 40
            assert svc.loot_service.loot_chance(['AB-3 "Kingfisher"']) == 60
            assert svc.loot_service.loot_chance(['AB-4 "Octopus"']) == 80
            assert svc.loot_service.loot_chance(["E2 Exoclad"]) == 0
