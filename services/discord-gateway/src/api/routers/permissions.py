"""
Permission router for Discord Gateway API.

This module provides REST endpoints for managing Discord permissions
including channel overwrites, permission reference data, and utility operations.
"""

from typing import Union
from fastapi import APIRouter, HTTPException, Request, status, Query

import shared.bblogger as bblogger
from api.schemas.permission_schemas import (
    PermissionOverwriteListResponse,
    PermissionOverwriteDetailResponse,
    PermissionOverwriteRequest,
    PermissionOverwriteListRequest,
    PermissionFlagListResponse,
    PermissionFlag,
    PermissionCheckResponse,
    NamesToValueRequest,
    NamesToValueResponse,
    ValueToNamesRequest,
    ValueToNamesResponse,
    CalculatePermissionsRequest,
    CalculatePermissionsResponse,
)
from api.schemas.base_schemas import SuccessResponse, DeleteResponse
from utils.discord_converters import PermissionConverter
from utils.discord_helpers import resolve_bot, get_entity_or_404, handle_discord_exception
from utils.permission_utils import (
    PERMISSION_FLAGS,
    get_all_permissions,
    get_role_permissions,
    get_user_permissions,
    get_channel_permissions,
    get_category_permissions,
    create_permission_overwrite,
    combine_permissions,
    get_permission_names_by_value,
    calculate_effective_permissions,
    has_channel_permission,
    has_guild_permission,
)

flogger = bblogger.get_logger("gateway-permission-router")

router = APIRouter(
    tags=["permissions"],
    responses={
        404: {"description": "Channel or permission target not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"},
    },
)

# -------------------------------------------------------------------
# GET endpoints
# -------------------------------------------------------------------

@router.get(
    "/channels/{channel_id}/permissions",
    response_model=PermissionOverwriteListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Channel Permission Overwrites",
    description="Get all permission overwrites for a channel",
)
async def get_channel_permission_overwrites(
    request: Request, channel_id: int
) -> PermissionOverwriteListResponse:
    flogger.info(f"get_channel_permission_overwrites called for channel_id={channel_id}")
    flogger.debug(f"Starting permission overwrite retrieval for channel {channel_id}")
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")

        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, channel_id, "Channel"
        )
        flogger.debug(f"Channel retrieved: {channel.name}")

        overwrites = []
        flogger.debug(f"Processing {len(channel.overwrites)} permission overwrites")
        for target, overwrite in channel.overwrites.items():
            flogger.trace(f"Processing overwrite for: {target.name} ({target.id})")
            payload = PermissionConverter.overwrite_to_payload(target, overwrite)
            overwrites.append(payload)

        flogger.info(f"Successfully retrieved {len(overwrites)} overwrites for channel {channel.name}")
        return PermissionOverwriteListResponse(status="success", overwrites=overwrites)

    except HTTPException:
        flogger.warning(f"HTTP exception in get_channel_permission_overwrites for {channel_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_channel_permission_overwrites for {channel_id}: {exc}")
        await handle_discord_exception("get channel permission overwrites", exc)


@router.get(
    "/channels/{channel_id}/permissions/{target_id}",
    response_model=PermissionOverwriteDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Specific Permission Overwrite",
    description="Get a specific permission overwrite for a channel",
)
async def get_channel_permission_overwrite(
    request: Request, channel_id: int, target_id: int
) -> PermissionOverwriteDetailResponse:
    flogger.info(f"get_channel_permission_overwrite called for channel_id={channel_id}, target_id={target_id}")
    flogger.debug("Starting specific overwrite retrieval")
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")

        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, channel_id, "Channel"
        )
        flogger.debug(f"Channel retrieved: {channel.name}")

        target = None
        overwrite = None
        flogger.debug(f"Searching for overwrite target {target_id}")
        for ow_target, ow_overwrite in channel.overwrites.items():
            if ow_target.id == target_id:
                target, overwrite = ow_target, ow_overwrite
                flogger.trace(f"Found overwrite target: {target.name}")
                break

        if not target:
            flogger.error(f"Overwrite for {target_id} not found in channel {channel_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Permission overwrite for {target_id} not found in channel {channel_id}",
            )

        payload = PermissionConverter.overwrite_to_payload(target, overwrite)
        flogger.trace("Conversion to payload completed")
        flogger.info(f"Retrieved overwrite for {target.name} in {channel.name}")
        return PermissionOverwriteDetailResponse(status="success", overwrite=payload)

    except HTTPException:
        flogger.warning(f"HTTP exception in get_channel_permission_overwrite for {target_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_channel_permission_overwrite for {target_id}: {exc}")
        await handle_discord_exception("get channel permission overwrite", exc)


