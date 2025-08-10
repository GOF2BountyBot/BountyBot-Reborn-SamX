"""
Discord helper utilities for API operations.

This module provides generic helper functions for Discord operations
including bot resolution and error handling.
"""

import asyncio
from typing import Optional, Union, Dict, Any, List
import discord
from discord.ext import commands
from fastapi import HTTPException, Request, status

import shared.bblogger as bblogger

flogger = bblogger.get_logger("discord-helpers")

async def resolve_bot(request: Request) -> commands.Bot:
    """
    Grab the running bot from FastAPI state and wait for readiness.
    Uses same implementation as _resolve_bot from messages.py.
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
    
    if isinstance(exc, discord.NotFound):
        flogger.error(f"Resource not found during {operation}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource not found during {operation}"
        )
    elif isinstance(exc, discord.Forbidden):
        flogger.error(f"Insufficient permissions for {operation}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions for {operation}"
        )
    elif isinstance(exc, discord.HTTPException):
        flogger.error(f"Discord API error during {operation}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Discord API error during {operation}: {exc}"
        )
    else:
        flogger.exception(f"Unexpected error during {operation}")
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
    
    if hasattr(channel, "guild") and channel.guild.id != guild_id:
        flogger.error(f"Channel {channel.id} belongs to guild {channel.guild.id}, expected {guild_id}")
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
    
    actual_type = channel.type.name
    if actual_type not in expected_types:
        flogger.error(f"Channel {channel_id} is type {actual_type}, expected one of: {expected_types}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Channel {channel_id} is type {actual_type}, expected one of: {expected_types}"
        )
    flogger.trace(f"Channel {channel_id} type {actual_type} is valid")
