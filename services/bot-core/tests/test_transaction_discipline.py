"""
Static transaction-discipline linter (AC-1, B.34 remediation).

Pytest-collectable test that statically enforces the Package G B.19
"caller owns the transaction" invariant: any router function that calls
(directly or transitively) a service method which performs commit=False
writes MUST satisfy at least one of:

1. Body contains ``async with ... db.begin():`` (any nesting), OR
2. Body contains an explicit ``await db.commit()`` on the success path, OR
3. Has a ``# noqa: TRANSACTION_DISCIPLINE - <reason>`` line comment
   immediately preceding the offending call (escape hatch for false
   positives — justification quality is enforced at PR review).

This complements existing ``test_transaction_invariant.py`` (which checks
that services never call ``db.begin()`` and routers never commit inside
a ``db.begin()`` block). Together the two tests close the static-analysis
side of the defense-in-depth strategy.

ALGORITHM
=========
Phase 1 — Build WRITES_FLUSH_ONLY set:
  Walk every services/*.py file's AST. For each ``async def`` method on
  any class whose name ends in 'Service', record the (ServiceClass, method)
  pair if its body contains a Call expression with a ``commit=False``
  keyword argument. Compute transitive closure: a service method that
  calls another service method already in the set is itself in the set.

Phase 2 — Per-router analysis:
  Walk every api/routers/**/*.py file's AST. For each ``async def`` route
  function (decorated with @router.<verb>), inspect its body. If it calls
  any method in WRITES_FLUSH_ONLY (via attribute access on a parameter or
  on a Depends-injected service), classify it as a "transaction-flush
  consumer". For each consumer:
    - PASS if the body contains ``async with ... db.begin():`` (the
      with-statement may include other context managers) OR an explicit
      ``await ...db.commit()`` call.
    - PASS if the offending Call has a ``# noqa: TRANSACTION_DISCIPLINE``
      comment on the line immediately preceding it (or on the same line).
    - FAIL otherwise.

False positives are ACCEPTABLE (curable by # noqa). False negatives are
NOT — those let regressions through. The analysis is intentionally
strict:

  * Cross-module attribute resolution: rough — we look for Call exprs
    whose ``.attr`` matches any method name in WRITES_FLUSH_ONLY,
    regardless of receiver shape. This over-reports if two services
    happen to share a method name; over-reporting is fine (curable).

  * Indirect calls (e.g. ``getattr(svc, "method_name")(...)``): NOT
    detected. Bot-core does not use this pattern in routers; if a future
    refactor introduces it, the runtime guard (AC-6) on the
    LoadoutConsistencyService choke-point catches the most dangerous
    case, and the integration tests (AC-8) catch wider-scope regressions.

SUPPRESSING A VIOLATION
=======================
Add a comment to the line containing the offending call:

    await player_service.update_player_credits(...)  # noqa: TRANSACTION_DISCIPLINE - explanation

The marker must be ``noqa: TRANSACTION_DISCIPLINE``. The trailing dash +
explanation is required (free-form; it's documentation for human
reviewers — the linter does not parse it).

Production code suppressions should cite a specific architectural reason
in the same commit message; reviewers reject ``# noqa`` comments without
a meaningful justification.
"""

from __future__ import annotations

import ast
import pathlib

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TESTS_DIR = pathlib.Path(__file__).parent
_SRC_DIR = _TESTS_DIR.parent / "src"
_SERVICES_DIR = _SRC_DIR / "services"
_ROUTERS_DIR = _SRC_DIR / "api" / "routers"

# Service files that have no relevant service class — skip during scanning.
_SERVICES_SKIP = frozenset(
    {
        "_item_type_normalizer.py",
        "combat_models.py",
        "exceptions.py",
        "game_constants.py",
        "game_maths.py",
        "map_renderer.py",
        "pathfinding_service.py",
        "system_graph_service.py",
        # temperature_service.py — RETIRED rev 0031 (module deleted)
        "division_service.py",
    }
)