@router.get(
    "/permissions",
    response_model=PermissionFlagListResponse,
    status_code=status.HTTP_200_OK,
    summary="List All Discord Permissions",
    description="Get a list of all Discord permissions with metadata",
)
async def list_all_permissions() -> PermissionFlagListResponse:
    flogger.info("list_all_permissions called")
    flogger.debug("Retrieving all permission flags")
    try:
        data = get_all_permissions()
        perms = [PermissionFlag(**p) for p in data]
        flogger.info(f"Retrieved {len(perms)} permission flags")
        return PermissionFlagListResponse(status="success", permissions=perms)
    except Exception as exc:
        flogger.exception("Error in list_all_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list permissions: {exc}",
        )


@router.get(
    "/permissions/roles",
    response_model=PermissionFlagListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Role Permissions",
    description="Get permissions that can be assigned to roles",
)
async def list_role_permissions() -> PermissionFlagListResponse:
    flogger.info("list_role_permissions called")
    flogger.debug("Retrieving role permission flags")
    try:
        data = get_role_permissions()
        perms = [PermissionFlag(**p) for p in data]
        flogger.info(f"Retrieved {len(perms)} role permissions")
        return PermissionFlagListResponse(status="success", permissions=perms)
    except Exception as exc:
        flogger.exception("Error in list_role_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list role permissions: {exc}",
        )


@router.get(
    "/permissions/users",
    response_model=PermissionFlagListResponse,
    status_code=status.HTTP_200_OK,
    summary="List User Permissions",
    description="Get permissions usable in overwrites for users",
)
async def list_user_permissions() -> PermissionFlagListResponse:
    flogger.info("list_user_permissions called")
    flogger.debug("Retrieving user permission flags")
    try:
        data = get_user_permissions()
        perms = [PermissionFlag(**p) for p in data]
        flogger.info(f"Retrieved {len(perms)} user permissions")
        return PermissionFlagListResponse(status="success", permissions=perms)
    except Exception as exc:
        flogger.exception("Error in list_user_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list user permissions: {exc}",
        )


@router.get(
    "/permissions/channels",
    response_model=PermissionFlagListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Channel Permissions",
    description="Get permissions applicable to channels",
)
async def list_channel_permissions() -> PermissionFlagListResponse:
    flogger.info("list_channel_permissions called")
    flogger.debug("Retrieving channel permission flags")
    try:
        data = get_channel_permissions()
        perms = [PermissionFlag(**p) for p in data]
        flogger.info(f"Retrieved {len(perms)} channel permissions")
        return PermissionFlagListResponse(status="success", permissions=perms)
    except Exception as exc:
        flogger.exception("Error in list_channel_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list channel permissions: {exc}",
        )


@router.get(
    "/permissions/categories",
    response_model=PermissionFlagListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Category Permissions",
    description="Get permissions applicable to categories",
)
async def list_category_permissions() -> PermissionFlagListResponse:
    flogger.info("list_category_permissions called")
    flogger.debug("Retrieving category permission flags")
    try:
        data = get_category_permissions()
        perms = [PermissionFlag(**p) for p in data]
        flogger.info(f"Retrieved {len(perms)} category permissions")
        return PermissionFlagListResponse(status="success", permissions=perms)
    except Exception as exc:
        flogger.exception("Error in list_category_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list category permissions: {exc}",
        )


# -------------------------------------------------------------------
# Extended utility endpoints
# -------------------------------------------------------------------

@router.get(
    "/channels/{channel_id}/permissions/{target_id}/check",
    response_model=PermissionCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Check Channel Permission",
    description="Check if a member has a specific permission in a channel",
)
async def check_channel_permission_endpoint(
    request: Request,
    channel_id: int,
    target_id: int,
    permission: str = Query(..., description="Permission name (uppercase, e.g. SEND_MESSAGES)"),
) -> PermissionCheckResponse:
    flogger.info(f"check_channel_permission called for channel={channel_id}, target={target_id}, perm={permission}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")
        guild = channel.guild
        member = await get_entity_or_404(
            guild.get_member,
            guild.fetch_member,
            target_id,
            "Member"
        )
        allowed = has_channel_permission(member, channel, permission)
        return PermissionCheckResponse(allowed=allowed)
    except HTTPException:
        raise
    except Exception as exc:
        await handle_discord_exception("check channel permission", exc)

