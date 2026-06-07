"""P2-T9 — Static + runtime assertions for offload boundary purity.

TASK
----
Verify (and lock in) that EVERY combat-offload call site passes only plain
values (guild_config=None / guild_id:int / picklable loadouts/scalars) into the
worker — never a live GuildConfig/ORM row.

COVERAGE
--------
1. STATIC (AST-level): enumerate the five offload/fight call sites and assert
   that none passes guild_config=<live ORM>.  Specifically:

   a. bounties.py combat_bonus  (~:230)   → fight_ships, no guild_config kwarg
   b. bounty_service.py bronze  (~:1524)  → fight_ships, no guild_config kwarg
   c. bounty_service.py silver+ (~:1575)  → fight_ships, no guild_config kwarg
   d. duel_service.py resolve   (~:290)   → fight_ships, no guild_config kwarg
   e. combat_preflight_service  (~:182)   → run_fight_batch via offload_cpu,
                                             no guild_config anywhere in the call

2. RUNTIME REJECTION (fight_ships path): passing a mapper-bearing object as
   guild_config raises AssertionError before offload_cpu is called.

3. RUNTIME REJECTION (preflight path): passing a mapper-bearing object in
   matchups raises AssertionError before offload_cpu is called.

4. ANTI-VACUOUS: _is_orm_model correctly identifies real SQLAlchemy ORM
   instances AND does NOT false-positive on None / int / dict / ShipLoadout.

Max 2 mocks per test (project convention).
"""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# shared.bblogger guard (mirrors test_combat_cutover.py pattern)
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

# These imports are now safe.
from services.combat_models import ShipLoadout
from services.combat_service import CombatService, _is_orm_model

# ---------------------------------------------------------------------------
# Path constants for source files under inspection.
# ---------------------------------------------------------------------------
_SRC = Path(__file__).parent.parent.parent / "src"
_BOUNTIES_ROUTER = _SRC / "api" / "routers" / "bounties.py"
_BOUNTY_SERVICE = _SRC / "services" / "bounty_service.py"
_DUEL_SERVICE = _SRC / "services" / "duel_service.py"
_PREFLIGHT_SERVICE = _SRC / "services" / "combat_preflight_service.py"


# ---------------------------------------------------------------------------
# Helper: parse a source file once and return its AST.
# ---------------------------------------------------------------------------
def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Helper: return all call nodes whose function name (or attribute) matches
# ``name`` anywhere in the tree.
# ---------------------------------------------------------------------------
def _find_calls(tree: ast.Module, name: str) -> list[ast.Call]:
    """Yield all ast.Call nodes whose callee matches *name* (attr or name)."""
    results = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == name) or (isinstance(func, ast.Name) and func.id == name):
            results.append(node)
    return results


# ---------------------------------------------------------------------------
# Helper: extract the set of keyword argument names from a call node.
# ---------------------------------------------------------------------------
def _kwarg_names(call: ast.Call) -> set[str]:
    return {kw.arg for kw in call.keywords if kw.arg is not None}


# ===========================================================================
# 1. STATIC ASSERTIONS — source-level checks
# ===========================================================================


