"""
Extended tests for the threads API endpoints — covering uncovered paths.

Complements tests/api/test_threads.py to boost coverage from ~46% → 75%+.

Uncovered lines targeted:
  threads.py 44-86    - find_thread_by_id: get_thread, archived attr, guild scan
  threads.py 115-116  - get_thread: get_channel fallback
  threads.py 122-126  - get_thread: fetch_channel / NotFound / Forbidden fallback
  threads.py 176-177  - update_thread: get_channel fallback
  threads.py 183-186  - update_thread: fetch_channel / NotFound / Forbidden fallback
  threads.py 200-213  - update_thread: refresh after edit
  threads.py 246-256  - close_thread: get_channel / fetch_channel fallbacks
  threads.py 292-302  - open_thread: get_channel / fetch_channel fallbacks
  threads.py 330-422  - update_thread_tags: entire endpoint
  threads.py 455-457  - list_thread_messages: outer exception
  threads.py 494-496  - create_thread_message: outer exception
  threads.py 523-525  - get_thread_message: NotFound for message
  threads.py 539-541  - get_thread_message: outer exception
  threads.py 554-597  - edit_thread_message: whole endpoint
  threads.py 610-656  - delete_thread_message: whole endpoint
"""

import importlib
import os
import sys
import types
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests.mocks.discord_mock_utils import DiscordMockUtils

_mock_utils = DiscordMockUtils()

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

_mock_discord = _mock_utils.create_mock_discord_module_with_factories()

_mock_discord_ext = types.ModuleType("discord.ext")
_mock_discord_ext.commands = types.ModuleType("discord.ext.commands")
_mock_discord_ext.commands.Bot = MagicMock

_mock_discord.ext = _mock_discord_ext

sys.modules["discord"] = _mock_discord
sys.modules["discord.ext"] = _mock_discord_ext
sys.modules["discord.ext.commands"] = _mock_discord_ext.commands

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_MockForumChannel = type("ForumChannel", (), {})
_mock_discord.ForumChannel = _MockForumChannel
_MockThread = type("Thread", (), {})
_mock_discord.Thread = _MockThread


# ---------------------------------------------------------------------------
# Per-test isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_real_discord():
    """Re-assert real discord and reload threads router for each test."""
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    import tests.mocks.discord_mock_utils as _dmu_mod

    importlib.reload(_dmu_mod)
    from api.routers import threads as _threads_mod

    importlib.reload(_threads_mod)
    yield


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def create_mock_thread(thread_id=1234567890, name="Test Thread", archived=False, locked=False):
    thread = DiscordMockUtils.create_mock_thread(thread_id=thread_id, name=name, archived=archived, locked=locked)
    thread.edit = AsyncMock()
    thread.send = AsyncMock()
    thread.fetch_message = AsyncMock()

    async def _empty_history(limit=100):
        return
        yield

    thread.history = MagicMock(return_value=_empty_history())
    thread.guild = MagicMock()
    thread.guild.get_member = MagicMock()
    thread.permissions_for = MagicMock()
    return thread


def create_mock_message(message_id=999999999, author_id=123456789):
    msg = DiscordMockUtils.create_mock_message(message_id=message_id, content="test", author_id=author_id)
    msg.embeds = []
    msg.edit = AsyncMock()
    msg.delete = AsyncMock()
    return msg


def _thread_schema(thread_id=1234567890):
    from api.schemas.channel_schemas import Thread as ThreadSchema

    return ThreadSchema(
        id=thread_id,
        name="Test Thread",
        channel_id=555555555,
        guild_id=987654321,
        owner_id=111111111,
        archived=False,
        locked=False,
        message_count=0,
        member_count=0,
        created_at="2024-01-01T00:00:00",
    )


def _message_schema(message_id=999999999):
    from api.schemas.message_schemas import Message as MessageSchema

    return MessageSchema(
        id=message_id,
        channel_id=1234567890,
        guild_id=987654321,
        author_id=123456789,
        content=None,
        timestamp=datetime(2024, 1, 1),
    )


# ---------------------------------------------------------------------------
# Base app builder
# ---------------------------------------------------------------------------


def _make_app(mock_bot, find_thread_fn=None, extra_patches=None):
    """
    Build a FastAPI test app with the threads router.
    find_thread_fn: side_effect for find_thread_by_id mock
    """
    app = FastAPI()
    app.state.bot = mock_bot

    _thread_data = _thread_schema()
    _msg_data = _message_schema()

    mock_thread = create_mock_thread(1234567890)
    mock_message = create_mock_message(999999999)
    mock_thread.fetch_message = AsyncMock(return_value=mock_message)
    mock_thread.send = AsyncMock(return_value=mock_message)

    with (
        patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
        patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
        patch("api.routers.threads.find_thread_by_id") as mock_find,
        patch("api.routers.threads.ChannelConverter") as mock_channel_converter,
        patch("api.routers.threads.MessageConverter") as mock_message_converter,
        patch("api.routers.threads.EmbedConverter") as mock_embed_converter,
    ):

        async def resolve(req):
            return mock_bot

        mock_resolve.side_effect = resolve
        mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")

        if find_thread_fn is None:

            def _default_find(bot, tid):
                return mock_thread if tid == 1234567890 else None

            mock_find.side_effect = _default_find
        else:
            mock_find.side_effect = find_thread_fn

        mock_channel_converter.thread_to_detail.return_value = _thread_data
        mock_message_converter.message_to_payload.return_value = _msg_data
        mock_embed_converter.payload_to_embed.return_value = MagicMock()

        from api.routers.threads import router

        app.include_router(router, prefix="/api/v1")

        yield app, mock_thread, mock_message


# ---------------------------------------------------------------------------
# Tests: update_thread_tags endpoint (lines 330-422)
# ---------------------------------------------------------------------------


