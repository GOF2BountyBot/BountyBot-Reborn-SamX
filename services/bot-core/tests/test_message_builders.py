"""
Unit tests for bot-core message builder modules:
  - message_builders.base  (abstract base, tested via concrete subclass)
  - message_builders.factory
  - message_builders.builders.time_announcement

IMPORTANT: shared.bblogger must be mocked BEFORE importing any source modules.
"""

import json
import os
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock shared / shared.bblogger BEFORE importing any source modules.
# conftest.py already does this at collection time; we repeat here for
# standalone execution safety.
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")

    def _make_mock_logger(name: str = "test") -> MagicMock:
        logger = MagicMock()
        for method in ("info", "debug", "warning", "error", "trace", "critical"):
            setattr(logger, method, MagicMock())
        return logger

    _mock_bblogger.get_logger = _make_mock_logger
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# ---------------------------------------------------------------------------
# Ensure the src directory is on the path.
# ---------------------------------------------------------------------------
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


# ===========================================================================
# Tests for message_builders.base (MessagePayloadBuilder ABC)
# ===========================================================================


class TestMessagePayloadBuilderABC:
    """Verify the ABC contract via a minimal concrete implementation."""

    def _make_concrete(self, **overrides):
        """Return a minimal concrete subclass with all abstract methods implemented."""
        from message_builders.base import MessagePayloadBuilder

        defaults = {
            "build_payload": lambda self, data: data,
            "extract_data": lambda self, payload: None,
            "get_message_type": lambda self: "test_type",
            "validate_input": lambda self, data: True,
        }
        defaults.update(overrides)

        return type("ConcreteBuilder", (MessagePayloadBuilder,), defaults)()

    def test_concrete_subclass_instantiates(self):
        builder = self._make_concrete()
        assert builder is not None

    def test_abstract_class_cannot_be_instantiated_directly(self):
        from message_builders.base import MessagePayloadBuilder

        with pytest.raises(TypeError):
            MessagePayloadBuilder()  # type: ignore[abstract]

    def test_missing_build_payload_raises_type_error(self):
        from message_builders.base import MessagePayloadBuilder

        with pytest.raises(TypeError):

            class Incomplete(MessagePayloadBuilder):
                def extract_data(self, payload: str) -> dict[str, Any] | None:
                    return None

                def get_message_type(self) -> str:
                    return "x"

                def validate_input(self, data: dict[str, Any]) -> bool:
                    return True

            Incomplete()

    def test_delegate_methods_work_on_concrete(self):
        builder = self._make_concrete()
        assert builder.get_message_type() == "test_type"
        assert builder.validate_input({"key": "val"}) is True
        assert builder.build_payload({"a": 1}) == {"a": 1}
        assert builder.extract_data("{}") is None


# ===========================================================================
# Tests for message_builders.builders.time_announcement.TimeAnnouncementBuilder
# ===========================================================================


class TestTimeAnnouncementBuilderGetMessageType:
    """Tests for TimeAnnouncementBuilder.get_message_type."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.time_announcement import TimeAnnouncementBuilder

        return TimeAnnouncementBuilder()

    def test_returns_correct_type_string(self, builder):
        assert builder.get_message_type() == "time_announcement"


class TestTimeAnnouncementBuilderValidateInput:
    """Tests for TimeAnnouncementBuilder.validate_input."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.time_announcement import TimeAnnouncementBuilder

        return TimeAnnouncementBuilder()

    def test_valid_with_current_time_string(self, builder):
        assert builder.validate_input({"current_time": "2026-01-01T00:00:00Z"}) is True

    def test_invalid_missing_current_time(self, builder):
        assert builder.validate_input({}) is False

    def test_invalid_current_time_not_string(self, builder):
        assert builder.validate_input({"current_time": 12345}) is False

    def test_invalid_current_time_none(self, builder):
        assert builder.validate_input({"current_time": None}) is False

    def test_valid_with_extra_fields(self, builder):
        assert builder.validate_input({"current_time": "now", "extra": "ignored"}) is True

    def test_invalid_empty_string_current_time(self, builder):
        """Empty string is technically a string, so validate_input returns True."""
        assert builder.validate_input({"current_time": ""}) is True


