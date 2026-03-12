"""
Tests for discord_helpers.py utilities.

This module provides comprehensive test coverage for the Discord helper utilities,
including bot resolution, error handling, entity retrieval, validation, and emoji normalization.
"""

import asyncio
import os
import sys

# conftest.py imports real discord BEFORE any test file runs and saves it as
# _REAL_DISCORD.  The sys.modules key for conftest varies by how pytest is
# invoked ("conftest" for full-suite, "tests.conftest" for subset runs), so
# we try both.
import sys as _sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from tests.mocks.discord_mock_utils import DiscordMockUtils

_conftest_mod = _sys.modules.get("tests.conftest") or _sys.modules.get("conftest")
_real_discord = _conftest_mod._REAL_DISCORD


_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    """Return a MagicMock that already has common log-level methods."""
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


# Build a hand-rolled fake discord module that:
# - Uses plain Python classes (not MagicMock) for channel/role/user types so
#   that isinstance() checks in discord_converters.py never raise TypeError.
# - Uses the *real* discord exception classes (NotFound, Forbidden,
#   HTTPException) so that isinstance checks inside discord_helpers.py work
#   regardless of which module had discord when discord_helpers was imported.
_mock_discord = types.ModuleType("discord")

_MockCategoryChannel = type("CategoryChannel", (), {})
_MockTextChannel = type("TextChannel", (), {})
_MockVoiceChannel = type("VoiceChannel", (), {})
_MockForumChannel = type("ForumChannel", (), {})
_MockThread = type("Thread", (), {})
_MockEmbed = type("Embed", (), {})
_MockPermissionOverwrite = type("PermissionOverwrite", (), {})
_MockGuild = type("Guild", (), {})
_MockUser = type("User", (), {})
_MockMember = type("Member", (), {})
_MockRole = type("Role", (), {})
_MockMessage = type("Message", (), {})

_mock_discord.CategoryChannel = _MockCategoryChannel
_mock_discord.TextChannel = _MockTextChannel
_mock_discord.VoiceChannel = _MockVoiceChannel
_mock_discord.ForumChannel = _MockForumChannel
_mock_discord.Thread = _MockThread
_mock_discord.Embed = _MockEmbed
_mock_discord.PermissionOverwrite = _MockPermissionOverwrite
_mock_discord.Guild = _MockGuild
_mock_discord.User = _MockUser
_mock_discord.Member = _MockMember
_mock_discord.Role = _MockRole
_mock_discord.Message = _MockMessage
# Use the real discord exception classes so that isinstance() checks in
# discord_helpers.py pass regardless of test execution order.
_mock_discord.Forbidden = _real_discord.Forbidden
_mock_discord.NotFound = _real_discord.NotFound
_mock_discord.HTTPException = _real_discord.HTTPException

_mock_discord_ext = types.ModuleType("discord.ext")
# Use the real commands module saved by conftest (before any fake injection).
_real_commands = _conftest_mod._REAL_DISCORD_EXT_COMMANDS
_mock_discord_ext.commands = types.ModuleType("discord.ext.commands")
_mock_discord_ext.commands.Bot = _real_commands.Bot  # real Bot for isinstance in resolve_bot

_MockPermissions = type("Permissions", (), {"value": 0})
_mock_discord.Permissions = _MockPermissions

_MockColor = type("Color", (), {"value": 0})
_mock_discord.Color = _MockColor

sys.modules["discord"] = _mock_discord
sys.modules["discord.ext"] = _mock_discord_ext
sys.modules["discord.ext.commands"] = _mock_discord_ext.commands


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Per-test isolation fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_real_discord():
    """
    Re-assert the real discord module into sys.modules before each test
    and reload utils.discord_helpers so its ``discord`` reference is fresh.

    discord_helpers.py captures ``import discord`` at module-load time.
    When the full test suite runs, the API test fixtures import
    discord_helpers.py while a hand-rolled fake (with fake exception types)
    is in sys.modules["discord"].  From that point on, the cached module's
    ``discord.NotFound`` etc. are the fake classes, so isinstance checks
    fail regardless of what we put in sys.modules later.

    By restoring the real discord AND reloading the module before every
    test we ensure the isinstance checks always use real discord types.
    No teardown restore is performed: leaving real discord in sys.modules
    after our tests run is cleaner for downstream test files.
    """
    import importlib
    # conftest saves real discord references before any test file pollutes
    # sys.modules.  The conftest key varies: "tests.conftest" for subset
    # runs, "conftest" for full-suite runs; try both.
    _cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS
    # Reload discord_mock_utils so its module-level ``discord`` binding (and
    # therefore ``discord.NotFound`` etc. used by create_discord_not_found())
    # is updated to real discord.  This also updates the ``__globals__`` dict
    # of already-imported function/method references from this module, because
    # importlib.reload() updates the module's __dict__ in-place.
    import tests.mocks.discord_mock_utils as _dmu_mod
    importlib.reload(_dmu_mod)
    # Force discord_helpers to re-bind its 'discord' global to real discord.
    import utils.discord_helpers as _dh_mod
    importlib.reload(_dh_mod)
    yield


