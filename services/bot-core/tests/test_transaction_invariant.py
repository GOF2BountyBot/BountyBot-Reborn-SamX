"""Option Y invariant regression test.

AST-based static analysis that fails the suite if anyone re-introduces:
1. ``db.begin()`` calls in any service module (services/*.py)
2. ``await db.commit()`` calls *inside* a ``db.begin()`` block in any router module

This test has ZERO dependencies on database fixtures or service imports.
Target runtime: < 100 ms.

Allowlist — known-correct "leave-as-is" sites identified in A.44/A.47 audit
(§3.1 of A45_A46_A47_EXPANSION_DESIGN_SPEC.md):

    players.py:428   — bare session + explicit commit, single-row write, no db.begin()
    admin.py:499     — bare session + explicit commit, no db.begin()
    bounties.py:165  — bare session + explicit commit, single-row write, no db.begin()
    discord_message.py:131,200,314 — bare session + explicit commit, single-write ops

None of these violate the invariant because they do NOT have a db.begin() in
the same with-block as their db.commit() calls.
"""

import ast
import pathlib

# ---------------------------------------------------------------------------
# Paths (relative to the tests/ directory root; resolved via __file__)
# ---------------------------------------------------------------------------

_TESTS_DIR = pathlib.Path(__file__).parent
_SRC_DIR = _TESTS_DIR.parent / "src"
_SERVICES_DIR = _SRC_DIR / "services"
_ROUTERS_DIR = _SRC_DIR / "api" / "routers"


# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

# Files in services/ that have NO db parameter at all — skip them entirely.
# These are pure-logic helpers, dataclasses, or constants.
_SERVICES_SKIP = frozenset(
    {
        "_item_type_normalizer.py",
        "combat_models.py",
        "game_constants.py",
        "game_maths.py",
        "map_renderer.py",  # PIL-based, no db.begin()
        "pathfinding_service.py",
        "system_graph_service.py",
        # temperature_service.py — RETIRED rev 0031 (module deleted)
        "division_service.py",
    }
)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _is_db_begin_call(node: ast.expr) -> bool:
    """Return True if ``node`` is a Call of shape ``db.begin()`` or ``session.begin()``.

    Matches: ``<Name>.begin()`` where Name is 'db' or 'session'.

    SCOPE CAVEAT (GAP-A-004):
    This helper catches ``db.begin()`` and ``session.begin()`` Call nodes only.
    It does NOT catch ``db.begin_nested()`` / ``session.begin_nested()`` (SAVEPOINT),
    because ``begin_nested`` has ``attr == "begin_nested"``, not ``"begin"``.
    The SQLAlchemy SAVEPOINT pattern has a different risk profile from full
    transaction management: a SAVEPOINT rolls back to a partial state within an
    already-open outer transaction rather than starting a new top-level transaction.
    If a future refactor introduces ``begin_nested()`` in any service, that
    requires a separate design review about SAVEPOINT semantics and partial-rollback
    behaviour; this invariant test is not the right gate for that pattern.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr != "begin":
        return False
    value = func.value
    if isinstance(value, ast.Name):
        return value.id in ("db", "session")
    return False


def _is_db_commit_await(node: ast.stmt) -> bool:
    """Return True if ``node`` is ``await db.commit()`` or ``await session.commit()``."""
    if not isinstance(node, ast.Expr):
        return False
    expr = node.value
    if not isinstance(expr, ast.Await):
        return False
    call = expr.value
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr != "commit":
        return False
    value = func.value
    if isinstance(value, ast.Name):
        return value.id in ("db", "session")
    return False


def _iter_nodes_with_parents(tree: ast.AST):
    """Yield (node, parent) pairs for every node in the tree."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            yield child, node


# ---------------------------------------------------------------------------
# Test 1: No service calls db.begin()
# ---------------------------------------------------------------------------


def test_no_service_calls_db_begin():
    """Assert that no service module calls db.begin() or session.begin().

    After A.44, transaction ownership belongs exclusively to routers.
    Services must NOT open their own transactions.
    """
    violations: list[str] = []

    for py_file in sorted(_SERVICES_DIR.rglob("*.py")):
        if py_file.name in _SERVICES_SKIP:
            continue

        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_db_begin_call(node):
                violations.append(f"{py_file.relative_to(_SRC_DIR)}:{node.lineno} — db.begin() call found")

    assert not violations, (
        "Option Y invariant violated — service(s) call db.begin():\n"
        + "\n".join(violations)
        + "\n\nFix: remove db.begin() from the service; the router owns the transaction."
    )


# ---------------------------------------------------------------------------
# Test 2: No router commits inside a db.begin() block
# ---------------------------------------------------------------------------


def _collect_async_with_items(with_node: ast.AsyncWith) -> list[ast.expr]:
    """Return the context-manager expressions from an AsyncWith node."""
    return [item.context_expr for item in with_node.items]


def _body_contains_db_commit(body: list[ast.stmt]) -> bool:
    """Return True if the statement list contains an ``await db.commit()`` call.

    Searches only the immediate body (not nested with-blocks).
    """
    for stmt in body:
        if _is_db_commit_await(stmt):
            return True
        # Walk into non-AsyncWith compound statements (if/for/try/etc.)
        for child in ast.iter_child_nodes(stmt):
            if isinstance(child, ast.AsyncWith):
                continue  # do not recurse into nested with-blocks
            for sub in ast.walk(child):
                if isinstance(sub, ast.stmt) and _is_db_commit_await(sub):
                    return True
    return False


def test_no_router_commits_inside_db_begin_block():
    """Assert that no router has ``await db.commit()`` inside a ``db.begin()`` block.

    The five "leave-as-is" sites in routers (players.py:428, admin.py:499, etc.)
    use bare sessions without db.begin() — they are NOT inside a db.begin() block
    and are therefore not captured by this test.

    Only the pattern:
        async with get_db_session() as db, db.begin():
            ...
            await db.commit()   ← VIOLATION
    is detected.
    """
    violations: list[str] = []

    for py_file in sorted(_ROUTERS_DIR.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncWith):
                continue

            # Check if any context manager in this with-block is a db.begin() call
            context_exprs = _collect_async_with_items(node)
            has_db_begin = any(_is_db_begin_call(expr) for expr in context_exprs)
            if not has_db_begin:
                continue

            # This is an ``async with ..., db.begin():`` block.
            # Check its body for ``await db.commit()`` calls.
            if _body_contains_db_commit(node.body):
                violations.append(
                    f"{py_file.relative_to(_SRC_DIR)}:{node.lineno} — await db.commit() found inside db.begin() block"
                )

    assert not violations, (
        "Option Y invariant violated — router(s) call db.commit() inside db.begin():\n"
        + "\n".join(violations)
        + "\n\nFix: remove the explicit db.commit(); the router-owned db.begin() commits on exit."
    )
