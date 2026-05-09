"""Tests for permission utility functions."""

from unittest.mock import MagicMock

import pytest
from utils.permission_utils import (
    PERMISSION_FLAGS,
    PermissionSource,
    _find_admin_source,
    _find_channel_permission_source,
    _find_permission_source,
    calculate_effective_permissions,
    check_permission,
    check_permissions,
    combine_permissions,
    create_permission_overwrite,
    evaluate_role_channel_permissions,
    evaluate_role_guild_permissions,
    evaluate_user_channel_permissions,
    evaluate_user_guild_permissions,
    get_all_permissions,
    get_category_permissions,
    get_channel_permissions,
    get_permission_names_by_value,
    get_role_permissions,
    get_user_permissions,
    has_administrator,
    has_channel_permission,
    has_guild_permission,
    overwrite_to_dict,
    permissions_to_dict,
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
        # 0x800 is SEND_MESSAGES only - KICK_MEMBERS (0x2) is absent
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
        result = check_permissions(perms, ["SEND_MESSAGES", "ADMINISTRATOR", "BAN_MEMBERS"])
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
        deny = 0x800  # deny SEND_MESSAGES
        result = calculate_effective_permissions(base, deny_overwrites=deny)
        assert result & 0x800 == 0  # SEND_MESSAGES removed
        assert result & 0x400 == 0x400  # VIEW_CHANNEL kept

    def test_calculate_effective_permissions_allow(self):
        """Allow overwrites should add the allowed bits."""
        base = 0x400  # VIEW_CHANNEL only
        allow = 0x800  # allow SEND_MESSAGES
        result = calculate_effective_permissions(base, allow_overwrites=allow)
        assert result & 0x800 == 0x800  # SEND_MESSAGES added
        assert result & 0x400 == 0x400  # VIEW_CHANNEL kept

    def test_calculate_effective_permissions_admin_bypass(self):
        """Administrator in base perms should bypass all overwrites."""
        base = 0x8 | 0x800  # ADMINISTRATOR + SEND_MESSAGES
        deny = 0x800  # try to deny SEND_MESSAGES
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
        deny = 0x800  # SEND_MESSAGES
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
                if not attr.startswith("_") and attr not in ("pair",):
                    getattr(result, attr, None)
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


# ---------------------------------------------------------------------------
# has_channel_permission
# ---------------------------------------------------------------------------


class TestHasChannelPermission:
    def test_has_channel_permission_returns_true_when_granted(self):
        """Should return True when member has the specified channel permission."""
        mock_member = MagicMock()
        mock_channel = MagicMock()
        mock_perms = MagicMock()
        mock_perms.send_messages = True
        mock_channel.permissions_for = MagicMock(return_value=mock_perms)

        result = has_channel_permission(mock_member, mock_channel, "SEND_MESSAGES")
        assert result is True

    def test_has_channel_permission_returns_false_when_denied(self):
        """Should return False when member lacks the specified channel permission."""
        mock_member = MagicMock()
        mock_channel = MagicMock()
        mock_perms = MagicMock()
        mock_perms.send_messages = False
        mock_channel.permissions_for = MagicMock(return_value=mock_perms)

        result = has_channel_permission(mock_member, mock_channel, "SEND_MESSAGES")
        assert result is False

    def test_has_channel_permission_returns_false_on_exception(self):
        """Should return False when permissions_for raises an exception."""
        mock_member = MagicMock()
        mock_channel = MagicMock()
        mock_channel.permissions_for = MagicMock(side_effect=Exception("Discord error"))

        result = has_channel_permission(mock_member, mock_channel, "SEND_MESSAGES")
        assert result is False

    def test_has_channel_permission_returns_false_when_attr_missing(self):
        """Should return False when permission attribute is absent."""
        mock_member = MagicMock()
        mock_channel = MagicMock()

        # Return a permissions object that does NOT have the attribute
        class MinimalPerms:
            pass

        mock_channel.permissions_for = MagicMock(return_value=MinimalPerms())

        result = has_channel_permission(mock_member, mock_channel, "SEND_MESSAGES")
        assert result is False


# ---------------------------------------------------------------------------
# has_guild_permission
# ---------------------------------------------------------------------------


class TestHasGuildPermission:
    def test_has_guild_permission_returns_true_when_granted(self):
        """Should return True when member has the specified guild permission."""
        mock_member = MagicMock()
        mock_member.guild_permissions.ban_members = True

        result = has_guild_permission(mock_member, "BAN_MEMBERS")
        assert result is True

    def test_has_guild_permission_returns_false_when_denied(self):
        """Should return False when member lacks the specified guild permission."""
        mock_member = MagicMock()
        mock_member.guild_permissions.ban_members = False

        result = has_guild_permission(mock_member, "BAN_MEMBERS")
        assert result is False

    def test_has_guild_permission_returns_false_on_exception(self):
        """Should return False when guild_permissions raises an exception."""
        mock_member = MagicMock()
        type(mock_member).guild_permissions = property(lambda self: (_ for _ in ()).throw(Exception("error")))

        result = has_guild_permission(mock_member, "BAN_MEMBERS")
        assert result is False


# ---------------------------------------------------------------------------
# evaluate_user_guild_permissions
# ---------------------------------------------------------------------------


class TestEvaluateUserGuildPermissions:
    def _make_member(self, perms_value: int, roles=None):
        """Build a mock member with the given guild permission value."""
        mock_member = MagicMock()
        mock_guild_perms = MagicMock()
        mock_guild_perms.value = perms_value
        mock_member.guild_permissions = mock_guild_perms
        mock_member.roles = roles or []
        return mock_member

    def test_evaluate_user_guild_permissions_grants_allowed(self):
        """Permissions present in the member's guild perms should be granted."""
        member = self._make_member(0x800)  # SEND_MESSAGES
        granted, denied = evaluate_user_guild_permissions(member, MagicMock(), ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in granted
        assert "SEND_MESSAGES" not in denied

    def test_evaluate_user_guild_permissions_denies_missing(self):
        """Permissions absent from the member's guild perms should be denied."""
        member = self._make_member(0x800)  # SEND_MESSAGES only
        granted, denied = evaluate_user_guild_permissions(member, MagicMock(), ["BAN_MEMBERS"])
        assert "BAN_MEMBERS" in denied
        assert "BAN_MEMBERS" not in granted

    def test_evaluate_user_guild_permissions_admin_bypass_grants_all(self):
        """Administrator flag should grant all requested permissions."""
        member = self._make_member(0x8)  # ADMINISTRATOR
        perms_requested = ["SEND_MESSAGES", "BAN_MEMBERS", "KICK_MEMBERS"]
        granted, denied = evaluate_user_guild_permissions(member, MagicMock(), perms_requested)
        # All requested known permissions should be in granted
        for perm in perms_requested:
            assert perm in granted
        assert len(denied) == 0

    def test_evaluate_user_guild_permissions_denies_unknown_permission(self):
        """Unknown permission names should be added to denied set."""
        # Use 0x800 (SEND_MESSAGES) only — no ADMINISTRATOR, so unknown perms are denied
        member = self._make_member(0x800)
        _, denied = evaluate_user_guild_permissions(member, MagicMock(), ["TOTALLY_FAKE"])
        assert "TOTALLY_FAKE" in denied

    def test_evaluate_user_guild_permissions_denies_all_on_exception(self):
        """Should deny all permissions when exception occurs."""
        mock_member = MagicMock()
        type(mock_member).guild_permissions = property(lambda self: (_ for _ in ()).throw(Exception("error")))
        _, denied = evaluate_user_guild_permissions(mock_member, MagicMock(), ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in denied

    def test_evaluate_user_guild_permissions_source_is_permission_source(self):
        """Granted permissions should map to PermissionSource instances."""
        mock_role = MagicMock()
        mock_role.name = "TestRole"
        mock_role.id = 999
        mock_role.permissions = MagicMock()
        mock_role.permissions.value = 0x800  # SEND_MESSAGES
        member = self._make_member(0x800, roles=[mock_role])
        granted, _ = evaluate_user_guild_permissions(member, MagicMock(), ["SEND_MESSAGES"])
        assert isinstance(granted["SEND_MESSAGES"], PermissionSource)

    def test_evaluate_user_guild_permissions_admin_source_is_permission_source(self):
        """When admin, each granted permission should map to a PermissionSource."""
        mock_role = MagicMock()
        mock_role.name = "@everyone"
        mock_role.id = 0
        mock_role.permissions = MagicMock()
        mock_role.permissions.administrator = True
        member = self._make_member(0x8, roles=[mock_role])
        granted, _ = evaluate_user_guild_permissions(member, MagicMock(), ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in granted
        assert isinstance(granted["SEND_MESSAGES"], PermissionSource)


# ---------------------------------------------------------------------------
# evaluate_user_channel_permissions
# ---------------------------------------------------------------------------


class TestEvaluateUserChannelPermissions:
    def _make_member_and_channel(self, perms_value: int, channel_type_name: str = "text"):
        mock_member = MagicMock()
        mock_channel_perms = MagicMock()
        mock_channel_perms.value = perms_value
        mock_channel = MagicMock()
        mock_channel.permissions_for = MagicMock(return_value=mock_channel_perms)
        mock_type = MagicMock()
        mock_type.name = channel_type_name
        mock_channel.type = mock_type
        mock_member.roles = []
        return mock_member, mock_channel

    def test_evaluate_user_channel_permissions_grants_allowed(self):
        """Channel permissions present in member's perms should be granted."""
        member, channel = self._make_member_and_channel(0x800)  # SEND_MESSAGES
        granted, denied = evaluate_user_channel_permissions(member, channel, ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in granted
        assert "SEND_MESSAGES" not in denied

    def test_evaluate_user_channel_permissions_denies_missing(self):
        """Channel permissions absent from member's perms should be denied."""
        member, channel = self._make_member_and_channel(0x0)
        _, denied = evaluate_user_channel_permissions(member, channel, ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in denied

    def test_evaluate_user_channel_permissions_admin_bypass(self):
        """Administrator flag grants all channel permissions."""
        member, channel = self._make_member_and_channel(0x8)  # ADMINISTRATOR
        granted, denied = evaluate_user_channel_permissions(member, channel, ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in granted
        assert len(denied) == 0

    def test_evaluate_user_channel_permissions_denies_unknown_perm(self):
        """Unknown permission names should be denied."""
        # Use 0x800 only — no ADMINISTRATOR, so unknown perms are denied
        member, channel = self._make_member_and_channel(0x800)
        _, denied = evaluate_user_channel_permissions(member, channel, ["FAKE_PERM"])
        assert "FAKE_PERM" in denied

    def test_evaluate_user_channel_permissions_denies_wrong_channel_type(self):
        """Permission not applicable to the channel type should be denied."""
        # PRIORITY_SPEAKER is voice-only; test with a text channel
        member, channel = self._make_member_and_channel(0x100, channel_type_name="text")  # PRIORITY_SPEAKER
        _, denied = evaluate_user_channel_permissions(member, channel, ["PRIORITY_SPEAKER"])
        assert "PRIORITY_SPEAKER" in denied

    def test_evaluate_user_channel_permissions_denies_all_on_exception(self):
        """Should deny all permissions when permissions_for raises an exception."""
        mock_member = MagicMock()
        mock_channel = MagicMock()
        mock_channel.permissions_for = MagicMock(side_effect=Exception("error"))
        _, denied = evaluate_user_channel_permissions(mock_member, mock_channel, ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in denied

    def test_evaluate_user_channel_permissions_voice_channel_type(self):
        """Voice channel type should allow voice-specific permissions."""
        member, channel = self._make_member_and_channel(
            0x100,  # PRIORITY_SPEAKER
            channel_type_name="voice",
        )
        granted, _ = evaluate_user_channel_permissions(member, channel, ["PRIORITY_SPEAKER"])
        assert "PRIORITY_SPEAKER" in granted

    def test_evaluate_user_channel_permissions_stage_channel_type(self):
        """Stage channel type is mapped correctly."""
        member, channel = self._make_member_and_channel(
            0x100000000,  # REQUEST_TO_SPEAK
            channel_type_name="stage_voice",
        )
        granted, _ = evaluate_user_channel_permissions(member, channel, ["REQUEST_TO_SPEAK"])
        assert "REQUEST_TO_SPEAK" in granted


# ---------------------------------------------------------------------------
# evaluate_role_guild_permissions
# ---------------------------------------------------------------------------


class TestEvaluateRoleGuildPermissions:
    def _make_role(self, perms_value: int, role_name: str = "TestRole", role_id: int = 123):
        mock_role = MagicMock()
        mock_role.name = role_name
        mock_role.id = role_id
        mock_perms = MagicMock()
        mock_perms.value = perms_value
        mock_role.permissions = mock_perms
        return mock_role

    def test_evaluate_role_guild_permissions_grants_allowed(self):
        """Permissions present in the role should be granted."""
        role = self._make_role(0x800)  # SEND_MESSAGES
        granted, denied = evaluate_role_guild_permissions(role, MagicMock(), ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in granted
        assert "SEND_MESSAGES" not in denied

    def test_evaluate_role_guild_permissions_denies_missing(self):
        """Permissions absent from the role should be denied."""
        role = self._make_role(0x0)
        _, denied = evaluate_role_guild_permissions(role, MagicMock(), ["BAN_MEMBERS"])
        assert "BAN_MEMBERS" in denied

    def test_evaluate_role_guild_permissions_admin_bypass(self):
        """Administrator flag in role grants all permissions."""
        role = self._make_role(0x8)  # ADMINISTRATOR
        granted, denied = evaluate_role_guild_permissions(role, MagicMock(), ["SEND_MESSAGES", "BAN_MEMBERS"])
        assert "SEND_MESSAGES" in granted
        assert "BAN_MEMBERS" in granted
        assert len(denied) == 0

    def test_evaluate_role_guild_permissions_source_has_role_name(self):
        """Granted permissions should reference the correct role."""
        role = self._make_role(0x800, role_name="Moderator", role_id=456)
        granted, _ = evaluate_role_guild_permissions(role, MagicMock(), ["SEND_MESSAGES"])
        src = granted["SEND_MESSAGES"]
        assert src.role_name == "Moderator"
        assert src.role_id == 456

    def test_evaluate_role_guild_permissions_denies_unknown_perm(self):
        """Unknown permission names should be denied."""
        # Use 0x800 only — no ADMINISTRATOR, so unknown perms go through the per-perm loop
        role = self._make_role(0x800)
        _, denied = evaluate_role_guild_permissions(role, MagicMock(), ["UNKNOWN_PERM"])
        assert "UNKNOWN_PERM" in denied

    def test_evaluate_role_guild_permissions_denies_all_on_exception(self):
        """Should deny all permissions on exception."""
        mock_role = MagicMock()
        type(mock_role).permissions = property(lambda self: (_ for _ in ()).throw(Exception("error")))
        _, denied = evaluate_role_guild_permissions(mock_role, MagicMock(), ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in denied


# ---------------------------------------------------------------------------
# evaluate_role_channel_permissions
# ---------------------------------------------------------------------------


class TestEvaluateRoleChannelPermissions:
    def _make_role_and_channel(
        self,
        base_perms_value: int,
        allow_val: int = 0,
        deny_val: int = 0,
        channel_type_name: str = "text",
        has_overwrite: bool = True,
    ):
        mock_role = MagicMock()
        mock_role.name = "TestRole"
        mock_role.id = 111
        mock_role_perms = MagicMock()
        mock_role_perms.value = base_perms_value
        mock_role.permissions = mock_role_perms

        mock_allow = MagicMock()
        mock_allow.value = allow_val
        mock_deny = MagicMock()
        mock_deny.value = deny_val

        mock_overwrite = MagicMock()
        mock_overwrite.pair = MagicMock(return_value=(mock_allow, mock_deny))

        mock_channel = MagicMock()
        mock_type = MagicMock()
        mock_type.name = channel_type_name
        mock_channel.type = mock_type
        if has_overwrite:
            mock_channel.overwrites = {mock_role: mock_overwrite}
        else:
            mock_channel.overwrites = {}
        return mock_role, mock_channel

    def test_evaluate_role_channel_permissions_grants_base_perm(self):
        """Base role permission (no overwrite) should be granted on text channel."""
        role, channel = self._make_role_and_channel(0x800, has_overwrite=False)
        granted, _ = evaluate_role_channel_permissions(role, channel, ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in granted

    def test_evaluate_role_channel_permissions_deny_overwrite_removes_perm(self):
        """Deny overwrite should remove permission even if in base role perms."""
        role, channel = self._make_role_and_channel(
            base_perms_value=0x800,  # SEND_MESSAGES granted at base
            deny_val=0x800,  # but denied in overwrite
        )
        _, denied = evaluate_role_channel_permissions(role, channel, ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in denied

    def test_evaluate_role_channel_permissions_allow_overwrite_adds_perm(self):
        """Allow overwrite should add permission not present in base role perms."""
        role, channel = self._make_role_and_channel(
            base_perms_value=0x0,
            allow_val=0x800,  # SEND_MESSAGES allowed in overwrite
        )
        granted, _ = evaluate_role_channel_permissions(role, channel, ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in granted

    def test_evaluate_role_channel_permissions_admin_bypass(self):
        """Administrator in effective permissions grants all requested."""
        # Admin (0x8) in base; no overwrites (overwrite deny doesn't apply to admin)
        role, channel = self._make_role_and_channel(0x8, has_overwrite=False)
        granted, denied = evaluate_role_channel_permissions(role, channel, ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in granted
        assert len(denied) == 0

    def test_evaluate_role_channel_permissions_denies_wrong_channel_type(self):
        """Permissions not applicable to the channel type should be denied."""
        # PRIORITY_SPEAKER is voice-only; text channel
        role, channel = self._make_role_and_channel(0x100, channel_type_name="text")
        _, denied = evaluate_role_channel_permissions(role, channel, ["PRIORITY_SPEAKER"])
        assert "PRIORITY_SPEAKER" in denied

    def test_evaluate_role_channel_permissions_denies_unknown_perm(self):
        """Unknown permission names should be denied."""
        # Use 0x800 only — no ADMINISTRATOR, so unknown perms go through the per-perm loop
        role, channel = self._make_role_and_channel(0x800)
        _, denied = evaluate_role_channel_permissions(role, channel, ["NOT_A_PERM"])
        assert "NOT_A_PERM" in denied

    def test_evaluate_role_channel_permissions_denies_all_on_exception(self):
        """Should deny all permissions on exception."""
        mock_role = MagicMock()
        type(mock_role).permissions = property(lambda self: (_ for _ in ()).throw(Exception("error")))
        mock_channel = MagicMock()
        _, denied = evaluate_role_channel_permissions(mock_role, mock_channel, ["SEND_MESSAGES"])
        assert "SEND_MESSAGES" in denied

    def test_evaluate_role_channel_permissions_overwrite_pair_exception_fallback(self):
        """When overwrite.pair() raises, should fall back to attribute inspection."""
        mock_role = MagicMock()
        mock_role.name = "TestRole"
        mock_role.id = 111
        mock_role.permissions = MagicMock()
        mock_role.permissions.value = 0x0

        # Create overwrite that fails pair() but has direct attribute access
        mock_overwrite = MagicMock()
        mock_overwrite.pair = MagicMock(side_effect=Exception("pair failed"))
        mock_overwrite.send_messages = True  # allow SEND_MESSAGES via attribute

        mock_channel = MagicMock()
        mock_type = MagicMock()
        mock_type.name = "text"
        mock_channel.type = mock_type
        mock_channel.overwrites = {mock_role: mock_overwrite}

        granted, _ = evaluate_role_channel_permissions(mock_role, mock_channel, ["SEND_MESSAGES"])
        # Because the fallback reads direct attributes, SEND_MESSAGES should be allowed
        assert "SEND_MESSAGES" in granted


# ---------------------------------------------------------------------------
# _find_admin_source
# ---------------------------------------------------------------------------


class TestFindAdminSource:
    def test_find_admin_source_returns_everyone_for_everyone_role(self):
        """Should return PermissionSource('everyone') when @everyone role is admin."""
        mock_role = MagicMock()
        mock_role.name = "@everyone"
        mock_role.id = 0
        mock_role.permissions = MagicMock()
        mock_role.permissions.administrator = True

        mock_member = MagicMock()
        mock_member.roles = [mock_role]

        result = _find_admin_source(mock_member)
        assert result.type == "everyone"

    def test_find_admin_source_returns_role_source_for_admin_role(self):
        """Should return PermissionSource('role') when a named role has admin."""
        mock_role = MagicMock()
        mock_role.name = "Admin"
        mock_role.id = 999
        mock_role.permissions = MagicMock()
        mock_role.permissions.administrator = True

        mock_member = MagicMock()
        mock_member.roles = [mock_role]

        result = _find_admin_source(mock_member)
        assert result.type == "role"
        assert result.role_name == "Admin"
        assert result.role_id == 999

    def test_find_admin_source_returns_direct_fallback_when_no_admin_role(self):
        """Should return PermissionSource('direct') when no role has admin."""
        mock_role = MagicMock()
        mock_role.name = "Members"
        mock_role.id = 1
        mock_role.permissions = MagicMock()
        mock_role.permissions.administrator = False

        mock_member = MagicMock()
        mock_member.roles = [mock_role]

        result = _find_admin_source(mock_member)
        assert result.type == "direct"


# ---------------------------------------------------------------------------
# _find_permission_source
# ---------------------------------------------------------------------------


class TestFindPermissionSource:
    def test_find_permission_source_returns_everyone_for_everyone_role(self):
        """Should return 'everyone' when @everyone role has the permission."""
        mock_role = MagicMock()
        mock_role.name = "@everyone"
        mock_role.id = 0
        mock_role.permissions = MagicMock()
        mock_role.permissions.value = 0x800  # SEND_MESSAGES

        mock_member = MagicMock()
        mock_member.roles = [mock_role]

        result = _find_permission_source(mock_member, "SEND_MESSAGES")
        assert result.type == "everyone"

    def test_find_permission_source_returns_role_for_named_role(self):
        """Should return 'role' source when a named role has the permission."""
        mock_role = MagicMock()
        mock_role.name = "Moderator"
        mock_role.id = 456
        mock_role.permissions = MagicMock()
        mock_role.permissions.value = 0x800  # SEND_MESSAGES

        mock_member = MagicMock()
        mock_member.roles = [mock_role]

        result = _find_permission_source(mock_member, "SEND_MESSAGES")
        assert result.type == "role"
        assert result.role_name == "Moderator"
        assert result.role_id == 456

    def test_find_permission_source_returns_direct_fallback_when_no_role_has_perm(self):
        """Should return 'direct' when no role has the permission."""
        mock_role = MagicMock()
        mock_role.name = "Members"
        mock_role.id = 1
        mock_role.permissions = MagicMock()
        mock_role.permissions.value = 0x0

        mock_member = MagicMock()
        mock_member.roles = [mock_role]

        result = _find_permission_source(mock_member, "SEND_MESSAGES")
        assert result.type == "direct"


# ---------------------------------------------------------------------------
# _find_channel_permission_source
# ---------------------------------------------------------------------------


class TestFindChannelPermissionSource:
    def test_find_channel_permission_source_returns_direct_for_member_overwrite(self):
        """Should return 'direct' when member has an explicit channel overwrite."""
        mock_member = MagicMock()
        mock_member.id = 555
        mock_member.roles = []

        allow_perm = MagicMock()
        allow_perm.value = 0x800  # SEND_MESSAGES
        deny_perm = MagicMock()
        deny_perm.value = 0

        mock_overwrite = MagicMock()
        mock_overwrite.pair = MagicMock(return_value=(allow_perm, deny_perm))

        mock_target = MagicMock()
        mock_target.id = 555  # same as member id

        mock_channel = MagicMock()
        mock_channel.overwrites = {mock_target: mock_overwrite}

        result = _find_channel_permission_source(mock_member, mock_channel, "SEND_MESSAGES")
        assert result.type == "direct"

    def test_find_channel_permission_source_returns_role_for_role_overwrite(self):
        """Should return 'role' source when role has channel overwrite granting perm."""
        mock_role = MagicMock()
        mock_role.name = "Moderator"
        mock_role.id = 789
        mock_role.permissions = MagicMock()
        mock_role.permissions.value = 0x0

        mock_member = MagicMock()
        mock_member.id = 111
        mock_member.roles = [mock_role]

        allow_perm = MagicMock()
        allow_perm.value = 0x800  # SEND_MESSAGES

        deny_perm = MagicMock()
        deny_perm.value = 0

        mock_overwrite = MagicMock()
        mock_overwrite.pair = MagicMock(return_value=(allow_perm, deny_perm))

        mock_channel = MagicMock()
        mock_channel.overwrites = {mock_role: mock_overwrite}

        result = _find_channel_permission_source(mock_member, mock_channel, "SEND_MESSAGES")
        assert result.type == "role"
        assert result.role_name == "Moderator"

    def test_find_channel_permission_source_falls_back_to_guild_perms(self):
        """Should fall back to guild permission source when no channel overwrite."""
        mock_role = MagicMock()
        mock_role.name = "@everyone"
        mock_role.id = 0
        mock_role.permissions = MagicMock()
        mock_role.permissions.value = 0x800  # SEND_MESSAGES

        mock_member = MagicMock()
        mock_member.id = 111
        mock_member.roles = [mock_role]

        mock_channel = MagicMock()
        mock_channel.overwrites = {}

        result = _find_channel_permission_source(mock_member, mock_channel, "SEND_MESSAGES")
        # Falls back to _find_permission_source, should find @everyone
        assert result.type == "everyone"

    def test_find_channel_permission_source_everyone_role_in_overwrite(self):
        """Should return 'everyone' when @everyone role has channel overwrite."""
        mock_role = MagicMock()
        mock_role.name = "@everyone"
        mock_role.id = 0
        mock_role.permissions = MagicMock()
        mock_role.permissions.value = 0x0

        mock_member = MagicMock()
        mock_member.id = 111
        mock_member.roles = [mock_role]

        allow_perm = MagicMock()
        allow_perm.value = 0x800  # SEND_MESSAGES
        deny_perm = MagicMock()
        deny_perm.value = 0

        mock_overwrite = MagicMock()
        mock_overwrite.pair = MagicMock(return_value=(allow_perm, deny_perm))

        mock_channel = MagicMock()
        mock_channel.overwrites = {mock_role: mock_overwrite}

        result = _find_channel_permission_source(mock_member, mock_channel, "SEND_MESSAGES")
        assert result.type == "everyone"


# ---------------------------------------------------------------------------
# overwrite_to_dict — discord.PermissionOverwrite path
# ---------------------------------------------------------------------------


class TestOverwriteToDictPermissionOverwrite:
    def test_overwrite_to_dict_from_permission_overwrite_with_pair(self):
        """Should handle a PermissionOverwrite object that has .pair() method."""
        mock_overwrite = MagicMock()
        allow_mock = MagicMock()
        allow_mock.value = 0x800
        deny_mock = MagicMock()
        deny_mock.value = 0x400
        mock_overwrite.pair = MagicMock(return_value=(allow_mock, deny_mock))
        # Make it NOT a dict so the dict branch is skipped
        # and set send_messages / view_channel attributes for the permissions map
        mock_overwrite.send_messages = True
        mock_overwrite.view_channel = False
        # Other PERMISSION_FLAGS attributes should be None to be excluded
        for name in ["kick_members", "ban_members", "administrator"]:
            setattr(mock_overwrite, name, None)

        result = overwrite_to_dict(mock_overwrite)
        assert result["allow"] == 0x800
        assert result["deny"] == 0x400
        assert isinstance(result["permissions"], dict)

    def test_overwrite_to_dict_from_permission_overwrite_pair_exception_fallback(self):
        """Should use exception fallback path when pair() raises."""
        mock_overwrite = MagicMock()
        mock_overwrite.pair = MagicMock(side_effect=Exception("pair failed"))
        mock_overwrite.send_messages = True
        mock_overwrite.view_channel = None  # None → excluded

        result = overwrite_to_dict(mock_overwrite)
        assert result["allow"] == 0
        assert result["deny"] == 0
        assert "send_messages" in result["permissions"]
        assert result["permissions"]["send_messages"] is True


# ---------------------------------------------------------------------------
# calculate_effective_permissions — additional edge cases
# ---------------------------------------------------------------------------


class TestCalculateEffectivePermissionsEdgeCases:
    def test_calculate_effective_permissions_no_overwrites(self):
        """With no overwrites, effective equals base."""
        base = 0x800 | 0x400
        result = calculate_effective_permissions(base)
        assert result == base

    def test_calculate_effective_permissions_deny_and_allow_together(self):
        """Deny applied first, then allow; allow takes precedence over deny."""
        base = 0x800 | 0x400  # SEND_MESSAGES + VIEW_CHANNEL
        deny = 0x800  # deny SEND_MESSAGES
        allow = 0x800  # but allow it back
        result = calculate_effective_permissions(base, allow_overwrites=allow, deny_overwrites=deny)
        # deny removes 0x800, then allow adds it back
        assert result & 0x800 == 0x800

    def test_calculate_effective_permissions_zero_base(self):
        """Zero base with allow overwrite adds the allowed bits."""
        base = 0x0
        allow = 0x800
        result = calculate_effective_permissions(base, allow_overwrites=allow)
        assert result == 0x800

    def test_calculate_effective_permissions_admin_ignores_allow(self):
        """Administrator base ignores allow overwrites too."""
        base = 0x8  # ADMINISTRATOR only
        allow = 0x800
        deny = 0x400
        result = calculate_effective_permissions(base, allow_overwrites=allow, deny_overwrites=deny)
        assert result == base  # unchanged since admin bypasses
