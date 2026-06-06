"""P2-T1 tests for compute.combat_worker.run_fight.

Test groups
-----------
1. PARITY — byte-identical timeline to dataclasses.asdict + key_events parity
2. PICKLABILITY — inputs/outputs round-trip through pickle
3. SEPARATE PROCESS — run_fight executes in a real forkserver ProcessPoolExecutor
4. COMPACT — compact=True returns (winner_side, is_stalemate) matching full result
5. FORKSERVER IMPORT HYGIENE (PRODUCTION PATH) — importing compute.combat_worker
   via the PRODUCTION import path (not spec_from_file_location) in a fresh
   forkserver child must not introduce any heavy ORM/DB/app module.
   Two sub-tests:
     5a. After `from compute.combat_worker import run_fight`: no forbidden modules.
     5b. After actually CALLING run_fight(...): still no forbidden modules.
"""

from __future__ import annotations

import dataclasses
import multiprocessing
import os
import pathlib
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"

# Ensure src/ is on path for in-process imports
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


# ---------------------------------------------------------------------------
# Shared test loadout helpers (in-process, after src/ on path)
# ---------------------------------------------------------------------------

from compute.combat_worker import run_fight
from services.combat_models import ShipLoadout, WeaponStats
from services.combat_resolver import TickResolver, _extract_key_events

_FIXED_SEED = 42


def _gun_loadout(name: str = "Fighter", base_armour: int = 300) -> ShipLoadout:
    """ShipLoadout with one primary weapon — fast cooldown so fights terminate."""
    weapon = WeaponStats(
        name="TestCannon",
        dps=500.0,
        damage_per_shot=50.0,
        loading_speed_ms=100,
        range_m=5000.0,
    )
    return ShipLoadout(ship_name=name, base_armour=base_armour, weapons=[weapon])


def _bare_loadout(name: str = "Bare", base_armour: int = 100) -> ShipLoadout:
    """Minimal ShipLoadout — no weapons; fight ends at time_cap (stalemate)."""
    return ShipLoadout(ship_name=name, base_armour=base_armour)


# ---------------------------------------------------------------------------
# 1. PARITY — byte-identical timeline to dataclasses.asdict + key_events
# ---------------------------------------------------------------------------


class TestParity:
    """run_fight compact=False timeline is byte-identical to dataclasses.asdict."""

    def test_timeline_byte_identical_to_asdict(self):
        """Manual projection produces the same dicts as dataclasses.asdict."""
        l1 = _gun_loadout("C1", base_armour=200)
        l2 = _gun_loadout("C2", base_armour=200)

        # In-process baseline: resolve + asdict
        result = TickResolver(seed=_FIXED_SEED).resolve(l1, l2)
        asdict_timeline = [dataclasses.asdict(ev) for ev in result.combat_log]

        # Worker path
        worker_out = run_fight(
            l1,
            l2,
            pvc_damage_reduction=0.0,
            seed=_FIXED_SEED,
            combatant1_label="",
            combatant2_label="",
            compact=False,
        )

        assert worker_out["timeline"] == asdict_timeline, (
            "run_fight timeline is not byte-identical to dataclasses.asdict output"
        )

    def test_timeline_length_matches_in_process(self):
        """Timeline length matches the in-process resolve() output."""
        l1 = _gun_loadout("A", base_armour=200)
        l2 = _gun_loadout("B", base_armour=200)

        result = TickResolver(seed=_FIXED_SEED).resolve(l1, l2)
        asdict_timeline = [dataclasses.asdict(ev) for ev in result.combat_log]

        worker_out = run_fight(
            l1,
            l2,
            pvc_damage_reduction=0.0,
            seed=_FIXED_SEED,
            combatant1_label="",
            combatant2_label="",
            compact=False,
        )

        assert len(worker_out["timeline"]) == len(asdict_timeline)

    def test_key_events_match_extract_key_events(self):
        """key_events from run_fight equals _extract_key_events on the same timeline."""
        l1 = _gun_loadout("P1", base_armour=200)
        l2 = _gun_loadout("P2", base_armour=200)

        worker_out = run_fight(
            l1,
            l2,
            pvc_damage_reduction=0.0,
            seed=_FIXED_SEED,
            combatant1_label="",
            combatant2_label="",
            compact=False,
        )

        expected_key_events = _extract_key_events(worker_out["timeline"])
        assert worker_out["key_events"] == expected_key_events

    def test_winner_and_stalemate_match_in_process(self):
        """winner_name, loser_name, is_stalemate, winner_side match in-process result."""
        l1 = _gun_loadout("Alpha", base_armour=200)
        l2 = _gun_loadout("Beta", base_armour=200)

        result = TickResolver(seed=_FIXED_SEED).resolve(l1, l2)
        worker_out = run_fight(
            l1,
            l2,
            pvc_damage_reduction=0.0,
            seed=_FIXED_SEED,
            combatant1_label="",
            combatant2_label="",
            compact=False,
        )

        assert worker_out["winner_name"] == result.winner_name
        assert worker_out["loser_name"] == result.loser_name
        assert worker_out["is_stalemate"] == result.is_stalemate
        assert worker_out["winner_side"] == result.winner_side

    def test_stalemate_fight_parity(self):
        """Bare (no-weapon) fight: stalemate, timeline byte-identical to asdict."""
        l1 = _bare_loadout("S1", base_armour=100)
        l2 = _bare_loadout("S2", base_armour=100)

        result = TickResolver(seed=_FIXED_SEED).resolve(l1, l2)
        asdict_timeline = [dataclasses.asdict(ev) for ev in result.combat_log]

        worker_out = run_fight(
            l1,
            l2,
            pvc_damage_reduction=0.0,
            seed=_FIXED_SEED,
            combatant1_label="",
            combatant2_label="",
            compact=False,
        )

        assert worker_out["is_stalemate"] is True
        assert worker_out["timeline"] == asdict_timeline


