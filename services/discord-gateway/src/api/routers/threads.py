"""
Threads router for Discord Gateway API.

This module provides REST endpoints for managing Discord threads
with simplified URIs that don't require channel context.
"""

import discord
from fastapi import APIRouter, HTTPException, Request, status
from shared import bblogger
from utils.discord_converters import ChannelConverter, MessageConverter
from utils.discord_helpers import handle_discord_exception, normalize_emoji, resolve_bot
from utils.embed_converter import EmbedConverter

from api.schemas.base_schemas import DeleteResponse, SuccessResponse
from api.schemas.channel_schemas import ForumTagListRequest, ThreadResponse, ThreadUpdateRequest
from api.schemas.message_schemas import MessageCreateRequest, MessageListResponse, MessageResponse, MessageUpdateRequest

flogger = bblogger.get_logger("gateway-thread-router")

router = APIRouter(
    tags=["threads"],
    responses={
        400: {"description": "Bad request - invalid parameters"},
        404: {"description": "Thread not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

def find_thread_by_id(bot, thread_id: int):
    """Find a thread by ID across the bot's cache.

    Strategy:
      1) Try bot.get_channel(thread_id) — threads are channels, so this
         returns a Thread object when cached.
      2) Otherwise scan guilds -> forum channels:
         - call get_thread if it exists (some versions provide it)
         - iterate channel.threads if present
    Returns the thread/channel object or None.
    """
    # 1) Direct cached lookup (fast path)
    try:
        ch = bot.get_channel(thread_id)
        # Accept if it's a thread-like channel (discord.Thread) or has forum parent
        if ch is not None:
            # Prefer explicit Thread type check when available
            if getattr(discord, "Thread", None) and isinstance(ch, discord.Thread):
                return ch
            # Some builds may represent thread channels as Channel objects with 'parent' attr
            if hasattr(ch, "parent") and getattr(ch, "parent", None) is not None:
                # If parent is a ForumChannel, treat this as the thread we want
                parent = getattr(ch, "parent", None)
                if getattr(discord, "ForumChannel", None) and isinstance(parent, discord.ForumChannel):
                    return ch
            # Fallback: if object has 'archived' attribute it is likely a Thread
            if hasattr(ch, "archived"):
                return ch
    except Exception:  # pylint: disable=broad-exception-caught
        # Defensive: don't allow a single lookup failure to crash search
        pass

    # 2) Scan guilds/forum channels in cache (fallback)
    for guild in getattr(bot, "guilds", []):
        for channel in getattr(guild, "channels", []):
            # only consider forum-like channels
            if getattr(discord, "ForumChannel", None) and isinstance(channel, discord.ForumChannel):
                # safe-get get_thread if provided by this discord.py variant
                gfn = getattr(channel, "get_thread", None)
                if callable(gfn):
                    try:
                        t = gfn(thread_id)
                        if t:
                            return t
                    except Exception:  # pylint: disable=broad-exception-caught
                        # ignore non-fatal errors from variant-specific helpers
                        pass
                # iterate known threads list if present
                for t in getattr(channel, "threads", []):
                    try:
                        if getattr(t, "id", None) == thread_id:
                            return t
                    except Exception:  # pylint: disable=broad-exception-caught
                        continue
    return None

@router.get(
    "/threads/{thread_id}",
    response_model=ThreadResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Thread Details",
    description="Get detailed information about a specific thread"
)
async def get_thread(request: Request, thread_id: int) -> ThreadResponse:
    """Get detailed information about a specific thread.

    Resolution strategy:
      1) fast cached lookup via find_thread_by_id(bot, id)
      2) bot.get_channel(id) (cached)
      3) await bot.fetch_channel(id) (API fetch)
    Raises 404 if not found and delegates other exceptions to handle_discord_exception.
    """
    flogger.info(f"get_thread called for thread_id={thread_id}")
    try:
        bot = await resolve_bot(request)

        # 1) cached scan helper
        thread = find_thread_by_id(bot, thread_id)

        # 2) direct cached channel lookup
        if not thread:
            try:
                thread = bot.get_channel(thread_id)
            except Exception:  # pylint: disable=broad-exception-caught
                thread = None

        # 3) final attempt: fetch from API (threads are channels)
        if not thread:
            try:
                thread = await bot.fetch_channel(thread_id)
            except discord.NotFound:
                thread = None
            except discord.Forbidden:
                # treat inaccessible as not found for API purposes
                thread = None

        if not thread:
            flogger.error(f"Thread {thread_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread {thread_id} not found"
            )

        thread_data = ChannelConverter.thread_to_detail(thread)
        flogger.info(f"Successfully retrieved thread {getattr(thread, 'name', thread_id)}")

        return ThreadResponse(
            status="success",
            data=thread_data
        )
    except HTTPException:
        # re-raise FastAPI HTTP errors unchanged
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in get_thread: {exc}")
        # centralized handler converts/logs and raises appropriate HTTPException
        await handle_discord_exception("get thread", exc)

@router.put(
    "/threads/{thread_id}",
    response_model=ThreadResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Thread",
    description="Update a thread's properties"
)
async def update_thread(
    request: Request, thread_id: int, thread_data: ThreadUpdateRequest
) -> ThreadResponse:
    """Update a thread's properties.

    Resolution strategy same as get_thread: try cache first, then fetch.
    Applies the provided updates via thread.edit(...) when necessary.
    """
    flogger.info(f"update_thread called for thread_id={thread_id}")
    try:
        bot = await resolve_bot(request)

        # 1) cached scan helper
        thread = find_thread_by_id(bot, thread_id)

        # 2) direct cached channel lookup
        if not thread:
            try:
                thread = bot.get_channel(thread_id)
            except Exception:  # pylint: disable=broad-exception-caught
                thread = None

        # 3) final attempt: fetch from API
        if not thread:
            try:
                thread = await bot.fetch_channel(thread_id)
            except discord.NotFound:
                thread = None
            except discord.Forbidden:
                thread = None

        if not thread:
            flogger.error(f"Thread {thread_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread {thread_id} not found"
            )

        # Compose update kwargs from the request model
        update_kwargs = {}
        if thread_data.name is not None:
            update_kwargs["name"] = thread_data.name
        if thread_data.archived is not None:
            update_kwargs["archived"] = thread_data.archived
        if thread_data.locked is not None:
            update_kwargs["locked"] = thread_data.locked

        if update_kwargs:
            await thread.edit(**update_kwargs)
            # best-effort: refresh from cache/fetch for up-to-date returned payload
            try:
                refreshed = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
                if refreshed is not None:
                    thread = refreshed
            except Exception:  # pylint: disable=broad-exception-caught
                # ignore refresh failures; use the object we have
                pass

        updated_thread_data = ChannelConverter.thread_to_detail(thread)
        flogger.info(f"Successfully updated thread {getattr(thread, 'name', thread_id)}")

        return ThreadResponse(
            status="updated",
            data=updated_thread_data
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in update_thread: {exc}")
        await handle_discord_exception("update thread", exc)

@router.put(
    "/threads/{thread_id}/close",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Close Thread",
    description="Archive (close) a thread"
)
async def close_thread(request: Request, thread_id: int) -> SuccessResponse:
    """Archive (close) a thread."""
    flogger.info(f"close_thread called for thread_id={thread_id}")
    try:
        bot = await resolve_bot(request)
        # 1) cached scan helper
        thread = find_thread_by_id(bot, thread_id)
        # 2) direct cached channel lookup
        if not thread:
            try:
                thread = bot.get_channel(thread_id)
            except Exception:  # pylint: disable=broad-exception-caught
                thread = None
        # 3) final attempt: fetch from API (threads are channels)
        if not thread:
            try:
                thread = await bot.fetch_channel(thread_id)
            except discord.NotFound:
                thread = None
            except discord.Forbidden:
                # treat inaccessible as not found for API purposes
                thread = None
        if not thread:
            flogger.error(f"Thread {thread_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread {thread_id} not found"
            )
        await thread.edit(archived=True)
        message = f"Thread {thread.name} closed"
        flogger.info(message)
        return SuccessResponse(status="closed", message=message)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in close_thread: {exc}")
        await handle_discord_exception("close thread", exc)


@router.put(
    "/threads/{thread_id}/open",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Open Thread",
    description="Unarchive (open) a thread"
)
async def open_thread(request: Request, thread_id: int) -> SuccessResponse:
    """Unarchive (open) a thread."""
    flogger.info(f"open_thread called for thread_id={thread_id}")
    try:
        bot = await resolve_bot(request)
        # 1) cached scan helper
        thread = find_thread_by_id(bot, thread_id)
        # 2) direct cached channel lookup
        if not thread:
            try:
                thread = bot.get_channel(thread_id)
            except Exception:  # pylint: disable=broad-exception-caught
                thread = None
        # 3) final attempt: fetch from API (threads are channels)
        if not thread:
            try:
                thread = await bot.fetch_channel(thread_id)
            except discord.NotFound:
                thread = None
            except discord.Forbidden:
                # treat inaccessible as not found for API purposes
                thread = None
        if not thread:
            flogger.error(f"Thread {thread_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread {thread_id} not found"
            )
        await thread.edit(archived=False)
        message = f"Thread {thread.name} opened"
        flogger.info(message)
        return SuccessResponse(status="opened", message=message)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in open_thread: {exc}")
        await handle_discord_exception("open thread", exc)

@router.put(
    "/threads/{thread_id}/tags",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Thread Tags",
    description="Update the tags applied to a thread"
)
async def update_thread_tags(
    request: Request, thread_id: int, tags_data: ForumTagListRequest
) -> SuccessResponse:
    """Update the tags applied to a thread."""
    flogger.info(f"update_thread_tags called for thread_id={thread_id}")
    try:
        bot = await resolve_bot(request)

        thread = find_thread_by_id(bot, thread_id)
        if not thread:
            flogger.error(f"Thread {thread_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread {thread_id} not found"
            )

        # Get parent forum channel
        parent_channel = thread.parent
        if not isinstance(parent_channel, discord.ForumChannel):
            flogger.error(f"Thread {thread_id} is not in a forum channel")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Thread is not in a forum channel"
            )

        # Resolve tag IDs to tag objects
        available = parent_channel.available_tags
        resolved_tags = []

        for t in tags_data.tags:
            if isinstance(t, int):
                # Integer ID → resolve to Tag object
                tag_obj = discord.utils.get(available, id=t)
                if not tag_obj:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Tag id {t} not found in channel"
                    )
                resolved_tags.append(tag_obj)
            else:
                # Object or dict → extract fields
                tid = getattr(t, "id", None) if not isinstance(t, dict) else t.get("id")
                name = getattr(t, "name", None) if not isinstance(t, dict) else t.get("name")
                emoji_val = getattr(t, "emoji", None) if not isinstance(t, dict) else t.get("emoji")

                # ID-based resolution
                if tid is not None:
                    tag_obj = discord.utils.get(available, id=tid)
                    if not tag_obj:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Tag id {tid} not found in channel"
                        )
                    resolved_tags.append(tag_obj)
                    continue

                # Name or emoji matching
                matched = None
                if name:
                    matched = discord.utils.get(available, name=name)

                if matched is None and emoji_val:
                    try:
                        norm = normalize_emoji(emoji_val)
                    except Exception:  # pylint: disable=broad-exception-caught
                        norm = emoji_val

                    for at in available:
                        at_e = getattr(at, "emoji", None)
                        if not at_e:
                            continue
                        at_name = getattr(at_e, "name", None) or str(at_e)
                        if norm == at_name or norm == str(at_e):
                            matched = at
                            break

                if matched is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=(
                            f"Tag not found for provided data (name={name!r}, emoji={emoji_val!r}). "
                            "Create the tag first or provide an existing tag id."
                        )
                    )

                resolved_tags.append(matched)

        await thread.edit(applied_tags=resolved_tags)

        message = "Thread tags updated"
        flogger.info(message)
        return SuccessResponse(status="updated", message=message)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in update_thread_tags: {exc}")
        await handle_discord_exception("update thread tags", exc)