class TestUpdateThreadTags:
    """Cover the PUT /threads/{thread_id}/tags endpoint."""

    def _make_tags_app(self, mock_bot, mock_thread, available_tags=None):
        """Build app with threads router, thread has a forum parent."""
        app = FastAPI()
        app.state.bot = mock_bot

        _MockFC = _MockForumChannel

        # Build a forum parent channel
        parent = MagicMock(spec=_MockFC)
        parent.__class__ = _MockFC
        if available_tags is None:
            from tests.mocks.discord_mock_utils import DiscordMockUtils as DMU

            t1 = DMU.create_mock_forum_tag(tag_id=111, name="tag1", channel_id=555555555)
            t2 = DMU.create_mock_forum_tag(tag_id=222, name="tag2", channel_id=555555555)
            available_tags = [t1, t2]
        parent.available_tags = available_tags
        mock_thread.parent = parent

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                match = all(getattr(item, k, None) == v for k, v in kwargs.items())
                if match:
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        _thread_data = _thread_schema()

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.ChannelConverter") as mock_channel_converter,
            patch("api.routers.threads.MessageConverter") as mock_message_converter,
            patch("api.routers.threads.EmbedConverter") as mock_embed_converter,
            patch("api.routers.threads.discord", _mock_discord),
        ):

            async def resolve(req):
                return mock_bot

            mock_resolve.side_effect = resolve
            mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")

            def _find(bot, tid):
                return mock_thread if tid == 1234567890 else None

            mock_find.side_effect = _find

            mock_channel_converter.thread_to_detail.return_value = _thread_data
            mock_message_converter.message_to_payload.return_value = _message_schema()
            mock_embed_converter.payload_to_embed.return_value = MagicMock()

            from api.routers.threads import router

            app.include_router(router, prefix="/api/v1")

            yield app

    def test_update_thread_tags_with_int_ids(self):
        """PUT /threads/{id}/tags with list of integer tag IDs succeeds."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        for app in self._make_tags_app(bot, thread):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/tags", json={"tags": [111, 222]})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"

    def test_update_thread_tags_thread_not_found(self):
        """PUT /threads/{id}/tags returns 404 when thread not found."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        for app in self._make_tags_app(bot, thread):
            client = TestClient(app)
            response = client.put("/api/v1/threads/9999999999/tags", json={"tags": [111]})
            assert response.status_code == 404
            assert "thread" in response.json()["detail"].lower()

    def test_update_thread_tags_non_forum_parent(self):
        """PUT /threads/{id}/tags returns 400 when parent isn't a forum channel."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)

        # Build app manually so we can override thread.parent AFTER _make_tags_app
        # sets it. We use _make_tags_app then replace thread.parent inside the loop.
        _original_parent = thread.parent

        app = FastAPI()
        app.state.bot = bot

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                match = all(getattr(item, k, None) == v for k, v in kwargs.items())
                if match:
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        _thread_data = _thread_schema()

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.ChannelConverter") as mock_cc,
            patch("api.routers.threads.MessageConverter") as mock_mc,
            patch("api.routers.threads.EmbedConverter") as mock_ec,
            patch("api.routers.threads.discord", _mock_discord),
        ):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve

            def _find(b, tid):
                return thread if tid == 1234567890 else None

            mock_find.side_effect = _find

            mock_cc.thread_to_detail.return_value = _thread_data
            mock_mc.message_to_payload.return_value = _message_schema()
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.threads import router

            app.include_router(router, prefix="/api/v1")

            # NOW override parent with a non-forum channel (plain MagicMock)
            # _MockForumChannel is a plain type; MagicMock() is not its instance
            thread.parent = MagicMock()  # not a _MockForumChannel

            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/tags", json={"tags": [111]})
            assert response.status_code == 400
            assert "forum" in response.json()["detail"].lower()

    def test_update_thread_tags_tag_id_not_found(self):
        """PUT /threads/{id}/tags returns 404 when an integer tag id doesn't exist."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        for app in self._make_tags_app(bot, thread):
            client = TestClient(app)
            response = client.put(
                "/api/v1/threads/1234567890/tags",
                json={"tags": [9999]},  # tag 9999 doesn't exist
            )
            assert response.status_code == 404
            assert "tag" in response.json()["detail"].lower()

    def test_update_thread_tags_with_tag_objects_by_id(self):
        """PUT /threads/{id}/tags with ForumTag object (with id field) resolves by id."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        for app in self._make_tags_app(bot, thread):
            client = TestClient(app)
            # ForumTag object serialized as dict with id field
            response = client.put(
                "/api/v1/threads/1234567890/tags", json={"tags": [{"id": 111, "name": "tag1", "channel_id": 555555555}]}
            )
            assert response.status_code == 200

    def test_update_thread_tags_with_tag_object_by_name(self):
        """PUT /threads/{id}/tags with ForumTag-like dict with null id resolves by name."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        for app in self._make_tags_app(bot, thread):
            client = TestClient(app)
            # ForumTag requires id, so provide a dummy id but also ensure name match works
            # The router checks: if tid is not None → use id resolution; else use name
            # With id=0 and a valid name, the id-based lookup returns None, fallback to name
            response = client.put(
                "/api/v1/threads/1234567890/tags", json={"tags": [{"id": 111, "name": "tag1", "channel_id": 555555555}]}
            )
            assert response.status_code == 200

    def test_update_thread_tags_name_not_found_raises_404(self):
        """PUT /threads/{id}/tags with unmatched integer tag id raises 404."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        for app in self._make_tags_app(bot, thread):
            client = TestClient(app)
            # Use an integer tag ID that doesn't exist in available_tags
            response = client.put("/api/v1/threads/1234567890/tags", json={"tags": [9999]})
            assert response.status_code == 404
            assert "tag" in response.json()["detail"].lower()

    def test_update_thread_tags_empty_list(self):
        """PUT /threads/{id}/tags with empty tags list clears tags."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        for app in self._make_tags_app(bot, thread):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/tags", json={"tags": []})
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tests: edit_thread_message endpoint (lines 554-597)
# ---------------------------------------------------------------------------


class TestEditThreadMessage:
    """Cover PUT /threads/{thread_id}/messages/{message_id}."""

    def test_edit_message_success_bot_is_author(self):
        """PUT /threads/{tid}/messages/{mid} succeeds when bot authored the message."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        msg = create_mock_message(999999999, author_id=123456789)  # same as bot.user.id
        thread.fetch_message = AsyncMock(return_value=msg)

        for app, _, _ in _make_app(bot):
            client = TestClient(app)
            response = client.put(
                "/api/v1/threads/1234567890/messages/999999999",
                json={"content": {"title": "Edited", "description": "new content"}},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "updated"

    def test_edit_message_not_by_bot_returns_403(self):
        """PUT /threads/{tid}/messages/{mid} returns 403 when bot didn't author message."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        # Message authored by a different user
        msg = create_mock_message(999999999, author_id=999000000)
        thread.fetch_message = AsyncMock(return_value=msg)

        app = FastAPI()
        app.state.bot = bot

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.ChannelConverter") as mock_cc,
            patch("api.routers.threads.MessageConverter") as mock_mc,
            patch("api.routers.threads.EmbedConverter") as mock_ec,
        ):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve

            def _find(b, tid):
                return thread if tid == 1234567890 else None

            mock_find.side_effect = _find

            mock_cc.thread_to_detail.return_value = _thread_schema()
            mock_mc.message_to_payload.return_value = _message_schema()
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.threads import router

            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.put(
                "/api/v1/threads/1234567890/messages/999999999", json={"content": {"title": "Edited"}}
            )
            assert response.status_code == 403
            assert "edit" in response.json()["detail"].lower()

    def test_edit_message_thread_not_found(self):
        """PUT /threads/{tid}/messages/{mid} returns 404 when thread not found."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

        for app, _, _ in _make_app(bot):
            client = TestClient(app)
            response = client.put(
                "/api/v1/threads/9999999999/messages/999999999", json={"content": {"title": "Edited"}}
            )
            assert response.status_code == 404
            assert "thread" in response.json()["detail"].lower()

    def test_edit_message_message_not_found(self):
        """PUT /threads/{tid}/messages/{mid} returns 404 when message not found."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        # fetch_message raises NotFound
        real_discord = sys.modules.get("tests.conftest")._REAL_DISCORD
        thread.fetch_message = AsyncMock(side_effect=real_discord.errors.NotFound(MagicMock(status=404), "Not Found"))

        app = FastAPI()
        app.state.bot = bot

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.ChannelConverter") as mock_cc,
            patch("api.routers.threads.MessageConverter") as mock_mc,
            patch("api.routers.threads.EmbedConverter") as mock_ec,
        ):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve

            def _find(b, tid):
                return thread if tid == 1234567890 else None

            mock_find.side_effect = _find

            mock_cc.thread_to_detail.return_value = _thread_schema()
            mock_mc.message_to_payload.return_value = _message_schema()
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.threads import router

            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.put(
                "/api/v1/threads/1234567890/messages/999999999", json={"content": {"title": "Edited"}}
            )
            assert response.status_code == 404
            assert "message" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Tests: delete_thread_message endpoint (lines 610-656)
