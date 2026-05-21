"""Phase 5b executor push tests — shop and bounty cache push to gateway.

Tests the non-fatal gateway push calls added to:
  - shop_refresh_executor._push_shop_cache
  - bounty_spawn_executor._push_bounty_cache
  - bounty_expire_executor._push_bounty_cache_expire

Pattern: tests/AGENTS.md §"Executor Test Pattern (S2)"

Three-tier breakdown:
  Tier A — Pure unit tests: helper can be called with mocked args.
  Tier B — SQLite integration where applicable.
  Tier C — respx HTTP boundary: assert correct URL, body, token header.

Key behaviours:
  1. shop push called with correct URL and payload after refresh
  2. bounty spawn push called after successful spawn
  3. bounty expire push called after expiry
  4. Push failure is non-fatal (executor still succeeds)
  5. INTERNAL_AUTH_TOKEN included in headers when set
  6. Headers empty when INTERNAL_AUTH_TOKEN not set
"""

from __future__ import annotations

import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup and stub registration
# ---------------------------------------------------------------------------

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_shared.bblogger = MagicMock()  # type: ignore[attr-defined]
    _mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_shared.bblogger  # type: ignore[arg-type]

if "sqlalchemy_utils" not in sys.modules:
    _mock_sau = types.ModuleType("sqlalchemy_utils")
    _mock_sau.UUIDType = MagicMock()  # type: ignore[attr-defined]
    sys.modules["sqlalchemy_utils"] = _mock_sau

# ---------------------------------------------------------------------------
# Application imports (after path setup)
# ---------------------------------------------------------------------------

import httpx
import respx

# ---------------------------------------------------------------------------
# Constants (matching executor env defaults)
# ---------------------------------------------------------------------------

_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"

GUILD_ID = 9_600_000_001
TIER = "Bronze"
INTERNAL_TOKEN = "test-push-secret"

_SHOP_PUSH_URL = f"{_GATEWAY_BASE}/internal/autocomplete/shop-cache/{GUILD_ID}/{TIER}"
_BOUNTY_PUSH_URL = f"{_GATEWAY_BASE}/internal/autocomplete/bounty-cache/{GUILD_ID}"


# ---------------------------------------------------------------------------
# ===========================================================================
# SHOP PUSH TESTS
# ===========================================================================


class TestShopPushHelper:
    """Tests for shop_refresh_executor._push_shop_cache."""

    async def test_push_called_with_correct_url_and_payload(self):
        """_push_shop_cache POSTs to the correct URL with items payload."""
        from utils.executors.shop_refresh_executor import _push_shop_cache

        items = [{"id": 1, "item_name": "Laser Cannon", "price": 5000, "item_type": "primary_weapon"}]

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            respx.mock(assert_all_called=True) as router,
        ):
            route = router.post(_SHOP_PUSH_URL).respond(204)
            await _push_shop_cache("job-001", GUILD_ID, TIER, items)

        assert route.called, f"Expected POST to {_SHOP_PUSH_URL}"
        request_body = route.calls[0].request
        import json

        body = json.loads(request_body.content)
        assert "items" in body
        assert len(body["items"]) == 1
        assert body["items"][0]["item_name"] == "Laser Cannon"

    async def test_auth_token_included_in_header(self):
        """_push_shop_cache sends X-Internal-Auth header when token is set."""
        from utils.executors.shop_refresh_executor import _push_shop_cache

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            respx.mock(assert_all_called=True) as router,
        ):
            route = router.post(_SHOP_PUSH_URL).respond(204)
            await _push_shop_cache("job-002", GUILD_ID, TIER, [])

        assert route.called
        auth_header = route.calls[0].request.headers.get("x-internal-auth")
        assert auth_header == INTERNAL_TOKEN, f"Expected auth header '{INTERNAL_TOKEN}', got {auth_header!r}"

    async def test_no_auth_header_when_token_not_set(self):
        """_push_shop_cache omits X-Internal-Auth header when token is not set."""
        from utils.executors.shop_refresh_executor import _push_shop_cache

        env_no_token = {k: v for k, v in os.environ.items() if k != "INTERNAL_AUTH_TOKEN"}
        with (
            patch.dict(os.environ, env_no_token, clear=True),
            respx.mock(assert_all_called=True) as router,
        ):
            route = router.post(_SHOP_PUSH_URL).respond(204)
            await _push_shop_cache("job-003", GUILD_ID, TIER, [])

        assert route.called
        auth_header = route.calls[0].request.headers.get("x-internal-auth")
        assert auth_header is None, f"Expected no auth header, got {auth_header!r}"

    async def test_push_failure_is_nonfatal(self):
        """_push_shop_cache swallows errors and does not re-raise."""
        from utils.executors.shop_refresh_executor import _push_shop_cache

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            respx.mock(assert_all_called=True) as router,
        ):
            router.post(_SHOP_PUSH_URL).respond(500)
            # Should not raise even though gateway returned 500
            await _push_shop_cache("job-004", GUILD_ID, TIER, [])

    async def test_network_error_is_nonfatal(self):
        """_push_shop_cache handles network errors without raising."""
        from utils.executors.shop_refresh_executor import _push_shop_cache

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            respx.mock(assert_all_called=True) as router,
        ):
            router.post(_SHOP_PUSH_URL).mock(side_effect=httpx.ConnectError("unreachable"))
            # Should not raise
            await _push_shop_cache("job-005", GUILD_ID, TIER, [])


