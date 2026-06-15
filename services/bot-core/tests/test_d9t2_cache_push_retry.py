"""D9-T2 — Tests: bot-core cache-push POSTs are retried (idempotent only).

Adversarial coverage (per D9-T2 tester spec):
  1. A dropped push (transient 503) is retried and converges on success.
  2. A push that fails all 3 attempts is non-fatal (executor still succeeds).
  3. Announce POSTs (_announce_bounty, _announce_shop_refresh, _notify_expiry)
     are NOT wrapped with the retry helper — verified by:
     a. Grepping source to confirm no with_transient_retry around announce calls.
     b. Fault-injection test: simulated 503 on announce → single call (no retry),
        result is propagated to caller as failure (not silently retried).

Design notes
------------
- Uses respx to intercept httpx calls.
- wait strategy patched to wait_none() so tests run instantly.
- Max 2 mocks per test (per repo convention).
- pytestmark = real_push to opt out of autouse _stub_gateway_push_helpers.
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from tenacity import wait_none

# ---------------------------------------------------------------------------
# Path + shared stub setup
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

# conftest.py installs a flat mock 'shared' module.  Load shared.http_retry
# directly from the filesystem so it is importable by the executor modules.
import importlib.util as _ilu

if "shared.http_retry" not in sys.modules:
    _spec = _ilu.spec_from_file_location("shared.http_retry", os.path.join(_SRC_DIR, "shared", "http_retry.py"))
    _mod = _ilu.module_from_spec(_spec)
    sys.modules["shared.http_retry"] = _mod
    _spec.loader.exec_module(_mod)

# Opt out of the autouse `_stub_gateway_push_helpers` fixture
pytestmark = pytest.mark.real_push

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
_GATEWAY_BASE = f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}/api/v1"

GUILD_ID = 9_700_000_001
TIER = "Silver"
PLAYER_ID = 42
_SHOP_PUSH_URL = f"{_GATEWAY_BASE}/internal/autocomplete/shop-cache/{GUILD_ID}/{TIER}"
_BOUNTY_PUSH_URL = f"{_GATEWAY_BASE}/internal/autocomplete/bounty-cache/{GUILD_ID}"
_DUEL_PUSH_URL = f"{_GATEWAY_BASE}/internal/autocomplete/duel-cache/{GUILD_ID}/{PLAYER_ID}"


# ---------------------------------------------------------------------------
# Helper: build a minimal HTTPStatusError
# ---------------------------------------------------------------------------


def _http_err(status: int) -> httpx.HTTPStatusError:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status
    return httpx.HTTPStatusError(f"HTTP {status}", request=MagicMock(), response=mock_resp)


# ===========================================================================
# D9-T2-1: shop cache push is retried on transient failures
# ===========================================================================


class TestShopCachePushRetry:
    async def test_push_retried_on_503_then_succeeds(self):
        """_push_shop_cache retries a 503 and converges on the second call."""
        from utils.executors.shop_refresh_executor import _push_shop_cache

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503)
            return httpx.Response(204)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.post(_SHOP_PUSH_URL).mock(side_effect=side_effect)
            await _push_shop_cache("job-retry-shop", GUILD_ID, TIER, [{"id": 1}])

        assert call_count == 2  # 1 fail + 1 success

    async def test_push_nonfatal_when_all_3_attempts_fail(self):
        """_push_shop_cache is non-fatal even when all 3 retry attempts fail."""
        from utils.executors.shop_refresh_executor import _push_shop_cache

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(503)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.post(_SHOP_PUSH_URL).mock(side_effect=side_effect)
            # Must not raise — non-fatal
            await _push_shop_cache("job-all-fail-shop", GUILD_ID, TIER, [])

        assert call_count == 3  # Exhausted all 3 attempts


# ===========================================================================
# D9-T2-2: bounty spawn cache push is retried on transient failures
# ===========================================================================


class TestBountySpawnCachePushRetry:
    async def test_push_retried_on_503_then_succeeds(self):
        """_push_bounty_cache retries a 503 and converges on the second call."""
        from utils.executors.bounty_spawn_executor import _push_bounty_cache

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503)
            return httpx.Response(204)

        # _push_bounty_cache does a deferred `from persist.repositories.bounty_repository import BountyRepository`
        # inside the function. Patch it at the deferred-import path.
        mock_db = MagicMock()
        mock_repo = MagicMock()
        mock_repo.get_active_by_guild = MagicMock(return_value=[])

        async def mock_get_active(*args, **kwargs):
            return []

        mock_repo.get_active_by_guild = mock_get_active

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=mock_repo),
            respx.mock() as router,
        ):
            router.post(_BOUNTY_PUSH_URL).mock(side_effect=side_effect)
            await _push_bounty_cache("job-retry-spawn", GUILD_ID, mock_db)

        assert call_count == 2

    async def test_push_nonfatal_on_all_failures(self):
        """_push_bounty_cache is non-fatal when all 3 attempts fail."""
        from utils.executors.bounty_spawn_executor import _push_bounty_cache

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(503)

        mock_db = MagicMock()
        mock_repo = MagicMock()

        async def mock_get_active(*args, **kwargs):
            return []

        mock_repo.get_active_by_guild = mock_get_active

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=mock_repo),
            respx.mock() as router,
        ):
            router.post(_BOUNTY_PUSH_URL).mock(side_effect=side_effect)
            # Must not raise
            await _push_bounty_cache("job-all-fail-spawn", GUILD_ID, mock_db)

        assert call_count == 3


# ===========================================================================
# D9-T2-3: bounty expire cache push is retried on transient failures
# ===========================================================================


class TestBountyExpireCachePushRetry:
    async def test_push_retried_on_503_then_succeeds(self):
        """_push_bounty_cache_expire retries a 503 and converges on success."""
        from utils.executors.bounty_expire_executor import _push_bounty_cache_expire

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503)
            return httpx.Response(204)

        mock_db = MagicMock()
        mock_repo = MagicMock()

        async def mock_get_active(*args, **kwargs):
            return []

        mock_repo.get_active_by_guild = mock_get_active

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=mock_repo),
            respx.mock() as router,
        ):
            router.post(_BOUNTY_PUSH_URL).mock(side_effect=side_effect)
            await _push_bounty_cache_expire("job-retry-expire", GUILD_ID, mock_db)

        assert call_count == 2

    async def test_push_nonfatal_on_all_failures(self):
        """_push_bounty_cache_expire is non-fatal when all 3 attempts fail."""
        from utils.executors.bounty_expire_executor import _push_bounty_cache_expire

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(503)

        mock_db = MagicMock()
        mock_repo = MagicMock()

        async def mock_get_active(*args, **kwargs):
            return []

        mock_repo.get_active_by_guild = mock_get_active

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            patch("persist.repositories.bounty_repository.BountyRepository", return_value=mock_repo),
            respx.mock() as router,
        ):
            router.post(_BOUNTY_PUSH_URL).mock(side_effect=side_effect)
            await _push_bounty_cache_expire("job-all-fail-expire", GUILD_ID, mock_db)

        assert call_count == 3


# ===========================================================================
# D9-T2-4: duel expire cache push is retried on transient failures
# ===========================================================================


class TestDuelCachePushRetry:
    async def test_push_retried_on_503_then_succeeds(self):
        """_push_duel_cache retries a 503 and converges on success."""
        from utils.executors.duel_expire_executor import _push_duel_cache

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503)
            return httpx.Response(204)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.post(_DUEL_PUSH_URL).mock(side_effect=side_effect)
            await _push_duel_cache("job-retry-duel", GUILD_ID, PLAYER_ID)

        assert call_count == 2

    async def test_push_nonfatal_on_all_failures(self):
        """_push_duel_cache is non-fatal when all 3 attempts fail."""
        from utils.executors.duel_expire_executor import _push_duel_cache

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(503)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.post(_DUEL_PUSH_URL).mock(side_effect=side_effect)
            await _push_duel_cache("job-all-fail-duel", GUILD_ID, PLAYER_ID)

        assert call_count == 3


# ===========================================================================
# D9-T2-5: Announce / upload POSTs are NOT wrapped in retry helper
# (Grep-based source verification + fault-injection proving no double-post)
# ===========================================================================


class TestAnnounceNotWrapped:
    def test_announce_bounty_not_wrapped_by_grep(self):
        """_announce_bounty must NOT call with_transient_retry (X5 constraint).

        Greps the source of bounty_spawn_executor._announce_bounty to confirm
        the announce POST does not call with_transient_retry.
        """
        import inspect

        from utils.executors.bounty_spawn_executor import _announce_bounty

        src = inspect.getsource(_announce_bounty)
        # The announce POST should NOT use the retry helper
        assert "with_transient_retry" not in src, (
            "_announce_bounty must NOT use with_transient_retry (X5: non-idempotent announce POST)"
        )

    def test_announce_shop_refresh_not_wrapped_by_grep(self):
        """_announce_shop_refresh must NOT call with_transient_retry (X5 constraint)."""
        import inspect

        from utils.executors.shop_refresh_executor import _announce_shop_refresh

        src = inspect.getsource(_announce_shop_refresh)
        assert "with_transient_retry" not in src, (
            "_announce_shop_refresh must NOT use with_transient_retry (X5: non-idempotent announce)"
        )

    def test_notify_expiry_not_wrapped_by_grep(self):
        """_notify_expiry in duel_expire_executor must NOT call with_transient_retry."""
        import inspect

        from utils.executors.duel_expire_executor import _notify_expiry

        src = inspect.getsource(_notify_expiry)
        assert "with_transient_retry" not in src, (
            "_notify_expiry must NOT use with_transient_retry (X5: non-idempotent notification POST)"
        )

    async def test_announce_bounty_503_single_attempt_no_double_post(self):
        """Fault injection: a 503 on the announce POST is NOT retried.

        Proves no double-post: exactly 1 HTTP call is made even on 503.
        The failure propagates as failure_phase='announce' (not silently swallowed).
        """
        from unittest.mock import AsyncMock

        from utils.executors.bounty_spawn_executor import _announce_bounty

        # Build minimal bounty mock
        mock_bounty = MagicMock()
        mock_bounty.id = 1001
        mock_bounty.guild_id = GUILD_ID
        mock_bounty.division = "silver"
        mock_bounty.criminal_name = "Test Criminal"

        mock_config = MagicMock()
        mock_config.silver_bounty_channel_id = 555_000
        mock_config.image_channel_id = None
        mock_config.bounty_hunter_role_id = 777_000
        mock_config.silver_role_id = None

        mock_db = MagicMock()

        announce_call_count = 0
        _ANNOUNCE_URL = f"{_GATEWAY_BASE}/announcements/bounty/channel/555000"

        def announce_side_effect(request):
            nonlocal announce_call_count
            announce_call_count += 1
            return httpx.Response(503)

        # _announce_bounty uses deferred imports; patch at the deferred-import paths
        with (
            respx.mock() as router,
            patch(
                "utils.bounty_announcement_payload.build_bounty_announcement_request",
                AsyncMock(return_value={"type": "bounty", "guild_id": GUILD_ID}),
            ),
            patch(
                "persist.repositories.criminal_repository.CriminalRepository",
                MagicMock(return_value=MagicMock(get_by_name=AsyncMock(return_value=None))),
            ),
        ):
            router.post(_ANNOUNCE_URL).mock(side_effect=announce_side_effect)
            result = await _announce_bounty("job-announce-503", mock_bounty, mock_config, mock_db)

        # The announce POST should have been called exactly ONCE (no retry)
        assert announce_call_count == 1, f"Expected exactly 1 announce attempt (no retry); got {announce_call_count}"
        # Result should indicate announce failure
        assert result["success"] is False
        assert result["failure_phase"] == "announce"


# ===========================================================================
# D9-T2-6: duels router _push_duel_cache is retried on transient failures
# ===========================================================================


class TestDuelsRouterCachePushRetry:
    async def test_push_retried_on_503_then_succeeds(self):
        """duels._push_duel_cache (router) retries a 503 and converges on success."""
        from api.routers.duels import _push_duel_cache

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503)
            return httpx.Response(204)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.post(_DUEL_PUSH_URL).mock(side_effect=side_effect)
            await _push_duel_cache(GUILD_ID, PLAYER_ID, [], [])

        assert call_count == 2  # 1 fail + 1 success

    async def test_push_nonfatal_on_all_failures(self):
        """duels._push_duel_cache (router) is non-fatal when all 3 attempts fail."""
        from api.routers.duels import _push_duel_cache

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(503)

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.post(_DUEL_PUSH_URL).mock(side_effect=side_effect)
            # Must not raise — non-fatal
            await _push_duel_cache(GUILD_ID, PLAYER_ID, [], [])

        assert call_count == 3  # Exhausted all 3 attempts


# ===========================================================================
# D9-T2-7: BountyService._push_bounty_cache_after_capture is retried on
#           transient failures (was the only wrapped site without a retry test)
# ===========================================================================


class TestBountyServiceCachePushAfterCaptureRetry:
    async def test_push_retried_on_503_then_succeeds(self):
        """_push_bounty_cache_after_capture retries a 503 and converges on success."""
        from services.bounty_service import BountyService

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503)
            return httpx.Response(204)

        mock_db = MagicMock()
        mock_repo = MagicMock()

        async def mock_get_active(*args, **kwargs):
            return []

        mock_repo.get_active_by_guild = mock_get_active

        service = BountyService()
        service.bounty_repo = mock_repo

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.post(_BOUNTY_PUSH_URL).mock(side_effect=side_effect)
            await service._push_bounty_cache_after_capture(mock_db, GUILD_ID)

        assert call_count == 2  # 1 fail + 1 success

    async def test_push_nonfatal_on_all_failures(self):
        """_push_bounty_cache_after_capture is non-fatal when all 3 attempts fail."""
        from services.bounty_service import BountyService

        call_count = 0

        def side_effect(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(503)

        mock_db = MagicMock()
        mock_repo = MagicMock()

        async def mock_get_active(*args, **kwargs):
            return []

        mock_repo.get_active_by_guild = mock_get_active

        service = BountyService()
        service.bounty_repo = mock_repo

        with (
            patch("shared.http_retry.TRANSIENT_WAIT", wait_none()),
            respx.mock() as router,
        ):
            router.post(_BOUNTY_PUSH_URL).mock(side_effect=side_effect)
            # Must not raise — non-fatal
            await service._push_bounty_cache_after_capture(mock_db, GUILD_ID)

        assert call_count == 3  # Exhausted all 3 attempts
