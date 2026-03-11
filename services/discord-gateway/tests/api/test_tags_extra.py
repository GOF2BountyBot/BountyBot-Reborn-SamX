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
"""

import pytest
import importlib
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import sys
import os
import types
from datetime import datetime

from tests.mocks.discord_mock_utils import DiscordMockUtils

_mock_utils = DiscordMockUtils()

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


@pytest.fixture(autouse=True)
def _restore_real_discord():
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    import tests.mocks.discord_mock_utils as _dmu_mod
    importlib.reload(_dmu_mod)
    from api.routers import tags as _tags_mod
    importlib.reload(_tags_mod)
    yield


def _make_tag(tag_id=1234567890, name="Test Tag", emoji=None):
    tag = DiscordMockUtils.create_mock_forum_tag(tag_id=tag_id, name=name, emoji=emoji, channel_id=555555555)
    tag.moderated = False
    tag.edit = AsyncMock()
    tag.delete = AsyncMock()
    return tag


def _make_forum_channel(channel_id=555555555, tags=None):
    ch = MagicMock(spec=_MockForumChannel)
    ch.__class__ = _MockForumChannel
    ch.id = channel_id
    ch.guild = MagicMock()
    ch.guild.id = 987654321
    ch.available_tags = tags if tags is not None else [_make_tag()]
    ch.edit = AsyncMock()
    ch.create_tag = AsyncMock(return_value=_make_tag())
    return ch


def _forum_tag_payload(tag_id=1234567890, emoji=None):
    from api.schemas.channel_schemas import ForumTag
    return ForumTag(id=tag_id, channel_id=555555555, name="Test Tag", emoji=emoji)


def _utils_get_fn(iterable, **kwargs):
    for item in (iterable or []):
        match = all(getattr(item, k, None) == v for k, v in kwargs.items())
        if match:
            return item
    return None


def _build_base_app(mock_bot, get_entity_side_effect, payload_return=None, utils_get=None):
    """Build a minimal app with tags router."""
    app = FastAPI()
    app.state.bot = mock_bot

    if payload_return is None:
        payload_return = _forum_tag_payload()

    _mock_discord.utils.get = utils_get or _utils_get_fn

    with patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
         patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
         patch("api.routers.tags.ChannelConverter") as mock_converter, \
         patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
         patch("api.routers.tags.discord", _mock_discord):

        async def resolve(req):
            return mock_bot

        mock_resolve.side_effect = resolve
        mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")
        mock_converter.forum_tag_to_payload.return_value = payload_return
        mock_get_entity.side_effect = get_entity_side_effect

        from api.routers.tags import router
        app.include_router(router, prefix="/api/v1")

        yield app, mock_handle


# ---------------------------------------------------------------------------
# Tests targeting get_tag dict payload with emoji normalization failure (80-82)
# ---------------------------------------------------------------------------

class TestGetTagEmojiNormFailure:

    def test_get_tag_dict_payload_emoji_normalize_raises(self):
        """Lines 80-82: normalize_emoji raises in get_tag dict payload → silently ignored."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        ch = _make_forum_channel()
        guild = MagicMock()
        guild.channels = [ch]
        bot.guilds = [guild]

        # Return a dict payload with emoji (triggers normalization line 79)
        dict_payload = {
            "id": 1234567890,
            "channel_id": 555555555,
            "name": "Test Tag",
            "emoji": "bad_emoji",
        }

        app = FastAPI()
        app.state.bot = bot
        _mock_discord.utils.get = _utils_get_fn

        with patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.tags.ChannelConverter") as mock_converter, \
             patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock), \
             patch("api.routers.tags.discord", _mock_discord), \
             patch("api.routers.tags.normalize_emoji", side_effect=ValueError("bad emoji")):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            mock_converter.forum_tag_to_payload.return_value = dict_payload

            from api.routers.tags import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.get("/api/v1/tags/1234567890")
            # normalize_emoji failure is silently ignored; should still succeed
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tests targeting get_tag non-dict (object) payload path (lines 83-93)
# ---------------------------------------------------------------------------

