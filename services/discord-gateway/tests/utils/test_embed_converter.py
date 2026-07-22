"""
Tests for embed_converter.py utilities.

This module provides comprehensive test coverage for the Embed conversion utilities,
including bidirectional conversion between JSON payloads and Discord embeds.

Fidelity note
-------------
These tests use the **real** ``discord.Embed`` / ``discord.Color`` (discord.py is
fully constructible without a live client).  The former hand-rolled ``_MockEmbed`` /
``_MockColor`` fakes accepted anything and validated nothing — a regression that
overflowed an embed field (>256-char name, >1024-char value, >25 fields) passed
against the fake yet 400s against real discord.py.  Building against the real types
means ``payload_to_embed`` / ``embed_to_payload`` assertions validate discord.py's
genuine behaviour.  The only remaining hand-rolled object is ``_RawEmbed`` — a small
mutable stand-in used solely to drive the converter's *defensive* branches (e.g.
``fields is None`` or an attribute whose access raises), scenarios a real
``discord.Embed`` cannot represent.
"""

import os
import sys
import types
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Force the REAL discord package into sys.modules before importing it, so the
# module-level ``discord`` name binds to real discord.py regardless of any
# import-time fake another test module may have installed (conftest saved the
# genuine references before any fake swap).
# ---------------------------------------------------------------------------
_cm = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
sys.modules["discord"] = _cm._REAL_DISCORD
sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS

import discord

# shared.bblogger is not importable in the test environment; provide a stub.
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


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class _RawEmbed:
    """Minimal mutable embed-like object for the converter's *defensive* paths.

    Deliberately NOT a discord.Embed replacement — it exists only so tests can
    place an object whose attribute access raises, or whose ``fields`` is
    ``None``, in front of ``embed_to_payload`` to exercise the swallow/guard
    branches.  A real ``discord.Embed`` cannot represent those states
    (``.fields`` is never ``None``; ``.footer`` etc. are read-only proxies).
    """

    def __init__(self):
        self.title = None
        self.description = None
        self.color = None
        self.fields = []
        self.footer = None
        self.timestamp = None
        self.thumbnail = None
        self.image = None


@pytest.fixture(autouse=True)
def _embed_converter_uses_real_discord():
    """
    Re-assert real discord into sys.modules and reload utils.embed_converter
    before each test so its module-level ``discord`` reference (used for
    ``discord.Embed()`` / ``discord.Color()``) is the genuine package, even if a
    sibling test module reloaded embed_converter against a fake discord earlier
    in the session.  The root conftest's ``_restore_source_modules`` autouse
    fixture snapshots and restores the module cache around every test, so no
    explicit teardown is needed here.
    """
    import importlib

    sys.modules["discord"] = _cm._REAL_DISCORD
    sys.modules["discord.ext"] = _cm._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _cm._REAL_DISCORD_EXT_COMMANDS

    import utils.embed_converter as _ec_mod

    importlib.reload(_ec_mod)
    yield


def _make_full_discord_embed() -> discord.Embed:
    """Build a fully-populated real ``discord.Embed`` for embed_to_payload tests."""
    embed = discord.Embed(
        title="Test Title",
        description="Test Description",
        color=discord.Color(0x00FF00),
    )
    embed.timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    embed.add_field(name="Field1", value="Value1", inline=True)
    embed.add_field(name="Field2", value="Value2", inline=False)
    embed.set_footer(text="Footer Text", icon_url="footer_icon_url")
    embed.set_thumbnail(url="thumbnail_url")
    embed.set_image(url="image_url")
    return embed


