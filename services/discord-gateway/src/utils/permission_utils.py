"""
Permission utilities for Discord API operations.

This module provides utilities for handling Discord permissions,
including permission flags reference data and permission checking functions.
Contains NO direct Discord interactions.
"""

from typing import TYPE_CHECKING, Any

from shared import bblogger

if TYPE_CHECKING:
    pass  # type: ignore

flogger = bblogger.get_logger("gateway-permission-utils")


class PermissionSource:
    """Represents how a permission was granted."""

    def __init__(self, source_type: str, role_name: str | None = None, role_id: int | None = None):
        self.type = source_type  # "direct", "role", "everyone"
        self.role_name = role_name
        self.role_id = role_id


# Discord permission flags with descriptions
PERMISSION_FLAGS = {
    "CREATE_INSTANT_INVITE": {
        "value": 0x0000000000000001,
        "description": "Allows creation of instant invites",
        "channel_types": ["text", "voice", "stage"],
    },
    "KICK_MEMBERS": {"value": 0x0000000000000002, "description": "Allows kicking members", "channel_types": []},
    "BAN_MEMBERS": {"value": 0x0000000000000004, "description": "Allows banning members", "channel_types": []},
    "ADMINISTRATOR": {
        "value": 0x0000000000000008,
        "description": "Allows all permissions and bypasses channel permission overwrites",
        "channel_types": [],
    },
    "MANAGE_CHANNELS": {
        "value": 0x0000000000000010,
        "description": "Allows management and editing of channels",
        "channel_types": ["text", "voice", "stage"],
    },
    "MANAGE_GUILD": {
        "value": 0x0000000000000020,
        "description": "Allows management and editing of the guild",
        "channel_types": [],
    },
    "ADD_REACTIONS": {
        "value": 0x0000000000000040,
        "description": "Allows for the addition of reactions to messages",
        "channel_types": ["text", "voice", "stage"],
    },
    "VIEW_AUDIT_LOG": {
        "value": 0x0000000000000080,
        "description": "Allows for viewing of audit logs",
        "channel_types": [],
    },
    "PRIORITY_SPEAKER": {
        "value": 0x0000000000000100,
        "description": "Allows for using priority speaker in a voice channel",
        "channel_types": ["voice"],
    },
    "STREAM": {
        "value": 0x0000000000000200,
        "description": "Allows the user to use video and stream (go live) in a voice channel",
        "channel_types": ["voice", "stage"],
    },
    "VIEW_CHANNEL": {
        "value": 0x0000000000000400,
        "description": (
            "Allows guild members to view a channel, which includes reading messages "
            "in text channels and joining voice channels"
        ),
        "channel_types": ["text", "voice", "stage"],
    },
    "SEND_MESSAGES": {
        "value": 0x0000000000000800,
        "description": "Allows for sending messages in a channel and creating threads in a forum",
        "channel_types": ["text", "voice", "stage"],
    },
    "SEND_TTS_MESSAGES": {
        "value": 0x0000000000001000,
        "description": "Allows for sending of /tts messages",
        "channel_types": ["text", "voice", "stage"],
    },
    "MANAGE_MESSAGES": {
        "value": 0x0000000000002000,
        "description": "Allows for deletion of other users messages",
        "channel_types": ["text", "voice", "stage"],
    },
    "EMBED_LINKS": {
        "value": 0x0000000000004000,
        "description": "Links sent by users with this permission will be auto-embedded",
        "channel_types": ["text", "voice", "stage"],
    },
    "ATTACH_FILES": {
        "value": 0x0000000000008000,
        "description": "Allows for uploading images and files",
        "channel_types": ["text", "voice", "stage"],
    },
    "READ_MESSAGE_HISTORY": {
        "value": 0x0000000000010000,
        "description": "Allows for reading of message history",
        "channel_types": ["text", "voice", "stage"],
    },
    "MENTION_EVERYONE": {
        "value": 0x0000000000020000,
        "description": (
            "Allows for using the @everyone tag to notify all users in a channel, "
            "and the @here tag to notify all online users in a channel"
        ),
        "channel_types": ["text", "voice", "stage"],
    },
    "USE_EXTERNAL_EMOJIS": {
        "value": 0x0000000000040000,
        "description": "Allows the usage of custom emojis from other servers",
        "channel_types": ["text", "voice", "stage"],
    },
    "VIEW_GUILD_INSIGHTS": {
        "value": 0x0000000000080000,
        "description": "Allows for viewing guild insights",
        "channel_types": [],
    },
    "CONNECT": {
        "value": 0x0000000000100000,
        "description": "Allows for joining of a voice channel",
        "channel_types": ["voice", "stage"],
    },
    "SPEAK": {
        "value": 0x0000000000200000,
        "description": "Allows for speaking in a voice channel",
        "channel_types": ["voice"],
    },
    "MUTE_MEMBERS": {
        "value": 0x0000000000400000,
        "description": "Allows for muting members in a voice channel",
        "channel_types": ["voice", "stage"],
    },
    "DEAFEN_MEMBERS": {
        "value": 0x0000000000800000,
        "description": "Allows for deafening of members in a voice channel",
        "channel_types": ["voice"],
    },
    "MOVE_MEMBERS": {
        "value": 0x0000000001000000,
        "description": "Allows for moving of members between voice channels",
        "channel_types": ["voice", "stage"],
    },
    "USE_VAD": {
        "value": 0x0000000002000000,
        "description": "Allows for using voice-activity-detection in a voice channel",
        "channel_types": ["voice"],
    },
    "CHANGE_NICKNAME": {
        "value": 0x0000000004000000,
        "description": "Allows for modification of own nickname",
        "channel_types": [],
    },
    "MANAGE_NICKNAMES": {
        "value": 0x0000000008000000,
        "description": "Allows for modification of other users nicknames",
        "channel_types": [],
    },
    "MANAGE_ROLES": {
        "value": 0x0000000010000000,
        "description": "Allows management and editing of roles",
        "channel_types": ["text", "voice", "stage"],
    },
    "MANAGE_WEBHOOKS": {
        "value": 0x0000000020000000,
        "description": "Allows management and editing of webhooks",
        "channel_types": ["text", "voice", "stage"],
    },
    "MANAGE_EXPRESSIONS": {
        "value": 0x0000000040000000,
        "description": "Allows editing and deleting emojis, stickers, and soundboard sounds",
        "channel_types": [],
    },
    "USE_APPLICATION_COMMANDS": {
        "value": 0x0000000080000000,
        "description": "Allows members to use application commands, including slash commands and context menu commands",
        "channel_types": ["text", "voice", "stage"],
    },
    "REQUEST_TO_SPEAK": {
        "value": 0x0000000100000000,
        "description": "Allows for requesting to speak in stage channels",
        "channel_types": ["stage"],
    },
    "MANAGE_EVENTS": {
        "value": 0x0000000200000000,
        "description": "Allows for editing and deleting scheduled events",
        "channel_types": ["voice", "stage"],
    },
    "MANAGE_THREADS": {
        "value": 0x0000000400000000,
        "description": "Allows for deleting and archiving threads, and viewing all private threads",
        "channel_types": ["text"],
    },
    "CREATE_PUBLIC_THREADS": {
        "value": 0x0000000800000000,
        "description": "Allows for creating public and announcement threads",
        "channel_types": ["text"],
    },
    "CREATE_PRIVATE_THREADS": {
        "value": 0x0000001000000000,
        "description": "Allows for creating private threads",
        "channel_types": ["text"],
    },
    "USE_EXTERNAL_STICKERS": {
        "value": 0x0000002000000000,
        "description": "Allows the usage of custom stickers from other servers",
        "channel_types": ["text", "voice", "stage"],
    },
    "SEND_MESSAGES_IN_THREADS": {
        "value": 0x0000004000000000,
        "description": "Allows for sending messages in threads",
        "channel_types": ["text"],
    },
    "USE_EMBEDDED_ACTIVITIES": {
        "value": 0x0000008000000000,
        "description": "Allows for using Activities (applications with the EMBEDDED flag) in a voice channel",
        "channel_types": ["text", "voice"],
    },
    "MODERATE_MEMBERS": {
        "value": 0x0000010000000000,
        "description": (
            "Allows for timing out users to prevent them from sending or reacting to messages "
            "in chat and threads, and from speaking in voice and stage channels"
        ),
        "channel_types": [],
    },
    "VIEW_CREATOR_MONETIZATION_ANALYTICS": {
        "value": 0x0000020000000000,
        "description": "Allows for viewing guild role subscriptions insights",
        "channel_types": [],
    },
    "USE_SOUNDBOARD": {
        "value": 0x0000040000000000,
        "description": "Allows the usage of the soundboard in a voice channel",
        "channel_types": ["voice"],
    },
    "CREATE_EXPRESSIONS": {
        "value": 0x0000080000000000,
        "description": (
            "Allows for creating emojis, stickers, and soundboard sounds, "
            "and editing/deleting ones created by the current user"
        ),
        "channel_types": [],
    },
    "CREATE_EVENTS": {
        "value": 0x0000100000000000,
        "description": "Allows for creating scheduled events, and editing/deleting ones created by the current user",
        "channel_types": [],
    },
    "USE_EXTERNAL_SOUNDS": {
        "value": 0x0000200000000000,
        "description": "Allows the usage of custom soundboard sounds from other servers",
        "channel_types": ["voice"],
    },
    "SEND_VOICE_MESSAGES": {
        "value": 0x0000400000000000,
        "description": "Allows for sending voice messages in a channel",
        "channel_types": ["text", "voice", "stage"],
    },
    "SET_VOICE_CHANNEL_STATUS": {
        "value": 0x0001000000000000,
        "description": "Allows setting voice channel status",
        "channel_types": ["voice"],
    },
    "SEND_POLLS": {
        "value": 0x0002000000000000,
        "description": "Allows sending polls",
        "channel_types": ["text", "voice", "stage"],
    },
    "USE_EXTERNAL_APPS": {
        "value": 0x0004000000000000,
        "description": "Allows the usage of user-installed applications without forced-ephemeral responses",
        "channel_types": ["text", "voice", "stage"],
    },
}


