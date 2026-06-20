"""T10 — holistic end-to-end PvC-loot integration pass (LOOT_JOURNAL §10 T10).

This is the *capstone* suite: it exercises the whole loot pipeline through the
REAL shipped components (T1+T3+T4+T4b+T5+T6+T7+T9) against the seeded throwaway
Postgres, mocking ONLY what is required for determinism — the combat WINNER
outcome and the tractor success RNG.  Where a scenario already has a per-task
unit test, the T10 version is the INTEGRATED variant (real spawn-roll → real
``_apply_loot_on_win`` write → real inventory → real ``sell_item`` /
``transfer_item_between_players``), so a cross-task wiring gap that the per-task
tests cannot see is caught here.

What is REAL vs mocked
----------------------
* REAL: ``BountyService.spawn_bounty`` (rolls + persists the criminal cargo via
  the real ``LootService`` cache), ``BountyService._apply_loot_on_win`` (tractor
  gate → free-cargo gate → §5.4 clamp → real ``add_item_to_inventory`` write +
  own commit), ``ShopService.sell_item`` (commodity face-value sink),
  ``InventoryService.transfer_item_between_players`` (the ``/give`` backend),
  ``BountyService.check_bounty`` over-cap gate (T7), the T6 ``_loot_to_schema``
  wire mapping, and the persisted-cargo→pre-fight-visibility surface (T4b).
* MOCKED (determinism only): ``roll_loot_success`` (the tractor RNG) is stubbed
  per-scenario so loot success/failure is deterministic; the spawn-time
  criminal/route/loadout machinery (ARRAY tables) is stubbed at the repo
  boundary in the spawn-roll helper because it is orthogonal to loot.  The
  combat WIN itself is driven by calling ``_apply_loot_on_win`` directly with a
  win-shaped loadout — the T5 trigger suite already proves the win-branch
  *trigger* exclusion exhaustively, so T10 drives the post-win loot write with
  the cargo that a REAL spawn actually rolled and persisted.

Mock budget: <=2 mocks per test, real objects preferred.
"""

from __future__ import annotations

import os
import random
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest
from api.routers.bounties import _loot_to_schema
from persist.models.player import Player
from persist.models.player_inventory import PlayerInventory
from persist.models.player_ship import PlayerShip
from persist.models.user import User
from services.bounty_service import BountyService
from services.combat_models import ModuleStats, ShipLoadout
from services.game_constants import GameConstants
from services.inventory_service import InventoryService
from services.shop_service import ShopService
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# pg_env lives in tests/ (one level up).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from pg_env import PG_ASYNC_URL, pg_skip_reason

_PG_SKIP = pg_skip_reason()
pytestmark = pytest.mark.skipif(_PG_SKIP is not None, reason=_PG_SKIP or "")

# Isolation constants that cannot collide with production rows.
_TEST_GUILD = 999_888_321_010
_USER_A = 999_888_321_011
_USER_B = 999_888_321_012

# Seed-data anchors (verified present in the throwaway PG at head 0022).
_SHIP_NAME = "Betty"  # ship row, cargo = 25
_SHIP_CARGO = 25
_RETRACTOR = 'AB-1 "Retractor"'  # TractorBeamModule, TL4 → 20% chance
_OCTOPUS = 'AB-4 "Octopus"'  # TractorBeamModule, TL8 → 80% chance
_COMMODITY = "Vulpes Soup"  # a real booze commodity; value > 0 (T1 made it writable)
_WEAPON = "Micro Gun MK I"  # a real primary weapon (band-1 equippable)
_INV_TYPE_WEAPON = "primary_weapon"


# ---------------------------------------------------------------------------
# Postgres session + seed helpers (mirror the T5/T7 conventions)
# ---------------------------------------------------------------------------


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
        await db.execute(text(f"DELETE FROM users WHERE id IN ({_USER_A}, {_USER_B})"))


