"""
Message router for Discord Gateway API.

This module defines REST endpoints for creating, updating, deleting,
and retrieving Discord messages with standardized embed payloads.
"""
from typing import Optional
import asyncio
import discord
from discord.ext import commands
from fastapi import APIRouter, HTTPException, Request, status

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
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)


async def _resolve_bot(request: Request) -> commands.Bot:
    """
    Grab the running bot from FastAPI state and wait for readiness.
    """
    bot = getattr(request.app.state, "bot", None)
    flogger.debug(f"_resolve_bot: app.state.bot → {bot!r} (type={type(bot)})")
    if not isinstance(bot, commands.Bot):
        flogger.error("app.state.bot is not a commands.Bot instance")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Bot instance invalid"
        )

    if not bot.is_ready():
        flogger.info("Bot not ready, awaiting wait_until_ready()")
        try:
            await asyncio.wait_for(bot.wait_until_ready(), timeout=15)
        except asyncio.TimeoutError:
            flogger.error("Timed out waiting for Discord bot to become ready")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Discord bot is not ready"
            )

    flogger.debug("Bot instance resolved and ready")
    return bot


@router.post(
    "",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Discord Message",
    description="Create a new Discord message with embed content"
)
async def create_message(
    request: Request,
    payload: MessageRequest
) -> MessageResponse:
    flogger.info(f"create_message called: guild={payload.guild_id}, channel={payload.channel_id}")
    flogger.debug(f"Request payload: {payload.dict()}")

    try:
        bot = await _resolve_bot(request)

        # Resolve channel
        channel = bot.get_channel(payload.channel_id)
        if channel:
            flogger.debug(f"Channel fetched from cache: {channel.id}")
        else:
            flogger.debug(f"Channel not in cache, fetching: {payload.channel_id}")
            try:
                channel = await bot.fetch_channel(payload.channel_id)
                flogger.debug(f"Channel fetched via API: {channel.id}")
            except discord.NotFound:
                flogger.error(f"Channel {payload.channel_id} not found")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {payload.channel_id} not found"
                )
            except discord.Forbidden:
                flogger.error(f"No access to channel {payload.channel_id}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"No access to channel {payload.channel_id}"
                )

        # Verify guild association
        if hasattr(channel, "guild") and channel.guild.id != payload.guild_id:
            flogger.error(
                f"Channel {channel.id} belongs to guild {channel.guild.id}, expected {payload.guild_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel.id} does not belong to guild {payload.guild_id}"
            )

        # Build embed & send
        embed = EmbedConverter.payload_to_embed(payload.content)
        flogger.debug(f"Embed built: {embed.to_dict()}")
        message = await channel.send(embed=embed)
        flogger.info(f"Message created: id={message.id}, timestamp={message.created_at}")

        return MessageResponse(
            status="created",
            guild_id=payload.guild_id,
            channel_id=payload.channel_id,
            message_id=message.id,
            timestamp=message.created_at
        )

    except HTTPException:
        raise
    except discord.HTTPException as exc:
        flogger.exception("Discord API error during create_message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Discord API error: {exc}"
        ) from exc
    except Exception as exc:
        flogger.exception("Unexpected error during create_message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create message: {exc}"
        ) from exc


@router.put(
    "",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Discord Message",
    description="Update an existing Discord message with new embed content"
)
async def update_message(
    request: Request,
    payload: MessageUpdateRequest
) -> MessageResponse:
    flogger.info(
        f"update_message called: guild={payload.guild_id}, "
        f"channel={payload.channel_id}, message={payload.message_id}"
    )
    flogger.debug(f"Request payload: {payload.dict()}")

    try:
        bot = await _resolve_bot(request)

        # Resolve channel
        channel = bot.get_channel(payload.channel_id)
        if channel:
            flogger.debug(f"Channel fetched from cache: {channel.id}")
        else:
            flogger.debug(f"Channel not in cache, fetching: {payload.channel_id}")
            try:
                channel = await bot.fetch_channel(payload.channel_id)
                flogger.debug(f"Channel fetched via API: {channel.id}")
            except discord.NotFound:
                flogger.error(f"Channel {payload.channel_id} not found")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {payload.channel_id} not found"
                )

        # Verify guild association
        if hasattr(channel, "guild") and channel.guild.id != payload.guild_id:
            flogger.error(
                f"Channel {channel.id} belongs to guild {channel.guild.id}, expected {payload.guild_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel.id} does not belong to guild {payload.guild_id}"
            )

        # Fetch & edit message
        try:
            message = await channel.fetch_message(payload.message_id)
            flogger.debug(f"Message fetched: id={message.id}")
        except discord.NotFound:
            flogger.error(f"Message {payload.message_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {payload.message_id} not found"
            )

        embed = EmbedConverter.payload_to_embed(payload.content)
        flogger.debug(f"Embed built for update: {embed.to_dict()}")
        await message.edit(embed=embed)
        flogger.info(f"Message updated: id={message.id}")

        return MessageResponse(
            status="updated",
            guild_id=payload.guild_id,
            channel_id=payload.channel_id,
            message_id=payload.message_id,
            timestamp=message.edited_at or message.created_at
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update message: {exc}"
        ) from exc