# ---------------------------------------------------------------------------
# 2. PICKLABILITY — inputs/outputs round-trip through pickle
# ---------------------------------------------------------------------------


class TestPicklability:
    """Inputs and outputs round-trip cleanly through pickle."""

    def test_loadout_is_picklable(self):
        """ShipLoadout with weapons is picklable."""
        loadout = _gun_loadout()
        assert pickle.loads(pickle.dumps(loadout)) == loadout

    def test_compact_output_is_picklable(self):
        """compact=True output (tuple) is picklable."""
        l1 = _bare_loadout("X1")
        l2 = _bare_loadout("X2")
        out = run_fight(
            l1, l2, pvc_damage_reduction=0.0, seed=_FIXED_SEED, combatant1_label="", combatant2_label="", compact=True
        )
        rt = pickle.loads(pickle.dumps(out))
        assert rt == out

    def test_full_output_is_picklable(self):
        """compact=False output (dict) is picklable."""
        l1 = _gun_loadout("P1", base_armour=200)
        l2 = _gun_loadout("P2", base_armour=200)
        out = run_fight(
            l1, l2, pvc_damage_reduction=0.0, seed=_FIXED_SEED, combatant1_label="", combatant2_label="", compact=False
        )
        rt = pickle.loads(pickle.dumps(out))
        assert rt["winner_side"] == out["winner_side"]
        assert rt["timeline"] == out["timeline"]
        assert rt["key_events"] == out["key_events"]


# ---------------------------------------------------------------------------
# 3. SEPARATE PROCESS — run in a real forkserver ProcessPoolExecutor
# ---------------------------------------------------------------------------

# Worker helper that runs in the child process.  Must be a module-level
# function (not a lambda/closure) to be picklable by multiprocessing.


def _run_fight_in_child(src_dir: str, loadout1_pkl: bytes, loadout2_pkl: bytes, seed: int, compact: bool) -> dict:
    """Execute run_fight inside the child process. Returns a dict with results."""
    import sys

    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    import pickle as _pickle

    l1 = _pickle.loads(loadout1_pkl)
    l2 = _pickle.loads(loadout2_pkl)

    from compute.combat_worker import run_fight as _run_fight

    result = _run_fight(
        l1, l2, pvc_damage_reduction=0.0, seed=seed, combatant1_label="", combatant2_label="", compact=compact
    )
    child_pid = __import__("os").getpid()
    if compact:
        return {"result": result, "child_pid": child_pid}
    else:
        return {
            "winner_side": result["winner_side"],
            "is_stalemate": result["is_stalemate"],
            "timeline_len": len(result["timeline"]),
            "key_events_len": len(result["key_events"]),
            "child_pid": child_pid,
        }