def get_all_permissions() -> list[dict[str, Any]]:
    """Get all Discord permissions with metadata."""
    return [
        {
            "name": name,
            "value": data["value"],
            "description": data["description"],
            "channel_types": data["channel_types"],
        }
        for name, data in PERMISSION_FLAGS.items()
    ]


def get_role_permissions() -> list[dict[str, Any]]:
    """Get permissions that can be assigned to roles."""
    # All permissions can be assigned to roles
    return get_all_permissions()


def get_user_permissions() -> list[dict[str, Any]]:
    """Get permissions that can be used in user overwrites."""
    # Channel-level permissions that can be overwritten for users
    return [
        perm
        for perm in get_all_permissions()
        if perm["channel_types"]  # Only permissions that apply to channels
    ]


def get_channel_permissions() -> list[dict[str, Any]]:
    """Get permissions applicable to text/voice channels."""
    return [
        perm for perm in get_all_permissions() if "text" in perm["channel_types"] or "voice" in perm["channel_types"]
    ]


def get_category_permissions() -> list[dict[str, Any]]:
    """Get permissions applicable to category channels."""
    # Category channels inherit most channel permissions
    return get_channel_permissions()


def create_permission_overwrite(allow: int | None = None, deny: int | None = None) -> "Any":
    """
    Create a Discord permission overwrite from allow/deny bit values.

    Args:
        allow: Permissions to allow (bitfield)
        deny: Permissions to deny (bitfield)

    Returns:
        discord.PermissionOverwrite-like object (constructed lazily)
    """
    allow_dbg = f"{allow:#x}" if allow is not None else "None"
    deny_dbg = f"{deny:#x}" if deny is not None else "None"
    flogger.debug(f"Creating permission overwrite: allow={allow_dbg}, deny={deny_dbg}")

    kwargs = {}

    if allow is not None:
        # Convert allow bitfield to individual permission kwargs
        for name, data in PERMISSION_FLAGS.items():
            if allow & data["value"]:
                kwargs[name.lower()] = True

    if deny is not None:
        # Convert deny bitfield to individual permission kwargs
        for name, data in PERMISSION_FLAGS.items():
            if deny & data["value"]:
                kwargs[name.lower()] = False

    # Import discord only when actually constructing the object to avoid a hard runtime dependency
    try:
        import discord  # pylint: disable=import-outside-toplevel

        overwrite = discord.PermissionOverwrite(**kwargs)
        flogger.debug(f"Successfully created discord.PermissionOverwrite with {len(kwargs)} permissions")
        return overwrite
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Fall back to returning the raw kwargs dict if discord isn't available.
        # Callers that expect a discord.PermissionOverwrite should import discord themselves.
        flogger.debug(f"Discord import or PermissionOverwrite creation failed: {exc}; returning kwargs dict")
        return kwargs