async def _seed_player(
    factory,
    *,
    user_id: int = _USER_A,
    equip: list[str] | None = None,
    cargo_load: int = 0,
    credits: int = 10_000,
    ship_name: str = _SHIP_NAME,
) -> int:
    """Seed a player on an active ship with optional equipped modules + cargo load."""
    async with factory() as db, db.begin():
        if await db.get(User, user_id) is None:
            db.add(User(id=user_id, discord_username=f"t10-{user_id}"))
            await db.flush()
        player = Player(user_id=user_id, guild_id=_TEST_GUILD, credits=credits, tier="Bronze", classic_mode=False)
        db.add(player)
        await db.flush()
        ship = PlayerShip(
            player_id=player.id,
            ship_name=ship_name,
            is_active=True,
            modules=list(equip or []),
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


async def _credits(factory, player_id: int) -> int:
    async with factory() as db:
        return (await db.execute(select(Player.credits).where(Player.id == player_id))).scalars().first()


async def _shop_rows_for(factory, item_name: str) -> int:
    """Count GuildShop rows for an item in the test guild (sink ⇒ must stay 0)."""
    async with factory() as db:
        return (
            await db.execute(
                text(f"SELECT count(*) FROM guild_shops WHERE guild_id = {_TEST_GUILD} AND item_name = :n"),
                {"n": item_name},
            )
        ).scalar() or 0


async def _fresh_service(factory) -> BountyService:
    """A real BountyService with a warm LootService cache (real preload from PG)."""
    svc = BountyService()
    async with factory() as db:
        await svc.loot_service.preload_static_data(db)
    return svc


def _loadout(*modules: str) -> ShipLoadout:
    return ShipLoadout(ship_name=_SHIP_NAME, base_armour=100, modules=[ModuleStats(name=m) for m in modules])


# ---------------------------------------------------------------------------
# Real spawn-roll helper: drives the REAL spawn_bounty cargo roll (T3+T4) so the
# cargo a criminal carries is a genuine catalog row, not a hand-built dict.  Only
# the ARRAY-table criminal/route/loadout machinery is stubbed (orthogonal to
# loot); the loot roll itself is the real LootService.roll_loot.
# ---------------------------------------------------------------------------


_LOADOUT_BLOB = {
    "ship_name": _SHIP_NAME,
    "ship_emoji": "",
    "ship_value": 12000,
    "ship_armour": 95,
    "armor_hp": 95,
    "shield_hp": 0,
    "total_hp": 95,
    "ship_max_primaries": 1,
    "ship_max_modules": 2,
    "ship_max_turrets": 0,
    "weapons": [],
    "turrets": [],
    "modules": [],
    "secondaries": [],
    "total_value": 12000,
}


def _spawn_roll_service(factory) -> BountyService:
    svc = BountyService()
    svc.criminal_repo = MagicMock()
    svc.criminal_repo.list_all = AsyncMock(
        return_value=[SimpleNamespace(name="Viper", faction="terran", is_player=False)]
    )
    svc.config_repo = MagicMock()
    svc.config_repo.get_by_guild_id = AsyncMock(return_value=None)
    svc.graph_service = MagicMock()
    svc.graph_service.load_graph = AsyncMock()
    svc.graph_service.get_systems_with_jump_gates = MagicMock(return_value=["A", "B", "C", "D"])
    svc.pathfinding_service = MagicMock()
    svc.pathfinding_service.make_route = MagicMock(return_value=["A", "B", "C"])

    async def _capture_create(_db, bounty, **_kw):
        bounty.id = 987654
        return bounty

    svc.bounty_repo = MagicMock()
    svc.bounty_repo.create = AsyncMock(side_effect=_capture_create)
    svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(return_value=[])
    return svc


async def _real_spawn_cargo(
    factory, *, division: str, tech_level: int, require_type: str | None = None, _max_seeds: int = 200
) -> dict:
    """Run the REAL spawn_bounty cargo roll and return the persisted cargo dict.

    Mocks only the criminal/route/loadout/persistence boundary (ARRAY deps the
    seeded PG bounty table must not be mutated by); the LootService + roll_loot
    are the real cached objects, so the carried item is a genuine catalog row.

    ``require_type`` (e.g. ``"commodity"``) deterministically pins the RNG seed —
    iterating real seeds until the REAL roll yields that concrete type — so a
    sink/sell scenario reliably gets a commodity without weakening the "real
    roll" guarantee (every result is still a genuine spawn-rolled catalog item).
    """
    svc = _spawn_roll_service(factory)
    # Capture the real Random class up-front so the patch factory below never
    # references the patched module-global (which would recurse infinitely).
    _RealRandom = random.Random
    seed = 0
    while seed < _max_seeds:
        with (
            patch.object(svc, "generate_loadout", new=AsyncMock(return_value=dict(_LOADOUT_BLOB))),
            patch("services.bounty_service.random.Random", new=(lambda s=seed: _RealRandom(s))),
        ):
            async with factory() as db:
                bounty = await svc.spawn_bounty(db, guild_id=_TEST_GUILD, division=division, tech_level=tech_level)
        cargo = bounty.criminal_ship["cargo"]
        if require_type is None or cargo["item_type"] == require_type:
            return cargo
        seed += 1
    raise AssertionError(f"no real spawn roll of type {require_type!r} found in {_max_seeds} seeds")


def _bounty_carrying(cargo: dict, bounty_id: int = 5555):
    """A minimal bounty object carrying a (real-rolled or explicit) cargo dict."""
    return SimpleNamespace(
        id=bounty_id,
        criminal_name="Viper",
        criminal_ship={"ship_name": _SHIP_NAME, "cargo": cargo},
    )


# ===========================================================================
# 1. FULL HAPPY PATH — real spawn-roll → real win-write → real sell + real give
# ===========================================================================


class TestHappyPathEndToEnd:
    async def test_spawn_win_inventory_sell_commodity_end_to_end(self) -> None:
        """spawn(real roll) → win-loot(real write) → real sell sink → credits up.

        Drives T3+T4 (the criminal's commodity cargo is a genuine catalog row from
        the real roll) → T5 (real ``_apply_loot_on_win`` writes it to inventory) →
        T1/§5.7 (real ``sell_item`` sinks it: credits up, units destroyed, NO shop
        stock).  Only the tractor RNG is mocked (forced success).
        """
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                # T3+T4: a REAL spawn roll at a Bronze TL that lands a band-3 booze
                # commodity (seed pinned so the scenario is deterministic + a sink).
                cargo = await _real_spawn_cargo(factory, division="bronze", tech_level=1, require_type="commodity")
                # Sanity on the real roll: it is a genuine carried item.
                assert cargo["quantity"] >= 1 and isinstance(cargo["item_name"], str)

                pid = await _seed_player(factory, equip=[_OCTOPUS], cargo_load=0)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)  # 1 mock: tractor RNG

                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_carrying(cargo),
                        player_loadout=_loadout(_OCTOPUS),
                        cfg=None,
                    )

                # T5: full haul written to inventory under the concrete item_type.
                assert outcome.outcome == "looted"
                assert outcome.qty_looted == cargo["quantity"]
                looted_name = cargo["item_name"]
                assert await _inv_qty(factory, pid, looted_name) == cargo["quantity"]

                # T6 wire mapping: the internal outcome maps to a renderable LootResult.
                wire = _loot_to_schema(outcome)
                assert wire is not None and wire.outcome == "looted"
                assert wire.item_name == looted_name and wire.qty_looted == cargo["quantity"]

                # T1/§5.7: SELL the looted commodity as a real face-value sink.
                shop = ShopService()
                credits_before = await _credits(factory, pid)
                async with factory() as db, db.begin():
                    sell = await shop.sell_item(db, pid, looted_name, quantity=cargo["quantity"])

                # Commodity branch: sunk (no shop stock), credited at face value.
                assert sell["sunk"] is True
                assert sell["target_shop_tier"] is None
                # Units destroyed → cargo row gone.
                assert await _inv_qty(factory, pid, looted_name) == 0
                # Credits up by exactly Item.value × qty × LOOT_COMMODITY_SELL_FRACTION.
                async with factory() as db:
                    item_value = (
                        await db.execute(text("SELECT value FROM item WHERE name = :n"), {"n": looted_name})
                    ).scalar()
                expected_gain = int(item_value * GameConstants.LOOT_COMMODITY_SELL_FRACTION * cargo["quantity"])
                assert sell["total_sell_value"] == expected_gain
                assert await _credits(factory, pid) == credits_before + expected_gain
                # The sink NEVER stocked a GuildShop (commodities can't be bought).
                assert await _shop_rows_for(factory, looted_name) == 0
            finally:
                await _cleanup(factory)

    async def test_spawn_win_then_give_looted_commodity_qty(self) -> None:
        """A looted commodity can be /give-n with quantity (T9 + T1 Literal).

        Real roll → real loot write → real ``transfer_item_between_players`` moves
        a quantity of the looted commodity to a second player (cargo decremented on
        the giver, incremented on the receiver — no minting).
        """
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                cargo = await _real_spawn_cargo(factory, division="bronze", tech_level=1, require_type="commodity")
                qty = cargo["quantity"]
                name = cargo["item_name"]
                # Ensure the receiver has room: give half, keep half.
                give_n = max(1, qty // 2)

                giver = await _seed_player(factory, user_id=_USER_A, equip=[_OCTOPUS], cargo_load=0)
                receiver = await _seed_player(factory, user_id=_USER_B, cargo_load=0)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)

                async with factory() as db:
                    player = await db.get(Player, giver)
                    out = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=giver,
                        bounty=_bounty_carrying(cargo),
                        player_loadout=_loadout(_OCTOPUS),
                        cfg=None,
                    )
                assert out.outcome == "looted"

                # T9/T1: give a quantity of the looted commodity to the receiver.
                inv = InventoryService()
                async with factory() as db, db.begin():
                    await inv.transfer_item_between_players(db, giver, receiver, "commodity", name, quantity=give_n)

                assert await _inv_qty(factory, giver, name) == qty - give_n
                assert await _inv_qty(factory, receiver, name) == give_n
            finally:
                await _cleanup(factory)


