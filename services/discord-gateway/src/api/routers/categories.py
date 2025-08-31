"""
Category router for Discord Gateway API.

This module provides REST endpoints for managing Discord category channels
including getting, updating, and deleting categories and their permissions.
"""

from fastapi import APIRouter, HTTPException, Request, status, Query
import discord
import shared.bblogger as bblogger
from api.schemas.channel_schemas import (
    CategoryResponse, CategoryUpdateRequest, ChannelListResponse
)
from api.schemas.permission_schemas import (
    PermissionOverwriteListResponse, PermissionOverwriteListRequest
)
from api.schemas.base_schemas import SuccessResponse, DeleteResponse
from utils.discord_converters import ChannelConverter, PermissionConverter
from utils.discord_helpers import (
    resolve_bot, get_entity_or_404, validate_channel_type, handle_discord_exception
)
from utils.permission_utils import create_permission_overwrite

flogger = bblogger.get_logger("gateway-category-router")

router = APIRouter(
    tags=["categories"],
    responses={
        404: {"description": "Category not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

@router.get(
    "/categories/{category_id}",
    response_model=CategoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Category Details",
    description="Get detailed information about a specific category"
)
async def get_category(request: Request, category_id: int) -> CategoryResponse:
    """Get detailed information about a specific category."""
    flogger.info(f"get_category endpoint called for category_id: {category_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, category_id, "Channel"
        )
        
        validate_channel_type(channel, ["category"], category_id)
        
        category_data = ChannelConverter.category_to_detail(channel)
        flogger.info(f"Successfully retrieved category details for {channel.name}")
        
        return CategoryResponse(
            status="success",
            data=category_data
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_category for category {category_id}: {exc}")
        await handle_discord_exception("get category details", exc)

@router.put(
    "/categories/{category_id}",
    response_model=CategoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Category",
    description="Update a category's properties"
)
async def update_category(
    request: Request, category_id: int, category_data: CategoryUpdateRequest
) -> CategoryResponse:
    """Update a category's properties."""
    flogger.info(f"update_category endpoint called for category_id: {category_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, category_id, "Channel"
        )
        
        validate_channel_type(channel, ["category"], category_id)
        
        # Update category with provided parameters
        update_kwargs = {}
        if category_data.name is not None:
            update_kwargs["name"] = category_data.name
        if category_data.position is not None:
            update_kwargs["position"] = category_data.position
        
        if update_kwargs:
            await channel.edit(**update_kwargs)
            # Re-fetch the channel after the edit
            channel = await get_entity_or_404(
                bot.get_channel, bot.fetch_channel, category_id, "Channel"
            )
        
        category_detail = ChannelConverter.category_to_detail(channel)
        flogger.info(f"Successfully updated category {channel.name}")
        
        return CategoryResponse(
            status="updated",
            data=category_detail
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in update_category for category {category_id}: {exc}")
        await handle_discord_exception("update category", exc)

@router.delete(
    "/categories/{category_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Category",
    description="Delete a category (child channels remain or are deleted based on cascade parameter)"
)
async def delete_category(
    request: Request,
    category_id: int,
    cascade: bool = Query(False, description="Whether to delete child channels")
) -> DeleteResponse:
    """Delete a category."""
    flogger.info(f"delete_category endpoint called for category_id: {category_id}, cascade: {cascade}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, category_id, "Channel"
        )
        
        validate_channel_type(channel, ["category"], category_id)
        
        category_name = channel.name
        child_channels = list(channel.channels)
        
        if cascade:
            # Delete all child channels first
            for child_channel in child_channels:
                await child_channel.delete()
            flogger.info(f"Deleted {len(child_channels)} child channels")
        
        # Delete the category
        await channel.delete()
        
        message = f"Category {category_name} deleted"
        if cascade and child_channels:
            message += f" along with {len(child_channels)} child channels"
        
        flogger.info(message)
        return DeleteResponse(
            status="deleted",
            deleted=True,
            message=message
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in delete_category for category {category_id}: {exc}")
        await handle_discord_exception("delete category", exc)

@router.get(
    "/categories/{category_id}/channels",
    response_model=ChannelListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Category Channels",
    description="Get a list of all channels within a category"
)
async def list_category_channels(request: Request, category_id: int) -> ChannelListResponse:
    """List all channels within a category."""
    flogger.info(f"list_category_channels endpoint called for category_id: {category_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, category_id, "Channel"
        )
        
        validate_channel_type(channel, ["category"], category_id)
        
        # Sort channels in this category by their position
        channels = []
        sorted_chs = sorted(channel.channels, key=lambda ch: ch.position)
        for ch in sorted_chs:
            channel_data = ChannelConverter.channel_to_summary(ch)
            channels.append(channel_data)
        
        flogger.info(f"Successfully retrieved {len(channels)} channels from category {channel.name}")
        return ChannelListResponse(
            status="success",
            data=channels
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in list_category_channels for category {category_id}: {exc}")
        await handle_discord_exception("list category channels", exc)

@router.get(
    "/categories/{category_id}/permissions",
    response_model=PermissionOverwriteListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Category Permissions",
    description="Get permission overwrites for a category"
)
async def get_category_permissions(request: Request, category_id: int) -> PermissionOverwriteListResponse:
    """Get permission overwrites for a category."""
    flogger.info(f"get_category_permissions endpoint called for category_id: {category_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, category_id, "Channel"
        )
        
        validate_channel_type(channel, ["category"], category_id)
        
        overwrites = []
        for target, overwrite in channel.overwrites.items():
            overwrite_data = PermissionConverter.overwrite_to_payload(target, overwrite)
            overwrites.append(overwrite_data)
        
        flogger.info(f"Successfully retrieved {len(overwrites)} permission overwrites for category {channel.name}")
        return PermissionOverwriteListResponse(
            status="success",
            data=overwrites
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_category_permissions for category {category_id}: {exc}")
        await handle_discord_exception("get category permissions", exc)

@router.put(
    "/categories/{category_id}/permissions",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Category Permissions",
    description="Replace all permission overwrites for a category"
)
async def update_category_permissions(
    request: Request,
    category_id: int,
    permissions_data: PermissionOverwriteListRequest
) -> SuccessResponse:
    """Replace all permission overwrites for a category."""
    flogger.info(f"update_category_permissions endpoint called for category_id: {category_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, category_id, "Channel"
        )
        
        validate_channel_type(channel, ["category"], category_id)
        guild = channel.guild
        
        # Clear existing overwrites
        existing = list(channel.overwrites.keys())
        for target in existing:
            await channel.set_permissions(target, overwrite=None)
        
        # Apply new overwrites, skipping missing roles/members
        for od in permissions_data.overwrites:
            target_id, tgt_type = od.target_id, od.type
            allow, deny = od.allow or 0, od.deny or 0
            
            if tgt_type == "role":
                target = guild.get_role(target_id)
                if not target:
                    flogger.warning(f"Role {target_id} not found—skipping")
                    continue
            else:
                target = guild.get_member(target_id)
                if not target:
                    try:
                        target = await guild.fetch_member(target_id)
                    except Exception:
                        flogger.warning(f"Member {target_id} not found—skipping")
                        continue
            
            overwrite = create_permission_overwrite(allow=allow, deny=deny)
            await channel.set_permissions(target, overwrite=overwrite)
        
        message = f"Permissions updated for category {channel.name}"
        flogger.info(message)
        return SuccessResponse(status="updated", message=message)
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in update_category_permissions for category {category_id}: {exc}")
        await handle_discord_exception("update category permissions", exc)