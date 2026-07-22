"""
Extra targeted tests for uncovered paths in tags.py — second pass.

Targets remaining uncovered lines after test_tags_extended.py:
  80-82   - normalize_emoji exception in get_tag dict payload
  86-93   - non-dict payload path in get_tag (setattr fallback)
  127-128 - normalize_emoji exception in create_forum_tag
  146-162 - channel.edit raises AttributeError → proxy fallback in create
  170-175 - dict payload emoji normalization in create response
  179-186 - non-dict payload in create response
  192-194 - outer exception in create_forum_tag
  235-236 - invalid emoji in update_tag
  250-283 - fallback when tag has no edit / no edit_tag
  289-292 - re-fetch updated tag by name fallback
  297-308 - dict payload emoji in update response
  312-324 - non-dict payload in update response
  330-332 - outer exception in update_tag
  385-417 - fallback edit paths in delete_tag
  421-422 - deleted=False guard
  432-434 - outer exception in delete_tag

Fidelity notes
--------------
No ``sys.modules["discord"]`` swap. ``resolve_bot``/``handle_discord_exception``/
``get_entity_or_404``/``ChannelConverter`` are real and unpatched everywhere
except the handful of tests whose whole *point* is a branch the real
``ChannelConverter.forum_tag_to_payload`` can never take (it always returns a
plain dict — see ``tag_to_dict`` in ``src/utils/discord_helpers.py`` — so the
router's "non-dict payload" setattr fallback is dead code for the real
converter). Those tests patch only ``ChannelConverter.forum_tag_to_payload``
(narrowest possible scope) with a comment explaining why a mock is the only
way to exercise that defensive branch. Everything else uses the same
real-spec'd ``discord.ForumChannel``/``discord.ForumTag`` mocks as
``test_tags.py``.
"""

import os
import sys
import types
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tests.mocks.discord_mock_utils as discord_mock_utils

DiscordMockUtils = discord_mock_utils.DiscordMockUtils
create_discord_not_found = discord_mock_utils.create_discord_not_found

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    for m in ["info", "debug", "warning", "error", "trace", "critical"]:
        setattr(logger, m, MagicMock())
    return logger


_mock_bblogger.get_logger = _make_mock_logger
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_tag(tag_id=1234567890, name="Test Tag", emoji=None):
    tag = MagicMock(spec=discord.ForumTag)
    tag.id = tag_id
    tag.name = name
    tag.emoji = emoji
    tag.moderated = False
    return tag


def _make_forum_channel(channel_id=555555555, tags=None):
    ch = MagicMock(spec=discord.ForumChannel)
    ch.id = channel_id
    ch.guild = MagicMock()
    ch.guild.id = 987654321
    ch.available_tags = tags if tags is not None else [_make_tag()]

    async def _create_tag(name, emoji=None, **_kwargs):
        new_id = max((t.id for t in ch.available_tags), default=0) + 1
        new_tag = _make_tag(tag_id=new_id, name=name, emoji=emoji)
        ch.available_tags.append(new_tag)
        return new_tag

    async def _edit(**kwargs):
        if "available_tags" not in kwargs:
            return
        new_list = []
        next_id = max((t.id for t in ch.available_tags if isinstance(t.id, int)), default=0) + 1
        for item in kwargs["available_tags"]:
            if hasattr(item, "moderated"):
                new_list.append(item)
                continue
            data = item if isinstance(item, dict) else item.to_dict()
            tid = data.get("id")
            existing = discord.utils.get(ch.available_tags, id=tid) if tid is not None else None
            if existing is not None:
                existing.name = data.get("name")
                existing.emoji = data.get("emoji")
                new_list.append(existing)
            else:
                if tid is None:
                    tid, next_id = next_id, next_id + 1
                new_list.append(_make_tag(tag_id=tid, name=data.get("name"), emoji=data.get("emoji")))
        ch.available_tags = new_list

    ch.edit = AsyncMock(side_effect=_edit)
    ch.create_tag = AsyncMock(side_effect=_create_tag)
    return ch


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


@contextmanager
def _force_payload(payload_or_exc):
    """Patch only ``ChannelConverter.forum_tag_to_payload`` (narrowest scope).

    Used exclusively to reach branches the real converter can never take
    (a non-dict return) or to inject an unexpected error for the outer
    exception-handler tests, while leaving resolve_bot/get_entity_or_404/
    handle_discord_exception/everything else real.
    """
    # Resolve the converter dynamically: the api-package conftest purges cached
    # utils/api modules per test, so the class the router imported THIS test is
    # the one that must be patched (a module-level import would go stale).
    from utils.discord_converters import ChannelConverter

    with patch.object(ChannelConverter, "forum_tag_to_payload") as mock_fn:
        if isinstance(payload_or_exc, Exception):
            mock_fn.side_effect = payload_or_exc
        else:
            mock_fn.return_value = payload_or_exc
        yield mock_fn


# ---------------------------------------------------------------------------
# Tests targeting get_tag dict payload with emoji normalization failure (80-82)
# ---------------------------------------------------------------------------