# ===========================================================================
# 2. CLAMP (partial) — free < N → exactly `free` looted, overflow lost
# ===========================================================================


class TestClampPartial:
    async def test_partial_clamp_overflow_lost(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                # Load 22/25 → free = 3.  Criminal carries 14 → take 3, lose 11.
                pid = await _seed_player(factory, equip=[_OCTOPUS], cargo_load=22)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)
                cargo = {"item_type": "commodity", "item_name": _COMMODITY, "quantity": 14}
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_carrying(cargo),
                        player_loadout=_loadout(_OCTOPUS),
                        cfg=None,
                    )
                assert outcome.outcome == "partial"
                assert outcome.qty_looted == 3 and outcome.qty_total == 14
                # Inventory reflects the clamp; the 11 overflow is "lost in space".
                assert await _inv_qty(factory, pid, _COMMODITY) == 3
                # Player now exactly at cap (22 + 3 = 25), not over.
                async with factory() as db:
                    player = await db.get(Player, pid)
                    from services.cargo_utils import compute_free_cargo, is_over_cap

                    _free, load, cap = await compute_free_cargo(db, svc.inventory_repo, player)
                assert load == _SHIP_CARGO and cap == _SHIP_CARGO
                assert not is_over_cap(load, cap)  # clamp never pushes over cap (§5.4/§5.5)
            finally:
                await _cleanup(factory)