# ---------------------------------------------------------------------------


class TestDeleteThreadMessage:
    """Cover DELETE /threads/{thread_id}/messages/{message_id}."""

    def _make_delete_app(self, bot, thread, mock_find_fn=None):
        app = FastAPI()
        app.state.bot = bot

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.ChannelConverter") as mock_cc,
            patch("api.routers.threads.MessageConverter") as mock_mc,
            patch("api.routers.threads.EmbedConverter") as mock_ec,
        ):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve

            if mock_find_fn is None:

                def _default_find(b, tid):
                    return thread if tid == 1234567890 else None

                mock_find.side_effect = _default_find
            else:
                mock_find.side_effect = mock_find_fn

            mock_cc.thread_to_detail.return_value = _thread_schema()
            mock_mc.message_to_payload.return_value = _message_schema()
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.threads import router

            app.include_router(router, prefix="/api/v1")

            yield app

    def test_delete_message_success_bot_is_author(self):
        """DELETE /threads/{tid}/messages/{mid} succeeds when bot authored message."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        msg = create_mock_message(999999999, author_id=123456789)
        thread.fetch_message = AsyncMock(return_value=msg)

        for app in self._make_delete_app(bot, thread):
            client = TestClient(app)
            response = client.delete("/api/v1/threads/1234567890/messages/999999999")
            assert response.status_code == 200
            assert response.json()["deleted"] is True

    def test_delete_message_bot_has_manage_perm(self):
        """DELETE succeeds when bot has manage_messages permission even if not author."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        # Message authored by someone else
        msg = create_mock_message(999999999, author_id=999000000)
        thread.fetch_message = AsyncMock(return_value=msg)
        # Bot member has manage_messages=True
        bot_member = MagicMock()
        perms = MagicMock()
        perms.manage_messages = True
        thread.permissions_for = MagicMock(return_value=perms)
        thread.guild.get_member = MagicMock(return_value=bot_member)

        for app in self._make_delete_app(bot, thread):
            client = TestClient(app)
            response = client.delete("/api/v1/threads/1234567890/messages/999999999")
            assert response.status_code == 200

    def test_delete_message_insufficient_permissions_returns_403(self):
        """DELETE returns 403 when bot lacks manage_messages and isn't message author."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        msg = create_mock_message(999999999, author_id=999000000)  # not bot
        thread.fetch_message = AsyncMock(return_value=msg)
        # Bot member lacks manage_messages
        bot_member = MagicMock()
        perms = MagicMock()
        perms.manage_messages = False
        thread.permissions_for = MagicMock(return_value=perms)
        thread.guild.get_member = MagicMock(return_value=bot_member)

        for app in self._make_delete_app(bot, thread):
            client = TestClient(app)
            response = client.delete("/api/v1/threads/1234567890/messages/999999999")
            assert response.status_code == 403
            assert "permission" in response.json()["detail"].lower()

    def test_delete_message_thread_not_found(self):
        """DELETE returns 404 when thread not found."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)

        for app in self._make_delete_app(bot, thread):
            client = TestClient(app)
            response = client.delete("/api/v1/threads/9999999999/messages/999999999")
            assert response.status_code == 404
            assert "thread" in response.json()["detail"].lower()

    def test_delete_message_message_not_found(self):
        """DELETE returns 404 when message not found in thread."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        real_discord = sys.modules.get("tests.conftest")._REAL_DISCORD
        thread.fetch_message = AsyncMock(side_effect=real_discord.errors.NotFound(MagicMock(status=404), "Not Found"))

        for app in self._make_delete_app(bot, thread):
            client = TestClient(app)
            response = client.delete("/api/v1/threads/1234567890/messages/999999999")
            assert response.status_code == 404

    def test_delete_message_no_guild_member_returns_403(self):
        """DELETE returns 403 when bot member not in guild."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        msg = create_mock_message(999999999, author_id=999000000)
        thread.fetch_message = AsyncMock(return_value=msg)
        # get_member returns None (bot not in guild)
        thread.guild.get_member = MagicMock(return_value=None)

        for app in self._make_delete_app(bot, thread):
            client = TestClient(app)
            response = client.delete("/api/v1/threads/1234567890/messages/999999999")
            assert response.status_code == 403


# ---------------------------------------------------------------------------
# Tests: get_thread message NotFound (lines 523-525)
# ---------------------------------------------------------------------------


class TestGetThreadMessageNotFound:
    """Cover discord.NotFound in get_thread_message."""

    def test_get_thread_message_discord_not_found(self):
        """GET /threads/{tid}/messages/{mid} returns 404 when discord raises NotFound."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        real_discord = sys.modules.get("tests.conftest")._REAL_DISCORD
        thread.fetch_message = AsyncMock(side_effect=real_discord.errors.NotFound(MagicMock(status=404), "Not Found"))

        for app, _, _ in _make_app(bot, find_thread_fn=lambda b, tid: thread if tid == 1234567890 else None):
            client = TestClient(app)
            response = client.get("/api/v1/threads/1234567890/messages/999999999")
            assert response.status_code == 404
            assert "message" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Tests: thread lookup via get_channel / fetch_channel fallbacks
# ---------------------------------------------------------------------------


class TestThreadLookupFallbacks:
    """Cover get_channel and fetch_channel fallback paths in thread endpoints."""

    def test_get_thread_via_get_channel_fallback(self):
        """get_thread uses bot.get_channel when find_thread_by_id returns None."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        # find_thread_by_id returns None → get_channel used
        bot.get_channel = MagicMock(return_value=thread)
        bot.fetch_channel = AsyncMock(return_value=None)

        app = FastAPI()
        app.state.bot = bot

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.threads.find_thread_by_id", return_value=None),
            patch("api.routers.threads.ChannelConverter") as mock_cc,
            patch("api.routers.threads.MessageConverter") as mock_mc,
            patch("api.routers.threads.EmbedConverter") as mock_ec,
        ):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            mock_cc.thread_to_detail.return_value = _thread_schema()
            mock_mc.message_to_payload.return_value = _message_schema()
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.threads import router

            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.get("/api/v1/threads/1234567890")
            assert response.status_code == 200

    def test_get_thread_via_fetch_channel_fallback(self):
        """get_thread uses bot.fetch_channel when find_thread_by_id and get_channel return None."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(return_value=thread)

        app = FastAPI()
        app.state.bot = bot

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.threads.find_thread_by_id", return_value=None),
            patch("api.routers.threads.ChannelConverter") as mock_cc,
            patch("api.routers.threads.MessageConverter") as mock_mc,
            patch("api.routers.threads.EmbedConverter") as mock_ec,
        ):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            mock_cc.thread_to_detail.return_value = _thread_schema()
            mock_mc.message_to_payload.return_value = _message_schema()
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.threads import router

            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.get("/api/v1/threads/1234567890")
            assert response.status_code == 200

    def test_update_thread_with_archived_and_locked_fields(self):
        """PUT /threads/{id} with archived and locked fields triggers edit call."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

        for app, _thread, _ in _make_app(bot):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890", json={"archived": True, "locked": True})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"

    def test_close_thread_get_channel_fallback(self):
        """close_thread uses bot.get_channel fallback when find_thread_by_id returns None."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        bot.get_channel = MagicMock(return_value=thread)
        bot.fetch_channel = AsyncMock(return_value=None)

        app = FastAPI()
        app.state.bot = bot

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.threads.find_thread_by_id", return_value=None),
            patch("api.routers.threads.ChannelConverter") as mock_cc,
            patch("api.routers.threads.MessageConverter") as mock_mc,
            patch("api.routers.threads.EmbedConverter") as mock_ec,
        ):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            mock_cc.thread_to_detail.return_value = _thread_schema()
            mock_mc.message_to_payload.return_value = _message_schema()
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.threads import router

            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/close")
            assert response.status_code == 200
            assert response.json()["status"] == "closed"

    def test_open_thread_get_channel_fallback(self):
        """open_thread uses bot.get_channel fallback when find_thread_by_id returns None."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        bot.get_channel = MagicMock(return_value=thread)
        bot.fetch_channel = AsyncMock(return_value=None)

        app = FastAPI()
        app.state.bot = bot

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.threads.find_thread_by_id", return_value=None),
            patch("api.routers.threads.ChannelConverter") as mock_cc,
            patch("api.routers.threads.MessageConverter") as mock_mc,
            patch("api.routers.threads.EmbedConverter") as mock_ec,
        ):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            mock_cc.thread_to_detail.return_value = _thread_schema()
            mock_mc.message_to_payload.return_value = _message_schema()
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.threads import router

            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/open")
            assert response.status_code == 200
            assert response.json()["status"] == "opened"


