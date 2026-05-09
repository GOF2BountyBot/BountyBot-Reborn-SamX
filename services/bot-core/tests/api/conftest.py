"""Shared fixtures and import path setup for API router tests.

This conftest handles the critical issue where tests/api/__init__.py
causes pytest to register tests/api as the 'api' Python package,
which shadows src/api (the real application code). We fix this by:

1. Ensuring src/ is at position 0 in sys.path
2. Purging any stale 'api.*' entries from sys.modules
3. Mocking sqlalchemy_utils (not installed in test env but required
   by persist/models/discord_message.py)

This runs before any test in this directory, ensuring consistent imports.
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# 1. Ensure src/ is first on sys.path
# ---------------------------------------------------------------------------
_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
elif sys.path[0] != _SRC_DIR:
    sys.path.remove(_SRC_DIR)
    sys.path.insert(0, _SRC_DIR)

# ---------------------------------------------------------------------------
# 2. Purge stale api.* modules loaded from tests/api/
# ---------------------------------------------------------------------------
for _key in list(sys.modules):
    if _key == "api" or _key.startswith("api."):
        _mod = sys.modules[_key]
        _file = getattr(_mod, "__file__", "") or ""
        # Keep it only if it lives under src/
        if _SRC_DIR not in _file:
            del sys.modules[_key]

# ---------------------------------------------------------------------------
# 3. Mock sqlalchemy_utils (required transitively by models)
# ---------------------------------------------------------------------------
if "sqlalchemy_utils" not in sys.modules:
    _mock_sqla_utils = types.ModuleType("sqlalchemy_utils")
    _mock_sqla_utils.UUIDType = MagicMock  # type: ignore[attr-defined]
    sys.modules["sqlalchemy_utils"] = _mock_sqla_utils


# ---------------------------------------------------------------------------
# 4. Shared db-session fixture for router tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db_session():
    """Pre-configured mock for get_db_session async context manager.

    Replaces per-test @patch("...get_db_session") decorators.
    Returns (mock_session, mock_cm) tuple so tests can unpack as needed.

    Also configures mock_session.begin() to behave as an async context manager
    so that routers using ``async with get_db_session() as db, db.begin():``
    work correctly after the A.44 transaction-ownership fix.
    """
    from contextlib import asynccontextmanager

    mock_session = AsyncMock()

    @asynccontextmanager
    async def _mock_begin():
        yield

    mock_session.begin = MagicMock(side_effect=lambda: _mock_begin())

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_session, mock_cm
