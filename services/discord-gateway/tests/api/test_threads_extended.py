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

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap. ``resolve_bot``, ``handle_discord_exception``,
``find_thread_by_id``, ``ChannelConverter``, ``MessageConverter`` and
``EmbedConverter`` are all real and unpatched everywhere except:
  - the ``TestOuterExceptionHandlers`` tests, which patch only ``resolve_bot``
    to raise so the real ``handle_discord_exception`` maps it to a real 500;
  - ``TestThreadLookupFallbacks``, which patches only ``find_thread_by_id``
    (return_value=None) to isolate and exercise the *router's own* second
    ``bot.get_channel``/``bot.fetch_channel`` fallback attempts, given
    ``find_thread_by_id`` itself is unit-tested directly in
    ``TestFindThreadById*`` below and already covers its own get_channel
    fast-path;
  - a few ``update_thread_tags`` tests that call the router function directly
    (bypassing the FastAPI/ASGI request cycle) patch ``resolve_bot`` because
    a bare ``MagicMock()`` request has no real ``app.state.bot``.
Real ``discord.Thread``/``discord.ForumChannel``/``discord.ForumTag`` are
used via ``spec=`` throughout so ``isinstance`` checks are genuine.
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import tests.mocks.discord_mock_utils as discord_mock_utils

DiscordMockUtils = discord_mock_utils.DiscordMockUtils
create_discord_not_found = discord_mock_utils.create_discord_not_found
create_discord_forbidden = discord_mock_utils.create_discord_forbidden

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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def create_mock_thread(thread_id=1234567890, name="Test Thread", archived=False, locked=False):
    """Real-spec'd mock Thread whose edit() mutates state like the real thing."""
    thread = DiscordMockUtils.create_mock_thread(thread_id=thread_id, name=name, archived=archived, locked=locked)
    thread.__class__ = discord.Thread

    async def _edit(**kwargs):
        for k, v in kwargs.items():
            setattr(thread, k, v)

    thread.edit = AsyncMock(side_effect=_edit)
    thread.send = AsyncMock()
    thread.fetch_message = AsyncMock()

    async def _empty_history(limit=100):
        return
        yield  # pragma: no cover - makes this an async generator

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


def _make_bot(thread=None, guilds=None):
    """A bot whose get_channel/fetch_channel resolve `thread` by id, raising a real
    discord.NotFound on a genuine cache-and-fetch miss."""
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")

    def get_channel(tid):
        if thread is not None and tid == thread.id:
            return thread
        return None

    async def fetch_channel(tid):
        found = get_channel(tid)
        if found is None:
            raise create_discord_not_found(f"Channel {tid} not found")
        return found

    bot.get_channel = get_channel
    bot.fetch_channel = AsyncMock(side_effect=fetch_channel)
    bot.guilds = guilds or []
    return bot


def _make_app(bot):
    """A real FastAPI app with the threads router and a real bot state — no patches."""
    app = FastAPI()
    app.state.bot = bot
    from api.routers.threads import router

    app.include_router(router, prefix="/api/v1")
    return app


def _make_forum_parent(tags=None):
    """A real-spec'd ForumChannel usable as a thread's `.parent`."""
    parent = MagicMock(spec=discord.ForumChannel)
    parent.available_tags = tags if tags is not None else []
    return parent


# ---------------------------------------------------------------------------
# Tests: update_thread_tags endpoint (lines 330-422)
# ---------------------------------------------------------------------------