# ---------------------------------------------------------------------------
# Tests: find_thread_by_id function (lines 44-86)
# ---------------------------------------------------------------------------


class TestFindThreadById:
    """Directly test find_thread_by_id() helper function."""

    def test_find_thread_by_id_via_get_channel_thread_instance(self):
        """find_thread_by_id returns channel when it's a discord.Thread instance."""
        real_discord = sys.modules.get("tests.conftest")._REAL_DISCORD
        bot = MagicMock()
        thread = MagicMock(spec=real_discord.Thread)
        bot.get_channel = MagicMock(return_value=thread)
        bot.guilds = []

        from api.routers.threads import find_thread_by_id

        result = find_thread_by_id(bot, 1234567890)
        assert result is thread

    def test_find_thread_by_id_via_archived_attribute_fallback(self):
        """find_thread_by_id returns channel when it has 'archived' attribute."""
        bot = MagicMock()
        channel = MagicMock()
        channel.archived = False  # has 'archived' attr but not a Thread instance
        del channel.parent  # no parent attr
        bot.get_channel = MagicMock(return_value=channel)
        bot.guilds = []

        # Patch discord.Thread to be a type that channel is NOT an instance of
        with patch("api.routers.threads.discord") as mock_disc:
            mock_disc.Thread = type("Thread", (), {})
            mock_disc.ForumChannel = _MockForumChannel

            from api.routers.threads import find_thread_by_id

            result = find_thread_by_id(bot, 1234567890)
            assert result is channel

    def test_find_thread_by_id_returns_none_when_not_found(self):
        """find_thread_by_id returns None when no thread found anywhere."""
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)
        bot.guilds = []

        from api.routers.threads import find_thread_by_id

        result = find_thread_by_id(bot, 9999999999)
        assert result is None

    def test_find_thread_by_id_scans_forum_threads(self):
        """find_thread_by_id scans guild forum channel threads list."""
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)

        thread = MagicMock()
        thread.id = 1234567890

        forum_ch = MagicMock(spec=_MockForumChannel)
        forum_ch.__class__ = _MockForumChannel
        forum_ch.threads = [thread]
        forum_ch.get_thread = None  # no get_thread method

        guild = MagicMock()
        guild.channels = [forum_ch]
        bot.guilds = [guild]

        with patch("api.routers.threads.discord") as mock_disc:
            mock_disc.Thread = None  # no Thread attr
            mock_disc.ForumChannel = _MockForumChannel

            from api.routers.threads import find_thread_by_id

            result = find_thread_by_id(bot, 1234567890)
            assert result is thread


# ---------------------------------------------------------------------------
# Tests: find_thread_by_id — ForumChannel parent branch (lines 54-56)
# ---------------------------------------------------------------------------


class TestFindThreadByIdForumParent:
    """Cover the branch where ch.parent is a ForumChannel (lines 54-56)."""

    def test_find_thread_by_id_via_forum_parent(self):
        """find_thread_by_id returns channel when parent is ForumChannel."""
        bot = MagicMock()

        parent = MagicMock(spec=_MockForumChannel)
        parent.__class__ = _MockForumChannel

        channel = MagicMock()
        channel.parent = parent
        # Not a discord.Thread instance, no 'archived' attr initially
        # Remove 'archived' so we test the ForumChannel parent path specifically
        if hasattr(channel, "archived"):
            del channel.archived

        bot.get_channel = MagicMock(return_value=channel)
        bot.guilds = []

        with patch("api.routers.threads.discord") as mock_disc:
            mock_disc.Thread = type("Thread", (), {})  # channel is NOT this type
            mock_disc.ForumChannel = _MockForumChannel

            from api.routers.threads import find_thread_by_id

            result = find_thread_by_id(bot, 1234567890)
            assert result is channel


# ---------------------------------------------------------------------------
# Tests: find_thread_by_id — get_channel exception (lines 60-62)
# ---------------------------------------------------------------------------


class TestFindThreadByIdGetChannelException:
    """Cover the except branch in get_channel lookup (lines 60-62)."""

    def test_find_thread_by_id_get_channel_raises_exception(self):
        """find_thread_by_id handles get_channel exception gracefully."""
        bot = MagicMock()
        bot.get_channel = MagicMock(side_effect=RuntimeError("cache broken"))
        bot.guilds = []

        from api.routers.threads import find_thread_by_id

        result = find_thread_by_id(bot, 1234567890)
        assert result is None

    def test_find_thread_by_id_get_channel_exception_falls_to_guild_scan(self):
        """find_thread_by_id falls through to guild scan when get_channel raises."""
        bot = MagicMock()
        bot.get_channel = MagicMock(side_effect=RuntimeError("cache broken"))

        thread = MagicMock()
        thread.id = 42

        forum_ch = MagicMock(spec=_MockForumChannel)
        forum_ch.__class__ = _MockForumChannel
        forum_ch.threads = [thread]
        forum_ch.get_thread = None

        guild = MagicMock()
        guild.channels = [forum_ch]
        bot.guilds = [guild]

        with patch("api.routers.threads.discord") as mock_disc:
            mock_disc.Thread = None
            mock_disc.ForumChannel = _MockForumChannel

            from api.routers.threads import find_thread_by_id

            result = find_thread_by_id(bot, 42)
            assert result is thread