def check_permission(permissions_value: int, permission_name: str) -> bool:
    """
    Check if a permission value includes a specific permission.

    Args:
        permissions_value: Integer representation of permissions
        permission_name: Name of permission to check (uppercase with underscores)

    Returns:
        bool: True if permission is granted, False otherwise
    """
    flogger.debug(f"Checking permission {permission_name} against permissions_value={permissions_value:#x}")

    if permission_name not in PERMISSION_FLAGS:
        flogger.error(f"Unknown permission requested: {permission_name}")
        raise ValueError(f"Unknown permission: {permission_name}")

    permission_bit = PERMISSION_FLAGS[permission_name]["value"]
    has_perm = bool(permissions_value & permission_bit)

    if not has_perm:
        flogger.debug(
            f"Permission check failed: {permission_name} not granted in permissions_value={permissions_value:#x}"
        )
    else:
        flogger.debug(f"Permission check passed: {permission_name} granted in permissions_value={permissions_value:#x}")

    return has_perm


def check_permissions(permissions_value: int, permission_names: list[str]) -> dict[str, bool]:
    """
    Check multiple permissions against a permission value.

    Args:
        permissions_value: Integer representation of permissions
        permission_names: List of permission names to check

    Returns:
        Dict[str, bool]: Mapping of permission names to their granted status
    """
    flogger.debug(f"Checking {len(permission_names)} permissions against permissions_value={permissions_value:#x}")
    results = {}
    for permission_name in permission_names:
        results[permission_name] = check_permission(permissions_value, permission_name)

    denied = [name for name, granted in results.items() if not granted]
    if denied:
        flogger.warning(f"Multiple permissions check: {len(denied)} permission(s) denied: {denied}")

    return results


