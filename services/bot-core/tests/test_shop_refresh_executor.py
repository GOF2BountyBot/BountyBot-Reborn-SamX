"""
Unit tests for utils.executors.shop_refresh_executor.

Tests verify:
 - single guild + single tier dispatch
 - single guild + all tiers dispatch
 - bulk refresh (all guilds)
 - missing guild_id falls back to bulk-refresh path
 - ShopService errors re-raise
 - correct item counts when ShopService is called (via real config objects)
 - job_executor.py dispatches shop_refresh job_type
 - shop_channel_id=None skips announcement with warning
 - shop_channel_id set causes POST to /channels/{id}/messages
 - _announce_shop_refresh is called once per guild in bulk mode

IMPORTANT: shared.bblogger is mocked BEFORE any source imports (via
conftest.py, with a belt-and-suspenders guard below).

Because shop_refresh_executor uses deferred (in-function) imports, we patch
at the source module level:
  - "persist.database.manager.db_manager"
  - "services.shop_service.ShopService"
  - "persist.repositories.config_repository.ConfigRepository"
We pre-register stub modules in sys.modules so that the deferred imports
inside execute_shop_refresh_job resolve without pulling in real ORM code.
"""

import os as _os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Guard: mock shared / shared.bblogger before importing any source modules.
# conftest.py does this at collection time; we repeat here for standalone runs.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")

    def _make_logger(name: str = "test") -> MagicMock:
        logger = MagicMock()
        for m in ("info", "debug", "warning", "error", "trace", "critical"):
            setattr(logger, m, MagicMock())
        return logger

    _mock_bblogger.get_logger = _make_logger
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure src is on the path
_SRC = _os.path.join(_os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# ---------------------------------------------------------------------------
# Pre-register stub modules so deferred imports in shop_refresh_executor work
# without requiring a live database or installed ORM extras.
# ---------------------------------------------------------------------------


def _ensure_stub(module_path: str, **attrs) -> types.ModuleType:
    """Create and register a stub module if not already present."""
    if module_path not in sys.modules:
        mod = types.ModuleType(module_path)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[module_path] = mod
    return sys.modules[module_path]


# Stub for persist.database.manager — only db_manager attribute needed
_mock_db_mgr_instance = MagicMock()
_db_manager_module = _ensure_stub(
    "persist.database.manager",
    db_manager=_mock_db_mgr_instance,
)

# Stub for persist.repositories.config_repository — ConfigRepository class
_MockConfigRepository = MagicMock()
_config_repo_module = _ensure_stub(
    "persist.repositories.config_repository",
    ConfigRepository=_MockConfigRepository,
)

# Stub for services.shop_service — ShopService class
_MockShopService = MagicMock()
_shop_service_module = _ensure_stub(
    "services.shop_service",
    ShopService=_MockShopService,
)

# Ensure parent packages exist too
_ensure_stub("persist")
_ensure_stub("persist.database")
_ensure_stub("persist.repositories")
_ensure_stub("services")


# ===========================================================================
# Helpers
# ===========================================================================


def _make_guild_config(
    guild_id: int,
    hunting_channel_id: int | None = 234567,
    shop_channel_id: int | None = 345678,
) -> MagicMock:
    cfg = MagicMock()
    cfg.guild_id = guild_id
    cfg.hunting_channel_id = hunting_channel_id
    cfg.shop_channel_id = shop_channel_id
    return cfg


def _make_refresh_result(guild_id: int, tier: str, items_generated: int = 4) -> dict:
    return {
        "guild_id": guild_id,
        "tier": tier,
        "tech_level": 5,
        "items_generated": items_generated,
        "refresh_time": "2026-01-01T00:00:00+00:00",
    }


def _mock_session_ctx(session: AsyncMock) -> MagicMock:
    """Return an async context manager that yields *session*."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _configure_db_manager(mock_db: AsyncMock) -> MagicMock:
    """Configure the stub db_manager to yield *mock_db* on get_session()."""
    mgr = sys.modules["persist.database.manager"].db_manager
    mgr.get_session = MagicMock(return_value=_mock_session_ctx(mock_db))
    return mgr


# ===========================================================================
# Tests: execute_shop_refresh_job — single guild + single tier
# ===========================================================================


@pytest.mark.asyncio
async def test_single_guild_single_tier_calls_refresh_once():
    """Single guild + tier: ShopService.refresh_shop called exactly once."""
    from utils.executors.shop_refresh_executor import execute_shop_refresh_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    mock_shop_svc = AsyncMock()
    mock_shop_svc.refresh_shop = AsyncMock(return_value=_make_refresh_result(111, "Bronze"))
    sys.modules["services.shop_service"].ShopService = MagicMock(return_value=mock_shop_svc)

    result = await execute_shop_refresh_job("job-1", {"job_type": "shop_refresh", "guild_id": 111, "tier": "Bronze"})

    mock_shop_svc.refresh_shop.assert_awaited_once_with(mock_db, 111, "Bronze", None)
    assert result["status"] == "success"
    assert result["guild_id"] == 111
    assert result["tier"] == "Bronze"


@pytest.mark.asyncio
async def test_single_guild_single_tier_passes_force_tech_level():
    """force_tech_level is forwarded to ShopService.refresh_shop."""
    from utils.executors.shop_refresh_executor import execute_shop_refresh_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    mock_shop_svc = AsyncMock()
    mock_shop_svc.refresh_shop = AsyncMock(return_value=_make_refresh_result(222, "Gold"))
    sys.modules["services.shop_service"].ShopService = MagicMock(return_value=mock_shop_svc)

    result = await execute_shop_refresh_job(
        "job-2",
        {
            "job_type": "shop_refresh",
            "guild_id": 222,
            "tier": "Gold",
            "force_tech_level": 7,
        },
    )

    mock_shop_svc.refresh_shop.assert_awaited_once_with(mock_db, 222, "Gold", 7)
    assert result["status"] == "success"


# ===========================================================================
# Tests: execute_shop_refresh_job — single guild, all tiers
# ===========================================================================


@pytest.mark.asyncio
async def test_single_guild_all_tiers_calls_refresh_three_times():
    """guild_id without tier triggers refresh for Bronze, Silver, Gold, Platinum."""
    from utils.executors.shop_refresh_executor import execute_shop_refresh_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    mock_shop_svc = AsyncMock()
    mock_shop_svc.refresh_shop = AsyncMock(
        side_effect=[
            _make_refresh_result(333, "Bronze"),
            _make_refresh_result(333, "Silver"),
            _make_refresh_result(333, "Gold"),
            _make_refresh_result(333, "Platinum"),
        ]
    )
    sys.modules["services.shop_service"].ShopService = MagicMock(return_value=mock_shop_svc)

    result = await execute_shop_refresh_job("job-3", {"job_type": "shop_refresh", "guild_id": 333})

    assert mock_shop_svc.refresh_shop.await_count == 4
    assert result["status"] == "success"
    assert result["guild_id"] == 333
    assert "Bronze" in result["results"]
    assert "Silver" in result["results"]
    assert "Gold" in result["results"]
    assert "Platinum" in result["results"]


@pytest.mark.asyncio
async def test_single_guild_all_tiers_order_is_bronze_silver_gold():
    """Tiers are refreshed in Bronze → Silver → Gold order."""
    from utils.executors.shop_refresh_executor import execute_shop_refresh_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    mock_shop_svc = AsyncMock()
    called_tiers: list[str] = []

    async def _capture_tier(db, guild_id, tier, force_tl):
        called_tiers.append(tier)
        return _make_refresh_result(guild_id, tier)

    mock_shop_svc.refresh_shop = _capture_tier
    sys.modules["services.shop_service"].ShopService = MagicMock(return_value=mock_shop_svc)

    await execute_shop_refresh_job("job-4", {"job_type": "shop_refresh", "guild_id": 444})

    assert called_tiers == ["Bronze", "Silver", "Gold", "Platinum"]


# ===========================================================================
# Tests: execute_shop_refresh_job — bulk refresh (no guild_id)
# ===========================================================================


@pytest.mark.asyncio
async def test_bulk_refresh_iterates_all_guilds():
    """Bulk mode (no guild_id) refreshes every guild returned by ConfigRepository."""
    from utils.executors.shop_refresh_executor import execute_shop_refresh_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    mock_shop_svc = AsyncMock()
    mock_shop_svc.refresh_shop = AsyncMock(side_effect=lambda db, gid, t, ftl: _make_refresh_result(gid, t))
    sys.modules["services.shop_service"].ShopService = MagicMock(return_value=mock_shop_svc)

    guild_configs = [_make_guild_config(10), _make_guild_config(20)]
    mock_config_repo = AsyncMock()
    mock_config_repo.list_all = AsyncMock(return_value=guild_configs)
    sys.modules["persist.repositories.config_repository"].ConfigRepository = MagicMock(return_value=mock_config_repo)

    with patch("utils.executors.shop_refresh_executor._announce_shop_refresh", new=AsyncMock()):
        result = await execute_shop_refresh_job("job-5", {"job_type": "shop_refresh"})

    # 2 guilds x 4 tiers = 8 calls
    assert mock_shop_svc.refresh_shop.await_count == 8
    assert result["status"] == "success"
    assert result["guilds_refreshed"] == 2
    assert 10 in result["results"]
    assert 20 in result["results"]


@pytest.mark.asyncio
async def test_bulk_refresh_no_guilds_returns_zero():
    """Bulk refresh with no guilds configured completes with guilds_refreshed == 0."""
    from utils.executors.shop_refresh_executor import execute_shop_refresh_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    mock_shop_svc = AsyncMock()
    sys.modules["services.shop_service"].ShopService = MagicMock(return_value=mock_shop_svc)

    mock_config_repo = AsyncMock()
    mock_config_repo.list_all = AsyncMock(return_value=[])
    sys.modules["persist.repositories.config_repository"].ConfigRepository = MagicMock(return_value=mock_config_repo)

    result = await execute_shop_refresh_job("job-6", {"job_type": "shop_refresh"})

    mock_shop_svc.refresh_shop.assert_not_awaited()
    assert result["status"] == "success"
    assert result["guilds_refreshed"] == 0


@pytest.mark.asyncio
async def test_bulk_refresh_announces_once_per_guild():
    """_announce_shop_refresh is called exactly once per guild in bulk mode."""
    from utils.executors.shop_refresh_executor import execute_shop_refresh_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    mock_shop_svc = AsyncMock()
    mock_shop_svc.refresh_shop = AsyncMock(side_effect=lambda db, gid, t, ftl: _make_refresh_result(gid, t))
    sys.modules["services.shop_service"].ShopService = MagicMock(return_value=mock_shop_svc)

    guild_configs = [
        _make_guild_config(30, hunting_channel_id=111000, shop_channel_id=811000),
        _make_guild_config(40, hunting_channel_id=222000, shop_channel_id=822000),
    ]
    mock_config_repo = AsyncMock()
    mock_config_repo.list_all = AsyncMock(return_value=guild_configs)
    sys.modules["persist.repositories.config_repository"].ConfigRepository = MagicMock(return_value=mock_config_repo)

    mock_announce = AsyncMock()
    with patch("utils.executors.shop_refresh_executor._announce_shop_refresh", new=mock_announce):
        await execute_shop_refresh_job("job-announce-bulk", {"job_type": "shop_refresh"})

    # Called once per guild (not once per tier).
    assert mock_announce.await_count == 2
    calls = mock_announce.call_args_list
    # Each call: (job_id, guild_id, shop_channel_id, bounty_hunter_role_id)
    # Verify the shop_channel_id (not hunting_channel_id) was passed
    called_args = {(c.args[1], c.args[2]) for c in calls}
    assert called_args == {(30, 811000), (40, 822000)}


# ===========================================================================
# Tests: error handling
# ===========================================================================


@pytest.mark.asyncio
async def test_shop_service_error_is_re_raised():
    """When ShopService.refresh_shop raises, the executor re-raises the exception."""
    from utils.executors.shop_refresh_executor import execute_shop_refresh_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    mock_shop_svc = AsyncMock()
    mock_shop_svc.refresh_shop = AsyncMock(side_effect=RuntimeError("DB gone"))
    sys.modules["services.shop_service"].ShopService = MagicMock(return_value=mock_shop_svc)

    with pytest.raises(RuntimeError, match="DB gone"):
        await execute_shop_refresh_job(
            "job-err",
            {"job_type": "shop_refresh", "guild_id": 99, "tier": "Bronze"},
        )


# ===========================================================================
# Tests: _announce_shop_refresh
# ===========================================================================


@pytest.mark.asyncio
async def test_announce_shop_refresh_skipped_when_no_channel_id():
    """_announce_shop_refresh skips HTTP when shop_channel_id is None."""
    from utils.executors.shop_refresh_executor import _announce_shop_refresh

    with patch("utils.executors.shop_refresh_executor.httpx.AsyncClient") as mock_cls:
        await _announce_shop_refresh("parent-job", 100, None)
        mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_announce_shop_refresh_http_error_is_non_fatal():
    """An HTTP error in _announce_shop_refresh does not propagate."""
    import httpx
    from utils.executors.shop_refresh_executor import _announce_shop_refresh

    with patch("utils.executors.shop_refresh_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise.
        await _announce_shop_refresh("parent-job", 100, 12345)


@pytest.mark.asyncio
async def test_announce_shop_refresh_posts_to_correct_endpoint():
    """_announce_shop_refresh POSTs to /channels/{channel_id}/messages with embed payload."""
    from utils.executors.shop_refresh_executor import _announce_shop_refresh

    channel_id = 555666
    guild_id = 101

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("utils.executors.shop_refresh_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _announce_shop_refresh("parent-job", guild_id, channel_id)

        call_args = mock_client.post.call_args
        posted_url = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
        assert f"/channels/{channel_id}/messages" in posted_url

        posted_body = call_args.kwargs.get("json") or (call_args.args[1] if len(call_args.args) > 1 else {})
        assert "content" in posted_body
        assert posted_body["content"]["title"] == "🛒 Shop Refreshed!"
        assert posted_body["content"]["footer_text"] == "Use /shop to browse!"
        assert "message_type" in posted_body


# ===========================================================================
# Tests: item count verification via GameConstants
# ===========================================================================


def test_default_item_counts_from_game_constants():
    """Verify GameConstants default shop counts: 5 ships + 5 weapons + 5 modules + 2 turrets = 17."""
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "_game_constants_direct",
        _os.path.join(_SRC, "services", "game_constants.py"),
    )
    _mod = importlib.util.module_from_spec(_spec)
    # game_constants.py only uses 'os'; no ORM dependencies.
    _spec.loader.exec_module(_mod)
    GameConstants = _mod.GameConstants

    assert GameConstants.SHOP_DEFAULT_SHIPS_NUM == 5
    assert GameConstants.SHOP_DEFAULT_WEAPONS_NUM == 5
    assert GameConstants.SHOP_DEFAULT_MODULES_NUM == 5
    assert GameConstants.SHOP_DEFAULT_TURRETS_NUM == 2
    total = (
        GameConstants.SHOP_DEFAULT_SHIPS_NUM
        + GameConstants.SHOP_DEFAULT_WEAPONS_NUM
        + GameConstants.SHOP_DEFAULT_MODULES_NUM
        + GameConstants.SHOP_DEFAULT_TURRETS_NUM
    )
    assert total == 17


@pytest.mark.asyncio
async def test_refresh_result_reports_items_generated():
    """The result dict from a single-tier refresh carries items_generated from ShopService."""
    from utils.executors.shop_refresh_executor import execute_shop_refresh_job

    expected_items = 17  # 5 ships + 5 weapons + 5 modules + 2 turrets
    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    mock_shop_svc = AsyncMock()
    mock_shop_svc.refresh_shop = AsyncMock(
        return_value={
            "guild_id": 555,
            "tier": "Bronze",
            "tech_level": 3,
            "items_generated": expected_items,
            "refresh_time": "2026-01-01T00:00:00+00:00",
        }
    )
    sys.modules["services.shop_service"].ShopService = MagicMock(return_value=mock_shop_svc)

    result = await execute_shop_refresh_job(
        "job-cnt",
        {"job_type": "shop_refresh", "guild_id": 555, "tier": "Bronze"},
    )

    assert result["result"]["items_generated"] == expected_items


# ===========================================================================
# Tests: job_executor dispatch
# ===========================================================================


@pytest.mark.asyncio
async def test_job_executor_dispatches_shop_refresh():
    """JobExecutor.execute routes shop_refresh payload to execute_shop_refresh_job."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "shop_refresh", "guild_id": 777, "tier": "Silver"}

    mock_fn = AsyncMock(return_value={"status": "success"})
    with patch("utils.job_executor.execute_shop_refresh_job", mock_fn):
        await executor.execute("job-dispatch", payload)

    mock_fn.assert_awaited_once_with("job-dispatch", payload)


@pytest.mark.asyncio
async def test_job_executor_does_not_dispatch_shop_refresh_for_other_types():
    """Non-shop_refresh payloads do NOT call execute_shop_refresh_job."""
    from utils.job_executor import JobExecutor

    executor = JobExecutor()
    payload = {"job_type": "time_announcement", "guild_id": "g1", "channel_id": "c1"}

    mock_shop_fn = AsyncMock()
    mock_time_fn = AsyncMock(return_value=None)

    with (
        patch("utils.job_executor.execute_shop_refresh_job", mock_shop_fn),
        patch("utils.job_executor.execute_time_announcement_job", mock_time_fn),
    ):
        await executor.execute("job-other", payload)

    mock_shop_fn.assert_not_awaited()
    mock_time_fn.assert_awaited_once()


# ===========================================================================
# Tests: role mention in announcements (SEG-10)
# ===========================================================================


@pytest.mark.asyncio
async def test_shop_announcement_role_mention_in_text_content_not_description():
    """Test 27/28: Role mention is in text_content (NOT embed description) when role configured.

    Bug 2 fix: role mention must NOT be inside embed description.
    It must be in text_content so Discord recognises it as an actual mention.
    """
    from utils.executors.shop_refresh_executor import _announce_shop_refresh

    role_id = 987654321
    channel_id = 555777
    guild_id = 202

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("utils.executors.shop_refresh_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _announce_shop_refresh("parent-job", guild_id, channel_id, role_id)

        call_args = mock_client.post.call_args
        posted_body = call_args.kwargs.get("json") or (call_args.args[1] if len(call_args.args) > 1 else {})

        # Test 27: text_content should contain the role mention
        assert posted_body.get("text_content") == f"<@&{role_id}>", (
            f"Expected text_content='<@&{role_id}>' but got {posted_body.get('text_content')!r}"
        )

        # Test 28: embed description must NOT contain the role mention
        description = posted_body["content"]["description"]
        assert "<@&" not in description, (
            f"Role mention should NOT be inside embed description, but found in: {description!r}"
        )


@pytest.mark.asyncio
async def test_shop_announcement_no_role_mention_when_none():
    """Test 29: When bounty_hunter_role_id is None, text_content is None (no mention)."""
    from utils.executors.shop_refresh_executor import _announce_shop_refresh

    channel_id = 555888
    guild_id = 303

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("utils.executors.shop_refresh_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _announce_shop_refresh("parent-job", guild_id, channel_id, None)

        call_args = mock_client.post.call_args
        posted_body = call_args.kwargs.get("json") or (call_args.args[1] if len(call_args.args) > 1 else {})

        # text_content should be None when no role configured
        assert posted_body.get("text_content") is None, (
            f"Expected text_content=None but got {posted_body.get('text_content')!r}"
        )

        # embed description must not have role mention
        description = posted_body["content"]["description"]
        assert "<@&" not in description


@pytest.mark.asyncio
async def test_shop_announcement_still_works_without_role():
    """Backward compatibility: announcement posts successfully when no bounty_hunter_role_id."""
    from utils.executors.shop_refresh_executor import _announce_shop_refresh

    channel_id = 555999
    guild_id = 404

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("utils.executors.shop_refresh_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Call with no bounty_hunter_role_id argument (tests default/omitted behavior)
        await _announce_shop_refresh("parent-job", guild_id, channel_id)

        # Should have posted successfully
        mock_client.post.assert_awaited_once()
        call_args = mock_client.post.call_args
        posted_body = call_args.kwargs.get("json") or (call_args.args[1] if len(call_args.args) > 1 else {})
        assert posted_body["content"]["title"] == "🛒 Shop Refreshed!"
        mock_response.raise_for_status.assert_called_once()


# ===========================================================================
# New tests for Bug 2 fixes (tests 26, 30, 31)
# ===========================================================================


@pytest.mark.asyncio
async def test_executor_reads_shop_channel_id_not_hunting_channel_id():
    """Test 26: Executor reads shop_channel_id from config (NOT hunting_channel_id)."""
    from utils.executors.shop_refresh_executor import execute_shop_refresh_job

    mock_db = AsyncMock()
    _configure_db_manager(mock_db)

    mock_shop_svc = AsyncMock()
    mock_shop_svc.refresh_shop = AsyncMock(side_effect=lambda db, gid, t, ftl: _make_refresh_result(gid, t))
    sys.modules["services.shop_service"].ShopService = MagicMock(return_value=mock_shop_svc)

    # Set shop_channel_id to 999888 and hunting_channel_id to 111000
    # The executor must pass 999888 (shop channel), not 111000 (hunting channel)
    guild_configs = [
        _make_guild_config(50, hunting_channel_id=111000, shop_channel_id=999888),
    ]
    mock_config_repo = AsyncMock()
    mock_config_repo.list_all = AsyncMock(return_value=guild_configs)
    sys.modules["persist.repositories.config_repository"].ConfigRepository = MagicMock(return_value=mock_config_repo)

    announce_calls = []

    async def _capture_announce(job_id, guild_id, channel_id, role_id=None):
        announce_calls.append({"guild_id": guild_id, "channel_id": channel_id})

    with patch("utils.executors.shop_refresh_executor._announce_shop_refresh", new=_capture_announce):
        await execute_shop_refresh_job("job-ch", {"job_type": "shop_refresh"})

    assert len(announce_calls) == 1
    assert announce_calls[0]["channel_id"] == 999888, (
        f"Expected shop_channel_id=999888 but got {announce_calls[0]['channel_id']}"
    )


@pytest.mark.asyncio
async def test_embed_description_is_exact_refresh_message_no_role_prefix():
    """Test 30: Embed description is exactly the refresh message text, no role prefix."""
    from utils.executors.shop_refresh_executor import _announce_shop_refresh

    expected_description = (
        "The guild shop has been restocked with new items across all tiers. "
        "Check out the latest offerings and upgrade your loadout!"
    )
    role_id = 12345678
    channel_id = 333444
    guild_id = 505

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("utils.executors.shop_refresh_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _announce_shop_refresh("parent-job", guild_id, channel_id, role_id)

        call_args = mock_client.post.call_args
        posted_body = call_args.kwargs.get("json") or (call_args.args[1] if len(call_args.args) > 1 else {})
        description = posted_body["content"]["description"]

        # Description must be EXACTLY the refresh message — no role prefix
        assert description == expected_description, (
            f"Expected exact description:\n  {expected_description!r}\n"
            f"Got:\n  {description!r}"
        )


@pytest.mark.asyncio
async def test_message_posted_to_shop_channel_url():
    """Test 31: Message is posted to correct URL /channels/{shop_channel_id}/messages."""
    from utils.executors.shop_refresh_executor import _announce_shop_refresh

    shop_channel_id = 777888999
    guild_id = 606

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("utils.executors.shop_refresh_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _announce_shop_refresh("parent-job", guild_id, shop_channel_id)

        call_args = mock_client.post.call_args
        posted_url = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
        assert f"/channels/{shop_channel_id}/messages" in posted_url, (
            f"Expected URL containing /channels/{shop_channel_id}/messages but got {posted_url!r}"
        )