class TestGetTagObjectPayload:

    def test_get_tag_object_payload_setattr_succeeds(self):
        """Lines 84-85: forum_tag_to_payload returns object (not dict), setattr works."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        ch = _make_forum_channel()
        guild = MagicMock()
        guild.channels = [ch]
        bot.guilds = [guild]

        # Return an object-like payload (Pydantic model)
        obj_payload = _forum_tag_payload()

        app = FastAPI()
        app.state.bot = bot
        _mock_discord.utils.get = _utils_get_fn

        with patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.tags.ChannelConverter") as mock_converter, \
             patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock), \
             patch("api.routers.tags.discord", _mock_discord):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            mock_converter.forum_tag_to_payload.return_value = obj_payload  # object, not dict

            from api.routers.tags import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.get("/api/v1/tags/1234567890")
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tests targeting create_forum_tag with dict/object payload returns (170-186)
# ---------------------------------------------------------------------------

class TestCreateForumTagPayloadPaths:

    def test_create_forum_tag_dict_response_with_emoji(self):
        """Lines 170-175: create returns dict payload with emoji → normalize_emoji called."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        ch = _make_forum_channel()
        bot.get_channel = MagicMock(return_value=ch)
        bot.fetch_channel = AsyncMock(return_value=ch)
        bot.guilds = []

        dict_payload = {
            "id": 1234567890,
            "channel_id": 555555555,
            "name": "New Tag",
            "emoji": "🎯",
        }

        app = FastAPI()
        app.state.bot = bot
        _mock_discord.utils.get = _utils_get_fn

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            ch_res = get_fn(entity_id)
            if ch_res is None:
                raise HTTPException(status_code=404, detail="not found")
            return ch_res

        with patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.tags.ChannelConverter") as mock_converter, \
             patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.tags.discord", _mock_discord), \
             patch("api.routers.tags.normalize_emoji", return_value="🎯") as mock_norm:

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            mock_converter.forum_tag_to_payload.return_value = dict_payload
            mock_get_entity.side_effect = _get_entity

            from api.routers.tags import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.post(
                "/api/v1/channels/555555555/tags",
                json={"name": "New Tag", "emoji": "🎯"}
            )
            assert response.status_code == 201

    def test_create_forum_tag_object_payload_response(self):
        """Lines 176-186: create returns object payload → setattr path."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        ch = _make_forum_channel()
        bot.get_channel = MagicMock(return_value=ch)
        bot.fetch_channel = AsyncMock(return_value=ch)
        bot.guilds = []

        obj_payload = _forum_tag_payload()  # Pydantic object

        app = FastAPI()
        app.state.bot = bot
        _mock_discord.utils.get = _utils_get_fn

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            ch_res = get_fn(entity_id)
            if ch_res is None:
                raise HTTPException(status_code=404, detail="not found")
            return ch_res

        with patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.tags.ChannelConverter") as mock_converter, \
             patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.tags.discord", _mock_discord):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            mock_converter.forum_tag_to_payload.return_value = obj_payload
            mock_get_entity.side_effect = _get_entity

            from api.routers.tags import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.post("/api/v1/channels/555555555/tags", json={"name": "New Tag"})
            assert response.status_code == 201

    def test_create_forum_tag_outer_exception_handler(self):
        """Lines 192-194: outer exception handler in create_forum_tag."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        ch = _make_forum_channel()
        bot.get_channel = MagicMock(return_value=ch)
        bot.fetch_channel = AsyncMock(return_value=ch)
        bot.guilds = []

        app = FastAPI()
        app.state.bot = bot
        _mock_discord.utils.get = _utils_get_fn

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            ch_res = get_fn(entity_id)
            if ch_res is None:
                raise HTTPException(status_code=404, detail="not found")
            return ch_res

        with patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.tags.ChannelConverter") as mock_converter, \
             patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.tags.discord", _mock_discord):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            # Make ChannelConverter.forum_tag_to_payload raise an unexpected exception
            mock_converter.forum_tag_to_payload.side_effect = RuntimeError("Unexpected error")
            mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")
            mock_get_entity.side_effect = _get_entity

            from api.routers.tags import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.post("/api/v1/channels/555555555/tags", json={"name": "New Tag"})
            assert response.status_code == 500

    def test_create_forum_tag_attributeerror_proxy_fallback(self):
        """Lines 146-162: channel.edit raises AttributeError → proxy fallback in create."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        ch = _make_forum_channel()
        # create_tag raises AttributeError → fallback to edit
        ch.create_tag = AsyncMock(side_effect=AttributeError("no create_tag"))
        # edit also raises AttributeError → proxy fallback
        ch.edit = AsyncMock(side_effect=[AttributeError("no edit"), None])
        ch.available_tags = [_make_tag(name="Fallback Tag")]

        bot.get_channel = MagicMock(return_value=ch)
        bot.fetch_channel = AsyncMock(return_value=ch)
        bot.guilds = []

        app = FastAPI()
        app.state.bot = bot

        def _ug(iterable, **kwargs):
            for item in (iterable or []):
                if all(getattr(item, k, None) == v for k, v in kwargs.items()):
                    return item
            return None
        _mock_discord.utils.get = _ug

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            r = get_fn(entity_id)
            if r is None:
                raise HTTPException(status_code=404, detail="not found")
            return r

        with patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock), \
             patch("api.routers.tags.ChannelConverter") as mock_converter, \
             patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity, \
             patch("api.routers.tags.discord", _mock_discord), \
             patch("api.routers.tags.tags_to_edit_payload", return_value=[]):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            mock_converter.forum_tag_to_payload.return_value = _forum_tag_payload()
            mock_get_entity.side_effect = _get_entity

            from api.routers.tags import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.post(
                "/api/v1/channels/555555555/tags",
                json={"name": "Fallback Tag"}
            )
            # Should succeed (proxy fallback was used)
            assert response.status_code == 201


# ---------------------------------------------------------------------------
# Tests targeting update_tag paths (235-236, 250-283, 289-292, 297-308, 312-324, 330-332)
# ---------------------------------------------------------------------------

class TestUpdateTagPaths:

    def _make_update_app(self, mock_bot, payload_return=None):
        app = FastAPI()
        app.state.bot = mock_bot

        _mock_discord.utils.get = _utils_get_fn

        with patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.tags.ChannelConverter") as mock_converter, \
             patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock), \
             patch("api.routers.tags.discord", _mock_discord):

            async def resolve(req):
                return mock_bot

            mock_resolve.side_effect = resolve
            mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")
            mock_converter.forum_tag_to_payload.return_value = payload_return or _forum_tag_payload()

            from api.routers.tags import router
            app.include_router(router, prefix="/api/v1")

            yield app

    def test_update_tag_dict_payload_with_emoji_in_response(self):
        """Lines 297-308: update returns dict payload with emoji."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        tag = _make_tag(1234567890)
        ch = _make_forum_channel(tags=[tag])
        guild = MagicMock()
        guild.channels = [ch]
        bot.guilds = [guild]

        dict_payload = {
            "id": 1234567890,
            "channel_id": 555555555,
            "name": "Updated Tag",
            "emoji": "🚀",
        }

        for app in self._make_update_app(bot, dict_payload):
            client = TestClient(app)
            response = client.put("/api/v1/tags/1234567890", json={"name": "Updated Tag", "emoji": "🚀"})
            assert response.status_code == 200

    def test_update_tag_object_payload_with_emoji_in_response(self):
        """Lines 312-324: update returns object payload."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        tag = _make_tag(1234567890)
        ch = _make_forum_channel(tags=[tag])
        guild = MagicMock()
        guild.channels = [ch]
        bot.guilds = [guild]

        obj_payload = _forum_tag_payload()

        for app in self._make_update_app(bot, obj_payload):
            client = TestClient(app)
            response = client.put("/api/v1/tags/1234567890", json={"name": "Updated"})
            assert response.status_code == 200

    def test_update_tag_outer_exception_handler(self):
        """Lines 330-332: outer exception handler in update_tag."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        tag = _make_tag(1234567890)
        ch = _make_forum_channel(tags=[tag])
        guild = MagicMock()
        guild.channels = [ch]
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot
        _mock_discord.utils.get = _utils_get_fn

        with patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.tags.ChannelConverter") as mock_converter, \
             patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock), \
             patch("api.routers.tags.discord", _mock_discord):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            # Make ChannelConverter raise an unexpected error
            mock_converter.forum_tag_to_payload.side_effect = RuntimeError("Unexpected!")
            mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")

            from api.routers.tags import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.put("/api/v1/tags/1234567890", json={"name": "New"})
            assert response.status_code == 500

    def test_update_tag_refetch_by_name_when_id_gone(self):
        """Lines 289-292: re-fetch updated tag by name when id lookup fails after edit."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        tag = _make_tag(1234567890, name="Old Name")
        ch = _make_forum_channel(tags=[tag])
        guild = MagicMock()
        guild.channels = [ch]
        bot.guilds = [guild]

        call_count = [0]

        def _custom_utils_get(iterable, **kwargs):
            call_count[0] += 1
            # First call (initial search): find tag by id
            # Second call (re-fetch after edit by id): return None to trigger name fallback
            # Third call (re-fetch by name): return the tag
            if kwargs.get("id") == 1234567890:
                if call_count[0] <= 2:
                    return tag if call_count[0] == 1 else None  # first: found, second: not found
                return None
            if kwargs.get("name") == "New Name":
                return tag
            return None

        _mock_discord.utils.get = _custom_utils_get

        for app in self._make_update_app(bot):
            client = TestClient(app)
            response = client.put("/api/v1/tags/1234567890", json={"name": "New Name"})
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# Tests targeting delete_tag fallback paths (385-417, 421-422, 432-434)
# ---------------------------------------------------------------------------

class TestDeleteTagPaths:

    def _make_delete_app(self, mock_bot, extra_handle_side_effect=None):
        app = FastAPI()
        app.state.bot = mock_bot

        _mock_discord.utils.get = _utils_get_fn

        with patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.tags.ChannelConverter") as mock_converter, \
             patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock), \
             patch("api.routers.tags.discord", _mock_discord):

            async def resolve(req):
                return mock_bot

            mock_resolve.side_effect = resolve
            if extra_handle_side_effect:
                mock_handle.side_effect = extra_handle_side_effect
            else:
                mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")
            mock_converter.forum_tag_to_payload.return_value = _forum_tag_payload()

            from api.routers.tags import router
            app.include_router(router, prefix="/api/v1")

            yield app

    def test_delete_tag_fallback_via_edit_with_dict_payloads(self):
        """Lines 385-414: delete uses edit(available_tags=payloads) fallback."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        tag = _make_tag(1234567890)
        # Remove delete from tag so edit fallback is used
        del tag.delete
        ch = _make_forum_channel(tags=[tag])
        # Remove delete_tag from channel
        if hasattr(ch, "delete_tag"):
            del ch.delete_tag
        ch.edit = AsyncMock()
        guild = MagicMock()
        guild.channels = [ch]
        bot.guilds = [guild]

        for app in self._make_delete_app(bot):
            client = TestClient(app)
            response = client.delete("/api/v1/tags/1234567890")
            assert response.status_code == 200
            assert response.json()["deleted"] is True

    def test_delete_tag_edit_raises_type_error_then_succeeds(self):
        """Lines 386-414: edit raises TypeError → dict payload fallback."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        tag = _make_tag(1234567890)
        del tag.delete
        ch = _make_forum_channel(tags=[tag])
        if hasattr(ch, "delete_tag"):
            del ch.delete_tag
        # First edit call raises TypeError, second succeeds
        ch.edit = AsyncMock(side_effect=[TypeError("wrong type"), None])
        guild = MagicMock()
        guild.channels = [ch]
        bot.guilds = [guild]

        for app in self._make_delete_app(bot):
            client = TestClient(app)
            response = client.delete("/api/v1/tags/1234567890")
            assert response.status_code == 200

    def test_delete_tag_outer_exception_handler(self):
        """Lines 432-434: outer exception handler in delete_tag."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        tag = _make_tag(1234567890)
        ch = _make_forum_channel(tags=[tag])
        guild = MagicMock()
        guild.channels = [ch]
        bot.guilds = [guild]

        app = FastAPI()
        app.state.bot = bot
        _mock_discord.utils.get = _utils_get_fn

        with patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve, \
             patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock) as mock_handle, \
             patch("api.routers.tags.ChannelConverter") as mock_converter, \
             patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock), \
             patch("api.routers.tags.discord", _mock_discord):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            # Make the tag.delete raise an unexpected error (not HTTPException)
            tag.delete = AsyncMock(side_effect=RuntimeError("Unexpected!"))
            mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")
            mock_converter.forum_tag_to_payload.return_value = _forum_tag_payload()

            from api.routers.tags import router
            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.delete("/api/v1/tags/1234567890")
            assert response.status_code == 500
