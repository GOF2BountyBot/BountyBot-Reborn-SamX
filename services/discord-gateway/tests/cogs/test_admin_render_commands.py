"""
Tests for AdminCog render configuration commands:
  /render_config  (view / set / reset)
  /render_cache_clear

B.25 Fix B: These commands now use defer() + followup.send() instead of
response.send_message() directly.

Bootstrap pattern mirrors test_setupCog.py:
  - mock shared.bblogger before any cog imports
  - use real discord library
  - at most 2 mocks per test
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# -------------------------------------------------------------------------
# Bootstrap: mock shared.bblogger before any cog imports
# -------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    return logger


_mock_bblogger.get_logger = MagicMock(side_effect=_make_mock_logger)

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used (evict any stubs)
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------


def _make_mock_interaction(is_admin_user: bool = True) -> MagicMock:
    """Return a minimal mock discord.Interaction with defer+followup support."""
    import discord

    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 111222333
    interaction.guild_id = 999888777

    # Guild permissions
    interaction.user.guild_permissions = MagicMock()
    interaction.user.guild_permissions.administrator = is_admin_user

    # Response helpers — B.25 Fix B: commands now use defer + followup
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)

    # Followup — used after defer
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()

    return interaction


def _make_mock_http_response(data: dict, status_code: int = 200) -> MagicMock:
    """Return a mock httpx.Response with .json() and .raise_for_status()."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=data)
    resp.raise_for_status = MagicMock()
    return resp


# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


def _close_coro(coro):
    """Close a coroutine to prevent 'never awaited' RuntimeWarning."""
    coro.close()
    return MagicMock()


@pytest.fixture()
def admin_cog():
    """Return a fresh AdminCog instance with a mocked http_client."""
    from cogs.adminCog import AdminCog
    from discord.ext import commands

    bot = MagicMock(spec=commands.Bot)
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock(side_effect=_close_coro)
    cog = AdminCog(bot)
    # Replace the real httpx.AsyncClient with a MagicMock
    cog.http_client = MagicMock()
    return cog


# -------------------------------------------------------------------------
# /render_config view — B.25 Fix B: now uses defer + followup.send
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_config_view(admin_cog) -> None:
    """B.25 Fix B: render_config view defers first, then GETs /config/render and sends embed."""
    config_data = {
        "max_res_x": 3840,
        "max_res_y": 2160,
        "min_res_x": 352,
        "min_res_y": 240,
        "max_samples": 128,
        "min_samples": 1,
        "default_res_x": 1920,
        "default_res_y": 1080,
        "default_samples": 64,
        "max_concurrent_renders": 2,
        "job_ttl_hours": 1,
    }
    mock_resp = _make_mock_http_response(config_data)
    admin_cog.http_client.get = AsyncMock(return_value=mock_resp)

    interaction = _make_mock_interaction(is_admin_user=True)

    await admin_cog.render_config.callback(admin_cog, interaction, action="view")

    # B.25 Fix B: defer must be called first
    interaction.response.defer.assert_awaited_once()

    admin_cog.http_client.get.assert_called_once()
    call_url = admin_cog.http_client.get.call_args[0][0]
    assert "/config/render" in call_url

    # B.25 Fix B: response is sent via followup.send, not response.send_message
    interaction.followup.send.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
    _, kwargs = interaction.followup.send.call_args
    assert kwargs.get("ephemeral") is True
    # Verify an embed was sent.
    import discord

    assert isinstance(kwargs.get("embed"), discord.Embed)


# -------------------------------------------------------------------------
# /render_config set — B.25 Fix B
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_config_set(admin_cog) -> None:
    """B.25 Fix B: render_config set defers, then PUTs /config/render, responds via followup."""
    mock_resp = _make_mock_http_response({"max_res_x": 1920})
    admin_cog.http_client.put = AsyncMock(return_value=mock_resp)

    interaction = _make_mock_interaction(is_admin_user=True)

    await admin_cog.render_config.callback(admin_cog, interaction, action="set", setting="max_res_x", value=1920)

    interaction.response.defer.assert_awaited_once()
    admin_cog.http_client.put.assert_called_once()
    call_url = admin_cog.http_client.put.call_args[0][0]
    assert "/config/render" in call_url
    call = admin_cog.http_client.put.call_args
    json_payload = call.kwargs.get("json") or call[1].get("json")
    assert json_payload == {"max_res_x": 1920}

    # B.25 Fix B: response via followup
    interaction.followup.send.assert_awaited_once()
    msg_text = str(interaction.followup.send.call_args)
    assert "max_res_x" in msg_text
    assert "1920" in msg_text


@pytest.mark.asyncio
async def test_render_config_set_missing_args(admin_cog) -> None:
    """B.25 Fix B: render_config set with missing args warns via followup.send."""
    interaction = _make_mock_interaction(is_admin_user=True)

    # Call with setting=None, value=None  (user forgot args)
    await admin_cog.render_config.callback(admin_cog, interaction, action="set", setting=None, value=None)

    interaction.response.defer.assert_awaited_once()
    # B.25 Fix B: warning sent via followup
    interaction.followup.send.assert_awaited_once()
    # http_client.put should NOT have been called
    assert not hasattr(admin_cog.http_client, "put") or not admin_cog.http_client.put.called


