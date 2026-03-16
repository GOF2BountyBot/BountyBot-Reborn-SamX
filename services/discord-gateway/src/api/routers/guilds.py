"""
Guild router for Discord Gateway API.

This module provides REST endpoints for managing Discord guilds
including listing guilds, getting guild details, managing members,
and creating resources within guilds.
"""

import asyncio
import random
import time

import discord
from fastapi import APIRouter, HTTPException, Query, Request, status
from shared import bblogger

from api.schemas.channel_schemas import (
    CategoryCreateRequest,
    CategoryListResponse,
    CategoryResponse,
    ChannelCreateRequest,
    ChannelListResponse,
    ChannelResponse,
)
from api.schemas.guild_schemas import GuildListResponse, GuildResponse
from api.schemas.role_schemas import RoleCreateRequest, RoleListResponse, RoleResponse
from api.schemas.user_schemas import MemberListResponse
from utils.discord_converters import ChannelConverter, GuildConverter, RoleConverter, UserConverter
from utils.discord_helpers import get_entity_or_404, handle_discord_exception, resolve_bot

flogger = bblogger.get_logger("gateway-guild-router")

router = APIRouter(
    prefix="/guilds",
    tags=["guilds"],
    responses={
        404: {"description": "Guild not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"},
    },
)


@router.get(
    "",
    response_model=GuildListResponse,
    status_code=status.HTTP_200_OK,
    summary="List All Guilds",
    description="Get a list of all guilds the bot is a member of",
)
async def list_guilds(request: Request) -> GuildListResponse:
    """List all guilds the bot is a member of."""
    flogger.info("list_guilds endpoint called")
    try:
        bot = await resolve_bot(request)

        guilds = []
        for guild in bot.guilds:
            guild_data = GuildConverter.guild_to_summary(guild)
            guilds.append(guild_data)

        flogger.info(f"Successfully retrieved {len(guilds)} guilds")
        return GuildListResponse(status="success", data=guilds)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in list_guilds: {exc}")
        await handle_discord_exception("list guilds", exc)


@router.get(
    "/{guild_id}",
    response_model=GuildResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Guild Details",
    description="Get detailed information about a specific guild",
)
async def get_guild(request: Request, guild_id: int) -> GuildResponse:
    """Get detailed information about a specific guild."""
    flogger.info(f"get_guild endpoint called for guild_id: {guild_id}")
    try:
        bot = await resolve_bot(request)
        guild = await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")

        guild_data = GuildConverter.guild_to_detail(guild)
        flogger.info(f"Successfully retrieved guild details for {guild.name}")

        return GuildResponse(status="success", data=guild_data)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in get_guild for guild {guild_id}: {exc}")
        await handle_discord_exception("get guild details", exc)


@router.get(
    "/{guild_id}/members",
    response_model=MemberListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Guild Members",
    description="Get a list of all members in a guild",
)
async def list_guild_members(
    request: Request, guild_id: int, limit: int = Query(1000, description="Maximum number of members to return")
) -> MemberListResponse:
    """Get a list of all members in a guild."""
    flogger.info(f"list_guild_members endpoint called for guild_id: {guild_id}")
    try:
        bot = await resolve_bot(request)
        guild = await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")

        # If guild members are not cached, fetch them
        if not guild.chunked:
            await guild.chunk(cache=True)

        members = []
        for member_count, member in enumerate(guild.members):
            if member_count >= limit:
                break
            member_data = UserConverter.member_to_payload(member)
            members.append(member_data)

        flogger.info(f"Successfully retrieved {len(members)} members from guild {guild.name}")
        return MemberListResponse(status="success", data=members)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in list_guild_members for guild {guild_id}: {exc}")
        await handle_discord_exception("list guild members", exc)