# ===========================================================================
# 3. cargo_full — exactly at cap → outcome cargo_full, zero loot
# ===========================================================================


class TestCargoFull:
    async def test_at_cap_no_loot_written(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player(factory, equip=[_OCTOPUS], cargo_load=_SHIP_CARGO)  # 25/25 full
                svc = await _fresh_service(factory)
                # Stub success TRUE so a leaked roll would write — proving the skip.
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)
                cargo = {"item_type": "commodity", "item_name": _COMMODITY, "quantity": 8}
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_carrying(cargo),
                        player_loadout=_loadout(_OCTOPUS),
                        cfg=None,
                    )
                assert outcome.outcome == "cargo_full"
                assert outcome.cargo_current == _SHIP_CARGO and outcome.cargo_max == _SHIP_CARGO
                svc.loot_service.roll_loot_success.assert_not_called()  # roll skipped (M-1)
                assert await _inv_qty(factory, pid, _COMMODITY) == 0
            finally:
                await _cleanup(factory)


# ===========================================================================
# 4. NO TRACTOR — player without a beam wins → outcome none, no loot field
# ===========================================================================


class TestNoTractor:
    async def test_no_beam_outcome_none_and_field_omitted(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player(factory, equip=["E2 Exoclad"], cargo_load=0)  # no beam
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)
                cargo = {"item_type": "commodity", "item_name": _COMMODITY, "quantity": 8}
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_carrying(cargo),
                        player_loadout=_loadout("E2 Exoclad"),
                        cfg=None,
                    )
                assert outcome.outcome == "none"
                svc.loot_service.roll_loot_success.assert_not_called()  # chance 0 ⇒ never rolled
                assert await _inv_qty(factory, pid, _COMMODITY) == 0
                # T6/§5.9 omission: a "none" outcome maps to NO wire Loot field.
                assert _loot_to_schema(outcome) is None
            finally:
                await _cleanup(factory)


