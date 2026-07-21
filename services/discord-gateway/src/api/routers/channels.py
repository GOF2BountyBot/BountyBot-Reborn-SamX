"""
Channel router for Discord Gateway API.

This module provides REST endpoints for managing Discord channels
with simplified URIs and consolidated operations.
"""

import io

import discord
from fastapi import APIRouter, File, Header, HTTPException, Query, Request, UploadFile, status
from shared import bblogger
from utils.discord_converters import ChannelConverter, MessageConverter, PermissionConverter
from utils.discord_helpers import (
    get_entity_or_404,
    handle_discord_exception,
    preserve_embed_image,
    resolve_bot,
    validate_channel_type,
)
from utils.embed_converter import EmbedConverter
from utils.permission_utils import create_permission_overwrite

from api.schemas.base_schemas import DeleteResponse, SuccessResponse
from api.schemas.channel_schemas import (
    ChannelResponse,
    ChannelUpdateRequest,
    ForumTagListResponse,
    ThreadCreateRequest,
    ThreadListResponse,
    ThreadResponse,
)
from api.schemas.message_schemas import (
    BatchFileUploadData,
    BatchFileUploadResponse,
    FileUploadResponse,
    MessageCreateRequest,
    MessageListResponse,
    MessageResponse,
    MessageUpdateRequest,
)
from api.schemas.permission_schemas import PermissionOverwriteListRequest, PermissionOverwriteListResponse

flogger = bblogger.get_logger("gateway-channel-router")