class TestUpdateThreadTags:
    """Cover the PUT /threads/{thread_id}/tags endpoint."""

    def _app_with_thread(self, thread, available_tags=None):
        if available_tags is None:
            t1 = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="tag1", channel_id=555555555)
            t2 = DiscordMockUtils.create_mock_forum_tag(tag_id=222, name="tag2", channel_id=555555555)
            available_tags = [t1, t2]
        thread.parent = _make_forum_parent(available_tags)
        thread.edit = AsyncMock()
        bot = _make_bot(thread=thread)
        return _make_app(bot)

    def test_update_thread_tags_with_int_ids(self):
        """PUT /threads/{id}/tags with list of integer tag IDs succeeds via the real
        thread.edit(applied_tags=...) call."""
        thread = create_mock_thread(1234567890)
        app = self._app_with_thread(thread)
        client = TestClient(app)
        response = client.put("/api/v1/threads/1234567890/tags", json={"tags": [111, 222]})
        assert response.status_code == 200
        assert response.json()["status"] == "updated"
        thread.edit.assert_awaited_once()
        applied = thread.edit.await_args.kwargs["applied_tags"]
        assert {t.id for t in applied} == {111, 222}

    def test_update_thread_tags_thread_not_found(self):
        """PUT /threads/{id}/tags returns 404 when thread not found."""
        thread = create_mock_thread(1234567890)
        app = self._app_with_thread(thread)
        client = TestClient(app)
        response = client.put("/api/v1/threads/9999999999/tags", json={"tags": [111]})
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()

    def test_update_thread_tags_non_forum_parent(self):
        """PUT /threads/{id}/tags returns 400 when parent isn't a forum channel (real isinstance check)."""
        thread = create_mock_thread(1234567890)
        thread.parent = MagicMock()  # plain MagicMock, genuinely not a discord.ForumChannel
        bot = _make_bot(thread=thread)
        app = _make_app(bot)

        client = TestClient(app)
        response = client.put("/api/v1/threads/1234567890/tags", json={"tags": [111]})
        assert response.status_code == 400
        assert "forum" in response.json()["detail"].lower()

    def test_update_thread_tags_tag_id_not_found(self):
        """PUT /threads/{id}/tags returns 404 when an integer tag id doesn't exist."""
        thread = create_mock_thread(1234567890)
        app = self._app_with_thread(thread)
        client = TestClient(app)
        response = client.put("/api/v1/threads/1234567890/tags", json={"tags": [9999]})
        assert response.status_code == 404
        assert "tag" in response.json()["detail"].lower()

    def test_update_thread_tags_with_tag_objects_by_id(self):
        """PUT /threads/{id}/tags with ForumTag object (with id field) resolves by id."""
        thread = create_mock_thread(1234567890)
        app = self._app_with_thread(thread)
        client = TestClient(app)
        response = client.put(
            "/api/v1/threads/1234567890/tags", json={"tags": [{"id": 111, "name": "tag1", "channel_id": 555555555}]}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "updated"

    async def test_update_thread_tags_with_tag_object_by_name(self):
        """A tag-like object with tid=None resolves by name against the real available_tags
        (id-required ForumTagListRequest validation can't express a null id over HTTP, so this
        calls the router function directly, as TestUpdateThreadTagsNameEmojiMatching does)."""
        thread = create_mock_thread(1234567890)
        t1 = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="tag1", channel_id=555555555)
        thread.parent = _make_forum_parent([t1])
        bot = _make_bot(thread=thread)

        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "tag1"
        tag_input.emoji = None

        tags_data = MagicMock()
        tags_data.tags = [tag_input]
        mock_request = MagicMock()

        with patch("api.routers.threads.resolve_bot", new_callable=AsyncMock, side_effect=lambda req: bot):
            from api.routers.threads import update_thread_tags

            result = await update_thread_tags(mock_request, 1234567890, tags_data)
            assert result.status == "updated"

    def test_update_thread_tags_name_not_found_raises_404(self):
        """PUT /threads/{id}/tags with a tag object whose name matches nothing → real 404."""
        thread = create_mock_thread(1234567890)
        app = self._app_with_thread(thread)
        client = TestClient(app)
        response = client.put(
            "/api/v1/threads/1234567890/tags",
            json={"tags": [{"id": 424242, "name": "no-such-tag", "channel_id": 555555555}]},
        )
        assert response.status_code == 404
        assert "tag" in response.json()["detail"].lower()

    def test_update_thread_tags_empty_list(self):
        """PUT /threads/{id}/tags with empty tags list clears tags."""
        thread = create_mock_thread(1234567890)
        app = self._app_with_thread(thread)
        client = TestClient(app)
        response = client.put("/api/v1/threads/1234567890/tags", json={"tags": []})
        assert response.status_code == 200
        assert response.json()["status"] == "updated"
        thread.edit.assert_awaited_once_with(applied_tags=[])


# ---------------------------------------------------------------------------
# Tests: edit_thread_message endpoint (lines 554-597)
# ---------------------------------------------------------------------------


