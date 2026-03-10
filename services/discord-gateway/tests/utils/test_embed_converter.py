"""
Tests for embed_converter.py utilities.

This module provides comprehensive test coverage for the Embed conversion utilities,
including bidirectional conversion between JSON payloads and Discord embeds.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException
import sys
import os
import types
from datetime import datetime, timezone
from unittest.mock import PropertyMock

from tests.mocks.discord_mock_utils import DiscordMockUtils


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


_mock_discord = types.ModuleType("discord")

_MockCategoryChannel = type("CategoryChannel", (), {})
_MockTextChannel = type("TextChannel", (), {})
_MockVoiceChannel = type("VoiceChannel", (), {})
_MockForumChannel = type("ForumChannel", (), {})
_MockThread = type("Thread", (), {})
class _MockField:
    """Mock for embed fields."""
    def __init__(self, name, value, inline):
        self.name = name
        self.value = value
        self.inline = inline


class _MockFooter:
    """Mock for embed footer."""
    def __init__(self, text=None, icon_url=None):
        self.text = text
        self.icon_url = icon_url


class _MockMedia:
    """Mock for thumbnail/image."""
    def __init__(self, url=None):
        self.url = url


class _MockColor:
    """Mock for discord.Color that accepts integer values."""
    def __init__(self, value=0):
        if isinstance(value, int):
            self.value = value
        else:
            try:
                self.value = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"Invalid color value: {value}")
    
    def __int__(self):
        return self.value


class _MockEmbed:
    """Mock for discord.Embed with all necessary methods."""
    def __init__(self):
        self.title = None
        self.description = None
        self.color = None
        self.fields = []
        self.footer = None
        self.timestamp = None
        self.thumbnail = None
        self.image = None
    
    def add_field(self, name, value, inline=False):
        """Add a field to the embed."""
        self.fields.append(_MockField(name, value, inline))
    
    def set_footer(self, text=None, icon_url=None):
        """Set the footer."""
        self.footer = _MockFooter(text, icon_url)
    
    def set_thumbnail(self, url=None):
        """Set the thumbnail."""
        self.thumbnail = _MockMedia(url)
    
    def set_image(self, url=None):
        """Set the image."""
        self.image = _MockMedia(url)
_MockPermissionOverwrite = type("PermissionOverwrite", (), {})
_MockGuild = type("Guild", (), {})
_MockUser = type("User", (), {})
_MockMember = type("Member", (), {})
_MockRole = type("Role", (), {})
_MockMessage = type("Message", (), {})
_MockForbidden = type("Forbidden", (Exception,), {})
_MockNotFound = type("NotFound", (Exception,), {})
_MockHTTPException = type("HTTPException", (Exception,), {})

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
_mock_discord.Forbidden = _MockForbidden
_mock_discord.NotFound = _MockNotFound
_mock_discord.HTTPException = _MockHTTPException

_mock_discord_ext = types.ModuleType("discord.ext")
_MockBot = type("Bot", (), {})
_mock_discord_ext.commands = types.ModuleType("discord.ext.commands")
_mock_discord_ext.commands.Bot = _MockBot

_MockPermissions = type("Permissions", (), {"value": 0})
_mock_discord.Permissions = _MockPermissions

_mock_discord.Color = _MockColor


sys.modules["discord"] = _mock_discord
sys.modules["discord.ext"] = _mock_discord_ext
sys.modules["discord.ext.commands"] = _mock_discord_ext.commands


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestEmbedConverter:
    """Tests for EmbedConverter class."""

    @pytest.fixture
    def mock_embed_payload(self):
        """Create a mock EmbedPayload."""
        from api.schemas.message_schemas import EmbedPayload, EmbedField
        return EmbedPayload(
            title="Test Title",
            description="Test Description",
            color=0x00FF00,
            fields=[
                EmbedField(name="Field1", value="Value1", inline=True),
                EmbedField(name="Field2", value="Value2", inline=False)
            ],
            footer_text="Footer Text",
            footer_icon_url="footer_icon_url",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            thumbnail_url="thumbnail_url",
            image_url="image_url"
        )

    @pytest.fixture
    def mock_discord_embed(self):
        """Create a mock Discord embed."""
        # Create field mocks with explicit name attribute (not spec name)
        field1 = MagicMock()
        field1.name = "Field1"
        field1.value = "Value1"
        field1.inline = True

        field2 = MagicMock()
        field2.name = "Field2"
        field2.value = "Value2"
        field2.inline = False

        return DiscordMockUtils.create_mock_embed(
            title="Test Title",
            description="Test Description",
            color_value=0x00FF00,
            fields=[field1, field2],
            footer={"text": "Footer Text", "icon_url": "footer_icon_url"},
            timestamp=datetime(2024, 1, 1),
            thumbnail={"url": "thumbnail_url"},
            image={"url": "image_url"},
        )

    def test_coerce_to_embed_payload_returns_payload_unchanged(self, mock_embed_payload):
        """_coerce_to_embed_payload should return EmbedPayload unchanged."""
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter._coerce_to_embed_payload(mock_embed_payload)
        assert result == mock_embed_payload

    def test_coerce_to_embed_payload_converts_dict_to_payload(self, mock_embed_payload):
        """_coerce_to_embed_payload should convert dict to EmbedPayload."""
        from utils.embed_converter import EmbedConverter
        payload_dict = mock_embed_payload.model_dump()
        result = EmbedConverter._coerce_to_embed_payload(payload_dict)
        assert result.title == mock_embed_payload.title
        assert result.description == mock_embed_payload.description

    def test_coerce_to_embed_payload_converts_pydantic_model_to_payload(self, mock_embed_payload):
        """_coerce_to_embed_payload should convert pydantic model with model_dump() to payload."""
        class MockPydanticModel:
            def model_dump(self):
                return mock_embed_payload.model_dump()

        mock_model = MockPydanticModel()
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter._coerce_to_embed_payload(mock_model)
        assert result.title == mock_embed_payload.title
        assert result.description == mock_embed_payload.description

    def test_coerce_to_embed_payload_raises_on_unsupported_type(self):
        """_coerce_to_embed_payload should raise TypeError on unsupported type."""
        from utils.embed_converter import EmbedConverter
        with pytest.raises(TypeError):
            EmbedConverter._coerce_to_embed_payload(123)

    def test_payload_to_embed_converts_basic_payload(self, mock_embed_payload):
        """payload_to_embed should convert basic payload to Discord embed."""
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.payload_to_embed(mock_embed_payload)

        assert result.title == "Test Title"
        assert result.description == "Test Description"
        assert result.color.value == 0x00FF00
        assert len(result.fields) == 2
        assert result.fields[0].name == "Field1"
        assert result.fields[0].value == "Value1"
        assert result.fields[0].inline is True
        assert result.footer.text == "Footer Text"
        assert result.footer.icon_url == "footer_icon_url"
        assert result.timestamp == datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert result.thumbnail.url == "thumbnail_url"
        assert result.image.url == "image_url"

    def test_payload_to_embed_handles_invalid_color(self, mock_embed_payload):
        """payload_to_embed should handle invalid color values gracefully."""
        mock_embed_payload.color = "invalid_color"
        from utils.embed_converter import EmbedConverter
        
        with pytest.raises(Exception):
            EmbedConverter.payload_to_embed(mock_embed_payload)

    def test_payload_to_embed_handles_none_color(self, mock_embed_payload):
        """payload_to_embed should handle None color gracefully."""
        mock_embed_payload.color = None
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.payload_to_embed(mock_embed_payload)
        assert result.color is None

    def test_payload_to_embed_handles_none_fields(self, mock_embed_payload):
        """payload_to_embed should handle None fields gracefully."""
        mock_embed_payload.fields = None
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.payload_to_embed(mock_embed_payload)
        assert len(result.fields) == 0

    def test_payload_to_embed_handles_none_footer(self, mock_embed_payload):
        """payload_to_embed should handle None footer gracefully."""
        mock_embed_payload.footer_text = None
        mock_embed_payload.footer_icon_url = None
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.payload_to_embed(mock_embed_payload)
        assert not result.footer

    def test_payload_to_embed_handles_none_timestamp(self, mock_embed_payload):
        """payload_to_embed should handle None timestamp gracefully."""
        mock_embed_payload.timestamp = None
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.payload_to_embed(mock_embed_payload)
        assert result.timestamp is None

    def test_payload_to_embed_handles_none_thumbnail(self, mock_embed_payload):
        """payload_to_embed should handle None thumbnail gracefully."""
        mock_embed_payload.thumbnail_url = None
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.payload_to_embed(mock_embed_payload)
        assert not result.thumbnail

    def test_payload_to_embed_handles_none_image(self, mock_embed_payload):
        """payload_to_embed should handle None image gracefully."""
        mock_embed_payload.image_url = None
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.payload_to_embed(mock_embed_payload)
        assert not result.image

    def test_embed_to_payload_converts_basic_embed(self, mock_discord_embed):
        """embed_to_payload should convert basic Discord embed to payload."""
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.embed_to_payload(mock_discord_embed)

        assert result.title == "Test Title"
        assert result.description == "Test Description"
        assert result.color == 0x00FF00
        assert len(result.fields) == 2
        assert result.fields[0].name == "Field1"
        assert result.fields[0].value == "Value1"
        assert result.fields[0].inline is True
        assert result.footer_text == "Footer Text"
        assert result.footer_icon_url == "footer_icon_url"
        assert result.timestamp == datetime(2024, 1, 1)
        assert result.thumbnail_url == "thumbnail_url"
        assert result.image_url == "image_url"

    def test_embed_to_payload_handles_none_title(self, mock_discord_embed):
        """embed_to_payload should handle None title gracefully."""
        mock_discord_embed.title = None
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.embed_to_payload(mock_discord_embed)
        assert result.title is None

    def test_embed_to_payload_handles_none_description(self, mock_discord_embed):
        """embed_to_payload should handle None description gracefully."""
        mock_discord_embed.description = None
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.embed_to_payload(mock_discord_embed)
        assert result.description is None

    def test_embed_to_payload_handles_none_color(self, mock_discord_embed):
        """embed_to_payload should handle None color gracefully."""
        mock_discord_embed.color = None
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.embed_to_payload(mock_discord_embed)
        assert result.color is None

    def test_embed_to_payload_handles_empty_fields(self, mock_discord_embed):
        """embed_to_payload should handle empty fields gracefully."""
        mock_discord_embed.fields = []
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.embed_to_payload(mock_discord_embed)
        assert len(result.fields) == 0

    def test_embed_to_payload_handles_none_footer(self, mock_discord_embed):
        """embed_to_payload should handle None footer gracefully."""
        mock_discord_embed.footer = None
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.embed_to_payload(mock_discord_embed)
        assert result.footer_text is None
        assert result.footer_icon_url is None

    def test_embed_to_payload_handles_none_timestamp(self, mock_discord_embed):
        """embed_to_payload should handle None timestamp gracefully."""
        mock_discord_embed.timestamp = None
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.embed_to_payload(mock_discord_embed)
        assert result.timestamp is None

    def test_embed_to_payload_handles_none_thumbnail(self, mock_discord_embed):
        """embed_to_payload should handle None thumbnail gracefully."""
        mock_discord_embed.thumbnail = None
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.embed_to_payload(mock_discord_embed)
        assert result.thumbnail_url is None

    def test_embed_to_payload_handles_none_image(self, mock_discord_embed):
        """embed_to_payload should handle None image gracefully."""
        mock_discord_embed.image = None
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.embed_to_payload(mock_discord_embed)
        assert result.image_url is None

    def test_payload_to_grid_embed_injects_spacers(self, mock_embed_payload):
        """payload_to_grid_embed should inject spacers for grid layout."""
        # Add a third field so we have 3 fields with per_row=2
        # This should result in: field0, field1, spacer, field2
        from api.schemas.message_schemas import EmbedField
        mock_embed_payload.fields.append(EmbedField(name="Field3", value="Value3", inline=True))
        
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.payload_to_grid_embed(mock_embed_payload, fields_per_row=2)

        # Should have original 3 fields + 1 spacer (after the 2nd field)
        assert len(result.fields) == 4
        # Spacer should have zero-width characters and be at index 2
        assert result.fields[2].name == "\u200B"
        assert result.fields[2].value == "\u200B"
        assert result.fields[2].inline is True

    def test_payload_to_grid_embed_handles_few_fields(self, mock_embed_payload):
        """payload_to_grid_embed should handle few fields without unnecessary spacers."""
        mock_embed_payload.fields = [mock_embed_payload.fields[0]]
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.payload_to_grid_embed(mock_embed_payload, fields_per_row=2)
        assert len(result.fields) == 1  # No spacer needed

    def test_test_round_trip_consistency_with_valid_payload(self, mock_embed_payload):
        """test_round_trip_consistency should return True for valid payload."""
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.test_round_trip_consistency(mock_embed_payload)
        assert result is True

    def test_test_round_trip_consistency_with_invalid_payload(self):
        """test_round_trip_consistency should return False for invalid payload."""
        from utils.embed_converter import EmbedConverter
        # Use a completely invalid type that cannot be coerced to EmbedPayload
        invalid_payload = 12345
        result = EmbedConverter.test_round_trip_consistency(invalid_payload)
        assert result is False

    def test_test_round_trip_consistency_with_empty_payload(self):
        """test_round_trip_consistency should handle empty payload."""
        from api.schemas.message_schemas import EmbedPayload
        empty_payload = EmbedPayload()
        from utils.embed_converter import EmbedConverter
        result = EmbedConverter.test_round_trip_consistency(empty_payload)
        assert result is True  # Should handle empty payload gracefully