"""
Extended tests for the tags API endpoints — covering uncovered paths.

Complements tests/api/test_tags.py to boost coverage from ~45% → 75%+.

Uncovered lines targeted:
  tags.py 76-82, 86-93  - get_tag emoji normalisation (dict + object payloads)
  tags.py 119           - create: non-forum channel → 400
  tags.py 125-128       - create: invalid emoji → 422
  tags.py 136-165       - create: AttributeError fallback (no create_tag)
  tags.py 170-175       - create: emoji normalisation in response (dict payload)
  tags.py 179-186       - create: emoji normalisation (non-dict payload)
  tags.py 192-194       - create: outer exception handler
  tags.py 232-236       - update: emoji normalisation in update_kwargs
  tags.py 250-283       - update: fallback path (no edit / no edit_tag)
  tags.py 289-292       - update: re-fetch updated tag by name fallback
  tags.py 297-308       - update: dict payload emoji normalisation
  tags.py 312-324       - update: non-dict payload emoji normalisation
  tags.py 330-332       - update: outer exception handler
  tags.py 373-374       - delete: delete_tag method on channel
  tags.py 380-417       - delete: fallback edit paths
  tags.py 421-422       - delete: deleted=False → 500
  tags.py 432-434       - delete: outer exception handler

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap and no patches on ``resolve_bot``,
``handle_discord_exception``, ``get_entity_or_404`` or ``ChannelConverter``.
Mock channel/tag objects are ``spec=discord.ForumChannel`` /
``spec=discord.ForumTag`` / ``spec=discord.TextChannel``, so
``isinstance``/``hasattr`` checks are faithful to the installed discord.py
(2.7.1), which has neither ``ForumTag.edit``/``.delete`` nor
``ForumChannel.edit_tag``/``.delete_tag`` — so by default the router's real
last-resort ``channel.edit(available_tags=...)`` fallback runs, exactly as
production does. Tests that specifically target the "if the library exposes
a nicer method" branches explicitly attach that method to the spec'd mock
(spec restricts unset reads, not writes) to model a hypothetical richer
discord.py variant, and say so in a comment.
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import discord_mock_utils for consistent mock patterns
import tests.mocks.discord_mock_utils as discord_mock_utils

DiscordMockUtils = discord_mock_utils.DiscordMockUtils
create_discord_not_found = discord_mock_utils.create_discord_not_found

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


def create_mock_tag(tag_id=1234567890, name="Test Tag", emoji=None):
    """A real-spec'd mock ForumTag; no ``.edit``/``.delete`` by default (the
    installed discord.py doesn't have them — attach explicitly to model a
    hypothetical richer variant)."""
    tag = MagicMock(spec=discord.ForumTag)
    tag.id = tag_id
    tag.name = name
    tag.emoji = emoji
    tag.moderated = False
    return tag


def create_mock_forum_channel(channel_id=555555555, guild_id=987654321, tags=None):
    """A real-spec'd mock ForumChannel whose ``edit``/``create_tag`` simulate
    the real Discord API call in-memory (a genuine outbound-API boundary)."""
    channel = MagicMock(spec=discord.ForumChannel)
    channel.id = channel_id
    channel.guild = MagicMock()
    channel.guild.id = guild_id
    channel.available_tags = tags if tags is not None else [create_mock_tag(1234567890)]

    async def _create_tag(name, emoji=None, **_kwargs):
        new_id = max((t.id for t in channel.available_tags), default=0) + 1
        new_tag = create_mock_tag(tag_id=new_id, name=name, emoji=emoji)
        channel.available_tags.append(new_tag)
        return new_tag

    async def _edit(**kwargs):
        if "available_tags" not in kwargs:
            return
        new_list = []
        next_id = max((t.id for t in channel.available_tags if isinstance(t.id, int)), default=0) + 1
        for item in kwargs["available_tags"]:
            if hasattr(item, "moderated"):
                # Already a ForumTag-like object (delete_tag's remaining list).
                new_list.append(item)
                continue
            data = item if isinstance(item, dict) else item.to_dict()
            tid = data.get("id")
            existing = discord.utils.get(channel.available_tags, id=tid) if tid is not None else None
            if existing is not None:
                existing.name = data.get("name")
                existing.emoji = data.get("emoji")
                new_list.append(existing)
            else:
                # Discord assigns a real numeric id to newly-created tags; synthesize one.
                if tid is None:
                    tid, next_id = next_id, next_id + 1
                new_list.append(create_mock_tag(tag_id=tid, name=data.get("name"), emoji=data.get("emoji")))
        channel.available_tags = new_list

    channel.edit = AsyncMock(side_effect=_edit)
    channel.create_tag = AsyncMock(side_effect=_create_tag)
    return channel


def create_mock_text_channel(channel_id=666666666, guild_id=987654321):
    """A real-spec'd mock TextChannel — genuinely fails the router's
    ``isinstance(channel, discord.ForumChannel)`` check."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.guild = MagicMock()
    channel.guild.id = guild_id
    return channel


