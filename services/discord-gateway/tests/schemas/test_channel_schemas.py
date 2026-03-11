"""Tests for channel Pydantic schemas."""
import pytest
from api.schemas.channel_schemas import (
    ChannelCreateRequest,
    ChannelResponse,
    ChannelUpdateRequest,
)
from pydantic import ValidationError


class TestChannelCreateRequest:
    def test_valid_instantiation(self):
        req = ChannelCreateRequest(name="general")
        assert req.name == "general"
        assert req.type == "text"

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            ChannelCreateRequest()  # missing name

class TestChannelUpdateRequest:
    def test_valid_instantiation_with_optional_fields(self):
        req = ChannelUpdateRequest(name="general", nsfw=True, position=1)
        assert req.name == "general"
        assert req.nsfw is True
        assert req.position == 1

    def test_invalid_field_types_raise(self):
        with pytest.raises(ValidationError):
            ChannelUpdateRequest(position="first")  # invalid type

class TestChannelResponse:
    def test_serialization(self):
        channel_data = {
            "id": 1,
            "name": "general",
            "type": "text",
            "position": 0,
            "created_at": "2026-03-09T12:00:00Z"
        }
        resp = ChannelResponse(status="ok", data=channel_data)
        result = resp.model_dump()
        assert result["status"] == "ok"
        assert result["data"]["id"] == 1
        assert result["data"]["name"] == "general"