def has_administrator(permissions_value: int) -> bool:
    """
    Check if permissions include Administrator (which grants all permissions).

    Args:
        permissions_value: Integer representation of permissions

    Returns:
        bool: True if Administrator permission is granted
    """
    return check_permission(permissions_value, "ADMINISTRATOR")


def calculate_effective_permissions(
    base_permissions: int, allow_overwrites: int | None = None, deny_overwrites: int | None = None
) -> int:
    """
    Calculate effective permissions after applying overwrites.

    Args:
        base_permissions: Base permissions (usually from roles)
        allow_overwrites: Permissions explicitly allowed in overwrites
        deny_overwrites: Permissions explicitly denied in overwrites

    Returns:
        int: Effective permissions value
    """
    allow_str = f"{allow_overwrites:#x}" if allow_overwrites is not None else "None"
    deny_str = f"{deny_overwrites:#x}" if deny_overwrites is not None else "None"
    flogger.debug(f"Calculating effective permissions: base={base_permissions:#x}, allow={allow_str}, deny={deny_str}")

    # Administrator bypasses overwrites
    if has_administrator(base_permissions):
        flogger.debug("Administrator permission detected; overwrites bypassed")
        return base_permissions

    effective = base_permissions

    # Apply deny overwrites first
    if deny_overwrites is not None:
        effective &= ~deny_overwrites
        flogger.debug(f"Applied deny overwrites: effective={effective:#x}")

    # Then apply allow overwrites
    if allow_overwrites is not None:
        effective |= allow_overwrites
        flogger.debug(f"Applied allow overwrites: effective={effective:#x}")

    return effective


def permissions_to_dict(permissions_value: int) -> dict[str, bool]:
    """Convert integer permissions value to dictionary of permission flags."""
    return {name.lower(): check_permission(permissions_value, name) for name in PERMISSION_FLAGS}


def overwrite_to_dict(overwrite: "Any") -> dict[str, Any]:
    """Convert permission overwrite to dictionary with allow/deny values."""
    flogger.debug(f"Converting overwrite to dict: type={type(overwrite).__name__}")

    # overwrite may be a discord.PermissionOverwrite or a dict (from the fallback above)
    if isinstance(overwrite, dict):
        allow_val = overwrite.get("_allow", None) or 0
        deny_val = overwrite.get("_deny", None) or 0
        perms_map = {k: v for k, v in overwrite.items() if not k.startswith("_")}
        result = {"allow": allow_val, "deny": deny_val, "permissions": perms_map}
        flogger.debug(
            f"Converted dict overwrite: allow={allow_val:#x}, deny={deny_val:#x}, perms_count={len(perms_map)}"
        )
        return result

    # Best-effort handling for discord.PermissionOverwrite
    try:
        allow, deny = overwrite.pair()
        allow_val = getattr(allow, "value", allow)
        deny_val = getattr(deny, "value", deny)
        perms_dict = {
            name.lower(): getattr(overwrite, name.lower())
            for name in PERMISSION_FLAGS
            if getattr(overwrite, name.lower()) is not None
        }
        result = {"allow": allow_val, "deny": deny_val, "permissions": perms_dict}
        flogger.debug(f"Converted discord.PermissionOverwrite: allow={allow_val:#x}, deny={deny_val:#x}")
        return result
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Last-resort normalization
        flogger.debug(f"Error converting overwrite using pair(): {exc}; using attribute fallback")
        result = {}
        for name in PERMISSION_FLAGS:
            try:
                val = getattr(overwrite, name.lower())
                if val is not None:
                    result[name.lower()] = val
            except Exception:  # pylint: disable=broad-exception-caught
                continue
        result_dict = {"allow": 0, "deny": 0, "permissions": result}
        flogger.debug(f"Fallback conversion result: perms_count={len(result)}")
        return result_dict