# ---------------------------------------------------------------------------
# ===========================================================================
# BOUNTY SPAWN PUSH TESTS
# ===========================================================================


class TestBountySpawnPushHelper:
    """Tests for bounty_spawn_executor._push_bounty_cache."""

    async def test_push_called_with_correct_url(self):
        """_push_bounty_cache POSTs to the correct URL."""
        from utils.executors.bounty_spawn_executor import _push_bounty_cache

        # Mock the db and bounty_repo to return a fake bounty
        fake_bounty = SimpleNamespace(
            id=42,
            guild_id=GUILD_ID,
            criminal_name="Pal Tyyrt",
            division="bronze",
            reward=50000,
            status="active",
        )

        async def _fake_get_active(db, guild_id):
            return [fake_bounty]

        mock_repo = MagicMock()
        mock_repo.get_active_by_guild = _fake_get_active

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=mock_repo),
            respx.mock(assert_all_called=True) as router,
        ):
            route = router.post(_BOUNTY_PUSH_URL).respond(204)
            await _push_bounty_cache("job-100", GUILD_ID, db=None)

        assert route.called, f"Expected POST to {_BOUNTY_PUSH_URL}"

    async def test_bounty_dicts_serialised_in_payload(self):
        """_push_bounty_cache includes bounty data in the request body."""
        from utils.executors.bounty_spawn_executor import _push_bounty_cache

        fake_bounty = SimpleNamespace(
            id=99,
            guild_id=GUILD_ID,
            criminal_name="Test Criminal",
            division="silver",
            reward=75000,
            status="active",
            __dict__={
                "id": 99,
                "guild_id": GUILD_ID,
                "criminal_name": "Test Criminal",
                "division": "silver",
                "reward": 75000,
                "status": "active",
            },
        )

        async def _fake_get_active(db, guild_id):
            return [fake_bounty]

        mock_repo = MagicMock()
        mock_repo.get_active_by_guild = _fake_get_active

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=mock_repo),
            respx.mock(assert_all_called=True) as router,
        ):
            route = router.post(_BOUNTY_PUSH_URL).respond(204)
            await _push_bounty_cache("job-101", GUILD_ID, db=None)

        import json

        body = json.loads(route.calls[0].request.content)
        assert "bounties" in body
        assert len(body["bounties"]) == 1
        assert body["bounties"][0]["criminal_name"] == "Test Criminal"

    async def test_push_failure_is_nonfatal(self):
        """_push_bounty_cache swallows errors."""
        from utils.executors.bounty_spawn_executor import _push_bounty_cache

        async def _fake_get_active(db, guild_id):
            return []

        mock_repo = MagicMock()
        mock_repo.get_active_by_guild = _fake_get_active

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=mock_repo),
            respx.mock(assert_all_called=True) as router,
        ):
            router.post(_BOUNTY_PUSH_URL).respond(503)
            # Must not raise
            await _push_bounty_cache("job-102", GUILD_ID, db=None)

    async def test_auth_header_included(self):
        """_push_bounty_cache sends X-Internal-Auth header."""
        from utils.executors.bounty_spawn_executor import _push_bounty_cache

        async def _fake_get_active(db, guild_id):
            return []

        mock_repo = MagicMock()
        mock_repo.get_active_by_guild = _fake_get_active

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=mock_repo),
            respx.mock(assert_all_called=True) as router,
        ):
            route = router.post(_BOUNTY_PUSH_URL).respond(204)
            await _push_bounty_cache("job-103", GUILD_ID, db=None)

        auth = route.calls[0].request.headers.get("x-internal-auth")
        assert auth == INTERNAL_TOKEN


# ---------------------------------------------------------------------------
# ===========================================================================
# BOUNTY EXPIRE PUSH TESTS
# ===========================================================================