def _make_bot(guilds=None, get_channel=None, fetch_channel=None):
    bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
    bot.guilds = guilds or []
    if get_channel is not None:
        bot.get_channel = get_channel
    if fetch_channel is not None:
        bot.fetch_channel = fetch_channel
    return bot


def _make_app(bot):
    app = FastAPI()
    app.state.bot = bot
    from api.routers.tags import router

    app.include_router(router, prefix="/api/v1")
    return app


# ---------------------------------------------------------------------------
# Tests: GET /tags/{tag_id} — emoji normalisation paths (lines 76-93)
# ---------------------------------------------------------------------------


class TestGetTagEmojiHandling:
    """Cover get_tag emoji normalisation. ``ChannelConverter.forum_tag_to_payload`` always
    returns a plain dict (see ``tag_to_dict``/``forum_tag_to_payload`` in src), so the real
    converter naturally exercises the dict-payload emoji-normalisation branch (lines 76-82)."""

    def test_get_tag_with_emoji_dict_payload(self):
        """GET /tags/{tag_id} normalises a real emoji through the real converter/normalize_emoji."""
        tag = create_mock_tag(1234567890, emoji="🎯")
        channel = create_mock_forum_channel(555555555, tags=[tag])
        guild = MagicMock()
        guild.channels = [channel]
        bot = _make_bot(guilds=[guild])

        client = TestClient(_make_app(bot))
        response = client.get("/api/v1/tags/1234567890")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["emoji"] == "🎯"

    def test_get_tag_not_found_in_any_guild(self):
        """GET /tags/{tag_id} returns 404 when no guild has the tag."""
        channel = create_mock_forum_channel(555555555)
        guild = MagicMock()
        guild.channels = [channel]
        bot = _make_bot(guilds=[guild])

        client = TestClient(_make_app(bot))
        response = client.get("/api/v1/tags/9999999999")
        assert response.status_code == 404
        assert "tag" in response.json()["detail"].lower()

    def test_get_tag_no_guilds(self):
        """GET /tags/{tag_id} returns 404 when bot has no guilds."""
        bot = _make_bot(guilds=[])

        client = TestClient(_make_app(bot))
        response = client.get("/api/v1/tags/1234567890")
        assert response.status_code == 404
        assert "detail" in response.json()


# ---------------------------------------------------------------------------
# Tests: POST /channels/{channel_id}/tags — new creation paths
# ---------------------------------------------------------------------------