def get_permission_names_by_value(permissions_value: int) -> list[str]:
    """
    Get list of permission names that are granted in a permissions value.

    Args:
        permissions_value: Integer representation of permissions

    Returns:
        List[str]: List of granted permission names
    """
    granted_permissions = []
    for name, data in PERMISSION_FLAGS.items():
        if permissions_value & data["value"]:
            granted_permissions.append(name)
    return granted_permissions


def combine_permissions(*permission_values: int) -> int:
    """
    Combine multiple permission values using bitwise OR.

    Args:
        *permission_values: Variable number of permission integer values

    Returns:
        int: Combined permissions value
    """
    result = 0
    for value in permission_values:
        result |= value
    return result


def has_channel_permission(member: "Any", channel: "Any", permission: str) -> bool:
    """
    Check whether `member` has the named permission in `channel`.
    Permission name must be uppercase with underscores, e.g. "SEND_MESSAGES".
    """
    member_id = getattr(member, "id", "unknown")
    channel_id = getattr(channel, "id", "unknown")

    try:
        perms = channel.permissions_for(member)
        has_perm = bool(getattr(perms, permission.lower(), False))
        if not has_perm:
            flogger.debug(
                f"Channel permission denied: member {member_id}, channel {channel_id}, permission {permission}"
            )
        else:
            flogger.debug(
                f"Channel permission granted: member {member_id}, channel {channel_id}, permission {permission}"
            )
        return has_perm
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # If channel.permissions_for is not available or fails, conservative False
        flogger.warning(f"Error checking channel permission for member {member_id} in channel {channel_id}: {exc}")
        return False


def has_guild_permission(member: "Any", permission: str) -> bool:
    """
    Check whether `member` has the named guild permission.
    Permission name must be uppercase with underscores, e.g. "BAN_MEMBERS".
    """
    member_id = getattr(member, "id", "unknown")

    try:
        perms = member.guild_permissions
        has_perm = bool(getattr(perms, permission.lower(), False))
        if not has_perm:
            flogger.debug(f"Guild permission denied: member {member_id}, permission {permission}")
        else:
            flogger.debug(f"Guild permission granted: member {member_id}, permission {permission}")
        return has_perm
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.warning(f"Error checking guild permission for member {member_id}: {exc}")
        return False


def evaluate_user_guild_permissions(
    member: "Any", _guild: "Any", requested_permissions: list[str]
) -> tuple[dict[str, PermissionSource], set[str]]:
    """
    Evaluate guild-level permissions for a user.

    Returns:
        Tuple of (granted_permissions_dict, denied_permissions_set)
    """
    flogger.debug(
        f"Evaluating guild permissions for member {getattr(member, 'id', 'unknown')}; requested={requested_permissions}"
    )

    granted = {}
    denied = set()

    try:
        # Get effective guild permissions
        guild_perms = member.guild_permissions
        perms_value = getattr(guild_perms, "value", 0)

        # Check administrator first (grants everything)
        if has_administrator(perms_value):
            flogger.debug(f"Member {getattr(member, 'id', 'unknown')} has ADMINISTRATOR permission")
            for perm in requested_permissions:
                if perm in PERMISSION_FLAGS:
                    # Find which role granted administrator
                    admin_source = _find_admin_source(member)
                    granted[perm] = admin_source
            return granted, denied

        # Check each requested permission
        for perm in requested_permissions:
            if perm not in PERMISSION_FLAGS:
                flogger.warning(f"Unknown permission in guild check: {perm}")
                denied.add(perm)
                continue

            if check_permission(perms_value, perm):
                # Find the source of this permission
                source = _find_permission_source(member, perm)
                granted[perm] = source
            else:
                flogger.debug(f"Guild permission denied for member {getattr(member, 'id', 'unknown')}: {perm}")
                denied.add(perm)

    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Conservative: deny all on error
        member_id = getattr(member, "id", "unknown")
        flogger.error(f"Error evaluating guild permissions for member {member_id}: {exc}")
        denied.update(requested_permissions)

    return granted, denied


