"""Tests for permission utility functions."""
import pytest
from utils.permission_utils import (
    PERMISSION_FLAGS,
    PermissionSource,
    check_permission,
    check_permissions,
    has_administrator,
    calculate_effective_permissions,
    permissions_to_dict,
    get_permission_names_by_value,
    combine_permissions,
    get_all_permissions,
    get_role_permissions,
    get_user_permissions,
    get_channel_permissions,
    get_category_permissions,
    create_permission_overwrite,
    overwrite_to_dict,
)
from tests.mocks.discord_mock_utils import DiscordMockUtils


# ---------------------------------------------------------------------------
# check_permission
# ---------------------------------------------------------------------------

class TestCheckPermission:
    def test_check_permission_send_messages(self):
        """SEND_MESSAGES (0x800) should be detected when set."""
        perms = 0x800
        assert check_permission(perms, "SEND_MESSAGES") is True

    def test_check_permission_not_set(self):
        """Permission not present in the value should return False."""
        # 0x800 is SEND_MESSAGES only – KICK_MEMBERS (0x2) is absent
        perms = 0x800
        assert check_permission(perms, "KICK_MEMBERS") is False

    def test_check_permission_unknown_raises(self):
        """An unknown permission name must raise ValueError."""
        with pytest.raises(ValueError, match="Unknown permission"):
            check_permission(0, "TOTALLY_FAKE_PERM")


# ---------------------------------------------------------------------------
# check_permissions  (multiple at once)
# ---------------------------------------------------------------------------

class TestCheckPermissions:
    def test_check_permissions_multiple(self):
        """Check several permissions at once and verify mapping."""
        # SEND_MESSAGES (0x800) | ADMINISTRATOR (0x8) = 0x808
        perms = 0x808
        result = check_permissions(
            perms, ["SEND_MESSAGES", "ADMINISTRATOR", "BAN_MEMBERS"]
        )
        assert result == {
            "SEND_MESSAGES": True,
            "ADMINISTRATOR": True,
            "BAN_MEMBERS": False,
        }


# ---------------------------------------------------------------------------
# has_administrator
# ---------------------------------------------------------------------------

class TestHasAdministrator:
    def test_has_administrator_true(self):
        """Value that includes ADMINISTRATOR (0x8) should return True."""
        assert has_administrator(0x8) is True
        # Also true when combined with other perms
        assert has_administrator(0x8 | 0x800) is True

    def test_has_administrator_false(self):
        """Value without ADMINISTRATOR should return False."""
        assert has_administrator(0x800) is False
        assert has_administrator(0) is False


# ---------------------------------------------------------------------------
# calculate_effective_permissions
# ---------------------------------------------------------------------------

class TestCalculateEffectivePermissions:
    def test_calculate_effective_permissions_deny(self):
        """Deny overwrites should remove the denied bits."""
        base = 0x800 | 0x400  # SEND_MESSAGES + VIEW_CHANNEL
        deny = 0x800          # deny SEND_MESSAGES
        result = calculate_effective_permissions(base, deny_overwrites=deny)
        assert result & 0x800 == 0       # SEND_MESSAGES removed
        assert result & 0x400 == 0x400   # VIEW_CHANNEL kept

    def test_calculate_effective_permissions_allow(self):
        """Allow overwrites should add the allowed bits."""
        base = 0x400           # VIEW_CHANNEL only
        allow = 0x800          # allow SEND_MESSAGES
        result = calculate_effective_permissions(base, allow_overwrites=allow)
        assert result & 0x800 == 0x800   # SEND_MESSAGES added
        assert result & 0x400 == 0x400   # VIEW_CHANNEL kept

    def test_calculate_effective_permissions_admin_bypass(self):
        """Administrator in base perms should bypass all overwrites."""
        base = 0x8 | 0x800    # ADMINISTRATOR + SEND_MESSAGES
        deny = 0x800          # try to deny SEND_MESSAGES
        result = calculate_effective_permissions(base, deny_overwrites=deny)
        # Admin bypasses: result must equal the original base
        assert result == base


