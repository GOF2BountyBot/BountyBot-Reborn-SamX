"""Tests for the internal autocomplete push endpoints (Phase 5b).

Tests:
  POST /api/v1/internal/autocomplete/shop-cache/{guild_id}/{tier}
  POST /api/v1/internal/autocomplete/bounty-cache/{guild_id}

Coverage:
  - Valid auth token → 204 and cache updated
  - Wrong auth token → 401
  - Missing cog → 503 (shop) / 204 no-op (bounty)
  - Bounty cog has no _bounty_cache attr → 204 no-op
  - Bounty cog has _bounty_cache → 204 and cache updated
"""

import os
import sys
import types
from unittest.mock import MagicMock, patch

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

# Ensure the src directory is on the path
_SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_TOKEN = "test-internal-secret"
_GUILD_ID = 111222333
_TIER = "Bronze"
_SAMPLE_ITEMS = [{"id": 1, "item_name": "Laser Cannon", "price": 5000, "item_type": "primary_weapon"}]
_SAMPLE_BOUNTIES = [{"id": 42, "criminal_name": "Pal Tyyrt", "division": "bronze", "reward": 50000}]


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
    # Evict cached module state to ensure clean import
    for k in list(sys.modules.keys()):
        if k in ("api.routers.internal_autocomplete", "api.schemas.internal_schemas"):
            sys.modules.pop(k, None)

    app = FastAPI(title="Test App")
    app.state.bot = bot

    from api.routers.internal_autocomplete import router

    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
def mock_shop_cog():
    """A mock ShopCog with a real-ish _shop_cache."""
    cog = MagicMock()
    cog._shop_cache = _make_mock_autocomplete_cache()
    return cog


@pytest.fixture
def mock_bounty_cog_with_cache():
    """A mock BountyCog that already has a _bounty_cache attribute."""
    cog = MagicMock()
    cog._bounty_cache = _make_mock_autocomplete_cache()
    return cog


@pytest.fixture
def mock_bounty_cog_no_cache():
    """A mock BountyCog that does NOT have a _bounty_cache attribute (Phase 6 not yet)."""
    cog = MagicMock(spec=["bot"])  # spec without _bounty_cache
    return cog


@pytest.fixture
def mock_bot_with_shop_cog(mock_shop_cog):
    """Bot that returns the mock ShopCog when get_cog('ShopCog') is called."""
    bot = MagicMock()
    bot.is_ready.return_value = True

    def _get_cog(name):
        if name == "ShopCog":
            return mock_shop_cog
        return None

    bot.get_cog = _get_cog
    return bot


@pytest.fixture
def mock_bot_no_cog():
    """Bot with no cogs loaded."""
    bot = MagicMock()
    bot.is_ready.return_value = True
    bot.get_cog = MagicMock(return_value=None)
    return bot


# ---------------------------------------------------------------------------
# Tests: POST /internal/autocomplete/shop-cache/{guild_id}/{tier}
# ---------------------------------------------------------------------------