class TestResolveBot:
    """Tests for resolve_bot function."""

    @staticmethod
    def _make_mock_bot(is_ready: bool = True) -> MagicMock:
        """
        Create a bot mock that passes ``isinstance(bot, commands.Bot)`` as seen
        by the already-imported ``discord_helpers`` module.

        The test module replaces ``sys.modules["discord.ext.commands"]`` with a
        hand-rolled fake, but ``discord_helpers`` captures the *real*
        ``commands`` reference at import time.  Using ``MagicMock(spec=...)``
        with that same real ``commands.Bot`` class is the only way to ensure
        the isinstance guard inside ``resolve_bot`` is satisfied regardless of
        whether the tests run in isolation or as part of the full suite.
        """
        import utils.discord_helpers as _dh  # already cached; gives us the real commands
        real_bot_cls = _dh.commands.Bot
        mock_bot = MagicMock(spec=real_bot_cls)
        mock_bot.is_ready = MagicMock(return_value=is_ready)
        mock_bot.wait_until_ready = AsyncMock()
        return mock_bot

    @pytest.fixture
    def mock_request(self):
        """Create a mock FastAPI request."""
        request = MagicMock()
        request.app.state.bot = None
        return request

    def test_resolve_bot_returns_bot_when_ready(self, mock_request):
        """resolve_bot should return bot when it's ready."""
        mock_bot = self._make_mock_bot(is_ready=True)
        mock_request.app.state.bot = mock_bot

        from utils.discord_helpers import resolve_bot
        result = asyncio.run(resolve_bot(mock_request))

        assert result == mock_bot
        mock_bot.wait_until_ready.assert_not_called()

    def test_resolve_bot_awaits_bot_when_not_ready(self, mock_request):
        """resolve_bot should await bot when it's not ready."""
        mock_bot = self._make_mock_bot(is_ready=False)
        mock_request.app.state.bot = mock_bot

        from utils.discord_helpers import resolve_bot
        result = asyncio.run(resolve_bot(mock_request))

        assert result == mock_bot
        mock_bot.wait_until_ready.assert_called_once()

    def test_resolve_bot_times_out_when_bot_not_ready(self, mock_request):
        """resolve_bot should raise HTTPException when bot doesn't become ready."""
        mock_bot = self._make_mock_bot(is_ready=False)
        mock_bot.wait_until_ready = AsyncMock(side_effect=TimeoutError())
        mock_request.app.state.bot = mock_bot

        from utils.discord_helpers import resolve_bot

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(resolve_bot(mock_request))

        assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "Discord bot is not ready" in exc_info.value.detail

    def test_resolve_bot_raises_on_invalid_bot_instance(self, mock_request):
        """resolve_bot should raise HTTPException when bot instance is invalid (not commands.Bot)."""
        mock_request.app.state.bot = MagicMock()  # plain MagicMock is NOT a _MockBot instance

        from utils.discord_helpers import resolve_bot

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(resolve_bot(mock_request))

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Bot instance invalid" in exc_info.value.detail


class TestHandleDiscordException:
    """Tests for handle_discord_exception function."""

    @pytest.fixture
    def mock_logger(self):
        """Create a mock logger."""
        return MagicMock()

    def test_handle_discord_exception_handles_not_found(self, mock_logger):
        """handle_discord_exception should handle NotFound exception correctly."""
        mock_exc = DiscordMockUtils.create_discord_not_found()

        from utils.discord_helpers import handle_discord_exception

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handle_discord_exception("test operation", mock_exc))

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "Resource not found during test operation" in exc_info.value.detail

    def test_handle_discord_exception_handles_forbidden(self, mock_logger):
        """handle_discord_exception should handle Forbidden exception correctly."""
        mock_exc = DiscordMockUtils.create_discord_forbidden()

        from utils.discord_helpers import handle_discord_exception

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handle_discord_exception("test operation", mock_exc))

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "Insufficient permissions for test operation" in exc_info.value.detail

    def test_handle_discord_exception_handles_http_exception_with_status(self, mock_logger):
        """handle_discord_exception should handle HTTPException with status code."""
        mock_exc = DiscordMockUtils.create_discord_http_exception(status=404)

        from utils.discord_helpers import handle_discord_exception

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handle_discord_exception("test operation", mock_exc))

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "Resource not found during test operation" in exc_info.value.detail

    def test_handle_discord_exception_handles_http_exception_with_403(self, mock_logger):
        """handle_discord_exception should handle HTTPException with 403 status."""
        mock_exc = DiscordMockUtils.create_discord_http_exception(status=403, code=50013)

        from utils.discord_helpers import handle_discord_exception

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handle_discord_exception("test operation", mock_exc))

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "Insufficient permissions for test operation" in exc_info.value.detail
        assert "50013" in exc_info.value.detail

    def test_handle_discord_exception_handles_http_exception_with_400(self, mock_logger):
        """handle_discord_exception should handle HTTPException with 400 status."""
        mock_exc = DiscordMockUtils.create_discord_http_exception(status=400, code=10006)

        from utils.discord_helpers import handle_discord_exception

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handle_discord_exception("test operation", mock_exc))

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Bad request during test operation" in exc_info.value.detail

    def test_handle_discord_exception_handles_http_exception_with_5xx(self, mock_logger):
        """handle_discord_exception should handle HTTPException with 5xx status."""
        mock_exc = DiscordMockUtils.create_discord_http_exception(status=502)

        from utils.discord_helpers import handle_discord_exception

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handle_discord_exception("test operation", mock_exc))

        assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
        assert "Discord upstream error:" in exc_info.value.detail

    def test_handle_discord_exception_handles_unknown_exception(self, mock_logger):
        """handle_discord_exception should handle unknown exceptions as 500."""
        mock_exc = Exception("Test error")

        from utils.discord_helpers import handle_discord_exception

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handle_discord_exception("test operation", mock_exc))

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Failed to test operation" in exc_info.value.detail