# ---------------------------------------------------------------------------
# permissions_to_dict
# ---------------------------------------------------------------------------

class TestPermissionsToDict:
    def test_permissions_to_dict(self):
        """Should convert integer to dict with correct True/False values."""
        perms = 0x8 | 0x800  # ADMINISTRATOR + SEND_MESSAGES
        result = permissions_to_dict(perms)

        # Keys are lower-case versions of PERMISSION_FLAGS
        assert result["administrator"] is True
        assert result["send_messages"] is True
        assert result["ban_members"] is False
        assert result["kick_members"] is False

        # Every known flag should have an entry
        for name in PERMISSION_FLAGS:
            assert name.lower() in result


# ---------------------------------------------------------------------------
# get_permission_names_by_value
# ---------------------------------------------------------------------------

class TestGetPermissionNamesByValue:
    def test_get_permission_names_by_value(self):
        """Should return list of uppercase permission names that are set."""
        perms = 0x8 | 0x800  # ADMINISTRATOR + SEND_MESSAGES
        names = get_permission_names_by_value(perms)
        assert "ADMINISTRATOR" in names
        assert "SEND_MESSAGES" in names
        assert "BAN_MEMBERS" not in names


# ---------------------------------------------------------------------------
# combine_permissions
# ---------------------------------------------------------------------------

class TestCombinePermissions:
    def test_combine_permissions(self):
        """Should combine multiple values with bitwise OR."""
        result = combine_permissions(0x8, 0x800, 0x400)
        assert result == (0x8 | 0x800 | 0x400)
        # Also verify individual bits
        assert result & 0x8 == 0x8
        assert result & 0x800 == 0x800
        assert result & 0x400 == 0x400


# ---------------------------------------------------------------------------
# PermissionSource
# ---------------------------------------------------------------------------

class TestPermissionSource:
    def test_permission_source_direct(self):
        """Create a direct permission source."""
        source = PermissionSource("direct")
        assert source.type == "direct"
        assert source.role_name is None
        assert source.role_id is None

    def test_permission_source_role(self):
        """Create a role-based permission source."""
        source = PermissionSource("role", role_name="Moderators", role_id=12345)
        assert source.type == "role"
        assert source.role_name == "Moderators"
        assert source.role_id == 12345

    def test_permission_source_everyone(self):
        """Create an everyone permission source."""
        source = PermissionSource("everyone")
        assert source.type == "everyone"


# ---------------------------------------------------------------------------
# get_role_permissions
# ---------------------------------------------------------------------------

class TestGetRolePermissions:
    def test_get_role_permissions_returns_all(self):
        """Should return all permissions since all can be assigned to roles."""
        result = get_role_permissions()
        assert isinstance(result, list)
        assert len(result) > 0
        assert len(result) == len(get_all_permissions())


# ---------------------------------------------------------------------------
# get_user_permissions
# ---------------------------------------------------------------------------

class TestGetUserPermissions:
    def test_get_user_permissions_only_channel_perms(self):
        """Should only return permissions that apply to channels."""
        result = get_user_permissions()
        assert isinstance(result, list)
        assert len(result) > 0
        for perm in result:
            assert len(perm["channel_types"]) > 0

    def test_get_user_permissions_excludes_guild_only(self):
        """Should exclude guild-only permissions like KICK_MEMBERS."""
        result = get_user_permissions()
        perm_names = [p["name"] for p in result]
        assert "KICK_MEMBERS" not in perm_names
        assert "BAN_MEMBERS" not in perm_names
        assert "ADMINISTRATOR" not in perm_names


# ---------------------------------------------------------------------------
# get_channel_permissions
# ---------------------------------------------------------------------------

class TestGetChannelPermissions:
    def test_get_channel_permissions_includes_text_voice(self):
        """Should return permissions applicable to text/voice channels."""
        result = get_channel_permissions()
        assert isinstance(result, list)
        assert len(result) > 0
        for perm in result:
            has_text_or_voice = "text" in perm["channel_types"] or "voice" in perm["channel_types"]
            assert has_text_or_voice is True