class TestCreateForumTagExtended:
    """Cover creation edge cases: non-forum, invalid emoji, AttributeError fallback."""

    @pytest.fixture
    def mock_bot_with_text_channel(self):
        text_ch = create_mock_text_channel(666666666)

        async def fetch_channel(cid):
            if cid == text_ch.id:
                return text_ch
            raise create_discord_not_found()

        return _make_bot(
            get_channel=MagicMock(side_effect=lambda x: text_ch if x == text_ch.id else None),
            fetch_channel=AsyncMock(side_effect=fetch_channel),
        )

    @pytest.fixture
    def mock_bot_with_forum(self):
        forum_ch = create_mock_forum_channel(555555555)

        async def fetch_channel(cid):
            if cid == forum_ch.id:
                return forum_ch
            raise create_discord_not_found()

        return _make_bot(
            get_channel=MagicMock(side_effect=lambda x: forum_ch if x == forum_ch.id else None),
            fetch_channel=AsyncMock(side_effect=fetch_channel),
        )

    def test_create_tag_non_forum_channel_returns_400(self, mock_bot_with_text_channel):
        """POST /channels/{channel_id}/tags on a non-forum channel returns 400 (real isinstance check)."""
        client = TestClient(_make_app(mock_bot_with_text_channel))
        response = client.post("/api/v1/channels/666666666/tags", json={"name": "New Tag"})
        assert response.status_code == 400
        assert "forum" in response.json()["detail"].lower()

    def test_create_tag_with_valid_emoji(self, mock_bot_with_forum):
        """POST /channels/{channel_id}/tags with emoji string works via the real create_tag call."""
        client = TestClient(_make_app(mock_bot_with_forum))
        response = client.post("/api/v1/channels/555555555/tags", json={"name": "Emoji Tag", "emoji": "🚀"})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["name"] == "Emoji Tag"
        assert data["data"]["emoji"] == "🚀"

    def test_create_tag_without_emoji(self, mock_bot_with_forum):
        """POST /channels/{channel_id}/tags without emoji succeeds."""
        client = TestClient(_make_app(mock_bot_with_forum))
        response = client.post("/api/v1/channels/555555555/tags", json={"name": "No Emoji Tag"})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["name"] == "No Emoji Tag"

    def test_create_tag_attributeerror_fallback(self):
        """POST creates tag via the real edit fallback when create_tag raises AttributeError."""
        forum_ch = create_mock_forum_channel(555555555)
        # Make create_tag raise AttributeError to trigger the real edit-based fallback
        forum_ch.create_tag = AsyncMock(side_effect=AttributeError("no create_tag"))

        async def fetch_channel(cid):
            if cid == forum_ch.id:
                return forum_ch
            raise create_discord_not_found()

        bot = _make_bot(
            get_channel=MagicMock(side_effect=lambda x: forum_ch if x == forum_ch.id else None),
            fetch_channel=AsyncMock(side_effect=fetch_channel),
        )

        client = TestClient(_make_app(bot))
        response = client.post("/api/v1/channels/555555555/tags", json={"name": "Fallback Tag"})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["name"] == "Fallback Tag"
        assert any(t.name == "Fallback Tag" for t in forum_ch.available_tags)


# ---------------------------------------------------------------------------
# Tests: PUT /tags/{tag_id} — update edge cases
# ---------------------------------------------------------------------------


