"""
User router for Discord Gateway API.

This module provides REST endpoints for managing Discord users and members
including getting user details, member info, and performing member actions.
"""

from fastapi import APIRouter, HTTPException, Request, status

import shared.bblogger as bblogger
from api.schemas.user_schemas import (
    UserDetailResponse, MemberDetailResponse, MemberUpdateRequest
)
from api.schemas.base_schemas import SuccessResponse
from utils.discord_converters import UserConverter
from utils.discord_helpers import resolve_bot, get_entity_or_404, handle_discord_exception

flogger = bblogger.get_logger("gateway-user-router")

router = APIRouter(
    tags=["users"],
    responses={
        404: {"description": "User, member, or guild not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

# GET endpoints (ordered: List, Get Details, Get Extra Info)

@router.get(
    "/users/@me",
    response_model=UserDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Bot Identity",
    description="Get information about the bot user"
)
async def get_bot_identity(request: Request) -> UserDetailResponse:
    """
    Get information about the bot user.
    
    Returns:
        Bot user information
    """
    flogger.info("get_bot_identity endpoint called")
    flogger.debug("Starting bot identity retrieval")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        user_payload = UserConverter.user_to_payload(bot.user)
        flogger.trace(f"Bot identity conversion completed for {bot.user.name}")
        
        flogger.info(f"Successfully retrieved bot identity: {bot.user.name}")
        return UserDetailResponse(
            status="success",
            user=user_payload
        )
        
    except HTTPException:
        flogger.warning("HTTP exception occurred in get_bot_identity")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_bot_identity: {exc}")
        await handle_discord_exception("get bot identity", exc)

@router.get(
    "/users/{user_id}",
    response_model=UserDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get User Details",
    description="Get information about a specific user"
)
async def get_user(request: Request, user_id: int) -> UserDetailResponse:
    """
    Get information about a specific user.
    
    Args:
        user_id: The ID of the user to retrieve
        
    Returns:
        User information
    """
    flogger.info(f"get_user endpoint called for user_id: {user_id}")
    flogger.debug(f"Starting user detail retrieval for user {user_id}")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        user = await get_entity_or_404(
            bot.get_user,
            bot.fetch_user,
            user_id,
            "User"
        )
        flogger.debug(f"User retrieved: {user.name}")
        
        user_payload = UserConverter.user_to_payload(user)
        flogger.trace(f"User detail conversion completed for {user.name}")
        
        flogger.info(f"Successfully retrieved user details for {user.name}")
        return UserDetailResponse(
            status="success",
            user=user_payload
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in get_user for user {user_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_user for user {user_id}: {exc}")
        await handle_discord_exception("get user details", exc)

@router.get(
    "/guilds/{guild_id}/members/{user_id}",
    response_model=MemberDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Member Details",
    description="Get information about a guild member"
)
async def get_member(request: Request, guild_id: int, user_id: int) -> MemberDetailResponse:
    """
    Get information about a guild member.
    
    Args:
        guild_id: The ID of the guild
        user_id: The ID of the user/member to retrieve
        
    Returns:
        Member information including guild-specific data
    """
    flogger.info(f"get_member endpoint called for guild_id: {guild_id}, user_id: {user_id}")
    flogger.debug(f"Starting member detail retrieval for user {user_id}")
    
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
        
        member = guild.get_member(user_id)
        if not member:
            flogger.trace(f"Member {user_id} not in cache, fetching")
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                flogger.error(f"Member {user_id} not found in guild {guild_id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Member {user_id} not found in guild {guild_id}"
                )
        
        flogger.debug(f"Member retrieved: {member.display_name}")
        
        member_payload = UserConverter.member_to_payload(member)
        flogger.trace(f"Member detail conversion completed for {member.display_name}")
        
        flogger.info(f"Successfully retrieved member details for {member.display_name}")
        return MemberDetailResponse(
            status="success",
            member=member_payload
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in get_member for member {user_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_member for member {user_id}: {exc}")
        await handle_discord_exception("get member details", exc)

# PUT endpoints

@router.put(
    "/guilds/{guild_id}/members/{user_id}",
    response_model=MemberDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Member",
    description="Update a guild member's properties (wrapper for PATCH)"
)
async def update_member(
    request: Request,
    guild_id: int,
    user_id: int,
    member_data: MemberUpdateRequest
) -> MemberDetailResponse:
    """
    Update a guild member's properties.
    
    Args:
        guild_id: The ID of the guild
        user_id: The ID of the member to update
        member_data: Member update parameters
        
    Returns:
        Updated member information
    """
    flogger.info(f"update_member endpoint called for guild_id: {guild_id}, user_id: {user_id}")
    flogger.debug(f"Starting member update with data: {member_data.dict()}")
    
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
        
        member = guild.get_member(user_id)
        if not member:
            flogger.trace(f"Member {user_id} not in cache, fetching")
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                flogger.error(f"Member {user_id} not found in guild {guild_id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Member {user_id} not found in guild {guild_id}"
                )
        
        flogger.debug(f"Member retrieved: {member.display_name}")
        
        # Build update kwargs
        update_kwargs = {}
        
        if member_data.nick is not None:
            update_kwargs["nick"] = member_data.nick
            flogger.trace(f"Will update nick to: {member_data.nick}")
        if member_data.mute is not None:
            update_kwargs["mute"] = member_data.mute
            flogger.trace(f"Will update mute to: {member_data.mute}")
        if member_data.deaf is not None:
            update_kwargs["deafen"] = member_data.deaf
            flogger.trace(f"Will update deafen to: {member_data.deaf}")
        
        # Handle role updates
        if member_data.roles is not None:
            roles = []
            flogger.debug(f"Processing {len(member_data.roles)} role assignments")
            for role_id in member_data.roles:
                role = guild.get_role(role_id)
                if not role:
                    flogger.error(f"Role {role_id} not found in guild {guild_id}")
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Role {role_id} not found in guild {guild_id}"
                    )
                roles.append(role)
                flogger.trace(f"Added role to assignment: {role.name}")
            update_kwargs["roles"] = roles
        
        # Handle voice channel move
        if member_data.channel_id is not None:
            if member_data.channel_id == 0:
                # Disconnect from voice
                update_kwargs["voice_channel"] = None
                flogger.trace("Will disconnect member from voice")
            else:
                voice_channel = guild.get_channel(member_data.channel_id)
                if not voice_channel:
                    flogger.error(f"Voice channel {member_data.channel_id} not found")
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Voice channel {member_data.channel_id} not found"
                    )
                update_kwargs["voice_channel"] = voice_channel
                flogger.trace(f"Will move member to voice channel: {voice_channel.name}")
        
        # Update member
        if update_kwargs:
            flogger.debug(f"Applying updates: {update_kwargs}")
            await member.edit(**update_kwargs)
        else:
            flogger.debug("No updates to apply")
        
        # Return updated member data
        member_payload = UserConverter.member_to_payload(member)
        flogger.trace("Member detail conversion completed")
        
        flogger.info(f"Successfully updated member {member.display_name}")
        return MemberDetailResponse(
            status="updated",
            member=member_payload
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in update_member for member {user_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in update_member for member {user_id}: {exc}")
        await handle_discord_exception("update member", exc)

# DELETE endpoints

@router.delete(
    "/guilds/{guild_id}/members/{user_id}",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Kick Member",
    description="Kick a user from a guild"
)
async def kick_member(request: Request, guild_id: int, user_id: int, reason: str = None) -> SuccessResponse:
    """
    Kick a user from a guild.
    
    Args:
        guild_id: The ID of the guild
        user_id: The ID of the user to kick
        reason: Optional reason for the kick
        
    Returns:
        Success confirmation
    """
    flogger.info(f"kick_member endpoint called for guild_id: {guild_id}, user_id: {user_id}")
    flogger.debug(f"Starting member kick with reason: {reason}")
    
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
        
        member = guild.get_member(user_id)
        if not member:
            flogger.trace(f"Member {user_id} not in cache, fetching")
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                flogger.error(f"Member {user_id} not found in guild {guild_id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Member {user_id} not found in guild {guild_id}"
                )
        
        member_name = member.display_name
        flogger.debug(f"Member retrieved: {member_name}")
        
        # Kick the member
        flogger.debug(f"Kicking member {member_name} from guild {guild.name}")
        await member.kick(reason=reason)
        
        message = f"Member {member_name} kicked from {guild.name}"
        if reason:
            message += f" (Reason: {reason})"
        
        flogger.info(message)
        return SuccessResponse(
            status="kicked",
            message=message
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in kick_member for member {user_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in kick_member for member {user_id}: {exc}")
        await handle_discord_exception("kick member", exc)
