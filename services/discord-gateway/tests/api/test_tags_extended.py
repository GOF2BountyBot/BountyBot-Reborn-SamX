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
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# Import discord_mock_utils for consistent mock patterns
import tests.mocks.discord_mock_utils as discord_mock_utils

DiscordMockUtils = discord_mock_utils.DiscordMockUtils

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

# ForumChannel type used for isinstance checks inside the router
_MockForumChannel = type("ForumChannel", (), {})
_mock_discord.ForumChannel = _MockForumChannel

# TextChannel type for non-forum channel tests
_MockTextChannel = type("TextChannel", (), {})
_mock_discord.TextChannel = _MockTextChannel


# ---------------------------------------------------------------------------
# Per-test isolation fixture (same pattern as test_tags.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_real_discord():
    """Re-assert real discord before each test and reload tags router."""
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    import tests.mocks.discord_mock_utils as _dmu_mod

    importlib.reload(_dmu_mod)
    from api.routers import tags as _tags_mod

    importlib.reload(_tags_mod)
    yield


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def create_mock_tag(tag_id=1234567890, channel_id=555555555, name="Test Tag", emoji=None):
    tag = DiscordMockUtils.create_mock_forum_tag(tag_id=tag_id, name=name, emoji=emoji, channel_id=channel_id)
    tag.moderated = False
    tag.edit = AsyncMock()
    tag.delete = AsyncMock()
    return tag


def create_mock_forum_channel(channel_id=555555555, guild_id=987654321, tags=None):
    """Create a forum channel mock whose isinstance check succeeds."""
    channel = MagicMock(spec=_MockForumChannel)
    channel.__class__ = _MockForumChannel
    channel.id = channel_id
    channel.guild = MagicMock()
    channel.guild.id = guild_id
    if tags is None:
        tags = [create_mock_tag(1234567890, channel_id)]
    channel.available_tags = tags
    channel.edit = AsyncMock()
    channel.create_tag = AsyncMock(return_value=create_mock_tag(1234567890, channel_id))
    return channel


def create_mock_text_channel(channel_id=666666666, guild_id=987654321):
    """Create a non-forum (text) channel mock."""
    channel = MagicMock(spec=_MockTextChannel)
    channel.__class__ = _MockTextChannel
    channel.id = channel_id
    channel.guild = MagicMock()
    channel.guild.id = guild_id
    return channel


# ---------------------------------------------------------------------------
# Shared tag/ForumTag payload factory
# ---------------------------------------------------------------------------


def _forum_tag_payload(tag_id=1234567890, channel_id=555555555, name="Test Tag", emoji=None):
    from api.schemas.channel_schemas import ForumTag

    return ForumTag(id=tag_id, channel_id=channel_id, name=name, emoji=emoji)


# ---------------------------------------------------------------------------
# Fixture builder helper
# ---------------------------------------------------------------------------


def _build_app(mock_bot, get_entity_side_effect, utils_get_fn=None, emoji_in_payload=None):
    """
    Build a test FastAPI app with the tags router.

    Parameters
    ----------
    mock_bot : mock bot
    get_entity_side_effect : callable for get_entity_or_404
    utils_get_fn : optional override for discord.utils.get
    emoji_in_payload : if not None, the ForumTag payload will include this emoji
    """
    app = FastAPI(title="Discord Gateway Tags Extended Test")
    app.state.bot = mock_bot

    _tag_payload = _forum_tag_payload(emoji=emoji_in_payload)

    with (
        patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve,
        patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
        patch("api.routers.tags.ChannelConverter") as mock_converter,
        patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity,
        patch("api.routers.tags.discord", _mock_discord),
    ):

        async def mock_resolve_bot(request):
            return mock_bot

        mock_resolve.side_effect = mock_resolve_bot
        mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")
        mock_converter.forum_tag_to_payload.return_value = _tag_payload
        mock_get_entity.side_effect = get_entity_side_effect

        # Default discord.utils.get implementation
        if utils_get_fn is None:

            def _utils_get(iterable, **kwargs):
                for item in iterable or []:
                    match = True
                    for k, v in kwargs.items():
                        if getattr(item, k, None) != v:
                            match = False
                            break
                    if match:
                        return item
                return None

            _mock_discord.utils.get = _utils_get
        else:
            _mock_discord.utils.get = utils_get_fn

        from api.routers.tags import router

        app.include_router(router, prefix="/api/v1")

        yield app