class TestEditThreadMessage:
    """Cover PUT /threads/{thread_id}/messages/{message_id}."""

    def test_edit_message_success_bot_is_author(self):
        """PUT succeeds when bot authored the message; response reflects the real edited content."""
        thread = create_mock_thread(1234567890)
        msg = create_mock_message(999999999, author_id=123456789)  # same as bot.user.id
        thread.fetch_message = AsyncMock(return_value=msg)
        bot = _make_bot(thread=thread)

        client = TestClient(_make_app(bot))
        response = client.put(
            "/api/v1/threads/1234567890/messages/999999999",
            json={"content": {"title": "Edited", "description": "new content"}},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "updated"
        msg.edit.assert_awaited_once()

    def test_edit_message_not_by_bot_returns_403(self):
        """PUT returns 403 when bot didn't author the message."""
        thread = create_mock_thread(1234567890)
        msg = create_mock_message(999999999, author_id=999000000)
        thread.fetch_message = AsyncMock(return_value=msg)
        bot = _make_bot(thread=thread)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/1234567890/messages/999999999", json={"content": {"title": "Edited"}})
        assert response.status_code == 403
        assert "edit" in response.json()["detail"].lower()

    def test_edit_message_thread_not_found(self):
        """PUT returns 404 when thread not found."""
        bot = _make_bot(thread=None)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/9999999999/messages/999999999", json={"content": {"title": "Edited"}})
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()

    def test_edit_message_message_not_found(self):
        """PUT returns 404 when message not found (real discord.NotFound)."""
        thread = create_mock_thread(1234567890)
        thread.fetch_message = AsyncMock(side_effect=create_discord_not_found())
        bot = _make_bot(thread=thread)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/1234567890/messages/999999999", json={"content": {"title": "Edited"}})
        assert response.status_code == 404
        assert "message" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Tests: delete_thread_message endpoint (lines 610-656)
# ---------------------------------------------------------------------------


class TestDeleteThreadMessage:
    """Cover DELETE /threads/{thread_id}/messages/{message_id}."""

    def test_delete_message_success_bot_is_author(self):
        """DELETE succeeds when bot authored the message."""
        thread = create_mock_thread(1234567890)
        msg = create_mock_message(999999999, author_id=123456789)
        thread.fetch_message = AsyncMock(return_value=msg)
        bot = _make_bot(thread=thread)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/threads/1234567890/messages/999999999")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        msg.delete.assert_awaited_once()

    def test_delete_message_bot_has_manage_perm(self):
        """DELETE succeeds when bot has manage_messages permission even if not author."""
        thread = create_mock_thread(1234567890)
        msg = create_mock_message(999999999, author_id=999000000)
        thread.fetch_message = AsyncMock(return_value=msg)
        bot_member = MagicMock()
        perms = MagicMock()
        perms.manage_messages = True
        thread.permissions_for = MagicMock(return_value=perms)
        thread.guild.get_member = MagicMock(return_value=bot_member)
        bot = _make_bot(thread=thread)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/threads/1234567890/messages/999999999")
        assert response.status_code == 200
        assert response.json()["deleted"] is True

    def test_delete_message_insufficient_permissions_returns_403(self):
        """DELETE returns 403 when bot lacks manage_messages and isn't message author."""
        thread = create_mock_thread(1234567890)
        msg = create_mock_message(999999999, author_id=999000000)  # not bot
        thread.fetch_message = AsyncMock(return_value=msg)
        bot_member = MagicMock()
        perms = MagicMock()
        perms.manage_messages = False
        thread.permissions_for = MagicMock(return_value=perms)
        thread.guild.get_member = MagicMock(return_value=bot_member)
        bot = _make_bot(thread=thread)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/threads/1234567890/messages/999999999")
        assert response.status_code == 403
        assert "permission" in response.json()["detail"].lower()

    def test_delete_message_thread_not_found(self):
        """DELETE returns 404 when thread not found."""
        bot = _make_bot(thread=None)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/threads/9999999999/messages/999999999")
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()

    def test_delete_message_message_not_found(self):
        """DELETE returns 404 when message not found (real discord.NotFound)."""
        thread = create_mock_thread(1234567890)
        thread.fetch_message = AsyncMock(side_effect=create_discord_not_found())
        bot = _make_bot(thread=thread)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/threads/1234567890/messages/999999999")
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_delete_message_no_guild_member_returns_403(self):
        """DELETE returns 403 when bot member not in guild."""
        thread = create_mock_thread(1234567890)
        msg = create_mock_message(999999999, author_id=999000000)
        thread.fetch_message = AsyncMock(return_value=msg)
        thread.guild.get_member = MagicMock(return_value=None)
        bot = _make_bot(thread=thread)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/threads/1234567890/messages/999999999")
        assert response.status_code == 403
        assert "detail" in response.json()


# ---------------------------------------------------------------------------
# Tests: get_thread message NotFound (lines 523-525)
# ---------------------------------------------------------------------------


class TestGetThreadMessageNotFound:
    """Cover discord.NotFound in get_thread_message."""

    def test_get_thread_message_discord_not_found(self):
        """GET /threads/{tid}/messages/{mid} returns 404 when discord raises a real NotFound."""
        thread = create_mock_thread(1234567890)
        thread.fetch_message = AsyncMock(side_effect=create_discord_not_found())
        bot = _make_bot(thread=thread)

        client = TestClient(_make_app(bot))
        response = client.get("/api/v1/threads/1234567890/messages/999999999")
        assert response.status_code == 404
        assert "message" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Tests: thread lookup via get_channel / fetch_channel fallbacks
# ---------------------------------------------------------------------------


class TestThreadLookupFallbacks:
    """Cover the *router's own* get_channel/fetch_channel fallback paths — reached only when
    find_thread_by_id (itself real-tested in TestFindThreadById* below) returns None. Patches
    only find_thread_by_id (return_value=None) to isolate this specific caller-side branch;
    resolve_bot/handle_discord_exception/converters remain real."""

    def test_get_thread_via_get_channel_fallback(self):
        """get_thread uses bot.get_channel when find_thread_by_id returns None."""
        thread = create_mock_thread(1234567890)
        bot = _make_bot(thread=thread)

        with patch("api.routers.threads.find_thread_by_id", return_value=None):
            client = TestClient(_make_app(bot))
            response = client.get("/api/v1/threads/1234567890")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["data"]["id"] == 1234567890

    def test_get_thread_via_fetch_channel_fallback(self):
        """get_thread uses bot.fetch_channel when find_thread_by_id and get_channel return None."""
        thread = create_mock_thread(1234567890)
        bot = _make_bot(thread=None)  # get_channel always misses
        bot.fetch_channel = AsyncMock(return_value=thread)  # fetch succeeds

        with patch("api.routers.threads.find_thread_by_id", return_value=None):
            client = TestClient(_make_app(bot))
            response = client.get("/api/v1/threads/1234567890")
            assert response.status_code == 200
            assert response.json()["status"] == "success"

    def test_update_thread_with_archived_and_locked_fields(self):
        """PUT /threads/{id} with archived and locked fields triggers a real edit call."""
        thread = create_mock_thread(1234567890)
        bot = _make_bot(thread=thread)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/1234567890", json={"archived": True, "locked": True})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["archived"] is True
        assert data["data"]["locked"] is True

    def test_close_thread_get_channel_fallback(self):
        """close_thread uses bot.get_channel fallback when find_thread_by_id returns None."""
        thread = create_mock_thread(1234567890)
        bot = _make_bot(thread=thread)

        with patch("api.routers.threads.find_thread_by_id", return_value=None):
            client = TestClient(_make_app(bot))
            response = client.put("/api/v1/threads/1234567890/close")
            assert response.status_code == 200
            assert response.json()["status"] == "closed"

    def test_open_thread_get_channel_fallback(self):
        """open_thread uses bot.get_channel fallback when find_thread_by_id returns None."""
        thread = create_mock_thread(1234567890)
        bot = _make_bot(thread=thread)

        with patch("api.routers.threads.find_thread_by_id", return_value=None):
            client = TestClient(_make_app(bot))
            response = client.put("/api/v1/threads/1234567890/open")
            assert response.status_code == 200
            assert response.json()["status"] == "opened"


# ---------------------------------------------------------------------------
# Tests: find_thread_by_id function (lines 44-86) — direct unit tests, no HTTP
# ---------------------------------------------------------------------------


class TestFindThreadById:
    """Directly test find_thread_by_id() helper function against real discord types."""

    def test_find_thread_by_id_via_get_channel_thread_instance(self):
        """find_thread_by_id returns channel when it's a real discord.Thread instance."""
        bot = MagicMock()
        thread = MagicMock(spec=discord.Thread)
        bot.get_channel = MagicMock(return_value=thread)
        bot.guilds = []

        from api.routers.threads import find_thread_by_id

        result = find_thread_by_id(bot, 1234567890)
        assert result is thread

    def test_find_thread_by_id_via_archived_attribute_fallback(self):
        """find_thread_by_id returns channel when it has an 'archived' attribute but isn't a
        real Thread/ForumChannel-parented object (a plain, unspec'd MagicMock genuinely fails
        both isinstance checks)."""
        bot = MagicMock()
        channel = MagicMock()
        channel.archived = False
        bot.get_channel = MagicMock(return_value=channel)
        bot.guilds = []

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

        forum_ch = _make_forum_parent()
        forum_ch.threads = [thread]
        forum_ch.get_thread = None  # no get_thread method available on this variant

        guild = MagicMock()
        guild.channels = [forum_ch]
        bot.guilds = [guild]

        from api.routers.threads import find_thread_by_id

        result = find_thread_by_id(bot, 1234567890)
        assert result is thread


# ---------------------------------------------------------------------------
# Tests: find_thread_by_id — ForumChannel parent branch (lines 54-56)
# ---------------------------------------------------------------------------


class TestFindThreadByIdForumParent:
    """Cover the branch where ch.parent is a real ForumChannel (lines 54-56)."""

    def test_find_thread_by_id_via_forum_parent(self):
        """find_thread_by_id returns channel when parent is a real ForumChannel and the
        channel itself has no 'archived' attribute (forcing the parent-check branch)."""
        bot = MagicMock()

        parent = _make_forum_parent()

        channel = MagicMock(spec=["parent", "id"])  # explicitly no 'archived' attribute
        channel.parent = parent

        bot.get_channel = MagicMock(return_value=channel)
        bot.guilds = []

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

        forum_ch = _make_forum_parent()
        forum_ch.threads = [thread]
        forum_ch.get_thread = None

        guild = MagicMock()
        guild.channels = [forum_ch]
        bot.guilds = [guild]

        from api.routers.threads import find_thread_by_id

        result = find_thread_by_id(bot, 42)
        assert result is thread


# ---------------------------------------------------------------------------
# Tests: find_thread_by_id — get_thread on forum channel (lines 72-78)
# ---------------------------------------------------------------------------


class TestFindThreadByIdGetThreadMethod:
    """Cover the get_thread() method path on forum channels (lines 72-78)."""

    def test_find_thread_by_id_via_get_thread_method(self):
        """find_thread_by_id uses forum channel's real get_thread() method."""
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)

        thread = MagicMock()
        thread.id = 1234567890

        forum_ch = _make_forum_parent()
        forum_ch.get_thread = MagicMock(return_value=thread)
        forum_ch.threads = []

        guild = MagicMock()
        guild.channels = [forum_ch]
        bot.guilds = [guild]

        from api.routers.threads import find_thread_by_id

        result = find_thread_by_id(bot, 1234567890)
        assert result is thread

    def test_find_thread_by_id_get_thread_returns_none(self):
        """find_thread_by_id continues when get_thread() returns None."""
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)

        forum_ch = _make_forum_parent()
        forum_ch.get_thread = MagicMock(return_value=None)
        forum_ch.threads = []

        guild = MagicMock()
        guild.channels = [forum_ch]
        bot.guilds = [guild]

        from api.routers.threads import find_thread_by_id

        result = find_thread_by_id(bot, 9999)
        assert result is None

    def test_find_thread_by_id_get_thread_raises_exception(self):
        """find_thread_by_id handles get_thread() exception (lines 76-78)."""
        bot = MagicMock()
        bot.get_channel = MagicMock(return_value=None)

        forum_ch = _make_forum_parent()
        forum_ch.get_thread = MagicMock(side_effect=RuntimeError("broken"))
        forum_ch.threads = []

        guild = MagicMock()
        guild.channels = [forum_ch]
        bot.guilds = [guild]

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

        broken_thread = MagicMock()
        type(broken_thread).id = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken")))

        good_thread = MagicMock()
        good_thread.id = 42

        forum_ch = _make_forum_parent()
        forum_ch.get_thread = None
        forum_ch.threads = [broken_thread, good_thread]

        guild = MagicMock()
        guild.channels = [forum_ch]
        bot.guilds = [guild]

        from api.routers.threads import find_thread_by_id

        result = find_thread_by_id(bot, 42)
        assert result is good_thread