class TestGetTagEmojiNormFailure:
    def test_get_tag_dict_payload_emoji_normalize_raises(self):
        """Lines 80-82: normalize_emoji raises in get_tag's (real) dict payload → silently ignored."""
        tag = _make_tag(1234567890, emoji="bad_emoji")
        ch = _make_forum_channel(tags=[tag])
        guild = MagicMock()
        guild.channels = [ch]
        bot = _make_bot(guilds=[guild])

        with patch("api.routers.tags.normalize_emoji", side_effect=ValueError("bad emoji")):
            client = TestClient(_make_app(bot))
            response = client.get("/api/v1/tags/1234567890")
            # normalize_emoji failure is silently ignored; should still succeed
            assert response.status_code == 200
            assert response.json()["status"] == "success"


# ---------------------------------------------------------------------------
# Tests targeting get_tag non-dict (object) payload path (lines 83-93)
# ---------------------------------------------------------------------------


class TestGetTagObjectPayload:
    def test_get_tag_object_payload_setattr_succeeds(self):
        """Lines 84-93: forum_tag_to_payload returns a non-dict object, setattr path.

        The real converter always returns a dict (see module docstring), so this
        branch is only reachable by forcing ``ChannelConverter.forum_tag_to_payload``
        to return a pydantic object instead — the only way to exercise this
        defensive fallback code.
        """
        tag = _make_tag(1234567890)
        ch = _make_forum_channel(tags=[tag])
        guild = MagicMock()
        guild.channels = [ch]
        bot = _make_bot(guilds=[guild])

        from api.schemas.channel_schemas import ForumTag

        obj_payload = ForumTag(id=1234567890, channel_id=555555555, name="Test Tag", emoji=None)

        with _force_payload(obj_payload):
            client = TestClient(_make_app(bot))
            response = client.get("/api/v1/tags/1234567890")
            assert response.status_code == 200
            assert response.json()["status"] == "success"


# ---------------------------------------------------------------------------
# Tests targeting create_forum_tag payload/error paths
# ---------------------------------------------------------------------------