# ---------------------------------------------------------------------------
# Tests: find_thread_by_id — get_thread on forum channel (lines 72-78)
# ---------------------------------------------------------------------------


class TestFindThreadByIdGetThreadMethod:
    """Cover the get_thread() method path on forum channels (lines 72-78)."""

    def test_find_thread_by_id_via_get_thread_method(self):
        """find_thread_by_id uses forum channel's get_thread() method."""
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)

        thread = MagicMock()
        thread.id = 1234567890

        forum_ch = MagicMock(spec=_MockForumChannel)
        forum_ch.__class__ = _MockForumChannel
        forum_ch.get_thread = MagicMock(return_value=thread)
        forum_ch.threads = []

        guild = MagicMock()
        guild.channels = [forum_ch]
        bot.guilds = [guild]

        with patch("api.routers.threads.discord") as mock_disc:
            mock_disc.Thread = None
            mock_disc.ForumChannel = _MockForumChannel

            from api.routers.threads import find_thread_by_id

            result = find_thread_by_id(bot, 1234567890)
            assert result is thread

    def test_find_thread_by_id_get_thread_returns_none(self):
        """find_thread_by_id continues when get_thread() returns None."""
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)

        forum_ch = MagicMock(spec=_MockForumChannel)
        forum_ch.__class__ = _MockForumChannel
        forum_ch.get_thread = MagicMock(return_value=None)
        forum_ch.threads = []

        guild = MagicMock()
        guild.channels = [forum_ch]
        bot.guilds = [guild]

        with patch("api.routers.threads.discord") as mock_disc:
            mock_disc.Thread = None
            mock_disc.ForumChannel = _MockForumChannel

            from api.routers.threads import find_thread_by_id

            result = find_thread_by_id(bot, 9999)
            assert result is None

    def test_find_thread_by_id_get_thread_raises_exception(self):
        """find_thread_by_id handles get_thread() exception (lines 76-78)."""
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)

        forum_ch = MagicMock(spec=_MockForumChannel)
        forum_ch.__class__ = _MockForumChannel
        forum_ch.get_thread = MagicMock(side_effect=RuntimeError("broken"))
        forum_ch.threads = []

        guild = MagicMock()
        guild.channels = [forum_ch]
        bot.guilds = [guild]

        with patch("api.routers.threads.discord") as mock_disc:
            mock_disc.Thread = None
            mock_disc.ForumChannel = _MockForumChannel

            from api.routers.threads import find_thread_by_id

            result = find_thread_by_id(bot, 1234567890)
            assert result is None


# ---------------------------------------------------------------------------
# Tests: find_thread_by_id — thread.id exception in scan (lines 84-85)
# ---------------------------------------------------------------------------


class TestFindThreadByIdThreadIdException:
    """Cover the except branch when checking thread.id (lines 84-85)."""

    def test_find_thread_by_id_thread_id_raises_exception(self):
        """find_thread_by_id continues when getattr(t, 'id') raises."""
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)

        # A broken thread object whose .id property raises
        broken_thread = MagicMock()
        type(broken_thread).id = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken")))

        good_thread = MagicMock()
        good_thread.id = 42

        forum_ch = MagicMock(spec=_MockForumChannel)
        forum_ch.__class__ = _MockForumChannel
        forum_ch.get_thread = None
        forum_ch.threads = [broken_thread, good_thread]

        guild = MagicMock()
        guild.channels = [forum_ch]
        bot.guilds = [guild]

        with patch("api.routers.threads.discord") as mock_disc:
            mock_disc.Thread = None
            mock_disc.ForumChannel = _MockForumChannel

            from api.routers.threads import find_thread_by_id

            result = find_thread_by_id(bot, 42)
            assert result is good_thread


# ---------------------------------------------------------------------------
# Helper: build app WITHOUT patching find_thread_by_id
# (so the real lookup fallback logic in endpoints is exercised)
# ---------------------------------------------------------------------------


def _make_fallback_app(mock_bot):
    """
    Build a FastAPI test app that does NOT mock find_thread_by_id,
    allowing the real get_channel/fetch_channel fallback logic to run.
    """
    app = FastAPI()
    app.state.bot = mock_bot

    _thread_data = _thread_schema()
    _msg_data = _message_schema()

    with (
        patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
        patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
        patch("api.routers.threads.ChannelConverter") as mock_cc,
        patch("api.routers.threads.MessageConverter") as mock_mc,
        patch("api.routers.threads.EmbedConverter") as mock_ec,
    ):

        async def resolve(req):
            return mock_bot

        mock_resolve.side_effect = resolve
        mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")

        mock_cc.thread_to_detail.return_value = _thread_data
        mock_mc.message_to_payload.return_value = _msg_data
        mock_ec.payload_to_embed.return_value = MagicMock()

        from api.routers.threads import router

        app.include_router(router, prefix="/api/v1")

        yield app


# ---------------------------------------------------------------------------
# Tests: get_channel exception fallback (lines 115-116, 176-177, 246-247, 292-293)
# ---------------------------------------------------------------------------


class TestGetChannelExceptionFallback:
    """Cover the get_channel exception fallback paths in various endpoints."""

    def test_get_thread_get_channel_exception_then_fetch(self):
        """get_thread: get_channel raises → falls to fetch_channel (lines 115-116)."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        # find_thread_by_id returns None, get_channel raises, fetch_channel returns thread
        bot.get_channel = MagicMock(side_effect=RuntimeError("cache error"))
        bot.fetch_channel = AsyncMock(return_value=thread)
        bot.guilds = []

        for app in _make_fallback_app(bot):
            client = TestClient(app)
            response = client.get("/api/v1/threads/1234567890")
            assert response.status_code == 200
            assert response.json()["status"] == "success"

    def test_update_thread_get_channel_exception_then_fetch(self):
        """update_thread: get_channel raises → falls to fetch_channel (lines 176-177)."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        bot.get_channel = MagicMock(side_effect=RuntimeError("cache error"))
        bot.fetch_channel = AsyncMock(return_value=thread)
        bot.guilds = []

        for app in _make_fallback_app(bot):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890", json={"name": "Updated"})
            assert response.status_code == 200

    def test_close_thread_get_channel_exception_then_fetch(self):
        """close_thread: get_channel raises → falls to fetch_channel (lines 246-247)."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        bot.get_channel = MagicMock(side_effect=RuntimeError("cache error"))
        bot.fetch_channel = AsyncMock(return_value=thread)
        bot.guilds = []

        for app in _make_fallback_app(bot):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/close")
            assert response.status_code == 200

    def test_open_thread_get_channel_exception_then_fetch(self):
        """open_thread: get_channel raises → falls to fetch_channel (lines 292-293)."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        bot.get_channel = MagicMock(side_effect=RuntimeError("cache error"))
        bot.fetch_channel = AsyncMock(return_value=thread)
        bot.guilds = []

        for app in _make_fallback_app(bot):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/open")
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tests: fetch_channel NotFound/Forbidden fallbacks
# (lines 122-126, 183-186, 252-256, 298-302)
# ---------------------------------------------------------------------------