class TestGetEntityOr404:
    """Tests for get_entity_or404 function."""

    @pytest.fixture
    def mock_get_func(self):
        """Create a mock get function."""
        return MagicMock()

    @pytest.fixture
    def mock_fetch_func(self):
        """Create a mock fetch function."""
        return AsyncMock()

    def test_get_entity_or_404_returns_entity_from_cache(self, mock_get_func, mock_fetch_func):
        """get_entity_or_404 should return entity from cache when available."""
        mock_entity = MagicMock()
        mock_get_func.return_value = mock_entity

        from utils.discord_helpers import get_entity_or_404

        result = asyncio.run(get_entity_or_404(mock_get_func, mock_fetch_func, 123, "test entity"))

        assert result == mock_entity
        mock_get_func.assert_called_once_with(123)
        mock_fetch_func.assert_not_called()

    def test_get_entity_or_404_fetches_entity_when_not_in_cache(self, mock_get_func, mock_fetch_func):
        """get_entity_or_404 should fetch entity when not in cache."""
        mock_get_func.return_value = None
        mock_entity = MagicMock()
        mock_fetch_func.return_value = mock_entity

        from utils.discord_helpers import get_entity_or_404

        result = asyncio.run(get_entity_or_404(mock_get_func, mock_fetch_func, 123, "test entity"))

        assert result == mock_entity
        mock_get_func.assert_called_once_with(123)
        mock_fetch_func.assert_called_once_with(123)

    def test_get_entity_or_404_raises_not_found_when_entity_not_found(self, mock_get_func, mock_fetch_func):
        """get_entity_or_404 should raise HTTPException when entity not found."""
        mock_get_func.return_value = None
        mock_fetch_func.side_effect = DiscordMockUtils.create_discord_not_found()

        from utils.discord_helpers import get_entity_or_404

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_entity_or_404(mock_get_func, mock_fetch_func, 123, "test entity"))

        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
        assert "Resource not found during fetch test entity 123" in exc_info.value.detail

    def test_get_entity_or_404_raises_forbidden_when_access_denied(self, mock_get_func, mock_fetch_func):
        """get_entity_or_404 should raise HTTPException when access denied."""
        mock_get_func.return_value = None
        mock_fetch_func.side_effect = DiscordMockUtils.create_discord_forbidden()

        from utils.discord_helpers import get_entity_or_404

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_entity_or_404(mock_get_func, mock_fetch_func, 123, "test entity"))

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "Insufficient permissions for fetch test entity 123" in exc_info.value.detail

    def test_get_entity_or_404_handles_http_exception(self, mock_get_func, mock_fetch_func):
        """get_entity_or_404 should handle HTTPException through handle_discord_exception."""
        mock_get_func.return_value = None
        mock_fetch_func.side_effect = DiscordMockUtils.create_discord_http_exception()

        from utils.discord_helpers import get_entity_or_404

        with pytest.raises(HTTPException):
            asyncio.run(get_entity_or_404(mock_get_func, mock_fetch_func, 123, "test entity"))


