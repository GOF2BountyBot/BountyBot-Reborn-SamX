import pytest
from api.schemas.user_schemas import Member, MemberListResponse, MemberResponse, MemberUpdateRequest, User, UserResponse
from pydantic import ValidationError


class TestUser:
    def test_valid_instantiation(self):
        u = User(id=1, username="u", discriminator="0001", created_at="2020-01-01T00:00:00Z")
        assert u.bot is False and u.system is False and u.public_flags == 0

    def test_missing_required(self):
        with pytest.raises(ValidationError):
            User(username="u", discriminator="0001", created_at="2020-01-01T00:00:00Z")


class TestMember:
    def test_valid_instantiation(self):
        u = User(id=1, username="u", discriminator="0001", created_at="2020-01-01T00:00:00Z")
        m = Member(user=u, guild_id=10, permissions=5)
        assert m.roles == [] and m.deaf is False and m.mute is False

    def test_missing_required(self):
        with pytest.raises(ValidationError):
            Member(guild_id=1, permissions=5)


class TestMemberUpdateRequest:
    def test_optional_fields(self):
        req = MemberUpdateRequest()
        assert req.nick is None and req.roles is None and req.mute is None and req.deaf is None


class TestResponseModels:
    def test_user_response(self):
        u = User(id=1, username="u", discriminator="0001", created_at="2020-01-01T00:00:00Z")
        resp = UserResponse(status="ok", data=u)
        assert resp.data.id == 1

    def test_member_response_and_list(self):
        u = User(id=1, username="u", discriminator="0001", created_at="2020-01-01T00:00:00Z")
        m = Member(user=u, guild_id=1, permissions=1)
        r = MemberResponse(status="ok", data=m)
        assert r.data.guild_id == 1
        lr = MemberListResponse(status="ok", data=[m])
        assert len(lr.data) == 1