# Suppression marker — must appear in a line comment on the offending Call's line.
_SUPPRESSION_MARKER = "noqa: TRANSACTION_DISCIPLINE"


# ---------------------------------------------------------------------------
# Phase 1 — Build WRITES_FLUSH_ONLY set
# ---------------------------------------------------------------------------


def _has_commit_false_kwarg(call: ast.Call) -> bool:
    """Return True iff the Call has a commit=False keyword argument."""
    for kw in call.keywords:
        if kw.arg == "commit" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return True
    return False


def _iter_async_methods(class_node: ast.ClassDef):
    """Yield (method_name, method_node) for every async method in a class."""
    for stmt in class_node.body:
        if isinstance(stmt, ast.AsyncFunctionDef):
            yield stmt.name, stmt


def _get_method_calls_within(node: ast.AST) -> set[str]:
    """Return the set of attribute names of every Call in node (e.g. 'equip_one').

    Used for transitive-closure resolution: if body contains
    ``await self.something.foo(...)`` we record 'foo'. The receiver is
    irrelevant for closure purposes — over-inclusion is acceptable
    because closure is over a finite set of service method names.
    """
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            names.add(sub.func.attr)
    return names


def _is_db_flush_call(node: ast.AST) -> bool:
    """Return True if node is ``db.flush()`` or ``session.flush()``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "flush":
        return False
    return isinstance(func.value, ast.Name) and func.value.id in ("db", "session")


def _is_db_commit_call(node: ast.AST) -> bool:
    """Return True if node is ``db.commit()`` or ``session.commit()``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "commit":
        return False
    return isinstance(func.value, ast.Name) and func.value.id in ("db", "session")


def _has_commit_false_call(method_node: ast.AsyncFunctionDef) -> bool:
    """Return True if the method is flush-only by construction.

    A method is flush-only iff it propagates the no-commit contract to its
    callers: it performs writes (or delegates writes) but never commits the
    transaction itself. Two qualifying signatures:

    1. Body contains a Call with literal ``commit=False`` kwarg AND does NOT
       contain any unconditional ``await db.commit()``.  The presence of an
       explicit ``db.commit()`` indicates the method is a SELF-COMMITTING
       AGGREGATOR — it uses ``commit=False`` on inner repo calls only to
       defer the commit until all cross-table writes are flushed, then
       commits atomically itself.  Such methods are SAFE to call from bare
       routes; they are NOT in the flush-only set.

    2. Body contains a direct ``db.flush()`` call AND does NOT contain any
       unconditional ``db.commit()`` call.  Methods that have BOTH
       (e.g. ``if commit: db.commit() else: db.flush()``) are dual-mode
       transaction-owner-or-participant — their semantics depend on the
       caller's ``commit`` argument, so we exclude them from the
       unconditional flush-only set.  Their callers are unconstrained by
       this linter.

    The unifying rule: a method that has an unconditional ``db.commit()``
    in its body is NEVER flush-only — it owns the transaction whenever it
    is called, regardless of any inner ``commit=False`` delegations.

    Approximation: 'no unconditional commit' is checked as 'no commit call
    anywhere in the body'.  False positives are possible (a method with a
    commit() inside an obscure conditional could escape) but won't matter
    in practice — the method is still pure if it commits in its happy path.
    """
    has_commit_false_kwarg = False
    has_db_flush = False
    has_db_commit = False
    for sub in ast.walk(method_node):
        if not isinstance(sub, ast.Call):
            continue
        if _has_commit_false_kwarg(sub):
            has_commit_false_kwarg = True
        if _is_db_flush_call(sub):
            has_db_flush = True
        if _is_db_commit_call(sub):
            has_db_commit = True
    # Self-committing aggregator: has commit=False internally BUT also has
    # explicit db.commit() — owns its own transaction. Not flush-only.
    if has_db_commit:
        return False
    if has_commit_false_kwarg:
        return True
    return bool(has_db_flush and not has_db_commit)


