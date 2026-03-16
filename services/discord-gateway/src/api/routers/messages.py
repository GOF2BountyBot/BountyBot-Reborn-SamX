"""
Message router for Discord Gateway API.

This module provides REST endpoints for managing Discord messages
with simplified URIs that don't require channel/guild context.
"""

import asyncio

import discord
from fastapi import APIRouter, HTTPException, Request, status
from shared import bblogger
from utils.discord_converters import MessageConverter
from utils.discord_helpers import handle_discord_exception, resolve_bot
from utils.embed_converter import EmbedConverter

from api.schemas.base_schemas import DeleteResponse
from api.schemas.message_schemas import MessageResponse, MessageUpdateRequest

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


async def _find_message(bot: discord.Client, message_id: int, logger) -> discord.Message | None:
    """
    Try to locate a message by id:
      1) fast-path: check common caches if present
      2) fallback: scan guilds/channels but use a short per-channel timeout

    Returns discord.Message or None if not found.
    """
    # Fast path: try common cache attributes (best-effort)
    # Many bots/plugins expose some cached message collections — don't rely on them, but try.
    cached_attrs = ("cached_messages", "messages_cache", "_message_cache")
    for attr in cached_attrs:
        cached = getattr(bot, attr, None)
        if cached:
            try:
                # cached might be an iterable of messages or mapping
                if isinstance(cached, dict):
                    for m in cached.values():
                        if getattr(m, "id", None) == message_id:
                            logger.trace(f"Found message {message_id} in bot.{attr}")
                            return m
                else:
                    for m in cached:
                        if getattr(m, "id", None) == message_id:
                            logger.trace(f"Found message {message_id} in bot.{attr}")
                            return m
            except Exception:  # pylint: disable=broad-exception-caught
                # don't fail on unexpected cache shapes
                pass

    # Fallback: scan guilds/channels. Use a short timeout per channel to avoid very long blocking loops.
    for guild in getattr(bot, "guilds", []):
        for channel in getattr(guild, "channels", []):
            if not hasattr(channel, "fetch_message"):
                continue
            try:
                # small timeout to avoid long waits across many channels
                msg = await asyncio.wait_for(channel.fetch_message(message_id), timeout=2.0)
                if msg:
                    logger.trace(f"Found message {message_id} in channel {getattr(channel, 'id', None)}")
                    return msg
            except discord.NotFound:
                # message not in this channel
                continue
            except discord.Forbidden:
                # no access to this channel — skip
                continue
            except TimeoutError:
                logger.debug(
                    f"fetch_message timeout for channel {getattr(channel, 'id', None)} "
                    f"while searching for {message_id}"
                )
                continue
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # best-effort: log and continue searching other channels
                logger.debug(
                    f"Unexpected error fetching message {message_id} "
                    f"from channel {getattr(channel, 'id', None)}: {exc}"
                )
                continue

    return None


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

        message = await _find_message(bot, message_id, flogger)

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
    except Exception as exc:  # pylint: disable=broad-exception-caught
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

        message = await _find_message(bot, message_id, flogger)

        if not message:
            flogger.error(f"Message {message_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {message_id} not found"
            )

        # Check if bot can edit this message (must be bot's own message)
        if not getattr(message, "author", None) or message.author.id != getattr(bot.user, "id", None):
            flogger.error(f"Cannot edit message {message_id} - not sent by bot")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Can only edit messages sent by the bot"
            )

        embed = EmbedConverter.payload_to_embed(payload.content)
        await message.edit(embed=embed)

        # After edit, message object may be updated in-place by discord.py — re-read for payload conversion
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
    except Exception as exc:  # pylint: disable=broad-exception-caught
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

        message = await _find_message(bot, message_id, flogger)

        if not message:
            flogger.error(f"Message {message_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {message_id} not found"
            )

        # Check if bot can delete this message
        if not getattr(message, "author", None) or message.author.id != getattr(bot.user, "id", None):
            # Check if bot has manage_messages permission in the channel
            channel = getattr(message, "channel", None)
            if channel and hasattr(channel, "guild"):
                bot_member = channel.guild.get_member(getattr(bot.user, "id", None))
                if not bot_member or not channel.permissions_for(bot_member).manage_messages:
                    flogger.error(f"Cannot delete message {message_id} - insufficient permissions")
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Insufficient permissions to delete this message"
                    )

        await message.delete()

        msg = f"Message {message_id} deleted"
        flogger.info(msg)

        return DeleteResponse(
            status="deleted",
            deleted=True,
            message=msg
        )
    except HTTPException:
        raise
    except discord.HTTPException as exc:
        flogger.exception("Discord API error during delete_message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Discord API error: {exc}"
        ) from exc
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.exception("Unexpected error during delete_message")
        await handle_discord_exception("delete message", exc)