# ---------------------------------------------------------------------------
# Tests: get_channel exception fallback (lines 115-116, 176-177, 246-247, 292-293)
# ---------------------------------------------------------------------------


class TestGetChannelExceptionFallback:
    """Cover the get_channel exception fallback paths in various endpoints — all real:
    find_thread_by_id's own try/except swallows the same get_channel exception and returns
    None (guild scan finds nothing either, since bot.guilds=[]), so the router's own
    get_channel retry (which also raises) is genuinely exercised before falling to fetch."""

    def test_get_thread_get_channel_exception_then_fetch(self):
        """get_thread: get_channel raises → falls to fetch_channel (lines 115-116)."""
        thread = create_mock_thread(1234567890)
        bot = _make_bot(guilds=[])
        bot.get_channel = MagicMock(side_effect=RuntimeError("cache error"))
        bot.fetch_channel = AsyncMock(return_value=thread)

        client = TestClient(_make_app(bot))
        response = client.get("/api/v1/threads/1234567890")
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_update_thread_get_channel_exception_then_fetch(self):
        """update_thread: get_channel raises → falls to fetch_channel (lines 176-177)."""
        thread = create_mock_thread(1234567890)
        bot = _make_bot(guilds=[])
        bot.get_channel = MagicMock(side_effect=RuntimeError("cache error"))
        bot.fetch_channel = AsyncMock(return_value=thread)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/1234567890", json={"name": "Updated"})
        assert response.status_code == 200
        assert response.json()["status"] == "updated"

    def test_close_thread_get_channel_exception_then_fetch(self):
        """close_thread: get_channel raises → falls to fetch_channel (lines 246-247)."""
        thread = create_mock_thread(1234567890)
        bot = _make_bot(guilds=[])
        bot.get_channel = MagicMock(side_effect=RuntimeError("cache error"))
        bot.fetch_channel = AsyncMock(return_value=thread)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/1234567890/close")
        assert response.status_code == 200
        assert response.json()["status"] == "closed"

    def test_open_thread_get_channel_exception_then_fetch(self):
        """open_thread: get_channel raises → falls to fetch_channel (lines 292-293)."""
        thread = create_mock_thread(1234567890)
        bot = _make_bot(guilds=[])
        bot.get_channel = MagicMock(side_effect=RuntimeError("cache error"))
        bot.fetch_channel = AsyncMock(return_value=thread)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/1234567890/open")
        assert response.status_code == 200
        assert response.json()["status"] == "opened"


