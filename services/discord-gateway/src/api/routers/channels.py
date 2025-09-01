"""
Channel router for Discord Gateway API.

This module provides REST endpoints for managing Discord channels
with simplified URIs and consolidated operations.
"""

from fastapi import APIRouter, HTTPException, Request, status, Query
import discord
import shared.bblogger as bblogger
from api.schemas.channel_schemas import (
    ChannelResponse, ChannelUpdateRequest,
    ForumTagListResponse, ForumTagCreateRequest, ForumTagResponse,
    ThreadListResponse, ThreadCreateRequest, ThreadResponse
)
from api.schemas.message_schemas import MessageCreateRequest, MessageResponse, MessageListResponse
from api.schemas.permission_schemas import (
    PermissionOverwriteListResponse, PermissionOverwriteListRequest
)
from api.schemas.base_schemas import SuccessResponse, DeleteResponse
from utils.discord_converters import ChannelConverter, MessageConverter, PermissionConverter
from utils.discord_helpers import (
    resolve_bot, get_entity_or_404, validate_channel_type, handle_discord_exception, normalize_emoji
)
from utils.permission_utils import create_permission_overwrite
from utils.embed_converter import EmbedConverter

flogger = bblogger.get_logger("gateway-channel-router")

router = APIRouter(
    tags=["channels"],
    responses={
        400: {"description": "Invalid request"},
        404: {"description": "Channel or guild not found"},
        403: {"description": "Insufficient permissions"},
        422: {"description": "Bad request - unprocessable request content"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

@router.get(
    "/channels/{channel_id}",
    response_model=ChannelResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Channel Details",
    description="Get detailed information about a specific channel"
)
async def get_channel(request: Request, channel_id: int) -> ChannelResponse:
    """Get detailed information about a specific channel."""
    flogger.info(f"get_channel endpoint called for channel_id: {channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, channel_id, "Channel"
        )
        
        if isinstance(channel, discord.CategoryChannel):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel_id} is a category. Use category endpoints instead."
            )
        
        channel_data = ChannelConverter.channel_to_detail(channel)
        flogger.info(f"Successfully retrieved channel details for {channel.name}")
        
        return ChannelResponse(
            status="success",
            data=channel_data
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_channel for channel {channel_id}: {exc}")
        await handle_discord_exception("get channel details", exc)

@router.put(
    "/channels/{channel_id}",
    response_model=ChannelResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Channel",
    description="Update a channel's properties"
)
async def update_channel(
    request: Request, channel_id: int, channel_data: ChannelUpdateRequest
) -> ChannelResponse:
    """Update a channel's properties."""
    flogger.info(f"update_channel called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, channel_id, "Channel"
        )
        
        if isinstance(channel, discord.CategoryChannel):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Use category endpoints to update categories"
            )
        
        # Resolve optional category change
        category = None
        if channel_data.category_id is not None:
            if channel_data.category_id == 0:
                category = None
            else:
                category = channel.guild.get_channel(channel_data.category_id)
                if not isinstance(category, discord.CategoryChannel):
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Category {channel_data.category_id} not found"
                    )
        
        # Collect updates
        kwargs = {}
        if channel_data.name is not None:
            kwargs["name"] = channel_data.name
        if channel_data.position is not None:
            kwargs["position"] = channel_data.position
        if category is not None or channel_data.category_id == 0:
            kwargs["category"] = category
        
        # Type-specific fields
        if isinstance(channel, discord.TextChannel):
            if channel_data.topic is not None:
                kwargs["topic"] = channel_data.topic
            if channel_data.nsfw is not None:
                kwargs["nsfw"] = channel_data.nsfw
            if channel_data.slowmode_delay is not None:
                kwargs["slowmode_delay"] = channel_data.slowmode_delay
        elif isinstance(channel, discord.VoiceChannel):
            if channel_data.bitrate is not None:
                kwargs["bitrate"] = channel_data.bitrate
            if channel_data.user_limit is not None:
                kwargs["user_limit"] = channel_data.user_limit
        elif isinstance(channel, discord.ForumChannel):
            if channel_data.topic is not None:
                kwargs["topic"] = channel_data.topic
            if channel_data.default_auto_archive_duration is not None:
                kwargs["default_auto_archive_duration"] = channel_data.default_auto_archive_duration
        
        # Apply updates
        if kwargs:
            await channel.edit(**kwargs)
            # Re-fetch to get updated state
            channel = await get_entity_or_404(
                bot.get_channel, bot.fetch_channel, channel_id, "Channel"
            )
        
        updated_channel_data = ChannelConverter.channel_to_detail(channel)
        flogger.info(f"Successfully updated channel {channel.name}")
        
        return ChannelResponse(
            status="updated",
            data=updated_channel_data
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in update_channel: {exc}")
        await handle_discord_exception("update channel", exc)

@router.delete(
    "/channels/{channel_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Channel",
    description="Delete a channel"
)
async def delete_channel(request: Request, channel_id: int) -> DeleteResponse:
    """Delete a channel."""
    flogger.info(f"delete_channel endpoint called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, channel_id, "Channel"
        )
        
        name, ctype = channel.name, channel.type.name
        await channel.delete()
        
        message = f"{ctype.title()} channel {name} deleted"
        flogger.info(message)
        
        return DeleteResponse(
            status="deleted",
            deleted=True,
            message=message
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in delete_channel: {exc}")
        await handle_discord_exception("delete channel", exc)

@router.get(
    "/channels/{channel_id}/messages",
    response_model=MessageListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Channel Messages",
    description="Get the last `limit` messages from a channel"
)
async def list_channel_messages(
    request: Request,
    channel_id: int,
    limit: int = Query(50, le=100, description="Number of messages to retrieve")
) -> MessageListResponse:
    """Get the last `limit` messages from a channel."""
    flogger.info(f"list_channel_messages called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")
        
        if not hasattr(channel, "history"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel_id} cannot contain messages"
            )
        
        msgs = [msg async for msg in channel.history(limit=limit)]
        message_data = [MessageConverter.message_to_payload(m) for m in msgs]
        
        flogger.info(f"Retrieved {len(message_data)} messages from channel {channel_id}")
        return MessageListResponse(
            status="success",
            data=message_data
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in list_channel_messages: {exc}")
        await handle_discord_exception("list channel messages", exc)

@router.post(
    "/channels/{channel_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Channel Message",
    description="Create a new message in a channel"
)
async def create_channel_message(
    request: Request, channel_id: int, payload: MessageCreateRequest
) -> MessageResponse:
    """Create a new message in a channel."""
    flogger.info(f"create_channel_message called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not hasattr(channel, "send"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel_id} cannot receive messages"
            )
        # Convert the incoming embed-payload to a discord.Embed (EmbedConverter raises on invalid payload)
        embed = EmbedConverter.payload_to_embed(payload.content)
        # Send the message (discord.py may raise; also handle older/newer signature differences if needed)
        try:
            message = await channel.send(embed=embed)
        except TypeError:
            # fallback if discord.py variant doesn't accept embed arg on send
            message = await channel.send()
            if embed:
                await message.reply(embed=embed)  # or send as follow-up; adjust per your bot library version
        # Build a canonical Message-shaped dict (fields must match api.schemas.message_schemas.Message)
        # Prefer the request payload's content as the authoritative embed payload we've converted from.
        try:
            content_obj = payload.content.model_dump()  # EmbedPayload -> dict
        except Exception:
            # if payload.content is already a plain dict or not a pydantic model
            content_obj = getattr(payload, "content", None)
        message_obj = {
            "id": int(message.id),
            "channel_id": int(channel.id),
            "guild_id": int(channel.guild.id) if getattr(channel, "guild", None) else None,
            "author_id": int(getattr(message.author, "id", None)),
            "content": content_obj,
            "timestamp": message.created_at,
            "edited_timestamp": getattr(message, "edited_at", None) or None,
            "message_type": getattr(getattr(message, "type", None), "name", "general") if getattr(message, "type", None) is not None else "general",
        }
        flogger.info(f"Created message {message.id} in channel {channel_id}")
        # Return a plain dict matching the Message schema so Pydantic can validate it for MessageResponse
        return MessageResponse(
            status="created",
            data=message_obj
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in create_channel_message: {exc}")
        await handle_discord_exception("create channel message", exc)

@router.get(
    "/channels/{channel_id}/permissions",
    response_model=PermissionOverwriteListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Channel Permissions",
    description="Get permission overwrites for a channel"
)
async def get_channel_permissions(request: Request, channel_id: int) -> PermissionOverwriteListResponse:
    """Get permission overwrites for a channel."""
    flogger.info(f"get_channel_permissions endpoint called for channel_id: {channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, channel_id, "Channel"
        )
        
        overwrites = []
        for target, overwrite in channel.overwrites.items():
            overwrite_data = PermissionConverter.overwrite_to_payload(target, overwrite, channel.id)
            overwrites.append(overwrite_data)
        
        flogger.info(f"Successfully retrieved {len(overwrites)} permission overwrites for channel {channel.name}")
        return PermissionOverwriteListResponse(
            status="success",
            data=overwrites
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_channel_permissions for channel {channel_id}: {exc}")
        await handle_discord_exception("get channel permissions", exc)

@router.put(
    "/channels/{channel_id}/permissions",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Channel Permissions",
    description="Replace all permission overwrites for a channel"
)
async def update_channel_permissions(
    request: Request,
    channel_id: int,
    permissions_data: PermissionOverwriteListRequest
) -> SuccessResponse:
    """Replace all permission overwrites for a channel."""
    flogger.info(f"update_channel_permissions endpoint called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, channel_id, "Channel"
        )
        guild = channel.guild
        
        # Clear existing
        for target in list(channel.overwrites.keys()):
            await channel.set_permissions(target, overwrite=None)
        
        # Apply new
        for od in permissions_data.overwrites:
            allow, deny = od.allow or 0, od.deny or 0
            
            if od.type == "role":
                target = guild.get_role(od.target_id)
                if not target:
                    flogger.warning(f"Role {od.target_id} not found—skipping")
                    continue
            else:
                target = guild.get_member(od.target_id)
                if not target:
                    try:
                        target = await guild.fetch_member(od.target_id)
                    except Exception:
                        flogger.warning(f"Member {od.target_id} not found—skipping")
                        continue
            
            overwrite = create_permission_overwrite(allow=allow, deny=deny)
            await channel.set_permissions(target, overwrite=overwrite)
        
        message = f"Permissions updated for channel {channel.name}"
        flogger.info(message)
        return SuccessResponse(status="updated", message=message)
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in update_channel_permissions: {exc}")
        await handle_discord_exception("update channel permissions", exc)

@router.get(
    "/channels/{channel_id}/threads",
    response_model=ThreadListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Forum Threads",
    description="List all threads in a ForumChannel"
)
async def list_threads(request: Request, channel_id: int) -> ThreadListResponse:
    """List all threads in a ForumChannel."""
    flogger.info(f"list_threads called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")
        
        if not isinstance(channel, discord.ForumChannel):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Not a forum channel"
            )
        
        threads = [ChannelConverter.thread_to_summary(t) for t in channel.threads]
        
        flogger.info(f"Retrieved {len(threads)} threads from forum {channel_id}")
        return ThreadListResponse(
            status="success",
            data=threads
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in list_threads: {exc}")
        await handle_discord_exception("list threads", exc)

@router.post(
    "/channels/{channel_id}/threads",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Forum Thread",
    description="Create a new thread in a ForumChannel"
)
async def create_thread(
    request: Request, channel_id: int, payload: ThreadCreateRequest
) -> ThreadResponse:
    """Create a new thread in a ForumChannel."""
    flogger.info(f"create_thread called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")
        
        if not isinstance(channel, discord.ForumChannel):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Not a forum channel"
            )
        
        embed = EmbedConverter.payload_to_embed(payload.initial_message) if payload.initial_message else None
        
        # Create the thread
        try:
            result = await channel.create_thread(
                name=payload.name,
                auto_archive_duration=payload.auto_archive_duration or channel.default_auto_archive_duration,
                embed=embed
            )
        except TypeError:
            # Fallback for discord.py versions without embed argument
            result = await channel.create_thread(
                name=payload.name,
                auto_archive_duration=payload.auto_archive_duration or channel.default_auto_archive_duration
            )
            if embed:
                await result.send(embed=embed)
        
        # Unpack ThreadWithMessage → actual Thread object
        thread_obj = getattr(result, "thread", result)
        if thread_obj is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Thread creation failed"
            )
        
        thread_data = ChannelConverter.thread_to_detail(thread_obj)
        flogger.info(f"Successfully created thread {thread_obj.name}")
        
        return ThreadResponse(
            status="created",
            data=thread_data
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in create_thread: {exc}")
        await handle_discord_exception("create thread", exc)

@router.get(
    "/channels/{channel_id}/tags",
    response_model=ForumTagListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Forum Tags",
    description="List all tags in a forum channel"
)
async def list_forum_tags(request: Request, channel_id: int) -> ForumTagListResponse:
    """List all tags in a forum channel."""
    flogger.info(f"list_forum_tags called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")
        
        if not isinstance(channel, discord.ForumChannel):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Channel is not a forum"
            )
        
        tags = [ChannelConverter.forum_tag_to_payload(t, channel_id=channel_id) for t in channel.available_tags]
        
        flogger.info(f"Retrieved {len(tags)} tags from forum {channel_id}")
        return ForumTagListResponse(
            status="success",
            data=tags
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in list_forum_tags: {exc}")
        await handle_discord_exception("list forum tags", exc)

@router.post(
    "/channels/{channel_id}/tags",
    response_model=ForumTagResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Forum Tag",
    description="Create a new tag in a forum channel"
)
async def create_forum_tag(
    request: Request, channel_id: int, tag: ForumTagCreateRequest
) -> ForumTagResponse:
    """Create a tag in a ForumChannel."""
    flogger.info(f"create_forum_tag called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")
        
        if not isinstance(channel, discord.ForumChannel):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Not a forum channel"
            )
        
        emoji_value = None
        if tag.emoji:
            try:
                emoji_value = normalize_emoji(tag.emoji)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid emoji: {tag.emoji}"
                )
        
        new_tag = await channel.create_tag(name=tag.name, emoji=emoji_value)
        tag_data = ChannelConverter.forum_tag_to_payload(new_tag, channel_id=channel_id)
        
        flogger.info(f"Successfully created tag {new_tag.name}")
        return ForumTagResponse(
            status="created",
            data=tag_data
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in create_forum_tag: {exc}")
        await handle_discord_exception("create forum tag", exc)

@router.put(
    "/channels/{channel_id}/category/{category_id}",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Move Channel to Category",
    description="Move a channel into a specific category"
)
async def move_channel_to_category(
    request: Request, channel_id: int, category_id: int
) -> SuccessResponse:
    """Move a channel into a category."""
    flogger.info(f"move_channel_to_category called for channel_id={channel_id}, category_id={category_id}")
    try:
        bot = await resolve_bot(request)
        
        channel = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, channel_id, "Channel"
        )
        category = await get_entity_or_404(
            bot.get_channel, bot.fetch_channel, category_id, "Channel"
        )
        
        validate_channel_type(category, ["category"], category_id)
        
        if isinstance(channel, discord.CategoryChannel):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot move category {channel_id} into another category"
            )
        
        await channel.edit(category=category)
        
        message = f"Channel {channel.name} moved to category {category.name}"
        flogger.info(message)
        return SuccessResponse(
            status="moved",
            message=message
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in move_channel_to_category: {exc}")
        await handle_discord_exception("move channel to category", exc)