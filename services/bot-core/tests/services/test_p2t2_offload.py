"""P2-T2 — fight_ships offload tests.

Covers:
  - fight_ships with log_result=True returns a full FightResults AND persists
    a combat_log whose serialised timeline is byte-identical to a direct
    TickResolver call with the same seed.
  - fight_ships with log_result=False returns a full FightResults (not a tuple),
    with .is_stalemate / .winner_name / .winner_side populated.
  - C1a-4: passing a live ORM-model guild_config to fight_ships is rejected
    before the worker boundary.

Max 2 mocks per test.
"""

from __future__ import annotations

import dataclasses
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Shared bblogger + sqlalchemy_utils guard (mirrors test_combat_cutover.py)
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

import pytest
from services.combat_models import ShipLoadout, WeaponStats
from services.combat_service import CombatService, _is_orm_model

# ---------------------------------------------------------------------------
# Shared loadout helper
# ---------------------------------------------------------------------------


def _make_loadouts(seed: int = 42):
    """Return a deterministic (l1, l2) pair for parity testing.

    C1 has a weapon strong enough to kill C2 quickly; C2 has no weapon.
    This guarantees a non-stalemate decisive outcome.
    """
    l1 = ShipLoadout(
        ship_name="Attacker",
        base_armour=500,
        weapons=[WeaponStats(name="Pulse", dps=0.0, damage_per_shot=9999, loading_speed_ms=100, range_m=999999.0)],
    )
    l2 = ShipLoadout(ship_name="Defender", base_armour=10)
    return l1, l2


# ---------------------------------------------------------------------------
# Test: log_result=False returns a full FightResults
# ---------------------------------------------------------------------------


class TestLogResultFalse:
    """fight_ships(log_result=False) returns a full FightResults."""

    @pytest.mark.asyncio
    async def test_returns_fight_results_not_tuple(self):
        """Return value has FightResults shape (not a tuple or dict)."""
        service = CombatService()
        l1, l2 = _make_loadouts()
        result = await service.fight_ships(l1, l2, log_result=False)
        # Check shape rather than isinstance to avoid src./service. module-path discrepancy
        assert hasattr(result, "winner_name"), f"Expected FightResults shape, got {type(result)}"
        assert hasattr(result, "is_stalemate"), f"Expected FightResults shape, got {type(result)}"
        assert hasattr(result, "combat_log"), f"Expected FightResults shape, got {type(result)}"

    @pytest.mark.asyncio
    async def test_winner_side_populated(self):
        """winner_side is 1 when C1 wins (decisive fight)."""
        service = CombatService()
        l1, l2 = _make_loadouts()
        result = await service.fight_ships(l1, l2, log_result=False)
        assert not result.is_stalemate, "Expected decisive fight with this loadout"
        assert result.winner_side == 1, f"Expected winner_side=1, got {result.winner_side}"

    @pytest.mark.asyncio
    async def test_winner_name_populated(self):
        """winner_name matches l1.ship_name when C1 wins."""
        service = CombatService()
        l1, l2 = _make_loadouts()
        result = await service.fight_ships(l1, l2, log_result=False)
        assert result.winner_name == l1.ship_name, f"Expected winner_name={l1.ship_name!r}, got {result.winner_name!r}"

    @pytest.mark.asyncio
    async def test_is_stalemate_false_for_decisive_fight(self):
        """is_stalemate=False for a decisive fight."""
        service = CombatService()
        l1, l2 = _make_loadouts()
        result = await service.fight_ships(l1, l2, log_result=False)
        assert result.is_stalemate is False

    @pytest.mark.asyncio
    async def test_combat_log_id_is_none(self):
        """combat_log_id is None (no DB writes on log_result=False)."""
        service = CombatService()
        l1, l2 = _make_loadouts()
        result = await service.fight_ships(l1, l2, log_result=False)
        assert result.combat_log_id is None

    @pytest.mark.asyncio
    async def test_combat_log_has_events(self):
        """combat_log is non-empty (timeline is present)."""
        service = CombatService()
        l1, l2 = _make_loadouts()
        result = await service.fight_ships(l1, l2, log_result=False)
        assert len(result.combat_log) > 0, "Expected non-empty combat_log timeline"


