"""
Channel router for Discord Gateway API.

This module provides REST endpoints for managing Discord channels
including listing, creating, updating, and deleting channels and their permissions.
"""

from typing import Union
from fastapi import APIRouter, HTTPException, Request, status, Query
import discord

import shared.bblogger as bblogger
from api.schemas.channel_schemas import (
    ChannelListResponse, ChannelDetailResponse, ChannelCreateRequest,
    ChannelUpdateRequest
)
from api.schemas.message_schemas import MessageListResponse
from api.schemas.permission_schemas import (
    PermissionOverwriteListResponse, PermissionOverwriteListRequest
)
from api.schemas.base_schemas import SuccessResponse, DeleteResponse
from utils.discord_converters import ChannelConverter, PermissionConverter, MessageConverter
from utils.discord_helpers import (
    resolve_bot, get_entity_or_404, validate_guild_channel_relationship,
    validate_channel_type, handle_discord_exception
)
from utils.permission_utils import create_permission_overwrite

flogger = bblogger.get_logger("gateway-channel-router")

router = APIRouter(
    tags=["channels"],
    responses={
        404: {"description": "Channel or guild not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

# GET endpoints (ordered: List, Get Details, Get Extra Info)

@router.get(
    "/guilds/{guild_id}/channels",
    response_model=ChannelListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Guild Channels",
    description="Get a list of all channels in a guild"
)
async def list_guild_channels(request: Request, guild_id: int) -> ChannelListResponse:
    """
    List all channels in a guild.
    
    Args:
        guild_id: The ID of the guild to get channels from
        
    Returns:
        List of channels in the guild
    """
    flogger.info(f"list_guild_channels endpoint called for guild_id: {guild_id}")
    flogger.debug(f"Starting channel list retrieval for guild {guild_id}")
    
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
        
        channels = []
        flogger.debug(f"Processing {len(guild.channels)} total channels")
        for channel in guild.channels:
            # Skip category channels as they're handled separately
            if not isinstance(channel, discord.CategoryChannel):
                flogger.trace(f"Processing channel: {channel.name} ({channel.id})")
                channel_summary = ChannelConverter.channel_to_summary(channel)
                channels.append(channel_summary)
            else:
                flogger.trace(f"Skipping category channel: {channel.name}")
        
        flogger.info(f"Successfully retrieved {len(channels)} channels from guild {guild.name}")
        return ChannelListResponse(
            status="success",
            channels=channels
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in list_guild_channels for guild {guild_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in list_guild_channels for guild {guild_id}: {exc}")
        await handle_discord_exception("list guild channels", exc)

@router.get(
    "/channels/{channel_id}",
    response_model=ChannelDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Channel Details",
    description="Get detailed information about a specific channel"
)
async def get_channel(request: Request, channel_id: int) -> ChannelDetailResponse:
    """
    Get detailed information about a specific channel.
    
    Args:
        channel_id: The ID of the channel to retrieve
        
    Returns:
        Detailed channel information
    """
    flogger.info(f"get_channel endpoint called for channel_id: {channel_id}")
    flogger.debug(f"Starting channel detail retrieval for channel {channel_id}")
    
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
        
        if isinstance(channel, discord.CategoryChannel):
            flogger.error(f"Channel {channel_id} is a category")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel_id} is a category. Use category endpoints instead."
            )
        
        flogger.trace("Channel type validation completed")
        
        channel_detail = ChannelConverter.channel_to_detail(channel)
        flogger.trace(f"Channel detail conversion completed for {channel.name}")
        
        flogger.info(f"Successfully retrieved channel details for {channel.name}")
        return ChannelDetailResponse(
            status="success",
            channel=channel_detail
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in get_channel for channel {channel_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_channel for channel {channel_id}: {exc}")
        await handle_discord_exception("get channel details", exc)

@router.get(
    "/channels/{channel_id}/permissions",
    response_model=PermissionOverwriteListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Channel Permissions",
    description="Get permission overwrites for a channel"
)
async def get_channel_permissions(request: Request, channel_id: int) -> PermissionOverwriteListResponse:
    """
    Get permission overwrites for a channel.
    
    Args:
        channel_id: The ID of the channel to get permissions for
        
    Returns:
        List of permission overwrites for the channel
    """
    flogger.info(f"get_channel_permissions endpoint called for channel_id: {channel_id}")
    flogger.debug(f"Starting permission retrieval for channel {channel_id}")
    
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
        flogger.warning(f"HTTP exception occurred in get_channel_permissions for channel {channel_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_channel_permissions for channel {channel_id}: {exc}")
        await handle_discord_exception("get channel permissions", exc)

# Get message history from channel
@router.get(
    "/guilds/{guild_id}/channels/{channel_id}/messages",
    response_model=MessageListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Channel Messages",
    description="Get the last `limit` messages from a channel"
)
async def list_guild_channel_messages(
    request: Request,
    guild_id: int,
    channel_id: int,
    limit: int = Query(50, le=100, description="Number of messages to retrieve")
) -> MessageListResponse:
    bot     = await resolve_bot(request)
    guild   = await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")
    channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

    if not hasattr(channel, "history"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Channel {channel_id} cannot contain messages"
        )

    msgs = [msg async for msg in channel.history(limit=limit)]
    payloads = [MessageConverter.message_to_payload(m) for m in msgs]
    return MessageListResponse(status="success", messages=payloads)

# POST endpoints

@router.post(
    "/guilds/{guild_id}/channels",
    response_model=ChannelDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Channel",
    description="Create a new channel in a guild"
)
async def create_channel(
    request: Request,
    guild_id: int,
    channel_data: ChannelCreateRequest
) -> ChannelDetailResponse:
    """
    Create a new channel in a guild.
    
    Args:
        guild_id: The ID of the guild to create the channel in
        channel_data: Channel creation parameters
        
    Returns:
        Details of the created channel
    """
    flogger.info(f"create_channel endpoint called for guild_id: {guild_id}, name: {channel_data.name}, type: {channel_data.type}")
    flogger.debug(f"Starting channel creation with data: {channel_data.dict()}")
    
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
        
        # Get category if specified
        category = None
        if channel_data.category_id:
            flogger.trace(f"Looking up category {channel_data.category_id}")
            category = guild.get_channel(channel_data.category_id)
            if not category or not isinstance(category, discord.CategoryChannel):
                flogger.error(f"Category {channel_data.category_id} not found")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Category {channel_data.category_id} not found"
                )
            flogger.trace(f"Category found: {category.name}")
        
        # Create channel based on type
        if channel_data.type.lower() == "voice":
            flogger.debug("Creating voice channel")
            channel = await guild.create_voice_channel(
                name=channel_data.name,
                category=category,
                position=channel_data.position,
                bitrate=channel_data.bitrate,
                user_limit=channel_data.user_limit or 0
            )
        else:  # Default to text channel
            flogger.debug("Creating text channel")
            channel = await guild.create_text_channel(
                name=channel_data.name,
                category=category,
                position=channel_data.position,
                topic=channel_data.topic,
                nsfw=channel_data.nsfw,
                slowmode_delay=channel_data.slowmode_delay or 0
            )
        
        flogger.debug(f"Channel created: {channel.name} (ID: {channel.id})")
        
        channel_detail = ChannelConverter.channel_to_detail(channel)
        flogger.trace("Channel detail conversion completed")
        
        flogger.info(f"Successfully created {channel_data.type} channel {channel.name} (ID: {channel.id})")
        return ChannelDetailResponse(
            status="created",
            channel=channel_detail
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in create_channel for guild {guild_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in create_channel for guild {guild_id}: {exc}")
        await handle_discord_exception("create channel", exc)

# PUT endpoints

@router.put(
    "/channels/{channel_id}",
    response_model=ChannelDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Channel",
    description="Update a channel's properties"
)
async def update_channel(
    request: Request,
    channel_id: int,
    channel_data: ChannelUpdateRequest
) -> ChannelDetailResponse:
    """
    Update a channel's properties.
    
    Args:
        channel_id: The ID of the channel to update
        channel_data: Channel update parameters
        
    Returns:
        Details of the updated channel
    """
    flogger.info(f"update_channel endpoint called for channel_id: {channel_id}")
    flogger.debug(f"Starting channel update with data: {channel_data.dict()}")
    
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
        
        if isinstance(channel, discord.CategoryChannel):
            flogger.error(f"Channel {channel_id} is a category")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel_id} is a category. Use category endpoints instead."
            )
        
        flogger.trace("Channel type validation completed")
        
        # Get category if specified
        category = None
        if channel_data.category_id is not None:
            if channel_data.category_id == 0:
                category = None  # Remove from category
                flogger.trace("Will remove channel from category")
            else:
                flogger.trace(f"Looking up new category {channel_data.category_id}")
                category = channel.guild.get_channel(channel_data.category_id)
                if not category or not isinstance(category, discord.CategoryChannel):
                    flogger.error(f"Category {channel_data.category_id} not found")
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Category {channel_data.category_id} not found"
                    )
                flogger.trace(f"New category found: {category.name}")
        
        # Build update kwargs based on channel type and provided data
        update_kwargs = {}
        
        if channel_data.name is not None:
            update_kwargs["name"] = channel_data.name
            flogger.trace(f"Will update name to: {channel_data.name}")
        if channel_data.position is not None:
            update_kwargs["position"] = channel_data.position
            flogger.trace(f"Will update position to: {channel_data.position}")
        if category is not None or channel_data.category_id == 0:
            update_kwargs["category"] = category
            flogger.trace(f"Will update category to: {category.name if category else None}")
        
        if isinstance(channel, discord.TextChannel):
            if channel_data.topic is not None:
                update_kwargs["topic"] = channel_data.topic
                flogger.trace(f"Will update topic to: {channel_data.topic}")
            if channel_data.nsfw is not None:
                update_kwargs["nsfw"] = channel_data.nsfw
                flogger.trace(f"Will update nsfw to: {channel_data.nsfw}")
            if channel_data.slowmode_delay is not None:
                update_kwargs["slowmode_delay"] = channel_data.slowmode_delay
                flogger.trace(f"Will update slowmode_delay to: {channel_data.slowmode_delay}")
        elif isinstance(channel, discord.VoiceChannel):
            if channel_data.bitrate is not None:
                update_kwargs["bitrate"] = channel_data.bitrate
                flogger.trace(f"Will update bitrate to: {channel_data.bitrate}")
            if channel_data.user_limit is not None:
                update_kwargs["user_limit"] = channel_data.user_limit
                flogger.trace(f"Will update user_limit to: {channel_data.user_limit}")
        
        # Update channel with provided parameters
        if update_kwargs:
            flogger.debug(f"Applying updates: {update_kwargs}")
            await channel.edit(**update_kwargs)
        else:
            flogger.debug("No updates to apply")
        
        channel_detail = ChannelConverter.channel_to_detail(channel)
        flogger.trace("Channel detail conversion completed")
        
        flogger.info(f"Successfully updated channel {channel.name}")
        return ChannelDetailResponse(
            status="updated",
            channel=channel_detail
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in update_channel for channel {channel_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in update_channel for channel {channel_id}: {exc}")
        await handle_discord_exception("update channel", exc)

@router.put(
    "/channels/{channel_id}/permissions",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Channel Permissions",
    description="Replace all permission overwrites for a channel"
)
async def update_channel_permissions(
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
    flogger.info(f"update_channel_permissions endpoint called for channel_id: {channel_id}")
    flogger.debug(f"Starting permission update with {len(permissions_data.overwrites)} overwrites")
    
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
            target_id = overwrite_data.target_id
            tgt_type  = overwrite_data.type
            allow     = overwrite_data.allow or 0
            deny      = overwrite_data.deny  or 0
            
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
        
        message = f"Permissions updated for channel {channel.name}"
        flogger.info(message)
        return SuccessResponse(
            status="updated",
            message=message
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in update_channel_permissions for channel {channel_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in update_channel_permissions for channel {channel_id}: {exc}")
        await handle_discord_exception("update channel permissions", exc)

# DELETE endpoints

@router.delete(
    "/channels/{channel_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Channel",
    description="Delete a channel"
)
async def delete_channel(request: Request, channel_id: int) -> DeleteResponse:
    """
    Delete a channel.
    
    Args:
        channel_id: The ID of the channel to delete
        
    Returns:
        Deletion confirmation
    """
    flogger.info(f"delete_channel endpoint called for channel_id: {channel_id}")
    flogger.debug(f"Starting channel deletion")
    
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
        
        channel_name = channel.name
        channel_type = channel.type.name
        
        # Delete the channel
        flogger.debug(f"Deleting {channel_type} channel: {channel_name}")
        await channel.delete()
        
        message = f"{channel_type.title()} channel {channel_name} deleted"
        
        flogger.info(message)
        return DeleteResponse(
            status="deleted",
            deleted=True,
            message=message
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in delete_channel for channel {channel_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in delete_channel for channel {channel_id}: {exc}")
        await handle_discord_exception("delete channel", exc)
