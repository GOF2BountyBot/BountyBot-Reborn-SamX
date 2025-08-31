"""
Discord helper utilities for API operations.

This module provides generic helper functions for Discord operations
including bot resolution and error handling.
"""

import asyncio
from typing import Optional, Union, Dict, Any, List
import re
import discord
from discord.ext import commands
from fastapi import HTTPException, Request, status

import shared.bblogger as bblogger

flogger = bblogger.get_logger("discord-helpers")


async def resolve_bot(request: Request) -> commands.Bot:
    """
    Grab the running bot from FastAPI state and wait for readiness.
    """
    flogger.debug("resolve_bot called")
    bot = getattr(request.app.state, "bot", None)
    flogger.debug(f"resolve_bot: app.state.bot → {bot!r} (type={type(bot)})")

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


async def handle_discord_exception(operation: str, exc: Exception) -> None:
    """
    Handle common Discord exceptions and convert to HTTP exceptions.

    Args:
        operation: Description of the operation being performed
        exc: The Discord exception

    Raises:
        HTTPException: Appropriate HTTP exception
    """
    flogger.debug(f"handle_discord_exception called for operation: {operation}")

    # Gather diagnostic details to improve logging
    details: Dict[str, Any] = {
        "exc_type": exc.__class__.__name__,
        "exc_repr": repr(exc),
    }

    # try to extract common discord.HTTPException attributes safely
    exc_status: Optional[int] = None
    exc_code: Optional[int] = None
    try:
        exc_status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        exc_code = getattr(exc, "code", None)
        details["exc_status"] = exc_status
        details["exc_code"] = exc_code

        # Some discord exceptions carry a `response` / `http_response` with extra info.
        resp = getattr(exc, "response", None) or getattr(exc, "http_response", None)
        if resp is not None:
            try:
                rstatus = getattr(resp, "status", None) or getattr(resp, "status_code", None)
                details["response_status"] = rstatus
                # Defensive attempt to grab a body/text if present (may be coroutine or large)
                body = getattr(resp, "text", None) or getattr(resp, "body", None)
                if body and not callable(body):
                    details["response_text_preview"] = str(body)[:1000]
            except Exception:
                # Best-effort only — don't fail parsing diagnostic info
                pass
    except Exception:
        # Non-fatal if attribute extraction fails
        pass

    flogger.error(f"Discord exception during {operation}: {details}")

    # Map well-known discord exceptions to proper HTTP responses
    if isinstance(exc, discord.NotFound):
        flogger.error(f"Resource not found during {operation}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource not found during {operation}"
        )

    if isinstance(exc, discord.Forbidden):
        flogger.error(f"Insufficient permissions for {operation}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions for {operation}"
        )

    if isinstance(exc, discord.HTTPException):
        # Prefer explicit status mapping from the discord exception, fall back to heuristics
        if isinstance(exc_status, int):
            if 400 <= exc_status < 500:
                # Client / validation errors
                if exc_status == 403:
                    flogger.error(f"Discord returned 403 for {operation}: {details}")
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Insufficient permissions for {operation}: {exc_code or ''} {repr(exc)}"
                    )
                if exc_status == 404:
                    flogger.error(f"Discord returned 404 for {operation}: {details}")
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Resource not found during {operation}: {repr(exc)}"
                    )
                flogger.error(f"Discord returned {exc_status} for {operation}: {details}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Bad request during {operation}: {repr(exc)}"
                )
            else:
                # Upstream Discord server or gateway errors -> surface as Bad Gateway
                flogger.error(f"Discord upstream error {exc_status} during {operation}: {details}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Discord API error during {operation}: {repr(exc)}"
                )

        # If we couldn't determine a numeric status, treat as a 502 (upstream error)
        flogger.error(f"Unhandled discord.HTTPException during {operation}: {details}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Discord API error during {operation}: {repr(exc)}"
        )

    # Other exceptions: log full traceback then return 500
    flogger.exception(f"Unexpected error during {operation}: {exc}")
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Failed to {operation}: {exc}"
    )


async def get_entity_or_404(
    get_func,
    fetch_func,
    entity_id: int,
    entity_type: str
) -> Union[discord.Guild, discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel, discord.User, discord.Member, discord.Role]:
    """
    Generic function to get entity from cache or fetch from API.

    Args:
        get_func: Function to get entity from cache (e.g., bot.get_guild)
        fetch_func: Function to fetch entity from API (e.g., bot.fetch_guild)
        entity_id: ID of entity to retrieve
        entity_type: Type name for error messages

    Returns:
        The retrieved entity

    Raises:
        HTTPException: If entity not found or access denied
    """
    flogger.debug(f"get_entity_or_404 called for {entity_type} {entity_id}")

    entity = get_func(entity_id)
    if entity:
        flogger.trace(f"{entity_type} {entity_id} found in cache")
        return entity

    flogger.trace(f"{entity_type} {entity_id} not in cache, fetching from API")
    try:
        entity = await fetch_func(entity_id)
        flogger.trace(f"{entity_type} {entity_id} fetched from API")
        return entity
    except discord.NotFound:
        flogger.error(f"{entity_type} {entity_id} not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_type} {entity_id} not found"
        )
    except discord.Forbidden:
        flogger.error(f"No access to {entity_type} {entity_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No access to {entity_type} {entity_id}"
        )
    except discord.HTTPException as exc:
        # Delegate to the centralized handler so we get consistent logging and mapping
        await handle_discord_exception(f"fetch {entity_type} {entity_id}", exc)