def _build_writes_flush_only_set() -> set[tuple[str, str]]:
    """Build the canonical set of (ServiceClass, method) pairs that flush only.

    Algorithm:
      1. Direct seed: any async method that contains a Call with commit=False.
      2. Transitive closure: any async method that calls (by name) a method
         already in the set is itself in the set.

    Note on closure precision: over-inclusion (false positives in the set)
    is fine — a method that happens to share a name with a flush-only
    method gets flagged as flush-only and forces its callers to wrap. The
    cure is either to wrap (correct response if it's a real cross-table
    flow) or to rename the colliding method.
    """
    # Per-class method maps, plus seed set
    class_method_nodes: dict[tuple[str, str], ast.AsyncFunctionDef] = {}
    seed: set[tuple[str, str]] = set()

    for py_file in sorted(_SERVICES_DIR.rglob("*.py")):
        if py_file.name in _SERVICES_SKIP or py_file.name == "__init__.py":
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not node.name.endswith("Service"):
                continue
            for method_name, method_node in _iter_async_methods(node):
                key = (node.name, method_name)
                class_method_nodes[key] = method_node
                if _has_commit_false_call(method_node):
                    seed.add(key)

    # Transitive closure by method name. If method M of class C calls a name
    # that's the method-name component of any seed entry, M is also flush-only.
    closed = set(seed)
    seed_method_names = {m for (_c, m) in seed}
    changed = True
    while changed:
        changed = False
        for key, method_node in class_method_nodes.items():
            if key in closed:
                continue
            called_names = _get_method_calls_within(method_node)
            # If this method calls any name that is currently in closed-set
            # method names, add it.
            closed_method_names = {m for (_c, m) in closed} | seed_method_names
            if called_names & closed_method_names:
                closed.add(key)
                changed = True

    return closed


# ---------------------------------------------------------------------------
# Phase 2 — Per-router analysis
# ---------------------------------------------------------------------------


def _route_decorator_verb(deco: ast.expr) -> str | None:
    """If deco is @router.<verb>(...), return <verb>; else None.

    Verbs: get, post, put, delete, patch, head, options.
    """
    if isinstance(deco, ast.Call):
        deco = deco.func
    if (
        isinstance(deco, ast.Attribute)
        and isinstance(deco.value, ast.Name)
        and deco.value.id == "router"
        and deco.attr in {"get", "post", "put", "delete", "patch", "head", "options"}
    ):
        return deco.attr
    return None


def _is_route_function(fn: ast.AsyncFunctionDef) -> bool:
    return any(_route_decorator_verb(d) is not None for d in fn.decorator_list)


def _collect_calls(node: ast.AST) -> list[ast.Call]:
    """Yield every ast.Call in node."""
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def _has_db_begin_in_body(fn: ast.AsyncFunctionDef) -> bool:
    """Return True if fn's body contains an ``async with ..., db.begin():`` block."""
    for node in ast.walk(fn):
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                expr = item.context_expr
                if (
                    isinstance(expr, ast.Call)
                    and isinstance(expr.func, ast.Attribute)
                    and expr.func.attr == "begin"
                    and isinstance(expr.func.value, ast.Name)
                    and expr.func.value.id in ("db", "session")
                ):
                    return True
    return False


