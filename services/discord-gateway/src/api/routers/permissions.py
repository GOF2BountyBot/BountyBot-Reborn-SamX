"""
Permission router for Discord Gateway API.

This module provides REST endpoints for managing Discord permissions
with simplified URIs and consolidated permission operations.
"""

from fastapi import APIRouter, HTTPException, Request, status, Query
import shared.bblogger as bblogger
from api.schemas.permission_schemas import (
    PermissionOverwriteResponse, PermissionOverwriteRequest,
    PermissionFlagListResponse, PermissionCheckResponse,
    NamesToValueRequest, NamesToValueResponse,
    ValueToNamesRequest, ValueToNamesResponse,
    CalculatePermissionsRequest, CalculatePermissionsResponse,
    PermissionCheckRequest, MultiPermissionCheckResponse,
    BotPermissionSummaryResponse
)
from api.schemas.base_schemas import DeleteResponse
from utils.discord_converters import PermissionConverter
from utils.discord_helpers import resolve_bot, handle_discord_exception
from utils.permission_utils import (
    PERMISSION_FLAGS, get_all_permissions, get_role_permissions,
    get_user_permissions, get_channel_permissions, get_category_permissions,
    create_permission_overwrite, combine_permissions,
    get_permission_names_by_value, calculate_effective_permissions,
    has_channel_permission, has_guild_permission
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
        perms = [{"name": p["name"], "value": p["value"], "description": p["description"], "channel_types": p["channel_types"]} for p in data]
        
        flogger.info(f"Retrieved {len(perms)} permission flags")
        return PermissionFlagListResponse(
            status="success",
            data=perms
        )
    except Exception as exc:
        flogger.exception("Error in list_all_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list permissions: {exc}"
        )

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
        perms = [{"name": p["name"], "value": p["value"], "description": p["description"], "channel_types": p["channel_types"]} for p in data]
        
        flogger.info(f"Retrieved {len(perms)} role permissions")
        return PermissionFlagListResponse(
            status="success",
            data=perms
        )
    except Exception as exc:
        flogger.exception("Error in list_role_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list role permissions: {exc}"
        )

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
        perms = [{"name": p["name"], "value": p["value"], "description": p["description"], "channel_types": p["channel_types"]} for p in data]
        
        flogger.info(f"Retrieved {len(perms)} user permissions")
        return PermissionFlagListResponse(
            status="success",
            data=perms
        )
    except Exception as exc:
        flogger.exception("Error in list_user_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list user permissions: {exc}"
        )

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
        perms = [{"name": p["name"], "value": p["value"], "description": p["description"], "channel_types": p["channel_types"]} for p in data]
        
        flogger.info(f"Retrieved {len(perms)} channel permissions")
        return PermissionFlagListResponse(
            status="success",
            data=perms
        )
    except Exception as exc:
        flogger.exception("Error in list_channel_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list channel permissions: {exc}"
        )

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
        perms = [{"name": p["name"], "value": p["value"], "description": p["description"], "channel_types": p["channel_types"]} for p in data]
        
        flogger.info(f"Retrieved {len(perms)} category permissions")
        return PermissionFlagListResponse(
            status="success",
            data=perms
        )
    except Exception as exc:
        flogger.exception("Error in list_category_permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list category permissions: {exc}"
        )

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
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="permission_id must be in format 'channel_id:target_id'"
            )
        
        bot = await resolve_bot(request)
        
        # Find the channel and overwrite
        channel = bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(channel_id)
            except:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {channel_id} not found"
                )
        
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
        
        overwrite_data = PermissionConverter.overwrite_to_payload(target, overwrite)
        overwrite_data.id = permission_id
        
        flogger.info(f"Retrieved permission overwrite {permission_id}")
        return PermissionOverwriteResponse(
            status="success",
            data=overwrite_data
        )
    except HTTPException:
        raise
    except Exception as exc:
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
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="permission_id must be in format 'channel_id:target_id'"
            )
        
        bot = await resolve_bot(request)
        
        # Find the channel
        channel = bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(channel_id)
            except:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {channel_id} not found"
                )
        
        guild = channel.guild
        
        # Find role or member
        target = guild.get_role(target_id)
        if not target:
            target = guild.get_member(target_id)
            if not target:
                try:
                    target = await guild.fetch_member(target_id)
                except:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Role or member {target_id} not found"
                    )
        
        # Create and set overwrite
        allow = permissions_data.allow or 0
        deny = permissions_data.deny or 0
        overwrite = create_permission_overwrite(allow=allow, deny=deny)
        await channel.set_permissions(target, overwrite=overwrite)
        
        # Return updated overwrite
        updated_overwrite_data = PermissionConverter.overwrite_to_payload(target, overwrite)
        updated_overwrite_data.id = permission_id
        
        flogger.info(f"Updated permission overwrite {permission_id}")
        return PermissionOverwriteResponse(
            status="updated",
            data=updated_overwrite_data
        )
    except HTTPException:
        raise
    except Exception as exc:
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
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="permission_id must be in format 'channel_id:target_id'"
            )
        
        bot = await resolve_bot(request)
        
        # Find the channel
        channel = bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(channel_id)
            except:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {channel_id} not found"
                )
        
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
    except Exception as exc:
        flogger.error(f"Unexpected error in remove_permission_overwrite: {exc}")
        await handle_discord_exception("remove permission overwrite", exc)