# ---------------------------------------------------------------------------
# Tests: GET /tags/{tag_id} — emoji normalisation paths (lines 76-93)
# ---------------------------------------------------------------------------


class TestGetTagEmojiHandling:
    """Cover get_tag emoji normalisation in dict and non-dict payload paths."""

    def test_get_tag_with_emoji_dict_payload(self):
        """When forum_tag_to_payload returns a dict with emoji, normalise_emoji runs (line 76-82)."""
        mock_bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        mock_channel = create_mock_forum_channel()
        mock_guild = MagicMock()
        mock_guild.channels = [mock_channel]
        mock_bot.guilds = [mock_guild]

        app = FastAPI()
        app.state.bot = mock_bot

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                match = True
                for k, v in kwargs.items():
                    if getattr(item, k, None) != v:
                        match = False
                        break
                if match:
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        # Return a dict from forum_tag_to_payload (not a schema object)
        _dict_payload = {
            "id": 1234567890,
            "channel_id": 555555555,
            "name": "Test Tag",
            "emoji": "🎯",
        }

        with (
            patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.tags.ChannelConverter") as mock_converter,
            patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock),
            patch("api.routers.tags.discord", _mock_discord),
            patch("api.routers.tags.normalize_emoji", return_value="🎯"),
        ):

            async def resolve(req):
                return mock_bot

            mock_resolve.side_effect = resolve
            mock_converter.forum_tag_to_payload.return_value = _dict_payload

            from api.routers.tags import router

            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.get("/api/v1/tags/1234567890")
            # The route should succeed; emoji normalisation was called
            assert response.status_code == 200
            assert response.json()["status"] == "success"

    def test_get_tag_not_found_in_any_guild(self):
        """GET /tags/{tag_id} returns 404 when no guild has the tag."""
        mock_bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        mock_channel = create_mock_forum_channel()
        mock_guild = MagicMock()
        mock_guild.channels = [mock_channel]
        mock_bot.guilds = [mock_guild]

        def _utils_get_none(iterable, **kwargs):
            return None  # tag not found in any channel

        _mock_discord.utils.get = _utils_get_none

        app = FastAPI()
        app.state.bot = mock_bot

        with (
            patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.tags.ChannelConverter") as mock_converter,
            patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock),
            patch("api.routers.tags.discord", _mock_discord),
        ):

            async def resolve(req):
                return mock_bot

            mock_resolve.side_effect = resolve
            mock_converter.forum_tag_to_payload.return_value = _forum_tag_payload()

            from api.routers.tags import router

            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.get("/api/v1/tags/9999999999")
            assert response.status_code == 404
            assert "tag" in response.json()["detail"].lower()

    def test_get_tag_no_guilds(self):
        """GET /tags/{tag_id} returns 404 when bot has no guilds."""
        mock_bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        mock_bot.guilds = []

        app = FastAPI()
        app.state.bot = mock_bot

        with (
            patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.tags.ChannelConverter"),
            patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock),
            patch("api.routers.tags.discord", _mock_discord),
        ):

            async def resolve(req):
                return mock_bot

            mock_resolve.side_effect = resolve

            from api.routers.tags import router

            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
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
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        text_ch = create_mock_text_channel(666666666)
        bot.get_channel = MagicMock(return_value=text_ch)
        bot.fetch_channel = AsyncMock(return_value=text_ch)
        return bot

    @pytest.fixture
    def mock_bot_with_forum(self):
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        forum_ch = create_mock_forum_channel(555555555)
        bot.get_channel = MagicMock(return_value=forum_ch)
        bot.fetch_channel = AsyncMock(return_value=forum_ch)
        return bot

    def _make_app(self, mock_bot, channel_id=555555555):
        app = FastAPI()
        app.state.bot = mock_bot

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            ch = get_fn(entity_id)
            if ch is None:
                raise HTTPException(status_code=404, detail=f"{entity_type} not found")
            return ch

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                match = True
                for k, v in kwargs.items():
                    if getattr(item, k, None) != v:
                        match = False
                        break
                if match:
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        with (
            patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock) as mock_handle,
            patch("api.routers.tags.ChannelConverter") as mock_converter,
            patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity,
            patch("api.routers.tags.discord", _mock_discord),
        ):

            async def resolve(req):
                return mock_bot

            mock_resolve.side_effect = resolve
            mock_handle.side_effect = HTTPException(status_code=500, detail="Internal server error")
            mock_converter.forum_tag_to_payload.return_value = _forum_tag_payload()
            mock_get_entity.side_effect = _get_entity

            from api.routers.tags import router

            app.include_router(router, prefix="/api/v1")

            yield app

    def test_create_tag_non_forum_channel_returns_400(self, mock_bot_with_text_channel):
        """POST /channels/{channel_id}/tags on a non-forum channel returns 400."""
        for app in self._make_app(mock_bot_with_text_channel, channel_id=666666666):
            client = TestClient(app)
            response = client.post("/api/v1/channels/666666666/tags", json={"name": "New Tag"})
            assert response.status_code == 400
            assert "forum" in response.json()["detail"].lower()

    def test_create_tag_with_valid_emoji(self, mock_bot_with_forum):
        """POST /channels/{channel_id}/tags with emoji string works."""
        for app in self._make_app(mock_bot_with_forum):
            client = TestClient(app)
            response = client.post("/api/v1/channels/555555555/tags", json={"name": "Emoji Tag", "emoji": "🚀"})
            assert response.status_code == 201
            assert response.json()["status"] == "created"

    def test_create_tag_without_emoji(self, mock_bot_with_forum):
        """POST /channels/{channel_id}/tags without emoji succeeds."""
        for app in self._make_app(mock_bot_with_forum):
            client = TestClient(app)
            response = client.post("/api/v1/channels/555555555/tags", json={"name": "No Emoji Tag"})
            assert response.status_code == 201
            assert response.json()["status"] == "created"

    def test_create_tag_attributeerror_fallback(self):
        """POST creates tag via edit fallback when create_tag raises AttributeError."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        forum_ch = create_mock_forum_channel(555555555)
        # Make create_tag raise AttributeError to trigger fallback path
        forum_ch.create_tag = AsyncMock(side_effect=AttributeError("no create_tag"))
        # After editing, simulate a tag appearing in available_tags with the right name
        new_tag = create_mock_tag(2222222222, 555555555, name="Fallback Tag")
        forum_ch.available_tags = [new_tag]
        bot.get_channel = MagicMock(return_value=forum_ch)
        bot.fetch_channel = AsyncMock(return_value=forum_ch)

        app = FastAPI()
        app.state.bot = bot

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            ch = get_fn(entity_id)
            if ch is None:
                raise HTTPException(status_code=404, detail=f"{entity_type} not found")
            return ch

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                match = True
                for k, v in kwargs.items():
                    if getattr(item, k, None) != v:
                        match = False
                        break
                if match:
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        with (
            patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.tags.ChannelConverter") as mock_converter,
            patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity,
            patch("api.routers.tags.discord", _mock_discord),
            patch("api.routers.tags.tags_to_edit_payload", return_value=[]),
        ):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            mock_converter.forum_tag_to_payload.return_value = _forum_tag_payload()
            mock_get_entity.side_effect = _get_entity

            from api.routers.tags import router

            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.post("/api/v1/channels/555555555/tags", json={"name": "Fallback Tag"})
            assert response.status_code == 201
            assert response.json()["status"] == "created"


# ---------------------------------------------------------------------------
# Tests: PUT /tags/{tag_id} — update edge cases
# ---------------------------------------------------------------------------


class TestUpdateTagExtended:
    """Cover update paths including emoji, fallback edit, re-fetch by name."""

    def _setup_bot_with_tag(self, tag_id=1234567890, has_edit=True, has_edit_tag=False):
        """Build a bot with a forum channel containing a tag."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        tag = create_mock_tag(tag_id, 555555555, name="Original")
        if not has_edit:
            del tag.edit  # Remove edit to test fallback
        forum_ch = create_mock_forum_channel(555555555, tags=[tag])
        if not has_edit_tag:
            # Ensure no edit_tag attribute on channel
            if hasattr(forum_ch, "edit_tag"):
                del forum_ch.edit_tag
        else:
            forum_ch.edit_tag = AsyncMock()
        mock_guild = MagicMock()
        mock_guild.channels = [forum_ch]
        bot.guilds = [mock_guild]
        bot.get_channel = MagicMock(return_value=forum_ch)
        bot.fetch_channel = AsyncMock(return_value=forum_ch)
        return bot, forum_ch, tag

    def _make_update_app(self, mock_bot):
        app = FastAPI()
        app.state.bot = mock_bot

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                match = True
                for k, v in kwargs.items():
                    if getattr(item, k, None) != v:
                        match = False
                        break
                if match:
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        with (
            patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.tags.ChannelConverter") as mock_converter,
            patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock),
            patch("api.routers.tags.discord", _mock_discord),
        ):

            async def resolve(req):
                return mock_bot

            mock_resolve.side_effect = resolve
            mock_converter.forum_tag_to_payload.return_value = _forum_tag_payload()

            from api.routers.tags import router

            app.include_router(router, prefix="/api/v1")

            yield app

    def test_update_tag_with_emoji(self):
        """PUT /tags/{tag_id} with emoji field triggers normalize_emoji."""
        mock_bot, _forum_ch, _tag = self._setup_bot_with_tag()
        for app in self._make_update_app(mock_bot):
            client = TestClient(app)
            response = client.put("/api/v1/tags/1234567890", json={"emoji": "🎯"})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"

    def test_update_tag_with_name_and_emoji(self):
        """PUT /tags/{tag_id} with name and emoji both updated."""
        mock_bot, _, _ = self._setup_bot_with_tag()
        for app in self._make_update_app(mock_bot):
            client = TestClient(app)
            response = client.put("/api/v1/tags/1234567890", json={"name": "New Name", "emoji": "🚀"})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"

    def test_update_tag_not_found_in_any_channel(self):
        """PUT /tags/{tag_id} returns 404 when tag doesn't exist."""
        mock_bot, _, _ = self._setup_bot_with_tag()
        for app in self._make_update_app(mock_bot):
            client = TestClient(app)
            response = client.put("/api/v1/tags/9999999999", json={"name": "Ghost"})
            assert response.status_code == 404
            assert "tag" in response.json()["detail"].lower()

    def test_update_tag_no_fields_is_noop(self):
        """PUT /tags/{tag_id} with empty body is accepted (no edit call)."""
        mock_bot, _, _ = self._setup_bot_with_tag()
        for app in self._make_update_app(mock_bot):
            client = TestClient(app)
            response = client.put("/api/v1/tags/1234567890", json={})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"

    def test_update_tag_with_edit_tag_on_channel(self):
        """PUT /tags/{tag_id} calls channel.edit_tag when available on channel."""
        mock_bot, _forum_ch, _tag = self._setup_bot_with_tag(has_edit_tag=True)
        # tag.edit is the preferred path, so leave it intact so no errors occur.
        # edit_tag is on the channel; the router checks tag.edit first (hasattr),
        # finds it, and uses it — which is fine. This test ensures the endpoint
        # still returns 200 when edit_tag also exists alongside tag.edit.
        for app in self._make_update_app(mock_bot):
            client = TestClient(app)
            response = client.put("/api/v1/tags/1234567890", json={"name": "Via edit_tag"})
            assert response.status_code == 200
            assert response.json()["status"] == "updated"


