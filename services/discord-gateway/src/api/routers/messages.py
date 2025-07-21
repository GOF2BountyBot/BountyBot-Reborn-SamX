"""
Message router for Discord Gateway API.

This module provides REST endpoints for creating, updating, and deleting
Discord messages using standardized embed payloads. The service is completely
generic and contains no business logic or message-type-specific code.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, status
import requests
import discord
from discord.ext import commands

import shared.bblogger as bblogger
from api.schemas.message_schemas import (
    MessageRequest, 
    MessageUpdateRequest,
    MessageDeleteRequest,
    MessageResponse
)
from utils.embed_converter import EmbedConverter

flogger = bblogger.get_logger("gateway-message-router")

router = APIRouter(
    prefix="/messages",
    tags=["messages"],
    responses={
        400: {"description": "Bad request - missing required parameters"},
        404: {"description": "Message or channel not found"},
        500: {"description": "Internal server error"}
    }
)

def get_bot() -> commands.Bot:
    """Get the Discord bot instance from the module."""
    try:
        import bot
        if hasattr(bot, 'bot') and isinstance(bot.bot, commands.Bot):
            if not bot.bot.is_ready():
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Discord bot is not ready"
                )
            return bot.bot
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Bot instance not properly initialized"
            )
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not access bot module"
        )

@router.post(
    "",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Discord Message",
    description="Create a new Discord message with embed content"
)
async def create_message(request: MessageRequest) -> MessageResponse:
    """Create a Discord message from embed payload."""
    flogger.info(f"Creating message: guild={request.guild_id}, channel={request.channel_id}")
    
    try:
        bot_instance = get_bot()
        
        # Get the channel
        channel = bot_instance.get_channel(request.channel_id)
        if not channel:
            try:
                channel = await bot_instance.fetch_channel(request.channel_id)
            except discord.NotFound:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {request.channel_id} not found"
                )
            except discord.Forbidden:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"No access to channel {request.channel_id}"
                )
        
        # Verify guild matches
        if hasattr(channel, 'guild') and channel.guild.id != request.guild_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {request.channel_id} does not belong to guild {request.guild_id}"
            )
        
        # Convert payload to embed using generic converter
        embed = EmbedConverter.payload_to_embed(request.content)
        
        # Send the message
        message = await channel.send(embed=embed)
        flogger.info(f"Message created: guild={request.guild_id}, channel={request.channel_id}, message={message.id}")
        
        return MessageResponse(
            status="created",
            guild_id=request.guild_id,
            channel_id=request.channel_id,
            message_id=message.id,
            timestamp=message.created_at
        )
        
    except HTTPException:
        raise
    except discord.HTTPException as e:
        flogger.error(f"Discord API error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Discord API error: {str(e)}"
        )
    except Exception as e:
        flogger.error(f"Error creating message: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create message: {str(e)}"
        )

@router.put(
    "",
    response_model=MessageResponse,
    summary="Update Discord Message",
    description="Update an existing Discord message with new embed content"
)
async def update_message(request: MessageUpdateRequest) -> MessageResponse:
    """Update an existing Discord message."""
    flogger.info(f"Updating message: guild={request.guild_id}, channel={request.channel_id}, message={request.message_id}")
    
    try:
        bot_instance = get_bot()
        
        # Get the channel
        channel = bot_instance.get_channel(request.channel_id)
        if not channel:
            try:
                channel = await bot_instance.fetch_channel(request.channel_id)
            except discord.NotFound:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {request.channel_id} not found"
                )
        
        # Verify guild matches
        if hasattr(channel, 'guild') and channel.guild.id != request.guild_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {request.channel_id} does not belong to guild {request.guild_id}"
            )
        
        # Get the message
        try:
            message = await channel.fetch_message(request.message_id)
        except discord.NotFound:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {request.message_id} not found in channel {request.channel_id}"
            )
        
        # Convert payload to embed using generic converter
        embed = EmbedConverter.payload_to_embed(request.content)
        
        # Update the message
        await message.edit(embed=embed)
        flogger.info(f"Message updated: guild={request.guild_id}, channel={request.channel_id}, message={request.message_id}")
        
        return MessageResponse(
            status="updated",
            guild_id=request.guild_id,
            channel_id=request.channel_id,
            message_id=request.message_id,
            timestamp=message.edited_at or message.created_at
        )
        
    except HTTPException:
        raise
    except discord.HTTPException as e:
        flogger.error(f"Discord API error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Discord API error: {str(e)}"
        )
    except Exception as e:
        flogger.error(f"Error updating message: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update message: {str(e)}"
        )

@router.delete(
    "",
    response_model=MessageResponse,
    summary="Delete Discord Message",
    description="Delete an existing Discord message"
)
async def delete_message(request: MessageDeleteRequest) -> MessageResponse:
    """Delete an existing Discord message."""
    flogger.info(f"Deleting message: guild={request.guild_id}, channel={request.channel_id}, message={request.message_id}")
    
    try:
        bot_instance = get_bot()
        
        # Get the channel
        channel = bot_instance.get_channel(request.channel_id)
        if not channel:
            try:
                channel = await bot_instance.fetch_channel(request.channel_id)
            except discord.NotFound:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {request.channel_id} not found"
                )
        
        # Verify guild matches
        if hasattr(channel, 'guild') and channel.guild.id != request.guild_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {request.channel_id} does not belong to guild {request.guild_id}"
            )
        
        # Get and delete the message
        try:
            message = await channel.fetch_message(request.message_id)
            await message.delete()
        except discord.NotFound:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {request.message_id} not found in channel {request.channel_id}"
            )
        
        flogger.info(f"Message deleted: guild={request.guild_id}, channel={request.channel_id}, message={request.message_id}")
        
        return MessageResponse(
            status="deleted",
            guild_id=request.guild_id,
            channel_id=request.channel_id,
            message_id=request.message_id  # Return the provided message_id as requested
        )
        
    except HTTPException:
        raise
    except discord.HTTPException as e:
        flogger.error(f"Discord API error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Discord API error: {str(e)}"
        )
    except Exception as e:
        flogger.error(f"Error deleting message: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete message: {str(e)}"
        )

@router.get(
    "/{guild_id}/{channel_id}/{message_id}",
    response_model=MessageResponse,
    summary="Get Discord Message",
    description="Retrieve an existing Discord message and convert to payload format"
)
async def get_message(guild_id: int, channel_id: int, message_id: int) -> MessageResponse:
    """Get a Discord message and convert to payload format using generic converter."""
    flogger.info(f"Getting message: guild={guild_id}, channel={channel_id}, message={message_id}")
    
    try:
        bot_instance = get_bot()
        
        # Get the channel
        channel = bot_instance.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot_instance.fetch_channel(channel_id)
            except discord.NotFound:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {channel_id} not found"
                )
        
        # Verify guild matches
        if hasattr(channel, 'guild') and channel.guild.id != guild_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel_id} does not belong to guild {guild_id}"
            )
        
        # Get the message
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {message_id} not found in channel {channel_id}"
            )
        
        # Convert embed to payload using generic converter (if message has embed)
        content = None
        if message.embeds:
            content = EmbedConverter.embed_to_payload(message.embeds[0])
        
        return MessageResponse(
            status="found",
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=message_id,
            content=content,
            timestamp=message.created_at
        )
        
    except HTTPException:
        raise
    except discord.HTTPException as e:
        flogger.error(f"Discord API error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Discord API error: {str(e)}"
        )
    except Exception as e:
        flogger.error(f"Error getting message: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get message: {str(e)}"
        )