class TestCreateForumTagPayloadPaths:
    def _bot_with_forum(self, ch):
        return _make_bot(
            get_channel=MagicMock(side_effect=lambda x: ch if x == ch.id else None),
            fetch_channel=AsyncMock(side_effect=lambda x: ch if x == ch.id else create_discord_not_found()),
        )

    def test_create_forum_tag_dict_response_with_emoji(self):
        """Lines 170-175: create returns the real dict payload with emoji → normalize_emoji runs for real."""
        ch = _make_forum_channel()
        bot = self._bot_with_forum(ch)

        client = TestClient(_make_app(bot))
        response = client.post("/api/v1/channels/555555555/tags", json={"name": "New Tag", "emoji": "🎯"})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["emoji"] == "🎯"

    def test_create_forum_tag_object_payload_response(self):
        """Lines 176-186: create returns a non-dict object payload → setattr path.

        Only reachable by forcing the converter (see TestGetTagObjectPayload note).
        """
        ch = _make_forum_channel()
        bot = self._bot_with_forum(ch)

        from api.schemas.channel_schemas import ForumTag

        obj_payload = ForumTag(id=1234567890, channel_id=555555555, name="Test Tag", emoji=None)

        with _force_payload(obj_payload):
            client = TestClient(_make_app(bot))
            response = client.post("/api/v1/channels/555555555/tags", json={"name": "New Tag"})
            assert response.status_code == 201
            assert response.json()["status"] == "created"

    def test_create_forum_tag_outer_exception_handler(self):
        """Lines 192-194: an unexpected error maps to a real 500 via handle_discord_exception."""
        ch = _make_forum_channel()
        bot = self._bot_with_forum(ch)

        with _force_payload(RuntimeError("Unexpected error")):
            client = TestClient(_make_app(bot))
            response = client.post("/api/v1/channels/555555555/tags", json={"name": "New Tag"})
            assert response.status_code == 500
            assert "unexpected error" in response.json()["detail"].lower()

    def test_create_forum_tag_attributeerror_proxy_fallback(self):
        """Lines 146-162: create_tag AND the dict-payload edit both raise AttributeError →
        real proxy-object fallback (``_TagProxy.to_dict()``) is exercised."""
        ch = _make_forum_channel()
        ch.create_tag = AsyncMock(side_effect=AttributeError("no create_tag"))
        real_edit = ch.edit

        calls = {"n": 0}

        async def _edit_first_fails(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise AttributeError("no edit(available_tags=list[dict])")
            return await real_edit.side_effect(**kwargs)

        ch.edit = AsyncMock(side_effect=_edit_first_fails)
        bot = self._bot_with_forum(ch)

        client = TestClient(_make_app(bot))
        response = client.post("/api/v1/channels/555555555/tags", json={"name": "Fallback Tag"})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["data"]["name"] == "Fallback Tag"
        assert ch.edit.await_count == 2


# ---------------------------------------------------------------------------
# Tests targeting update_tag paths
# ---------------------------------------------------------------------------


class TestUpdateTagPaths:
    def _bot_with_tag(self, tag):
        ch = _make_forum_channel(tags=[tag])
        guild = MagicMock()
        guild.channels = [ch]
        return _make_bot(guilds=[guild]), ch

    def test_update_tag_dict_payload_with_emoji_in_response(self):
        """Lines 297-308: update returns the real dict payload with emoji."""
        tag = _make_tag(1234567890)
        bot, _ch = self._bot_with_tag(tag)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "Updated Tag", "emoji": "🚀"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["emoji"] == "🚀"

    def test_update_tag_object_payload_with_emoji_in_response(self):
        """Lines 312-324: update returns a non-dict object payload → setattr path.

        Only reachable by forcing the converter (see TestGetTagObjectPayload note).
        """
        tag = _make_tag(1234567890)
        bot, _ch = self._bot_with_tag(tag)

        from api.schemas.channel_schemas import ForumTag

        obj_payload = ForumTag(id=1234567890, channel_id=555555555, name="Test Tag", emoji=None)

        with _force_payload(obj_payload):
            client = TestClient(_make_app(bot))
            response = client.put("/api/v1/tags/1234567890", json={"name": "Updated"})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"

    def test_update_tag_outer_exception_handler(self):
        """Lines 330-332: an unexpected error maps to a real 500 via handle_discord_exception."""
        tag = _make_tag(1234567890)
        bot, _ch = self._bot_with_tag(tag)

        with _force_payload(RuntimeError("Unexpected!")):
            client = TestClient(_make_app(bot))
            response = client.put("/api/v1/tags/1234567890", json={"name": "New"})
            assert response.status_code == 500
            assert "detail" in response.json()

    def test_update_tag_refetch_by_name_when_id_gone(self):
        """Lines 289-292: id-based re-fetch miss → real name-based fallback.

        Simulates a discord.py variant whose ``edit()`` reassigns the tag's id;
        the router's post-edit lookup by id then misses and must fall back to
        looking the tag up by the requested name — both real ``discord.utils.get``
        calls.
        """
        tag = _make_tag(1234567890, name="Old Name")
        ch = _make_forum_channel(tags=[tag])
        guild = MagicMock()
        guild.channels = [ch]
        bot = _make_bot(guilds=[guild])

        async def _edit_reassigns_id(**kwargs):
            new_tag = _make_tag(tag_id=999999999, name="New Name")
            ch.available_tags = [new_tag]

        ch.edit = AsyncMock(side_effect=_edit_reassigns_id)

        client = TestClient(_make_app(bot))
        response = client.put("/api/v1/tags/1234567890", json={"name": "New Name"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["data"]["name"] == "New Name"
        assert data["data"]["id"] == 999999999


# ---------------------------------------------------------------------------
# Tests targeting delete_tag fallback paths
# ---------------------------------------------------------------------------


class TestDeleteTagPaths:
    def _bot_with_tag(self, tag, ch=None):
        ch = ch or _make_forum_channel(tags=[tag])
        guild = MagicMock()
        guild.channels = [ch]
        return _make_bot(guilds=[guild]), ch

    def test_delete_tag_fallback_via_edit_with_dict_payloads(self):
        """Lines 385-414: delete uses the real edit(available_tags=[...]) fallback.

        The installed discord.py's ForumTag/ForumChannel don't expose
        ``delete``/``delete_tag`` (see test_tags.py's fidelity note), so this
        is the default, faithful behavior — no attribute removal needed.
        """
        tag = _make_tag(1234567890)
        bot, ch = self._bot_with_tag(tag)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        assert not any(t.id == 1234567890 for t in ch.available_tags)

    def test_delete_tag_edit_raises_type_error_then_succeeds(self):
        """Lines 386-414: edit(available_tags=<object list>) raises TypeError →
        real dict-payload fallback succeeds."""
        tag = _make_tag(1234567890)
        ch = _make_forum_channel(tags=[tag])
        real_edit = ch.edit
        calls = {"n": 0}

        async def _edit_first_type_errors(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TypeError("wrong type")
            return await real_edit.side_effect(**kwargs)

        ch.edit = AsyncMock(side_effect=_edit_first_type_errors)
        bot, _ch = self._bot_with_tag(tag, ch=ch)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 200
        assert response.json()["deleted"] is True
        assert ch.edit.await_count == 2

    def test_delete_tag_outer_exception_handler(self):
        """Lines 432-434: an unexpected error (real tag.delete() attached, raising
        RuntimeError) maps to a real 500 via handle_discord_exception."""
        tag = _make_tag(1234567890)
        tag.delete = AsyncMock(side_effect=RuntimeError("Unexpected!"))
        bot, _ch = self._bot_with_tag(tag)

        client = TestClient(_make_app(bot))
        response = client.delete("/api/v1/tags/1234567890")
        assert response.status_code == 500
        assert "detail" in response.json()
