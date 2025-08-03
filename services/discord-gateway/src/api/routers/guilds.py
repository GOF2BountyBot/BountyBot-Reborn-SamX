"""
Guild router for Discord Gateway API.

This module provides REST endpoints for managing Discord guilds
including listing guilds, getting guild details, and managing members.
"""

from typing import List
from fastapi import APIRouter, HTTPException, Request, status

import shared.bblogger as bblogger
from api.schemas.guild_schemas import GuildListResponse, GuildDetailResponse
from api.schemas.user_schemas import MemberListResponse
from utils.discord_converters import GuildConverter, UserConverter
from utils.discord_helpers import resolve_bot, get_entity_or_404, handle_discord_exception

flogger = bblogger.get_logger("gateway-guild-router")

router = APIRouter(
    prefix="/guilds",
    tags=["guilds"],
    responses={
        404: {"description": "Guild not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

# GET endpoints (ordered: List, Get Details, Get Extra Info)

@router.get(
    "",
    response_model=GuildListResponse,
    status_code=status.HTTP_200_OK,
    summary="List All Guilds",
    description="Get a list of all guilds the bot is a member of"
)
async def list_guilds(request: Request) -> GuildListResponse:
    """
    List all guilds the bot is a member of.
    
    Returns basic information about each guild including ID, name,
    icon, member count, and owner ID.
    """
    flogger.info("list_guilds endpoint called")
    flogger.debug("Starting guild list retrieval")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug(f"Bot resolved, found {len(bot.guilds)} guilds")
        
        guilds = []
        for guild in bot.guilds:
            flogger.trace(f"Processing guild: {guild.name} ({guild.id})")
            guild_summary = GuildConverter.guild_to_summary(guild)
            guilds.append(guild_summary)
        
        flogger.info(f"Successfully retrieved {len(guilds)} guilds")
        return GuildListResponse(
            status="success",
            guilds=guilds
        )
        
    except HTTPException:
        flogger.warning("HTTP exception occurred in list_guilds")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in list_guilds: {exc}")
        await handle_discord_exception("list guilds", exc)

@router.get(
    "/{guild_id}",
    response_model=GuildDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Guild Details",
    description="Get detailed information about a specific guild"
)
async def get_guild(request: Request, guild_id: int) -> GuildDetailResponse:
    """
    Get detailed information about a specific guild.
    
    Args:
        guild_id: The ID of the guild to retrieve
        
    Returns:
        Detailed guild information including settings, features, and metadata
    """
    flogger.info(f"get_guild endpoint called for guild_id: {guild_id}")
    flogger.debug(f"Starting guild detail retrieval for guild {guild_id}")
    
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
        
        guild_detail = GuildConverter.guild_to_detail(guild)
        flogger.trace(f"Guild detail conversion completed for {guild.name}")
        
        flogger.info(f"Successfully retrieved guild details for {guild.name}")
        return GuildDetailResponse(
            status="success",
            guild=guild_detail
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in get_guild for guild {guild_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_guild for guild {guild_id}: {exc}")
        await handle_discord_exception("get guild details", exc)

@router.get(
    "/{guild_id}/members",
    response_model=MemberListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Guild Members",
    description="Get a list of all members in a guild"
)
async def list_guild_members(request: Request, guild_id: int, limit: int = 1000) -> MemberListResponse:
    """
    Get a list of all members in a guild.
    
    Args:
        guild_id: The ID of the guild to get members from
        limit: Maximum number of members to return (default: 1000)
        
    Returns:
        List of guild members with user information and guild-specific data
    """
    flogger.info(f"list_guild_members endpoint called for guild_id: {guild_id}, limit: {limit}")
    flogger.debug(f"Starting member list retrieval for guild {guild_id}")
    
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
        
        # If guild members are not cached, fetch them
        if not guild.chunked:
            flogger.debug(f"Guild {guild_id} not chunked, fetching members...")
            await guild.chunk(cache=True)
            flogger.trace("Guild chunking completed")
        
        members = []
        member_count = 0
        
        flogger.debug(f"Processing up to {limit} members from {len(guild.members)} total members")
        for member in guild.members:
            if member_count >= limit:
                flogger.trace(f"Reached limit of {limit} members")
                break
                
            flogger.trace(f"Processing member: {member.display_name} ({member.id})")
            member_payload = UserConverter.member_to_payload(member)
            members.append(member_payload)
            member_count += 1
        
        flogger.info(f"Successfully retrieved {len(members)} members from guild {guild.name}")
        return MemberListResponse(
            status="success",
            members=members
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in list_guild_members for guild {guild_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in list_guild_members for guild {guild_id}: {exc}")
        await handle_discord_exception("list guild members", exc)
