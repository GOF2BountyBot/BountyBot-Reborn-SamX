"""Guard against the dual-namespace import foot-gun.

``tests/conftest.py`` puts ``src/`` on ``sys.path`` while ``python -m pytest`` also puts the
bot-core root on ``sys.path``. Because ``src/`` has no ``__init__.py``, a module such as
``services.combat_service`` is importable two ways:

* ``services.combat_service``         -- the namespace production code actually uses, and
* ``src.services.combat_service``     -- a SECOND, distinct module object.

A test that imports / patches via ``src.services.*`` operates on a different module object than
the one production reads, so the patch silently no-ops (a false-green). Production code never uses
the ``src.`` prefix, so tests must not either.

This guard scans every test module and fails if any references a top-level package via the ``src.``
prefix in an ``import`` statement, a dynamic ``importlib.import_module(...)`` call, or an
``unittest.mock.patch(...)`` target. It is intentionally strict so the foot-gun cannot be
reintroduced.
"""

from __future__ import annotations

import re
from pathlib import Path

_TESTS_ROOT = Path(__file__).resolve().parent

# Top-level packages that live under src/ and are importable bare in production.
_PACKAGES = (
    "services",
    "api",
    "utils",
    "persist",
    "repositories",
    "models",
    "core",
    "shared",
    "cogs",
)
_PKG_ALT = "|".join(_PACKAGES)

# ``from src.<pkg>...`` / ``import src.<pkg>...`` (statement form).
_IMPORT_RE = re.compile(rf"^\s*(?:from|import)\s+src\.(?:{_PKG_ALT})\b")
# ``importlib.import_module("src.<pkg>...")`` and ``patch("src.<pkg>...")`` (string-arg form),
# either quote style.
_STRING_RE = re.compile(rf"""(?:import_module|patch)\(\s*['"]src\.(?:{_PKG_ALT})\b""")


def _offending_lines(path: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, text)`` for every line in *path* that uses the ``src.`` prefix.

    Lines whose first non-whitespace character is ``#`` (pure comments) are ignored so that
    explanatory prose mentioning ``src.`` does not trip the guard. Import statements and
    string-arg calls are matched on their code form.
    """
    offenders: list[tuple[int, str]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.lstrip()
        if stripped.startswith("#"):
            continue
        if _IMPORT_RE.search(raw) or _STRING_RE.search(raw):
            offenders.append((lineno, raw.strip()))
    return offenders


def test_no_src_prefixed_namespace_imports() -> None:
    """No test file may import / patch a src-package via the ``src.`` prefix."""
    this_file = Path(__file__).resolve()
    failures: list[str] = []

    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        if path.resolve() == this_file:
            continue  # the guard documents the forbidden pattern in its own docstring/regex
        for lineno, text in _offending_lines(path):
            rel = path.relative_to(_TESTS_ROOT)
            failures.append(f"{rel}:{lineno}: {text}")

    assert not failures, (
        "Dual-namespace import detected. Use the bare production namespace (e.g. "
        "`from services.X import Y`), never the `src.` prefix:\n  " + "\n  ".join(failures)
    )