def evaluate_user_channel_permissions(
    member: "Any", channel: "Any", requested_permissions: list[str]
) -> tuple[dict[str, PermissionSource], set[str]]:
    """
    Evaluate channel-level permissions for a user.

    Returns:
        Tuple of (granted_permissions_dict, denied_permissions_set)
    """
    member_id = getattr(member, "id", "unknown")
    channel_id = getattr(channel, "id", "unknown")
    flogger.debug(
        f"Evaluating channel permissions for member {member_id} in channel {channel_id}; "
        f"requested={requested_permissions}"
    )

    granted = {}
    denied = set()

    try:
        # Get effective channel permissions
        channel_perms = channel.permissions_for(member)
        perms_value = getattr(channel_perms, "value", 0)

        # Check administrator first (grants everything)
        if has_administrator(perms_value):
            flogger.debug(f"Member {member_id} has ADMINISTRATOR in channel {channel_id}")
            for perm in requested_permissions:
                if perm in PERMISSION_FLAGS:
                    admin_source = _find_admin_source(member)
                    granted[perm] = admin_source
            return granted, denied

        # Check each requested permission
        for perm in requested_permissions:
            if perm not in PERMISSION_FLAGS:
                flogger.warning(f"Unknown permission in channel check: {perm}")
                denied.add(perm)
                continue

            # Check if permission applies to this channel type
            perm_data = PERMISSION_FLAGS[perm]
            channel_type = getattr(channel, "type", None)
            channel_type_name = getattr(channel_type, "name", "unknown") if channel_type else "unknown"

            # Map Discord channel types to our permission channel types
            type_mapping = {
                "text": "text",
                "voice": "voice",
                "stage_voice": "stage",
                "category": "category",
                "forum": "text",  # Forum channels use text permissions
            }

            mapped_type = type_mapping.get(channel_type_name, "text")
            if perm_data["channel_types"] and mapped_type not in perm_data["channel_types"]:
                flogger.debug(
                    f"Permission {perm} denied for member {member_id} in channel {channel_id}: "
                    f"not applicable to channel type {mapped_type}"
                )
                denied.add(perm)
                continue

            if check_permission(perms_value, perm):
                # Find the source of this permission
                source = _find_channel_permission_source(member, channel, perm)
                granted[perm] = source
            else:
                flogger.debug(f"Channel permission denied for member {member_id}: {perm} in {channel_id}")
                denied.add(perm)

    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Conservative: deny all on error
        flogger.error(f"Error evaluating channel permissions for member {member_id} in {channel_id}: {exc}")
        denied.update(requested_permissions)

    return granted, denied


def evaluate_role_guild_permissions(
    role: "Any", _guild: "Any", requested_permissions: list[str]
) -> tuple[dict[str, PermissionSource], set[str]]:
    """
    Evaluate guild-level permissions for a role.

    Returns:
        Tuple of (granted_permissions_dict, denied_permissions_set)
    """
    role_id = getattr(role, "id", "unknown")
    role_name = getattr(role, "name", "unknown")
    flogger.debug(
        f"Evaluating guild permissions for role {role_name} (id={role_id}); requested={requested_permissions}"
    )

    granted = {}
    denied = set()

    try:
        # Get role permissions
        role_perms = role.permissions
        perms_value = getattr(role_perms, "value", 0)

        # Check administrator first
        if has_administrator(perms_value):
            flogger.debug(f"Role {role_name} (id={role_id}) has ADMINISTRATOR permission")
            for perm in requested_permissions:
                if perm in PERMISSION_FLAGS:
                    source = PermissionSource("role", role.name, role.id)
                    granted[perm] = source
            return granted, denied

        # Check each requested permission
        for perm in requested_permissions:
            if perm not in PERMISSION_FLAGS:
                flogger.warning(f"Unknown permission in role guild check: {perm}")
                denied.add(perm)
                continue

            if check_permission(perms_value, perm):
                source = PermissionSource("role", role.name, role.id)
                granted[perm] = source
            else:
                flogger.debug(f"Permission denied for role {role_name} (id={role_id}): {perm}")
                denied.add(perm)

    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Error evaluating guild permissions for role {role_name} (id={role_id}): {exc}")
        denied.update(requested_permissions)

    return granted, denied


