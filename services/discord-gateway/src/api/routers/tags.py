"""
Tags router for Discord Gateway API.

This module provides REST endpoints for managing Discord forum tags
with simplified URIs that don't require channel context.
"""

from fastapi import APIRouter, HTTPException, Request, status
import discord
import shared.bblogger as bblogger
from api.schemas.channel_schemas import ForumTagResponse, ForumTagUpdateRequest
from api.schemas.base_schemas import DeleteResponse
from utils.discord_helpers import resolve_bot, handle_discord_exception, normalize_emoji
from utils.discord_converters import ChannelConverter

flogger = bblogger.get_logger("gateway-tag-router")

router = APIRouter(
    tags=["tags"],
    responses={
        404: {"description": "Tag not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

@router.get(
    "/tags/{tag_id}",
    response_model=ForumTagResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Forum Tag",
    description="Get details for a single forum tag"
)
async def get_tag(request: Request, tag_id: int) -> ForumTagResponse:
    """Get details for a single forum tag."""
    flogger.info(f"get_tag called for tag_id={tag_id}")
    try:
        bot = await resolve_bot(request)
        
        # Search for the tag across all forum channels
        tag = None
        parent_channel = None
        for guild in bot.guilds:
            for channel in guild.channels:
                if isinstance(channel, discord.ForumChannel):
                    tag = discord.utils.get(channel.available_tags, id=tag_id)
                    if tag:
                        parent_channel = channel
                        break
            if tag:
                break
        
        if not tag:
            flogger.error(f"Tag {tag_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tag {tag_id} not found"
            )
        
        tag_data = ChannelConverter.forum_tag_to_payload(tag)
        # Add channel_id to the tag data since it's not in the original payload
        tag_data.channel_id = parent_channel.id
        
        flogger.info(f"Successfully retrieved tag {tag.name}")
        return ForumTagResponse(
            status="success",
            data=tag_data
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_tag: {exc}")
        await handle_discord_exception("get tag", exc)

@router.put(
    "/tags/{tag_id}",
    response_model=ForumTagResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Forum Tag",
    description="Update a forum tag's properties"
)
async def update_tag(
    request: Request, tag_id: int, tag_data: ForumTagUpdateRequest
) -> ForumTagResponse:
    """Update a forum tag's properties."""
    flogger.info(f"update_tag called for tag_id={tag_id}")
    try:
        bot = await resolve_bot(request)
        
        # Search for the tag across all forum channels
        tag = None
        parent_channel = None
        for guild in bot.guilds:
            for channel in guild.channels:
                if isinstance(channel, discord.ForumChannel):
                    tag = discord.utils.get(channel.available_tags, id=tag_id)
                    if tag:
                        parent_channel = channel
                        break
            if tag:
                break
        
        if not tag or not parent_channel:
            flogger.error(f"Tag {tag_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tag {tag_id} not found"
            )
        
        # Prepare update parameters
        update_kwargs = {}
        if tag_data.name is not None:
            update_kwargs["name"] = tag_data.name
        if tag_data.emoji is not None:
            try:
                emoji_value = normalize_emoji(tag_data.emoji)
                update_kwargs["emoji"] = emoji_value
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid emoji: {tag_data.emoji}"
                )
        
        # Update the tag
        if update_kwargs:
            await parent_channel.edit_tag(tag, **update_kwargs)
        
        # Re-fetch the tag to get updated data
        updated_tag = discord.utils.get(parent_channel.available_tags, id=tag_id)
        if not updated_tag:
            # Fallback - the tag might have changed ID, search by name
            if tag_data.name:
                updated_tag = discord.utils.get(parent_channel.available_tags, name=tag_data.name)
        
        if not updated_tag:
            updated_tag = tag  # Use original if we can't find updated
        
        updated_tag_data = ChannelConverter.forum_tag_to_payload(updated_tag)
        updated_tag_data.channel_id = parent_channel.id
        
        flogger.info(f"Successfully updated tag {updated_tag.name}")
        return ForumTagResponse(
            status="updated",
            data=updated_tag_data
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in update_tag: {exc}")
        await handle_discord_exception("update tag", exc)

@router.delete(
    "/tags/{tag_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Forum Tag",
    description="Remove a tag from its forum channel"
)
async def delete_tag(request: Request, tag_id: int) -> DeleteResponse:
    """Remove a tag from its forum channel."""
    flogger.info(f"delete_tag called for tag_id={tag_id}")
    try:
        bot = await resolve_bot(request)
        
        # Search for the tag across all forum channels
        tag = None
        parent_channel = None
        for guild in bot.guilds:
            for channel in guild.channels:
                if isinstance(channel, discord.ForumChannel):
                    tag = discord.utils.get(channel.available_tags, id=tag_id)
                    if tag:
                        parent_channel = channel
                        break
            if tag:
                break
        
        if not tag or not parent_channel:
            flogger.error(f"Tag {tag_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tag {tag_id} not found"
            )
        
        tag_name = tag.name
        await parent_channel.delete_tag(tag)
        
        message = f"Tag {tag_name} deleted"
        flogger.info(message)
        
        return DeleteResponse(
            status="deleted",
            deleted=True,
            message=message
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in delete_tag: {exc}")
        await handle_discord_exception("delete tag", exc)