class TestEmbedConverter:
    """Tests for EmbedConverter class."""

    @pytest.fixture
    def mock_embed_payload(self):
        """Create a real EmbedPayload."""
        from api.schemas.message_schemas import EmbedField, EmbedPayload

        return EmbedPayload(
            title="Test Title",
            description="Test Description",
            color=0x00FF00,
            fields=[
                EmbedField(name="Field1", value="Value1", inline=True),
                EmbedField(name="Field2", value="Value2", inline=False),
            ],
            footer_text="Footer Text",
            footer_icon_url="footer_icon_url",
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            thumbnail_url="thumbnail_url",
            image_url="image_url",
        )

    @pytest.fixture
    def mock_discord_embed(self):
        """Create a real, fully-populated discord.Embed."""
        return _make_full_discord_embed()

    def test_coerce_to_embed_payload_returns_payload_unchanged(self, mock_embed_payload):
        """_coerce_to_embed_payload should return EmbedPayload unchanged."""
        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter._coerce_to_embed_payload(mock_embed_payload)
        assert result == mock_embed_payload

    def test_coerce_to_embed_payload_converts_dict_to_payload(self, mock_embed_payload):
        """_coerce_to_embed_payload should convert dict to EmbedPayload."""
        import utils.embed_converter as _ec_mod

        payload_dict = mock_embed_payload.model_dump()
        result = _ec_mod.EmbedConverter._coerce_to_embed_payload(payload_dict)
        assert result.title == mock_embed_payload.title
        assert result.description == mock_embed_payload.description

    def test_coerce_to_embed_payload_converts_pydantic_model_to_payload(self, mock_embed_payload):
        """_coerce_to_embed_payload should convert pydantic model with model_dump() to payload."""

        class MockPydanticModel:
            def model_dump(self):
                return mock_embed_payload.model_dump()

        mock_model = MockPydanticModel()
        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter._coerce_to_embed_payload(mock_model)
        assert result.title == mock_embed_payload.title
        assert result.description == mock_embed_payload.description

    def test_coerce_to_embed_payload_raises_on_unsupported_type(self):
        """_coerce_to_embed_payload should raise TypeError on unsupported type."""
        import utils.embed_converter as _ec_mod

        with pytest.raises(TypeError):
            _ec_mod.EmbedConverter._coerce_to_embed_payload(123)

    def test_payload_to_embed_converts_basic_payload(self, mock_embed_payload):
        """payload_to_embed should convert basic payload to a real Discord embed."""
        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter.payload_to_embed(mock_embed_payload)

        assert isinstance(result, discord.Embed)
        assert result.title == "Test Title"
        assert result.description == "Test Description"
        assert result.color.value == 0x00FF00
        assert len(result.fields) == 2
        assert result.fields[0].name == "Field1"
        assert result.fields[0].value == "Value1"
        assert result.fields[0].inline is True
        assert result.footer.text == "Footer Text"
        assert result.footer.icon_url == "footer_icon_url"
        assert result.timestamp == datetime(2024, 1, 1, tzinfo=UTC)
        assert result.thumbnail.url == "thumbnail_url"
        assert result.image.url == "image_url"

    def test_payload_to_embed_handles_invalid_color(self, mock_embed_payload):
        """payload_to_embed should raise on invalid color values (real discord.Color rejects them)."""
        mock_embed_payload.color = "invalid_color"
        import utils.embed_converter as _ec_mod

        with pytest.raises(Exception):
            _ec_mod.EmbedConverter.payload_to_embed(mock_embed_payload)

    def test_payload_to_embed_handles_none_color(self, mock_embed_payload):
        """payload_to_embed should handle None color gracefully."""
        mock_embed_payload.color = None
        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter.payload_to_embed(mock_embed_payload)
        assert result.color is None

    def test_payload_to_embed_handles_none_fields(self, mock_embed_payload):
        """payload_to_embed should handle None fields gracefully."""
        mock_embed_payload.fields = None
        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter.payload_to_embed(mock_embed_payload)
        assert len(result.fields) == 0

    def test_payload_to_embed_handles_none_footer(self, mock_embed_payload):
        """payload_to_embed should handle None footer gracefully."""
        mock_embed_payload.footer_text = None
        mock_embed_payload.footer_icon_url = None
        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter.payload_to_embed(mock_embed_payload)
        assert not result.footer

    def test_payload_to_embed_handles_none_timestamp(self, mock_embed_payload):
        """payload_to_embed should handle None timestamp gracefully."""
        mock_embed_payload.timestamp = None
        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter.payload_to_embed(mock_embed_payload)
        assert result.timestamp is None

    def test_payload_to_embed_handles_none_thumbnail(self, mock_embed_payload):
        """payload_to_embed should handle None thumbnail gracefully."""
        mock_embed_payload.thumbnail_url = None
        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter.payload_to_embed(mock_embed_payload)
        assert not result.thumbnail

    def test_payload_to_embed_handles_none_image(self, mock_embed_payload):
        """payload_to_embed should handle None image gracefully."""
        mock_embed_payload.image_url = None
        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter.payload_to_embed(mock_embed_payload)
        assert not result.image

    def test_embed_to_payload_converts_basic_embed(self, mock_discord_embed):
        """embed_to_payload should convert a real Discord embed to a payload."""
        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter.embed_to_payload(mock_discord_embed)

        assert result.title == "Test Title"
        assert result.description == "Test Description"
        assert result.color == 0x00FF00
        assert len(result.fields) == 2
        assert result.fields[0].name == "Field1"
        assert result.fields[0].value == "Value1"
        assert result.fields[0].inline is True
        assert result.footer_text == "Footer Text"
        assert result.footer_icon_url == "footer_icon_url"
        assert result.timestamp == datetime(2024, 1, 1, tzinfo=UTC)
        assert result.thumbnail_url == "thumbnail_url"
        assert result.image_url == "image_url"

    def test_embed_to_payload_handles_none_title(self):
        """embed_to_payload should handle an embed with no title."""
        import utils.embed_converter as _ec_mod

        embed = discord.Embed(description="Test Description")
        result = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert result.title is None

    def test_embed_to_payload_handles_none_description(self):
        """embed_to_payload should handle an embed with no description."""
        import utils.embed_converter as _ec_mod

        embed = discord.Embed(title="Test Title")
        result = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert result.description is None

    def test_embed_to_payload_handles_none_color(self):
        """embed_to_payload should handle an embed with no color."""
        import utils.embed_converter as _ec_mod

        embed = discord.Embed(title="Test Title")
        assert embed.color is None
        result = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert result.color is None

    def test_embed_to_payload_handles_empty_fields(self):
        """embed_to_payload should handle an embed with no fields."""
        import utils.embed_converter as _ec_mod

        embed = discord.Embed(title="Test Title")
        result = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert len(result.fields) == 0

    def test_embed_to_payload_handles_none_footer(self):
        """embed_to_payload should handle an embed with no footer."""
        import utils.embed_converter as _ec_mod

        embed = discord.Embed(title="Test Title")
        result = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert result.footer_text is None
        assert result.footer_icon_url is None

    def test_embed_to_payload_handles_none_timestamp(self):
        """embed_to_payload should handle an embed with no timestamp."""
        import utils.embed_converter as _ec_mod

        embed = discord.Embed(title="Test Title")
        result = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert result.timestamp is None

    def test_embed_to_payload_handles_none_thumbnail(self):
        """embed_to_payload should handle an embed with no thumbnail."""
        import utils.embed_converter as _ec_mod

        embed = discord.Embed(title="Test Title")
        result = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert result.thumbnail_url is None

    def test_embed_to_payload_handles_none_image(self):
        """embed_to_payload should handle an embed with no image."""
        import utils.embed_converter as _ec_mod

        embed = discord.Embed(title="Test Title")
        result = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert result.image_url is None

    def test_payload_to_grid_embed_injects_spacers(self, mock_embed_payload):
        """payload_to_grid_embed should inject spacers for grid layout."""
        # Add a third field so we have 3 fields with per_row=2
        # This should result in: field0, field1, spacer, field2
        from api.schemas.message_schemas import EmbedField

        mock_embed_payload.fields.append(EmbedField(name="Field3", value="Value3", inline=True))

        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter.payload_to_grid_embed(mock_embed_payload, fields_per_row=2)

        # Should have original 3 fields + 1 spacer (after the 2nd field)
        assert len(result.fields) == 4
        # Spacer should have zero-width characters and be at index 2
        assert result.fields[2].name == "​"
        assert result.fields[2].value == "​"
        assert result.fields[2].inline is True

    def test_payload_to_grid_embed_handles_few_fields(self, mock_embed_payload):
        """payload_to_grid_embed should handle few fields without unnecessary spacers."""
        mock_embed_payload.fields = [mock_embed_payload.fields[0]]
        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter.payload_to_grid_embed(mock_embed_payload, fields_per_row=2)
        assert len(result.fields) == 1  # No spacer needed

    def test_test_round_trip_consistency_with_valid_payload(self, mock_embed_payload):
        """test_round_trip_consistency should return True for valid payload."""
        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter.test_round_trip_consistency(mock_embed_payload)
        assert result is True

    def test_test_round_trip_consistency_with_invalid_payload(self):
        """test_round_trip_consistency should return False for invalid payload."""
        import utils.embed_converter as _ec_mod

        # Use a completely invalid type that cannot be coerced to EmbedPayload
        invalid_payload = 12345
        result = _ec_mod.EmbedConverter.test_round_trip_consistency(invalid_payload)
        assert result is False

    def test_test_round_trip_consistency_with_empty_payload(self):
        """test_round_trip_consistency should handle empty payload."""
        from api.schemas.message_schemas import EmbedPayload

        empty_payload = EmbedPayload()
        import utils.embed_converter as _ec_mod

        result = _ec_mod.EmbedConverter.test_round_trip_consistency(empty_payload)
        assert result is True  # Should handle empty payload gracefully

    def test_payload_to_embed_with_thumbnail_and_image(self, mock_embed_payload):
        """Test that thumbnail and image fields are properly converted."""
        import utils.embed_converter as _ec_mod

        mock_embed_payload.thumbnail_url = "https://example.com/thumb.png"
        mock_embed_payload.image_url = "https://example.com/image.png"
        embed = _ec_mod.EmbedConverter.payload_to_embed(mock_embed_payload)
        assert embed.thumbnail.url == "https://example.com/thumb.png"
        assert embed.image.url == "https://example.com/image.png"

    def test_embed_to_payload_with_multiple_fields(self):
        """Test that multiple fields are properly extracted from a real embed."""
        import utils.embed_converter as _ec_mod

        embed = discord.Embed(title="Multi")
        embed.add_field(name="Field 1", value="Value 1", inline=True)
        embed.add_field(name="Field 2", value="Value 2", inline=False)
        payload = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert len(payload.fields) == 2
        assert payload.fields[0].name == "Field 1"
        assert payload.fields[1].name == "Field 2"

    def test_coerce_to_embed_payload_with_none_input(self):
        """Test that None input raises appropriate error."""
        import utils.embed_converter as _ec_mod

        with pytest.raises(Exception):
            _ec_mod.EmbedConverter._coerce_to_embed_payload(None)

    def test_payload_to_embed_with_all_fields(self, mock_embed_payload):
        """Test payload_to_embed with all fields populated."""
        import utils.embed_converter as _ec_mod

        # Set various fields
        mock_embed_payload.title = "Full Embed"
        mock_embed_payload.description = "Complete embed with all fields"
        mock_embed_payload.color = 0xFF0000
        mock_embed_payload.footer_text = "Footer"
        mock_embed_payload.footer_icon_url = "https://example.com/footer.png"

        embed = _ec_mod.EmbedConverter.payload_to_embed(mock_embed_payload)
        assert embed.title == "Full Embed"
        assert embed.description == "Complete embed with all fields"

    def test_embed_to_payload_defensive_access(self):
        """Test that embed_to_payload safely handles fields=None (defensive branch).

        A real discord.Embed can never have ``fields is None``; this exercises the
        converter's ``getattr(embed, "fields", []) or []`` guard using a raw stand-in.
        """
        import utils.embed_converter as _ec_mod

        embed = _RawEmbed()
        embed.fields = None
        payload = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert payload.fields == []

    def test_payload_to_grid_embed_with_fields(self, mock_embed_payload):
        """Test that payload_to_grid_embed properly formats embed for grid layout."""
        # Add some fields to the payload
        import utils.embed_converter as _ec_mod
        from api.schemas.message_schemas import EmbedField

        field1 = EmbedField(name="Field 1", value="Value 1", inline=True)
        field2 = EmbedField(name="Field 2", value="Value 2", inline=True)
        mock_embed_payload.fields = [field1, field2]
        mock_embed_payload.title = "Grid Embed"

        grid_embed = _ec_mod.EmbedConverter.payload_to_grid_embed(mock_embed_payload, fields_per_row=2)
        assert grid_embed is not None

    # ------------------------------------------------------------------
    # Tests covering previously-missing lines
    # ------------------------------------------------------------------

    # Lines 52-54: _coerce_to_embed_payload — model_dump() path raises
    def test_coerce_to_embed_payload_model_dump_raises_reraises(self):
        """When model_dump() raises, _coerce_to_embed_payload should log and re-raise (lines 52-54)."""
        import utils.embed_converter as _ec_mod

        class BadModelDump:
            def model_dump(self):
                raise ValueError("model_dump exploded")

        with pytest.raises(ValueError, match="model_dump exploded"):
            _ec_mod.EmbedConverter._coerce_to_embed_payload(BadModelDump())

    # Lines 58-62: _coerce_to_embed_payload — .dict() path (happy path)
    def test_coerce_to_embed_payload_uses_dict_method_on_pydantic_v1_model(self, mock_embed_payload):
        """_coerce_to_embed_payload should call .dict() on pydantic v1-style objects (lines 58-62)."""
        import utils.embed_converter as _ec_mod

        class PydanticV1Style:
            """Mimics a pydantic-v1 model (has .dict() but NOT .model_dump())."""

            def dict(self):
                return mock_embed_payload.model_dump()

            # deliberately no model_dump attribute

        obj = PydanticV1Style()
        result = _ec_mod.EmbedConverter._coerce_to_embed_payload(obj)
        assert result.title == mock_embed_payload.title
        assert result.description == mock_embed_payload.description

    # Lines 58-62: _coerce_to_embed_payload — .dict() path raises
    def test_coerce_to_embed_payload_dict_method_raises_reraises(self):
        """When .dict() raises, _coerce_to_embed_payload should log and re-raise (lines 60-62)."""
        import utils.embed_converter as _ec_mod

        class BadDictMethod:
            def dict(self):
                raise RuntimeError("dict() exploded")

        with pytest.raises(RuntimeError, match="dict\\(\\) exploded"):
            _ec_mod.EmbedConverter._coerce_to_embed_payload(BadDictMethod())

    # Lines 70-74: _coerce_to_embed_payload — iterable mapping succeeds as dict but
    # EmbedPayload validation fails → re-raises from inner try/except
    def test_coerce_to_embed_payload_iterable_mapping_invalid_fields_reraises(self):
        """dict(payload) succeeds but EmbedPayload(**dict) fails → lines 70-74 are executed."""
        import utils.embed_converter as _ec_mod

        # An iterable of key-value pairs that becomes a valid dict but contains
        # fields that fail EmbedPayload validation (e.g. extra unknown required field
        # that triggers a pydantic error).
        # The simplest way: pass an object that is convertible via dict() but
        # whose resulting dict causes a validation error in EmbedPayload.
        class IterableMapping:
            """Supports dict() via __iter__ returning (k, v) pairs."""

            def __iter__(self):
                # Return an invalid field type that will fail EmbedPayload validation
                return iter([("fields", "not-a-list")])

        with pytest.raises(Exception):
            _ec_mod.EmbedConverter._coerce_to_embed_payload(IterableMapping())

    # Line 134-135: payload_to_embed — non-datetime timestamp raises TypeError
    def test_payload_to_embed_non_datetime_timestamp_raises_type_error(self, mock_embed_payload):
        """payload_to_embed should raise TypeError when timestamp is not a datetime (lines 134-135)."""
        import utils.embed_converter as _ec_mod

        mock_embed_payload.timestamp = "2024-01-01T00:00:00Z"  # string, not datetime
        with pytest.raises(TypeError, match="timestamp must be a datetime instance"):
            _ec_mod.EmbedConverter.payload_to_embed(mock_embed_payload)

    # Lines 209-211: embed_to_payload — color.value is None → fallback to int(embed.color)
    def test_embed_to_payload_color_fallback_to_int_when_value_is_none(self):
        """embed_to_payload should fall back to int(embed.color) when .value is None (lines 209-211)."""
        import utils.embed_converter as _ec_mod

        class ColorWithNoValue:
            """Simulates a color object where .value returns None but int() works."""

            value = None

            def __int__(self):
                return 0xFF0000

        embed = _RawEmbed()
        embed.color = ColorWithNoValue()
        result = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert result.color == 0xFF0000

    # Lines 209-211 (except branch): embed_to_payload — int(embed.color) also raises
    def test_embed_to_payload_color_fallback_int_raises_sets_none(self):
        """embed_to_payload should set color=None when both .value and int() fail (line 211)."""
        import utils.embed_converter as _ec_mod

        class BadColor:
            value = None

            def __int__(self):
                raise ValueError("cannot convert")

        embed = _RawEmbed()
        embed.color = BadColor()
        result = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert result.color is None

    # Lines 235-238: embed_to_payload — footer access raises → swallowed
    def test_embed_to_payload_footer_access_raises_is_swallowed(self):
        """embed_to_payload should swallow exceptions when accessing footer (lines 235-238)."""
        import utils.embed_converter as _ec_mod

        class ExplodingFooter:
            """Accessing any attribute raises."""

            def __getattr__(self, item):
                raise RuntimeError("footer exploded")

        embed = _RawEmbed()
        # Make embed.footer a truthy object so the `if` branch is entered,
        # but then accessing footer.text raises.
        object.__setattr__(embed, "footer", ExplodingFooter())
        result = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert result.footer_text is None
        assert result.footer_icon_url is None

    # Lines 246-247: embed_to_payload — thumbnail access raises → swallowed
    def test_embed_to_payload_thumbnail_access_raises_is_swallowed(self):
        """embed_to_payload should swallow exceptions when accessing thumbnail (lines 246-247)."""
        import utils.embed_converter as _ec_mod

        class ExplodingThumbnail:
            @property
            def url(self):
                raise RuntimeError("thumbnail exploded")

        embed = _RawEmbed()
        embed.thumbnail = ExplodingThumbnail()
        result = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert result.thumbnail_url is None

    # Lines 253-254: embed_to_payload — image access raises → swallowed
    def test_embed_to_payload_image_access_raises_is_swallowed(self):
        """embed_to_payload should swallow exceptions when accessing image (lines 253-254)."""
        import utils.embed_converter as _ec_mod

        class ExplodingImage:
            @property
            def url(self):
                raise RuntimeError("image exploded")

        embed = _RawEmbed()
        embed.image = ExplodingImage()
        result = _ec_mod.EmbedConverter.embed_to_payload(embed)
        assert result.image_url is None
