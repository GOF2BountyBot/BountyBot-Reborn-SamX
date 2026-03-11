"""Extended tests for the messages API endpoints — boosting coverage from 50% to 85%+."""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import discord_mock_utils for consistent mock patterns
from tests.mocks.discord_mock_utils import DiscordMockUtils

# ---------------------------------------------------------------------------
# Module-level mock setup — must happen before any src imports
# ---------------------------------------------------------------------------

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


_mock_bblogger.get_logger = _make_mock_logger

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

# Ensure real discord is used (not a hand-rolled fake from another test module)
for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))



def _evict_discord_modules():
    """Remove any cached discord or source modules so they re-import with real discord."""
    to_evict = [
        k for k in sys.modules
        if k == "discord" or k.startswith("discord.")
        or k in ("api", "bot", "utils") or k.startswith("api.")
        or k.startswith("utils.") or k.startswith("cogs.")
    ]
    for k in to_evict:
        sys.modules.pop(k, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_message_payload():
    """Return a minimal MessageSummary-compatible MagicMock."""
    from api.schemas.message_schemas import MessageSummary
    return MessageSummary(
        id=1234567890,
        author_id=123456789,
        content=None,
        timestamp="2024-01-01T00:00:00",
    )


def _make_mock_message(message_id=1234567890, author_id=123456789):
    """Build a mock Discord message with edit/delete mocked."""
    msg = MagicMock()
    msg.id = message_id
    msg.author = MagicMock()
    msg.author.id = author_id
    msg.channel = MagicMock()
    msg.channel.guild = MagicMock()
    msg.channel.guild.get_member = MagicMock(return_value=MagicMock())
    msg.channel.permissions_for = MagicMock(
        return_value=MagicMock(manage_messages=True)
    )
    msg.edit = AsyncMock()
    msg.delete = AsyncMock()
    return msg


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    """Mock bot for messages tests."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.get_channel = MagicMock(return_value=None)
    bot.fetch_channel = AsyncMock(return_value=None)
    return bot


@pytest.fixture
def messages_test_app(mock_bot):
    """Full test app using real _find_message patch pattern from existing tests."""
    _evict_discord_modules()
    app = FastAPI(title="Messages Test")
    app.state.bot = mock_bot

    with patch("api.routers.messages._find_message", new_callable=AsyncMock) as mock_find, \
         patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
         patch("api.routers.messages.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
         patch("api.routers.messages.MessageConverter") as mock_converter:

        from api.schemas.message_schemas import MessageSummary

        _payload = MessageSummary(
            id=1234567890,
            author_id=123456789,
            content=None,
            timestamp="2024-01-01T00:00:00",
        )

        async def _find_message_impl(bot, message_id, logger):
            if message_id == 1234567890:
                return _make_mock_message(message_id=1234567890, author_id=bot.user.id)
            return None

        async def _resolve_bot(request):
            return mock_bot

        mock_find.side_effect = _find_message_impl
        mock_resolve.side_effect = _resolve_bot
        mock_handle.return_value = None
        mock_converter.message_to_payload.return_value = _payload

        from api.routers.messages import router
        app.include_router(router, prefix="/api/v1")

        yield app, {
            "find_message": mock_find,
            "resolve_bot": mock_resolve,
            "handle_exception": mock_handle,
            "converter": mock_converter,
        }


@pytest.fixture
def messages_client(messages_test_app):
    app, _ = messages_test_app
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /messages/{message_id}
# ---------------------------------------------------------------------------


class TestGetMessageExtended:
    """Extended tests for GET /messages/{message_id}."""

    def test_get_message_success(self, messages_client):
        """GET existing message returns 200 with message data."""
        resp = messages_client.get("/api/v1/messages/1234567890")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "found"
        assert "data" in data

    def test_get_message_not_found(self, messages_client):
        """GET non-existent message returns 404."""
        resp = messages_client.get("/api/v1/messages/9999999999")
        assert resp.status_code == 404
        assert "message" in resp.json()["detail"].lower()

    def test_get_message_invalid_zero_id(self, messages_client):
        """GET message_id=0 returns 400 (non-positive)."""
        # message_id=0 → path parameter won't match int route properly;
        # test the positive-validation branch with message_id clamped via direct call
        # FastAPI will 422 on non-int, but 0 is technically a valid int path param
        # that the router should reject with 400.
        resp = messages_client.get("/api/v1/messages/0")
        assert resp.status_code == 400
        assert "positive" in resp.json()["detail"].lower()

    def test_get_message_negative_id(self, messages_client):
        """GET message with negative ID returns 400 or 422."""
        resp = messages_client.get("/api/v1/messages/-1")
        # Negative integer path params may be rejected at FastAPI level (422) or our level (400)
        assert resp.status_code in (400, 422)

    def test_get_message_data_shape(self, messages_client):
        """GET message data should include id and author_id."""
        resp = messages_client.get("/api/v1/messages/1234567890")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "id" in data
        assert "author_id" in data


# ---------------------------------------------------------------------------
# PUT /messages/{message_id}
# ---------------------------------------------------------------------------


class TestUpdateMessageExtended:
    """Extended tests for PUT /messages/{message_id}."""

    def test_update_message_bot_own_message_success(self, mock_bot):
        """PUT on bot's own message returns 200."""
        _evict_discord_modules()
        app = FastAPI()
        app.state.bot = mock_bot

        bot_msg = _make_mock_message(message_id=1234567890, author_id=mock_bot.user.id)

        with patch("api.routers.messages._find_message", new_callable=AsyncMock) as mock_find, \
             patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.messages.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.messages.MessageConverter") as mock_conv, \
             patch("api.routers.messages.EmbedConverter") as mock_ec:

            from api.schemas.message_schemas import MessageSummary
            _payload = MessageSummary(
                id=1234567890, author_id=mock_bot.user.id,
                content=None, timestamp="2024-01-01T00:00:00"
            )

            async def _find(bot, mid, logger):
                return bot_msg if mid == 1234567890 else None

            async def _resolve(req):
                return mock_bot

            mock_find.side_effect = _find
            mock_resolve.side_effect = _resolve
            mock_conv.message_to_payload.return_value = _payload
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.messages import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            payload = {"content": {"title": "Updated", "description": "Content"}}
            resp = client.put("/api/v1/messages/1234567890", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "updated"

    def test_update_message_not_found(self, messages_client):
        """PUT on non-existent message returns 404."""
        payload = {"content": {"title": "x"}}
        resp = messages_client.put("/api/v1/messages/9999999999", json=payload)
        assert resp.status_code == 404

    def test_update_message_zero_id_returns_400(self, messages_client):
        """PUT with message_id=0 returns 400."""
        payload = {"content": {"title": "x"}}
        resp = messages_client.put("/api/v1/messages/0", json=payload)
        assert resp.status_code == 400

    def test_update_message_not_bots_own_returns_403(self, mock_bot):
        """PUT on another user's message returns 403."""
        _evict_discord_modules()
        app = FastAPI()
        app.state.bot = mock_bot

        # Message authored by different user
        other_msg = _make_mock_message(message_id=1234567890, author_id=999999999)

        with patch("api.routers.messages._find_message", new_callable=AsyncMock) as mock_find, \
             patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.messages.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.messages.MessageConverter"), \
             patch("api.routers.messages.EmbedConverter"):

            async def _find(bot, mid, logger):
                return other_msg if mid == 1234567890 else None

            async def _resolve(req):
                return mock_bot

            mock_find.side_effect = _find
            mock_resolve.side_effect = _resolve

            from api.routers.messages import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            payload = {"content": {"title": "Updated"}}
            resp = client.put("/api/v1/messages/1234567890", json=payload)
            assert resp.status_code == 403
            assert "bot" in resp.json()["detail"].lower()

    def test_update_message_missing_content_returns_422(self, messages_client):
        """PUT with missing content field returns 422."""
        payload = {}  # content is required
        resp = messages_client.put("/api/v1/messages/1234567890", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /messages/{message_id}
# ---------------------------------------------------------------------------


class TestDeleteMessageExtended:
    """Extended tests for DELETE /messages/{message_id}."""

    def test_delete_message_success(self, messages_client):
        """DELETE existing message returns 200."""
        resp = messages_client.delete("/api/v1/messages/1234567890")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["deleted"] is True

    def test_delete_message_not_found(self, messages_client):
        """DELETE non-existent message returns 404."""
        resp = messages_client.delete("/api/v1/messages/9999999999")
        assert resp.status_code == 404

    def test_delete_message_zero_id_returns_400(self, messages_client):
        """DELETE with message_id=0 returns 400."""
        resp = messages_client.delete("/api/v1/messages/0")
        assert resp.status_code == 400

    def test_delete_message_no_permission_returns_403(self, mock_bot):
        """DELETE message where bot lacks manage_messages permission returns 403."""
        _evict_discord_modules()
        app = FastAPI()
        app.state.bot = mock_bot

        # Message by different user, bot member has no manage_messages
        other_msg = MagicMock()
        other_msg.id = 1234567890
        other_msg.author = MagicMock()
        other_msg.author.id = 999999999  # not the bot
        other_msg.channel = MagicMock()
        other_msg.channel.guild = MagicMock()
        bot_member = MagicMock()
        other_msg.channel.guild.get_member = MagicMock(return_value=bot_member)
        other_msg.channel.permissions_for = MagicMock(
            return_value=MagicMock(manage_messages=False)
        )
        other_msg.delete = AsyncMock()

        with patch("api.routers.messages._find_message", new_callable=AsyncMock) as mock_find, \
             patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.messages.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.messages.MessageConverter"), \
             patch("api.routers.messages.EmbedConverter"):

            async def _find(bot, mid, logger):
                return other_msg if mid == 1234567890 else None

            async def _resolve(req):
                return mock_bot

            mock_find.side_effect = _find
            mock_resolve.side_effect = _resolve

            from api.routers.messages import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            resp = client.delete("/api/v1/messages/1234567890")
            assert resp.status_code == 403
            assert "permission" in resp.json()["detail"].lower()

    def test_delete_message_bot_member_not_found_returns_403(self, mock_bot):
        """DELETE message by other user with no bot_member found in guild returns 403."""
        _evict_discord_modules()
        app = FastAPI()
        app.state.bot = mock_bot

        other_msg = MagicMock()
        other_msg.id = 1234567890
        other_msg.author = MagicMock()
        other_msg.author.id = 999999999
        other_msg.channel = MagicMock()
        other_msg.channel.guild = MagicMock()
        # get_member returns None → bot_member is None → 403
        other_msg.channel.guild.get_member = MagicMock(return_value=None)
        other_msg.channel.permissions_for = MagicMock(
            return_value=MagicMock(manage_messages=True)
        )
        other_msg.delete = AsyncMock()

        with patch("api.routers.messages._find_message", new_callable=AsyncMock) as mock_find, \
             patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.messages.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.messages.MessageConverter"), \
             patch("api.routers.messages.EmbedConverter"):

            async def _find(bot, mid, logger):
                return other_msg if mid == 1234567890 else None

            async def _resolve(req):
                return mock_bot

            mock_find.side_effect = _find
            mock_resolve.side_effect = _resolve

            from api.routers.messages import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            resp = client.delete("/api/v1/messages/1234567890")
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# _find_message internal helper (direct unit tests)
# ---------------------------------------------------------------------------


class TestFindMessageHelper:
    """Direct unit tests for the _find_message async helper function."""

    def test_find_message_returns_none_when_no_guilds(self):
        """_find_message returns None when bot has no guilds."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        bot = MagicMock()
        bot.guilds = []
        bot.cached_messages = None
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 1234567890, logger))
        assert result is None

    def test_find_message_from_cached_messages_dict(self):
        """_find_message should find a message in a dict cache."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        mock_msg = MagicMock()
        mock_msg.id = 1234567890

        bot = MagicMock()
        bot.guilds = []
        bot.cached_messages = {1234567890: mock_msg}
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 1234567890, logger))
        assert result is mock_msg

    def test_find_message_from_cached_messages_list(self):
        """_find_message should find a message in a list cache."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        mock_msg = MagicMock()
        mock_msg.id = 1234567890

        bot = MagicMock()
        bot.guilds = []
        bot.cached_messages = [mock_msg]
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 1234567890, logger))
        assert result is mock_msg

    def test_find_message_via_fetch_not_found(self):
        """_find_message scanning guilds returns None when all channels raise NotFound."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        # Re-import discord to get real NotFound

        from api.routers.messages import _find_message

        channel = MagicMock()
        channel.id = 999
        channel.fetch_message = AsyncMock(
            side_effect=DiscordMockUtils.create_discord_not_found("not found")
        )

        guild = MagicMock()
        guild.channels = [channel]

        bot = MagicMock()
        bot.guilds = [guild]
        bot.cached_messages = None
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 1234567890, logger))
        assert result is None

    def test_find_message_via_fetch_forbidden(self):
        """_find_message scanning guilds skips channels raising Forbidden."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        channel = MagicMock()
        channel.id = 999
        channel.fetch_message = AsyncMock(
            side_effect=DiscordMockUtils.create_discord_forbidden("forbidden")
        )

        guild = MagicMock()
        guild.channels = [channel]

        bot = MagicMock()
        bot.guilds = [guild]
        bot.cached_messages = None
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 1234567890, logger))
        assert result is None

    def test_find_message_via_fetch_found(self):
        """_find_message scanning guilds returns message when found in a channel."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        mock_msg = MagicMock()
        mock_msg.id = 1234567890

        channel = MagicMock()
        channel.id = 999
        channel.fetch_message = AsyncMock(return_value=mock_msg)

        guild = MagicMock()
        guild.channels = [channel]

        bot = MagicMock()
        bot.guilds = [guild]
        bot.cached_messages = None
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 1234567890, logger))
        assert result is mock_msg

    def test_find_message_channel_without_fetch_message_skipped(self):
        """_find_message skips channels that don't have fetch_message."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        # Channel without fetch_message attribute
        channel = MagicMock(spec=[])  # empty spec, no fetch_message

        guild = MagicMock()
        guild.channels = [channel]

        bot = MagicMock()
        bot.guilds = [guild]
        bot.cached_messages = None
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 1234567890, logger))
        assert result is None

    def test_find_message_timeout_continues_to_next_channel(self):
        """_find_message handles asyncio.TimeoutError and continues searching."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        slow_channel = MagicMock()
        slow_channel.id = 111
        slow_channel.fetch_message = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        found_msg = MagicMock()
        found_msg.id = 1234567890

        fast_channel = MagicMock()
        fast_channel.id = 222
        fast_channel.fetch_message = AsyncMock(return_value=found_msg)

        guild = MagicMock()
        guild.channels = [slow_channel, fast_channel]

        bot = MagicMock()
        bot.guilds = [guild]
        bot.cached_messages = None
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 1234567890, logger))
        assert result is found_msg

    def test_find_message_unexpected_error_continues(self):
        """_find_message handles unexpected exceptions and continues searching."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        error_channel = MagicMock()
        error_channel.id = 111
        error_channel.fetch_message = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )

        found_msg = MagicMock()
        found_msg.id = 1234567890

        good_channel = MagicMock()
        good_channel.id = 222
        good_channel.fetch_message = AsyncMock(return_value=found_msg)

        guild = MagicMock()
        guild.channels = [error_channel, good_channel]

        bot = MagicMock()
        bot.guilds = [guild]
        bot.cached_messages = None
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 1234567890, logger))
        assert result is found_msg


# ---------------------------------------------------------------------------
# Additional tests for missing coverage lines
# Lines 60-62: Exception in cache iteration (broad except / pass)
# Lines 77, 80: continue in NotFound/Forbidden branches (direct unit coverage)
# Lines 136-138: except Exception in get_message handler
# Lines 193-201: except discord.HTTPException + except Exception in update_message
# Lines 258-266: except discord.HTTPException + except Exception in delete_message
# ---------------------------------------------------------------------------


class TestFindMessageCacheException:
    """Tests covering lines 60-62: exception raised during cache iteration."""

    def test_find_message_cache_raises_exception_falls_through_to_guild_scan(self):
        """Lines 60-62: When iterating a cache attr raises an exception, it is silently
        swallowed and the function falls through to guild scanning."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        # Build a bad cache that raises on iteration
        class _BadIterable:
            def __iter__(self):
                raise RuntimeError("bad cache – boom")

        bot = MagicMock()
        bot.guilds = []
        # cached_messages is set but iteration raises
        bot.cached_messages = _BadIterable()
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        # Should not raise; just returns None (no guilds to scan)
        result = asyncio.run(_find_message(bot, 1234567890, logger))
        assert result is None

    def test_find_message_dict_cache_raises_exception_falls_through(self):
        """Lines 60-62: When iterating a dict cache's values raises, it is silently
        swallowed and the function continues."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        # A dict-like object whose .values() raises
        class _BadDict(dict):
            def values(self):
                raise RuntimeError("bad dict values – boom")

        bot = MagicMock()
        bot.guilds = []
        bot.cached_messages = _BadDict()
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 1234567890, logger))
        assert result is None


class TestFindMessageNotFoundForbiddenContinue:
    """Tests explicitly covering lines 77 and 80 (the bare `continue` statements)."""

    def test_find_message_not_found_continues_and_returns_none(self):
        """Line 77: discord.NotFound → continue.  With only one channel that raises
        NotFound the function should return None rather than propagating."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        channel = MagicMock()
        channel.id = 777
        channel.fetch_message = AsyncMock(
            side_effect=DiscordMockUtils.create_discord_not_found("not found")
        )

        guild = MagicMock()
        guild.channels = [channel]

        bot = MagicMock()
        bot.guilds = [guild]
        bot.cached_messages = None
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 9999, logger))
        assert result is None

    def test_find_message_forbidden_continues_and_returns_none(self):
        """Line 80: discord.Forbidden → continue.  With only one channel that raises
        Forbidden the function should return None rather than propagating."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        channel = MagicMock()
        channel.id = 888
        channel.fetch_message = AsyncMock(
            side_effect=DiscordMockUtils.create_discord_forbidden("forbidden")
        )

        guild = MagicMock()
        guild.channels = [channel]

        bot = MagicMock()
        bot.guilds = [guild]
        bot.cached_messages = None
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 9999, logger))
        assert result is None

    def test_find_message_not_found_then_found_in_next_channel(self):
        """Line 77: After NotFound on first channel the loop continues and finds
        the message in the second channel."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        not_found_channel = MagicMock()
        not_found_channel.id = 777
        not_found_channel.fetch_message = AsyncMock(
            side_effect=DiscordMockUtils.create_discord_not_found("not found")
        )

        found_msg = MagicMock()
        found_msg.id = 555

        found_channel = MagicMock()
        found_channel.id = 888
        found_channel.fetch_message = AsyncMock(return_value=found_msg)

        guild = MagicMock()
        guild.channels = [not_found_channel, found_channel]

        bot = MagicMock()
        bot.guilds = [guild]
        bot.cached_messages = None
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 555, logger))
        assert result is found_msg

    def test_find_message_forbidden_then_found_in_next_channel(self):
        """Line 80: After Forbidden on first channel the loop continues and finds
        the message in the second channel."""
        import asyncio

        _evict_discord_modules()
        sys.modules["shared"] = _mock_shared
        sys.modules["shared.bblogger"] = _mock_bblogger

        from api.routers.messages import _find_message

        forbidden_channel = MagicMock()
        forbidden_channel.id = 111
        forbidden_channel.fetch_message = AsyncMock(
            side_effect=DiscordMockUtils.create_discord_forbidden("forbidden")
        )

        found_msg = MagicMock()
        found_msg.id = 666

        good_channel = MagicMock()
        good_channel.id = 222
        good_channel.fetch_message = AsyncMock(return_value=found_msg)

        guild = MagicMock()
        guild.channels = [forbidden_channel, good_channel]

        bot = MagicMock()
        bot.guilds = [guild]
        bot.cached_messages = None
        bot.messages_cache = None
        bot._message_cache = None
        logger = _make_mock_logger()

        result = asyncio.run(_find_message(bot, 666, logger))
        assert result is found_msg