class TestSeparateProcess:
    """run_fight executes correctly in a separate forkserver process."""

    def test_runs_in_different_pid_compact(self):
        """compact=True run_fight in forkserver child returns a different pid."""
        l1 = _gun_loadout("FP1", base_armour=200)
        l2 = _gun_loadout("FP2", base_armour=200)

        ctx = multiprocessing.get_context("forkserver")
        with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
            fut = pool.submit(
                _run_fight_in_child,
                str(_SRC_DIR),
                pickle.dumps(l1),
                pickle.dumps(l2),
                _FIXED_SEED,
                True,
            )
            child_result = fut.result(timeout=60)

        assert child_result["child_pid"] != os.getpid(), "run_fight must execute in a different process"
        # (winner_side, is_stalemate)
        assert isinstance(child_result["result"], tuple)
        assert len(child_result["result"]) == 2

    def test_runs_in_different_pid_full(self):
        """compact=False run_fight in forkserver child returns timeline and key_events."""
        l1 = _gun_loadout("FP3", base_armour=200)
        l2 = _gun_loadout("FP4", base_armour=200)

        ctx = multiprocessing.get_context("forkserver")
        with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
            fut = pool.submit(
                _run_fight_in_child,
                str(_SRC_DIR),
                pickle.dumps(l1),
                pickle.dumps(l2),
                _FIXED_SEED,
                False,
            )
            child_result = fut.result(timeout=60)

        assert child_result["child_pid"] != os.getpid()
        assert child_result["timeline_len"] > 0
        assert child_result["key_events_len"] > 0

    def test_child_result_matches_in_process(self):
        """compact=False child result matches in-process run_fight output."""
        l1 = _gun_loadout("MP1", base_armour=200)
        l2 = _gun_loadout("MP2", base_armour=200)

        # In-process reference
        in_proc = run_fight(
            l1,
            l2,
            pvc_damage_reduction=0.0,
            seed=_FIXED_SEED,
            combatant1_label="",
            combatant2_label="",
            compact=False,
        )

        ctx = multiprocessing.get_context("forkserver")
        with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
            fut = pool.submit(
                _run_fight_in_child,
                str(_SRC_DIR),
                pickle.dumps(l1),
                pickle.dumps(l2),
                _FIXED_SEED,
                False,
            )
            child_result = fut.result(timeout=60)

        assert child_result["winner_side"] == in_proc["winner_side"]
        assert child_result["is_stalemate"] == in_proc["is_stalemate"]
        assert child_result["timeline_len"] == len(in_proc["timeline"])
        assert child_result["key_events_len"] == len(in_proc["key_events"])


# ---------------------------------------------------------------------------
# 4. COMPACT — returns (winner_side, is_stalemate) consistent with full result
# ---------------------------------------------------------------------------


class TestCompact:
    """compact=True returns a 2-tuple consistent with the full result."""

    def test_compact_is_tuple(self):
        """compact=True → returns a plain 2-tuple."""
        l1 = _bare_loadout("C1")
        l2 = _bare_loadout("C2")
        out = run_fight(
            l1, l2, pvc_damage_reduction=0.0, seed=_FIXED_SEED, combatant1_label="", combatant2_label="", compact=True
        )
        assert isinstance(out, tuple)
        assert len(out) == 2

    def test_compact_winner_side_matches_full(self):
        """compact winner_side matches full result winner_side (same seed)."""
        l1 = _gun_loadout("D1", base_armour=200)
        l2 = _gun_loadout("D2", base_armour=200)

        compact_out = run_fight(
            l1, l2, pvc_damage_reduction=0.0, seed=_FIXED_SEED, combatant1_label="", combatant2_label="", compact=True
        )
        full_out = run_fight(
            l1, l2, pvc_damage_reduction=0.0, seed=_FIXED_SEED, combatant1_label="", combatant2_label="", compact=False
        )

        winner_side, is_stalemate = compact_out
        assert winner_side == full_out["winner_side"]
        assert is_stalemate == full_out["is_stalemate"]

    def test_compact_stalemate_winner_side_is_none(self):
        """Stalemate fight: compact returns (None, True)."""
        l1 = _bare_loadout("S1", base_armour=100)
        l2 = _bare_loadout("S2", base_armour=100)
        winner_side, is_stalemate = run_fight(
            l1,
            l2,
            pvc_damage_reduction=0.0,
            seed=_FIXED_SEED,
            combatant1_label="",
            combatant2_label="",
            compact=True,
        )
        assert winner_side is None
        assert is_stalemate is True

    def test_compact_non_stalemate_winner_side_set(self):
        """Non-stalemate fight: compact winner_side is 1 or 2."""
        l1 = _gun_loadout("W1", base_armour=500)  # vastly more HP → will win
        l2 = _gun_loadout("W2", base_armour=1)  # 1 HP → dies immediately

        winner_side, is_stalemate = run_fight(
            l1,
            l2,
            pvc_damage_reduction=0.0,
            seed=_FIXED_SEED,
            combatant1_label="",
            combatant2_label="",
            compact=True,
        )
        assert is_stalemate is False
        assert winner_side in (1, 2)


# ---------------------------------------------------------------------------
# 5. FORKSERVER IMPORT HYGIENE (PRODUCTION PATH)
#
# CRITICAL: these child functions use `from compute.combat_worker import run_fight`
# — the PRODUCTION import path — NOT spec_from_file_location.  The old
# spec_from_file_location approach bypassed utils/__init__ and gave a FALSE
# NEGATIVE.  The production path is what ProcessPoolExecutor actually uses.
# ---------------------------------------------------------------------------