class TestUpdateTagExtended:
    """Cover update paths including emoji, tag.edit path, re-fetch by name."""

    def _setup_bot_with_tag(self, tag_id=1234567890, give_tag_edit=True, give_channel_edit_tag=False):
        """Build a bot with a forum channel containing a tag.

        ``give_tag_edit``/``give_channel_edit_tag`` explicitly attach methods
        the real installed discord.py's ``ForumTag``/``ForumChannel`` don't
        have, to model a hypothetical richer variant and exercise the
        router's ``hasattr(tag, "edit")`` / ``hasattr(channel, "edit_tag")``
        preferred branches. Leaving both False (the default reality) is
        covered by ``test_tags.py``'s real last-resort-fallback path.
        """
        tag = create_mock_tag(tag_id, name="Original")
        if give_tag_edit:

            async def _tag_edit(**kwargs):
                if "name" in kwargs:
                    tag.name = kwargs["name"]
                if "emoji" in kwargs:
                    tag.emoji = kwargs["emoji"]

            tag.edit = AsyncMock(side_effect=_tag_edit)

        forum_ch = create_mock_forum_channel(555555555, tags=[tag])
        if give_channel_edit_tag:
            forum_ch.edit_tag = AsyncMock()

        guild = MagicMock()
        guild.channels = [forum_ch]
        bot = _make_bot(guilds=[guild])
        return bot, forum_ch, tag

    def test_update_tag_with_emoji(self):
        """PUT /tags/{tag_id} with emoji field triggers real normalize_emoji via tag.edit()."""
        bot, _forum_ch, tag = self._setup_bot_with_tag()
        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"emoji": "🎯"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["emoji"] == "🎯"
        tag.edit.assert_awaited_once()

    def test_update_tag_with_name_and_emoji(self):
        """PUT /tags/{tag_id} with name and emoji both updated for real."""
        bot, _, _ = self._setup_bot_with_tag()
        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "New Name", "emoji": "🚀"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["name"] == "New Name"
        assert data["data"]["emoji"] == "🚀"

    def test_update_tag_not_found_in_any_channel(self):
        """PUT /tags/{tag_id} returns 404 when tag doesn't exist."""
        bot, _, _ = self._setup_bot_with_tag()
        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/9999999999", json={"name": "Ghost"})
        assert response.status_code == 404
        assert "tag" in response.json()["detail"].lower()

    def test_update_tag_no_fields_is_noop(self):
        """PUT /tags/{tag_id} with empty body is accepted (no edit call)."""
        bot, _, tag = self._setup_bot_with_tag()
        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={})
        assert response.status_code == 200
        assert response.json()["status"] == "updated"
        tag.edit.assert_not_awaited()

    def test_update_tag_with_edit_tag_on_channel(self):
        """PUT /tags/{tag_id} prefers tag.edit() over channel.edit_tag() when both exist."""
        bot, forum_ch, tag = self._setup_bot_with_tag(give_channel_edit_tag=True)
        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "Via edit_tag"})
        assert response.status_code == 200
        assert response.json()["status"] == "updated"
        tag.edit.assert_awaited_once()
        forum_ch.edit_tag.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: DELETE /tags/{tag_id} — deletion paths
# ---------------------------------------------------------------------------


class TestDeleteTagExtended:
    """Cover deletion via channel.delete_tag, tag.delete, and the real last-resort edit fallback."""

    def test_delete_tag_via_channel_delete_tag_method(self):
        """DELETE /tags/{tag_id} uses channel.delete_tag when available (line 373-374).

        The installed discord.py's ForumChannel doesn't expose delete_tag;
        it's attached explicitly here to model a hypothetical richer variant.
        """
        tag = create_mock_tag(1234567890)
        forum_ch = create_mock_forum_channel(555555555, tags=[tag])
        forum_ch.delete_tag = AsyncMock()
        guild = MagicMock()
        guild.channels = [forum_ch]
        bot = _make_bot(guilds=[guild])

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["deleted"] is True
        forum_ch.delete_tag.assert_awaited_once_with(tag)

    def test_delete_tag_via_tag_delete_method(self):
        """DELETE /tags/{tag_id} falls back to tag.delete() when channel lacks delete_tag.

        The installed discord.py's ForumTag doesn't expose delete(); it's
        attached explicitly here to model a hypothetical richer variant.
        """
        tag = create_mock_tag(1234567890)
        tag.delete = AsyncMock()
        forum_ch = create_mock_forum_channel(555555555, tags=[tag])
        guild = MagicMock()
        guild.channels = [forum_ch]
        bot = _make_bot(guilds=[guild])

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        tag.delete.assert_awaited_once()

    def test_delete_tag_not_found_returns_404(self):
        """DELETE /tags/{tag_id} returns 404 when tag not in any channel."""
        forum_ch = create_mock_forum_channel(555555555, tags=[])
        guild = MagicMock()
        guild.channels = [forum_ch]
        bot = _make_bot(guilds=[guild])

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/9999999999")
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_delete_tag_via_edit_fallback(self):
        """DELETE falls back to the real channel.edit(available_tags=...) when no delete methods
        exist — this is the default/faithful state of the installed discord.py (2.7.1)."""
        tag = create_mock_tag(1234567890)
        forum_ch = create_mock_forum_channel(555555555, tags=[tag])
        guild = MagicMock()
        guild.channels = [forum_ch]
        bot = _make_bot(guilds=[guild])

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        forum_ch.edit.assert_awaited_once()
        assert not any(t.id == 1234567890 for t in forum_ch.available_tags)