# -------------------------------------------------------------------------
# /render_config reset — B.25 Fix B
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_config_reset(admin_cog) -> None:
    """B.25 Fix B: render_config reset defers, then POSTs /config/render/reset via followup."""
    mock_resp = _make_mock_http_response({})
    admin_cog.http_client.post = AsyncMock(return_value=mock_resp)

    interaction = _make_mock_interaction(is_admin_user=True)

    await admin_cog.render_config.callback(admin_cog, interaction, action="reset")

    interaction.response.defer.assert_awaited_once()
    admin_cog.http_client.post.assert_called_once()
    call_url = admin_cog.http_client.post.call_args[0][0]
    assert "/config/render/reset" in call_url

    # B.25 Fix B: response via followup
    interaction.followup.send.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()


# -------------------------------------------------------------------------
# /render_cache_clear — B.25 Fix B
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_cache_clear(admin_cog) -> None:
    """B.25 Fix B: render_cache_clear defers, POSTs /cache/clear, sends embed via followup."""
    cache_result = {"cleared_directories": 3, "freed_bytes": 1048576, "freed_mb": 1.0}
    mock_resp = _make_mock_http_response(cache_result)
    admin_cog.http_client.post = AsyncMock(return_value=mock_resp)

    interaction = _make_mock_interaction(is_admin_user=True)

    await admin_cog.render_cache_clear.callback(admin_cog, interaction)

    # B.25 Fix B: defer called first
    interaction.response.defer.assert_awaited_once()
    admin_cog.http_client.post.assert_called_once()
    call_url = admin_cog.http_client.post.call_args[0][0]
    assert "/cache/clear" in call_url

    # B.25 Fix B: response via followup
    interaction.followup.send.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()
    _, kwargs = interaction.followup.send.call_args
    assert kwargs.get("ephemeral") is True
    import discord

    embed = kwargs.get("embed")
    assert isinstance(embed, discord.Embed)
    # Verify stats are mentioned in embed fields
    field_values = [f.value for f in embed.fields]
    assert "3" in field_values  # cleared_directories
    assert "1.0 MB" in field_values  # freed_mb


# -------------------------------------------------------------------------
# B.25 Fix A: Post-defer admin permission check
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_config_blocks_non_admin(admin_cog) -> None:
    """B.25 Fix A: render_config must defer first, then reject non-admin via followup."""
    interaction = _make_mock_interaction(is_admin_user=False)
    # Ensure no Bot Admin role
    interaction.user.roles = []

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DEVELOPERS", "")
        mock_http_resp = _make_mock_http_response({"admin_role_id": None})
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "httpx.AsyncClient.__aenter__",
            return_value=MagicMock(get=AsyncMock(return_value=mock_http_resp)),
        ):
            await admin_cog.render_config.callback(admin_cog, interaction, action="view")

    # Defer must have been called before the admin check
    interaction.response.defer.assert_awaited_once()
    # Non-admin gets permission-denied via followup
    interaction.followup.send.assert_awaited_once()
    msg = str(interaction.followup.send.call_args)
    assert "admin" in msg.lower() or "privilege" in msg.lower()


@pytest.mark.asyncio
async def test_render_cache_clear_blocks_non_admin(admin_cog) -> None:
    """B.25 Fix A: render_cache_clear must defer first, then reject non-admin via followup."""
    interaction = _make_mock_interaction(is_admin_user=False)
    interaction.user.roles = []

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DEVELOPERS", "")
        mock_http_resp = _make_mock_http_response({"admin_role_id": None})
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "httpx.AsyncClient.__aenter__",
            return_value=MagicMock(get=AsyncMock(return_value=mock_http_resp)),
        ):
            await admin_cog.render_cache_clear.callback(admin_cog, interaction)

    # Defer must have been called before the admin check
    interaction.response.defer.assert_awaited_once()
    # Non-admin gets permission-denied via followup
    interaction.followup.send.assert_awaited_once()
    msg = str(interaction.followup.send.call_args)
    assert "admin" in msg.lower() or "privilege" in msg.lower()


# -------------------------------------------------------------------------
# B.25 Fix A: Admin commands defer before any HTTP call
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_config_defer_before_http(admin_cog) -> None:
    """B.25 Fix A+B: defer must be called BEFORE any HTTP call in render_config."""
    call_order = []
    interaction = _make_mock_interaction(is_admin_user=True)

    original_defer = interaction.response.defer

    async def track_defer(*args, **kwargs):
        call_order.append("defer")
        return await original_defer(*args, **kwargs)

    interaction.response.defer = track_defer

    mock_resp = _make_mock_http_response({"max_res_x": 3840})
    call_count = [0]

    async def track_get(*args, **kwargs):
        call_order.append("get")
        call_count[0] += 1
        return mock_resp

    admin_cog.http_client.get = track_get

    await admin_cog.render_config.callback(admin_cog, interaction, action="view")

    assert "defer" in call_order
    assert "get" in call_order
    assert call_order.index("defer") < call_order.index("get"), "defer must happen before HTTP GET"