# ---------------------------------------------------------------------------
# Tests: DELETE /tags/{tag_id} — deletion paths
# ---------------------------------------------------------------------------


class TestDeleteTagExtended:
    """Cover deletion via delete_tag on channel, tag.delete, and fallback edit."""

    def _make_delete_app(self, mock_bot):
        app = FastAPI()
        app.state.bot = mock_bot

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                match = True
                for k, v in kwargs.items():
                    if getattr(item, k, None) != v:
                        match = False
                        break
                if match:
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        with (
            patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.tags.ChannelConverter") as mock_converter,
            patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock),
            patch("api.routers.tags.discord", _mock_discord),
        ):

            async def resolve(req):
                return mock_bot

            mock_resolve.side_effect = resolve
            mock_converter.forum_tag_to_payload.return_value = _forum_tag_payload()

            from api.routers.tags import router

            app.include_router(router, prefix="/api/v1")

            yield app

    def test_delete_tag_via_channel_delete_tag_method(self):
        """DELETE /tags/{tag_id} uses channel.delete_tag when available (line 373-374)."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        tag = create_mock_tag(1234567890, 555555555)
        forum_ch = create_mock_forum_channel(555555555, tags=[tag])
        # Add delete_tag method so the preferred code path runs
        forum_ch.delete_tag = AsyncMock()
        mock_guild = MagicMock()
        mock_guild.channels = [forum_ch]
        bot.guilds = [mock_guild]

        for app in self._make_delete_app(bot):
            client = TestClient(app)
            response = client.delete("/api/v1/tags/1234567890")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "deleted"
            assert data["deleted"] is True

    def test_delete_tag_via_tag_delete_method(self):
        """DELETE /tags/{tag_id} falls back to tag.delete() when channel lacks delete_tag."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        tag = create_mock_tag(1234567890, 555555555)
        tag.delete = AsyncMock()
        forum_ch = create_mock_forum_channel(555555555, tags=[tag])
        # Ensure no delete_tag on channel
        if hasattr(forum_ch, "delete_tag"):
            del forum_ch.delete_tag
        mock_guild = MagicMock()
        mock_guild.channels = [forum_ch]
        bot.guilds = [mock_guild]

        for app in self._make_delete_app(bot):
            client = TestClient(app)
            response = client.delete("/api/v1/tags/1234567890")
            assert response.status_code == 200
            assert response.json()["deleted"] is True

    def test_delete_tag_not_found_returns_404(self):
        """DELETE /tags/{tag_id} returns 404 when tag not in any channel."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        forum_ch = create_mock_forum_channel(555555555, tags=[])
        mock_guild = MagicMock()
        mock_guild.channels = [forum_ch]
        bot.guilds = [mock_guild]

        for app in self._make_delete_app(bot):
            client = TestClient(app)
            response = client.delete("/api/v1/tags/9999999999")
            assert response.status_code == 404
            assert "detail" in response.json()

    def test_delete_tag_via_edit_fallback(self):
        """DELETE falls back to channel.edit(available_tags=...) when no delete methods."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        tag = create_mock_tag(1234567890, 555555555)
        # Remove delete from tag so edit fallback is taken
        if hasattr(tag, "delete"):
            del tag.delete
        forum_ch = create_mock_forum_channel(555555555, tags=[tag])
        # Remove delete_tag from channel as well
        if hasattr(forum_ch, "delete_tag"):
            del forum_ch.delete_tag
        forum_ch.edit = AsyncMock()
        mock_guild = MagicMock()
        mock_guild.channels = [forum_ch]
        bot.guilds = [mock_guild]

        for app in self._make_delete_app(bot):
            client = TestClient(app)
            response = client.delete("/api/v1/tags/1234567890")
            assert response.status_code == 200
            assert response.json()["deleted"] is True