@router.get(
    "/guilds/{guild_id}/members/{member_id}/permissions/check",
    response_model=PermissionCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Check Guild Permission",
    description="Check if a guild member has a specific guild-level permission"
)
async def check_guild_permission_endpoint(
    request: Request,
    guild_id: int,
    member_id: int,
    permission: str = Query(..., description="Permission name (uppercase, e.g. BAN_MEMBERS)")
) -> PermissionCheckResponse:
    flogger.info(f"check_guild_permission called for guild={guild_id}, member={member_id}, perm={permission}")
    try:
        bot = await resolve_bot(request)
        # fetch guild
        guild = await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")
        # now get the member
        member = await get_entity_or_404(
            guild.get_member,
            guild.fetch_member,
            member_id,
            "Member"
        )
        member = guild.get_member(member_id) or await guild.fetch_member(member_id)
        allowed = has_guild_permission(member, permission)
        return PermissionCheckResponse(allowed=allowed)
    except HTTPException:
        raise
    except Exception as exc:
        await handle_discord_exception("check guild permission", exc)

@router.post(
    "/permissions/convert/names-to-value",
    response_model=NamesToValueResponse,
    status_code=status.HTTP_200_OK,
    summary="Convert Permission Names to Bitfield",
    description="Combine a list of permission names into a single bitfield",
)
async def convert_names_to_value(body: NamesToValueRequest) -> NamesToValueResponse:
    flogger.debug(f"convert_names_to_value: {body.names}")
    try:
        bit_values = [PERMISSION_FLAGS[name]["value"] for name in body.names]
        value = combine_permissions(*bit_values)
        return NamesToValueResponse(value=value)
    except KeyError as ke:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown permission: {ke}")


@router.post(
    "/permissions/convert/value-to-names",
    response_model=ValueToNamesResponse,
    status_code=status.HTTP_200_OK,
    summary="Convert Bitfield to Permission Names",
    description="Expand a bitfield into the list of granted permission names",
)
async def convert_value_to_names(body: ValueToNamesRequest) -> ValueToNamesResponse:
    flogger.debug(f"convert_value_to_names: {body.value}")
    names = get_permission_names_by_value(body.value)
    return ValueToNamesResponse(names=names)


@router.post(
    "/permissions/calculate",
    response_model=CalculatePermissionsResponse,
    status_code=status.HTTP_200_OK,
    summary="Calculate Effective Permissions",
    description="Apply allow/deny overwrites to a base permissions bitfield",
)
async def calculate_permissions_endpoint(body: CalculatePermissionsRequest) -> CalculatePermissionsResponse:
    flogger.debug(f"calculate_permissions: base={body.base}, allow={body.allow}, deny={body.deny}")
    effective = calculate_effective_permissions(body.base, body.allow, body.deny)
    return CalculatePermissionsResponse(effective=effective)


# -------------------------------------------------------------------
# PUT endpoints
# -------------------------------------------------------------------

@router.put(
    "/channels/{channel_id}/permissions",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Replace Channel Permission Overwrites",
    description="Replace all permission overwrites for a channel",
)
async def replace_channel_permission_overwrites(
    request: Request,
    channel_id: int,
    permissions_data: PermissionOverwriteListRequest,
) -> SuccessResponse:
    flogger.info(f"replace_channel_permission_overwrites called for channel_id={channel_id}")
    flogger.debug(f"Starting replacement with {len(permissions_data.overwrites)} overwrites")
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")

        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, channel_id, "Channel"
        )
        flogger.debug(f"Channel retrieved: {channel.name}")

        guild = channel.guild
        flogger.trace(f"Guild retrieved: {guild.name}")

        # Clear existing overwrites
        existing = list(channel.overwrites.keys())
        flogger.debug(f"Clearing {len(existing)} existing overwrites")
        for tgt in existing:
            flogger.trace(f"Clearing overwrite for: {tgt.name}")
            await channel.set_permissions(tgt, overwrite=None)

        # Set new overwrites
        flogger.debug(f"Setting {len(permissions_data.overwrites)} new overwrites")
        for ow in permissions_data.overwrites:
            tgt_id, tgt_type = ow.target_id, ow.type
            allow, deny = ow.allow or 0, ow.deny or 0

            flogger.trace(f"Processing overwrite for {tgt_type} {tgt_id}: allow={hex(allow)}, deny={hex(deny)}")
            if tgt_type == "role":
                target = guild.get_role(tgt_id)
                if not target:
                    flogger.error(f"Role {tgt_id} not found")
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Role {tgt_id} not found")
            else:
                target = guild.get_member(tgt_id) or await guild.fetch_member(tgt_id)

            po = create_permission_overwrite(allow=allow, deny=deny)
            flogger.trace(f"Setting overwrite for {target.name}")
            await channel.set_permissions(target, overwrite=po)

        msg = f"Permissions replaced for channel {channel.name}"
        flogger.info(msg)
        return SuccessResponse(status="updated", message=msg)

    except HTTPException:
        flogger.warning(f"HTTP exception in replace_channel_permission_overwrites for {channel_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in replace_channel_permission_overwrites for {channel_id}: {exc}")
        await handle_discord_exception("replace channel permission overwrites", exc)