def evaluate_role_channel_permissions(
    role: "Any", channel: "Any", requested_permissions: list[str]
) -> tuple[dict[str, PermissionSource], set[str]]:
    """
    Evaluate channel-level permissions for a role.

    Returns:
        Tuple of (granted_permissions_dict, denied_permissions_set)
    """
    role_id = getattr(role, "id", "unknown")
    role_name = getattr(role, "name", "unknown")
    channel_id = getattr(channel, "id", "unknown")
    flogger.debug(
        f"Evaluating channel permissions for role {role_name} (id={role_id}) in channel {channel_id}; "
        f"requested={requested_permissions}"
    )

    granted = {}
    denied = set()

    try:
        # Get base role permissions
        base_perms = getattr(role.permissions, "value", 0)

        # Get channel overwrites for this role
        overwrite = channel.overwrites.get(role)
        allow_val = deny_val = 0

        if overwrite:
            flogger.debug(f"Found overwrite for role {role_name} (id={role_id}) in channel {channel_id}")
            try:
                allow_perm, deny_perm = overwrite.pair()
                allow_val = getattr(allow_perm, "value", 0)
                deny_val = getattr(deny_perm, "value", 0)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                flogger.debug(f"Error extracting overwrite pair for role {role_name}: {exc}; using fallback")
                # Fallback: extract from overwrite attributes
                for perm_name, perm_data in PERMISSION_FLAGS.items():
                    attr_name = perm_name.lower()
                    value = getattr(overwrite, attr_name, None)
                    if value is True:
                        allow_val |= perm_data["value"]
                    elif value is False:
                        deny_val |= perm_data["value"]

        # Calculate effective permissions
        effective_perms = calculate_effective_permissions(base_perms, allow_val, deny_val)

        # Check administrator
        if has_administrator(effective_perms):
            flogger.debug(f"Role {role_name} (id={role_id}) has ADMINISTRATOR in channel {channel_id}")
            for perm in requested_permissions:
                if perm in PERMISSION_FLAGS:
                    source = PermissionSource("role", role.name, role.id)
                    granted[perm] = source
            return granted, denied

        # Check each requested permission
        for perm in requested_permissions:
            if perm not in PERMISSION_FLAGS:
                flogger.warning(f"Unknown permission in role channel check: {perm}")
                denied.add(perm)
                continue

            # Check if permission applies to this channel type
            perm_data = PERMISSION_FLAGS[perm]
            channel_type = getattr(channel, "type", None)
            channel_type_name = getattr(channel_type, "name", "unknown") if channel_type else "unknown"

            type_mapping = {
                "text": "text",
                "voice": "voice",
                "stage_voice": "stage",
                "category": "category",
                "forum": "text",
            }

            mapped_type = type_mapping.get(channel_type_name, "text")
            if perm_data["channel_types"] and mapped_type not in perm_data["channel_types"]:
                flogger.debug(
                    f"Permission {perm} denied for role {role_name} in channel {channel_id}: "
                    f"not applicable to channel type {mapped_type}"
                )
                denied.add(perm)
                continue

            if check_permission(effective_perms, perm):
                source = PermissionSource("role", role.name, role.id)
                granted[perm] = source
            else:
                flogger.debug(f"Permission denied for role {role_name} (id={role_id}): {perm}")
                denied.add(perm)

    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(
            f"Error evaluating channel permissions for role {role_name} (id={role_id}) in channel {channel_id}: {exc}"
        )
        denied.update(requested_permissions)

    return granted, denied


def _find_admin_source(member: "Any") -> PermissionSource:
    """Find which role grants administrator permission."""
    member_id = getattr(member, "id", "unknown")
    for role in reversed(member.roles):  # Highest role first
        if getattr(role.permissions, "administrator", False):
            role_name = getattr(role, "name", "unknown")
            if role_name == "@everyone":
                flogger.debug(f"Admin source for member {member_id}: @everyone role")
                return PermissionSource("everyone")
            role_id = getattr(role, "id", "unknown")
            flogger.debug(f"Admin source for member {member_id}: role {role_name} (id={role_id})")
            return PermissionSource("role", role.name, role.id)
    flogger.debug(f"No admin role found for member {member_id}; returning direct")
    return PermissionSource("direct")  # Fallback


def _find_permission_source(member: "Any", permission: str) -> PermissionSource:
    """Find the source of a specific permission for a member."""
    member_id = getattr(member, "id", "unknown")
    perm_bit = PERMISSION_FLAGS[permission]["value"]

    # Check roles from highest to lowest priority
    for role in reversed(member.roles):
        role_perms = getattr(role.permissions, "value", 0)
        if role_perms & perm_bit:
            role_name = getattr(role, "name", "unknown")
            if role_name == "@everyone":
                flogger.debug(f"Permission source for member {member_id} ({permission}): @everyone role")
                return PermissionSource("everyone")
            role_id = getattr(role, "id", "unknown")
            flogger.debug(f"Permission source for member {member_id} ({permission}): role {role_name} (id={role_id})")
            return PermissionSource("role", role.name, role.id)

    flogger.debug(f"No role source found for member {member_id} ({permission}); returning direct")
    return PermissionSource("direct")  # Fallback


