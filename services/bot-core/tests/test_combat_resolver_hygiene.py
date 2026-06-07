"""Forkserver hygiene + AST static check tests for services.combat_resolver.

Two test groups:
1. Import-hygiene via forkserver child — proves combat_resolver does NOT drag
   heavy dependencies (sqlalchemy, persist, fastapi, main, or other services.*)
   into a fresh interpreter when imported.
2. Static AST check — proves no heavy module names appear in top-level import
   statements in the source file itself.

Mirrors the design of test_compute_combat_worker.py.
"""

from __future__ import annotations

import ast
import multiprocessing
import pathlib

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"

# ---------------------------------------------------------------------------
# Heavy-module prefixes that combat_resolver must NOT introduce.
# Allowed: combat_resolver itself, combat_models, combat_balance, game_constants.
# ---------------------------------------------------------------------------

_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "sqlalchemy",
    "asyncpg",
    "fastapi",
    "starlette",
    "persist",
    "main",
    # All services.* EXCEPT the leaf cluster (resolver/models/balance/game_constants)
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


# ---------------------------------------------------------------------------
# Worker function — runs inside the forkserver child
# ---------------------------------------------------------------------------


def _child_check_imports(src_dir: str) -> dict:
    """Run inside a fresh forkserver child.

    Imports ``combat_resolver`` directly by file path, bypassing any package
    __init__ that might auto-import heavy dependencies.

    Returns::

        {
            "success": bool,
            "error": str | None,
            "added_modules": list,
        }
    """
    import importlib.util
    import pathlib
    import sys

    # Add src/ to path so services.* imports resolve
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    result: dict = {"success": False, "error": None, "added_modules": []}
    try:
        module_path = pathlib.Path(src_dir) / "services" / "combat_resolver.py"

        before: set[str] = set(sys.modules.keys())

        spec = importlib.util.spec_from_file_location("services.combat_resolver", module_path)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules["services.combat_resolver"] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        after: set[str] = set(sys.modules.keys())
        result["added_modules"] = sorted(after - before)
        result["success"] = True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        result["error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# Test 1: forkserver import-hygiene
# ---------------------------------------------------------------------------


def test_combat_resolver_imports_cleanly_in_forkserver_child() -> None:
    """combat_resolver must import without error in a fresh forkserver child."""
    ctx = multiprocessing.get_context("forkserver")
    pool = ctx.Pool(processes=1)
    try:
        result = pool.apply(_child_check_imports, args=(str(_SRC_DIR),))
    finally:
        pool.close()
        pool.join()

    assert result["success"], f"combat_resolver failed to import in forkserver child: {result['error']}"


def test_combat_resolver_adds_no_heavy_modules_in_forkserver_child() -> None:
    """Importing combat_resolver must not introduce heavy/ORM/DB modules."""
    ctx = multiprocessing.get_context("forkserver")
    pool = ctx.Pool(processes=1)
    try:
        result = pool.apply(_child_check_imports, args=(str(_SRC_DIR),))
    finally:
        pool.close()
        pool.join()

    assert result["success"], f"combat_resolver import failed; cannot check added modules: {result['error']}"

    added: list[str] = result["added_modules"]
    violations: list[str] = [
        mod for mod in added for prefix in _FORBIDDEN_PREFIXES if mod == prefix or mod.startswith(prefix + ".")
    ]

    assert not violations, (
        "combat_resolver import dragged in forbidden heavy modules:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\n\nFull added-module list:\n"
        + "\n".join(f"  {m}" for m in added)
    )


# ---------------------------------------------------------------------------
# Test 2: static AST check on top-level import statements
# ---------------------------------------------------------------------------

_COMBAT_RESOLVER_PATH = _SRC_DIR / "services" / "combat_resolver.py"

_FORBIDDEN_IMPORT_NAMES: tuple[str, ...] = (
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
    "apscheduler",
    "httpx",
    "pydantic",
    "PIL",
    "uvicorn",
    "aiofiles",
    "alembic",
)


def _collect_toplevel_import_names(source: str) -> list[str]:
    """Return all module names from top-level import statements."""
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_combat_resolver_has_no_forbidden_toplevel_imports() -> None:
    """Top-level imports in combat_resolver.py must be DB-free."""
    source = _COMBAT_RESOLVER_PATH.read_text(encoding="utf-8")
    toplevel_imports = _collect_toplevel_import_names(source)

    violations: list[str] = []
    for imported_name in toplevel_imports:
        for forbidden in _FORBIDDEN_IMPORT_NAMES:
            if imported_name == forbidden or imported_name.startswith(forbidden + "."):
                violations.append(imported_name)
                break

    assert not violations, (
        "combat_resolver.py has forbidden top-level import(s):\n"
        + "\n".join(f"  import {v}" for v in violations)
        + "\n\nOnly stdlib/shared/combat_models/combat_balance/game_constants allowed."
    )


def test_combat_resolver_source_exists() -> None:
    """Sanity check: combat_resolver.py is present at expected path."""
    assert _COMBAT_RESOLVER_PATH.exists(), f"combat_resolver.py not found at {_COMBAT_RESOLVER_PATH}"


# ---------------------------------------------------------------------------
# P2-T8b: Static assertion — no name-keyed win/identity/attribution decision
# in combat/duel/bounty mechanics.
# ---------------------------------------------------------------------------

_MECHANIC_FILES = [
    _SRC_DIR / "services" / "combat_resolver.py",
    _SRC_DIR / "services" / "bounty_service.py",
    _SRC_DIR / "services" / "duel_service.py",
    _SRC_DIR / "api" / "routers" / "bounties.py",
    _SRC_DIR / "api" / "routers" / "duels.py",
    _SRC_DIR / "services" / "combat_service.py",
    _SRC_DIR / "services" / "combat_preflight_service.py",
]

# Patterns that indicate a name-keyed DECISION (win/identity/attribution).
# Presentation-only uses (f-strings, log lines, == in test assertions) are expected
# to be NOT in the mechanic source files.
# We grep for equality comparisons of winner_name/loser_name against another name.
_FORBIDDEN_PATTERNS = [
    "winner_name ==",
    "loser_name ==",
    "== winner_name",
    "== loser_name",
    "fight_results.winner_name == player_loadout",
    "fight.winner_name == ",
]


def test_no_name_keyed_decision_in_combat_mechanics() -> None:
    """P2-T8b: No combat/duel/bounty MECHANIC file may use winner_name/loser_name
    equality as a DECISION predicate.

    Presentation-only uses (embedding in dicts, logging, schema fields) are
    allowed but must not drive win/identity/attribution logic.

    Files checked: combat_resolver, bounty_service, duel_service, bounties router,
    duels router, combat_service, combat_preflight_service.
    """
    violations: list[str] = []

    for filepath in _MECHANIC_FILES:
        if not filepath.exists():
            continue
        source = filepath.read_text(encoding="utf-8")
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            # Skip comment lines — these may document the old pattern for clarity
            if stripped.startswith("#"):
                continue
            for pattern in _FORBIDDEN_PATTERNS:
                if pattern in stripped:
                    violations.append(f"{filepath.name}:{lineno}: {stripped!r}")
                    break

    assert not violations, (
        "P2-T8b: name-keyed mechanic decision found — all wins/identity must use "
        "winner_side or player snowflake IDs, not name comparisons:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