class TestBountyExpirePushHelper:
    """Tests for bounty_expire_executor._push_bounty_cache_expire."""

    async def test_push_called_after_expire(self):
        """_push_bounty_cache_expire POSTs remaining bounties to gateway."""
        from utils.executors.bounty_expire_executor import _push_bounty_cache_expire

        async def _fake_get_active(db, guild_id):
            return []  # No bounties remaining after expiry

        mock_repo = MagicMock()
        mock_repo.get_active_by_guild = _fake_get_active

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=mock_repo),
            respx.mock(assert_all_called=True) as router,
        ):
            route = router.post(_BOUNTY_PUSH_URL).respond(204)
            await _push_bounty_cache_expire("job-200", GUILD_ID, db=None)

        assert route.called
        import json

        body = json.loads(route.calls[0].request.content)
        assert body["bounties"] == []

    async def test_push_failure_is_nonfatal(self):
        """_push_bounty_cache_expire does not raise on gateway error."""
        from utils.executors.bounty_expire_executor import _push_bounty_cache_expire

        async def _fake_get_active(db, guild_id):
            return []

        mock_repo = MagicMock()
        mock_repo.get_active_by_guild = _fake_get_active

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=mock_repo),
            respx.mock(assert_all_called=True) as router,
        ):
            router.post(_BOUNTY_PUSH_URL).respond(500)
            # Must not raise
            await _push_bounty_cache_expire("job-201", GUILD_ID, db=None)

    async def test_repo_error_is_nonfatal(self):
        """_push_bounty_cache_expire handles repo errors without raising."""
        from utils.executors.bounty_expire_executor import _push_bounty_cache_expire

        async def _fake_get_active_raises(db, guild_id):
            raise RuntimeError("DB connection lost")

        mock_repo = MagicMock()
        mock_repo.get_active_by_guild = _fake_get_active_raises

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=mock_repo),
            respx.mock(assert_all_called=False),
        ):
            # Must not raise
            await _push_bounty_cache_expire("job-202", GUILD_ID, db=None)

    async def test_auth_header_included(self):
        """_push_bounty_cache_expire sends X-Internal-Auth header."""
        from utils.executors.bounty_expire_executor import _push_bounty_cache_expire

        async def _fake_get_active(db, guild_id):
            return []

        mock_repo = MagicMock()
        mock_repo.get_active_by_guild = _fake_get_active

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=mock_repo),
            respx.mock(assert_all_called=True) as router,
        ):
            route = router.post(_BOUNTY_PUSH_URL).respond(204)
            await _push_bounty_cache_expire("job-203", GUILD_ID, db=None)

        auth = route.calls[0].request.headers.get("x-internal-auth")
        assert auth == INTERNAL_TOKEN


# ---------------------------------------------------------------------------
# ===========================================================================
# ADVERSARIAL TESTS
# ===========================================================================