_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "sqlalchemy",
    "asyncpg",
    "fastapi",
    "starlette",
    "persist",
    "main",
    "services.combat_service",
    "services.combat_log_service",
    "services.bounty_service",
    "services.player_service",
    "services.shop_service",
    "services.loadout_service",
    "services.ship_service",
    "services.guild_service",
    "services.admin_service",
    "services.audit_service",
    "services.duel_service",
    "services.spawn_service",
    "utils.executors",
    "utils.job_executor",
    "utils.auto_seeder",
    "utils.data_loader",
    "utils.scheduler_holder",
    "utils.executor_holder",
    "utils.offload",
    "message_builders",
    "api",
    "alembic",
    "apscheduler",
    "httpx",
    "pydantic",
    "PIL",
    "uvicorn",
    "aiofiles",
)


def _child_hygiene_import_only(src_dir: str) -> dict:
    """Run inside a fresh forkserver child — PRODUCTION import path.

    Uses ``from compute.combat_worker import run_fight`` (the same path
    ProcessPoolExecutor uses when unpickling the function reference) rather
    than spec_from_file_location.  Snapshots sys.modules before and after,
    returns the delta for the parent to inspect.
    """
    import sys

    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    result: dict = {"success": False, "error": None, "added_modules": []}
    try:
        before: set[str] = set(sys.modules.keys())

        from compute.combat_worker import run_fight as _  # noqa: F401

        after: set[str] = set(sys.modules.keys())
        result["added_modules"] = sorted(after - before)
        result["success"] = True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        result["error"] = str(exc)
    return result


def _child_hygiene_import_and_call(src_dir: str) -> dict:
    """Run inside a fresh forkserver child — PRODUCTION import path + call.

    Imports compute.combat_worker (production path), then actually CALLS
    run_fight with real loadouts.  Re-snapshots sys.modules after the call
    to prove the execution path is ORM-free end-to-end.
    """
    import sys

    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    result: dict = {"success": False, "error": None, "added_modules": []}
    try:
        before: set[str] = set(sys.modules.keys())

        from compute.combat_worker import run_fight as _run_fight
        from services.combat_models import ShipLoadout, WeaponStats

        weapon = WeaponStats(
            name="HygieneGun",
            dps=500.0,
            damage_per_shot=50.0,
            loading_speed_ms=100,
            range_m=5000.0,
        )
        l1 = ShipLoadout(ship_name="H1", base_armour=200, weapons=[weapon])
        l2 = ShipLoadout(ship_name="H2", base_armour=200, weapons=[weapon])

        _run_fight(l1, l2, pvc_damage_reduction=0.0, seed=42, combatant1_label="", combatant2_label="", compact=False)

        after: set[str] = set(sys.modules.keys())
        result["added_modules"] = sorted(after - before)
        result["success"] = True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        result["error"] = str(exc)
    return result


class TestForkserverImportHygiene:
    """combat_worker (production import path) stays ORM/app-import-clean.

    These tests use the PRODUCTION import path (from compute.combat_worker ...)
    inside a forkserver child — NOT the old spec_from_file_location bypass that
    gave a false negative by skipping utils/__init__.
    """

    def test_production_import_no_forbidden_on_import(self):
        """5a: after `from compute.combat_worker import run_fight`, no forbidden modules."""
        ctx = multiprocessing.get_context("forkserver")
        pool = ctx.Pool(processes=1)
        try:
            result = pool.apply(_child_hygiene_import_only, args=(str(_SRC_DIR),))
        finally:
            pool.close()
            pool.join()

        assert result["success"], f"combat_worker production import failed in child: {result['error']}"

        added: list[str] = result["added_modules"]
        violations: list[str] = [
            mod for mod in added for prefix in _FORBIDDEN_PREFIXES if mod == prefix or mod.startswith(prefix + ".")
        ]

        assert not violations, (
            "compute.combat_worker production import dragged in forbidden modules:\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\n\nFull added-module list:\n"
            + "\n".join(f"  {m}" for m in added)
        )

    def test_production_import_no_forbidden_after_run_fight_call(self):
        """5b: after importing + calling run_fight, still no forbidden modules."""
        ctx = multiprocessing.get_context("forkserver")
        pool = ctx.Pool(processes=1)
        try:
            result = pool.apply(_child_hygiene_import_and_call, args=(str(_SRC_DIR),))
        finally:
            pool.close()
            pool.join()

        assert result["success"], f"run_fight call in child failed: {result['error']}"

        added: list[str] = result["added_modules"]
        violations: list[str] = [
            mod for mod in added for prefix in _FORBIDDEN_PREFIXES if mod == prefix or mod.startswith(prefix + ".")
        ]

        assert not violations, (
            "compute.combat_worker run_fight execution dragged in forbidden modules:\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\n\nFull added-module list:\n"
            + "\n".join(f"  {m}" for m in added)
        )