# ---------------------------------------------------------------------------
# Tests: fetch_channel NotFound/Forbidden fallbacks
# (lines 122-126, 183-186, 252-256, 298-302)
# ---------------------------------------------------------------------------


class TestFetchChannelNotFoundForbidden:
    """Cover fetch_channel raising a real NotFound or Forbidden → thread = None → 404."""

    def test_get_thread_fetch_channel_not_found(self):
        """get_thread: fetch_channel raises NotFound → 404 (lines 122-123)."""
        bot = _make_bot(guilds=[])
        bot.fetch_channel = AsyncMock(side_effect=create_discord_not_found())

        client = TestClient(_make_app(bot))
        response = client.get("/api/v1/threads/1234567890")
        assert response.status_code == 404
        assert "thread" in response.json()["detail"].lower()

    def test_get_thread_fetch_channel_forbidden(self):
        """get_thread: fetch_channel raises Forbidden → 404 (lines 124-126)."""
        bot = _make_bot(guilds=[])
        bot.fetch_channel = AsyncMock(side_effect=create_discord_forbidden())

        client = TestClient(_make_app(bot))
        response = client.get("/api/v1/threads/1234567890")
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_update_thread_fetch_channel_not_found(self):
        """update_thread: fetch_channel raises NotFound → 404 (lines 183-184)."""
        bot = _make_bot(guilds=[])
        bot.fetch_channel = AsyncMock(side_effect=create_discord_not_found())

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/1234567890", json={"name": "Updated"})
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_update_thread_fetch_channel_forbidden(self):
        """update_thread: fetch_channel raises Forbidden → 404 (lines 185-186)."""
        bot = _make_bot(guilds=[])
        bot.fetch_channel = AsyncMock(side_effect=create_discord_forbidden())

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/1234567890", json={"name": "Updated"})
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_close_thread_fetch_channel_not_found(self):
        """close_thread: fetch_channel raises NotFound → 404 (lines 252-253)."""
        bot = _make_bot(guilds=[])
        bot.fetch_channel = AsyncMock(side_effect=create_discord_not_found())

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/1234567890/close")
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_close_thread_fetch_channel_forbidden(self):
        """close_thread: fetch_channel raises Forbidden → 404 (lines 254-256)."""
        bot = _make_bot(guilds=[])
        bot.fetch_channel = AsyncMock(side_effect=create_discord_forbidden())

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/1234567890/close")
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_open_thread_fetch_channel_not_found(self):
        """open_thread: fetch_channel raises NotFound → 404 (lines 298-299)."""
        bot = _make_bot(guilds=[])
        bot.fetch_channel = AsyncMock(side_effect=create_discord_not_found())

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/1234567890/open")
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_open_thread_fetch_channel_forbidden(self):
        """open_thread: fetch_channel raises Forbidden → 404 (lines 300-302)."""
        bot = _make_bot(guilds=[])
        bot.fetch_channel = AsyncMock(side_effect=create_discord_forbidden())

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/1234567890/open")
        assert response.status_code == 404
        assert "detail" in response.json()