class TestShopCachePush:
    def test_valid_auth_updates_cache_returns_204(self, mock_bot_with_shop_cog, mock_shop_cog):
        """POST with correct X-Internal-Auth → 204 and cache updated."""
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_shop_cog)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.post(
                f"/api/v1/internal/autocomplete/shop-cache/{_GUILD_ID}/{_TIER}",
                json={"items": _SAMPLE_ITEMS},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"
        # Verify the cache was actually updated
        cached = mock_shop_cog._shop_cache.peek((_GUILD_ID, _TIER))
        assert cached == _SAMPLE_ITEMS, f"Expected cache to contain sample items, got {cached!r}"

    def test_wrong_auth_returns_401(self, mock_bot_with_shop_cog):
        """POST with wrong X-Internal-Auth → 401."""
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_shop_cog)
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post(
                f"/api/v1/internal/autocomplete/shop-cache/{_GUILD_ID}/{_TIER}",
                json={"items": _SAMPLE_ITEMS},
                headers={"X-Internal-Auth": "wrong-token"},
            )

        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert "Invalid internal auth token" in resp.json().get("detail", "")

    def test_missing_auth_header_with_token_set_returns_401(self, mock_bot_with_shop_cog):
        """POST with no X-Internal-Auth header when token is configured → 401."""
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_shop_cog)
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post(
                f"/api/v1/internal/autocomplete/shop-cache/{_GUILD_ID}/{_TIER}",
                json={"items": _SAMPLE_ITEMS},
                # No X-Internal-Auth header
            )

        assert resp.status_code == 401, f"Expected 401 when auth header missing, got {resp.status_code}"

    def test_no_cog_returns_503(self, mock_bot_no_cog):
        """POST when ShopCog is not loaded → 503."""
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_no_cog)
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post(
                f"/api/v1/internal/autocomplete/shop-cache/{_GUILD_ID}/{_TIER}",
                json={"items": _SAMPLE_ITEMS},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 503, f"Expected 503 when ShopCog not loaded, got {resp.status_code}"
        assert "ShopCog not loaded" in resp.json().get("detail", "")

    def test_empty_items_list_is_accepted(self, mock_bot_with_shop_cog, mock_shop_cog):
        """POST with empty items list → 204, cache set to empty list."""
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_shop_cog)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.post(
                f"/api/v1/internal/autocomplete/shop-cache/{_GUILD_ID}/{_TIER}",
                json={"items": []},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 204
        cached = mock_shop_cog._shop_cache.peek((_GUILD_ID, _TIER))
        assert cached == [], f"Expected empty list in cache, got {cached!r}"

    def test_dev_mode_no_token_allows_request(self, mock_bot_with_shop_cog, mock_shop_cog):
        """When INTERNAL_AUTH_TOKEN is not set, requests are allowed (dev mode)."""
        env_without_token = {k: v for k, v in os.environ.items() if k != "INTERNAL_AUTH_TOKEN"}
        with patch.dict(os.environ, env_without_token, clear=True):
            app = _make_app_with_bot(mock_bot_with_shop_cog)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.post(
                f"/api/v1/internal/autocomplete/shop-cache/{_GUILD_ID}/{_TIER}",
                json={"items": _SAMPLE_ITEMS},
                # No auth header in dev mode
            )

        assert resp.status_code == 204, f"Expected 204 in dev mode (no token), got {resp.status_code}"


# ---------------------------------------------------------------------------
# Tests: POST /internal/autocomplete/bounty-cache/{guild_id}
# ---------------------------------------------------------------------------


class TestBountyCachePush:
    def test_no_bounty_cache_attr_graceful_noop_returns_204(self, mock_bounty_cog_no_cache):
        """When BountyCog has no _bounty_cache attr, returns 204 (graceful no-op)."""
        bot = MagicMock()
        bot.is_ready.return_value = True
        bot.get_cog = MagicMock(return_value=mock_bounty_cog_no_cache)

        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(bot)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.post(
                f"/api/v1/internal/autocomplete/bounty-cache/{_GUILD_ID}",
                json={"bounties": _SAMPLE_BOUNTIES},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 204, f"Expected 204 graceful no-op, got {resp.status_code}: {resp.text}"

    def test_valid_auth_updates_cache_returns_204(self, mock_bounty_cog_with_cache):
        """POST with correct auth and BountyCog with _bounty_cache → 204 and cache updated."""
        bot = MagicMock()
        bot.is_ready.return_value = True
        bot.get_cog = MagicMock(return_value=mock_bounty_cog_with_cache)

        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(bot)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.post(
                f"/api/v1/internal/autocomplete/bounty-cache/{_GUILD_ID}",
                json={"bounties": _SAMPLE_BOUNTIES},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"
        # Verify cache was updated
        cached = mock_bounty_cog_with_cache._bounty_cache.peek(_GUILD_ID)
        assert cached == _SAMPLE_BOUNTIES, f"Expected bounty cache to be set, got {cached!r}"

    def test_no_cog_loaded_graceful_noop_returns_204(self, mock_bot_no_cog):
        """When BountyCog is not loaded at all → graceful 204 no-op."""
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_no_cog)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.post(
                f"/api/v1/internal/autocomplete/bounty-cache/{_GUILD_ID}",
                json={"bounties": _SAMPLE_BOUNTIES},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 204, f"Expected 204 no-op when BountyCog not loaded, got {resp.status_code}"

    def test_wrong_auth_returns_401(self, mock_bounty_cog_with_cache):
        """POST with wrong auth → 401."""
        bot = MagicMock()
        bot.is_ready.return_value = True
        bot.get_cog = MagicMock(return_value=mock_bounty_cog_with_cache)

        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(bot)
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post(
                f"/api/v1/internal/autocomplete/bounty-cache/{_GUILD_ID}",
                json={"bounties": _SAMPLE_BOUNTIES},
                headers={"X-Internal-Auth": "wrong"},
            )

        assert resp.status_code == 401

    def test_empty_bounty_list_accepted(self, mock_bounty_cog_with_cache):
        """POST with empty bounties list → 204, cache set to empty."""
        bot = MagicMock()
        bot.is_ready.return_value = True
        bot.get_cog = MagicMock(return_value=mock_bounty_cog_with_cache)

        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(bot)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.post(
                f"/api/v1/internal/autocomplete/bounty-cache/{_GUILD_ID}",
                json={"bounties": []},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 204
        cached = mock_bounty_cog_with_cache._bounty_cache.peek(_GUILD_ID)
        assert cached == []


# ---------------------------------------------------------------------------
# Adversarial Tests
# ---------------------------------------------------------------------------


class TestAdversarialEdgeCases:
    """Edge case and adversarial tests for Phase 5b push endpoints."""

    def test_special_char_token_plain_string_compare(self, mock_bot_with_shop_cog, mock_shop_cog):
        """Token with base64 special chars (=, /, +) must work via plain string compare.

        AC-SPEC: _verify_auth does a plain != comparison — no URL-decode, no hashing.
        This test pins that a base64-style token with =, /, + works correctly.
        """
        special_token = "abc=def+/ghi=="
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": special_token}):
            app = _make_app_with_bot(mock_bot_with_shop_cog)
            client = TestClient(app, raise_server_exceptions=True)

            # Correct special-char token → 204
            resp = client.post(
                f"/api/v1/internal/autocomplete/shop-cache/{_GUILD_ID}/{_TIER}",
                json={"items": _SAMPLE_ITEMS},
                headers={"X-Internal-Auth": special_token},
            )
        assert resp.status_code == 204, (
            f"Special-char token should be accepted via plain string compare, got {resp.status_code}"
        )

        # Wrong value → 401
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": special_token}):
            app2 = _make_app_with_bot(mock_bot_with_shop_cog)
            client2 = TestClient(app2, raise_server_exceptions=False)

            resp2 = client2.post(
                f"/api/v1/internal/autocomplete/shop-cache/{_GUILD_ID}/{_TIER}",
                json={"items": _SAMPLE_ITEMS},
                headers={"X-Internal-Auth": "abc=def+/ghi==WRONG"},
            )
        assert resp2.status_code == 401, f"Slightly-off special-char token should be rejected, got {resp2.status_code}"

    def test_ac_warm_3_push_then_peek_returns_new_stock(self, mock_bot_with_shop_cog, mock_shop_cog):
        """AC-WARM-3: After shop push, gateway cache reflects new stock without a GET.

        Simulates: (1) POST to shop-cache endpoint (the push the executor makes),
        (2) verify cache was updated, (3) verify peek returns the new stock.
        This validates the 'no GET required' contract — the gateway reads from cache.
        """
        new_stock = [
            {"id": 10, "item_name": "Plasma Cannon", "price": 9000, "item_type": "primary_weapon"},
            {"id": 11, "item_name": "Stealth Module", "price": 4000, "item_type": "module"},
        ]
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_shop_cog)
            client = TestClient(app, raise_server_exceptions=True)

            # Step 1: Verify cache is empty before push
            assert mock_shop_cog._shop_cache.peek((_GUILD_ID, _TIER)) is None, "Cache should be empty before push"

            # Step 2: POST the push (simulates executor calling after refresh)
            resp = client.post(
                f"/api/v1/internal/autocomplete/shop-cache/{_GUILD_ID}/{_TIER}",
                json={"items": new_stock},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        # Step 3: Assert 204 (push accepted)
        assert resp.status_code == 204, f"Push should return 204, got {resp.status_code}"

        # Step 4: Verify peek returns the new stock without any GET to bot-core
        peeked = mock_shop_cog._shop_cache.peek((_GUILD_ID, _TIER))
        assert peeked is not None, "Cache should be populated after push"
        assert len(peeked) == 2, f"Expected 2 items in cache, got {len(peeked)}"
        assert peeked[0]["item_name"] == "Plasma Cannon", (
            f"First item should be 'Plasma Cannon', got {peeked[0]['item_name']}"
        )
        assert peeked[1]["item_name"] == "Stealth Module", (
            f"Second item should be 'Stealth Module', got {peeked[1]['item_name']}"
        )

    def test_concurrent_guild_pushes_use_last_write_wins(self, mock_bot_with_shop_cog, mock_shop_cog):
        """Concurrent guild pushes use last-write-wins — no deadlock risk.

        Two pushes for the same (guild_id, tier) in sequence: second overwrites first.
        AutocompleteCache.set() has no lock that could cause a deadlock.
        """
        stock_v1 = [{"id": 1, "item_name": "Old Item", "price": 100}]
        stock_v2 = [{"id": 2, "item_name": "New Item", "price": 200}]

        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_shop_cog)
            client = TestClient(app, raise_server_exceptions=True)

            # First push
            resp1 = client.post(
                f"/api/v1/internal/autocomplete/shop-cache/{_GUILD_ID}/{_TIER}",
                json={"items": stock_v1},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )
            assert resp1.status_code == 204

            # Second push (simulates concurrent or sequential second spawn)
            resp2 = client.post(
                f"/api/v1/internal/autocomplete/shop-cache/{_GUILD_ID}/{_TIER}",
                json={"items": stock_v2},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )
            assert resp2.status_code == 204

        # Last write wins — cache should contain stock_v2
        peeked = mock_shop_cog._shop_cache.peek((_GUILD_ID, _TIER))
        assert peeked == stock_v2, f"Last-write-wins: expected stock_v2, got {peeked!r}"

    def test_bounty_cache_push_accepts_string_datetimes(self, mock_bounty_cog_with_cache):
        """BountyCachePush schema accepts string-serialised datetimes (not datetime objects).

        The executor serialises datetime fields to ISO strings before sending.
        The gateway schema uses list[dict] — so any dict is accepted.
        This test verifies that ISO-string datetimes round-trip through the push.
        """
        bounties_with_dates = [
            {
                "id": 1,
                "guild_id": _GUILD_ID,
                "criminal_name": "ISO Villain",
                "division": "bronze",
                "reward": 10000,
                "end_time": "2026-05-16T14:00:00+00:00",  # ISO string from executor
                "created_at": "2026-05-16T12:00:00+00:00",
                "updated_at": "2026-05-16T13:00:00+00:00",
                "issue_time": "2026-05-16T11:00:00+00:00",  # Also ISO string
                "respawn_time": None,
            }
        ]
        bot = MagicMock()
        bot.is_ready.return_value = True
        bot.get_cog = MagicMock(return_value=mock_bounty_cog_with_cache)

        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(bot)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.post(
                f"/api/v1/internal/autocomplete/bounty-cache/{_GUILD_ID}",
                json={"bounties": bounties_with_dates},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"
        cached = mock_bounty_cog_with_cache._bounty_cache.peek(_GUILD_ID)
        assert cached is not None
        assert len(cached) == 1
        assert cached[0]["end_time"] == "2026-05-16T14:00:00+00:00", (
            f"ISO string datetime should be preserved, got {cached[0]['end_time']!r}"
        )
        assert cached[0]["issue_time"] == "2026-05-16T11:00:00+00:00", (
            "issue_time should also be preserved as ISO string"
        )


# ---------------------------------------------------------------------------
# Tests: POST /internal/autocomplete/duel-cache/{guild_id}/{player_id}
# ---------------------------------------------------------------------------

_PLAYER_ID = 77788899
_SAMPLE_PENDING_DUELS = [
    {"id": 1, "challenger_id": 10, "target_id": _PLAYER_ID, "stakes": 500, "challenger_name": "Rando"}
]
_SAMPLE_OUTGOING_DUELS = [{"id": 2, "challenger_id": _PLAYER_ID, "target_id": 20, "stakes": 0, "target_name": "Enemy"}]


def _make_mock_duel_cog():
    """Create a simple mock DuelCog with real-ish duel caches."""
    cog = MagicMock()
    cog._pending_duel_cache = _make_mock_autocomplete_cache()
    cog._outgoing_duel_cache = _make_mock_autocomplete_cache()
    return cog


@pytest.fixture
def mock_duel_cog():
    """A mock DuelCog with real-ish _pending_duel_cache and _outgoing_duel_cache."""
    return _make_mock_duel_cog()


@pytest.fixture
def mock_bot_with_duel_cog(mock_duel_cog):
    """Bot that returns the mock DuelCog when get_cog('DuelCog') is called."""
    bot = MagicMock()
    bot.is_ready.return_value = True

    def _get_cog(name):
        if name == "DuelCog":
            return mock_duel_cog
        return None

    bot.get_cog = _get_cog
    return bot


class TestDuelCachePush:
    """Tests for POST /internal/autocomplete/duel-cache/{guild_id}/{player_id}."""

    def test_valid_auth_updates_both_caches_returns_204(self, mock_bot_with_duel_cog, mock_duel_cog):
        """POST with correct X-Internal-Auth → 204 and both duel caches updated."""
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_duel_cog)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.post(
                f"/api/v1/internal/autocomplete/duel-cache/{_GUILD_ID}/{_PLAYER_ID}",
                json={"pending_duels": _SAMPLE_PENDING_DUELS, "outgoing_duels": _SAMPLE_OUTGOING_DUELS},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"
        # Verify pending cache was updated
        cached_pending = mock_duel_cog._pending_duel_cache.peek((_GUILD_ID, _PLAYER_ID))
        assert cached_pending == _SAMPLE_PENDING_DUELS, f"Pending cache mismatch: {cached_pending!r}"
        # Verify outgoing cache was updated
        cached_outgoing = mock_duel_cog._outgoing_duel_cache.peek((_GUILD_ID, _PLAYER_ID))
        assert cached_outgoing == _SAMPLE_OUTGOING_DUELS, f"Outgoing cache mismatch: {cached_outgoing!r}"

    def test_wrong_auth_returns_401(self, mock_bot_with_duel_cog):
        """POST with wrong X-Internal-Auth → 401."""
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_duel_cog)
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post(
                f"/api/v1/internal/autocomplete/duel-cache/{_GUILD_ID}/{_PLAYER_ID}",
                json={"pending_duels": [], "outgoing_duels": []},
                headers={"X-Internal-Auth": "wrong-token"},
            )

        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert "Invalid internal auth token" in resp.json().get("detail", "")

    def test_missing_auth_header_with_token_set_returns_401(self, mock_bot_with_duel_cog):
        """POST with no X-Internal-Auth header when token is configured → 401."""
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_duel_cog)
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post(
                f"/api/v1/internal/autocomplete/duel-cache/{_GUILD_ID}/{_PLAYER_ID}",
                json={"pending_duels": [], "outgoing_duels": []},
                # No X-Internal-Auth header
            )

        assert resp.status_code == 401, f"Expected 401 when auth header missing, got {resp.status_code}"

    def test_no_duel_cog_returns_503(self, mock_bot_no_cog):
        """POST when DuelCog is not loaded → 503."""
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_no_cog)
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post(
                f"/api/v1/internal/autocomplete/duel-cache/{_GUILD_ID}/{_PLAYER_ID}",
                json={"pending_duels": [], "outgoing_duels": []},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 503, f"Expected 503 when DuelCog not loaded, got {resp.status_code}"
        assert "DuelCog not loaded" in resp.json().get("detail", "")

    def test_dev_mode_no_token_allows_request(self, mock_bot_with_duel_cog, mock_duel_cog):
        """When INTERNAL_AUTH_TOKEN is not set, requests are allowed (dev mode)."""
        env_without_token = {k: v for k, v in os.environ.items() if k != "INTERNAL_AUTH_TOKEN"}
        with patch.dict(os.environ, env_without_token, clear=True):
            app = _make_app_with_bot(mock_bot_with_duel_cog)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.post(
                f"/api/v1/internal/autocomplete/duel-cache/{_GUILD_ID}/{_PLAYER_ID}",
                json={"pending_duels": _SAMPLE_PENDING_DUELS, "outgoing_duels": []},
                # No auth header in dev mode
            )

        assert resp.status_code == 204, f"Expected 204 in dev mode (no token), got {resp.status_code}"

    def test_empty_duel_lists_are_accepted(self, mock_bot_with_duel_cog, mock_duel_cog):
        """POST with empty pending/outgoing lists → 204, caches set to empty lists (sentinel invalidation)."""
        # Pre-populate caches so we can verify they are overwritten
        mock_duel_cog._pending_duel_cache.set((_GUILD_ID, _PLAYER_ID), _SAMPLE_PENDING_DUELS)
        mock_duel_cog._outgoing_duel_cache.set((_GUILD_ID, _PLAYER_ID), _SAMPLE_OUTGOING_DUELS)

        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_duel_cog)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.post(
                f"/api/v1/internal/autocomplete/duel-cache/{_GUILD_ID}/{_PLAYER_ID}",
                json={"pending_duels": [], "outgoing_duels": []},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}"
        # Both caches should be overwritten with empty lists (expiry sentinel)
        assert mock_duel_cog._pending_duel_cache.peek((_GUILD_ID, _PLAYER_ID)) == []
        assert mock_duel_cog._outgoing_duel_cache.peek((_GUILD_ID, _PLAYER_ID)) == []

    def test_push_then_peek_returns_new_duels(self, mock_bot_with_duel_cog, mock_duel_cog):
        """After push, gateway cache reflects new duel state without a GET call.

        Validates the 'no GET required' contract: executor pushes → gateway peek returns updated data.
        """
        fresh_pending = [{"id": 5, "challenger_id": 11, "target_id": _PLAYER_ID, "stakes": 200}]
        fresh_outgoing = [{"id": 6, "challenger_id": _PLAYER_ID, "target_id": 22, "stakes": 0}]

        # Verify cache is empty before push
        assert mock_duel_cog._pending_duel_cache.peek((_GUILD_ID, _PLAYER_ID)) is None

        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_duel_cog)
            client = TestClient(app, raise_server_exceptions=True)

            resp = client.post(
                f"/api/v1/internal/autocomplete/duel-cache/{_GUILD_ID}/{_PLAYER_ID}",
                json={"pending_duels": fresh_pending, "outgoing_duels": fresh_outgoing},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 204
        peeked_pending = mock_duel_cog._pending_duel_cache.peek((_GUILD_ID, _PLAYER_ID))
        assert peeked_pending is not None
        assert peeked_pending[0]["id"] == 5
        peeked_outgoing = mock_duel_cog._outgoing_duel_cache.peek((_GUILD_ID, _PLAYER_ID))
        assert peeked_outgoing is not None
        assert peeked_outgoing[0]["id"] == 6

    def test_schema_rejects_missing_pending_duels_field(self, mock_bot_with_duel_cog):
        """Missing pending_duels field → 422 Pydantic validation error."""
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_duel_cog)
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post(
                f"/api/v1/internal/autocomplete/duel-cache/{_GUILD_ID}/{_PLAYER_ID}",
                json={"outgoing_duels": []},  # missing pending_duels
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 422, f"Expected 422 for missing pending_duels, got {resp.status_code}"

    def test_schema_rejects_missing_outgoing_duels_field(self, mock_bot_with_duel_cog):
        """Missing outgoing_duels field → 422 Pydantic validation error."""
        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_duel_cog)
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post(
                f"/api/v1/internal/autocomplete/duel-cache/{_GUILD_ID}/{_PLAYER_ID}",
                json={"pending_duels": []},  # missing outgoing_duels
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 422, f"Expected 422 for missing outgoing_duels, got {resp.status_code}"

    def test_second_push_overwrites_first(self, mock_bot_with_duel_cog, mock_duel_cog):
        """Second push for same (guild_id, player_id) overwrites the first (last-write-wins)."""
        v1_pending = [{"id": 1, "stakes": 100}]
        v2_pending = [{"id": 2, "stakes": 999}, {"id": 3, "stakes": 0}]

        with patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": _VALID_TOKEN}):
            app = _make_app_with_bot(mock_bot_with_duel_cog)
            client = TestClient(app, raise_server_exceptions=True)

            client.post(
                f"/api/v1/internal/autocomplete/duel-cache/{_GUILD_ID}/{_PLAYER_ID}",
                json={"pending_duels": v1_pending, "outgoing_duels": []},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )
            resp = client.post(
                f"/api/v1/internal/autocomplete/duel-cache/{_GUILD_ID}/{_PLAYER_ID}",
                json={"pending_duels": v2_pending, "outgoing_duels": []},
                headers={"X-Internal-Auth": _VALID_TOKEN},
            )

        assert resp.status_code == 204
        peeked = mock_duel_cog._pending_duel_cache.peek((_GUILD_ID, _PLAYER_ID))
        assert peeked == v2_pending, f"Last-write-wins: expected v2, got {peeked!r}"