@router.get(
    "/threads/{thread_id}/messages",
    response_model=MessageListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Thread Messages",
    description="List replies in a thread"
)
async def list_thread_messages(request: Request, thread_id: int) -> MessageListResponse:
    """List replies in a thread."""
    flogger.info(f"list_thread_messages called for thread_id={thread_id}")
    try:
        bot = await resolve_bot(request)

        thread = find_thread_by_id(bot, thread_id)
        if not thread:
            flogger.error(f"Thread {thread_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread {thread_id} not found"
            )

        msgs = [m async for m in thread.history(limit=100)]
        message_data = [MessageConverter.message_to_payload(m) for m in msgs]

        flogger.info(f"Retrieved {len(message_data)} messages from thread {thread_id}")
        return MessageListResponse(
            status="success",
            data=message_data
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in list_thread_messages: {exc}")
        await handle_discord_exception("list thread messages", exc)

@router.post(
    "/threads/{thread_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Thread Message",
    description="Post a reply to a thread"
)
async def create_thread_message(
    request: Request, thread_id: int, payload: MessageCreateRequest
) -> MessageResponse:
    """Post a reply to a thread."""
    flogger.info(f"create_thread_message called for thread_id={thread_id}")
    try:
        bot = await resolve_bot(request)

        thread = find_thread_by_id(bot, thread_id)
        if not thread:
            flogger.error(f"Thread {thread_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread {thread_id} not found"
            )

        embed = EmbedConverter.payload_to_embed(payload.content)
        msg = await thread.send(embed=embed)

        message_data = MessageConverter.message_to_payload(msg)
        flogger.info(f"Created message {msg.id} in thread {thread_id}")

        return MessageResponse(
            status="created",
            data=message_data
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in create_thread_message: {exc}")
        await handle_discord_exception("create thread message", exc)

@router.get(
    "/threads/{thread_id}/messages/{message_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Thread Message",
    description="Get a single reply from a thread"
)
async def get_thread_message(
    request: Request, thread_id: int, message_id: int
) -> MessageResponse:
    """Get a single reply from a thread."""
    flogger.info(f"get_thread_message called for thread_id={thread_id}, message_id={message_id}")
    try:
        bot = await resolve_bot(request)

        thread = find_thread_by_id(bot, thread_id)
        if not thread:
            flogger.error(f"Thread {thread_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread {thread_id} not found"
            )

        try:
            msg = await thread.fetch_message(message_id)
        except discord.NotFound as exc:
            flogger.error(f"Message {message_id} not found in thread {thread_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {message_id} not found in thread {thread_id}"
            ) from exc

        message_data = MessageConverter.message_to_payload(msg)
        flogger.info(f"Retrieved message {message_id} from thread {thread_id}")

        return MessageResponse(
            status="found",
            data=message_data
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in get_thread_message: {exc}")
        await handle_discord_exception("get thread message", exc)

@router.put(
    "/threads/{thread_id}/messages/{message_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Edit Thread Message",
    description="Edit a reply in a thread"
)
async def edit_thread_message(
    request: Request, thread_id: int, message_id: int, payload: MessageUpdateRequest
) -> MessageResponse:
    """Edit a reply in a thread."""
    flogger.info(f"edit_thread_message called for thread_id={thread_id}, message_id={message_id}")
    try:
        bot = await resolve_bot(request)

        thread = find_thread_by_id(bot, thread_id)
        if not thread:
            flogger.error(f"Thread {thread_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread {thread_id} not found"
            )

        try:
            msg = await thread.fetch_message(message_id)
        except discord.NotFound as exc:
            flogger.error(f"Message {message_id} not found in thread {thread_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {message_id} not found in thread {thread_id}"
            ) from exc

        # Check if bot can edit this message
        if msg.author.id != bot.user.id:
            flogger.error(f"Cannot edit message {message_id} - not sent by bot")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Can only edit messages sent by the bot"
            )

        embed = EmbedConverter.payload_to_embed(payload.content)
        await msg.edit(embed=embed)

        updated_message_data = MessageConverter.message_to_payload(msg)
        flogger.info(f"Updated message {message_id} in thread {thread_id}")

        return MessageResponse(
            status="updated",
            data=updated_message_data
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in edit_thread_message: {exc}")
        await handle_discord_exception("edit thread message", exc)

@router.delete(
    "/threads/{thread_id}/messages/{message_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Thread Message",
    description="Delete a reply in a thread"
)
async def delete_thread_message(
    request: Request, thread_id: int, message_id: int
) -> DeleteResponse:
    """Delete a reply in a thread."""
    flogger.info(f"delete_thread_message called for thread_id={thread_id}, message_id={message_id}")
    try:
        bot = await resolve_bot(request)

        thread = find_thread_by_id(bot, thread_id)
        if not thread:
            flogger.error(f"Thread {thread_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Thread {thread_id} not found"
            )

        try:
            msg = await thread.fetch_message(message_id)
        except discord.NotFound as exc:
            flogger.error(f"Message {message_id} not found in thread {thread_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {message_id} not found in thread {thread_id}"
            ) from exc

        # Check if bot can delete this message
        if msg.author.id != bot.user.id:
            # Check if bot has manage_messages permission in the thread
            bot_member = thread.guild.get_member(bot.user.id)
            if not bot_member or not thread.permissions_for(bot_member).manage_messages:
                flogger.error(f"Cannot delete message {message_id} - insufficient permissions")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Insufficient permissions to delete this message"
                )

        await msg.delete()

        message = f"Message {message_id} deleted from thread {thread_id}"
        flogger.info(message)

        return DeleteResponse(
            status="deleted",
            deleted=True,
            message=message
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in delete_thread_message: {exc}")
        await handle_discord_exception("delete thread message", exc)
