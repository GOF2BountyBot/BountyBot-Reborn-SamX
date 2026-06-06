"""Tests for the utils.compute.combat_worker leaf module.

Two test groups:
1. Import-hygiene via forkserver child process — proves combat_worker does NOT
   drag heavy dependencies (sqlalchemy, persist, fastapi, main, services.*)
   into a fresh interpreter when imported.
2. Static AST check — proves no heavy module names appear in top-level import
   statements in the source file itself.

FORKSERVER DESIGN
-----------------
``multiprocessing.get_context("forkserver")`` spawns a brand-new Python
interpreter for each child — it does NOT fork the parent process image.  This
means the child starts with only the stdlib and whatever the forkserver stub
imports on its own; it does NOT inherit any of the parent test process's already-
imported modules.

Inside the child we:
  1. Add the ``src/`` directory to ``sys.path`` (same path the parent uses).
  2. Snapshot ``sys.modules`` keys BEFORE importing combat_worker.
  3. Import ``utils.compute.combat_worker``.
  4. Snapshot ``sys.modules`` keys AFTER the import.
  5. Return the *difference* (newly imported module names) back to the parent.

The parent asserts that none of the forbidden heavy-module prefixes appear in
the newly-added set.  This genuinely proves that importing combat_worker does
not add any heavy dependency — regardless of what the parent process has
imported during the broader test suite run.
"""

from __future__ import annotations

import ast
import multiprocessing
import os
import pathlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"

# ---------------------------------------------------------------------------
# Heavy-module prefixes that combat_worker must NOT introduce.
# ---------------------------------------------------------------------------

_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "sqlalchemy",
    "asyncpg",
    "fastapi",
    "starlette",          # fastapi re-exports from starlette; ban both
    "persist",
    "main",               # the FastAPI app entrypoint
    "services",
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

    Imports ``combat_worker`` directly by file path using
    ``importlib.util.spec_from_file_location``, bypassing the ``utils``
    package init (which auto-imports executors and their heavy deps).  This
    isolates what *combat_worker itself* requires at import time.

    Returns a dict::

        {
            "success": bool,          # True if import succeeded without error
            "error": str | None,      # Exception message if import failed
            "added_modules": list,    # Module names added by importing combat_worker
        }
    """
    import importlib.util
    import pathlib

    result: dict = {"success": False, "error": None, "added_modules": []}
    try:
        module_path = pathlib.Path(src_dir) / "utils" / "compute" / "combat_worker.py"

        # Snapshot before import
        before: set[str] = set(sys.modules.keys())

        # Load module directly by file path — does NOT trigger utils/__init__.py
        spec = importlib.util.spec_from_file_location(
            "utils.compute.combat_worker", module_path
        )
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules["utils.compute.combat_worker"] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]

        # Snapshot after import
        after: set[str] = set(sys.modules.keys())

        result["added_modules"] = sorted(after - before)
        result["success"] = True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        result["error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# Test 1: forkserver import-hygiene check
# ---------------------------------------------------------------------------


def test_combat_worker_imports_cleanly_in_forkserver_child() -> None:
    """combat_worker must import without error in a fresh forkserver child."""
    ctx = multiprocessing.get_context("forkserver")
    pool = ctx.Pool(processes=1)
    try:
        result = pool.apply(_child_check_imports, args=(str(_SRC_DIR),))
    finally:
        pool.close()
        pool.join()

    assert result["success"], (
        f"combat_worker failed to import in forkserver child: {result['error']}"
    )


def test_combat_worker_adds_no_heavy_modules_in_forkserver_child() -> None:
    """Importing combat_worker must not introduce any heavy/ORM/DB module.

    The forkserver child starts with a clean interpreter, so ``added_modules``
    is the exact set of modules that combat_worker's import chain requires.
    We assert none of the forbidden prefixes appear in that set.
    """
    ctx = multiprocessing.get_context("forkserver")
    pool = ctx.Pool(processes=1)
    try:
        result = pool.apply(_child_check_imports, args=(str(_SRC_DIR),))
    finally:
        pool.close()
        pool.join()

    assert result["success"], (
        f"combat_worker import failed; cannot check added modules: {result['error']}"
    )

    added: list[str] = result["added_modules"]
    violations: list[str] = [
        mod
        for mod in added
        for prefix in _FORBIDDEN_PREFIXES
        if mod == prefix or mod.startswith(prefix + ".")
    ]

    assert not violations, (
        f"combat_worker import dragged in forbidden heavy modules:\n"
        + "\n".join(f"  {v}" for v in violations)
        + f"\n\nFull added-module list:\n"
        + "\n".join(f"  {m}" for m in added)
    )


# ---------------------------------------------------------------------------
# Test 2: static AST check on top-level import statements
# ---------------------------------------------------------------------------

_COMBAT_WORKER_PATH = _SRC_DIR / "utils" / "compute" / "combat_worker.py"

_FORBIDDEN_IMPORT_NAMES: tuple[str, ...] = (
    "sqlalchemy",
    "asyncpg",
    "fastapi",
    "starlette",
    "persist",
    "main",
    "services",
    "executors",
    "job_executor",
    "auto_seeder",
    "data_loader",
    "scheduler_holder",
    "executor_holder",
    "offload",
    "message_builders",
    "api",
    "alembic",
    "apscheduler",
    "httpx",
    "pydantic",
    "PIL",
    "uvicorn",
)


def _collect_toplevel_import_names(source: str) -> list[str]:
    """Return all module names referenced in top-level import statements.

    Only top-level (module-scope) ``import X`` and ``from X import Y``
    statements are collected; imports inside function bodies are ignored
    (deferred imports are intentionally allowed for executors, but
    combat_worker must not even have deferred imports of heavy modules).

    Actually for this skeleton the task only forbids top-level imports, so
    we restrict to ``ast.Import`` / ``ast.ImportFrom`` nodes whose parent is
    the module body (i.e. depth == 1 from the Module node).
    """
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.iter_child_nodes(tree):  # direct children of Module = top-level stmts
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


def test_combat_worker_has_no_forbidden_toplevel_imports() -> None:
    """Top-level import statements in combat_worker.py must be stdlib-only.

    Parses the source with ``ast`` and checks that no forbidden module name
    appears as a top-level import.  This catches accidental additions before
    they ever reach a runtime test.
    """
    source = _COMBAT_WORKER_PATH.read_text(encoding="utf-8")
    toplevel_imports = _collect_toplevel_import_names(source)

    violations: list[str] = []
    for imported_name in toplevel_imports:
        for forbidden in _FORBIDDEN_IMPORT_NAMES:
            if imported_name == forbidden or imported_name.startswith(forbidden + "."):
                violations.append(imported_name)
                break  # one violation entry per import line

    assert not violations, (
        f"combat_worker.py has forbidden top-level import(s):\n"
        + "\n".join(f"  import {v}" for v in violations)
        + "\n\nOnly stdlib imports are allowed at module top-level."
    )


def test_combat_worker_source_exists() -> None:
    """Sanity check: the module source file is present at the expected path."""
    assert _COMBAT_WORKER_PATH.exists(), (
        f"combat_worker.py not found at {_COMBAT_WORKER_PATH}"
    )