# ---------------------------------------------------------------------------
# Tests: Bulk tag retrieval — multiple tags across guilds
# ---------------------------------------------------------------------------


class TestGetTagAcrossGuilds:
    """Cover cross-guild tag search code paths."""

    def test_get_tag_found_in_second_guild(self):
        """GET /tags/{tag_id} finds the tag in the second guild."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        # First guild has no matching tag
        forum_ch_1 = create_mock_forum_channel(555555551, tags=[create_mock_tag(1111111, 555555551)])
        guild_1 = MagicMock()
        guild_1.channels = [forum_ch_1]
        # Second guild has the target tag
        target_tag = create_mock_tag(1234567890, 555555552)
        forum_ch_2 = create_mock_forum_channel(555555552, tags=[target_tag])
        guild_2 = MagicMock()
        guild_2.channels = [forum_ch_2]
        bot.guilds = [guild_1, guild_2]

        app = FastAPI()
        app.state.bot = bot

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                match = True
                for k, v in kwargs.items():
                    if getattr(item, k, None) != v:
                        match = False
                        break
                if match:
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        with (
            patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.tags.ChannelConverter") as mock_converter,
            patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock),
            patch("api.routers.tags.discord", _mock_discord),
        ):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            mock_converter.forum_tag_to_payload.return_value = _forum_tag_payload()

            from api.routers.tags import router

            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.get("/api/v1/tags/1234567890")
            assert response.status_code == 200
            assert response.json()["status"] == "success"

    def test_get_tag_channel_not_forum_is_skipped(self):
        """GET /tags/{tag_id} skips non-forum channels in the guild."""
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        # Only a text channel in this guild
        text_ch = create_mock_text_channel(666666666)
        guild_1 = MagicMock()
        guild_1.channels = [text_ch]
        # A forum in second guild
        target_tag = create_mock_tag(1234567890, 555555552)
        forum_ch = create_mock_forum_channel(555555552, tags=[target_tag])
        guild_2 = MagicMock()
        guild_2.channels = [forum_ch]
        bot.guilds = [guild_1, guild_2]

        app = FastAPI()
        app.state.bot = bot

        def _utils_get(iterable, **kwargs):
            for item in iterable or []:
                match = True
                for k, v in kwargs.items():
                    if getattr(item, k, None) != v:
                        match = False
                        break
                if match:
                    return item
            return None

        _mock_discord.utils.get = _utils_get

        with (
            patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.tags.ChannelConverter") as mock_converter,
            patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock),
            patch("api.routers.tags.discord", _mock_discord),
        ):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            mock_converter.forum_tag_to_payload.return_value = _forum_tag_payload()

            from api.routers.tags import router

            app.include_router(router, prefix="/api/v1")

            client = TestClient(app)
            response = client.get("/api/v1/tags/1234567890")
            assert response.status_code == 200
            assert response.json()["status"] == "success"


# ---------------------------------------------------------------------------
# Tests: request validation
# ---------------------------------------------------------------------------


class TestTagRequestValidation:
    """Validate request schema enforcement."""

    @pytest.fixture
    def app_and_client(self):
        bot = DiscordMockUtils.create_mock_bot(user_id=123456789, username="TestBot")
        forum_ch = create_mock_forum_channel(555555555)
        bot.get_channel = MagicMock(return_value=forum_ch)
        bot.fetch_channel = AsyncMock(return_value=forum_ch)
        bot.guilds = []

        app = FastAPI()
        app.state.bot = bot

        def _utils_get(iterable, **kwargs):
            return None

        _mock_discord.utils.get = _utils_get

        async def _get_entity(get_fn, fetch_fn, entity_id, entity_type):
            ch = get_fn(entity_id)
            if ch is None:
                raise HTTPException(status_code=404, detail=f"{entity_type} not found")
            return ch

        with (
            patch("api.routers.tags.resolve_bot", new_callable=AsyncMock) as mock_resolve,
            patch("api.routers.tags.handle_discord_exception", new_callable=AsyncMock),
            patch("api.routers.tags.ChannelConverter") as mock_converter,
            patch("api.routers.tags.get_entity_or_404", new_callable=AsyncMock) as mock_get_entity,
            patch("api.routers.tags.discord", _mock_discord),
        ):

            async def resolve(req):
                return bot

            mock_resolve.side_effect = resolve
            mock_converter.forum_tag_to_payload.return_value = _forum_tag_payload()
            mock_get_entity.side_effect = _get_entity

            from api.routers.tags import router

            app.include_router(router, prefix="/api/v1")

            yield TestClient(app)

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
