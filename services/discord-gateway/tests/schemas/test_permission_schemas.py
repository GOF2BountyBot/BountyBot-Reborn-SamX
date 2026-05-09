import pytest
from api.schemas.permission_schemas import PermissionFlag, PermissionOverwrite, PermissionOverwriteRequest
from pydantic import ValidationError


class TestPermissionOverwrite:
    def test_non_negative_defaults(self):
        o = PermissionOverwrite(target_id=1, type="role")
        assert o.allow == 0 and o.deny == 0 and o.id is None

    def test_negative_raises(self):
        with pytest.raises(ValidationError):
            PermissionOverwrite(target_id=1, type="role", allow=-1)
        with pytest.raises(ValidationError):
            PermissionOverwrite(target_id=1, type="role", deny=-1)

    def test_conflicting_bits(self):
        with pytest.raises(ValidationError):
            PermissionOverwrite(target_id=1, type="role", allow=1, deny=1)

    def test_id_auto_generate(self):
        o = PermissionOverwrite(channel_id=2, target_id=3, type="member")
        assert o.id == "2:3"


class TestPermissionFlag:
    def test_valid_instantiation(self):
        pf = PermissionFlag(name="SEND", value=1, description="desc")
        assert pf.channel_types == []

    def test_missing_required(self):
        with pytest.raises(ValidationError):
            PermissionFlag(value=1, description="desc", channel_types=[])


class TestPermissionOverwriteRequest:
    def test_optional_fields(self):
        por = PermissionOverwriteRequest()
        assert por.allow is None and por.deny is None

    def test_negative_raises(self):
        with pytest.raises(ValidationError):
            PermissionOverwriteRequest(allow=-1)
        with pytest.raises(ValidationError):
            PermissionOverwriteRequest(deny=-2)