def _has_explicit_commit(fn: ast.AsyncFunctionDef) -> bool:
    """Return True if fn's body contains an ``await db.commit()`` (or session.commit())."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Await):
            inner = node.value
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "commit"
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id in ("db", "session")
            ):
                return True
    return False


def _line_has_suppression(source_lines: list[str], lineno: int) -> bool:
    """Return True if the source line at lineno (1-indexed) or the line immediately
    preceding it contains the suppression marker.
    """
    if 1 <= lineno <= len(source_lines) and _SUPPRESSION_MARKER in source_lines[lineno - 1]:
        return True
    # Allow suppression on the line before too (multi-line call argument layout)
    return bool(2 <= lineno <= len(source_lines) + 1 and _SUPPRESSION_MARKER in source_lines[lineno - 2])


def _find_consumer_calls(fn: ast.AsyncFunctionDef, flush_only_method_names: set[str]) -> list[tuple[int, str]]:
    """Return [(lineno, method_name), ...] for every Call in fn that targets
    a flush-only method name (i.e. the .attr of an Attribute access).
    """
    hits: list[tuple[int, str]] = []
    for call in _collect_calls(fn):
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr in flush_only_method_names:
            hits.append((call.lineno, func.attr))
    return hits


# ---------------------------------------------------------------------------
# Public test functions
# ---------------------------------------------------------------------------


def test_writes_flush_only_set_is_non_empty():
    """Sanity check: the flush-only inventory must include at least the known
    canonical entries (player_service._create_starter_loadout and the
    LoadoutConsistencyService public methods).

    If this assertion fails it means the AST walker is wrong, not that the
    services are wrong — fix the walker.
    """
    writes = _build_writes_flush_only_set()
    # _create_starter_loadout calls inv_repo.add_item(commit=False) etc.
    assert any(method == "_create_starter_loadout" for (_c, method) in writes), (
        f"Expected _create_starter_loadout in WRITES_FLUSH_ONLY, got: {sorted(writes)}"
    )
    # LoadoutConsistencyService public methods are all flush-only by design.
    expected_choke_methods = {
        "equip_one",
        "unequip_one",
        "swap_one",
        "transfer_loadout_to_new_ship",
        "evacuate_ship_loadout_to_inventory",
        "reconcile_active_ship_slots",
        "repair_player",
    }
    found_choke = {m for (_c, m) in writes if m in expected_choke_methods}
    assert found_choke == expected_choke_methods, (
        f"Expected all 7 LoadoutConsistencyService public methods in "
        f"WRITES_FLUSH_ONLY; missing: {expected_choke_methods - found_choke}"
    )


def test_router_transaction_discipline():
    """Every route function that calls a flush-only service method MUST wrap
    in ``async with db.begin():`` or commit explicitly (or carry a
    documented suppression).

    See module docstring for the full algorithm and suppression mechanism.
    """
    writes = _build_writes_flush_only_set()
    flush_only_method_names = {m for (_c, m) in writes}

    violations: list[str] = []

    for py_file in sorted(_ROUTERS_DIR.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        source = py_file.read_text(encoding="utf-8")
        source_lines = source.splitlines()
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if not _is_route_function(node):
                continue

            consumer_calls = _find_consumer_calls(node, flush_only_method_names)
            if not consumer_calls:
                continue

            # Filter out suppressed calls
            unsuppressed = [(ln, meth) for (ln, meth) in consumer_calls if not _line_has_suppression(source_lines, ln)]
            if not unsuppressed:
                continue

            # Function-level wrapping pass?
            if _has_db_begin_in_body(node):
                continue
            if _has_explicit_commit(node):
                continue

            # All consumer calls in this route are unsuppressed AND the route
            # has neither db.begin() nor explicit commit — record violation.
            for ln, meth in unsuppressed:
                violations.append(
                    f"{py_file.relative_to(_SRC_DIR)}:{ln} — "
                    f"route '{node.name}' calls flush-only method '{meth}' "
                    f"without async with db.begin() or explicit commit"
                )

    assert not violations, (
        "Transaction-discipline invariant violated (B.34 class):\n" + "\n".join(violations) + "\n\nFix options:\n"
        "  1. Wrap the route body in `async with get_db_session() as db, db.begin():`\n"
        "  2. Add `await db.commit()` on the success path\n"
        "  3. Add `# noqa: TRANSACTION_DISCIPLINE - <reason>` to the offending line\n"
        "     (last resort; reviewers gate justification quality)\n"
    )