class TestValidationFunctions:
    """Tests for validation functions."""

    def test_validate_guild_channel_relationship_validates_correct_relationship(self):
        """validate_guild_channel_relationship should validate correct relationship."""
        mock_channel = DiscordMockUtils.create_mock_channel(channel_id=1, guild_id=987654321)

        from utils.discord_helpers import validate_guild_channel_relationship

        validate_guild_channel_relationship(mock_channel, 987654321)

    def test_validate_guild_channel_relationship_raises_on_incorrect_relationship(self):
        """validate_guild_channel_relationship should raise HTTPException on incorrect relationship."""
        mock_channel = DiscordMockUtils.create_mock_channel(channel_id=1, guild_id=111111111)

        from utils.discord_helpers import validate_guild_channel_relationship

        with pytest.raises(HTTPException) as exc_info:
            validate_guild_channel_relationship(mock_channel, 987654321)

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Channel" in exc_info.value.detail
        assert "does not belong to guild" in exc_info.value.detail

    def test_validate_guild_channel_relationship_handles_missing_guild(self):
        """validate_guild_channel_relationship should handle missing guild attribute."""
        mock_channel = DiscordMockUtils.create_mock_channel(channel_id=1)
        mock_channel.guild = None

        from utils.discord_helpers import validate_guild_channel_relationship

        with pytest.raises(HTTPException) as exc_info:
            validate_guild_channel_relationship(mock_channel, 987654321)

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Channel" in exc_info.value.detail
        assert "does not belong to guild" in exc_info.value.detail

    def test_validate_channel_type_validates_correct_type(self):
        """validate_channel_type should validate correct channel type."""
        mock_channel = DiscordMockUtils.create_mock_channel(channel_id=1, channel_type="text")

        from utils.discord_helpers import validate_channel_type

        validate_channel_type(mock_channel, ["text", "voice"], 123)

    def test_validate_channel_type_raises_on_incorrect_type(self):
        """validate_channel_type should raise HTTPException on incorrect channel type."""
        mock_channel = DiscordMockUtils.create_mock_channel(channel_id=1, channel_type="category")

        from utils.discord_helpers import validate_channel_type

        with pytest.raises(HTTPException) as exc_info:
            validate_channel_type(mock_channel, ["text", "voice"], 123)

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "Channel 123 is type category" in exc_info.value.detail
        assert "expected one of: ['text', 'voice']" in exc_info.value.detail

    def test_validate_channel_type_handles_string_type(self):
        """validate_channel_type should handle string type attribute."""
        mock_channel = MagicMock()
        mock_channel.type = "text"

        from utils.discord_helpers import validate_channel_type

        validate_channel_type(mock_channel, ["text", "voice"], 123)


class TestEmojiNormalization:
    """Tests for normalize_emoji function."""

    def test_normalize_emoji_returns_unicode_emoji(self):
        """normalize_emoji should return unicode emoji unchanged."""
        emoji = "👍"
        from utils.discord_helpers import normalize_emoji
        result = normalize_emoji(emoji)
        assert result == "👍"

    def test_normalize_emoji_converts_hex_codepoint(self):
        """normalize_emoji should convert hex codepoint to unicode."""
        emoji = "1f4cc"
        from utils.discord_helpers import normalize_emoji
        result = normalize_emoji(emoji)
        assert result == "📌"

    def test_normalize_emoji_converts_u_prefix_codepoint(self):
        """normalize_emoji should convert U+ prefix codepoint to unicode."""
        emoji = "U+1F4CC"
        from utils.discord_helpers import normalize_emoji
        result = normalize_emoji(emoji)
        assert result == "📌"

    def test_normalize_emoji_converts_0x_prefix_codepoint(self):
        """normalize_emoji should convert 0x prefix codepoint to unicode."""
        emoji = "0x1f4cc"
        from utils.discord_helpers import normalize_emoji
        result = normalize_emoji(emoji)
        assert result == "📌"

    def test_normalize_emoji_converts_concatenated_codepoints(self):
        """normalize_emoji should convert concatenated codepoints to unicode."""
        emoji = "1f3f7fe0f"
        from utils.discord_helpers import normalize_emoji
        result = normalize_emoji(emoji)
        # This should convert to a valid emoji sequence
        assert len(result) > 0

    def test_normalize_emoji_handles_custom_emoji_full_form(self):
        """normalize_emoji should handle full custom emoji form."""
        emoji = "<a:test:123456789>"
        from utils.discord_helpers import normalize_emoji
        result = normalize_emoji(emoji)
        assert result == "<a:test:123456789>"

    def test_normalize_emoji_handles_custom_emoji_short_form(self):
        """normalize_emoji should handle short custom emoji form."""
        emoji = ":test:"
        from utils.discord_helpers import normalize_emoji
        result = normalize_emoji(emoji)
        assert result == "test"

    def test_normalize_emoji_handles_invalid_input(self):
        """normalize_emoji should handle invalid input gracefully."""
        emoji = 123
        from utils.discord_helpers import normalize_emoji
        result = normalize_emoji(emoji)
        assert result == 123


class TestTagToDict:
    """Tests for tag_to_dict function."""

    def test_tag_to_dict_converts_mapping_tag(self):
        """tag_to_dict should convert mapping tag correctly."""
        tag = {
            "id": 111,
            "channel_id": 222,
            "name": "test-tag",
            "emoji": "👍",
            "extra_field": "should be ignored"
        }

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(tag, channel_id=333)

        assert result["id"] == 111
        assert result["channel_id"] == 333
        assert result["name"] == "test-tag"
        assert result["emoji"] == "👍"

    def test_tag_to_dict_converts_object_tag(self):
        """tag_to_dict should convert object tag correctly."""
        mock_tag = DiscordMockUtils.create_mock_forum_tag(
            tag_id=111, name="test-tag", emoji="👍", channel_id=222
        )

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(mock_tag, channel_id=333)

        assert result["id"] == 111
        assert result["channel_id"] == 333
        assert result["name"] == "test-tag"
        assert result["emoji"] == "👍"

    def test_tag_to_dict_handles_missing_fields(self):
        """tag_to_dict should handle missing fields gracefully."""
        mock_tag = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="test-tag")

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(mock_tag, channel_id=333)

        assert result["id"] == 111
        assert result["channel_id"] == 333
        assert result["name"] == "test-tag"
        assert result["emoji"] is None

    def test_tag_to_dict_handles_nested_emoji(self):
        """tag_to_dict should handle nested emoji structures."""
        mock_tag = DiscordMockUtils.create_mock_forum_tag(tag_id=111, name="test-tag")
        mock_tag.emoji = MagicMock()
        mock_tag.emoji.emoji = "👍"

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(mock_tag, channel_id=333)

        assert result["emoji"] == "👍"

    def test_tag_to_dict_handles_to_dict_method(self):
        """tag_to_dict should handle tag with to_dict method."""
        class TagWithDict:
            def __init__(self, tag_id, name, emoji):
                self.id = tag_id
                self.name = name
                self.emoji = emoji

            def to_dict(self):
                return {"id": self.id, "name": self.name, "emoji": self.emoji}

        tag = TagWithDict(111, "test-tag", "👍")

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(tag, channel_id=333)

        assert result["id"] == 111
        assert result["name"] == "test-tag"
        assert result["emoji"] == "👍"


