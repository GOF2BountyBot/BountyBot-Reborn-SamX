"""
Permission router for Discord Gateway API.

This module provides REST endpoints for managing Discord permissions
with simplified URIs and consolidated permission operations.
"""

from typing import Any, List, Tuple

from shared import bblogger
from api.schemas.base_schemas import DeleteResponse
from api.schemas.permission_schemas import (
    BotPermissionSummaryResponse,
    CalculatePermissionsRequest,
    CalculatePermissionsResponse,
    ComprehensivePermissionCheckData,
    ComprehensivePermissionCheckRequest,
    ComprehensivePermissionCheckResponse,
    NamesToValueRequest,
    NamesToValueResponse,
    PermissionFlagListResponse,
    PermissionGrant,
    PermissionGrantSource,
    PermissionOverwriteRequest,
    PermissionOverwriteResponse,
    ValueToNamesRequest,
    ValueToNamesResponse,
)
from fastapi import APIRouter, HTTPException, Request, status

from utils.discord_converters import PermissionConverter
from utils.discord_helpers import handle_discord_exception, resolve_bot
from utils.permission_utils import (
    PERMISSION_FLAGS,
    calculate_effective_permissions,
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
)

flogger = bblogger.get_logger("gateway-permission-router")

router = APIRouter(
    tags=["permissions"],
    responses={
        404: {"description": "Permission or target not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

# -------------------------
# Permission flag endpoints
# -------------------------
@router.get(
    "/permissions",
    response_model=PermissionFlagListResponse,
    status_code=status.HTTP_200_OK,
    summary="List All Discord Permissions",
    description="Get a list of all Discord permissions with metadata"
)
async def list_all_permissions() -> PermissionFlagListResponse:
    """List all Discord permissions."""
    flogger.info("list_all_permissions called")
    try:
        data = get_all_permissions()
        perms = [
            {"name": p["name"], "value": p["value"],
             "description": p["description"], "channel_types": p["channel_types"]}
            for p in data
        ]

        flogger.info(f"Retrieved {len(perms)} permission flags")
        return PermissionFlagListResponse(
            status="success",
            data=perms
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.exception("Error in list_all_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list permissions: {exc}"
        ) from exc


@router.get(
    "/permissions/roles",
    response_model=PermissionFlagListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Role Permissions",
    description="Get permissions that can be assigned to roles"
)
async def list_role_permissions() -> PermissionFlagListResponse:
    """List permissions that can be assigned to roles."""
    flogger.info("list_role_permissions called")
    try:
        data = get_role_permissions()
        perms = [
            {"name": p["name"], "value": p["value"],
             "description": p["description"], "channel_types": p["channel_types"]}
            for p in data
        ]

        flogger.info(f"Retrieved {len(perms)} role permissions")
        return PermissionFlagListResponse(
            status="success",
            data=perms
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.exception("Error in list_role_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list role permissions: {exc}"
        ) from exc


@router.get(
    "/permissions/users",
    response_model=PermissionFlagListResponse,
    status_code=status.HTTP_200_OK,
    summary="List User Permissions",
    description="Get permissions usable in overwrites for users"
)
async def list_user_permissions() -> PermissionFlagListResponse:
    """List permissions usable in overwrites for users."""
    flogger.info("list_user_permissions called")
    try:
        data = get_user_permissions()
        perms = [
            {"name": p["name"], "value": p["value"],
             "description": p["description"], "channel_types": p["channel_types"]}
            for p in data
        ]

        flogger.info(f"Retrieved {len(perms)} user permissions")
        return PermissionFlagListResponse(
            status="success",
            data=perms
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.exception("Error in list_user_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list user permissions: {exc}"
        ) from exc


@router.get(
    "/permissions/channels",
    response_model=PermissionFlagListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Channel Permissions",
    description="Get permissions applicable to channels"
)
async def list_channel_permissions() -> PermissionFlagListResponse:
    """List permissions applicable to channels."""
    flogger.info("list_channel_permissions called")
    try:
        data = get_channel_permissions()
        perms = [
            {"name": p["name"], "value": p["value"],
             "description": p["description"], "channel_types": p["channel_types"]}
            for p in data
        ]

        flogger.info(f"Retrieved {len(perms)} channel permissions")
        return PermissionFlagListResponse(
            status="success",
            data=perms
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.exception("Error in list_channel_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list channel permissions: {exc}"
        ) from exc


@router.get(
    "/permissions/categories",
    response_model=PermissionFlagListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Category Permissions",
    description="Get permissions applicable to categories"
)
async def list_category_permissions() -> PermissionFlagListResponse:
    """List permissions applicable to categories."""
    flogger.info("list_category_permissions called")
    try:
        data = get_category_permissions()
        perms = [
            {"name": p["name"], "value": p["value"],
             "description": p["description"], "channel_types": p["channel_types"]}
            for p in data
        ]

        flogger.info(f"Retrieved {len(perms)} category permissions")
        return PermissionFlagListResponse(
            status="success",
            data=perms
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.exception("Error in list_category_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list category permissions: {exc}"
        ) from exc


# -------------------------
# Overwrite endpoints
# -------------------------
@router.get(
    "/permissions/{permission_id}",
    response_model=PermissionOverwriteResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Permission Overwrite",
    description="Get a specific permission overwrite"
)
async def get_permission_overwrite(request: Request, permission_id: str) -> PermissionOverwriteResponse:
    """Get a specific permission overwrite by composite ID."""
    flogger.info(f"get_permission_overwrite called for permission_id={permission_id}")
    try:
        # Parse composite ID (format: channel_id:target_id)
        try:
            channel_id, target_id = permission_id.split(":")
            channel_id, target_id = int(channel_id), int(target_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="permission_id must be in format 'channel_id:target_id'"
            ) from exc

        bot = await resolve_bot(request)

        # Find the channel and overwrite
        channel = bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {channel_id} not found"
                ) from exc

        # Find the specific overwrite
        target = None
        overwrite = None
        for ow_target, ow_overwrite in channel.overwrites.items():
            if ow_target.id == target_id:
                target, overwrite = ow_target, ow_overwrite
                break

        if not target:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Permission overwrite for {target_id} not found in channel {channel_id}"
            )

        overwrite_data = PermissionConverter.overwrite_to_payload(target, overwrite, channel_id)
        overwrite_data.id = permission_id

        flogger.info(f"Retrieved permission overwrite {permission_id}")
        return PermissionOverwriteResponse(
            status="success",
            data=overwrite_data
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in get_permission_overwrite: {exc}")
        await handle_discord_exception("get permission overwrite", exc)


@router.put(
    "/permissions/{permission_id}",
    response_model=PermissionOverwriteResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Permission Overwrite",
    description="Update a specific permission overwrite"
)
async def update_permission_overwrite(
    request: Request, permission_id: str, permissions_data: PermissionOverwriteRequest
) -> PermissionOverwriteResponse:
    """Update a specific permission overwrite by composite ID."""
    flogger.info(f"update_permission_overwrite called for permission_id={permission_id}")
    try:
        # Parse composite ID
        try:
            channel_id, target_id = permission_id.split(":")
            channel_id, target_id = int(channel_id), int(target_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="permission_id must be in format 'channel_id:target_id'"
            ) from exc

        bot = await resolve_bot(request)

        # Find the channel
        channel = bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {channel_id} not found"
                ) from exc

        guild = channel.guild

        # Find role or member
        target = guild.get_role(target_id)
        if not target:
            target = guild.get_member(target_id)
            if not target:
                try:
                    target = await guild.fetch_member(target_id)
                except Exception as exc:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Role or member {target_id} not found"
                    ) from exc

        # Create and set overwrite
        allow = permissions_data.allow or 0
        deny = permissions_data.deny or 0
        overwrite = create_permission_overwrite(allow=allow, deny=deny)
        await channel.set_permissions(target, overwrite=overwrite)

        # Return updated overwrite
        updated_overwrite_data = PermissionConverter.overwrite_to_payload(target, overwrite, channel.id)
        updated_overwrite_data.id = permission_id

        flogger.info(f"Updated permission overwrite {permission_id}")
        return PermissionOverwriteResponse(
            status="updated",
            data=updated_overwrite_data
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in update_permission_overwrite: {exc}")
        await handle_discord_exception("update permission overwrite", exc)


@router.delete(
    "/permissions/{permission_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Remove Permission Overwrite",
    description="Remove a permission overwrite"
)
async def remove_permission_overwrite(request: Request, permission_id: str) -> DeleteResponse:
    """Remove a permission overwrite by composite ID."""
    flogger.info(f"remove_permission_overwrite called for permission_id={permission_id}")
    try:
        # Parse composite ID
        try:
            channel_id, target_id = permission_id.split(":")
            channel_id, target_id = int(channel_id), int(target_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="permission_id must be in format 'channel_id:target_id'"
            ) from exc

        bot = await resolve_bot(request)

        # Find the channel
        channel = bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {channel_id} not found"
                ) from exc

        # Find target in existing overwrites
        target = None
        for ow_target in channel.overwrites.keys():
            if ow_target.id == target_id:
                target = ow_target
                break

        if not target:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Permission overwrite for {target_id} not found in channel {channel_id}"
            )

        target_type = "role" if hasattr(target, 'permissions') else "member"
        await channel.set_permissions(target, overwrite=None)

        message = f"Permission overwrite removed for {target_type} {target.name} from channel {channel.name}"
        flogger.info(message)

        return DeleteResponse(
            status="deleted",
            deleted=True,
            message=message
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in remove_permission_overwrite: {exc}")
        await handle_discord_exception("remove permission overwrite", exc)


# -------------------------
# Small convenience endpoints
# -------------------------
@router.post(
    "/permissions/convert/names-to-value",
    response_model=NamesToValueResponse,
    status_code=status.HTTP_200_OK,
    summary="Convert Permission Names to Bitfield",
    description="Combine a list of permission names into a single bitfield"
)
async def convert_names_to_value(body: NamesToValueRequest) -> NamesToValueResponse:
    """Convert a list of permission names to a bitfield value."""
    flogger.info(f"convert_names_to_value called with names={body.names}")

    if not body.names:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="names list must contain at least one permission"
        )

    # Validate each permission name
    for name in body.names:
        if name not in PERMISSION_FLAGS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown permission: {name}"
            )

    # Combine bit values
    bit_values = [PERMISSION_FLAGS[name]["value"] for name in body.names]
    value = combine_permissions(*bit_values)

    flogger.info(f"convert_names_to_value: combined value=0x{value:x}")
    return NamesToValueResponse(
        status="success",
        data={"value": value}
    )


@router.post(
    "/permissions/convert/value-to-names",
    response_model=ValueToNamesResponse,
    status_code=status.HTTP_200_OK,
    summary="Convert Bitfield to Permission Names",
    description="Expand a bitfield into the list of granted permission names"
)
async def convert_value_to_names(body: ValueToNamesRequest) -> ValueToNamesResponse:
    """Convert a bitfield to permission names."""
    flogger.info(f"convert_value_to_names called with value={body.value}")
    names = get_permission_names_by_value(body.value)
    return ValueToNamesResponse(
        status="success",
        data={"names": names}
    )


@router.post(
    "/permissions/calculate",
    response_model=CalculatePermissionsResponse,
    status_code=status.HTTP_200_OK,
    summary="Calculate Effective Permissions",
    description="Apply allow/deny overwrites to a base permissions bitfield"
)
async def calculate_permissions(body: CalculatePermissionsRequest) -> CalculatePermissionsResponse:
    """Calculate effective permissions."""
    flogger.info(f"calculate_permissions called: base={body.base}, allow={body.allow}, deny={body.deny}")
    effective = calculate_effective_permissions(body.base, body.allow or 0, body.deny or 0)
    return CalculatePermissionsResponse(
        status="success",
        data={"effective": effective}
    )


# -------------------------
# Consolidated /permissions/check endpoint
# -------------------------
@router.post(
    "/permissions/check",
    status_code=status.HTTP_200_OK,
    summary="Check Comprehensive Permissions",
    description=(
        "Single canonical endpoint: if 'permissions' is empty -> returns evaluate-style summary; "
        "otherwise returns detailed per-permission grants with sources."
    )
)
async def check_comprehensive_permissions(
    request: Request,
    check_request: ComprehensivePermissionCheckRequest
):
    """Check comprehensive permissions with detailed source tracking or return evaluate-style summary
    when permissions list is empty."""
    flogger.info(
        f"check_comprehensive_permissions called: subject={check_request.subject}, "
        f"target={check_request.target}"
    )

    # Validate permission names if provided
    provided_perms = check_request.permissions or []
    invalid_perms = [p for p in provided_perms if p not in PERMISSION_FLAGS]
    if invalid_perms:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown permission(s): {invalid_perms}"
        )

    try:
        bot = await resolve_bot(request)

        # Resolve target entity and guild context
        target_entity, target_guild = await _resolve_target_entity(bot, check_request.target)

        # Resolve subject entity within the guild context
        subject_entity = await _resolve_subject_entity(bot, target_guild, check_request.subject)

        # If no permissions were requested, act as the old /permissions/evaluate endpoint:
        if not provided_perms:
            # Determine applicable permission flags for the scope (guild vs channel-like)
            scope_is_guild = check_request.target.type.lower() == "guild"

            # Compute effective bitfield similar to previous evaluate_permissions logic
            effective_val = 0
            if scope_is_guild:
                # subject_entity is a member or role here (subject type validated earlier)
                if check_request.subject.type.lower() == "user":
                    member = subject_entity
                    # Prefer numeric .value when available, otherwise fallback to 0
                    effective_val = getattr(getattr(member, "guild_permissions", None), "value", 0)
                else:
                    role = subject_entity
                    effective_val = getattr(getattr(role, "permissions", None), "value", 0)
                candidate_perms = get_all_permissions()
            else:
                # channel-like
                channel = target_entity
                candidate_perms = get_channel_permissions()
                if check_request.subject.type.lower() == "user":
                    member = subject_entity
                    try:
                        perms = channel.permissions_for(member)
                        effective_val = getattr(perms, "value", 0)
                    except Exception:  # pylint: disable=broad-exception-caught
                        # fallback: build bitfield from flags defensively
                        for p in candidate_perms:
                            try:
                                if getattr(channel.permissions_for(member), p["name"].lower(), False):
                                    effective_val |= p["value"]
                            except Exception:  # pylint: disable=broad-exception-caught
                                continue
                else:
                    role = subject_entity
                    base_perms = getattr(getattr(role, "permissions", None), "value", 0)
                    overwrite = None
                    try:
                        overwrite = channel.overwrites.get(role)
                    except Exception:  # pylint: disable=broad-exception-caught
                        overwrite = None

                    if overwrite:
                        try:
                            allow_perm_obj, deny_perm_obj = overwrite.pair()
                            allow_val = getattr(allow_perm_obj, "value", 0)
                            deny_val = getattr(deny_perm_obj, "value", 0)
                        except Exception:  # pylint: disable=broad-exception-caught
                            allow_val = deny_val = 0
                            for p in candidate_perms:
                                attr = p["name"].lower()
                                v = getattr(overwrite, attr, None)
                                if v is True:
                                    allow_val |= p["value"]
                                elif v is False:
                                    deny_val |= p["value"]
                    else:
                        allow_val = deny_val = 0

                    effective_val = calculate_effective_permissions(base_perms, allow_val, deny_val)

            allowed_names = get_permission_names_by_value(effective_val)
            cand_names = [p["name"] for p in candidate_perms]
            denied_names = [n for n in cand_names if n not in allowed_names]

            flogger.info(f"/permissions/check (evaluate mode) result: effective=0x{effective_val:x}")
            return BotPermissionSummaryResponse(
                status="success",
                data={"base": effective_val, "allowed_names": allowed_names, "denied_names": denied_names}
            )

        # Otherwise, perform the detailed per-permission evaluation and source tracking
        # Determine whether the target is "guild" vs channel-like
        if check_request.target.type.lower() == "guild":
            if check_request.subject.type.lower() == "user":
                granted_dict, denied_set = evaluate_user_guild_permissions(
                    subject_entity, target_entity, provided_perms
                )
            else:
                granted_dict, denied_set = evaluate_role_guild_permissions(
                    subject_entity, target_entity, provided_perms
                )
        else:
            # channel, category, thread -> use channel-like evaluators
            if check_request.subject.type.lower() == "user":
                granted_dict, denied_set = evaluate_user_channel_permissions(
                    subject_entity, target_entity, provided_perms
                )
            else:
                granted_dict, denied_set = evaluate_role_channel_permissions(
                    subject_entity, target_entity, provided_perms
                )

        # Convert to response models
        granted_list: List[PermissionGrant] = []
        for perm, source in granted_dict.items():
            grant_source = PermissionGrantSource(
                type=source.type,
                role_name=source.role_name,
                role_id=source.role_id
            )
            granted_list.append(PermissionGrant(permission=perm, source=grant_source))

        denied_list = list(denied_set)
        all_allowed = len(denied_list) == 0

        flogger.info(
            f"/permissions/check detailed result: allowed={all_allowed}, "
            f"granted={len(granted_list)}, denied={len(denied_list)}"
        )
        return ComprehensivePermissionCheckResponse(
            status="success",
            data=ComprehensivePermissionCheckData(
                allowed=all_allowed,
                denied=denied_list,
                granted=granted_list
            )
        )

    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in check_comprehensive_permissions: {exc}")
        await handle_discord_exception("check comprehensive permissions", exc)


# -------------------------
# Helper resolvers
# -------------------------
async def _resolve_target_entity(bot: Any, target: Any) -> Tuple[Any, Any]:
    """Resolve target entity and return (entity, guild)."""
    target_type = target.type.lower()
    target_id = target.id

    if target_type == "guild":
        guild = bot.get_guild(target_id)
        if not guild:
            try:
                guild = await bot.fetch_guild(target_id)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Guild {target_id} not found"
                ) from exc
        return guild, guild

    if target_type in ("channel", "category", "thread"):
        channel = bot.get_channel(target_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(target_id)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {target_id} not found"
                ) from exc

        guild = getattr(channel, "guild", None)
        if not guild:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {target_id} is not in a guild"
            )

        return channel, guild

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unknown target type: {target.type}"
    )


async def _resolve_subject_entity(_bot: Any, guild: Any, subject: Any) -> Any:
    """Resolve subject entity within the guild context."""
    subject_type = subject.type.lower()
    subject_id = subject.id

    if subject_type == "user":
        member = guild.get_member(subject_id)
        if not member:
            try:
                member = await guild.fetch_member(subject_id)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Member {subject_id} not found in guild {guild.id}"
                ) from exc
        return member

    if subject_type == "role":
        role = guild.get_role(subject_id)
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {subject_id} not found in guild {guild.id}"
            )
        return role

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unknown subject type: {subject.type}"
    )