def validate_guild_channel_relationship(channel, guild_id: int) -> None:
    """
    Validate that a channel belongs to the specified guild.

    Args:
        channel: Discord channel object
        guild_id: Expected guild ID

    Raises:
        HTTPException: If channel doesn't belong to guild
    """
    flogger.debug(f"validate_guild_channel_relationship called for channel {channel.id} and guild {guild_id}")

    if hasattr(channel, "guild") and getattr(channel.guild, "id", None) != guild_id:
        flogger.error(f"Channel {channel.id} belongs to guild {getattr(channel.guild, 'id', None)}, expected {guild_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Channel {channel.id} does not belong to guild {guild_id}"
        )
    flogger.trace(f"Channel {channel.id} belongs to correct guild {guild_id}")


def validate_channel_type(channel, expected_types: List[str], channel_id: int) -> None:
    """
    Validate that a channel is one of the expected types.

    Args:
        channel: Discord channel object
        expected_types: List of expected channel type names
        channel_id: Channel ID for error messages

    Raises:
        HTTPException: If channel is not of expected type
    """
    flogger.debug(f"validate_channel_type called for channel {channel_id}, expected types: {expected_types}")

    actual_type = getattr(channel.type, "name", str(channel.type))
    if actual_type not in expected_types:
        flogger.error(f"Channel {channel_id} is type {actual_type}, expected one of: {expected_types}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Channel {channel_id} is type {actual_type}, expected one of: {expected_types}"
        )
    flogger.trace(f"Channel {channel_id} type {actual_type} is valid")

def normalize_emoji(val: str) -> str:
    """
    Normalize an emoji input into the full unicode emoji string.

    Accepts:
      - a unicode emoji string (e.g. "🏷️", "📌")
      - a hex/codepoint string such as "1f4cc" or "1f1fa-1f1f8" or with prefixes "U+1F4CC" / "0x1f4cc"
      - concatenated hex codepoint strings like "1f3f7fe0f"
      - full custom emoji forms "<:name:id>" and "<a:name:id>" — returned as-is
      - short custom name form ":name:" — returned as "name" (without colons)

    Returns the normalized unicode emoji (or the original input on parse failure).
    """
    if not isinstance(val, str):
        return val
    s = val.strip()
    if not s:
        return s

    # If the value is a full custom emoji like <a:name:id> or <:name:id>, accept as-is.
    m_full_custom = re.fullmatch(r'^<a?:([A-Za-z0-9_~]+):(\d+)>$', s)
    if m_full_custom:
        return s  # keep full custom emoji notation

    # If the value is a short form like :name:, normalize to "name" (no colons).
    m_short = re.fullmatch(r'^:([A-Za-z0-9_~]+):$', s)
    if m_short:
        return m_short.group(1)

    # Remove common prefixes like "U+" or "0x" (single prefix)
    cleaned = re.sub(r'(?i)^(?:u\+|0x)', '', s)

    # If looks like hex codepoints separated by -, _, or whitespace, convert
    if re.fullmatch(r'[0-9A-Fa-f]+(?:[-_\s][0-9A-Fa-f]+)*', cleaned):
        parts = re.split(r'[-_\s]+', cleaned)
        # If there are explicit separators, just decode each part
        if len(parts) > 1:
            try:
                return ''.join(chr(int(p, 16)) for p in parts)
            except Exception:
                return val

        # Single concatenated hex string (no separators) — attempt to split into valid codepoints.
        hexstr = parts[0]

        # Backtracking splitter: try to partition hexstr into chunks of length 1..6
        # (unicode scalars fit in up to 6 hex digits), greedy longest-first to favor larger codepoints.
        from functools import lru_cache

        @lru_cache(maxsize=None)
        def try_split(idx):
            if idx == len(hexstr):
                return []
            # try lengths 6..1
            for L in range(6, 0, -1):
                if idx + L <= len(hexstr):
                    part = hexstr[idx:idx + L]
                    try:
                        cp = int(part, 16)
                        if cp <= 0x10FFFF:
                            rest = try_split(idx + L)
                            if rest is not None:
                                return [part] + rest
                    except Exception:
                        continue
            return None

        split = try_split(0)
        if split:
            try:
                return ''.join(chr(int(p, 16)) for p in split)
            except Exception:
                return val

    # Otherwise assume it's already a unicode emoji (or some other acceptable string)
    return s