class TestTagsToEditPayload:
    """Tests for tags_to_edit_payload function."""

    def test_tags_to_edit_payload_converts_basic_tags(self):
        """tags_to_edit_payload should convert basic tags correctly."""
        class MockTag:
            def __init__(self, tag_id, name, emoji):
                self.id = tag_id
                self.name = name
                self.emoji = emoji

        tags = [
            MockTag(111, "tag1", "👍"),
            MockTag(222, "tag2", "📝")
        ]

        from utils.discord_helpers import tags_to_edit_payload
        result = tags_to_edit_payload(tags)

        assert len(result) == 2
        assert result[0]["id"] == 111
        assert result[0]["name"] == "tag1"
        assert result[0]["emoji"] == "👍"
        assert result[1]["id"] == 222
        assert result[1]["name"] == "tag2"
        assert result[1]["emoji"] == "📝"

    def test_tags_to_edit_payload_handles_updates(self):
        """tags_to_edit_payload should handle updates correctly."""
        class MockTag:
            def __init__(self, tag_id, name, emoji):
                self.id = tag_id
                self.name = name
                self.emoji = emoji

        tags = [
            MockTag(111, "tag1", "👍"),
            MockTag(222, "tag2", "📝")
        ]

        updates = {
            111: {"name": "updated-tag1", "emoji": "👎"},
            333: {"name": "new-tag", "emoji": "👑"}
        }

        from utils.discord_helpers import tags_to_edit_payload
        result = tags_to_edit_payload(tags, updates=updates)

        assert len(result) == 3
        # Updated tag
        updated_tag = next(t for t in result if t["id"] == 111)
        assert updated_tag["name"] == "updated-tag1"
        assert updated_tag["emoji"] == "👎"
        # Unchanged tag
        unchanged_tag = next(t for t in result if t["id"] == 222)
        assert unchanged_tag["name"] == "tag2"
        assert unchanged_tag["emoji"] == "📝"
        # New tag from updates
        new_tag = next(t for t in result if t["id"] == 333)
        assert new_tag["name"] == "new-tag"
        assert new_tag["emoji"] == "👑"


# ---------------------------------------------------------------------------
# handle_discord_exception — additional paths
# ---------------------------------------------------------------------------

class TestHandleDiscordExceptionAdditionalPaths:
    def test_handle_discord_exception_with_response_body_text(self):
        """handle_discord_exception should extract body text from response."""
        # Create an HTTPException with a response that has a non-callable text attribute
        exc = DiscordMockUtils.create_discord_not_found()
        # Attach a response with non-callable body text
        exc.response.text = "Error body text"

        from utils.discord_helpers import handle_discord_exception

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handle_discord_exception("test operation", exc))

        # Still results in 404 (NotFound takes precedence)
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    def test_handle_discord_exception_http_exc_with_none_status(self):
        """handle_discord_exception should return 502 for HTTPException with None status."""
        # Create an HTTPException where exc_status extraction returns None
        # Build a raw HTTPException with no status
        from tests.mocks.discord_mock_utils import _FakeHTTPResponse
        resp = _FakeHTTPResponse(200, "OK")  # status=200 means it's NOT 4xx
        # Manually create an HTTPException and override its .status to None
        import discord as _discord
        exc = _discord.HTTPException(resp, "some message")
        exc.status = None  # type: ignore

        from utils.discord_helpers import handle_discord_exception

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handle_discord_exception("test op", exc))

        assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY


# ---------------------------------------------------------------------------
# get_entity_or_404 — successful fetch path
# ---------------------------------------------------------------------------

class TestGetEntityOr404FetchSuccess:
    def test_get_entity_or_404_returns_fetched_entity_successfully(self):
        """get_entity_or_404 should return the fetched entity when cache miss."""
        mock_entity = MagicMock()
        mock_get_func = MagicMock(return_value=None)  # Cache miss
        mock_fetch_func = AsyncMock(return_value=mock_entity)

        from utils.discord_helpers import get_entity_or_404

        result = asyncio.run(get_entity_or_404(mock_get_func, mock_fetch_func, 123, "guild"))
        assert result == mock_entity