class TestGetMessageExceptionHandlers:
    """Tests covering lines 136-138: except Exception in get_message endpoint."""

    def test_get_message_unexpected_exception_calls_handle_discord_exception(self, mock_bot):
        """Lines 136-138: When an unexpected (non-HTTP) exception is raised inside
        get_message's try block, handle_discord_exception is called and raises HTTP 500."""
        from fastapi import HTTPException as _HTTPException

        _evict_discord_modules()
        app = FastAPI()
        app.state.bot = mock_bot

        captured_calls = []

        with patch("api.routers.messages._find_message", new_callable=AsyncMock) as mock_find, \
             patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.messages.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.messages.MessageConverter"):

            async def _resolve(req):
                return mock_bot

            # Raising a generic RuntimeError triggers the except Exception branch
            async def _find(bot, mid, logger):
                raise RuntimeError("unexpected failure")

            async def _handle(operation, exc):
                captured_calls.append((operation, exc))
                raise _HTTPException(status_code=500, detail=f"Failed: {exc}")

            mock_resolve.side_effect = _resolve
            mock_find.side_effect = _find
            mock_handle.side_effect = _handle

            from api.routers.messages import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/messages/1234567890")
            assert resp.status_code == 500
            assert len(captured_calls) == 1
            assert captured_calls[0][0] == "get message"
            assert isinstance(captured_calls[0][1], RuntimeError)

    def test_get_message_resolve_bot_raises_generic_exception(self, mock_bot):
        """Lines 136-138: When resolve_bot raises a non-HTTP exception,
        the except Exception handler is invoked and returns HTTP 500."""
        from fastapi import HTTPException as _HTTPException

        _evict_discord_modules()
        app = FastAPI()
        app.state.bot = mock_bot

        captured_calls = []

        with patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.messages.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.messages._find_message", new_callable=AsyncMock), \
             patch("api.routers.messages.MessageConverter"):

            async def _resolve(req):
                raise ConnectionError("network error")

            async def _handle(operation, exc):
                captured_calls.append((operation, exc))
                raise _HTTPException(status_code=500, detail=f"Failed: {exc}")

            mock_resolve.side_effect = _resolve
            mock_handle.side_effect = _handle

            from api.routers.messages import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/messages/1234567890")
            assert resp.status_code == 500
            assert len(captured_calls) == 1
            assert captured_calls[0][0] == "get message"
            assert isinstance(captured_calls[0][1], ConnectionError)