@router.get(
    "/permissions/{permission_id}/check",
    response_model=PermissionCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Check Permission",
    description="Check if a permission is granted for a specific target"
)
async def check_permission(
    request: Request,
    permission_id: str,
    permission: str = Query(..., description="Permission name (uppercase, e.g. SEND_MESSAGES)")
) -> PermissionCheckResponse:
    """Check if a permission is granted for a specific target."""
    flogger.info(f"check_permission called for permission_id={permission_id}, permission={permission}")
    
    # Validate permission name
    if permission not in PERMISSION_FLAGS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown permission: {permission}"
        )
    
    try:
        # Parse composite ID
        try:
            channel_id, target_id = permission_id.split(":")
            channel_id, target_id = int(channel_id), int(target_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="permission_id must be in format 'channel_id:target_id'"
            )
        
        bot = await resolve_bot(request)
        
        # Find the channel
        channel = bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(channel_id)
            except:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {channel_id} not found"
                )
        
        guild = channel.guild
        
        # Find the target
        member = None
        role = None
        try:
            member = guild.get_member(target_id)
            if not member:
                member = await guild.fetch_member(target_id)
        except:
            role = guild.get_role(target_id)
        
        if not member and not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Member or role {target_id} not found"
            )
        
        # Check permission
        if member:
            allowed = has_channel_permission(member, channel, permission)
        else:
            # For roles, check base permissions + overwrites
            base_perms = role.permissions.value
            overwrite = channel.overwrites.get(role)
            allow = getattr(overwrite.allow, "value", overwrite.allow) if overwrite else 0
            deny = getattr(overwrite.deny, "value", overwrite.deny) if overwrite else 0
            effective = calculate_effective_permissions(base_perms, allow, deny)
            bit = PERMISSION_FLAGS[permission]["value"]
            allowed = bool(effective & bit)
        
        flogger.info(f"Permission '{permission}' check for {permission_id}: {allowed}")
        return PermissionCheckResponse(
            status="success",
            data={"allowed": allowed}
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in check_permission: {exc}")
        await handle_discord_exception("check permission", exc)

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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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

@router.post(
    "/permissions/evaluate",
    response_model=BotPermissionSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate Effective Permissions",
    description="Return the effective permissions bitfield and allowed/denied permission names for a target within a scope"
)
async def evaluate_permissions(request: Request, body: PermissionCheckRequest) -> BotPermissionSummaryResponse:
    """Evaluate effective permissions for a target within a scope."""
    flogger.info(f"evaluate_permissions called: target={body.target}, scope={body.scope}")

    # Validate permission names if provided (we'll still compute full effective bitfield even if none provided)
    invalid = [p for p in (body.permissions or []) if p not in PERMISSION_FLAGS]
    if invalid:
        flogger.error(f"evaluate_permissions: unknown permission(s): {invalid}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown permission(s): {invalid}"
        )

    try:
        bot = await resolve_bot(request)

        # Resolve scope
        scope_type = body.scope.type.lower()
        scope_id = body.scope.id
        guild = None
        channel = None

        if scope_type == "guild":
            if not scope_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="guild scope requires an id"
                )
            # resolve guild
            guild = None
            try:
                guild = bot.get_guild(scope_id) or await bot.fetch_guild(scope_id)  # type: ignore
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Guild {scope_id} not found"
                )
        elif scope_type in ("channel", "category", "thread"):
            if not scope_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{scope_type} scope requires an id"
                )
            # resolve channel
            channel = bot.get_channel(scope_id)
            if not channel:
                try:
                    channel = await bot.fetch_channel(scope_id)  # type: ignore
                except Exception:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Channel {scope_id} not found"
                    )
            guild = channel.guild
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown scope type: {body.scope.type}"
            )

        # Resolve target
        ttype = body.target.type.lower()
        target_id = body.target.id

        member = None
        role = None

        if ttype == "member":
            if not target_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="member target requires id"
                )
            member = guild.get_member(target_id)
            if not member:
                try:
                    member = await guild.fetch_member(target_id)
                except Exception:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Member {target_id} not found in guild {guild.id}"
                    )
        elif ttype == "role":
            if not target_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="role target requires id"
                )
            role = guild.get_role(target_id)
            if not role:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Role {target_id} not found in guild {guild.id}"
                )
        elif ttype == "bot":
            bot_user = bot.user
            if not bot_user:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Bot not ready"
                )
            member = guild.get_member(bot_user.id)
            if not member:
                try:
                    member = await guild.fetch_member(bot_user.id)
                except Exception:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Bot not a member of guild {guild.id}"
                    )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown target type: {body.target.type}"
            )

        # Determine applicable permission flags for the scope
        if scope_type == "guild":
            candidate_perms = get_all_permissions()
        else:
            # channel/category/thread -> use channel perms (text/voice)
            candidate_perms = get_channel_permissions()

        # Compute effective bitfield
        effective_val = 0

        if scope_type == "guild":
            if member:
                # member guild permissions
                try:
                    effective_val = getattr(member.guild_permissions, "value", int(member.guild_permissions))
                except Exception:
                    # conservative per-flag assembly
                    for p in candidate_perms:
                        if has_guild_permission(member, p["name"]):
                            effective_val |= p["value"]
            elif role:
                effective_val = getattr(role.permissions, "value", int(role.permissions))
        else:
            # channel-like scope; channel is available
            if member:
                try:
                    perms = channel.permissions_for(member)
                    effective_val = getattr(perms, "value", 0)
                except Exception:
                    # fallback: build bitfield from flags
                    for p in candidate_perms:
                        try:
                            if getattr(channel.permissions_for(member), p["name"].lower(), False):
                                effective_val |= p["value"]
                        except Exception:
                            continue
            elif role:
                base_perms = getattr(role.permissions, "value", int(role.permissions))
                overwrite = None
                try:
                    overwrite = channel.overwrites.get(role)
                except Exception:
                    overwrite = None

                if overwrite:
                    # Try to extract allow/deny bitfields
                    try:
                        allow_perm_obj, deny_perm_obj = overwrite.pair()
                        allow_val = getattr(allow_perm_obj, "value", int(allow_perm_obj))
                        deny_val = getattr(deny_perm_obj, "value", int(deny_perm_obj))
                    except Exception:
                        # best-effort: get booleans from overwrite attributes and build bitfields
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

        # Map bitfield to names
        allowed_names = get_permission_names_by_value(effective_val)

        # Determine denied names within applicable candidate set
        cand_names = [p["name"] for p in candidate_perms]
        denied_names = [n for n in cand_names if n not in allowed_names]

        flogger.info(f"evaluate_permissions result for target={body.target}, scope={body.scope}: effective=0x{effective_val:x}")

        return BotPermissionSummaryResponse(
            status="success",
            data={"base": effective_val, "allowed_names": allowed_names, "denied_names": denied_names}
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in evaluate_permissions: {exc}")
        await handle_discord_exception("evaluate permissions", exc)