router = APIRouter(
    tags=["channels"],
    responses={
        400: {"description": "Invalid request"},
        404: {"description": "Channel or guild not found"},
        403: {"description": "Insufficient permissions"},
        422: {"description": "Bad request - unprocessable request content"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"},
    },
)


@router.get(
    "/channels/{channel_id}",
    response_model=ChannelResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Channel Details",
    description="Get detailed information about a specific channel",
)
async def get_channel(request: Request, channel_id: int) -> ChannelResponse:
    """Get detailed information about a specific channel."""
    flogger.info(f"get_channel endpoint called for channel_id: {channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if isinstance(channel, discord.CategoryChannel):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel_id} is a category. Use category endpoints instead.",
            )

        channel_data = ChannelConverter.channel_to_detail(channel)
        flogger.info(f"Successfully retrieved channel details for {channel.name}")

        return ChannelResponse(status="success", data=channel_data)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in get_channel for channel {channel_id}: {exc}")
        await handle_discord_exception("get channel details", exc)


@router.put(
    "/channels/{channel_id}",
    response_model=ChannelResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Channel",
    description="Update a channel's properties",
)
async def update_channel(request: Request, channel_id: int, channel_data: ChannelUpdateRequest) -> ChannelResponse:
    """Update a channel's properties."""
    flogger.info(f"update_channel called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if isinstance(channel, discord.CategoryChannel):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Use category endpoints to update categories"
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
                        status_code=status.HTTP_404_NOT_FOUND, detail=f"Category {channel_data.category_id} not found"
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
            channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        updated_channel_data = ChannelConverter.channel_to_detail(channel)
        flogger.info(f"Successfully updated channel {channel.name}")

        return ChannelResponse(status="updated", data=updated_channel_data)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in update_channel: {exc}")
        await handle_discord_exception("update channel", exc)


@router.delete(
    "/channels/{channel_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Channel",
    description="Delete a channel",
)
async def delete_channel(request: Request, channel_id: int) -> DeleteResponse:
    """Delete a channel."""
    flogger.info(f"delete_channel endpoint called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        name, ctype = channel.name, channel.type.name
        await channel.delete()

        message = f"{ctype.title()} channel {name} deleted"
        flogger.info(message)

        return DeleteResponse(status="deleted", deleted=True, message=message)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in delete_channel: {exc}")
        await handle_discord_exception("delete channel", exc)


@router.get(
    "/channels/{channel_id}/messages",
    response_model=MessageListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Channel Messages",
    description="Get the last `limit` messages from a channel",
)
async def list_channel_messages(
    request: Request, channel_id: int, limit: int = Query(50, le=100, description="Number of messages to retrieve")
) -> MessageListResponse:
    """Get the last `limit` messages from a channel."""
    flogger.info(f"list_channel_messages called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not hasattr(channel, "history"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Channel {channel_id} cannot contain messages"
            )

        msgs = [msg async for msg in channel.history(limit=limit)]
        message_data = [MessageConverter.message_to_payload(m) for m in msgs]

        flogger.info(f"Retrieved {len(message_data)} messages from channel {channel_id}")
        return MessageListResponse(status="success", data=message_data)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in list_channel_messages: {exc}")
        await handle_discord_exception("list channel messages", exc)


@router.post(
    "/channels/{channel_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Channel Message",
    description="Create a new message in a channel",
)
async def create_channel_message(request: Request, channel_id: int, payload: MessageCreateRequest) -> MessageResponse:
    """Create a new message in a channel."""
    flogger.info(f"create_channel_message called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not hasattr(channel, "send"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Channel {channel_id} cannot receive messages"
            )
        # Convert the incoming embed-payload to a discord.Embed (EmbedConverter raises on invalid payload)
        embed = EmbedConverter.payload_to_embed(payload.content)
        text_content = payload.text_content
        # Send the message with optional text content (e.g. role mentions)
        try:
            message = await channel.send(content=text_content, embed=embed)
        except TypeError:
            # fallback if discord.py variant doesn't accept embed arg on send
            message = await channel.send(content=text_content)
            if embed:
                await message.reply(embed=embed)
        # Build a canonical Message-shaped dict (fields must match api.schemas.message_schemas.Message)
        # Prefer the request payload's content as the authoritative embed payload we've converted from.
        try:
            content_obj = payload.content.model_dump()  # EmbedPayload -> dict
        except Exception:  # pylint: disable=broad-exception-caught
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
            "message_type": (
                getattr(getattr(message, "type", None), "name", "general")
                if getattr(message, "type", None) is not None
                else "general"
            ),
        }
        flogger.info(f"Created message {message.id} in channel {channel_id}")
        # Return a plain dict matching the Message schema so Pydantic can validate it for MessageResponse
        return MessageResponse(status="created", data=message_obj)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in create_channel_message: {exc}")
        await handle_discord_exception("create channel message", exc)


@router.put(
    "/channels/{channel_id}/messages/{message_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Edit Channel Message",
    description="Edit an existing message in a channel (must be sent by the bot)",
)
async def edit_channel_message(
    request: Request, channel_id: int, message_id: int, payload: MessageUpdateRequest
) -> MessageResponse:
    """Edit a message directly using the known channel ID (avoids global channel scan)."""
    flogger.info(f"edit_channel_message called for channel_id={channel_id} message_id={message_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not hasattr(channel, "fetch_message"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Channel {channel_id} cannot contain messages"
            )

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Message {message_id} not found in channel {channel_id}"
            ) from exc

        if not getattr(message, "author", None) or message.author.id != getattr(bot.user, "id", None):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Can only edit messages sent by the bot")

        embed = EmbedConverter.payload_to_embed(payload.content)
        # Preserve the existing embed image when the new payload omits one (B.13).
        # Discord's full-embed-replace semantics would silently erase the image otherwise.
        embed = preserve_embed_image(embed, message)
        await message.edit(embed=embed)

        updated_data = MessageConverter.message_to_payload(message)
        flogger.info(f"Successfully edited message {message_id} in channel {channel_id}")
        return MessageResponse(status="updated", data=updated_data)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in edit_channel_message: {exc}")
        await handle_discord_exception("edit channel message", exc)


@router.delete(
    "/channels/{channel_id}/messages/{message_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Channel Message",
    description="Delete an existing message in a channel using the known channel ID (avoids global channel scan)",
)
async def delete_channel_message(request: Request, channel_id: int, message_id: int) -> DeleteResponse:
    """Delete a message directly using the known channel ID (avoids global channel scan)."""
    flogger.info(f"delete_channel_message called for channel_id={channel_id} message_id={message_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not hasattr(channel, "fetch_message"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Channel {channel_id} cannot contain messages"
            )

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            # Message already deleted — treat as success
            msg = f"Message {message_id} not found in channel {channel_id} (already deleted)"
            flogger.info(msg)
            return DeleteResponse(status="deleted", deleted=True, message=msg)

        await message.delete()

        msg = f"Message {message_id} deleted from channel {channel_id}"
        flogger.info(msg)
        return DeleteResponse(status="deleted", deleted=True, message=msg)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in delete_channel_message: {exc}")
        await handle_discord_exception("delete channel message", exc)


@router.delete(
    "/channels/{channel_id}/orphaned-announcement",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Orphaned Bounty Announcement",
    description=(
        "Scan a channel's recent history and delete the bot's own bounty announcement whose embed "
        "image carries the given route-map marker. Used to reap an announcement that Discord created "
        "but whose spawn was rolled back after an ambiguous announce timeout (no message_id returned)."
    ),
)
async def delete_orphaned_announcement(
    request: Request,
    channel_id: int,
    route_map_marker: str = Query(
        ..., description="Route-map image filename identifying the orphan, e.g. route_map_11754.png"
    ),
    limit: int = Query(30, le=100, description="How many recent messages to scan"),
) -> DeleteResponse:
    """Find and delete the bot's own bounty announcement whose embed image matches *route_map_marker*."""
    flogger.info(f"delete_orphaned_announcement called for channel_id={channel_id} marker={route_map_marker!r}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not hasattr(channel, "history"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Channel {channel_id} cannot contain messages"
            )

        bot_user_id = getattr(bot.user, "id", None)
        deleted = 0
        async for message in channel.history(limit=limit):
            # Only ever delete the bot's own posts.
            if bot_user_id is not None and getattr(message.author, "id", None) != bot_user_id:
                continue
            matched = False
            for embed in getattr(message, "embeds", []) or []:
                image_url = getattr(getattr(embed, "image", None), "url", None) or ""
                if route_map_marker in image_url:
                    matched = True
                    break
            if matched:
                await message.delete()
                deleted += 1
                flogger.info(
                    f"delete_orphaned_announcement deleted message {message.id} in channel {channel_id} "
                    f"(marker={route_map_marker!r})"
                )

        msg = f"Deleted {deleted} orphaned announcement(s) in channel {channel_id} matching {route_map_marker!r}"
        flogger.info(msg)
        return DeleteResponse(status="deleted", deleted=deleted > 0, message=msg)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in delete_orphaned_announcement: {exc}")
        await handle_discord_exception("delete orphaned announcement", exc)


@router.get(
    "/channels/{channel_id}/permissions",
    response_model=PermissionOverwriteListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Channel Permissions",
    description="Get permission overwrites for a channel",
)
async def get_channel_permissions(request: Request, channel_id: int) -> PermissionOverwriteListResponse:
    """Get permission overwrites for a channel."""
    flogger.info(f"get_channel_permissions endpoint called for channel_id: {channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        overwrites = []
        for target, overwrite in channel.overwrites.items():
            overwrite_data = PermissionConverter.overwrite_to_payload(target, overwrite, channel.id)
            overwrites.append(overwrite_data)

        flogger.info(f"Successfully retrieved {len(overwrites)} permission overwrites for channel {channel.name}")
        return PermissionOverwriteListResponse(status="success", data=overwrites)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in get_channel_permissions for channel {channel_id}: {exc}")
        await handle_discord_exception("get channel permissions", exc)


@router.put(
    "/channels/{channel_id}/permissions",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Channel Permissions",
    description="Replace all permission overwrites for a channel",
)
async def update_channel_permissions(
    request: Request, channel_id: int, permissions_data: PermissionOverwriteListRequest
) -> SuccessResponse:
    """Replace all permission overwrites for a channel."""
    flogger.info(f"update_channel_permissions endpoint called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")
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
                    except Exception:  # pylint: disable=broad-exception-caught
                        flogger.warning(f"Member {od.target_id} not found—skipping")
                        continue

            overwrite = create_permission_overwrite(allow=allow, deny=deny)
            await channel.set_permissions(target, overwrite=overwrite)

        message = f"Permissions updated for channel {channel.name}"
        flogger.info(message)
        return SuccessResponse(status="updated", message=message)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in update_channel_permissions: {exc}")
        await handle_discord_exception("update channel permissions", exc)


@router.get(
    "/channels/{channel_id}/threads",
    response_model=ThreadListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Forum Threads",
    description="List all threads in a ForumChannel",
)
async def list_threads(request: Request, channel_id: int) -> ThreadListResponse:
    """List all threads in a ForumChannel."""
    flogger.info(f"list_threads called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not isinstance(channel, discord.ForumChannel):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a forum channel")

        threads = [ChannelConverter.thread_to_summary(t) for t in channel.threads]

        flogger.info(f"Retrieved {len(threads)} threads from forum {channel_id}")
        return ThreadListResponse(status="success", data=threads)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in list_threads: {exc}")
        await handle_discord_exception("list threads", exc)


@router.post(
    "/channels/{channel_id}/threads",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Forum Thread",
    description="Create a new thread in a ForumChannel",
)
async def create_thread(request: Request, channel_id: int, payload: ThreadCreateRequest) -> ThreadResponse:
    """Create a new thread in a ForumChannel."""
    flogger.info(f"create_thread called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not isinstance(channel, discord.ForumChannel):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a forum channel")

        embed = EmbedConverter.payload_to_embed(payload.initial_message) if payload.initial_message else None

        # Create the thread
        try:
            result = await channel.create_thread(
                name=payload.name,
                auto_archive_duration=payload.auto_archive_duration or channel.default_auto_archive_duration,
                embed=embed,
            )
        except TypeError:
            # Fallback for discord.py versions without embed argument
            result = await channel.create_thread(
                name=payload.name,
                auto_archive_duration=payload.auto_archive_duration or channel.default_auto_archive_duration,
            )
            if embed:
                await result.send(embed=embed)

        # Unpack ThreadWithMessage → actual Thread object
        thread_obj = getattr(result, "thread", result)
        if thread_obj is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Thread creation failed")

        thread_data = ChannelConverter.thread_to_detail(thread_obj)
        flogger.info(f"Successfully created thread {thread_obj.name}")

        return ThreadResponse(status="created", data=thread_data)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in create_thread: {exc}")
        await handle_discord_exception("create thread", exc)


@router.get(
    "/channels/{channel_id}/tags",
    response_model=ForumTagListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Forum Tags",
    description="List all tags in a forum channel",
)
async def list_forum_tags(request: Request, channel_id: int) -> ForumTagListResponse:
    """List all tags in a forum channel."""
    flogger.info(f"list_forum_tags called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not isinstance(channel, discord.ForumChannel):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Channel is not a forum")

        tags = [ChannelConverter.forum_tag_to_payload(t, channel_id=channel_id) for t in channel.available_tags]

        flogger.info(f"Retrieved {len(tags)} tags from forum {channel_id}")
        return ForumTagListResponse(status="success", data=tags)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in list_forum_tags: {exc}")
        await handle_discord_exception("list forum tags", exc)


@router.put(
    "/channels/{channel_id}/category/{category_id}",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Move Channel to Category",
    description="Move a channel into a specific category",
)
async def move_channel_to_category(request: Request, channel_id: int, category_id: int) -> SuccessResponse:
    """Move a channel into a category."""
    flogger.info(f"move_channel_to_category called for channel_id={channel_id}, category_id={category_id}")
    try:
        bot = await resolve_bot(request)

        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")
        category = await get_entity_or_404(bot.get_channel, bot.fetch_channel, category_id, "Channel")

        validate_channel_type(category, ["category"], category_id)

        if isinstance(channel, discord.CategoryChannel):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot move category {channel_id} into another category",
            )

        await channel.edit(category=category)

        message = f"Channel {channel.name} moved to category {category.name}"
        flogger.info(message)
        return SuccessResponse(status="moved", message=message)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in move_channel_to_category: {exc}")
        await handle_discord_exception("move channel to category", exc)


@router.post(
    "/channels/{channel_id}/upload",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload File to Channel",
    description="Upload a file attachment to a channel and return the CDN URL",
)
async def upload_file_to_channel(
    request: Request,
    channel_id: int,
    x_filename: str = Header("upload.png", alias="X-Filename"),
) -> FileUploadResponse:
    """Upload a file to a channel and return the attachment CDN URL."""
    flogger.info(f"upload_file_to_channel called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not hasattr(channel, "send"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel_id} cannot receive messages",
            )

        # Read the raw body bytes
        body = await request.body()
        if not body:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Request body is empty — expected file content",
            )

        # Send as Discord file attachment
        file = discord.File(io.BytesIO(body), filename=x_filename)
        message = await channel.send(file=file)

        # Extract the CDN URL from the attachment
        if not message.attachments:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Message sent but no attachments returned",
            )

        attachment = message.attachments[0]
        result = {
            "message_id": message.id,
            "attachment_url": attachment.url,
            "filename": attachment.filename,
            "size": attachment.size,
        }

        flogger.info(f"Uploaded file to channel {channel_id}: message_id={message.id}, url={attachment.url}")
        return FileUploadResponse(status="created", data=result)

    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in upload_file_to_channel for channel {channel_id}: {exc}")
        await handle_discord_exception("upload file to channel", exc)