# ---------------------------------------------------------------------------
# Tests: update_thread refresh failure (lines 211-213)
# ---------------------------------------------------------------------------


class TestUpdateThreadRefreshFailure:
    """Cover the refresh-after-edit exception path (lines 211-213)."""

    def test_update_thread_refresh_raises_exception(self):
        """update_thread: refresh after edit raises → uses original (already-edited-in-place)
        thread object rather than failing the request."""
        thread = create_mock_thread(1234567890)
        bot = _make_bot(thread=thread)
        # find_thread_by_id's own get_channel call (call #1) must still find the thread for
        # the initial resolution; only the *refresh* attempt (call #2, post-edit) should miss
        # cache and fall to fetch_channel, which raises → refresh is abandoned, the
        # already-edited-in-place thread object is used for the response instead.
        call_count = [0]

        def _get_channel(tid):
            call_count[0] += 1
            return thread if call_count[0] == 1 else None

        bot.get_channel = MagicMock(side_effect=_get_channel)
        bot.fetch_channel = AsyncMock(side_effect=RuntimeError("refresh failed"))

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/threads/1234567890", json={"name": "Updated Name"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["name"] == "Updated Name"


# ---------------------------------------------------------------------------
# Tests: outer exception handlers for all endpoints
# (lines 224-226, 269-271, 315-317, 420-422, 455-457, 494-496, 539-541,
#  595-597, 654-656)
# ---------------------------------------------------------------------------