@pytest.mark.asyncio
async def test_render_cache_clear_defer_before_http(admin_cog) -> None:
    """B.25 Fix A+B: defer must be called BEFORE any HTTP call in render_cache_clear."""
    call_order = []
    interaction = _make_mock_interaction(is_admin_user=True)

    original_defer = interaction.response.defer

    async def track_defer(*args, **kwargs):
        call_order.append("defer")
        return await original_defer(*args, **kwargs)

    interaction.response.defer = track_defer

    mock_resp = _make_mock_http_response({"cleared_directories": 0, "freed_mb": 0.0})
    mock_resp.raise_for_status = MagicMock()

    async def track_post(*args, **kwargs):
        call_order.append("post")
        return mock_resp

    admin_cog.http_client.post = track_post

    await admin_cog.render_cache_clear.callback(admin_cog, interaction)

    assert "defer" in call_order
    assert "post" in call_order
    assert call_order.index("defer") < call_order.index("post"), "defer must happen before HTTP POST"


# -------------------------------------------------------------------------
# Admin permission check — original test preserved (checks _check_is_admin directly)
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_config_requires_admin(admin_cog) -> None:
    """render_config predicate (_check_is_admin) should return False for non-admin users."""
    from cogs.adminCog import _check_is_admin

    # Non-admin user: not in DEVELOPERS, no Administrator permission.
    interaction = _make_mock_interaction(is_admin_user=False)
    interaction.user.roles = []

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DEVELOPERS", "")
        # Mock the HTTP call to /config/guild to return no admin role
        mock_http_resp = _make_mock_http_response({"admin_role_id": None})

        async def _fake_get(*_a, **_kw):
            return mock_http_resp

        # We only need 1 mock: the HTTP GET for guild config
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "httpx.AsyncClient.__aenter__",
            return_value=MagicMock(get=AsyncMock(return_value=mock_http_resp)),
        ):
            result = await _check_is_admin(interaction)

    assert result is False


# -------------------------------------------------------------------------
# B.32: Unknown setting guard — cog-side validation
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_config_set_unknown_setting_blocked(admin_cog) -> None:
    """B.32: render_config set with unknown setting is rejected before API call.

    When setting is not in _render_settings, an error embed must be sent
    and http_client.put must NOT be called.
    """
    admin_cog._render_settings = [
        "max_res_x",
        "max_res_y",
        "min_res_x",
        "min_res_y",
        "max_samples",
        "min_samples",
        "default_res_x",
        "default_res_y",
        "default_samples",
        "max_concurrent_renders",
        "job_ttl_hours",
    ]
    admin_cog.http_client.put = AsyncMock()

    interaction = _make_mock_interaction(is_admin_user=True)

    # "samples" is the exact scenario from B.32 — not a valid field
    await admin_cog.render_config.callback(admin_cog, interaction, action="set", setting="samples", value=64)

    # Must NOT have called the API
    admin_cog.http_client.put.assert_not_called()
    # Must have sent an error message via followup
    interaction.followup.send.assert_awaited_once()
    msg = str(interaction.followup.send.call_args)
    assert "samples" in msg
    assert "unknown" in msg.lower() or "valid" in msg.lower()
    # Must be ephemeral
    kwargs = interaction.followup.send.call_args[1]
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_render_config_set_valid_setting_calls_api(admin_cog) -> None:
    """B.32: render_config set with valid setting still calls the PUT API."""
    admin_cog._render_settings = ["max_res_x", "default_samples"]
    mock_resp = _make_mock_http_response({"max_res_x": 1920})
    admin_cog.http_client.put = AsyncMock(return_value=mock_resp)

    interaction = _make_mock_interaction(is_admin_user=True)

    await admin_cog.render_config.callback(admin_cog, interaction, action="set", setting="max_res_x", value=1920)

    # API must have been called with the correct payload
    admin_cog.http_client.put.assert_called_once()
    interaction.followup.send.assert_awaited_once()
    msg = str(interaction.followup.send.call_args)
    assert "max_res_x" in msg
    assert "1920" in msg


@pytest.mark.asyncio
async def test_render_config_set_empty_preload_skips_guard(admin_cog) -> None:
    """B.32: If _render_settings is empty (preload failed), skip guard and call API.

    This is a safe-failure mode: if the preload fails, we don't block all set ops.
    """
    admin_cog._render_settings = []  # preload failed
    mock_resp = _make_mock_http_response({"max_res_x": 1920})
    admin_cog.http_client.put = AsyncMock(return_value=mock_resp)

    interaction = _make_mock_interaction(is_admin_user=True)

    await admin_cog.render_config.callback(admin_cog, interaction, action="set", setting="max_res_x", value=1920)

    # With empty preload, the guard is bypassed and API is called
    admin_cog.http_client.put.assert_called_once()
