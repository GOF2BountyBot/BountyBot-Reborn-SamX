"""Tests for POST /api/v1/internal/autocomplete/events-cache/{guild_id}.

Twin of the bounty-cache push tests in test_internal_autocomplete.py. Covers:
- Valid auth token → 204 and cache updated
- Wrong auth token → 401
- Missing cog → 204 graceful no-op
- EventsCog with _events_cache → 204 and cache updated
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Mock shared.bblogger BEFORE importing application code
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_shared.__path__ = []  # type: ignore[attr-defined]
    _mock_bblogger = types.ModuleType("shared.bblogger")

    def _make_mock_logger(*_args, **_kwargs):
        logger = MagicMock()
        for m in ("info", "debug", "warning", "error", "trace", "critical", "exception"):
            setattr(logger, m, MagicMock())
        return logger

    _mock_bblogger.get_logger = _make_mock_logger  # type: ignore[attr-defined]
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

_SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_TOKEN = "test-internal-secret"
_GUILD_ID = 444555666
_SAMPLE_EVENTS = [
    {
        "id": 1,
        "guild_id": _GUILD_ID,
        "type_slug": "duels_won",
        "type_display": "Duels Won",
        "state": "active",
        "duration_days": 7,
        "params": {},
        "scheduled_start_at": None,
        "started_at": "2026-09-01T00:00:00+00:00",
        "ends_at": "2026-09-08T00:00:00+00:00",
        "prize_count": 2,
    }
]


def _make_mock_autocomplete_cache():
    """Create a simple mock that behaves like AutocompleteCache for set/peek."""
    cache_store: dict = {}

    mock_cache = MagicMock()

    def _set(key, value):
        cache_store[key] = value

    def _peek(key):
        return cache_store.get(key)

    mock_cache.set = _set
    mock_cache.peek = _peek
    mock_cache._store = cache_store
    return mock_cache


def _make_app_with_bot(bot) -> FastAPI:
    """Create a FastAPI test app with the internal_autocomplete router mounted."""
    for k in list(sys.modules.keys()):
        if k in ("api.routers.internal_autocomplete", "api.schemas.internal_schemas"):
            sys.modules.pop(k, None)

    app = FastAPI(title="Test App")
    app.state.bot = bot

    from api.routers.internal_autocomplete import router

    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
def mock_events_cog():
    cog = MagicMock()
    cog._events_cache = _make_mock_autocomplete_cache()
    return cog


@pytest.fixture
def mock_bot_with_events_cog(mock_events_cog):
    bot = MagicMock()
    bot.is_ready.return_value = True

    def _get_cog(name):
        if name == "EventsCog":
            return mock_events_cog
        return None

    bot.get_cog = _get_cog
    return bot


@pytest.fixture
def mock_bot_no_events_cog():
    bot = MagicMock()
    bot.is_ready.return_value = True
    bot.get_cog = MagicMock(return_value=None)
    return bot


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPushEventsCache:
    def test_valid_auth_updates_cache(self, mock_bot_with_events_cog, mock_events_cog):
        """POST with valid auth and events payload → 204 and cache updated."""
        os.environ["INTERNAL_AUTH_TOKEN"] = _VALID_TOKEN
        try:
            app = _make_app_with_bot(mock_bot_with_events_cog)
            client = TestClient(app)
            resp = client.post(
                f"/api/v1/internal/autocomplete/events-cache/{_GUILD_ID}",
                json={"events": _SAMPLE_EVENTS},
                headers={"x-internal-auth": _VALID_TOKEN},
            )
            assert resp.status_code == 204
            # Cache should contain the pushed events (with _norm injected)
            cached = mock_events_cog._events_cache._store.get(_GUILD_ID)
            assert cached is not None
            assert len(cached) == 1
        finally:
            os.environ.pop("INTERNAL_AUTH_TOKEN", None)

    def test_wrong_auth_returns_401(self, mock_bot_with_events_cog):
        """POST with wrong auth → 401."""
        os.environ["INTERNAL_AUTH_TOKEN"] = _VALID_TOKEN
        try:
            app = _make_app_with_bot(mock_bot_with_events_cog)
            client = TestClient(app)
            resp = client.post(
                f"/api/v1/internal/autocomplete/events-cache/{_GUILD_ID}",
                json={"events": []},
                headers={"x-internal-auth": "wrong-token"},
            )
            assert resp.status_code == 401
        finally:
            os.environ.pop("INTERNAL_AUTH_TOKEN", None)

    def test_missing_cog_returns_204_noop(self, mock_bot_no_events_cog):
        """POST when EventsCog not loaded → 204 graceful no-op (not 503)."""
        os.environ.pop("INTERNAL_AUTH_TOKEN", None)
        app = _make_app_with_bot(mock_bot_no_events_cog)
        client = TestClient(app)
        resp = client.post(
            f"/api/v1/internal/autocomplete/events-cache/{_GUILD_ID}",
            json={"events": []},
        )
        assert resp.status_code == 204

    def test_norm_injected_on_push(self, mock_bot_with_events_cog, mock_events_cog):
        """Pushed events get a _norm field computed from label."""
        os.environ.pop("INTERNAL_AUTH_TOKEN", None)
        app = _make_app_with_bot(mock_bot_with_events_cog)
        client = TestClient(app)
        resp = client.post(
            f"/api/v1/internal/autocomplete/events-cache/{_GUILD_ID}",
            json={"events": _SAMPLE_EVENTS},
        )
        assert resp.status_code == 204
        cached = mock_events_cog._events_cache._store.get(_GUILD_ID)
        assert cached is not None
        assert "_norm" in cached[0]
        assert isinstance(cached[0]["_norm"], str)
        assert len(cached[0]["_norm"]) > 0