class TestUpdateMessageExceptionHandlers:
    """Tests covering lines 193-201: discord.HTTPException and except Exception in update_message."""

    def test_update_message_discord_http_exception_returns_500(self, mock_bot):
        """Lines 193-198: discord.HTTPException during message.edit → HTTP 500."""
        _evict_discord_modules()
        app = FastAPI()
        app.state.bot = mock_bot

        bot_msg = _make_mock_message(message_id=1234567890, author_id=mock_bot.user.id)
        # Make message.edit raise a real discord.HTTPException
        discord_exc = DiscordMockUtils.create_discord_http_exception(500, "Discord API error")
        bot_msg.edit = AsyncMock(side_effect=discord_exc)

        with patch("api.routers.messages._find_message", new_callable=AsyncMock) as mock_find, \
             patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.messages.MessageConverter"), \
             patch("api.routers.messages.EmbedConverter") as mock_ec:

            async def _find(bot, mid, logger):
                return bot_msg if mid == 1234567890 else None

            async def _resolve(req):
                return mock_bot

            mock_find.side_effect = _find
            mock_resolve.side_effect = _resolve
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.messages import router
            app.include_router(router, prefix="/api/v1")

            # The router's except discord.HTTPException block raises HTTPException(500) directly
            client = TestClient(app)
            payload = {"content": {"title": "Updated"}}
            resp = client.put("/api/v1/messages/1234567890", json=payload)
            assert resp.status_code == 500
            assert "Discord API error" in resp.json()["detail"]

    def test_update_message_unexpected_exception_calls_handle_discord_exception(self, mock_bot):
        """Lines 199-201: Generic exception during update_message calls handle_discord_exception."""
        from fastapi import HTTPException as _HTTPException

        _evict_discord_modules()
        app = FastAPI()
        app.state.bot = mock_bot

        bot_msg = _make_mock_message(message_id=1234567890, author_id=mock_bot.user.id)
        bot_msg.edit = AsyncMock(side_effect=RuntimeError("unexpected edit error"))

        captured_calls = []

        with patch("api.routers.messages._find_message", new_callable=AsyncMock) as mock_find, \
             patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.messages.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.messages.MessageConverter"), \
             patch("api.routers.messages.EmbedConverter") as mock_ec:

            async def _find(bot, mid, logger):
                return bot_msg if mid == 1234567890 else None

            async def _resolve(req):
                return mock_bot

            async def _handle(operation, exc):
                captured_calls.append((operation, exc))
                raise _HTTPException(status_code=500, detail=f"Failed: {exc}")

            mock_find.side_effect = _find
            mock_resolve.side_effect = _resolve
            mock_handle.side_effect = _handle
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.messages import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app, raise_server_exceptions=False)
            payload = {"content": {"title": "Updated"}}
            resp = client.put("/api/v1/messages/1234567890", json=payload)
            assert resp.status_code == 500
            assert len(captured_calls) == 1
            assert captured_calls[0][0] == "update message"
            assert isinstance(captured_calls[0][1], RuntimeError)

    def test_update_message_resolve_bot_raises_generic_exception(self, mock_bot):
        """Lines 199-201: resolve_bot raising a non-discord exception in update_message
        triggers the except Exception handler."""
        from fastapi import HTTPException as _HTTPException

        _evict_discord_modules()
        app = FastAPI()
        app.state.bot = mock_bot

        captured_calls = []

        with patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.messages.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.messages._find_message", new_callable=AsyncMock), \
             patch("api.routers.messages.MessageConverter"), \
             patch("api.routers.messages.EmbedConverter"):

            async def _resolve(req):
                raise ValueError("unexpected resolve error")

            async def _handle(operation, exc):
                captured_calls.append((operation, exc))
                raise _HTTPException(status_code=500, detail=f"Failed: {exc}")

            mock_resolve.side_effect = _resolve
            mock_handle.side_effect = _handle

            from api.routers.messages import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app, raise_server_exceptions=False)
            payload = {"content": {"title": "test"}}
            resp = client.put("/api/v1/messages/1234567890", json=payload)
            assert resp.status_code == 500
            assert len(captured_calls) == 1
            assert captured_calls[0][0] == "update message"
            assert isinstance(captured_calls[0][1], ValueError)