# ---------------------------------------------------------------------------
# validate_guild_channel_relationship — additional paths
# ---------------------------------------------------------------------------

class TestValidateGuildChannelRelationshipAdditionalPaths:
    def test_validate_guild_channel_relationship_passes_when_guild_matches(self):
        """Should not raise when channel.guild.id matches the expected guild_id."""
        mock_channel = DiscordMockUtils.create_mock_channel(channel_id=1, guild_id=999)
        # Ensure guild.id returns 999 (the expected guild_id)
        mock_channel.guild.id = 999

        from utils.discord_helpers import validate_guild_channel_relationship
        # Should not raise
        validate_guild_channel_relationship(mock_channel, 999)

    def test_validate_guild_channel_relationship_channel_without_guild_attr(self):
        """Should silently pass when channel has no 'guild' attribute at all."""
        # A channel object with no guild attribute at all
        class SimpleChannel:
            id = 42
        ch = SimpleChannel()

        from utils.discord_helpers import validate_guild_channel_relationship
        # hasattr(channel, "guild") == False → no exception
        validate_guild_channel_relationship(ch, 987654321)


# ---------------------------------------------------------------------------
# validate_channel_type — trace path (valid type)
# ---------------------------------------------------------------------------

class TestValidateChannelTypeTracePath:
    def test_validate_channel_type_does_not_raise_for_valid_type(self):
        """validate_channel_type should not raise when channel type is in expected_types."""
        mock_channel = DiscordMockUtils.create_mock_channel(channel_id=1, channel_type="voice")

        from utils.discord_helpers import validate_channel_type
        validate_channel_type(mock_channel, ["text", "voice"], 1)  # Must not raise


# ---------------------------------------------------------------------------
# normalize_emoji — edge cases
# ---------------------------------------------------------------------------

class TestNormalizeEmojiEdgeCases:
    def test_normalize_emoji_returns_empty_string_for_whitespace_only(self):
        """normalize_emoji should return stripped string for whitespace-only input."""
        from utils.discord_helpers import normalize_emoji
        result = normalize_emoji("   ")
        assert result == ""

    def test_normalize_emoji_handles_separated_hex_codepoints(self):
        """normalize_emoji should decode multi-part hex codepoints (dash separator)."""
        from utils.discord_helpers import normalize_emoji
        result = normalize_emoji("1f1fa-1f1f8")  # US flag
        assert len(result) > 0

    def test_normalize_emoji_handles_underscore_separated_hex(self):
        """normalize_emoji should decode underscore-separated hex codepoints."""
        from utils.discord_helpers import normalize_emoji
        result = normalize_emoji("1f1fa_1f1f8")  # US flag with underscore separator
        assert len(result) > 0

    def test_normalize_emoji_returns_val_when_split_fails(self):
        """normalize_emoji should return original when hex parsing fails."""
        from utils.discord_helpers import normalize_emoji
        # An invalid hex string that can't be parsed
        result = normalize_emoji("zzzzzz")
        # Not a valid hex, so returned unchanged
        assert result == "zzzzzz"


# ---------------------------------------------------------------------------
# tag_to_dict — additional paths for dict-type tags
# ---------------------------------------------------------------------------

class TestTagToDictAdditionalPaths:
    def test_tag_to_dict_mapping_without_channel_id_uses_channel_key(self):
        """tag_to_dict should extract channel_id from 'channel' key when channel_id=None."""
        tag = {
            "id": 42,
            "channel_id": 100,
            "name": "tag-name",
        }

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(tag, channel_id=None)

        assert result["id"] == 42
        assert result["channel_id"] == 100
        assert result["name"] == "tag-name"

    def test_tag_to_dict_mapping_with_nested_emoji_mapping(self):
        """tag_to_dict should extract emoji from nested mapping under 'emoji' key."""
        tag = {
            "id": 1,
            "name": "test",
            "emoji": {"name": "😀"},
        }

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(tag, channel_id=1)
        # The nested mapping provides emoji name "😀"
        assert result["emoji"] == "😀"

    def test_tag_to_dict_mapping_emoji_from_raw_emoji_key(self):
        """tag_to_dict should extract emoji from 'raw_emoji' key."""
        tag = {
            "id": 1,
            "name": "test",
            "raw_emoji": "👍",
        }

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(tag, channel_id=1)
        assert result["emoji"] == "👍"

    def test_tag_to_dict_non_mapping_without_channel_id_uses_channel_attr(self):
        """tag_to_dict should use tag.channel.id when channel_id=None and channel_id attr missing."""
        class TagWithChannel:
            id = 99
            name = "my-tag"
            emoji = "🏷️"
            channel_id = None

            class channel:
                id = 200

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(TagWithChannel(), channel_id=None)

        assert result["id"] == 99
        assert result["channel_id"] == 200
        assert result["name"] == "my-tag"

    def test_tag_to_dict_non_mapping_with_to_dict_method_used_for_emoji(self):
        """tag_to_dict should call to_dict() for emoji when direct emoji attr is None."""
        class TagWithToDict:
            id = 55
            name = "tag55"
            emoji = None  # Force to_dict path

            def to_dict(self):
                return {"emoji": "📌"}

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(TagWithToDict(), channel_id=1)
        assert result["emoji"] == "📌"

    def test_tag_to_dict_emoji_from_dict_dict_attribute(self):
        """tag_to_dict should inspect __dict__ for emoji-named fields."""
        class TagWithDictAttr:
            id = 77
            name = "t77"
            emoji = None

            def __init__(self):
                self.emoji_value = "⭐"  # 'emoji' in key name

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(TagWithDictAttr(), channel_id=1)
        # __dict__ will have 'emoji_value' which contains 'emoji' in the key name
        assert result["emoji"] == "⭐"