def _find_channel_permission_source(member: "Any", channel: "Any", permission: str) -> PermissionSource:
    """Find the source of a channel permission for a member.

    This version is robust to channel.overwrites keys being different object
    instances; it searches by target.id and prefers direct (member) overwrites
    before checking role overwrites (highest role first).
    """
    member_id = getattr(member, "id", "unknown")
    channel_id = getattr(channel, "id", "unknown")
    flogger.debug(f"Finding channel permission source for member {member_id} in channel {channel_id} ({permission})")

    perm_bit = PERMISSION_FLAGS[permission]["value"]

    # Helper to safely get an object's id
    def _get_id(obj):
        try:
            return getattr(obj, "id", None)
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    member_id_val = _get_id(member)

    # 1) Look for a user/member-specific overwrite by id (not by object identity)
    try:
        for target, user_overwrite in getattr(channel, "overwrites", {}).items():
            target_id = _get_id(target)
            # treat as member target if it has 'roles' attribute or target id matches member id
            if target_id is not None and target_id == member_id_val:
                try:
                    allow_perm, _ = user_overwrite.pair()
                    if getattr(allow_perm, "value", 0) & perm_bit:
                        flogger.debug(f"Found direct member overwrite for {member_id} in {channel_id} ({permission})")
                        return PermissionSource("direct")
                except Exception:  # pylint: disable=broad-exception-caught
                    if getattr(user_overwrite, permission.lower(), None) is True:
                        flogger.debug(f"Found direct member overwrite for {member_id} in {channel_id} ({permission})")
                        return PermissionSource("direct")
                # If a user overwrite exists but doesn't allow this perm, still continue to check role overwrites
                break
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Non-fatal: continue to role checks
        flogger.debug(f"Error checking member overwrite for {member_id} in {channel_id}: {exc}")

    # 2) Check role overwrites from highest to lowest priority (member.roles is ordered low->high)
    try:
        for role in reversed(getattr(member, "roles", []) or []):
            role_id = _get_id(role)
            role_name = getattr(role, "name", "unknown")
            # Try direct dict lookup first (fast path)
            role_overwrite = None
            try:
                role_overwrite = channel.overwrites.get(role)
            except Exception:  # pylint: disable=broad-exception-caught
                role_overwrite = None

            # Fallback: search overwrites by matching target id and target looks like a role
            if role_overwrite is None and role_id is not None:
                try:
                    for targ, ow in getattr(channel, "overwrites", {}).items():
                        targ_id = _get_id(targ)
                        # treat as role-like when it has 'permissions' attribute
                        if targ_id == role_id and hasattr(targ, "permissions"):
                            role_overwrite = ow
                            break
                except Exception:  # pylint: disable=broad-exception-caught
                    role_overwrite = None

            if role_overwrite:
                try:
                    allow_perm, _ = role_overwrite.pair()
                    if getattr(allow_perm, "value", 0) & perm_bit:
                        if role_name == "@everyone":
                            flogger.debug(f"Found role overwrite for @everyone in {channel_id} ({permission})")
                            return PermissionSource("everyone")
                        flogger.debug(
                            f"Found role overwrite for {role_name} (id={role_id}) in {channel_id} ({permission})"
                        )
                        return PermissionSource("role", role.name, role.id)
                except Exception:  # pylint: disable=broad-exception-caught
                    if getattr(role_overwrite, permission.lower(), None) is True:
                        if role_name == "@everyone":
                            flogger.debug(f"Found role overwrite for @everyone in {channel_id} ({permission})")
                            return PermissionSource("everyone")
                        flogger.debug(
                            f"Found role overwrite for {role_name} (id={role_id}) in {channel_id} ({permission})"
                        )
                        return PermissionSource("role", role.name, role.id)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Non-fatal; fall back to guild perms
        flogger.debug(f"Error checking role overwrites for member {member_id} in {channel_id}: {exc}")

    # 3) Fall back to guild permissions/source detection
    flogger.debug(f"No overwrite found for {member_id} in {channel_id} ({permission}); using guild perms")
    return _find_permission_source(member, permission)
