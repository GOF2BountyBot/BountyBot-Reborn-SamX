"""Tests for guild Pydantic schemas."""
import pytest
from api.schemas.guild_schemas import Guild, GuildListResponse, GuildResponse, GuildSummary
from pydantic import ValidationError


class TestGuild:
    def test_valid_instantiation(self):
        data = {
            "id": 1,
            "name": "Test Guild",
            "owner_id": 2,
            "created_at": "2026-03-09T12:00:00Z",
            "verification_level": "low",
            "default_notifications": "all_messages",
            "explicit_content_filter": "no_filter",
            "mfa_level": "none",
            "premium_tier": 1,
            "preferred_locale": "en-US"
        }
        guild = Guild(**data)
        assert guild.id == 1
        assert guild.name == "Test Guild"
        assert guild.features == []

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            Guild()

class TestGuildResponse:
    def test_instantiation_with_guild(self):
        guild_data = {
            "id": 1,
            "name": "Test Guild",
            "owner_id": 2,
            "created_at": "2026-03-09T12:00:00Z",
            "verification_level": "low",
            "default_notifications": "all_messages",
            "explicit_content_filter": "no_filter",
            "mfa_level": "none",
            "premium_tier": 1,
            "preferred_locale": "en-US"
        }
        resp = GuildResponse(status="ok", data=guild_data)
        assert resp.status == "ok"
        assert isinstance(resp.data, Guild)

class TestGuildListResponse:
    def test_default_pagination_fields(self):
        resp = GuildListResponse(status="ok", data=[])
        assert resp.total_count is None
        assert resp.page is None
        assert resp.page_size is None
        assert resp.has_more is None

    def test_data_list_validation(self):
        guild_data = {
            "id": 1,
            "name": "Test Guild",
            "owner_id": 2,
            "created_at": "2026-03-09T12:00:00Z",
            "verification_level": "low",
            "default_notifications": "all_messages",
            "explicit_content_filter": "no_filter",
            "mfa_level": "none",
            "premium_tier": 1,
            "preferred_locale": "en-US"
        }
        resp = GuildListResponse(status="ok", data=[guild_data])
        assert len(resp.data) == 1
        assert isinstance(resp.data[0], Guild)

class TestGuildSummary:
    def test_valid_instantiation(self):
        summary = GuildSummary(id=1, name="Summary Guild", owner_id=3)
        assert summary.id == 1
        assert summary.name == "Summary Guild"
        assert summary.owner_id == 3

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            GuildSummary()
