"""
User router for Discord Gateway API.

This module provides REST endpoints for managing Discord users and members
with simplified URIs that don't require guild context where possible.
"""

import discord
from fastapi import APIRouter, HTTPException, Query, Request, status
from shared import bblogger
from utils.discord_converters import UserConverter
from utils.discord_helpers import handle_discord_exception, resolve_bot
from utils.permission_utils import PERMISSION_FLAGS, has_guild_permission

from api.schemas.permission_schemas import PermissionCheckResponse
from api.schemas.user_schemas import MemberResponse, MemberUpdateRequest, UserResponse

flogger = bblogger.get_logger("gateway-user-router")

router = APIRouter(
    tags=["users"],
    responses={
        404: {"description": "User, member, or guild not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"},
    },
)


@router.get(
    "/users/@me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Bot Identity",
    description="Get information about the bot user",
)
async def get_bot_identity(request: Request) -> UserResponse:
    """Get information about the bot user."""
    flogger.info("get_bot_identity endpoint called")
    try:
        bot = await resolve_bot(request)
        user_data = UserConverter.user_to_payload(bot.user)

        flogger.info(f"Successfully retrieved bot identity: {bot.user.name}")
        return UserResponse(status="success", data=user_data)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in get_bot_identity: {exc}")
        await handle_discord_exception("get bot identity", exc)


@router.get(
    "/users/{user_id}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get User Details",
    description="Get information about a specific user",
)
async def get_user(request: Request, user_id: int) -> UserResponse:
    """Get information about a specific user."""
    flogger.info(f"get_user endpoint called for user_id: {user_id}")
    try:
        bot = await resolve_bot(request)

        # Try to get user from cache first
        user = bot.get_user(user_id)
        if not user:
            try:
                user = await bot.fetch_user(user_id)
            except discord.NotFound as exc:
                flogger.error(f"User {user_id} not found")
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User {user_id} not found") from exc

        user_data = UserConverter.user_to_payload(user)
        flogger.info(f"Successfully retrieved user details for {user.name}")

        return UserResponse(status="success", data=user_data)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in get_user for user {user_id}: {exc}")
        await handle_discord_exception("get user details", exc)


@router.get(
    "/members/{member_id}",
    response_model=MemberResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Member Details",
    description="Get information about a guild member",
)
async def get_member(request: Request, member_id: int) -> MemberResponse:
    """Get information about a guild member."""
    flogger.info(f"get_member endpoint called for member_id: {member_id}")
    try:
        bot = await resolve_bot(request)

        # Search for the member across all guilds
        # Note: member_id in this context is actually user_id, but we search for them as a member
        member = None
        for guild in bot.guilds:
            member = guild.get_member(member_id)
            if member:
                break

            # Try fetching if not in cache
            try:
                member = await guild.fetch_member(member_id)
                if member:
                    break
            except discord.NotFound:
                continue

        if not member:
            flogger.error(f"Member {member_id} not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member {member_id} not found")

        member_data = UserConverter.member_to_payload(member)
        flogger.info(f"Successfully retrieved member details for {member.display_name}")

        return MemberResponse(status="success", data=member_data)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in get_member for member {member_id}: {exc}")
        await handle_discord_exception("get member details", exc)


@router.put(
    "/members/{member_id}",
    response_model=MemberResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Member",
    description="Update a guild member's properties",
)
async def update_member(request: Request, member_id: int, member_data: MemberUpdateRequest) -> MemberResponse:
    """Update a guild member's properties."""
    flogger.info(f"update_member endpoint called for member_id: {member_id}")
    try:
        bot = await resolve_bot(request)

        # Search for the member across all guilds
        member = None
        for guild in bot.guilds:
            member = guild.get_member(member_id)
            if member:
                break

            # Try fetching if not in cache
            try:
                member = await guild.fetch_member(member_id)
                if member:
                    break
            except discord.NotFound:
                continue

        if not member:
            flogger.error(f"Member {member_id} not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member {member_id} not found")

        # Build update kwargs
        update_kwargs = {}
        if member_data.nick is not None:
            update_kwargs["nick"] = member_data.nick
        if member_data.mute is not None:
            update_kwargs["mute"] = member_data.mute
        if member_data.deaf is not None:
            update_kwargs["deafen"] = member_data.deaf

        if member_data.roles is not None:
            roles = []
            for role_id in member_data.roles:
                role = member.guild.get_role(role_id)
                if not role:
                    flogger.error(f"Role {role_id} not found in guild {member.guild.id}")
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Role {role_id} not found")
                roles.append(role)
            update_kwargs["roles"] = roles

        if member_data.channel_id is not None:
            if member_data.channel_id == 0:
                update_kwargs["voice_channel"] = None
            else:
                voice_channel = member.guild.get_channel(member_data.channel_id)
                if not voice_channel:
                    flogger.error(f"Voice channel {member_data.channel_id} not found")
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Voice channel {member_data.channel_id} not found",
                    )
                update_kwargs["voice_channel"] = voice_channel

        # Apply updates
        if update_kwargs:
            try:
                await member.edit(**update_kwargs)
            except discord.HTTPException as exc:
                if getattr(exc, "code", None) == 40032:
                    flogger.error("Target user not connected to voice")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST, detail="user not in a voice channel"
                    ) from exc
                raise

        updated_member_data = UserConverter.member_to_payload(member)
        flogger.info(f"Successfully updated member {member.display_name}")

        return MemberResponse(status="updated", data=updated_member_data)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in update_member for member {member_id}: {exc}")
        await handle_discord_exception("update member", exc)


@router.get(
    "/members/{member_id}/permissions/check",
    response_model=PermissionCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Check Member Permission",
    description="Check if a member has a specific guild-level permission.  Superceded by /permissions/check.",
    deprecated=True,
)
async def check_member_permission(
    request: Request,
    member_id: int,
    permission: str = Query(..., description="Permission name (uppercase, e.g. BAN_MEMBERS)"),
) -> PermissionCheckResponse:
    """Check whether a member has the named guild-level permission."""
    flogger.info(f"check_member_permission called for member_id={member_id}, permission={permission}")

    # Validate permission name
    if permission not in PERMISSION_FLAGS:
        flogger.error(f"check_member_permission: unknown permission '{permission}'")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Unknown permission: {permission}"
        )

    try:
        bot = await resolve_bot(request)

        # Search for the member across all guilds
        member = None
        for guild in bot.guilds:
            member = guild.get_member(member_id)
            if member:
                break

            # Try fetching if not in cache
            try:
                member = await guild.fetch_member(member_id)
                if member:
                    break
            except discord.NotFound:
                continue

        if not member:
            flogger.error(f"Member {member_id} not found")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Member {member_id} not found")

        allowed = has_guild_permission(member, permission)
        flogger.info(f"Guild permission '{permission}' for member '{member.display_name}': {allowed}")

        return PermissionCheckResponse(status="success", data={"allowed": allowed})
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in check_member_permission for member {member_id}: {exc}")
        await handle_discord_exception("check member permission", exc)