# ---------------------------------------------------------------------------
# Test: log_result=True returns a full FightResults + persists byte-identical
# ---------------------------------------------------------------------------


class TestLogResultTrue:
    """fight_ships(log_result=True) returns a full FightResults with combat_log_id
    and the persisted timeline is byte-identical to a direct TickResolver call.
    """

    @pytest.mark.asyncio
    async def test_returns_fight_results_with_combat_log_id(self):
        """log_result=True returns a FightResults with combat_log_id populated."""
        service = CombatService()
        l1, l2 = _make_loadouts()
        session_mock = AsyncMock()

        with (
            patch("services.combat_log_service.CombatLogService.persist", new=AsyncMock(return_value=77)),
            patch(
                "persist.repositories.player_repository.PlayerRepository.get_by_user_and_guild",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await service.fight_ships(
                l1,
                l2,
                context="duel",
                log_result=True,
                session=session_mock,
                guild_id=1,
                combatant1_user_id=100,
                combatant2_user_id=None,
            )

        assert hasattr(result, "combat_log_id"), "Expected FightResults shape"
        assert result.combat_log_id == 77

    @pytest.mark.asyncio
    async def test_winner_side_carried_through_log_result_true(self):
        """winner_side is carried through the log_result=True rebuild (frozen dataclass)."""
        service = CombatService()
        l1, l2 = _make_loadouts()
        session_mock = AsyncMock()

        with (
            patch("services.combat_log_service.CombatLogService.persist", new=AsyncMock(return_value=88)),
            patch(
                "persist.repositories.player_repository.PlayerRepository.get_by_user_and_guild",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await service.fight_ships(
                l1,
                l2,
                context="duel",
                log_result=True,
                session=session_mock,
                guild_id=1,
            )

        assert result.winner_side is not None or result.is_stalemate, (
            "winner_side must be populated (1 or 2) on a decisive fight"
        )

    @pytest.mark.asyncio
    async def test_persisted_timeline_is_byte_identical_to_direct_resolve(self):
        """Timeline passed to CombatLogService.persist is byte-identical to a direct run_fight call.

        Strategy: call run_fight(seed=42) directly to get the canonical list[dict]
        timeline, then call fight_ships — with run_fight patched to also use seed=42 —
        and compare the offload list[dict] directly against the canonical timeline.

        P2-T6: fight_results.combat_log is now list[dict] (no re-hydration into
        CombatEvent objects).  Byte-identity is proved by direct dict == dict comparison.

        The seed IS pinned to 42 on BOTH sides of the comparison; this is what
        guarantees byte-identical RNG streams.  The weapon setup (armour=10,
        damage_per_shot=9999, no evasion) ensures the fight completes in one hit
        regardless of seed, but the seed still governs per-tick RNG draws inside
        TickResolver — so a seed mismatch would produce different event sequences.
        Both sides must use FIXED_SEED=42 to guarantee identical output.
        """
        from compute.combat_worker import run_fight as real_run_fight

        FIXED_SEED = 42
        l1, l2 = _make_loadouts()

        # Step 1: get the canonical serialised timeline from run_fight(seed=42) directly
        canonical_raw = real_run_fight(
            l1,
            l2,
            pvc_damage_reduction=0.0,
            seed=FIXED_SEED,
            combatant1_label="",
            combatant2_label="",
            compact=False,
        )
        canonical_timeline: list[dict] = canonical_raw["timeline"]
        assert len(canonical_timeline) > 0, "Expected non-empty canonical timeline"

        # Step 2: run fight_ships via offload_cpu (using the patched seeded wrapper)
        # Patch at the compute.combat_worker module so offload_cpu captures the seeded version.
        captured_fight_results: list = []
        session_mock = AsyncMock()

        def _seeded_run_fight(
            loadout1, loadout2, *, pvc_damage_reduction, seed, combatant1_label, combatant2_label, compact
        ):
            return real_run_fight(
                loadout1,
                loadout2,
                pvc_damage_reduction=pvc_damage_reduction,
                seed=FIXED_SEED,  # override for parity
                combatant1_label=combatant1_label,
                combatant2_label=combatant2_label,
                compact=compact,
            )

        async def _capture_persist(meta, fight_results, context, session):
            captured_fight_results.append(fight_results)
            return 99

        service = CombatService()

        # fight_ships is defined in the module that CombatService was imported from
        # (services.combat_service), so we must patch run_fight in that same module.
        # Use fight_ships.__globals__ to get the exact module dict and patch run_fight in it.
        cs_globals = service.fight_ships.__func__.__globals__
        orig_run_fight = cs_globals["run_fight"]
        cs_globals["run_fight"] = _seeded_run_fight
        try:
            with (
                patch(
                    "services.combat_log_service.CombatLogService.persist",
                    new=AsyncMock(side_effect=_capture_persist),
                ),
                patch(
                    "persist.repositories.player_repository.PlayerRepository.get_by_user_and_guild",
                    new=AsyncMock(return_value=None),
                ),
            ):
                await service.fight_ships(
                    l1,
                    l2,
                    context="duel",
                    log_result=True,
                    session=session_mock,
                    guild_id=1,
                    combatant1_user_id=100,
                    combatant2_user_id=None,
                )
        finally:
            cs_globals["run_fight"] = orig_run_fight

        assert len(captured_fight_results) == 1
        fight_results = captured_fight_results[0]

        # P2-T6: fight_results.combat_log is now list[dict] (no re-hydration).
        assert len(fight_results.combat_log) > 0
        for ev in fight_results.combat_log:
            assert isinstance(ev, dict), f"Expected dict (P2-T6 passthrough), got {type(ev)}: {ev!r}"
            assert set(ev.keys()) == {"tick", "type", "actor", "target", "data"}, f"Unexpected event shape: {ev.keys()}"

        # Byte-identical parity: the offload list[dict] == the canonical raw dict (no conversion needed)
        assert len(fight_results.combat_log) == len(canonical_timeline), (
            f"Timeline length mismatch: offload={len(fight_results.combat_log)}, canonical={len(canonical_timeline)}"
        )

        for i, (offload_d, canonical_d) in enumerate(zip(fight_results.combat_log, canonical_timeline, strict=True)):
            assert offload_d == canonical_d, (
                f"Event [{i}] mismatch:\n  offload (dict): {offload_d}\n  canonical (raw): {canonical_d}"
            )


# ---------------------------------------------------------------------------
# Test: C1a-4 — ORM guard fires before the worker boundary
# ---------------------------------------------------------------------------


class TestOrmGuard:
    """C1a-4: fight_ships rejects a live ORM model as guild_config."""

    def test_is_orm_model_returns_true_for_mapper_class(self):
        """_is_orm_model returns True when the object's class has __mapper__."""
        fake_orm = MagicMock()
        type(fake_orm).__mapper__ = MagicMock()  # simulate SQLAlchemy mapped class
        assert _is_orm_model(fake_orm) is True

    def test_is_orm_model_returns_false_for_none(self):
        """_is_orm_model(None) returns False — None is always safe to pass."""
        assert _is_orm_model(None) is False

    def test_is_orm_model_returns_false_for_plain_dict(self):
        """_is_orm_model returns False for a plain dict."""
        assert _is_orm_model({"key": "value"}) is False

    def test_is_orm_model_returns_false_for_int(self):
        """_is_orm_model returns False for a plain scalar."""
        assert _is_orm_model(42) is False

    @pytest.mark.asyncio
    async def test_orm_guild_config_raises_assertion_error(self):
        """fight_ships raises AssertionError when guild_config is an ORM model (C1a-4)."""
        service = CombatService()
        l1, l2 = _make_loadouts()

        # Simulate a live GuildConfig ORM row — has __mapper__ on the class
        fake_orm_config = MagicMock()
        type(fake_orm_config).__mapper__ = MagicMock()

        with pytest.raises(AssertionError, match="guild_config must not be a live ORM model"):
            await service.fight_ships(l1, l2, log_result=False, guild_config=fake_orm_config)

    @pytest.mark.asyncio
    async def test_none_guild_config_is_accepted(self):
        """guild_config=None (the default) passes the ORM guard cleanly."""
        service = CombatService()
        l1, l2 = _make_loadouts()
        # Should not raise
        result = await service.fight_ships(l1, l2, log_result=False, guild_config=None)
        assert result is not None

    def test_is_orm_model_returns_true_for_real_orm_instance(self):
        """_is_orm_model returns True for a REAL SQLAlchemy-mapped class instance.

        Imports an actual ORM model (GuildConfig) so that __mapper__ is set by the
        SQLAlchemy mapper registry at class-definition time — not a MagicMock stub.
        This proves _is_orm_model detects real ORM rows, not just mock objects.
        """
        from persist.models.guild_config import GuildConfig

        # Verify that the real class has a __mapper__ (set by SQLAlchemy at decoration time).
        assert hasattr(GuildConfig, "__mapper__"), (
            "GuildConfig must have __mapper__; if missing, SQLAlchemy mapping failed"
        )
        # Construct a bare instance (no DB required — just allocate the object).
        instance = GuildConfig.__new__(GuildConfig)
        assert _is_orm_model(instance) is True, "_is_orm_model must return True for a real SQLAlchemy-mapped instance"

    def test_is_orm_model_returns_false_for_non_orm_objects(self):
        """_is_orm_model returns False for None, plain dict, int, str, and list.

        Ensures there are no false-positive ORM detections for common scalar/container types.
        """
        for obj in [None, {"key": "value"}, 0, "string", [], 3.14]:
            assert _is_orm_model(obj) is False, f"_is_orm_model must return False for {type(obj).__name__!r} ({obj!r})"


# ---------------------------------------------------------------------------
# Test: full FightResults reconstruction completeness via offload path
# ---------------------------------------------------------------------------


class TestFightResultsReconstructionCompleteness:
    """Verifies that fight_ships (offload path) reconstructs a COMPLETE FightResults
    — not just the timeline — identical to a direct TickResolver(seed=S).resolve(...) call.

    This is the central correctness risk for P2-T2: if any FightResults field is
    silently dropped or defaulted during reconstruction from the worker's plain-dict
    response, downstream consumers (persist, stat increments, duel decode) would receive
    incorrect data without any test catching it.
    """

    @pytest.mark.asyncio
    async def test_full_fight_results_parity_with_direct_resolve(self):
        """fight_ships (offload path, seed=42) produces a FightResults that is
        field-for-field equal to TickResolver(seed=42).resolve(...) directly.

        Compares ALL FightResults fields:
          winner_name, loser_name, winner_side, is_stalemate,
          ship1_stats (all FightStats fields), ship2_stats (all FightStats fields),
          timeline (via dataclasses.asdict), metadata, and combat_log_id (None).
        """

        from services.combat_resolver import TickResolver

        FIXED_SEED = 42
        l1, l2 = _make_loadouts()

        # --- Baseline: direct TickResolver call (in-process, no offload) ---
        baseline = TickResolver(seed=FIXED_SEED).resolve(l1, l2)

        # --- Offload path: fight_ships with run_fight patched to use FIXED_SEED ---
        from compute.combat_worker import run_fight as real_run_fight

        def _seeded_run_fight(lo1, lo2, *, pvc_damage_reduction, seed, combatant1_label, combatant2_label, compact):
            return real_run_fight(
                lo1,
                lo2,
                pvc_damage_reduction=pvc_damage_reduction,
                seed=FIXED_SEED,
                combatant1_label=combatant1_label,
                combatant2_label=combatant2_label,
                compact=compact,
            )

        service = CombatService()
        cs_globals = service.fight_ships.__func__.__globals__
        orig_run_fight = cs_globals["run_fight"]
        cs_globals["run_fight"] = _seeded_run_fight
        try:
            offload_result = await service.fight_ships(l1, l2, log_result=False)
        finally:
            cs_globals["run_fight"] = orig_run_fight

        # --- Compare every FightResults field ---

        # Scalar identity fields
        assert offload_result.winner_name == baseline.winner_name, (
            f"winner_name mismatch: offload={offload_result.winner_name!r} baseline={baseline.winner_name!r}"
        )
        assert offload_result.loser_name == baseline.loser_name, (
            f"loser_name mismatch: offload={offload_result.loser_name!r} baseline={baseline.loser_name!r}"
        )
        assert offload_result.winner_side == baseline.winner_side, (
            f"winner_side mismatch: offload={offload_result.winner_side!r} baseline={baseline.winner_side!r}"
        )
        assert offload_result.is_stalemate == baseline.is_stalemate, (
            f"is_stalemate mismatch: offload={offload_result.is_stalemate!r} baseline={baseline.is_stalemate!r}"
        )
        # log_result=False → no DB write → combat_log_id must be None
        assert offload_result.combat_log_id is None, (
            f"combat_log_id must be None on log_result=False, got {offload_result.combat_log_id!r}"
        )

        # FightStats — all six fields for both ships
        def _assert_stats_equal(label: str, got, want) -> None:
            for field_name in ("ship_name", "raw_hp", "raw_dps", "varied_hp", "varied_dps", "ttk"):
                got_val = getattr(got, field_name)
                want_val = getattr(want, field_name)
                assert got_val == want_val, f"{label}.{field_name} mismatch: offload={got_val!r} baseline={want_val!r}"

        _assert_stats_equal("ship1_stats", offload_result.ship1_stats, baseline.ship1_stats)
        _assert_stats_equal("ship2_stats", offload_result.ship2_stats, baseline.ship2_stats)

        # Timeline: length and per-event dict equality.
        # P2-T6: offload path produces list[dict]; baseline (in-process TickResolver) produces
        # list[CombatEvent].  Compare by converting the baseline via asdict.
        assert len(offload_result.combat_log) == len(baseline.combat_log), (
            f"timeline length mismatch: offload={len(offload_result.combat_log)} baseline={len(baseline.combat_log)}"
        )
        for i, (offload_d, baseline_ev) in enumerate(zip(offload_result.combat_log, baseline.combat_log, strict=True)):
            assert isinstance(offload_d, dict), f"Event [{i}]: expected dict (P2-T6), got {type(offload_d)}"
            baseline_d = dataclasses.asdict(baseline_ev)
            assert offload_d == baseline_d, f"Event [{i}] mismatch:\n  offload : {offload_d}\n  baseline: {baseline_d}"

        # Metadata: full value equality (not just key presence) — catches any
        # metadata-scrambling bug where keys are present but values differ.
        assert offload_result.metadata == baseline.metadata, (
            f"metadata value mismatch:\n  offload : {offload_result.metadata}\n  baseline: {baseline.metadata}"
        )


# ---------------------------------------------------------------------------
# Test: stalemate via the offload path
# ---------------------------------------------------------------------------


class TestStalemateViaOffload:
    """fight_ships(log_result=False) with two unarmed equal-armour ships produces a stalemate."""

    @pytest.mark.asyncio
    async def test_stalemate_winner_side_is_none(self):
        """Offload path stalemate: winner_side is None."""
        service = CombatService()
        # Two ships with no weapons and identical armour — neither can kill the other.
        l1 = ShipLoadout(ship_name="Ship1", base_armour=100)
        l2 = ShipLoadout(ship_name="Ship2", base_armour=100)
        result = await service.fight_ships(l1, l2, log_result=False)
        assert result.is_stalemate is True, f"Expected stalemate, got winner_name={result.winner_name!r}"
        assert result.winner_side is None, f"Expected winner_side=None on stalemate, got {result.winner_side!r}"

    @pytest.mark.asyncio
    async def test_stalemate_winner_name_is_none(self):
        """Offload path stalemate: winner_name and loser_name are None."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="Ship1", base_armour=100)
        l2 = ShipLoadout(ship_name="Ship2", base_armour=100)
        result = await service.fight_ships(l1, l2, log_result=False)
        assert result.winner_name is None, f"Expected winner_name=None on stalemate, got {result.winner_name!r}"
        assert result.loser_name is None, f"Expected loser_name=None on stalemate, got {result.loser_name!r}"

    @pytest.mark.asyncio
    async def test_stalemate_combat_log_id_is_none(self):
        """Offload path stalemate with log_result=False: combat_log_id is None."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="Ship1", base_armour=100)
        l2 = ShipLoadout(ship_name="Ship2", base_armour=100)
        result = await service.fight_ships(l1, l2, log_result=False)
        assert result.combat_log_id is None