# ===========================================================================
# 5. OVER-CAP LOCKOUT — over-cap player blocked at /check, NOTHING resolves
#    (real check_bounty gate, T7) — integrated here as the T10 capstone variant.
# ===========================================================================


class TestOverCapLockout:
    async def test_over_cap_check_blocks_no_resolution(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player(factory, cargo_load=_SHIP_CARGO + 4)  # 29 > 25
                svc = BountyService()
                async with factory() as db:
                    result = await svc.check_bounty(db, pid, system_name="Nowhere", guild_id=_TEST_GUILD)
                from services.bounty_service import CheckResult

                assert len(result.outcomes) == 1
                o = result.outcomes[0]
                assert o.result is CheckResult.OVER_CAP
                assert o.cargo_current == _SHIP_CARGO + 4 and o.cargo_max == _SHIP_CARGO
                # Nothing resolved: no bounty, no reward, no loot.
                assert o.bounty_id is None and o.reward is None and o.loot is None
            finally:
                await _cleanup(factory)


# ===========================================================================
# 6. MULTI-BOUNTY — independent loot results; one filling cargo does not corrupt
#    another.  Drives the real per-bounty loot write twice on the SAME player,
#    proving each carries its OWN result and the second sees the updated free
#    cargo (no cross-contamination of the persisted cargo blobs).
# ===========================================================================


class TestMultiBounty:
    async def test_two_bounties_independent_loot_one_fills_cargo(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                # Free = 25.  Bounty A carries 20 booze, Bounty B carries 12 ore.
                # A loots 20 (free now 5); B clamps to 5 (partial), distinct results.
                pid = await _seed_player(factory, equip=[_OCTOPUS], cargo_load=0)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)
                cargo_a = {"item_type": "commodity", "item_name": _COMMODITY, "quantity": 20}
                cargo_b = {"item_type": "commodity", "item_name": "Iron", "quantity": 12}

                async with factory() as db:
                    player = await db.get(Player, pid)
                    out_a = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_carrying(cargo_a, bounty_id=1),
                        player_loadout=_loadout(_OCTOPUS),
                        cfg=None,
                    )
                async with factory() as db:
                    player = await db.get(Player, pid)
                    out_b = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_carrying(cargo_b, bounty_id=2),
                        player_loadout=_loadout(_OCTOPUS),
                        cfg=None,
                    )

                # Each result is its OWN, correct outcome.
                assert out_a.outcome == "looted" and out_a.qty_looted == 20
                assert out_a.item_name == _COMMODITY
                assert out_b.outcome == "partial" and out_b.qty_looted == 5 and out_b.qty_total == 12
                assert out_b.item_name == "Iron"
                # Inventory holds both stacks at their clamped quantities — no corruption.
                assert await _inv_qty(factory, pid, _COMMODITY) == 20
                assert await _inv_qty(factory, pid, "Iron") == 5
                # Total load is exactly at cap, never over.
                async with factory() as db:
                    player = await db.get(Player, pid)
                    from services.cargo_utils import compute_free_cargo

                    _free, load, cap = await compute_free_cargo(db, svc.inventory_repo, player)
                assert load == _SHIP_CARGO == cap
            finally:
                await _cleanup(factory)