class TestOuterExceptionHandlers:
    """Cover the outer except Exception → handle_discord_exception paths.

    ``resolve_bot`` (a network/readiness boundary) is patched to raise a generic error so the
    real, unpatched ``handle_discord_exception`` maps it to a genuine 500.
    """

    def _app_with_failing_resolve(self):
        app = FastAPI()
        from api.routers.threads import router

        app.include_router(router, prefix="/api/v1")
        return app

    def test_update_thread_outer_exception(self):
        """update_thread: outer exception maps to a real 500 (lines 224-226)."""
        with patch("api.routers.threads.resolve_bot", side_effect=RuntimeError("unexpected")):
            client = TestClient(self._app_with_failing_resolve())
            response = client.put("/api/v1/threads/1234567890", json={"name": "test"})
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_close_thread_outer_exception(self):
        """close_thread: outer exception maps to a real 500 (lines 269-271)."""
        with patch("api.routers.threads.resolve_bot", side_effect=RuntimeError("unexpected")):
            client = TestClient(self._app_with_failing_resolve())
            response = client.put("/api/v1/threads/1234567890/close")
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_open_thread_outer_exception(self):
        """open_thread: outer exception maps to a real 500 (lines 315-317)."""
        with patch("api.routers.threads.resolve_bot", side_effect=RuntimeError("unexpected")):
            client = TestClient(self._app_with_failing_resolve())
            response = client.put("/api/v1/threads/1234567890/open")
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_update_thread_tags_outer_exception(self):
        """update_thread_tags: outer exception maps to a real 500 (lines 420-422)."""
        with patch("api.routers.threads.resolve_bot", side_effect=RuntimeError("unexpected")):
            client = TestClient(self._app_with_failing_resolve())
            response = client.put("/api/v1/threads/1234567890/tags", json={"tags": [111]})
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_list_thread_messages_outer_exception(self):
        """list_thread_messages: outer exception (lines 455-457)."""
        with patch("api.routers.threads.resolve_bot", side_effect=RuntimeError("unexpected")):
            client = TestClient(self._app_with_failing_resolve())
            response = client.get("/api/v1/threads/1234567890/messages")
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_create_thread_message_outer_exception(self):
        """create_thread_message: outer exception (lines 494-496)."""
        with patch("api.routers.threads.resolve_bot", side_effect=RuntimeError("unexpected")):
            client = TestClient(self._app_with_failing_resolve())
            response = client.post("/api/v1/threads/1234567890/messages", json={"content": {"title": "test"}})
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_get_thread_message_outer_exception(self):
        """get_thread_message: outer exception (lines 539-541)."""
        with patch("api.routers.threads.resolve_bot", side_effect=RuntimeError("unexpected")):
            client = TestClient(self._app_with_failing_resolve())
            response = client.get("/api/v1/threads/1234567890/messages/999999999")
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_edit_thread_message_outer_exception(self):
        """edit_thread_message: outer exception (lines 595-597)."""
        with patch("api.routers.threads.resolve_bot", side_effect=RuntimeError("unexpected")):
            client = TestClient(self._app_with_failing_resolve())
            response = client.put("/api/v1/threads/1234567890/messages/999999999", json={"content": {"title": "test"}})
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_delete_thread_message_outer_exception(self):
        """delete_thread_message: outer exception (lines 654-656)."""
        with patch("api.routers.threads.resolve_bot", side_effect=RuntimeError("unexpected")):
            client = TestClient(self._app_with_failing_resolve())
            response = client.delete("/api/v1/threads/1234567890/messages/999999999")
            assert response.status_code == 500
            assert "detail" in response.json()


# ---------------------------------------------------------------------------
# Tests: update_thread_tags — tag object id not found (line 375)
# ---------------------------------------------------------------------------