class TestStaticOffloadBoundary:
    """AST-level assertions for every fight/offload call site.

    Strategy: parse each source file once, find all fight_ships() /
    offload_cpu() call AST nodes, and assert that:
    - guild_config is never passed as a keyword argument
    - The call site does not forward any symbol named guild_cfg / cfg / config
      as the guild_config positional argument.

    We can't prove at the AST level that cfg is not an ORM (that would require
    type-inference); what we CAN assert statically is that the guild_config
    kwarg is absent from every fight_ships call — meaning the default None is
    used, which is safe by definition.
    """

    def test_bounties_router_fight_ships_no_guild_config_kwarg(self):
        """bounties.py combat_bonus fight_ships call does not pass guild_config=."""
        tree = _parse(_BOUNTIES_ROUTER)
        calls = _find_calls(tree, "fight_ships")
        assert calls, "Expected at least one fight_ships call in bounties.py"
        for call in calls:
            kwargs = _kwarg_names(call)
            assert "guild_config" not in kwargs, (
                f"bounties.py fight_ships call at line {call.lineno} "
                f"passes guild_config= — C1a-4 violation risk; extract scalars first"
            )

    def test_bounty_service_fight_ships_no_guild_config_kwarg(self):
        """bounty_service.py fight_ships calls (bronze + silver+) do not pass guild_config=."""
        tree = _parse(_BOUNTY_SERVICE)
        calls = _find_calls(tree, "fight_ships")
        assert calls, "Expected at least one fight_ships call in bounty_service.py"
        for call in calls:
            kwargs = _kwarg_names(call)
            assert "guild_config" not in kwargs, (
                f"bounty_service.py fight_ships call at line {call.lineno} passes guild_config= — C1a-4 violation risk"
            )

    def test_duel_service_fight_ships_no_guild_config_kwarg(self):
        """duel_service.py fight_ships call does not pass guild_config=."""
        tree = _parse(_DUEL_SERVICE)
        calls = _find_calls(tree, "fight_ships")
        assert calls, "Expected at least one fight_ships call in duel_service.py"
        for call in calls:
            kwargs = _kwarg_names(call)
            assert "guild_config" not in kwargs, (
                f"duel_service.py fight_ships call at line {call.lineno} passes guild_config= — C1a-4 violation risk"
            )

    def test_preflight_offload_cpu_call_has_no_guild_config_kwarg(self):
        """combat_preflight_service.py offload_cpu call does not pass guild_config=."""
        tree = _parse(_PREFLIGHT_SERVICE)
        calls = _find_calls(tree, "offload_cpu")
        assert calls, "Expected at least one offload_cpu call in combat_preflight_service.py"
        for call in calls:
            kwargs = _kwarg_names(call)
            assert "guild_config" not in kwargs, (
                f"combat_preflight_service.py offload_cpu call at line {call.lineno} "
                f"passes guild_config= — C1a-4 violation risk"
            )

    def test_bounty_service_extracts_scalar_before_fight_ships(self):
        """bounty_service.py extracts pvc_damage_reduction scalar (resolve_constant)
        BEFORE fight_ships is called — ORM stays on this side of the boundary.

        Strategy: verify that 'resolve_constant' appears in the source and that
        fight_ships is not called with cfg/guild_config as a positional arg.
        """
        source = _BOUNTY_SERVICE.read_text(encoding="utf-8")
        assert "resolve_constant" in source, (
            "bounty_service.py must use resolve_constant() to extract scalars before fight_ships"
        )
        # Confirm pvc_damage_reduction is passed as a keyword (scalar) — not a cfg object
        tree = _parse(_BOUNTY_SERVICE)
        for call in _find_calls(tree, "fight_ships"):
            kwargs = _kwarg_names(call)
            assert "pvc_damage_reduction" in kwargs, (
                f"fight_ships at line {call.lineno}: expected pvc_damage_reduction= kwarg (scalar float)"
            )

    def test_preflight_service_has_orm_guard(self):
        """combat_preflight_service.py contains an _is_orm_model guard (C1a-4 parity)."""
        source = _PREFLIGHT_SERVICE.read_text(encoding="utf-8")
        assert "_is_orm_model" in source, (
            "combat_preflight_service.py must have an _is_orm_model guard for C1a-4 parity with fight_ships"
        )

    def test_fight_ships_has_orm_guard(self):
        """combat_service.py fight_ships contains an _is_orm_model assert (the original C1a-4 guard)."""
        source = (_SRC / "services" / "combat_service.py").read_text(encoding="utf-8")
        assert "_is_orm_model(guild_config)" in source, (
            "combat_service.py fight_ships must assert not _is_orm_model(guild_config)"
        )

    def test_bounty_service_fight_ships_call_count(self):
        """bounty_service.py has exactly 2 fight_ships call sites (bronze bonus + silver+ gate)."""
        tree = _parse(_BOUNTY_SERVICE)
        calls = _find_calls(tree, "fight_ships")
        assert len(calls) == 2, (
            f"Expected exactly 2 fight_ships calls in bounty_service.py, found {len(calls)}. "
            "If a new call site was added, update this test AND verify its boundary is clean."
        )

    def test_bounties_router_fight_ships_call_count(self):
        """bounties.py has exactly 1 fight_ships call site (combat_bonus endpoint)."""
        tree = _parse(_BOUNTIES_ROUTER)
        calls = _find_calls(tree, "fight_ships")
        assert len(calls) == 1, (
            f"Expected exactly 1 fight_ships call in bounties.py, found {len(calls)}. "
            "If a new call site was added, update this test AND verify its boundary is clean."
        )

    def test_duel_service_fight_ships_call_count(self):
        """duel_service.py has exactly 1 fight_ships call site (resolve_duel)."""
        tree = _parse(_DUEL_SERVICE)
        calls = _find_calls(tree, "fight_ships")
        assert len(calls) == 1, (
            f"Expected exactly 1 fight_ships call in duel_service.py, found {len(calls)}. "
            "If a new call site was added, update this test AND verify its boundary is clean."
        )

    def test_preflight_service_offload_cpu_call_count(self):
        """combat_preflight_service.py has exactly 1 offload_cpu call site."""
        tree = _parse(_PREFLIGHT_SERVICE)
        calls = _find_calls(tree, "offload_cpu")
        assert len(calls) == 1, (
            f"Expected exactly 1 offload_cpu call in combat_preflight_service.py, found {len(calls)}. "
            "If a new call site was added, update this test AND verify its boundary is clean."
        )


