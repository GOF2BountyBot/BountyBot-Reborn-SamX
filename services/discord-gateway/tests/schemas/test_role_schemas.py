import pytest
from api.schemas.role_schemas import Role, RoleCreateRequest, RoleListResponse, RoleResponse, RoleUpdateRequest
from pydantic import ValidationError


class TestRole:
    def test_valid(self):
        r = Role(id=1, guild_id=2, name="n", color=3, hoist=True,
                 position=4, permissions=5, managed=False,
                 mentionable=False, created_at="2020-01-01T00:00:00Z")
        assert r.tags is None
    def test_missing_required(self):
        with pytest.raises(ValidationError):
            Role(guild_id=2, name="n", color=3, hoist=True,
                 position=4, permissions=5, managed=False,
                 mentionable=False, created_at="2020-01-01T00:00:00Z")

class TestRoleCreateRequest:
    def test_defaults(self):
        req = RoleCreateRequest()
        assert isinstance(req.name, str) and req.color == 0 and req.hoist is False
    def test_optional_properties(self):
        req = RoleCreateRequest(name="x", permissions=1,
                                 color=2, hoist=True,
                                 position=3, mentionable=True)
        assert req.name == "x" and req.permissions == 1 and req.mentionable is True

class TestRoleUpdateRequest:
    def test_optional(self):
        req = RoleUpdateRequest()
        assert req.name is None and req.permissions is None

class TestRoleResponseList:
    def test_role_response_and_list(self):
        r = Role(id=1, guild_id=2, name="n", color=3, hoist=False,
                 position=1, permissions=1, managed=False,
                 mentionable=False, created_at="2020-01-01T00:00:00Z")
        resp = RoleResponse(status="ok", data=r)
        assert resp.data.id == 1
        lr = RoleListResponse(status="ok", data=[r], total_count=1)
        assert len(lr.data) == 1 and lr.total_count == 1