@router.get(
    "/{guild_id}/channels",
    response_model=ChannelListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Guild Channels",
    description="Get a list of all channels in a guild",
)
async def list_guild_channels(request: Request, guild_id: int) -> ChannelListResponse:
    """List all channels in a guild."""
    flogger.info(f"list_guild_channels endpoint called for guild_id: {guild_id}")
    try:
        bot = await resolve_bot(request)
        guild = await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")

        # Exclude categories and sort the remaining channels by their position
        channels = []
        non_cat = [ch for ch in guild.channels if not isinstance(ch, discord.CategoryChannel)]
        for channel in sorted(non_cat, key=lambda c: c.position):
            channel_data = ChannelConverter.channel_to_summary(channel)
            channels.append(channel_data)

        flogger.info(f"Successfully retrieved {len(channels)} channels from guild {guild.name}")
        return ChannelListResponse(status="success", data=channels)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in list_guild_channels for guild {guild_id}: {exc}")
        await handle_discord_exception("list guild channels", exc)


@router.post(
    "/{guild_id}/channels",
    response_model=ChannelResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Channel",
    description="Create a new channel in a guild",
)
async def create_channel(request: Request, guild_id: int, channel_data: ChannelCreateRequest) -> ChannelResponse:
    """Create a new channel in a guild."""
    flogger.info(f"create_channel called for guild_id={guild_id}, name={channel_data.name}")
    try:
        bot = await resolve_bot(request)
        guild = await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")

        # Resolve optional category
        category = None
        if channel_data.category_id:
            category = guild.get_channel(channel_data.category_id)
            if not isinstance(category, discord.CategoryChannel):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Category {channel_data.category_id} not found"
                )

        channel_type = channel_data.type.lower()

        if channel_type == "voice":
            channel = await guild.create_voice_channel(
                name=channel_data.name,
                category=category,
                position=channel_data.position,
                bitrate=channel_data.bitrate,
                user_limit=channel_data.user_limit or 0,
            )
        elif channel_type == "forum":
            channel = await guild.create_forum(
                name=channel_data.name,
                category=category,
                position=channel_data.position,
                topic=channel_data.topic,
                default_auto_archive_duration=channel_data.default_auto_archive_duration or 60,
            )
        else:  # text channel
            channel = await guild.create_text_channel(
                name=channel_data.name,
                category=category,
                position=channel_data.position,
                topic=channel_data.topic,
                nsfw=channel_data.nsfw,
                slowmode_delay=channel_data.slowmode_delay or 0,
            )

        channel_detail = ChannelConverter.channel_to_detail(channel)
        flogger.info(f"Successfully created channel {channel.name}")

        return ChannelResponse(status="created", data=channel_detail)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in create_channel: {exc}")
        await handle_discord_exception("create channel", exc)


@router.get(
    "/{guild_id}/categories",
    response_model=CategoryListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Categories",
    description="Get a list of all categories in a guild",
)
async def list_categories(request: Request, guild_id: int) -> CategoryListResponse:
    """List all categories in a guild."""
    flogger.info(f"list_categories endpoint called for guild_id: {guild_id}")
    try:
        bot = await resolve_bot(request)
        guild = await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")

        # Sort categories by their position
        categories = []
        sorted_cats = sorted(guild.categories, key=lambda c: c.position)
        for category in sorted_cats:
            category_data = ChannelConverter.category_to_detail(category)
            categories.append(category_data)

        flogger.info(f"Successfully retrieved {len(categories)} categories from guild {guild.name}")
        return CategoryListResponse(status="success", data=categories)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in list_categories for guild {guild_id}: {exc}")
        await handle_discord_exception("list categories", exc)


@router.post(
    "/{guild_id}/categories",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Category",
    description="Create a new category in a guild",
)
async def create_category(request: Request, guild_id: int, category_data: CategoryCreateRequest) -> CategoryResponse:
    """Create a new category in a guild."""
    flogger.info(f"create_category endpoint called for guild_id: {guild_id}")
    try:
        bot = await resolve_bot(request)
        guild = await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")

        position_arg = category_data.position if category_data.position is not None else 1
        category = await guild.create_category_channel(name=category_data.name, position=position_arg)

        category_detail = ChannelConverter.category_to_detail(category)
        flogger.info(f"Successfully created category {category.name}")

        return CategoryResponse(status="created", data=category_detail)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in create_category for guild {guild_id}: {exc}")
        await handle_discord_exception("create category", exc)