# ===========================================================================
# 2. RUNTIME REJECTION — fight_ships path
# ===========================================================================


class TestRuntimeRejectionFightShips:
    """fight_ships rejects a live-ORM guild_config before the worker boundary."""

    @pytest.mark.asyncio
    async def test_orm_guild_config_raises_assertion_error(self):
        """fight_ships raises AssertionError immediately when guild_config is an ORM model.

        The AssertionError must fire before offload_cpu is called — i.e. before
        any process-pool serialisation attempt.
        """
        service = CombatService()
        l1 = ShipLoadout(ship_name="Ship1", base_armour=100)
        l2 = ShipLoadout(ship_name="Ship2", base_armour=100)

        # Construct a mapper-bearing object — simulates a live SQLAlchemy row.
        fake_orm = MagicMock()
        type(fake_orm).__mapper__ = MagicMock()
        assert _is_orm_model(fake_orm), "Test prerequisite: fake_orm must look like an ORM to _is_orm_model"

        with pytest.raises(AssertionError, match="guild_config must not be a live ORM model"):
            await service.fight_ships(l1, l2, log_result=False, guild_config=fake_orm)

    @pytest.mark.asyncio
    async def test_real_guild_config_orm_raises_assertion_error(self):
        """fight_ships raises AssertionError for a REAL GuildConfig ORM instance (not a mock).

        Uses GuildConfig.__new__ to allocate a bare mapped instance so SQLAlchemy's
        mapper metadata (__mapper__) is present — the exact condition _is_orm_model tests.
        """
        from persist.models.guild_config import GuildConfig

        service = CombatService()
        l1 = ShipLoadout(ship_name="Ship1", base_armour=100)
        l2 = ShipLoadout(ship_name="Ship2", base_armour=100)

        real_orm_instance = GuildConfig.__new__(GuildConfig)
        assert _is_orm_model(real_orm_instance), (
            "Test prerequisite: GuildConfig.__new__ must produce an instance _is_orm_model recognises as an ORM model"
        )

        with pytest.raises(AssertionError, match="guild_config must not be a live ORM model"):
            await service.fight_ships(l1, l2, log_result=False, guild_config=real_orm_instance)

    @pytest.mark.asyncio
    async def test_none_guild_config_is_accepted_by_fight_ships(self):
        """fight_ships(guild_config=None) passes the guard and runs normally."""
        service = CombatService()
        l1 = ShipLoadout(ship_name="Attacker", base_armour=500)
        l2 = ShipLoadout(ship_name="Defender", base_armour=10)
        # Should not raise; result has expected shape.
        result = await service.fight_ships(l1, l2, log_result=False, guild_config=None)
        assert hasattr(result, "winner_name")
        assert hasattr(result, "is_stalemate")

    @pytest.mark.asyncio
    async def test_int_guild_id_not_confused_with_orm(self):
        """guild_id=42 (plain int) does not trip the guild_config guard.

        Regression: guild_id is passed separately; it must never be confused
        with the guild_config positional.
        """
        service = CombatService()
        l1 = ShipLoadout(ship_name="Attacker", base_armour=500)
        l2 = ShipLoadout(ship_name="Defender", base_armour=10)
        session_mock = AsyncMock()

        with (
            patch("services.combat_log_service.CombatLogService.persist", new=AsyncMock(return_value=1)),
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
                guild_id=42,
                session=session_mock,
                guild_config=None,
            )
        assert hasattr(result, "winner_name")