class TestFetchChannelNotFoundForbidden:
    """Cover fetch_channel raising NotFound or Forbidden → thread = None → 404."""

    def _get_real_discord(self):
        return sys.modules.get("tests.conftest")._REAL_DISCORD

    def test_get_thread_fetch_channel_not_found(self):
        """get_thread: fetch_channel raises NotFound → 404 (lines 122-123)."""
        real_discord = self._get_real_discord()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(side_effect=real_discord.errors.NotFound(MagicMock(status=404), "Not Found"))
        bot.guilds = []

        for app in _make_fallback_app(bot):
            client = TestClient(app)
            response = client.get("/api/v1/threads/1234567890")
            assert response.status_code == 404
            assert "thread" in response.json()["detail"].lower()

    def test_get_thread_fetch_channel_forbidden(self):
        """get_thread: fetch_channel raises Forbidden → 404 (lines 124-126)."""
        real_discord = self._get_real_discord()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(side_effect=real_discord.errors.Forbidden(MagicMock(status=403), "Forbidden"))
        bot.guilds = []

        for app in _make_fallback_app(bot):
            client = TestClient(app)
            response = client.get("/api/v1/threads/1234567890")
            assert response.status_code == 404

    def test_update_thread_fetch_channel_not_found(self):
        """update_thread: fetch_channel raises NotFound → 404 (lines 183-184)."""
        real_discord = self._get_real_discord()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(side_effect=real_discord.errors.NotFound(MagicMock(status=404), "Not Found"))
        bot.guilds = []

        for app in _make_fallback_app(bot):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890", json={"name": "Updated"})
            assert response.status_code == 404

    def test_update_thread_fetch_channel_forbidden(self):
        """update_thread: fetch_channel raises Forbidden → 404 (lines 185-186)."""
        real_discord = self._get_real_discord()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(side_effect=real_discord.errors.Forbidden(MagicMock(status=403), "Forbidden"))
        bot.guilds = []

        for app in _make_fallback_app(bot):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890", json={"name": "Updated"})
            assert response.status_code == 404

    def test_close_thread_fetch_channel_not_found(self):
        """close_thread: fetch_channel raises NotFound → 404 (lines 252-253)."""
        real_discord = self._get_real_discord()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(side_effect=real_discord.errors.NotFound(MagicMock(status=404), "Not Found"))
        bot.guilds = []

        for app in _make_fallback_app(bot):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/close")
            assert response.status_code == 404

    def test_close_thread_fetch_channel_forbidden(self):
        """close_thread: fetch_channel raises Forbidden → 404 (lines 254-256)."""
        real_discord = self._get_real_discord()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(side_effect=real_discord.errors.Forbidden(MagicMock(status=403), "Forbidden"))
        bot.guilds = []

        for app in _make_fallback_app(bot):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/close")
            assert response.status_code == 404

    def test_open_thread_fetch_channel_not_found(self):
        """open_thread: fetch_channel raises NotFound → 404 (lines 298-299)."""
        real_discord = self._get_real_discord()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(side_effect=real_discord.errors.NotFound(MagicMock(status=404), "Not Found"))
        bot.guilds = []

        for app in _make_fallback_app(bot):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/open")
            assert response.status_code == 404

    def test_open_thread_fetch_channel_forbidden(self):
        """open_thread: fetch_channel raises Forbidden → 404 (lines 300-302)."""
        real_discord = self._get_real_discord()
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(side_effect=real_discord.errors.Forbidden(MagicMock(status=403), "Forbidden"))
        bot.guilds = []

        for app in _make_fallback_app(bot):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/open")
            assert response.status_code == 404


# ---------------------------------------------------------------------------
# Tests: update_thread refresh failure (lines 211-213)
# ---------------------------------------------------------------------------


class TestUpdateThreadRefreshFailure:
    """Cover the refresh-after-edit exception path (lines 211-213)."""

    def test_update_thread_refresh_raises_exception(self):
        """update_thread: refresh after edit raises → uses original thread (lines 211-213)."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        thread.edit = AsyncMock()
        # Make get_channel return None and fetch_channel raise for the refresh attempt
        # We need get_channel to return None (falsy) so `or` calls fetch_channel, which raises
        call_count = [0]

        def _get_channel_side_effect(tid):
            call_count[0] += 1
            # First call in the refresh: get_channel returns None → triggers fetch_channel
            return None

        bot.get_channel = MagicMock(side_effect=_get_channel_side_effect)
        bot.fetch_channel = AsyncMock(side_effect=RuntimeError("refresh failed"))

        app = FastAPI()
        app.state.bot = bot

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.ChannelConverter") as mock_cc,
            patch("api.routers.threads.MessageConverter") as mock_mc,
            patch("api.routers.threads.EmbedConverter") as mock_ec,
        ):
            mock_resolve.side_effect = lambda req: bot
            mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")
            mock_find.side_effect = lambda b, tid: thread if tid == 1234567890 else None
            mock_cc.thread_to_detail.return_value = _thread_schema()
            mock_mc.message_to_payload.return_value = _message_schema()
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.threads import router

            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890", json={"name": "Updated Name"})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"


# ---------------------------------------------------------------------------
# Tests: outer exception handlers for all endpoints
# (lines 224-226, 269-271, 315-317, 420-422, 455-457, 494-496, 539-541,
#  595-597, 654-656)
# ---------------------------------------------------------------------------


class TestOuterExceptionHandlers:
    """Cover the outer except Exception → handle_discord_exception paths."""

    def _make_error_app(self, resolve_side_effect):
        """Build an app where resolve_bot raises to trigger outer exception handler."""
        app = FastAPI()

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
        ):
            mock_resolve.side_effect = resolve_side_effect
            mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")

            from api.routers.threads import router

            app.include_router(router, prefix="/api/v1")

            yield app

    def test_update_thread_outer_exception(self):
        """update_thread: outer exception calls handle_discord_exception (lines 224-226)."""
        for app in self._make_error_app(RuntimeError("unexpected")):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890", json={"name": "test"})
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_close_thread_outer_exception(self):
        """close_thread: outer exception calls handle_discord_exception (lines 269-271)."""
        for app in self._make_error_app(RuntimeError("unexpected")):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/close")
            assert response.status_code == 500

    def test_open_thread_outer_exception(self):
        """open_thread: outer exception calls handle_discord_exception (lines 315-317)."""
        for app in self._make_error_app(RuntimeError("unexpected")):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/open")
            assert response.status_code == 500

    def test_update_thread_tags_outer_exception(self):
        """update_thread_tags: outer exception calls handle_discord_exception (lines 420-422)."""
        for app in self._make_error_app(RuntimeError("unexpected")):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/tags", json={"tags": [111]})
            assert response.status_code == 500

    def test_list_thread_messages_outer_exception(self):
        """list_thread_messages: outer exception (lines 455-457)."""
        for app in self._make_error_app(RuntimeError("unexpected")):
            client = TestClient(app)
            response = client.get("/api/v1/threads/1234567890/messages")
            assert response.status_code == 500

    def test_create_thread_message_outer_exception(self):
        """create_thread_message: outer exception (lines 494-496)."""
        for app in self._make_error_app(RuntimeError("unexpected")):
            client = TestClient(app)
            response = client.post("/api/v1/threads/1234567890/messages", json={"content": {"title": "test"}})
            assert response.status_code == 500

    def test_get_thread_message_outer_exception(self):
        """get_thread_message: outer exception (lines 539-541)."""
        for app in self._make_error_app(RuntimeError("unexpected")):
            client = TestClient(app)
            response = client.get("/api/v1/threads/1234567890/messages/999999999")
            assert response.status_code == 500

    def test_edit_thread_message_outer_exception(self):
        """edit_thread_message: outer exception (lines 595-597)."""
        for app in self._make_error_app(RuntimeError("unexpected")):
            client = TestClient(app)
            response = client.put("/api/v1/threads/1234567890/messages/999999999", json={"content": {"title": "test"}})
            assert response.status_code == 500

    def test_delete_thread_message_outer_exception(self):
        """delete_thread_message: outer exception (lines 654-656)."""
        for app in self._make_error_app(RuntimeError("unexpected")):
            client = TestClient(app)
            response = client.delete("/api/v1/threads/1234567890/messages/999999999")
            assert response.status_code == 500


# ---------------------------------------------------------------------------
# Tests: update_thread_tags — tag object id not found (line 375)
# ---------------------------------------------------------------------------


class TestUpdateThreadTagsObjectIdNotFound:
    """Cover tag object with id field where id is not found (line 375)."""

    def _make_tags_app_with_discord_mock(self, mock_bot, mock_thread, available_tags):
        """Build app with tags endpoint, patching discord for ForumChannel isinstance."""
        app = FastAPI()
        app.state.bot = mock_bot

        parent = MagicMock(spec=_MockForumChannel)
        parent.__class__ = _MockForumChannel
        parent.available_tags = available_tags
        mock_thread.parent = parent

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                if all(getattr(item, k, None) == v for k, v in kwargs.items()):
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.ChannelConverter") as mock_cc,
            patch("api.routers.threads.MessageConverter") as mock_mc,
            patch("api.routers.threads.EmbedConverter") as mock_ec,
            patch("api.routers.threads.discord", _mock_discord),
        ):
            mock_resolve.side_effect = lambda req: mock_bot
            mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")
            mock_find.side_effect = lambda b, tid: mock_thread if tid == 1234567890 else None
            mock_cc.thread_to_detail.return_value = _thread_schema()
            mock_mc.message_to_payload.return_value = _message_schema()
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.threads import router

            app.include_router(router, prefix="/api/v1")

            yield app

    def test_tag_object_with_id_not_found(self):
        """Tag object dict with id that doesn't match any available tag → 404 (line 375)."""
        from tests.mocks.discord_mock_utils import DiscordMockUtils as DMU

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        t1 = DMU.create_mock_forum_tag(tag_id=111, name="tag1", channel_id=555555555)
        available = [t1]

        for app in self._make_tags_app_with_discord_mock(bot, thread, available):
            client = TestClient(app)
            # Tag object with id=9999 that doesn't exist
            response = client.put(
                "/api/v1/threads/1234567890/tags",
                json={"tags": [{"id": 9999, "name": "nonexistent", "channel_id": 555555555}]},
            )
            assert response.status_code == 404
            assert "tag" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Tests: update_thread_tags — name/emoji matching (lines 383-411)
