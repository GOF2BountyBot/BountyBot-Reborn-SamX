"""
Tests for AdminCog render configuration commands:
  /render_config  (view / set / reset)
  /render_cache_clear

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
    """Return a minimal mock discord.Interaction."""
    import discord

    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 111222333
    interaction.guild_id = 999888777

    # Guild permissions
    interaction.user.guild_permissions = MagicMock()
    interaction.user.guild_permissions.administrator = is_admin_user

    # Response helpers
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)

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
# /render_config view
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_config_view(admin_cog) -> None:
    """render_config view should GET /config/render and send an embed."""
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

    admin_cog.http_client.get.assert_called_once()
    call_url = admin_cog.http_client.get.call_args[0][0]
    assert "/config/render" in call_url

    interaction.response.send_message.assert_called_once()
    _, kwargs = interaction.response.send_message.call_args
    assert kwargs.get("ephemeral") is True
    # Verify an embed was sent.
    import discord

    assert isinstance(kwargs.get("embed"), discord.Embed)


# -------------------------------------------------------------------------
# /render_config set
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_config_set(admin_cog) -> None:
    """render_config set should PUT /config/render with the given key/value."""
    mock_resp = _make_mock_http_response({"max_res_x": 1920})
    admin_cog.http_client.put = AsyncMock(return_value=mock_resp)

    interaction = _make_mock_interaction(is_admin_user=True)

    await admin_cog.render_config.callback(admin_cog, interaction, action="set", setting="max_res_x", value=1920)

    admin_cog.http_client.put.assert_called_once()
    call_url = admin_cog.http_client.put.call_args[0][0]
    assert "/config/render" in call_url
    call = admin_cog.http_client.put.call_args
    json_payload = call.kwargs.get("json") or call[1].get("json")
    assert json_payload == {"max_res_x": 1920}

    interaction.response.send_message.assert_called_once()
    msg_text = interaction.response.send_message.call_args[0][0]
    assert "max_res_x" in msg_text
    assert "1920" in msg_text


@pytest.mark.asyncio
async def test_render_config_set_missing_args(admin_cog) -> None:
    """render_config set with missing setting/value should warn the user."""
    interaction = _make_mock_interaction(is_admin_user=True)

    # Call with setting=None, value=None  (user forgot args)
    await admin_cog.render_config.callback(admin_cog, interaction, action="set", setting=None, value=None)

    interaction.response.send_message.assert_called_once()
    # http_client.put should NOT have been called
    assert not hasattr(admin_cog.http_client, "put") or not admin_cog.http_client.put.called


# -------------------------------------------------------------------------
# /render_config reset
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_config_reset(admin_cog) -> None:
    """render_config reset should POST /config/render/reset."""
    mock_resp = _make_mock_http_response({})
    admin_cog.http_client.post = AsyncMock(return_value=mock_resp)

    interaction = _make_mock_interaction(is_admin_user=True)

    await admin_cog.render_config.callback(admin_cog, interaction, action="reset")

    admin_cog.http_client.post.assert_called_once()
    call_url = admin_cog.http_client.post.call_args[0][0]
    assert "/config/render/reset" in call_url

    interaction.response.send_message.assert_called_once()


# -------------------------------------------------------------------------
# /render_cache_clear
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_cache_clear(admin_cog) -> None:
    """render_cache_clear should POST /cache/clear and return an embed with stats."""
    cache_result = {"cleared_directories": 3, "freed_bytes": 1048576, "freed_mb": 1.0}
    mock_resp = _make_mock_http_response(cache_result)
    admin_cog.http_client.post = AsyncMock(return_value=mock_resp)

    interaction = _make_mock_interaction(is_admin_user=True)

    await admin_cog.render_cache_clear.callback(admin_cog, interaction)

    admin_cog.http_client.post.assert_called_once()
    call_url = admin_cog.http_client.post.call_args[0][0]
    assert "/cache/clear" in call_url

    interaction.response.send_message.assert_called_once()
    _, kwargs = interaction.response.send_message.call_args
    assert kwargs.get("ephemeral") is True
    import discord

    embed = kwargs.get("embed")
    assert isinstance(embed, discord.Embed)
    # Verify stats are mentioned in embed fields
    field_values = [f.value for f in embed.fields]
    assert "3" in field_values  # cleared_directories
    assert "1.0 MB" in field_values  # freed_mb


# -------------------------------------------------------------------------
# Admin permission check
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_config_requires_admin(admin_cog) -> None:
    """render_config predicate should return False for non-admin users."""
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