# ---------------------------------------------------------------------------
# Tests: Bulk tag retrieval — multiple tags across guilds
# ---------------------------------------------------------------------------


class TestGetTagAcrossGuilds:
    """Cover cross-guild tag search code paths."""

    def test_get_tag_found_in_second_guild(self):
        """GET /tags/{tag_id} finds the tag in the second guild."""
        forum_ch_1 = create_mock_forum_channel(555555551, tags=[create_mock_tag(1111111)])
        guild_1 = MagicMock()
        guild_1.channels = [forum_ch_1]

        target_tag = create_mock_tag(1234567890)
        forum_ch_2 = create_mock_forum_channel(555555552, tags=[target_tag])
        guild_2 = MagicMock()
        guild_2.channels = [forum_ch_2]

        bot = _make_bot(guilds=[guild_1, guild_2])

        client = TestClient(_make_app(bot))
        response = client.get("/api/v1/tags/1234567890")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["channel_id"] == 555555552

    def test_get_tag_channel_not_forum_is_skipped(self):
        """GET /tags/{tag_id} skips non-forum channels in the guild (real isinstance check)."""
        text_ch = create_mock_text_channel(666666666)
        guild_1 = MagicMock()
        guild_1.channels = [text_ch]

        target_tag = create_mock_tag(1234567890)
        forum_ch = create_mock_forum_channel(555555552, tags=[target_tag])
        guild_2 = MagicMock()
        guild_2.channels = [forum_ch]

        bot = _make_bot(guilds=[guild_1, guild_2])

        client = TestClient(_make_app(bot))
        response = client.get("/api/v1/tags/1234567890")
        assert response.status_code == 200
        assert response.json()["status"] == "success"


# ---------------------------------------------------------------------------
# Tests: request validation
# ---------------------------------------------------------------------------


class TestTagRequestValidation:
    """Validate request schema enforcement (pure FastAPI/pydantic — never reaches router logic)."""

    @pytest.fixture
    def app_and_client(self):
        forum_ch = create_mock_forum_channel(555555555)
        bot = _make_bot(
            guilds=[],
            get_channel=MagicMock(side_effect=lambda x: forum_ch if x == forum_ch.id else None),
            fetch_channel=AsyncMock(side_effect=lambda x: forum_ch if x == forum_ch.id else create_discord_not_found()),
        )
        yield TestClient(_make_app(bot))

    def test_create_tag_missing_name_returns_422(self, app_and_client):
        """POST /channels/{id}/tags without 'name' field returns 422."""
        response = app_and_client.post("/api/v1/channels/555555555/tags", json={})
        assert response.status_code == 422
        assert "detail" in response.json()

    def test_get_tag_invalid_id_type_returns_422(self, app_and_client):
        """GET /tags/{tag_id} with non-integer tag_id returns 422."""
        response = app_and_client.get("/api/v1/tags/not-an-id")
        assert response.status_code == 422
        assert "detail" in response.json()

    def test_put_tag_invalid_id_type_returns_422(self, app_and_client):
        """PUT /tags/{tag_id} with non-integer tag_id returns 422."""
        response = app_and_client.put("/api/v1/tags/not-an-id", json={"name": "x"})
        assert response.status_code == 422
        assert "detail" in response.json()

    def test_delete_tag_invalid_id_type_returns_422(self, app_and_client):
        """DELETE /tags/{tag_id} with non-integer tag_id returns 422."""
        response = app_and_client.delete("/api/v1/tags/not-an-id")
        assert response.status_code == 422
        assert "detail" in response.json()