@router.get(
    "/{guild_id}/roles",
    response_model=RoleListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Guild Roles",
    description="Get a list of all roles in a guild",
)
async def list_guild_roles(request: Request, guild_id: int) -> RoleListResponse:
    """List all roles in a guild."""
    flogger.info(f"list_guild_roles endpoint called for guild_id: {guild_id}")
    try:
        bot = await resolve_bot(request)
        guild = await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")

        roles = []
        for role in guild.roles:
            role_data = RoleConverter.role_to_payload(role)
            roles.append(role_data)

        # Ensure roles are returned in ascending position order
        roles.sort(key=lambda payload: payload.position)

        flogger.info(f"Successfully retrieved {len(roles)} roles from guild {guild.name}")
        return RoleListResponse(status="success", data=roles)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in list_guild_roles for guild {guild_id}: {exc}")
        await handle_discord_exception("list guild roles", exc)


@router.post(
    "/{guild_id}/roles",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Role",
    description="Create a new role in a guild",
)
async def create_role(request: Request, guild_id: int, role_data: RoleCreateRequest) -> RoleResponse:
    """Create a new role in a guild."""
    flogger.info(f"create_role endpoint called for guild_id: {guild_id}")
    try:
        bot = await resolve_bot(request)
        guild = await get_entity_or_404(bot.get_guild, bot.fetch_guild, guild_id, "Guild")
        # Create role with provided parameters
        create_kwargs = {
            "name": role_data.name or "new role",
            "hoist": role_data.hoist or False,
            "mentionable": role_data.mentionable or False,
        }
        if role_data.permissions is not None:
            if role_data.permissions < 0:
                raise HTTPException(status_code=status.HTTP_422, detail="Invalid permissions bitmask")
            perms = discord.Permissions(role_data.permissions)
            if perms.value != role_data.permissions:
                raise HTTPException(status_code=status.HTTP_422, detail="Invalid permissions bitmask")
            create_kwargs["permissions"] = perms
        if role_data.color is not None:
            create_kwargs["color"] = discord.Color(role_data.color)
        # Instrumentation + retry logic to handle transient timeouts/rate-limits
        max_attempts = 3
        attempt = 0
        last_exc = None
        timeout_seconds = 30  # tune this to your environment
        while attempt < max_attempts:
            attempt += 1
            start_ts = time.time()
            try:
                flogger.debug(f"Attempt {attempt} to create role in guild {guild_id} (timeout={timeout_seconds}s)")
                # Bound the single HTTP call to avoid hanging indefinitely
                role = await asyncio.wait_for(guild.create_role(**create_kwargs), timeout=timeout_seconds)
                elapsed = time.time() - start_ts
                flogger.info(f"Successfully created role {role.name} in {elapsed:.2f}s (attempt {attempt})")
                role_payload = RoleConverter.role_to_payload(role)
                return RoleResponse(status="created", data=role_payload)
            except TimeoutError as te:
                last_exc = te
                flogger.warning(f"Timeout creating role on attempt {attempt}: {te}")
                if attempt >= max_attempts:
                    flogger.error("Exceeded retries for create_role due to repeated timeouts")
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Timeout creating role (transient error). Please retry.",
                    ) from te
                backoff = (2**attempt) + random.uniform(1, 3)
                flogger.debug(f"Sleeping {backoff:.2f}s before retrying create_role")
                await asyncio.sleep(backoff)
            except discord.HTTPException as http_exc:
                last_exc = http_exc
                flogger.warning(f"discord.HTTPException when creating role: {http_exc}")
                # retry on 5xx server errors; otherwise re-raise (403/400 etc. are client errors)
                status_code = getattr(http_exc, "status", None) or getattr(http_exc, "code", None)
                if status_code and 500 <= int(status_code) < 600 and attempt < max_attempts:
                    backoff = (2**attempt) + random.uniform(1, 3)
                    flogger.debug(f"Server error; sleeping {backoff:.2f}s and retrying (attempt {attempt})")
                    await asyncio.sleep(backoff)
                    continue
                raise
        # Shouldn't reach here, but rethrow last exception if it does
        raise last_exc
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in create_role for guild {guild_id}: {exc}")
        await handle_discord_exception("create role", exc)