# ---------------------------------------------------------------------------
# tags_to_edit_payload — additional edge cases
# ---------------------------------------------------------------------------

class TestTagsToEditPayloadAdditionalPaths:
    def test_tags_to_edit_payload_tag_without_id_not_added_to_seen(self):
        """tags_to_edit_payload should handle tags with no id."""
        class TagNoId:
            id = None
            name = "no-id-tag"
            emoji = "🎯"

        tags = [TagNoId()]
        from utils.discord_helpers import tags_to_edit_payload
        result = tags_to_edit_payload(tags)
        assert len(result) == 1
        assert result[0]["name"] == "no-id-tag"
        # No "id" key since id is None
        assert "id" not in result[0]

    def test_tags_to_edit_payload_non_integer_update_key(self):
        """tags_to_edit_payload should handle non-integer update keys."""
        tags = []
        updates = {
            "new-tag": {"name": "New Tag", "emoji": "🆕"}
        }
        from utils.discord_helpers import tags_to_edit_payload
        result = tags_to_edit_payload(tags, updates=updates)
        assert len(result) == 1
        assert result[0]["name"] == "New Tag"
        # key "new-tag" can't be cast to int, so id stays as "new-tag"
        assert result[0]["id"] == "new-tag"


# ---------------------------------------------------------------------------
# handle_discord_exception — response inner exception path
# ---------------------------------------------------------------------------

class TestHandleDiscordExceptionResponseInnerException:
    def test_handle_discord_exception_with_response_body_attribute_raises_internally(self):
        """handle_discord_exception handles internal exception while extracting response body."""
        exc = DiscordMockUtils.create_discord_not_found()
        # Give the exception a response-like attribute that raises on text access
        class BadResponse:
            status = 404
            @property
            def text(self):
                raise RuntimeError("can't read text")
        exc.response = BadResponse()

        from utils.discord_helpers import handle_discord_exception
        # The outer exception handler still maps correctly (NotFound → 404)
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handle_discord_exception("test op", exc))
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    def test_handle_discord_exception_outer_attribute_extraction_exception(self):
        """handle_discord_exception handles outer-level attribute extraction exception."""
        # Build an exception where accessing .status raises
        class WeirdExc(Exception):
            @property
            def status(self):
                raise TypeError("boom")
        exc = WeirdExc("weird")

        from utils.discord_helpers import handle_discord_exception
        # Falls through to generic 500 handler
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handle_discord_exception("test op", exc))
        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


# ---------------------------------------------------------------------------
# normalize_emoji — exception paths for invalid hex
# ---------------------------------------------------------------------------

class TestNormalizeEmojiExceptionPaths:
    def test_normalize_emoji_returns_val_on_invalid_separated_hex(self):
        """normalize_emoji should return original val when hex decoding raises."""
        from utils.discord_helpers import normalize_emoji
        # "g" is not valid hex, so split-decode will fail on individual parts
        # Use something that LOOKS hex-separated but has invalid chars
        result = normalize_emoji("GGGGGG-FFFFFF")
        # Can't be decoded, returns original
        assert result == "GGGGGG-FFFFFF"


# ---------------------------------------------------------------------------
# tag_to_dict — exception paths deep in dict path
# ---------------------------------------------------------------------------