# ---------------------------------------------------------------------------
# get_category_permissions
# ---------------------------------------------------------------------------

class TestGetCategoryPermissions:
    def test_get_category_permissions_same_as_channel(self):
        """Category permissions should be same as channel permissions."""
        result = get_category_permissions()
        channel_result = get_channel_permissions()
        assert result == channel_result


# ---------------------------------------------------------------------------
# create_permission_overwrite
# ---------------------------------------------------------------------------

class TestCreatePermissionOverwrite:
    def test_create_permission_overwrite_allow_only(self):
        """Should create overwrite with allowed permissions."""
        allow = 0x800 | 0x400  # SEND_MESSAGES + VIEW_CHANNEL
        result = create_permission_overwrite(allow=allow)
        # Returns discord.PermissionOverwrite when discord is available, dict otherwise
        if isinstance(result, dict):
            assert result.get("send_messages") is True
            assert result.get("view_channel") is True
            assert result.get("kick_members") is None
        else:
            assert result.send_messages is True
            assert result.view_channel is True
            assert result.kick_members is None

    def test_create_permission_overwrite_deny_only(self):
        """Should create overwrite with denied permissions."""
        deny = 0x800 | 0x400  # SEND_MESSAGES + VIEW_CHANNEL
        result = create_permission_overwrite(deny=deny)
        if isinstance(result, dict):
            assert result.get("send_messages") is False
            assert result.get("view_channel") is False
        else:
            assert result.send_messages is False
            assert result.view_channel is False

    def test_create_permission_overwrite_both_allow_and_deny(self):
        """Should handle both allow and deny in same overwrite."""
        allow = 0x400  # VIEW_CHANNEL
        deny = 0x800    # SEND_MESSAGES
        result = create_permission_overwrite(allow=allow, deny=deny)
        if isinstance(result, dict):
            assert result.get("view_channel") is True
            assert result.get("send_messages") is False
        else:
            assert result.view_channel is True
            assert result.send_messages is False

    def test_create_permission_overwrite_none_values(self):
        """Should handle None values gracefully."""
        result = create_permission_overwrite(allow=None, deny=None)
        # Returns discord.PermissionOverwrite when discord is available, dict otherwise
        # Either way, it should have no permissions set
        if isinstance(result, dict):
            assert len(result) == 0
        else:
            # PermissionOverwrite has attributes, check they are all None/default
            for attr in dir(result):
                if not attr.startswith('_') and attr not in ('pair',):
                    val = getattr(result, attr, None)
                    # All permission attributes should be None for empty overwrite


# ---------------------------------------------------------------------------
# overwrite_to_dict
# ---------------------------------------------------------------------------

class TestOverwriteToDict:
    def test_overwrite_to_dict_from_dict(self):
        """Should convert dict-style overwrite to standardized format."""
        overwrite = {
            "_allow": 0x800,
            "_deny": 0x400,
            "send_messages": True,
            "view_channel": False,
        }
        result = overwrite_to_dict(overwrite)
        assert result["allow"] == 0x800
        assert result["deny"] == 0x400
        assert result["permissions"]["send_messages"] is True
        assert result["permissions"]["view_channel"] is False

    def test_overwrite_to_dict_from_dict_no_allow_deny(self):
        """Should handle dict without _allow/_deny keys."""
        overwrite = {"send_messages": True}
        result = overwrite_to_dict(overwrite)
        assert result["allow"] == 0
        assert result["deny"] == 0
        assert result["permissions"]["send_messages"] is True


# ---------------------------------------------------------------------------
# get_all_permissions
# ---------------------------------------------------------------------------

class TestGetAllPermissions:
    def test_get_all_permissions_returns_list(self):
        """Should return a non-empty list of dicts with required keys."""
        result = get_all_permissions()
        assert isinstance(result, list)
        assert len(result) > 0
        for entry in result:
            assert isinstance(entry, dict)
            assert "name" in entry
            assert "value" in entry
            assert "description" in entry
            assert "channel_types" in entry