class TestDeleteMessageExceptionHandlers:
    """Tests covering lines 258-266: discord.HTTPException and except Exception in delete_message."""

    def test_delete_message_discord_http_exception_returns_500(self, mock_bot):
        """Lines 258-263: discord.HTTPException during message.delete → HTTP 500."""
        _evict_discord_modules()
        app = FastAPI()
        app.state.bot = mock_bot

        bot_msg = _make_mock_message(message_id=1234567890, author_id=mock_bot.user.id)
        # Make message.delete raise a real discord.HTTPException
        discord_exc = DiscordMockUtils.create_discord_http_exception(500, "Discord API error")
        bot_msg.delete = AsyncMock(side_effect=discord_exc)

        with patch("api.routers.messages._find_message", new_callable=AsyncMock) as mock_find, \
             patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.messages.MessageConverter"), \
             patch("api.routers.messages.EmbedConverter"):

            async def _find(bot, mid, logger):
                return bot_msg if mid == 1234567890 else None

            async def _resolve(req):
                return mock_bot

            mock_find.side_effect = _find
            mock_resolve.side_effect = _resolve

            from api.routers.messages import router
            app.include_router(router, prefix="/api/v1")

            # The router's except discord.HTTPException block raises HTTPException(500) directly
            client = TestClient(app)
            resp = client.delete("/api/v1/messages/1234567890")
            assert resp.status_code == 500
            assert "Discord API error" in resp.json()["detail"]

    def test_delete_message_unexpected_exception_calls_handle_discord_exception(self, mock_bot):
        """Lines 264-266: Generic exception during delete_message calls handle_discord_exception."""
        from fastapi import HTTPException as _HTTPException

        _evict_discord_modules()
        app = FastAPI()
        app.state.bot = mock_bot

        bot_msg = _make_mock_message(message_id=1234567890, author_id=mock_bot.user.id)
        bot_msg.delete = AsyncMock(side_effect=RuntimeError("unexpected delete error"))

        captured_calls = []

        with patch("api.routers.messages._find_message", new_callable=AsyncMock) as mock_find, \
             patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.messages.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.messages.MessageConverter"), \
             patch("api.routers.messages.EmbedConverter"):

            async def _find(bot, mid, logger):
                return bot_msg if mid == 1234567890 else None

            async def _resolve(req):
                return mock_bot

            async def _handle(operation, exc):
                captured_calls.append((operation, exc))
                raise _HTTPException(status_code=500, detail=f"Failed: {exc}")

            mock_find.side_effect = _find
            mock_resolve.side_effect = _resolve
            mock_handle.side_effect = _handle

            from api.routers.messages import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.delete("/api/v1/messages/1234567890")
            assert resp.status_code == 500
            assert len(captured_calls) == 1
            assert captured_calls[0][0] == "delete message"
            assert isinstance(captured_calls[0][1], RuntimeError)

    def test_delete_message_resolve_bot_raises_generic_exception(self, mock_bot):
        """Lines 264-266: resolve_bot raising a non-discord exception in delete_message
        triggers the except Exception handler."""
        from fastapi import HTTPException as _HTTPException

        _evict_discord_modules()
        app = FastAPI()
        app.state.bot = mock_bot

        captured_calls = []

        with patch("api.routers.messages.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.messages.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.messages._find_message", new_callable=AsyncMock), \
             patch("api.routers.messages.MessageConverter"), \
             patch("api.routers.messages.EmbedConverter"):

            async def _resolve(req):
                raise OSError("network failure")

            async def _handle(operation, exc):
                captured_calls.append((operation, exc))
                raise _HTTPException(status_code=500, detail=f"Failed: {exc}")

            mock_resolve.side_effect = _resolve
            mock_handle.side_effect = _handle

            from api.routers.messages import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.delete("/api/v1/messages/1234567890")
            assert resp.status_code == 500
            assert len(captured_calls) == 1
            assert captured_calls[0][0] == "delete message"
            assert isinstance(captured_calls[0][1], OSError)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