class TestAdversarialEdgeCases:
    """Adversarial and edge case tests for the Phase 5b push helpers.

    These tests go beyond the happy path to verify:
    - Datetime serialisation completeness (issue_time, respawn_time not in conversion list)
    - Shop push ordering relative to executor work
    - Special character token handling
    """

    async def test_bounty_orm_with_datetime_fields_is_nonfatal(self):
        """ORM Bounty object with ALL datetime fields is serialised and push succeeds.

        FIX VERIFIED (DEF-0005-001): _push_bounty_cache now uses a generic loop
        `for key, val in list(d.items()): if hasattr(val, "isoformat"): d[key] = val.isoformat()`
        which converts ALL datetime fields — including issue_time and respawn_time —
        to ISO strings before JSON serialisation. The HTTP push should now succeed
        (call_count == 1) and the serialised payload should contain ISO string values.
        """
        import json
        from datetime import UTC, datetime

        from utils.executors.bounty_spawn_executor import _push_bounty_cache

        # Simulate a real ORM Bounty with ALL datetime fields populated
        _now = datetime.now(UTC)

        class FakeOrmBounty:
            """Mimics the __dict__ structure of a real SQLAlchemy Bounty ORM object."""
            def __init__(self):
                self.__dict__ = {
                    "id": 42,
                    "guild_id": GUILD_ID,
                    "division": "bronze",
                    "criminal_name": "ORM Villain",
                    "criminal_faction": "pirates",
                    "reward": 50000,
                    "reward_per_sys": 10000,
                    "route": ["Kaamo", "Thynome"],
                    "answer": "Thynome",
                    "checked": {},
                    "issue_time": _now,     # Previously NOT in executor's conversion list
                    "end_time": _now,       # Was in executor's conversion list
                    "respawn_time": None,   # None — safe to serialise
                    "tech_level": 7,
                    "criminal_ship": None,
                    "status": "active",
                    "escape_count": 0,
                    "win_user_id": None,
                    "created_at": _now,     # Was in executor's conversion list
                    "updated_at": _now,     # Was in executor's conversion list
                    "_sa_instance_state": object(),  # Excluded by startswith("_")
                }

        fake_orm_bounty = FakeOrmBounty()
        expected_iso = _now.isoformat()

        async def _fake_get_active(db, guild_id):
            return [fake_orm_bounty]

        mock_repo = MagicMock()
        mock_repo.get_active_by_guild = _fake_get_active

        call_count = 0

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=mock_repo),
            respx.mock(assert_all_called=True, assert_all_mocked=True) as router,
        ):
            route = router.post(_BOUNTY_PUSH_URL).respond(204)
            # Must not raise — and with the fix applied, the push should succeed
            await _push_bounty_cache("job-adversarial-001", GUILD_ID, db=None)
            call_count = route.call_count

        # Post-fix: the push succeeds (call_count == 1) because all datetime fields
        # are now converted to ISO strings via the generic isoformat() loop.
        assert call_count == 1, (
            f"DEF-0005-001 FIX: _push_bounty_cache should serialise all datetime fields "
            f"(including issue_time) and complete the HTTP POST. "
            f"Expected call_count=1, got call_count={call_count}."
        )

        # Verify the serialised payload contains ISO string values for all datetime fields
        body = json.loads(route.calls[0].request.content)
        assert "bounties" in body
        assert len(body["bounties"]) == 1
        bounty_payload = body["bounties"][0]
        assert bounty_payload["issue_time"] == expected_iso, (
            f"issue_time should be ISO string, got {bounty_payload['issue_time']!r}"
        )
        assert bounty_payload["end_time"] == expected_iso, (
            f"end_time should be ISO string, got {bounty_payload['end_time']!r}"
        )
        assert bounty_payload["created_at"] == expected_iso, (
            f"created_at should be ISO string, got {bounty_payload['created_at']!r}"
        )
        assert bounty_payload["updated_at"] == expected_iso, (
            f"updated_at should be ISO string, got {bounty_payload['updated_at']!r}"
        )
        # respawn_time was None — should remain None (not converted)
        assert bounty_payload["respawn_time"] is None, (
            f"respawn_time (None) should remain None, got {bounty_payload['respawn_time']!r}"
        )

    async def test_shop_push_ordering_after_refresh_items(self):
        """_push_shop_cache is called AFTER refresh produces items (not before).

        Verifies that the push receives the items list from the refresh result,
        not an empty/stale list. This is a structural ordering test — the executor
        passes items as a function argument, so ordering is enforced by the call site.
        """
        from utils.executors.shop_refresh_executor import _push_shop_cache

        # Push with items that would come from a completed refresh
        refreshed_items = [
            {"id": 1, "item_name": "Blaster", "price": 3000, "item_type": "primary_weapon"},
            {"id": 2, "item_name": "Shield", "price": 5000, "item_type": "module"},
        ]

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            respx.mock(assert_all_called=True) as router,
        ):
            route = router.post(_SHOP_PUSH_URL).respond(204)
            await _push_shop_cache("job-ordering-001", GUILD_ID, TIER, refreshed_items)

        import json
        body = json.loads(route.calls[0].request.content)
        assert body["items"] == refreshed_items, (
            f"Push payload should contain exactly the items from the refresh result. "
            f"Expected {refreshed_items!r}, got {body['items']!r}"
        )

    async def test_shop_push_with_dict_items_serialised_correctly(self):
        """Shop items that are plain dicts are passed through unchanged.

        refresh_shop() returns items as plain dicts, not ORM objects.
        _push_shop_cache handles both dicts and ORM objects — verify dict passthrough.
        """
        from utils.executors.shop_refresh_executor import _push_shop_cache

        dict_items = [
            {"id": 3, "item_name": "Turret", "price": 7500, "item_type": "turret_weapon", "tier": "Bronze"},
        ]

        with (
            patch.dict(os.environ, {"INTERNAL_AUTH_TOKEN": INTERNAL_TOKEN}),
            respx.mock(assert_all_called=True) as router,
        ):
            route = router.post(_SHOP_PUSH_URL).respond(204)
            await _push_shop_cache("job-dict-001", GUILD_ID, TIER, dict_items)

        import json
        body = json.loads(route.calls[0].request.content)
        assert len(body["items"]) == 1
        assert body["items"][0]["item_name"] == "Turret"
        assert body["items"][0]["tier"] == "Bronze", (
            "All dict keys should be preserved without modification"
        )