@router.delete(
    "",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Discord Message",
    description="Delete an existing Discord message"
)
async def delete_message(
    request: Request,
    payload: MessageDeleteRequest
) -> MessageResponse:
    flogger.info(
        f"delete_message called: guild={payload.guild_id}, "
        f"channel={payload.channel_id}, message={payload.message_id}"
    )
    flogger.debug(f"Request payload: {payload.dict()}")

    try:
        bot = await _resolve_bot(request)

        # Resolve channel
        channel = bot.get_channel(payload.channel_id)
        if channel:
            flogger.debug(f"Channel fetched from cache: {channel.id}")
        else:
            flogger.debug(f"Channel not in cache, fetching: {payload.channel_id}")
            try:
                channel = await bot.fetch_channel(payload.channel_id)
                flogger.debug(f"Channel fetched via API: {channel.id}")
            except discord.NotFound:
                flogger.error(f"Channel {payload.channel_id} not found")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {payload.channel_id} not found"
                )

        # Verify guild association
        if hasattr(channel, "guild") and channel.guild.id != payload.guild_id:
            flogger.error(
                f"Channel {channel.id} belongs to guild {channel.guild.id}, expected {payload.guild_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel.id} does not belong to guild {payload.guild_id}"
            )

        # Fetch & delete message
        try:
            message = await channel.fetch_message(payload.message_id)
            flogger.debug(f"Message fetched for deletion: id={message.id}")
            await message.delete()
            flogger.info(f"Message deleted: id={message.id}")
        except discord.NotFound:
            flogger.error(f"Message {payload.message_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {payload.message_id} not found"
            )

        return MessageResponse(
            status="deleted",
            guild_id=payload.guild_id,
            channel_id=payload.channel_id,
            message_id=payload.message_id
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete message: {exc}"
        ) from exc


@router.get(
    "/{guild_id}/{channel_id}/{message_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Discord Message",
    description="Retrieve an existing Discord message and convert to payload format"
)
async def get_message(
    request: Request,
    guild_id: int,
    channel_id: int,
    message_id: int
) -> MessageResponse:
    flogger.info(f"get_message called: guild={guild_id}, channel={channel_id}, message={message_id}")

    try:
        bot = await _resolve_bot(request)

        # Resolve channel
        channel = bot.get_channel(channel_id)
        if channel:
            flogger.debug(f"Channel fetched from cache: {channel.id}")
        else:
            flogger.debug(f"Channel not in cache, fetching: {channel_id}")
            try:
                channel = await bot.fetch_channel(channel_id)
                flogger.debug(f"Channel fetched via API: {channel.id}")
            except discord.NotFound:
                flogger.error(f"Channel {channel_id} not found")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Channel {channel_id} not found"
                )

        # Verify guild association
        if hasattr(channel, "guild") and channel.guild.id != guild_id:
            flogger.error(
                f"Channel {channel.id} belongs to guild {channel.guild.id}, expected {guild_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel.id} does not belong to guild {guild_id}"
            )

        # Fetch message
        try:
            message = await channel.fetch_message(message_id)
            flogger.debug(f"Message fetched: id={message.id}")
        except discord.NotFound:
            flogger.error(f"Message {message_id} not found in channel {channel.id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {message_id} not found in channel {channel.id}"
            )

        # Convert to payload
        content: Optional[dict] = None
        if message.embeds:
            content = EmbedConverter.embed_to_payload(message.embeds[0]).dict()
            flogger.debug(f"Payload extracted from embed: {content}")

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
    except discord.HTTPException as exc:
        flogger.exception("Discord API error during get_message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Discord API error: {exc}"
        ) from exc
    except Exception as exc:
        flogger.exception("Unexpected error during get_message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get message: {exc}"
        ) from exc