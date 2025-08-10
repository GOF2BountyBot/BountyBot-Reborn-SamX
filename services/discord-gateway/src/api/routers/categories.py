"""
Category router for Discord Gateway API.

This module provides REST endpoints for managing Discord category channels
including listing, creating, updating, and deleting categories and their permissions.
"""

from typing import List
from fastapi import APIRouter, HTTPException, Request, status, Query
import discord

import shared.bblogger as bblogger
from api.schemas.channel_schemas import (
    CategoryListResponse, CategoryDetailResponse, CategoryCreateRequest,
    CategoryUpdateRequest, ChannelListResponse
)
from api.schemas.permission_schemas import (
    PermissionOverwriteListResponse, PermissionOverwriteListRequest
)
from api.schemas.base_schemas import SuccessResponse, DeleteResponse
from utils.discord_converters import ChannelConverter, PermissionConverter
from utils.discord_helpers import (
    resolve_bot, get_entity_or_404, validate_guild_channel_relationship,
    validate_channel_type, handle_discord_exception
)
from utils.permission_utils import create_permission_overwrite

flogger = bblogger.get_logger("gateway-category-router")

router = APIRouter(
    prefix="/guilds/{guild_id}/categories",
    tags=["categories"],
    responses={
        404: {"description": "Guild or category not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

# GET endpoints (ordered: List, Get Details, Get Extra Info)

@router.get(
    "",
    response_model=CategoryListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Categories",
    description="Get a list of all categories in a guild"
)
async def list_categories(request: Request, guild_id: int) -> CategoryListResponse:
    """
    List all categories in a guild.
    
    Args:
        guild_id: The ID of the guild to get categories from
        
    Returns:
        List of category channels in the guild
    """
    flogger.info(f"list_categories endpoint called for guild_id: {guild_id}")
    flogger.debug(f"Starting category list retrieval for guild {guild_id}")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        guild = await get_entity_or_404(
            bot.get_guild,
            bot.fetch_guild,
            guild_id,
            "Guild"
        )
        flogger.debug(f"Guild retrieved: {guild.name}, found {len(guild.categories)} categories")
        
        categories = []
        for category in guild.categories:
            flogger.trace(f"Processing category: {category.name} ({category.id})")
            category_detail = ChannelConverter.category_to_detail(category)
            categories.append(category_detail)
        
        flogger.info(f"Successfully retrieved {len(categories)} categories from guild {guild.name}")
        return CategoryListResponse(
            status="success",
            categories=categories
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in list_categories for guild {guild_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in list_categories for guild {guild_id}: {exc}")
        await handle_discord_exception("list categories", exc)

@router.get(
    "/{category_id}",
    response_model=CategoryDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Category Details",
    description="Get detailed information about a specific category"
)
async def get_category(request: Request, guild_id: int, category_id: int) -> CategoryDetailResponse:
    """
    Get detailed information about a specific category.
    
    Args:
        guild_id: The ID of the guild the category belongs to
        category_id: The ID of the category to retrieve
        
    Returns:
        Detailed category information
    """
    flogger.info(f"get_category endpoint called for guild_id: {guild_id}, category_id: {category_id}")
    flogger.debug(f"Starting category detail retrieval for category {category_id}")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        # Verify guild exists
        await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")
        flogger.trace(f"Guild {guild_id} verification completed")
        
        channel = await get_entity_or_404(
            bot.get_channel,
            bot.fetch_channel,
            category_id,
            "Channel"
        )
        flogger.debug(f"Channel retrieved: {channel.name}")
        
        validate_channel_type(channel, ["category"], category_id)
        validate_guild_channel_relationship(channel, guild_id)
        flogger.trace("Channel validation completed")
        
        category_detail = ChannelConverter.category_to_detail(channel)
        flogger.trace(f"Category detail conversion completed for {channel.name}")
        
        flogger.info(f"Successfully retrieved category details for {channel.name}")
        return CategoryDetailResponse(
            status="success",
            category=category_detail
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in get_category for category {category_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_category for category {category_id}: {exc}")
        await handle_discord_exception("get category details", exc)

@router.get(
    "/{category_id}/channels",
    response_model=ChannelListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Category Channels",
    description="Get a list of all channels within a category"
)
async def list_category_channels(request: Request, guild_id: int, category_id: int) -> ChannelListResponse:
    """
    List all channels within a category.
    
    Args:
        guild_id: The ID of the guild the category belongs to
        category_id: The ID of the category to get channels from
        
    Returns:
        List of channels in the category
    """
    flogger.info(f"list_category_channels endpoint called for guild_id: {guild_id}, category_id: {category_id}")
    flogger.debug(f"Starting channel list retrieval for category {category_id}")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        # Verify guild exists
        await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")
        flogger.trace(f"Guild {guild_id} verification completed")
        
        channel = await get_entity_or_404(
            bot.get_channel,
            bot.fetch_channel,
            category_id,
            "Channel"
        )
        flogger.debug(f"Category retrieved: {channel.name}")
        
        validate_channel_type(channel, ["category"], category_id)
        flogger.trace("Category validation completed")
        
        channels = []
        flogger.debug(f"Processing {len(channel.channels)} channels in category")
        for ch in channel.channels:
            flogger.trace(f"Processing channel: {ch.name} ({ch.id})")
            channel_summary = ChannelConverter.channel_to_summary(ch)
            channels.append(channel_summary)
        
        flogger.info(f"Successfully retrieved {len(channels)} channels from category {channel.name}")
        return ChannelListResponse(
            status="success",
            channels=channels
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in list_category_channels for category {category_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in list_category_channels for category {category_id}: {exc}")
        await handle_discord_exception("list category channels", exc)

@router.get(
    "/{category_id}/permissions",
    response_model=PermissionOverwriteListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Category Permissions",
    description="Get permission overwrites for a category"
)
async def get_category_permissions(request: Request, guild_id: int, category_id: int) -> PermissionOverwriteListResponse:
    """
    Get permission overwrites for a category.
    
    Args:
        guild_id: The ID of the guild the category belongs to
        category_id: The ID of the category to get permissions for
        
    Returns:
        List of permission overwrites for the category
    """
    flogger.info(f"get_category_permissions endpoint called for guild_id: {guild_id}, category_id: {category_id}")
    flogger.debug(f"Starting permission retrieval for category {category_id}")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        # Verify guild exists
        await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")
        flogger.trace(f"Guild {guild_id} verification completed")
        
        channel = await get_entity_or_404(
            bot.get_channel,
            bot.fetch_channel,
            category_id,
            "Channel"
        )
        flogger.debug(f"Category retrieved: {channel.name}")
        
        validate_channel_type(channel, ["category"], category_id)
        flogger.trace("Category validation completed")
        
        overwrites = []
        flogger.debug(f"Processing {len(channel.overwrites)} permission overwrites")
        for target, overwrite in channel.overwrites.items():
            flogger.trace(f"Processing overwrite for: {target.name} ({target.id})")
            overwrite_payload = PermissionConverter.overwrite_to_payload(target, overwrite)
            overwrites.append(overwrite_payload)
        
        flogger.info(f"Successfully retrieved {len(overwrites)} permission overwrites for category {channel.name}")
        return PermissionOverwriteListResponse(
            status="success",
            overwrites=overwrites
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in get_category_permissions for category {category_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_category_permissions for category {category_id}: {exc}")
        await handle_discord_exception("get category permissions", exc)

# POST endpoints

@router.post(
    "",
    response_model=CategoryDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Category",
    description="Create a new category in a guild"
)
async def create_category(
    request: Request, 
    guild_id: int, 
    category_data: CategoryCreateRequest
) -> CategoryDetailResponse:
    """
    Create a new category in a guild.
    
    Args:
        guild_id: The ID of the guild to create the category in
        category_data: Category creation parameters
        
    Returns:
        Details of the created category
    """
    flogger.info(f"create_category endpoint called for guild_id: {guild_id}, name: {category_data.name}")
    flogger.debug(f"Starting category creation with data: {category_data.dict()}")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        guild = await get_entity_or_404(
            bot.get_guild,
            bot.fetch_guild,
            guild_id,
            "Guild"
        )
        flogger.debug(f"Guild retrieved: {guild.name}")
        
        # Create category with provided parameters
        flogger.trace("Creating category channel")
        category = await guild.create_category_channel(
            name=category_data.name,
            position=category_data.position
        )
        if category_data.nsfw:
            await category.edit(nsfw=category_data.nsfw)

        flogger.debug(f"Category created: {category.name} (ID: {category.id})")
        
        category_detail = ChannelConverter.category_to_detail(category)
        flogger.trace("Category detail conversion completed")
        
        flogger.info(f"Successfully created category {category.name} (ID: {category.id})")
        return CategoryDetailResponse(
            status="created",
            category=category_detail
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in create_category for guild {guild_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in create_category for guild {guild_id}: {exc}")
        await handle_discord_exception("create category", exc)

@router.post(
    "/{category_id}/channels/{channel_id}",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Move Channel to Category",
    description="Move a channel into this category"
)
async def move_channel_to_category(
    request: Request,
    guild_id: int,
    category_id: int,
    channel_id: int
) -> SuccessResponse:
    """
    Move a channel into a category.
    
    Args:
        guild_id: The ID of the guild
        category_id: The ID of the target category
        channel_id: The ID of the channel to move
        
    Returns:
        Success confirmation
    """
    flogger.info(f"move_channel_to_category endpoint called for guild_id: {guild_id}, category_id: {category_id}, channel_id: {channel_id}")
    flogger.debug(f"Starting channel move operation")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        # Verify guild exists
        await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")
        flogger.trace(f"Guild {guild_id} verification completed")
        
        category = await get_entity_or_404(
            bot.get_channel,
            bot.fetch_channel,
            category_id,
            "Channel"
        )
        flogger.debug(f"Category retrieved: {category.name}")
        
        channel = await get_entity_or_404(
            bot.get_channel,
            bot.fetch_channel,
            channel_id,
            "Channel"
        )
        flogger.debug(f"Channel retrieved: {channel.name}")
        
        validate_channel_type(category, ["category"], category_id)
        
        if isinstance(channel, discord.CategoryChannel):
            flogger.error(f"Cannot move category {channel_id} into another category")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot move category {channel_id} into another category"
            )
        
        flogger.trace("Channel validation completed")
        
        # Move channel to category
        flogger.debug(f"Moving channel {channel.name} to category {category.name}")
        await channel.edit(category=category)
        
        message = f"Channel {channel.name} moved to category {category.name}"
        flogger.info(message)
        return SuccessResponse(
            status="moved",
            message=message
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in move_channel_to_category")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in move_channel_to_category: {exc}")
        await handle_discord_exception("move channel to category", exc)

# PUT endpoints

@router.put(
    "/{category_id}",
    response_model=CategoryDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Category",
    description="Update a category's properties"
)
async def update_category(
    request: Request,
    guild_id: int,
    category_id: int,
    category_data: CategoryUpdateRequest
) -> CategoryDetailResponse:
    """
    Update a category's properties.
    
    Args:
        guild_id: The ID of the guild the category belongs to
        category_id: The ID of the category to update
        category_data: Category update parameters
        
    Returns:
        Details of the updated category
    """
    flogger.info(f"update_category endpoint called for guild_id: {guild_id}, category_id: {category_id}")
    flogger.debug(f"Starting category update with data: {category_data.dict()}")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        # Verify guild exists
        await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")
        flogger.trace(f"Guild {guild_id} verification completed")
        
        channel = await get_entity_or_404(
            bot.get_channel,
            bot.fetch_channel,
            category_id,
            "Channel"
        )
        flogger.debug(f"Category retrieved: {channel.name}")
        
        validate_channel_type(channel, ["category"], category_id)
        flogger.trace("Category validation completed")
        
        # Update category with provided parameters
        update_kwargs = {}
        if category_data.name is not None:
            update_kwargs["name"] = category_data.name
            flogger.trace(f"Will update name to: {category_data.name}")
        if category_data.position is not None:
            update_kwargs["position"] = category_data.position
            flogger.trace(f"Will update position to: {category_data.position}")
        if category_data.nsfw is not None:
            update_kwargs["nsfw"] = category_data.nsfw
            flogger.trace(f"Will update nsfw to: {category_data.nsfw}")
        
        if update_kwargs:
            flogger.debug(f"Applying updates: {update_kwargs}")
            await channel.edit(**update_kwargs)
        else:
            flogger.debug("No updates to apply")
        
        category_detail = ChannelConverter.category_to_detail(channel)
        flogger.trace("Category detail conversion completed")
        
        flogger.info(f"Successfully updated category {channel.name}")
        return CategoryDetailResponse(
            status="updated",
            category=category_detail
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in update_category for category {category_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in update_category for category {category_id}: {exc}")
        await handle_discord_exception("update category", exc)

@router.put(
    "/{category_id}/permissions",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Category Permissions",
    description="Replace all permission overwrites for a category (wrapper for Discord PATCH)"
)
async def update_category_permissions(
    request: Request,
    guild_id: int,
    category_id: int,
    permissions_data: PermissionOverwriteListRequest
) -> SuccessResponse:
    """
    Replace all permission overwrites for a category.
    
    Args:
        guild_id: The ID of the guild the category belongs to
        category_id: The ID of the category to update permissions for
        permissions_data: List of permission overwrites to set
        
    Returns:
        Success confirmation
    """
    flogger.info(f"update_category_permissions endpoint called for guild_id: {guild_id}, category_id: {category_id}")
    flogger.debug(f"Starting permission update with {len(permissions_data.overwrites)} overwrites")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        guild = await get_entity_or_404(
            bot.get_guild,
            bot.fetch_guild,
            guild_id,
            "Guild"
        )
        flogger.debug(f"Guild retrieved: {guild.name}")
        
        channel = await get_entity_or_404(
            bot.get_channel,
            bot.fetch_channel,
            category_id,
            "Channel"
        )
        flogger.debug(f"Category retrieved: {channel.name}")
        
        validate_channel_type(channel, ["category"], category_id)
        flogger.trace("Category validation completed")
        
        # Clear existing overwrites
        existing_targets = list(channel.overwrites.keys())
        flogger.debug(f"Clearing {len(existing_targets)} existing overwrites")
        for target in existing_targets:
            flogger.trace(f"Clearing overwrite for: {target.name}")
            await channel.set_permissions(target, overwrite=None)
        
        # Set new overwrites
        flogger.debug(f"Setting {len(permissions_data.overwrites)} new overwrites")
        for overwrite_data in permissions_data.overwrites:
            target_id = overwrite_data.target_id
            tgt_type  = overwrite_data.type
            allow     = overwrite_data.allow  or 0
            deny      = overwrite_data.deny   or 0
            
            flogger.trace(f"Processing overwrite for {tgt_type} {target_id}: allow={hex(allow)}, deny={hex(deny)}")
            
            # Get target (role or member)
            if tgt_type == "role":
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
        
        message = f"Permissions updated for category {channel.name}"
        flogger.info(message)
        return SuccessResponse(
            status="updated",
            message=message
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in update_category_permissions for category {category_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in update_category_permissions for category {category_id}: {exc}")
        await handle_discord_exception("update category permissions", exc)

# DELETE endpoints

@router.delete(
    "/{category_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Category",
    description="Delete a category (child channels remain or are deleted based on cascade parameter)"
)
async def delete_category(
    request: Request,
    guild_id: int,
    category_id: int,
    cascade: bool = Query(False, description="Whether to delete child channels")
) -> DeleteResponse:
    """
    Delete a category.
    
    Args:
        guild_id: The ID of the guild the category belongs to
        category_id: The ID of the category to delete
        cascade: Whether to delete child channels (default: False)
        
    Returns:
        Deletion confirmation
    """
    flogger.info(f"delete_category endpoint called for guild_id: {guild_id}, category_id: {category_id}, cascade: {cascade}")
    flogger.debug(f"Starting category deletion")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        # Verify guild exists
        await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")
        flogger.trace(f"Guild {guild_id} verification completed")
        
        channel = await get_entity_or_404(
            bot.get_channel,
            bot.fetch_channel,
            category_id,
            "Channel"
        )
        flogger.debug(f"Category retrieved: {channel.name}")
        
        validate_channel_type(channel, ["category"], category_id)
        flogger.trace("Category validation completed")
        
        category_name = channel.name
        child_channels = list(channel.channels)
        flogger.debug(f"Category has {len(child_channels)} child channels")
        
        if cascade:
            # Delete all child channels first
            flogger.debug("Cascade delete enabled, deleting child channels")
            for child_channel in child_channels:
                flogger.trace(f"Deleting child channel: {child_channel.name}")
                await child_channel.delete()
            flogger.info(f"Deleted {len(child_channels)} child channels")
        
        # Delete the category
        flogger.debug(f"Deleting category: {category_name}")
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
        flogger.warning(f"HTTP exception occurred in delete_category for category {category_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in delete_category for category {category_id}: {exc}")
        await handle_discord_exception("delete category", exc)