class TestTimeAnnouncementBuilderBuildPayload:
    """Tests for TimeAnnouncementBuilder.build_payload."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.time_announcement import TimeAnnouncementBuilder

        return TimeAnnouncementBuilder()

    def test_builds_payload_with_required_keys(self, builder):
        data = {"current_time": "2026-01-01T12:00:00Z"}
        payload = builder.build_payload(data)

        assert "title" in payload
        assert "description" in payload
        assert "color" in payload
        assert "footer_text" in payload
        assert "timestamp" in payload

    def test_title_is_clock_string(self, builder):
        payload = builder.build_payload({"current_time": "2026-01-01T12:00:00Z"})
        assert "🕒" in payload["title"]
        assert "Time" in payload["title"]

    def test_description_contains_current_time(self, builder):
        time_str = "2026-03-11T08:30:00Z"
        payload = builder.build_payload({"current_time": time_str})
        assert time_str in payload["description"]
        assert "**Current time:**" in payload["description"]

    def test_color_is_blue_hex(self, builder):
        payload = builder.build_payload({"current_time": "now"})
        assert payload["color"] == 0x3498DB

    def test_footer_text(self, builder):
        payload = builder.build_payload({"current_time": "now"})
        assert payload["footer_text"] == "Time Announcement"

    def test_timestamp_is_iso_string(self, builder):
        payload = builder.build_payload({"current_time": "now"})
        # Should be a valid ISO-like string
        assert isinstance(payload["timestamp"], str)
        assert "T" in payload["timestamp"]

    def test_raises_on_invalid_input(self, builder):
        with pytest.raises(ValueError, match="Invalid input data"):
            builder.build_payload({})

    def test_raises_when_current_time_not_string(self, builder):
        with pytest.raises(ValueError, match="Invalid input data"):
            builder.build_payload({"current_time": 999})

    def test_raises_on_empty_dict(self, builder):
        with pytest.raises(ValueError):
            builder.build_payload({})


class TestTimeAnnouncementBuilderExtractData:
    """Tests for TimeAnnouncementBuilder.extract_data."""

    @pytest.fixture()
    def builder(self):
        from message_builders.builders.time_announcement import TimeAnnouncementBuilder

        return TimeAnnouncementBuilder()

    def _make_payload_str(self, time_str: str) -> str:
        return json.dumps({"description": f"**Current time:** {time_str}"})

    def test_extracts_time_from_valid_payload(self, builder):
        time_str = "2026-01-01T12:00:00Z"
        result = builder.extract_data(self._make_payload_str(time_str))
        assert result is not None
        assert result["current_time"] == time_str

    def test_returns_none_for_invalid_json(self, builder):
        result = builder.extract_data("{not valid json")
        assert result is None

    def test_returns_none_when_marker_absent(self, builder):
        payload = json.dumps({"description": "Some unrelated description"})
        result = builder.extract_data(payload)
        assert result is None

    def test_returns_none_for_empty_json_object(self, builder):
        result = builder.extract_data("{}")
        assert result is None

    def test_returns_none_for_empty_string(self, builder):
        result = builder.extract_data("")
        assert result is None

    def test_extracts_time_with_whitespace(self, builder):
        """Time string may contain spaces (e.g. human-readable)."""
        time_str = "March 11, 2026 at 08:30 UTC"
        result = builder.extract_data(self._make_payload_str(time_str))
        assert result is not None
        assert result["current_time"] == time_str

    def test_roundtrip_build_then_extract(self, builder):
        """build_payload → json.dumps → extract_data should return the original time."""
        time_str = "2026-06-15T10:45:00+00:00"
        payload_dict = builder.build_payload({"current_time": time_str})
        extracted = builder.extract_data(json.dumps(payload_dict))
        assert extracted is not None
        assert extracted["current_time"] == time_str


# ===========================================================================
# Tests for message_builders.factory.MessageBuilderFactory
# ===========================================================================


class TestMessageBuilderFactoryCreateBuilder:
    """Tests for MessageBuilderFactory.create_builder."""

    def test_creates_time_announcement_builder(self):
        from message_builders.builders.time_announcement import TimeAnnouncementBuilder
        from message_builders.factory import MessageBuilderFactory

        builder = MessageBuilderFactory.create_builder("time_announcement")
        assert isinstance(builder, TimeAnnouncementBuilder)

    def test_raises_for_unknown_type(self):
        from message_builders.factory import MessageBuilderFactory

        with pytest.raises(ValueError, match="Unknown message type"):
            MessageBuilderFactory.create_builder("does_not_exist")

    def test_raises_for_empty_string_type(self):
        from message_builders.factory import MessageBuilderFactory

        with pytest.raises(ValueError, match="Unknown message type"):
            MessageBuilderFactory.create_builder("")

    def test_returns_new_instance_each_call(self):
        from message_builders.factory import MessageBuilderFactory

        b1 = MessageBuilderFactory.create_builder("time_announcement")
        b2 = MessageBuilderFactory.create_builder("time_announcement")
        assert b1 is not b2


class TestMessageBuilderFactoryRegisterBuilder:
    """Tests for MessageBuilderFactory.register_builder."""

    def test_registers_new_builder_type(self):
        from message_builders.base import MessagePayloadBuilder
        from message_builders.factory import MessageBuilderFactory

        class DummyBuilder(MessagePayloadBuilder):
            def build_payload(self, data):
                return {}

            def extract_data(self, payload):
                return None

            def get_message_type(self):
                return "dummy"

            def validate_input(self, data):
                return True

        MessageBuilderFactory.register_builder("dummy", DummyBuilder)
        builder = MessageBuilderFactory.create_builder("dummy")
        assert isinstance(builder, DummyBuilder)

    def test_overrides_existing_builder(self):
        """Registering under an existing key replaces it."""
        from message_builders.base import MessagePayloadBuilder
        from message_builders.factory import MessageBuilderFactory

        class AlternativeBuilder(MessagePayloadBuilder):
            def build_payload(self, data):
                return {"alt": True}

            def extract_data(self, payload):
                return None

            def get_message_type(self):
                return "time_announcement"

            def validate_input(self, data):
                return True

        original_builders = dict(MessageBuilderFactory._builders)
        try:
            MessageBuilderFactory.register_builder("time_announcement", AlternativeBuilder)
            builder = MessageBuilderFactory.create_builder("time_announcement")
            assert isinstance(builder, AlternativeBuilder)
        finally:
            # Restore the original registration so other tests aren't affected
            MessageBuilderFactory._builders = original_builders

    def test_registered_type_appears_in_supported_types(self):
        from message_builders.base import MessagePayloadBuilder
        from message_builders.factory import MessageBuilderFactory

        class SomeBuilder(MessagePayloadBuilder):
            def build_payload(self, data):
                return {}

            def extract_data(self, payload):
                return None

            def get_message_type(self):
                return "some_type"

            def validate_input(self, data):
                return True

        original_builders = dict(MessageBuilderFactory._builders)
        try:
            MessageBuilderFactory.register_builder("some_type", SomeBuilder)
            assert "some_type" in MessageBuilderFactory.get_supported_types()
        finally:
            MessageBuilderFactory._builders = original_builders


class TestMessageBuilderFactoryGetSupportedTypes:
    """Tests for MessageBuilderFactory.get_supported_types."""

    def test_includes_time_announcement(self):
        from message_builders.factory import MessageBuilderFactory

        types_list = MessageBuilderFactory.get_supported_types()
        assert "time_announcement" in types_list

    def test_returns_list(self):
        from message_builders.factory import MessageBuilderFactory

        result = MessageBuilderFactory.get_supported_types()
        assert isinstance(result, list)

    def test_returns_non_empty_list(self):
        from message_builders.factory import MessageBuilderFactory

        result = MessageBuilderFactory.get_supported_types()
        assert len(result) >= 1

    def test_each_type_can_be_created(self):
        """Every supported type should be creatable without raising."""
        from message_builders.factory import MessageBuilderFactory

        for msg_type in MessageBuilderFactory.get_supported_types():
            builder = MessageBuilderFactory.create_builder(msg_type)
            assert builder is not None


# ===========================================================================
# Integration-style tests: factory + builder working together
# ===========================================================================


class TestFactoryBuilderIntegration:
    """End-to-end tests combining the factory with the time_announcement builder."""

    def test_factory_builder_validate_and_build(self):
        from message_builders.factory import MessageBuilderFactory

        builder = MessageBuilderFactory.create_builder("time_announcement")
        data = {"current_time": "2026-03-11T09:00:00Z"}

        assert builder.validate_input(data) is True

        payload = builder.build_payload(data)
        assert payload["description"].endswith("2026-03-11T09:00:00Z")

    def test_factory_builder_extract_roundtrip(self):
        from message_builders.factory import MessageBuilderFactory

        builder = MessageBuilderFactory.create_builder("time_announcement")
        time_str = "2026-12-31T23:59:59Z"

        payload = builder.build_payload({"current_time": time_str})
        extracted = builder.extract_data(json.dumps(payload))

        assert extracted is not None
        assert extracted["current_time"] == time_str

    def test_factory_builder_invalid_input_raises(self):
        from message_builders.factory import MessageBuilderFactory

        builder = MessageBuilderFactory.create_builder("time_announcement")
        with pytest.raises(ValueError):
            builder.build_payload({"wrong_key": "value"})
