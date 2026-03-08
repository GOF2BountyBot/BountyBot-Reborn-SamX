"""Tests for permission utility functions."""
import pytest
from utils.permission_utils import (
    PERMISSION_FLAGS,
    check_permission,
    check_permissions,
    has_administrator,
    calculate_effective_permissions,
    permissions_to_dict,
    get_permission_names_by_value,
    combine_permissions,
    get_all_permissions,
)


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