# ===========================================================================
# 7. NO-SHIP DEFENSIVE BRANCH — no crash, no loot (cargo guard / absent cargo)
#    Drives _apply_loot_on_win with malformed/absent cargo: must degrade to
#    "none" with no write and no raise (the defensive crash-/loot-safe guard).
# ===========================================================================


class TestNoShipDefensive:
    async def test_absent_cargo_outcome_none_no_crash(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player(factory, equip=[_OCTOPUS], cargo_load=0)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)
                # Criminal_ship with NO cargo key (the no-ship/no-roll shape).
                bounty = SimpleNamespace(id=7, criminal_name="V", criminal_ship={"ship_name": _SHIP_NAME})
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(  # must NOT raise
                        db, player=player, player_id=pid, bounty=bounty, player_loadout=_loadout(_OCTOPUS), cfg=None
                    )
                assert outcome.outcome == "none"
                svc.loot_service.roll_loot_success.assert_not_called()
                assert await _inv_qty(factory, pid, _COMMODITY) == 0
            finally:
                await _cleanup(factory)

    async def test_malformed_cargo_outcome_none(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player(factory, equip=[_OCTOPUS], cargo_load=0)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=True)
                # quantity 0 + missing item_name → malformed → none, no write, no raise.
                bounty = _bounty_carrying({"item_type": "commodity", "item_name": "", "quantity": 0})
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(
                        db, player=player, player_id=pid, bounty=bounty, player_loadout=_loadout(_OCTOPUS), cfg=None
                    )
                assert outcome.outcome == "none"
            finally:
                await _cleanup(factory)


# ===========================================================================
# 8. NO LOOT ON NON-WIN — the loot write is NEVER invoked on a miss; and a
#    forced tractor MISS yields "failed" with no inventory write (the in-routine
#    proof that a non-success path writes nothing).  The win-branch *trigger*
#    exclusion (loss/stalemate never reach the hook) is proven exhaustively in
#    test_t5_loot_win_hook.py::TestWinBranchTrigger; T10 corroborates the write
#    side: even when the hook IS reached, a miss writes nothing.
# ===========================================================================


class TestNoLootOnMiss:
    async def test_tractor_miss_failed_no_write(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                pid = await _seed_player(factory, equip=[_RETRACTOR], cargo_load=0)
                svc = await _fresh_service(factory)
                svc.loot_service.roll_loot_success = MagicMock(return_value=False)  # forced RNG miss
                cargo = {"item_type": "commodity", "item_name": _COMMODITY, "quantity": 8}
                async with factory() as db:
                    player = await db.get(Player, pid)
                    outcome = await svc._apply_loot_on_win(
                        db,
                        player=player,
                        player_id=pid,
                        bounty=_bounty_carrying(cargo),
                        player_loadout=_loadout(_RETRACTOR),
                        cfg=None,
                    )
                assert outcome.outcome == "failed"
                assert outcome.qty_looted == 0 and outcome.qty_total == 8
                assert await _inv_qty(factory, pid, _COMMODITY) == 0
            finally:
                await _cleanup(factory)


# ===========================================================================
# 9. PRE-FIGHT CARGO VISIBILITY (T4b) — the criminal's persisted cargo surfaces
#    for a freshly spawned bounty (the real roll persists item_name + quantity in
#    criminal_ship['cargo'], which is what the bounty read/announcement embed
#    renders before the fight).
# ===========================================================================


class TestPreFightVisibility:
    async def test_spawned_cargo_is_advertised_shape(self) -> None:
        async with _pg() as factory:
            await _cleanup(factory)
            try:
                cargo = await _real_spawn_cargo(factory, division="silver", tech_level=3)
                # The advertised contract: a real item_name + quantity >= 1 + a
                # concrete item_type — exactly what T4b renders as "Nx <Item>".
                assert isinstance(cargo["item_name"], str) and cargo["item_name"]
                assert cargo["quantity"] >= 1
                assert cargo["item_type"] in (
                    "commodity",
                    "primary_weapon",
                    "secondary_weapon",
                    "turret_weapon",
                    "module",
                )
            finally:
                await _cleanup(factory)
