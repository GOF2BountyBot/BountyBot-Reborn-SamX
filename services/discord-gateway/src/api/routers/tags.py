"""
Tags router for Discord Gateway API.

This module provides REST endpoints for managing Discord forum tags
with simplified URIs that don't require channel context.
"""

from fastapi import APIRouter, HTTPException, Request, status
import discord
import shared.bblogger as bblogger
from api.schemas.channel_schemas import ForumTagResponse, ForumTagUpdateRequest
from api.schemas.base_schemas import DeleteResponse
from utils.discord_helpers import resolve_bot, handle_discord_exception, normalize_emoji
from utils.discord_converters import ChannelConverter

flogger = bblogger.get_logger("gateway-tag-router")

router = APIRouter(
    tags=["tags"],
    responses={
        404: {"description": "Tag not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

@router.get(
    "/tags/{tag_id}",
    response_model=ForumTagResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Forum Tag",
    description="Get details for a single forum tag"
)
async def update_tag(
    request: Request, tag_id: int, tag_data: ForumTagUpdateRequest
) -> ForumTagResponse:
    """Update a forum tag's properties."""
    flogger.info(f"update_tag called for tag_id={tag_id}")
    try:
        bot = await resolve_bot(request)
        # Search for the tag across all forum channels
        tag = None
        parent_channel = None
        for guild in bot.guilds:
            for channel in guild.channels:
                if isinstance(channel, discord.ForumChannel):
                    tag = discord.utils.get(channel.available_tags, id=tag_id)
                    if tag:
                        parent_channel = channel
                        break
            if tag:
                break
        if not tag or not parent_channel:
            flogger.error(f"Tag {tag_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tag {tag_id} not found"
            )
        # Prepare update parameters
        update_kwargs = {}
        if tag_data.name is not None:
            update_kwargs["name"] = tag_data.name
        if tag_data.emoji is not None:
            try:
                emoji_value = normalize_emoji(tag_data.emoji)
                update_kwargs["emoji"] = emoji_value
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid emoji: {tag_data.emoji}"
                )
        # Update the tag — defensive across library variants
        if update_kwargs:
            try:
                # Preferred API if present on ForumChannel
                if hasattr(parent_channel, "edit_tag"):
                    await parent_channel.edit_tag(tag, **update_kwargs)
                # Fallback to tag.edit() if tag object supports it
                elif hasattr(tag, "edit"):
                    await tag.edit(**update_kwargs)
                else:
                    # Final fallback: rebuild available_tags excluding/including updated entry
                    remaining = []
                    for t in parent_channel.available_tags:
                        try:
                            tid = getattr(t, "id", None)
                        except Exception:
                            tid = None
                        if tid == tag_id:
                            # construct updated representation for this tag
                            try:
                                # prefer object-like substitution if channel.edit accepts it
                                updated_obj = t
                                if "name" in update_kwargs:
                                    setattr(updated_obj, "name", update_kwargs["name"])
                                if "emoji" in update_kwargs:
                                    setattr(updated_obj, "emoji", update_kwargs["emoji"])
                                remaining.append(updated_obj)
                            except Exception:
                                # fallback to dict payload
                                payload = {"name": update_kwargs.get("name", getattr(t, "name", None))}
                                emoji_val = update_kwargs.get("emoji", getattr(t, "emoji", None))
                                if emoji_val is not None:
                                    payload["emoji"] = emoji_val
                                remaining.append(payload)
                        else:
                            remaining.append(t)
                    # try to apply as objects first
                    try:
                        await parent_channel.edit(available_tags=remaining)
                    except TypeError:
                        # fallback to serializable dict form
                        serial = []
                        for t in remaining:
                            if isinstance(t, dict):
                                serial.append(t)
                            else:
                                try:
                                    serial.append({"name": getattr(t, "name", None), "emoji": getattr(t, "emoji", None)})
                                except Exception:
                                    continue
                        await parent_channel.edit(available_tags=serial)
            except HTTPException:
                raise
            except Exception as exc:
                # let centralized handler map/log/raise
                raise exc
        # Re-fetch the tag to get updated data
        updated_tag = discord.utils.get(parent_channel.available_tags, id=tag_id)
        if not updated_tag:
            # Fallback - the tag might have changed ID, search by name
            if tag_data.name:
                updated_tag = discord.utils.get(parent_channel.available_tags, name=tag_data.name)
        if not updated_tag:
            updated_tag = tag  # Use original if we can't find updated
        updated_tag_data = ChannelConverter.forum_tag_to_payload(updated_tag)
        # Ensure channel_id exists on the returned payload (support dict/object)
        if isinstance(updated_tag_data, dict):
            updated_tag_data["channel_id"] = parent_channel.id
        else:
            try:
                setattr(updated_tag_data, "channel_id", parent_channel.id)
            except Exception:
                updated_tag_data = dict(getattr(updated_tag_data, "__dict__", {}) or {})
                updated_tag_data["channel_id"] = parent_channel.id
        flogger.info(f"Successfully updated tag {updated_tag.name}")
        return ForumTagResponse(
            status="updated",
            data=updated_tag_data
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in update_tag: {exc}")
        await handle_discord_exception("update tag", exc)

@router.put(
    "/tags/{tag_id}",
    response_model=ForumTagResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Forum Tag",
    description="Update a forum tag's properties"
)
async def update_tag(
    request: Request, tag_id: int, tag_data: ForumTagUpdateRequest
) -> ForumTagResponse:
    """Update a forum tag's properties."""
    flogger.info(f"update_tag called for tag_id={tag_id}")
    try:
        bot = await resolve_bot(request)
        # Search for the tag across all forum channels
        tag = None
        parent_channel = None
        for guild in bot.guilds:
            for channel in guild.channels:
                if isinstance(channel, discord.ForumChannel):
                    tag = discord.utils.get(channel.available_tags, id=tag_id)
                    if tag:
                        parent_channel = channel
                        break
            if tag:
                break
        if not tag or not parent_channel:
            flogger.error(f"Tag {tag_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tag {tag_id} not found"
            )
        # Prepare update parameters
        update_kwargs = {}
        if tag_data.name is not None:
            update_kwargs["name"] = tag_data.name
        if tag_data.emoji is not None:
            try:
                emoji_value = normalize_emoji(tag_data.emoji)
                update_kwargs["emoji"] = emoji_value
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid emoji: {tag_data.emoji}"
                )
        # Update the tag
        if update_kwargs:
            await parent_channel.edit_tag(tag, **update_kwargs)
        # Re-fetch the tag to get updated data
        updated_tag = discord.utils.get(parent_channel.available_tags, id=tag_id)
        if not updated_tag:
            # Fallback - the tag might have changed ID, search by name
            if tag_data.name:
                updated_tag = discord.utils.get(parent_channel.available_tags, name=tag_data.name)
        if not updated_tag:
            updated_tag = tag  # Use original if we can't find updated
        updated_tag_data = ChannelConverter.forum_tag_to_payload(updated_tag)
        # Ensure channel_id exists on the returned payload (support dict/object)
        if isinstance(updated_tag_data, dict):
            updated_tag_data["channel_id"] = parent_channel.id
        else:
            try:
                setattr(updated_tag_data, "channel_id", parent_channel.id)
            except Exception:
                updated_tag_data = dict(getattr(updated_tag_data, "__dict__", {}) or {})
                updated_tag_data["channel_id"] = parent_channel.id
        flogger.info(f"Successfully updated tag {updated_tag.name}")
        return ForumTagResponse(
            status="updated",
            data=updated_tag_data
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in update_tag: {exc}")
        await handle_discord_exception("update tag", exc)

@router.delete(
    "/tags/{tag_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Forum Tag",
    description="Remove a tag from its forum channel"
)
async def delete_tag(request: Request, tag_id: int) -> DeleteResponse:
    """Remove a tag from its forum channel."""
    flogger.info(f"delete_tag called for tag_id={tag_id}")
    try:
        bot = await resolve_bot(request)
        # Search for the tag across all forum channels
        tag = None
        parent_channel = None
        for guild in bot.guilds:
            for channel in guild.channels:
                if isinstance(channel, discord.ForumChannel):
                    tag = discord.utils.get(channel.available_tags, id=tag_id)
                    if tag:
                        parent_channel = channel
                        break
            if tag:
                break
        if not tag or not parent_channel:
            flogger.error(f"Tag {tag_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tag {tag_id} not found"
            )
        tag_name = tag.name
        # Defensive deletion: support multiple discord.py/library variants.
        # 1) Preferred API if present on ForumChannel
        # 2) Fallback to tag.delete() if tag object exposes it
        # 3) Final fallback: edit available_tags on the channel to exclude the tag
        deleted = False
        try:
            if hasattr(parent_channel, "delete_tag"):
                await parent_channel.delete_tag(tag)
                deleted = True
            elif hasattr(tag, "delete"):
                await tag.delete()
                deleted = True
            else:
                # Attempt to remove the tag by editing the channel's available_tags
                remaining = [t for t in parent_channel.available_tags if getattr(t, "id", None) != tag_id]
                try:
                    # Some discord.py variants accept a list of ForumTag objects
                    await parent_channel.edit(available_tags=remaining)
                    deleted = True
                except TypeError:
                    # Other variants expect a serializable payload (dicts). Build minimal payloads.
                    payloads = []
                    for t in remaining:
                        try:
                            payloads.append({"name": t.name, "emoji": getattr(t, "emoji", None)})
                        except Exception:
                            # best-effort: skip malformed tag objects
                            continue
                    await parent_channel.edit(available_tags=payloads)
                    deleted = True
        except Exception as exc:
            # Allow centralized handler below to map/log/raise appropriately
            raise exc
        if not deleted:
            # If none of the strategies worked, raise a server error
            flogger.error(f"Unable to delete tag {tag_id} — unsupported library shape")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unable to delete tag {tag_id}: unsupported runtime"
            )
        message = f"Tag {tag_name} deleted"
        flogger.info(message)
        return DeleteResponse(
            status="deleted",
            deleted=True,
            message=message
        )
    except HTTPException:
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in delete_tag: {exc}")
        await handle_discord_exception("delete tag", exc)