# Discord allows up to 10 attachments per message.
_MAX_FILES_PER_BATCH = 10


@router.post(
    "/channels/{channel_id}/upload-batch",
    response_model=BatchFileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Multiple Files to Channel (single message)",
    description=(
        "Upload up to 10 files to a channel as attachments on a SINGLE Discord "
        "message. Returns per-file CDN URLs keyed by filename. Far more efficient "
        "than N sequential /upload calls because Discord rate-limits message "
        "creation per channel (~5/5s); 10 files in one message = 1 rate-limit slot."
    ),
)
async def upload_files_to_channel_batch(
    request: Request,
    channel_id: int,
    files: list[UploadFile] = File(..., description="Up to 10 files to attach to a single message"),
) -> BatchFileUploadResponse:
    """Upload multiple files to a channel as attachments on a single message."""
    flogger.info(f"upload_files_to_channel_batch called for channel_id={channel_id} files={len(files)}")
    try:
        if not files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one file is required",
            )
        if len(files) > _MAX_FILES_PER_BATCH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Maximum {_MAX_FILES_PER_BATCH} files per batch (Discord limit)",
            )

        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not hasattr(channel, "send"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel_id} cannot receive messages",
            )

        # Read all bodies and build discord.File list
        discord_files: list[discord.File] = []
        for upload in files:
            body = await upload.read()
            if not body:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File {upload.filename!r} has empty body",
                )
            discord_files.append(discord.File(io.BytesIO(body), filename=upload.filename or "upload.bin"))

        # Send all files as attachments on ONE message — one rate-limit slot
        message = await channel.send(files=discord_files)

        if not message.attachments or len(message.attachments) != len(discord_files):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"Message sent but attachment count mismatch: "
                    f"sent={len(discord_files)} returned={len(message.attachments)}"
                ),
            )

        data = [
            BatchFileUploadData(
                attachment_url=att.url,
                filename=att.filename,
                size=att.size,
            )
            for att in message.attachments
        ]

        flogger.info(f"Batch-uploaded {len(data)} files to channel {channel_id}: message_id={message.id}")
        return BatchFileUploadResponse(status="created", message_id=message.id, data=data)

    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in upload_files_to_channel_batch for channel {channel_id}: {exc}")
        await handle_discord_exception("batch upload files to channel", exc)