@router.put(
    "/channels/{channel_id}/permissions/{target_id}",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Set Specific Permission Overwrite",
    description="Set a specific permission overwrite for a channel",
)
async def set_channel_permission_overwrite(
    request: Request,
    channel_id: int,
    target_id: int,
    permissions_data: PermissionOverwriteRequest,
) -> SuccessResponse:
    flogger.info(f"set_channel_permission_overwrite called for channel_id={channel_id}, target_id={target_id}")
    flogger.debug("Starting specific permission overwrite setting")
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")

        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, channel_id, "Channel"
        )
        flogger.debug(f"Channel retrieved: {channel.name}")

        guild = channel.guild
        flogger.trace(f"Guild retrieved: {guild.name}")

        # Find role or member
        target = guild.get_role(target_id)
        if not target:
            flogger.trace(f"Role {target_id} not found, trying member")
            target = guild.get_member(target_id) or await guild.fetch_member(target_id)
        if not target:
            flogger.error(f"Role or member {target_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role or member {target_id} not found"
            )

        flogger.debug(f"Target retrieved: {target.name}")

        # Create and set overwrite
        allow = permissions_data.allow or 0
        deny = permissions_data.deny or 0
        flogger.trace(f"Setting overwrite: allow={hex(allow)}, deny={hex(deny)}")

        po = create_permission_overwrite(allow=allow, deny=deny)
        await channel.set_permissions(target, overwrite=po)

        tgt_type = "role" if hasattr(target, 'permissions') else "member"
        message = f"Permission overwrite set for {tgt_type} {target.name} in channel {channel.name}"
        flogger.info(message)
        return SuccessResponse(status="updated", message=message)

    except HTTPException:
        flogger.warning(f"HTTP exception in set_channel_permission_overwrite for target {target_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in set_channel_permission_overwrite for target {target_id}: {exc}")
        await handle_discord_exception("set channel permission overwrite", exc)


@router.delete(
    "/channels/{channel_id}/permissions/{target_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Remove Permission Overwrite",
    description="Remove a permission overwrite from a channel",
)
async def remove_channel_permission_overwrite(
    request: Request,
    channel_id: int,
    target_id: int,
) -> DeleteResponse:
    flogger.info(f"remove_channel_permission_overwrite called for channel_id={channel_id}, target_id={target_id}")
    flogger.debug("Starting permission overwrite removal")
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")

        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, channel_id, "Channel"
        )
        flogger.debug(f"Channel retrieved: {channel.name}")

        # Find target in existing overwrites
        target = None
        flogger.debug(f"Searching for existing overwrite target {target_id}")
        for ow_target in channel.overwrites.keys():
            if ow_target.id == target_id:
                target = ow_target
                flogger.trace(f"Found existing overwrite target: {target.name}")
                break

        if not target:
            flogger.error(f"Permission overwrite for {target_id} not found in channel {channel_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Permission overwrite for {target_id} not found in channel {channel_id}"
            )

        tgt_type = "role" if hasattr(target, 'permissions') else "member"
        flogger.debug(f"Removing overwrite for {tgt_type} {target.name}")
        await channel.set_permissions(target, overwrite=None)

        message = f"Permission overwrite removed for {tgt_type} {target.name} from channel {channel.name}"
        flogger.info(message)
        return DeleteResponse(status="deleted", deleted=True, message=message)

    except HTTPException:
        flogger.warning(f"HTTP exception in remove_channel_permission_overwrite for target {target_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in remove_channel_permission_overwrite for target {target_id}: {exc}")
        await handle_discord_exception("remove channel permission overwrite", exc)