class TestUpdateThreadTagsObjectIdNotFound:
    """Cover tag object with id field where id is not found (line 375)."""

    def test_tag_object_with_id_not_found(self):
        """Tag object dict with id that doesn't match any available tag → 404 (line 375)."""
        thread = create_mock_thread(1234567890)
        t1 = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="tag1", channel_id=555555555)
        thread.parent = _make_forum_parent([t1])
        bot = _make_bot(thread=thread)

        client = TestClient(_make_app(bot))
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
    """Cover name and emoji matching paths in update_thread_tags (lines 383-411).

    A few tests call the router function directly (bypassing the ASGI request cycle) because
    the request body needs a raw tag-like object with ``id=None`` — not expressible through the
    ``ForumTagListRequest`` pydantic schema's HTTP JSON validation (id must be int|ForumTag).
    Only ``resolve_bot`` is patched (there being no real ``app.state.bot`` on a bare
    ``MagicMock()`` request); ``find_thread_by_id`` and the router's own tag-matching logic
    run for real against a real-spec'd ForumChannel/ForumTag.
    """

    def _direct_call_ctx(self, bot):
        return patch("api.routers.threads.resolve_bot", new_callable=AsyncMock, side_effect=lambda req: bot)

    async def test_tag_matched_by_name_direct_call(self):
        """Tag with tid=None matched by name via direct function call (lines 383-385)."""
        thread = create_mock_thread(1234567890)
        t1 = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="tag1", channel_id=555555555)
        thread.parent = _make_forum_parent([t1])
        bot = _make_bot(thread=thread)

        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "tag1"
        tag_input.emoji = None

        tags_data = MagicMock()
        tags_data.tags = [tag_input]
        mock_request = MagicMock()

        with self._direct_call_ctx(bot):
            from api.routers.threads import update_thread_tags

            result = await update_thread_tags(mock_request, 1234567890, tags_data)
            assert result.status == "updated"

    def test_tag_no_match_by_name_or_emoji_raises_404(self):
        """Tag dict with id that matches nothing, falls through → 404 (lines 402-409)."""
        thread = create_mock_thread(1234567890)
        t1 = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="tag1", channel_id=555555555)
        thread.parent = _make_forum_parent([t1])
        bot = _make_bot(thread=thread)

        client = TestClient(_make_app(bot))
        response = client.put(
            "/api/v1/threads/1234567890/tags",
            json={"tags": [{"id": 9999, "name": "nonexistent", "channel_id": 555555555}]},
        )
        assert response.status_code == 404
        assert "tag" in response.json()["detail"].lower()

    async def test_tag_name_emoji_matching_direct_call(self):
        """Tag matched by name via direct function call, tag also has an emoji (lines 383-411)."""
        thread = create_mock_thread(1234567890)

        t1 = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="bug", channel_id=555555555)
        t1.emoji = MagicMock()
        t1.emoji.name = "bug_emoji"
        thread.parent = _make_forum_parent([t1])
        bot = _make_bot(thread=thread)

        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "bug"
        tag_input.emoji = None

        tags_data = MagicMock()
        tags_data.tags = [tag_input]
        mock_request = MagicMock()

        with self._direct_call_ctx(bot):
            from api.routers.threads import update_thread_tags

            result = await update_thread_tags(mock_request, 1234567890, tags_data)
            assert result.status == "updated"

    async def test_tag_emoji_matching_direct_call(self):
        """Directly test emoji matching when name doesn't match (lines 387-400)."""
        thread = create_mock_thread(1234567890)

        t1 = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="bug", channel_id=555555555)
        t1.emoji = MagicMock()
        t1.emoji.name = "bug_emoji"
        thread.parent = _make_forum_parent([t1])
        bot = _make_bot(thread=thread)

        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "nonexistent"
        tag_input.emoji = "bug_emoji"

        tags_data = MagicMock()
        tags_data.tags = [tag_input]
        mock_request = MagicMock()

        with self._direct_call_ctx(bot), patch("api.routers.threads.normalize_emoji", return_value="bug_emoji"):
            from api.routers.threads import update_thread_tags

            result = await update_thread_tags(mock_request, 1234567890, tags_data)
            assert result.status == "updated"

    async def test_tag_emoji_matching_normalize_raises(self):
        """normalize_emoji raises → falls back to raw emoji_val (lines 390-391)."""
        thread = create_mock_thread(1234567890)

        t1 = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="bug", channel_id=555555555)
        t1.emoji = MagicMock()
        t1.emoji.name = "raw_emoji"
        thread.parent = _make_forum_parent([t1])
        bot = _make_bot(thread=thread)

        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "nonexistent"
        tag_input.emoji = "raw_emoji"

        tags_data = MagicMock()
        tags_data.tags = [tag_input]
        mock_request = MagicMock()

        with (
            self._direct_call_ctx(bot),
            patch("api.routers.threads.normalize_emoji", side_effect=ValueError("bad emoji")),
        ):
            from api.routers.threads import update_thread_tags

            result = await update_thread_tags(mock_request, 1234567890, tags_data)
            assert result.status == "updated"

    async def test_tag_no_name_no_emoji_match_raises_404(self):
        """Tag with no id, no name match, no emoji match → 404 (lines 402-409)."""
        thread = create_mock_thread(1234567890)

        t1 = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="bug", channel_id=555555555)
        t1.emoji = None
        thread.parent = _make_forum_parent([t1])
        bot = _make_bot(thread=thread)

        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "nonexistent"
        tag_input.emoji = None

        tags_data = MagicMock()
        tags_data.tags = [tag_input]
        mock_request = MagicMock()

        with self._direct_call_ctx(bot):
            from api.routers.threads import update_thread_tags

            with pytest.raises(HTTPException) as exc_info:
                await update_thread_tags(mock_request, 1234567890, tags_data)
            assert exc_info.value.status_code == 404
            assert "tag not found" in exc_info.value.detail.lower()

    async def test_tag_emoji_no_match_at_str(self):
        """Emoji matching tries str(at_e) comparison (line 398)."""
        thread = create_mock_thread(1234567890)

        t1 = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="bug", channel_id=555555555)
        emoji_obj = MagicMock()
        emoji_obj.name = None
        emoji_obj.__str__ = MagicMock(return_value="fire_emoji")
        t1.emoji = emoji_obj
        thread.parent = _make_forum_parent([t1])
        bot = _make_bot(thread=thread)

        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "nonexistent"
        tag_input.emoji = "fire_emoji"

        tags_data = MagicMock()
        tags_data.tags = [tag_input]
        mock_request = MagicMock()

        with self._direct_call_ctx(bot), patch("api.routers.threads.normalize_emoji", return_value="fire_emoji"):
            from api.routers.threads import update_thread_tags

            result = await update_thread_tags(mock_request, 1234567890, tags_data)
            assert result.status == "updated"

    async def test_tag_available_tag_emoji_is_none_skipped(self):
        """Available tag with emoji=None is skipped in emoji scan (line 395-396)."""
        thread = create_mock_thread(1234567890)

        t1 = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="no-emoji", channel_id=555555555)
        t1.emoji = None

        t2 = DiscordMockUtils.create_mock_forum_tag(tag_id=222, name="has-emoji", channel_id=555555555)
        t2.emoji = MagicMock()
        t2.emoji.name = "target_emoji"
        thread.parent = _make_forum_parent([t1, t2])
        bot = _make_bot(thread=thread)

        tag_input = MagicMock()
        tag_input.id = None
        tag_input.name = "nonexistent"
        tag_input.emoji = "target_emoji"

        tags_data = MagicMock()
        tags_data.tags = [tag_input]
        mock_request = MagicMock()

        with self._direct_call_ctx(bot), patch("api.routers.threads.normalize_emoji", return_value="target_emoji"):
            from api.routers.threads import update_thread_tags

            result = await update_thread_tags(mock_request, 1234567890, tags_data)
            assert result.status == "updated"