class TestTagToDictExceptionPaths:
    def test_tag_to_dict_mapping_id_non_castable_becomes_none(self):
        """tag_to_dict should set id=None when id value can't be cast to int."""
        tag = {
            "id": "not-an-int",
            "name": "test",
        }
        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(tag, channel_id=1)
        assert result["id"] is None
        assert result["name"] == "test"

    def test_tag_to_dict_mapping_channel_id_non_castable_becomes_none(self):
        """tag_to_dict should set channel_id=None when channel_id value can't be cast."""
        tag = {
            "id": 1,
            "channel_id": "not-int",
            "name": "test",
        }
        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(tag, channel_id=None)
        assert result["channel_id"] is None

    def test_tag_to_dict_non_mapping_id_non_castable_becomes_none(self):
        """tag_to_dict should set id=None when non-mapping tag.id can't be cast."""
        class BadIdTag:
            id = "not-int"
            name = "test"
            emoji = None

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(BadIdTag(), channel_id=1)
        assert result["id"] is None

    def test_tag_to_dict_non_mapping_channel_id_non_castable(self):
        """tag_to_dict should set channel_id=None when channel_id attr can't be cast."""
        class BadChannelIdTag:
            id = 1
            name = "test"
            emoji = None
            channel_id = "bad"

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(BadChannelIdTag(), channel_id=None)
        assert result["channel_id"] is None

    def test_tag_to_dict_non_mapping_emoji_as_mapping_type(self):
        """tag_to_dict should extract emoji name from emoji as a Mapping."""
        class TagWithMappingEmoji:
            id = 1
            name = "test"
            emoji = {"emoji": "🌟", "name": "star"}

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(TagWithMappingEmoji(), channel_id=1)
        assert result["emoji"] == "🌟"

    def test_tag_to_dict_non_mapping_emoji_fallback_via_str_tag_non_ascii(self):
        """tag_to_dict should detect emoji from str(tag) containing non-ascii chars."""
        class EmojiInStrTag:
            id = 1
            name = "test"
            emoji = None
            raw_emoji = None
            unicode = None
            emoji_str = None
            emoji_name = None

            def __str__(self):
                return "🎉 tag"

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(EmojiInStrTag(), channel_id=1)
        # The emoji candidate is "🎉 tag", which normalize_emoji will return as-is
        assert result["emoji"] is not None

    def test_tag_to_dict_non_mapping_alternate_emoji_attrs(self):
        """tag_to_dict should find emoji from alternate attribute names."""
        class TagWithRawEmoji:
            id = 1
            name = "test"
            emoji = None
            raw_emoji = "📌"

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(TagWithRawEmoji(), channel_id=1)
        assert result["emoji"] == "📌"

    def test_tag_to_dict_emoji_normalize_failure_fallback(self):
        """tag_to_dict should fall back to str() when normalize_emoji fails."""
        class TagWithBadEmoji:
            id = 1
            name = "test"

            class BadEmoji:
                def __str__(self):
                    return "😄"
            emoji = BadEmoji()

        from utils.discord_helpers import tag_to_dict
        # The emoji candidate is a BadEmoji instance (not a mock, not a Mapping)
        result = tag_to_dict(TagWithBadEmoji(), channel_id=1)
        # str(BadEmoji()) = "😄" → normalize_emoji("😄") → "😄"
        assert result["emoji"] == "😄"

    def test_tag_to_dict_mapping_with_non_castable_channel_id_via_channel_id_param(self):
        """tag_to_dict should set channel_id=None when passed channel_id can't be cast to int."""
        tag = {"id": 1, "name": "test"}

        from utils.discord_helpers import tag_to_dict
        # Pass a non-castable channel_id to trigger lines 371-372
        result = tag_to_dict(tag, channel_id="not-an-int")
        assert result["channel_id"] is None

    def test_tag_to_dict_mapping_returns_dict_with_all_fields(self):
        """tag_to_dict should return all expected fields from a dict tag."""
        tag = {"id": 42, "channel_id": 10, "name": "my-tag", "emoji": "🎈"}

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(tag, channel_id=10)
        assert result["id"] == 42
        assert result["channel_id"] == 10
        assert result["name"] == "my-tag"
        assert result["emoji"] == "🎈"

    def test_tag_to_dict_non_mapping_non_castable_channel_id_in_channel_id_param(self):
        """tag_to_dict non-mapping path: channel_id param non-castable → cid=None."""
        class SimpleTag:
            id = 5
            name = "t"
            emoji = "⭐"

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(SimpleTag(), channel_id="bad-value")
        assert result["channel_id"] is None
        assert result["id"] == 5

    def test_tag_to_dict_non_mapping_to_dict_raises_gracefully(self):
        """tag_to_dict should handle to_dict() raising an exception gracefully."""
        class TagWithBadToDict:
            id = 10
            name = "test"
            emoji = None

            def to_dict(self):
                raise RuntimeError("to_dict failed")

        from utils.discord_helpers import tag_to_dict
        result = tag_to_dict(TagWithBadToDict(), channel_id=1)
        # to_dict raised → emoji_candidate remains None
        assert result["id"] == 10
        assert result["emoji"] is None

    def test_tag_to_dict_non_mapping_dict_attr_emoji_key_exception(self):
        """tag_to_dict handles exception during __dict__ inspection."""
        # Create a class where iterating __dict__ raises
        class TagWithBadDict:
            id = 20
            name = "test"
            emoji = None

            @property
            def __dict__(self):
                raise RuntimeError("bad dict")

        from utils.discord_helpers import tag_to_dict
        # Should fall through gracefully, emoji stays None
        result = tag_to_dict(TagWithBadDict(), channel_id=1)
        assert result["id"] == 20

    def test_tag_to_dict_non_mapping_emoji_normalize_exception_uses_str_fallback(self):
        """tag_to_dict should use str(emoji) when normalize_emoji raises."""

        class TagWithEmojiStr:
            id = 30
            name = "test"
            emoji = "👍"

        from utils.discord_helpers import tag_to_dict
        # Patch normalize_emoji to raise to trigger the exception fallback path
        with patch("utils.discord_helpers.normalize_emoji", side_effect=RuntimeError("norm failed")):
            result = tag_to_dict(TagWithEmojiStr(), channel_id=1)
        # Falls back to str(emoji_candidate)
        assert result["emoji"] == "👍"