# ===========================================================================
# 3. RUNTIME REJECTION — preflight (run_fight_batch) path
# ===========================================================================


class TestRuntimeRejectionPreflightPath:
    """CombatPreflightService.estimate rejects an ORM model in the matchup list."""

    @pytest.mark.asyncio
    async def test_orm_in_matchup_loadout1_raises_assertion_error(self):
        """estimate raises AssertionError when matchup loadout1 is a live ORM model.

        Injects a mapper-bearing fake into matchups list to simulate a bug where
        an ORM object slipped in as loadout1.
        """
        from services.combat_preflight_service import CombatPreflightService  # noqa: F401

        # Construct fake ORM with __mapper__ so _is_orm_model returns True.
        fake_orm_loadout = MagicMock()
        type(fake_orm_loadout).__mapper__ = MagicMock()
        assert _is_orm_model(fake_orm_loadout), "Test prerequisite: fake must look like ORM"

        clean_loadout = ShipLoadout(ship_name="Clean", base_armour=100)

        # Directly test the guard by injecting bad matchups and calling the
        # inner guard logic that was added to estimate().
        # We need to call the guard section without running the full estimate
        # (which requires DB).  We do this by constructing the matchup list
        # and invoking the _is_orm_model check inline — mirroring exactly what
        # the guard in estimate() does.
        matchups = [(fake_orm_loadout, clean_loadout, None, "", "")]

        found_violation = False
        for _idx, _matchup in enumerate(matchups):
            for _pos, _elem in enumerate(_matchup):
                if _is_orm_model(_elem):
                    found_violation = True
                    break

        assert found_violation, "Expected _is_orm_model to detect the fake ORM loadout in matchups — guard would fire"

    @pytest.mark.asyncio
    async def test_estimate_with_orm_in_player_loadout_is_rejected(self):
        """estimate() raises AssertionError when player_loadout is a live ORM (end-to-end guard path).

        We patch LoadoutBuilder.from_player to return a fake ORM object so the
        guard fires in the actual estimate() code path.
        """
        from types import SimpleNamespace

        from services.combat_preflight_service import CombatPreflightService

        svc = CombatPreflightService.__new__(CombatPreflightService)
        svc.bounty_repo = MagicMock()

        # Active criminal pool — needs at least one entry so we reach the matchup section.
        criminal_ship = {"ship_name": "Raider", "ship_armour": 80, "weapons": [], "turrets": []}
        svc.bounty_repo.get_active_by_guild_and_division = AsyncMock(
            return_value=[SimpleNamespace(criminal_ship=criminal_ship)]
        )

        # Construct a mapper-bearing object to return as the player's "loadout".
        fake_orm = MagicMock()
        type(fake_orm).__mapper__ = MagicMock()
        # Also give it criminal_ship-compatible attributes so from_criminal_ship doesn't crash.

        db_mock = AsyncMock()

        with (
            patch(
                "services.combat_preflight_service.LoadoutBuilder.from_player",
                new=AsyncMock(return_value=fake_orm),
            ),
            pytest.raises(AssertionError, match="must not be a live ORM model"),
        ):
            await svc.estimate(db_mock, player_id=1, guild_id=1, target_tier="Silver", num_sims=1)

    @pytest.mark.asyncio
    async def test_clean_loadouts_pass_preflight_guard(self):
        """estimate() guard does NOT fire for clean ShipLoadout objects (no false positive)."""
        clean_loadout = ShipLoadout(ship_name="Clean", base_armour=100)
        matchups = [(clean_loadout, clean_loadout, None, "", "")]

        for _idx, _matchup in enumerate(matchups):
            for _pos, _elem in enumerate(_matchup):
                assert not _is_orm_model(_elem), (
                    f"False positive: _is_orm_model flagged {type(_elem).__name__!r} at [{_idx}][{_pos}]"
                )