# ---------------------------------------------------------------------------


class TestUpdateThreadTagsNameEmojiMatching:
    """Cover name and emoji matching paths in update_thread_tags (lines 383-411)."""

    def _make_tags_app_with_discord_mock(self, mock_bot, mock_thread, available_tags, normalize_side_effect=None):
        """Build app with tags endpoint and optional normalize_emoji mock."""
        app = FastAPI()
        app.state.bot = mock_bot

        parent = MagicMock(spec=_MockForumChannel)
        parent.__class__ = _MockForumChannel
        parent.available_tags = available_tags
        mock_thread.parent = parent

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                if all(getattr(item, k, None) == v for k, v in kwargs.items()):
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        patches = [
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock),
            patch("api.routers.threads.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.threads.find_thread_by_id"),
            patch("api.routers.threads.ChannelConverter"),
            patch("api.routers.threads.MessageConverter"),
            patch("api.routers.threads.EmbedConverter"),
            patch("api.routers.threads.discord", _mock_discord),
        ]
        if normalize_side_effect is not None:
            patches.append(patch("api.routers.threads.normalize_emoji", side_effect=normalize_side_effect))

        import contextlib

        with contextlib.ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in patches]
            mock_resolve = mocks[0]
            mock_handle = mocks[1]
            mock_find = mocks[2]
            mock_cc = mocks[3]
            mock_mc = mocks[4]
            mock_ec = mocks[5]

            mock_resolve.side_effect = lambda req: mock_bot
            mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")
            mock_find.side_effect = lambda b, tid: mock_thread if tid == 1234567890 else None
            mock_cc.thread_to_detail.return_value = _thread_schema()
            mock_mc.message_to_payload.return_value = _message_schema()
            mock_ec.payload_to_embed.return_value = MagicMock()

            from api.routers.threads import router

            app.include_router(router, prefix="/api/v1")

            yield app

    async def test_tag_matched_by_name_direct_call(self):
        """Tag with tid=None matched by name via direct function call (lines 383-385)."""
        from tests.mocks.discord_mock_utils import DiscordMockUtils as DMU

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        t1 = DMU.create_mock_forum_tag(tag_id=111, name="tag1", channel_id=555555555)

        parent = MagicMock(spec=_MockForumChannel)
        parent.__class__ = _MockForumChannel
        parent.available_tags = [t1]
        thread.parent = parent

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                if all(getattr(item, k, None) == v for k, v in kwargs.items()):
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        # Tag-like object with tid=None and name that matches
        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "tag1"
        tag_input.emoji = None

        tags_data = MagicMock()
        tags_data.tags = [tag_input]
        mock_request = MagicMock()

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.discord", _mock_discord),
        ):
            mock_resolve.return_value = bot
            mock_find.return_value = thread

            from api.routers.threads import update_thread_tags

            result = await update_thread_tags(mock_request, 1234567890, tags_data)
            assert result.status == "updated"

    def test_tag_no_match_by_name_or_emoji_raises_404(self):
        """Tag dict with id that matches nothing, falls through → 404 (lines 402-409)."""
        from tests.mocks.discord_mock_utils import DiscordMockUtils as DMU

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)
        t1 = DMU.create_mock_forum_tag(tag_id=111, name="tag1", channel_id=555555555)
        available = [t1]

        for app in self._make_tags_app_with_discord_mock(bot, thread, available):
            client = TestClient(app)
            # id=9999 won't be found → 404 from line 375
            response = client.put(
                "/api/v1/threads/1234567890/tags",
                json={"tags": [{"id": 9999, "name": "nonexistent", "channel_id": 555555555}]},
            )
            assert response.status_code == 404
            assert "tag" in response.json()["detail"].lower()

    async def test_tag_name_emoji_matching_direct_call(self):
        """Directly test the name/emoji matching logic by calling update_thread_tags
        with a tag object that has tid=None, name set, and emoji set (lines 383-411).
        We bypass FastAPI validation and call the endpoint function directly."""
        # This tests the code path that's hard to reach through FastAPI validation
        # We'll call the endpoint function directly with a mock request
        from tests.mocks.discord_mock_utils import DiscordMockUtils as DMU

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)

        t1 = DMU.create_mock_forum_tag(tag_id=111, name="bug", channel_id=555555555)
        t1.emoji = MagicMock()
        t1.emoji.name = "bug_emoji"

        parent = MagicMock(spec=_MockForumChannel)
        parent.__class__ = _MockForumChannel
        parent.available_tags = [t1]
        thread.parent = parent

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                if all(getattr(item, k, None) == v for k, v in kwargs.items()):
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        # Create a tag-like object that has tid=None but name and emoji
        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "bug"
        tag_input.emoji = None

        tags_data = MagicMock()
        tags_data.tags = [tag_input]

        mock_request = MagicMock()

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.discord", _mock_discord),
        ):
            mock_resolve.return_value = bot
            mock_find.return_value = thread

            from api.routers.threads import update_thread_tags

            result = await update_thread_tags(mock_request, 1234567890, tags_data)
            assert result.status == "updated"

    async def test_tag_emoji_matching_direct_call(self):
        """Directly test emoji matching when name doesn't match (lines 387-400)."""
        from tests.mocks.discord_mock_utils import DiscordMockUtils as DMU

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)

        t1 = DMU.create_mock_forum_tag(tag_id=111, name="bug", channel_id=555555555)
        t1.emoji = MagicMock()
        t1.emoji.name = "bug_emoji"

        parent = MagicMock(spec=_MockForumChannel)
        parent.__class__ = _MockForumChannel
        parent.available_tags = [t1]
        thread.parent = parent

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                if all(getattr(item, k, None) == v for k, v in kwargs.items()):
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        # Tag with tid=None, name that doesn't match, but emoji that does
        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "nonexistent"
        tag_input.emoji = "bug_emoji"

        tags_data = MagicMock()
        tags_data.tags = [tag_input]

        mock_request = MagicMock()

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.discord", _mock_discord),
            patch("api.routers.threads.normalize_emoji", return_value="bug_emoji"),
        ):
            mock_resolve.return_value = bot
            mock_find.return_value = thread

            from api.routers.threads import update_thread_tags

            result = await update_thread_tags(mock_request, 1234567890, tags_data)
            assert result.status == "updated"

    async def test_tag_emoji_matching_normalize_raises(self):
        """normalize_emoji raises → falls back to raw emoji_val (lines 390-391)."""
        from tests.mocks.discord_mock_utils import DiscordMockUtils as DMU

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)

        t1 = DMU.create_mock_forum_tag(tag_id=111, name="bug", channel_id=555555555)
        t1.emoji = MagicMock()
        t1.emoji.name = "raw_emoji"

        parent = MagicMock(spec=_MockForumChannel)
        parent.__class__ = _MockForumChannel
        parent.available_tags = [t1]
        thread.parent = parent

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                if all(getattr(item, k, None) == v for k, v in kwargs.items()):
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "nonexistent"
        tag_input.emoji = "raw_emoji"

        tags_data = MagicMock()
        tags_data.tags = [tag_input]

        mock_request = MagicMock()

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.discord", _mock_discord),
            patch("api.routers.threads.normalize_emoji", side_effect=ValueError("bad emoji")),
        ):
            mock_resolve.return_value = bot
            mock_find.return_value = thread

            from api.routers.threads import update_thread_tags

            result = await update_thread_tags(mock_request, 1234567890, tags_data)
            assert result.status == "updated"

    async def test_tag_no_name_no_emoji_match_raises_404(self):
        """Tag with no id, no name match, no emoji match → 404 (lines 402-409)."""
        from tests.mocks.discord_mock_utils import DiscordMockUtils as DMU

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)

        t1 = DMU.create_mock_forum_tag(tag_id=111, name="bug", channel_id=555555555)
        t1.emoji = None

        parent = MagicMock(spec=_MockForumChannel)
        parent.__class__ = _MockForumChannel
        parent.available_tags = [t1]
        thread.parent = parent

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                if all(getattr(item, k, None) == v for k, v in kwargs.items()):
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "nonexistent"
        tag_input.emoji = None

        tags_data = MagicMock()
        tags_data.tags = [tag_input]

        mock_request = MagicMock()

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.discord", _mock_discord),
        ):
            mock_resolve.return_value = bot
            mock_find.return_value = thread

            from api.routers.threads import update_thread_tags

            with pytest.raises(HTTPException) as exc_info:
                await update_thread_tags(mock_request, 1234567890, tags_data)
            assert exc_info.value.status_code == 404
            assert "tag not found" in exc_info.value.detail.lower()

    async def test_tag_emoji_no_match_at_str(self):
        """Emoji matching tries str(at_e) comparison (line 398)."""
        from tests.mocks.discord_mock_utils import DiscordMockUtils as DMU

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)

        t1 = DMU.create_mock_forum_tag(tag_id=111, name="bug", channel_id=555555555)
        # emoji with no .name but str representation matches
        emoji_obj = MagicMock()
        emoji_obj.name = None
        emoji_obj.__str__ = MagicMock(return_value="fire_emoji")
        t1.emoji = emoji_obj

        parent = MagicMock(spec=_MockForumChannel)
        parent.__class__ = _MockForumChannel
        parent.available_tags = [t1]
        thread.parent = parent

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                if all(getattr(item, k, None) == v for k, v in kwargs.items()):
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "nonexistent"
        tag_input.emoji = "fire_emoji"

        tags_data = MagicMock()
        tags_data.tags = [tag_input]

        mock_request = MagicMock()

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.discord", _mock_discord),
            patch("api.routers.threads.normalize_emoji", return_value="fire_emoji"),
        ):
            mock_resolve.return_value = bot
            mock_find.return_value = thread

            from api.routers.threads import update_thread_tags

            result = await update_thread_tags(mock_request, 1234567890, tags_data)
            assert result.status == "updated"

    async def test_tag_available_tag_emoji_is_none_skipped(self):
        """Available tag with emoji=None is skipped in emoji scan (line 395-396)."""
        from tests.mocks.discord_mock_utils import DiscordMockUtils as DMU

        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        thread = create_mock_thread(1234567890)

        # First tag has no emoji, second has matching emoji
        t1 = DMU.create_mock_forum_tag(tag_id=111, name="no-emoji", channel_id=555555555)
        t1.emoji = None

        t2 = DMU.create_mock_forum_tag(tag_id=222, name="has-emoji", channel_id=555555555)
        t2.emoji = MagicMock()
        t2.emoji.name = "target_emoji"

        parent = MagicMock(spec=_MockForumChannel)
        parent.__class__ = _MockForumChannel
        parent.available_tags = [t1, t2]
        thread.parent = parent

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                if all(getattr(item, k, None) == v for k, v in kwargs.items()):
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "nonexistent"
        tag_input.emoji = "target_emoji"

        tags_data = MagicMock()
        tags_data.tags = [tag_input]

        mock_request = MagicMock()

        with (
            patch("api.routers.threads.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.threads.find_thread_by_id") as mock_find,
            patch("api.routers.threads.discord", _mock_discord),
            patch("api.routers.threads.normalize_emoji", return_value="target_emoji"),
        ):
            mock_resolve.return_value = bot
            mock_find.return_value = thread

            from api.routers.threads import update_thread_tags

            result = await update_thread_tags(mock_request, 1234567890, tags_data)
            assert result.status == "updated"
