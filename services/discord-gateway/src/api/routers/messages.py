"""
Message router for Discord Gateway API.

This module provides REST endpoints for managing Discord messages
with simplified URIs that don't require channel/guild context.
"""

from fastapi import APIRouter, HTTPException, Request, status
import discord
import shared.bblogger as bblogger
from api.schemas.message_schemas import MessageResponse, MessageUpdateRequest
from api.schemas.base_schemas import DeleteResponse
from utils.discord_helpers import resolve_bot, handle_discord_exception
from utils.embed_converter import EmbedConverter
from utils.discord_converters import MessageConverter

flogger = bblogger.get_logger("gateway-message-router")

router = APIRouter(
    tags=["messages"],
    responses={
        400: {"description": "Bad request - missing required parameters or invalid IDs"},
        404: {"description": "Message not found"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

@router.get(
    "/messages/{message_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Discord Message",
    description="Retrieve an existing Discord message"
)
async def get_message(request: Request, message_id: int) -> MessageResponse:
    """Retrieve an existing Discord message."""
    flogger.info(f"get_message called for message_id={message_id}")
    
    if message_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="message_id must be a positive integer"
        )
    
    try:
        bot = await resolve_bot(request)
        
        # Search for the message across all accessible channels
        message = None
        for guild in bot.guilds:
            for channel in guild.channels:
                if hasattr(channel, 'fetch_message'):
                    try:
                        message = await channel.fetch_message(message_id)
                        if message:
                            break
                    except discord.NotFound:
                        continue
                    except discord.Forbidden:
                        continue
            if message:
                break
        
        if not message:
            flogger.error(f"Message {message_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {message_id} not found"
            )
        
        message_data = MessageConverter.message_to_payload(message)
        flogger.info(f"Successfully retrieved message {message_id}")
        
        return MessageResponse(
            status="found",
            data=message_data
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_message: {exc}")
        await handle_discord_exception("get message", exc)

@router.put(
    "/messages/{message_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Discord Message",
    description="Update an existing Discord message with new embed content"
)
async def update_message(
    request: Request, message_id: int, payload: MessageUpdateRequest
) -> MessageResponse:
    """Update an existing Discord message."""
    flogger.info(f"update_message called for message_id={message_id}")
    
    if message_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="message_id must be a positive integer"
        )
    
    try:
        bot = await resolve_bot(request)
        
        # Search for the message across all accessible channels
        message = None
        for guild in bot.guilds:
            for channel in guild.channels:
                if hasattr(channel, 'fetch_message'):
                    try:
                        message = await channel.fetch_message(message_id)
                        if message:
                            break
                    except discord.NotFound:
                        continue
                    except discord.Forbidden:
                        continue
            if message:
                break
        
        if not message:
            flogger.error(f"Message {message_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {message_id} not found"
            )
        
        # Check if bot can edit this message (must be bot's own message)
        if message.author.id != bot.user.id:
            flogger.error(f"Cannot edit message {message_id} - not sent by bot")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Can only edit messages sent by the bot"
            )
        
        embed = EmbedConverter.payload_to_embed(payload.content)
        await message.edit(embed=embed)
        
        updated_message_data = MessageConverter.message_to_payload(message)
        flogger.info(f"Successfully updated message {message_id}")
        
        return MessageResponse(
            status="updated",
            data=updated_message_data
        )
    except HTTPException:
        raise
    except discord.HTTPException as exc:
        flogger.exception("Discord API error during update_message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Discord API error: {exc}"
        ) from exc
    except Exception as exc:
        flogger.exception("Unexpected error during update_message")
        await handle_discord_exception("update message", exc)

@router.delete(
    "/messages/{message_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Discord Message",
    description="Delete an existing Discord message"
)
async def delete_message(request: Request, message_id: int) -> DeleteResponse:
    """Delete an existing Discord message."""
    flogger.info(f"delete_message called for message_id={message_id}")
    
    if message_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="message_id must be a positive integer"
        )
    
    try:
        bot = await resolve_bot(request)
        
        # Search for the message across all accessible channels
        message = None
        for guild in bot.guilds:
            for channel in guild.channels:
                if hasattr(channel, 'fetch_message'):
                    try:
                        message = await channel.fetch_message(message_id)
                        if message:
                            break
                    except discord.NotFound:
                        continue
                    except discord.Forbidden:
                        continue
            if message:
                break
        
        if not message:
            flogger.error(f"Message {message_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {message_id} not found"
            )
        
        # Check if bot can delete this message
        if message.author.id != bot.user.id:
            # Check if bot has manage_messages permission in the channel
            channel = message.channel
            if hasattr(channel, 'guild'):
                bot_member = channel.guild.get_member(bot.user.id)
                if not bot_member or not channel.permissions_for(bot_member).manage_messages:
                    flogger.error(f"Cannot delete message {message_id} - insufficient permissions")
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Insufficient permissions to delete this message"
                    )
        
        await message.delete()
        
        message = f"Message {message_id} deleted"
        flogger.info(message)
        
        return DeleteResponse(
            status="deleted",
            deleted=True,
            message=message
        )
    except HTTPException:
        raise
    except discord.HTTPException as exc:
        flogger.exception("Discord API error during delete_message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Discord API error: {exc}"
        ) from exc
    except Exception as exc:
        flogger.exception("Unexpected error during delete_message")
        await handle_discord_exception("delete message", exc)