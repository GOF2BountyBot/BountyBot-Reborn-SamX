"""
Permission router for Discord Gateway API.

This module provides REST endpoints for managing Discord permissions
including channel overwrites and permission reference data.
"""

from typing import Union
from fastapi import APIRouter, HTTPException, Request, status

import shared.bblogger as bblogger
from api.schemas.permission_schemas import (
    PermissionOverwriteListResponse, PermissionOverwriteDetailResponse,
    PermissionOverwriteRequest, PermissionOverwriteListRequest,
    PermissionFlagListResponse, PermissionFlag
)
from api.schemas.base_schemas import SuccessResponse, DeleteResponse
from utils.discord_converters import PermissionConverter
from utils.discord_helpers import resolve_bot, get_entity_or_404, handle_discord_exception
from utils.permission_utils import (
    get_all_permissions, get_role_permissions, get_user_permissions,
    get_channel_permissions, get_category_permissions, create_permission_overwrite
)

flogger = bblogger.get_logger("gateway-permission-router")

router = APIRouter(
    tags=["permissions"],
    responses={
        404: {"description": "Channel or permission target not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

# GET endpoints (ordered: List, Get Details, Get Extra Info)

@router.get(
    "/channels/{channel_id}/permissions",
    response_model=PermissionOverwriteListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Channel Permission Overwrites",
    description="Get all permission overwrites for a channel"
)
async def get_channel_permission_overwrites(request: Request, channel_id: int) -> PermissionOverwriteListResponse:
    """
    Get all permission overwrites for a channel.
    
    Args:
        channel_id: The ID of the channel to get permissions for
        
    Returns:
        List of permission overwrites for the channel
    """
    flogger.info(f"get_channel_permission_overwrites endpoint called for channel_id: {channel_id}")
    flogger.debug(f"Starting permission overwrite retrieval for channel {channel_id}")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        channel = await get_entity_or_404(
            bot.get_channel,
            bot.fetch_channel,
            channel_id,
            "Channel"
        )
        flogger.debug(f"Channel retrieved: {channel.name}")
        
        overwrites = []
        flogger.debug(f"Processing {len(channel.overwrites)} permission overwrites")
        for target, overwrite in channel.overwrites.items():
            flogger.trace(f"Processing overwrite for: {target.name} ({target.id})")
            overwrite_payload = PermissionConverter.overwrite_to_payload(target, overwrite)
            overwrites.append(overwrite_payload)
        
        flogger.info(f"Successfully retrieved {len(overwrites)} permission overwrites for channel {channel.name}")
        return PermissionOverwriteListResponse(
            status="success",
            overwrites=overwrites
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in get_channel_permission_overwrites for channel {channel_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_channel_permission_overwrites for channel {channel_id}: {exc}")
        await handle_discord_exception("get channel permission overwrites", exc)

@router.get(
    "/channels/{channel_id}/permissions/{overwrite_id}",
    response_model=PermissionOverwriteDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Specific Permission Overwrite",
    description="Get a specific permission overwrite for a channel"
)
async def get_channel_permission_overwrite(
    request: Request,
    channel_id: int,
    overwrite_id: int
) -> PermissionOverwriteDetailResponse:
    """
    Get a specific permission overwrite for a channel.
    
    Args:
        channel_id: The ID of the channel
        overwrite_id: The ID of the role or user the overwrite applies to
        
    Returns:
        Specific permission overwrite details
    """
    flogger.info(f"get_channel_permission_overwrite endpoint called for channel_id: {channel_id}, overwrite_id: {overwrite_id}")
    flogger.debug(f"Starting specific permission overwrite retrieval")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        channel = await get_entity_or_404(
            bot.get_channel,
            bot.fetch_channel,
            channel_id,
            "Channel"
        )
        flogger.debug(f"Channel retrieved: {channel.name}")
        
        # Find the target in overwrites
        target = None
        overwrite = None
        
        flogger.debug(f"Searching for overwrite target {overwrite_id}")
        for ow_target, ow_overwrite in channel.overwrites.items():
            if ow_target.id == overwrite_id:
                target = ow_target
                overwrite = ow_overwrite
                flogger.trace(f"Found overwrite target: {target.name}")
                break
        
        if not target or not overwrite:
            flogger.error(f"Permission overwrite for {overwrite_id} not found in channel {channel_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Permission overwrite for {overwrite_id} not found in channel {channel_id}"
            )
        
        overwrite_payload = PermissionConverter.overwrite_to_payload(target, overwrite)
        flogger.trace("Permission overwrite conversion completed")
        
        flogger.info(f"Successfully retrieved permission overwrite for {target.name} in channel {channel.name}")
        return PermissionOverwriteDetailResponse(
            status="success",
            overwrite=overwrite_payload
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in get_channel_permission_overwrite for overwrite {overwrite_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_channel_permission_overwrite for overwrite {overwrite_id}: {exc}")
        await handle_discord_exception("get channel permission overwrite", exc)

@router.get(
    "/permissions",
    response_model=PermissionFlagListResponse,
    status_code=status.HTTP_200_OK,
    summary="List All Discord Permissions",
    description="Get a list of all Discord permissions with metadata"
)
async def list_all_permissions() -> PermissionFlagListResponse:
    """
    Get a list of all Discord permissions.
    
    Returns:
        List of all Discord permissions with their bit values and descriptions
    """
    flogger.info("list_all_permissions endpoint called")
    flogger.debug("Starting permission flags retrieval")
    
    try:
        permissions_data = get_all_permissions()
        permissions = [PermissionFlag(**perm) for perm in permissions_data]
        
        flogger.info(f"Successfully retrieved {len(permissions)} Discord permissions")
        return PermissionFlagListResponse(
            status="success",
            permissions=permissions
        )
        
    except Exception as exc:
        flogger.exception("Error listing all permissions")
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
    """
    Get permissions that can be assigned to roles.
    
    Returns:
        List of permissions assignable to roles
    """
    flogger.info("list_role_permissions endpoint called")
    flogger.debug("Starting role permission flags retrieval")
    
    try:
        permissions_data = get_role_permissions()
        permissions = [PermissionFlag(**perm) for perm in permissions_data]
        
        flogger.info(f"Successfully retrieved {len(permissions)} role permissions")
        return PermissionFlagListResponse(
            status="success",
            permissions=permissions
        )
        
    except Exception as exc:
        flogger.exception("Error listing role permissions")
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
    """
    Get permissions that can be used in user overwrites.
    
    Returns:
        List of permissions usable in user overwrites
    """
    flogger.info("list_user_permissions endpoint called")
    flogger.debug("Starting user permission flags retrieval")
    
    try:
        permissions_data = get_user_permissions()
        permissions = [PermissionFlag(**perm) for perm in permissions_data]
        
        flogger.info(f"Successfully retrieved {len(permissions)} user permissions")
        return PermissionFlagListResponse(
            status="success",
            permissions=permissions
        )
        
    except Exception as exc:
        flogger.exception("Error listing user permissions")
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
    """
    Get permissions applicable to channels.
    
    Returns:
        List of permissions applicable to channels
    """
    flogger.info("list_channel_permissions endpoint called")
    flogger.debug("Starting channel permission flags retrieval")
    
    try:
        permissions_data = get_channel_permissions()
        permissions = [PermissionFlag(**perm) for perm in permissions_data]
        
        flogger.info(f"Successfully retrieved {len(permissions)} channel permissions")
        return PermissionFlagListResponse(
            status="success",
            permissions=permissions
        )
        
    except Exception as exc:
        flogger.exception("Error listing channel permissions")
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
    """
    Get permissions applicable to categories.
    
    Returns:
        List of permissions applicable to categories
    """
    flogger.info("list_category_permissions endpoint called")
    flogger.debug("Starting category permission flags retrieval")
    
    try:
        permissions_data = get_category_permissions()
        permissions = [PermissionFlag(**perm) for perm in permissions_data]
        
        flogger.info(f"Successfully retrieved {len(permissions)} category permissions")
        return PermissionFlagListResponse(
            status="success",
            permissions=permissions
        )
        
    except Exception as exc:
        flogger.exception("Error listing category permissions")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list category permissions: {exc}"
        )

# PUT endpoints

@router.put(
    "/channels/{channel_id}/permissions",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Replace Channel Permission Overwrites",
    description="Replace all permission overwrites for a channel"
)
async def replace_channel_permission_overwrites(
    request: Request,
    channel_id: int,
    permissions_data: PermissionOverwriteListRequest
) -> SuccessResponse:
    """
    Replace all permission overwrites for a channel.
    
    Args:
        channel_id: The ID of the channel to update permissions for
        permissions_data: List of permission overwrites to set
        
    Returns:
        Success confirmation
    """
    flogger.info(f"replace_channel_permission_overwrites endpoint called for channel_id: {channel_id}")
    flogger.debug(f"Starting permission replacement with {len(permissions_data.overwrites)} overwrites")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        channel = await get_entity_or_404(
            bot.get_channel,
            bot.fetch_channel,
            channel_id,
            "Channel"
        )
        flogger.debug(f"Channel retrieved: {channel.name}")
        
        guild = channel.guild
        flogger.trace(f"Guild retrieved: {guild.name}")
        
        # Clear existing overwrites
        existing_targets = list(channel.overwrites.keys())
        flogger.debug(f"Clearing {len(existing_targets)} existing overwrites")
        for target in existing_targets:
            flogger.trace(f"Clearing overwrite for: {target.name}")
            await channel.set_permissions(target, overwrite=None)
        
        # Set new overwrites
        flogger.debug(f"Setting {len(permissions_data.overwrites)} new overwrites")
        for overwrite_data in permissions_data.overwrites:
            target_id = overwrite_data["id"]
            target_type = overwrite_data["type"]
            allow = overwrite_data.get("allow", 0)
            deny = overwrite_data.get("deny", 0)
            
            flogger.trace(f"Processing overwrite for {target_type} {target_id}: allow={hex(allow)}, deny={hex(deny)}")
            
            # Get target (role or member)
            if target_type == "role":
                target = guild.get_role(target_id)
                if not target:
                    flogger.error(f"Role {target_id} not found")
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Role {target_id} not found"
                    )
            else:  # member
                target = guild.get_member(target_id)
                if not target:
                    flogger.trace(f"Member {target_id} not in cache, fetching")
                    target = await guild.fetch_member(target_id)
            
            # Create and set overwrite
            overwrite = create_permission_overwrite(allow=allow, deny=deny)
            flogger.trace(f"Setting overwrite for {target.name}")
            await channel.set_permissions(target, overwrite=overwrite)
        
        message = f"Permissions replaced for channel {channel.name}"
        flogger.info(message)
        return SuccessResponse(
            status="updated",
            message=message
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in replace_channel_permission_overwrites for channel {channel_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in replace_channel_permission_overwrites for channel {channel_id}: {exc}")
        await handle_discord_exception("replace channel permission overwrites", exc)

@router.put(
    "/channels/{channel_id}/permissions/{overwrite_id}",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Set Specific Permission Overwrite",
    description="Set a specific permission overwrite for a channel"
)
async def set_channel_permission_overwrite(
    request: Request,
    channel_id: int,
    overwrite_id: int,
    permissions_data: PermissionOverwriteRequest
) -> SuccessResponse:
    """
    Set a specific permission overwrite for a channel.
    
    Args:
        channel_id: The ID of the channel
        overwrite_id: The ID of the role or user to set permissions for
        permissions_data: Permission overwrite data
        
    Returns:
        Success confirmation
    """
    flogger.info(f"set_channel_permission_overwrite endpoint called for channel_id: {channel_id}, overwrite_id: {overwrite_id}")
    flogger.debug(f"Starting specific permission overwrite setting")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        channel = await get_entity_or_404(
            bot.get_channel,
            bot.fetch_channel,
            channel_id,
            "Channel"
        )
        flogger.debug(f"Channel retrieved: {channel.name}")
        
        guild = channel.guild
        flogger.trace(f"Guild retrieved: {guild.name}")
        
        # Find target (role or member)
        target = guild.get_role(overwrite_id)
        if not target:
            flogger.trace(f"Role {overwrite_id} not found, trying member")
            target = guild.get_member(overwrite_id)
            if not target:
                flogger.trace(f"Member {overwrite_id} not in cache, fetching")
                try:
                    target = await guild.fetch_member(overwrite_id)
                except:
                    flogger.error(f"Role or member {overwrite_id} not found")
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Role or member {overwrite_id} not found"
                    )
        
        flogger.debug(f"Target retrieved: {target.name}")
        
        # Create and set overwrite
        allow = permissions_data.allow or 0
        deny = permissions_data.deny or 0
        flogger.trace(f"Setting overwrite: allow={hex(allow)}, deny={hex(deny)}")
        
        overwrite = create_permission_overwrite(allow=allow, deny=deny)
        await channel.set_permissions(target, overwrite=overwrite)
        
        target_type = "role" if hasattr(target, 'permissions') else "member"
        message = f"Permission overwrite set for {target_type} {target.name} in channel {channel.name}"
        
        flogger.info(message)
        return SuccessResponse(
            status="updated",
            message=message
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in set_channel_permission_overwrite for overwrite {overwrite_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in set_channel_permission_overwrite for overwrite {overwrite_id}: {exc}")
        await handle_discord_exception("set channel permission overwrite", exc)

# DELETE endpoints

@router.delete(
    "/channels/{channel_id}/permissions/{overwrite_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Remove Permission Overwrite",
    description="Remove a permission overwrite from a channel"
)
async def remove_channel_permission_overwrite(
    request: Request,
    channel_id: int,
    overwrite_id: int
) -> DeleteResponse:
    """
    Remove a permission overwrite from a channel.
    
    Args:
        channel_id: The ID of the channel
        overwrite_id: The ID of the role or user to remove permissions for
        
    Returns:
        Deletion confirmation
    """
    flogger.info(f"remove_channel_permission_overwrite endpoint called for channel_id: {channel_id}, overwrite_id: {overwrite_id}")
    flogger.debug(f"Starting permission overwrite removal")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        channel = await get_entity_or_404(
            bot.get_channel,
            bot.fetch_channel,
            channel_id,
            "Channel"
        )
        flogger.debug(f"Channel retrieved: {channel.name}")
        
        # Find target in existing overwrites
        target = None
        flogger.debug(f"Searching for existing overwrite target {overwrite_id}")
        for ow_target in channel.overwrites.keys():
            if ow_target.id == overwrite_id:
                target = ow_target
                flogger.trace(f"Found existing overwrite target: {target.name}")
                break
        
        if not target:
            flogger.error(f"Permission overwrite for {overwrite_id} not found in channel {channel_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Permission overwrite for {overwrite_id} not found in channel {channel_id}"
            )
        
        target_name = target.name
        target_type = "role" if hasattr(target, 'permissions') else "member"
        
        # Remove overwrite
        flogger.debug(f"Removing overwrite for {target_type} {target_name}")
        await channel.set_permissions(target, overwrite=None)
        
        message = f"Permission overwrite removed for {target_type} {target_name} from channel {channel.name}"
        
        flogger.info(message)
        return DeleteResponse(
            status="deleted",
            deleted=True,
            message=message
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in remove_channel_permission_overwrite for overwrite {overwrite_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in remove_channel_permission_overwrite for overwrite {overwrite_id}: {exc}")
        await handle_discord_exception("remove channel permission overwrite", exc)