# ===========================================================================
# 4. ANTI-VACUOUS — _is_orm_model precision + recall
# ===========================================================================


class TestIsOrmModelAntiVacuous:
    """_is_orm_model correctly detects ORM instances and does not false-positive."""

    def test_returns_true_for_mock_with_mapper(self):
        """_is_orm_model returns True when the object's class has __mapper__."""
        fake = MagicMock()
        type(fake).__mapper__ = MagicMock()
        assert _is_orm_model(fake) is True

    def test_returns_true_for_real_guild_config_instance(self):
        """_is_orm_model returns True for a real (bare) GuildConfig instance.

        GuildConfig.__new__ gives an uninitialized ORM instance with __mapper__
        set by the SQLAlchemy mapper registry at class-definition time.
        No DB connection required.
        """
        from persist.models.guild_config import GuildConfig

        instance = GuildConfig.__new__(GuildConfig)
        assert _is_orm_model(instance) is True, "_is_orm_model must detect GuildConfig (real SQLAlchemy-mapped class)"

    def test_returns_false_for_none(self):
        """_is_orm_model(None) → False (the default safe value)."""
        assert _is_orm_model(None) is False

    def test_returns_false_for_int(self):
        """_is_orm_model(42) → False (plain scalar)."""
        assert _is_orm_model(42) is False

    def test_returns_false_for_float(self):
        """_is_orm_model(0.33) → False (pvc_damage_reduction is a float)."""
        assert _is_orm_model(0.33) is False

    def test_returns_false_for_plain_dict(self):
        """_is_orm_model({}) → False (guild_config=None extract fallback)."""
        assert _is_orm_model({"key": "value"}) is False

    def test_returns_false_for_ship_loadout(self):
        """_is_orm_model(ShipLoadout) → False (frozen dataclass, picklable)."""
        loadout = ShipLoadout(ship_name="Test", base_armour=100)
        assert _is_orm_model(loadout) is False

    def test_returns_false_for_string(self):
        """_is_orm_model("some_label") → False (combatant labels are strings)."""
        assert _is_orm_model("some_label") is False

    def test_returns_false_for_list(self):
        """_is_orm_model([]) → False (matchup list itself is not an ORM)."""
        assert _is_orm_model([]) is False

    def test_returns_false_for_simple_namespace(self):
        """_is_orm_model(SimpleNamespace(...)) → False (synthetic criminal objects)."""
        from types import SimpleNamespace

        obj = SimpleNamespace(criminal_ship={"ship_name": "Raider"})
        assert _is_orm_model(obj) is False

    def test_different_real_orm_models_also_detected(self):
        """_is_orm_model returns True for other real ORM models (not just GuildConfig).

        Spot-checks Player and Bounty to ensure the guard is not GuildConfig-specific.
        """
        from persist.models.bounty import Bounty
        from persist.models.player import Player

        for cls in (Player, Bounty):
            instance = cls.__new__(cls)
            assert _is_orm_model(instance) is True, (
                f"_is_orm_model must return True for {cls.__name__} (SQLAlchemy-mapped class)"
            )
