"""
Tags router for Discord Gateway API.

This module provides REST endpoints for managing Discord forum tags
with simplified URIs that don't require channel context.
"""

from contextlib import suppress
from typing import Any

import discord
from fastapi import APIRouter, HTTPException, Request, status
from shared import bblogger

from api.schemas.base_schemas import DeleteResponse
from api.schemas.channel_schemas import (
    ForumTagCreateRequest,
    ForumTagResponse,
    ForumTagUpdateRequest,
)
from utils.discord_converters import ChannelConverter
from utils.discord_helpers import (
    get_entity_or_404,
    handle_discord_exception,
    normalize_emoji,
    resolve_bot,
    tags_to_edit_payload,
)

flogger = bblogger.get_logger("gateway-tag-router")

router = APIRouter(
    tags=["tags"],
    responses={
        404: {"description": "Tag not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"},
    },
)


@router.get(
    "/tags/{tag_id}",
    response_model=ForumTagResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Forum Tag",
    description="Get details for a single forum tag",
)
async def get_tag(request: Request, tag_id: int) -> ForumTagResponse:
    """Get details for a single forum tag."""
    flogger.info(f"get_tag called for tag_id={tag_id}")
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
                detail=f"Tag {tag_id} not found",
            )

        # Convert to payload and ensure channel_id present. Normalize emoji for consistency.
        tag_payload = ChannelConverter.forum_tag_to_payload(tag, channel_id=parent_channel.id)
        if isinstance(tag_payload, dict):
            tag_payload["channel_id"] = parent_channel.id
            if tag_payload.get("emoji") is not None:
                with suppress(Exception):
                    tag_payload["emoji"] = normalize_emoji(tag_payload["emoji"])
        else:
            try:
                tag_payload.channel_id = parent_channel.id
            except Exception:  # pylint: disable=broad-exception-caught
                tag_payload = dict(getattr(tag_payload, "__dict__", {}) or {})
                tag_payload["channel_id"] = parent_channel.id
                if tag_payload.get("emoji") is not None:
                    with suppress(Exception):
                        tag_payload["emoji"] = normalize_emoji(tag_payload["emoji"])

        flogger.info(f"Successfully retrieved tag {getattr(tag, 'name', tag_id)}")
        return ForumTagResponse(status="success", data=tag_payload)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in get_tag: {exc}")
        await handle_discord_exception("get tag", exc)


@router.post(
    "/channels/{channel_id}/tags",
    response_model=ForumTagResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Forum Tag",
    description="Create a new tag in a forum channel",
)
async def create_forum_tag(request: Request, channel_id: int, tag: ForumTagCreateRequest) -> ForumTagResponse:
    """Create a tag in a ForumChannel."""
    flogger.info(f"create_forum_tag called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not isinstance(channel, discord.ForumChannel):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a forum channel")

        emoji_value = None
        if tag.emoji:
            try:
                emoji_value = normalize_emoji(tag.emoji)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                raise HTTPException(
                    status_code=status.HTTP_422,
                    detail=f"Invalid emoji: {tag.emoji}",
                ) from exc

        # Prefer higher-level API when available
        try:
            new_tag = await channel.create_tag(name=tag.name, emoji=emoji_value)
        except AttributeError:
            # Some runtimes may not expose create_tag; try edit-based fallback
            existing = list(getattr(channel, "available_tags", []) or [])
            # Use centralized helper to build a normalized edit payload (includes ids when available)
            payloads = tags_to_edit_payload(existing)
            # append the new tag as a serializable dict
            payloads.append({"name": tag.name, "emoji": emoji_value})
            # Many runtimes accept a list of dicts; some expect objects implementing to_dict()
            try:
                await channel.edit(available_tags=payloads)
            except AttributeError:
                # Fallback: wrap dicts in proxy objects exposing to_dict()
                class _TagProxy:
                    def __init__(self, d: dict[str, Any]):
                        self._d = d

                    def to_dict(self):
                        out = {"name": self._d.get("name"), "emoji": self._d.get("emoji")}
                        if "id" in self._d and self._d["id"] is not None:
                            try:
                                out["id"] = int(self._d["id"])
                            except Exception:  # pylint: disable=broad-exception-caught
                                out["id"] = self._d["id"]
                        return out

                proxy_payloads = [_TagProxy(p) for p in payloads]
                await channel.edit(available_tags=proxy_payloads)

            # Re-resolve the created tag
            new_tag = discord.utils.get(channel.available_tags, name=tag.name)

        tag_data = ChannelConverter.forum_tag_to_payload(new_tag, channel_id=channel_id)
        # Normalize response emoji (and ensure channel_id present)
        if isinstance(tag_data, dict):
            tag_data["channel_id"] = channel_id
            if tag_data.get("emoji") is not None:
                with suppress(Exception):
                    tag_data["emoji"] = normalize_emoji(tag_data["emoji"])
        else:
            try:
                tag_data.channel_id = channel_id
            except Exception:  # pylint: disable=broad-exception-caught
                tag_data = dict(getattr(tag_data, "__dict__", {}) or {})
                tag_data["channel_id"] = channel_id
                if tag_data.get("emoji") is not None:
                    with suppress(Exception):
                        tag_data["emoji"] = normalize_emoji(tag_data["emoji"])

        flogger.info(f"Successfully created tag {getattr(new_tag, 'name', tag.name)}")
        return ForumTagResponse(status="created", data=tag_data)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in create_forum_tag: {exc}")
        await handle_discord_exception("create forum tag", exc)


@router.put(
    "/tags/{tag_id}",
    response_model=ForumTagResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Forum Tag",
    description="Update a forum tag's properties",
)
async def update_tag(request: Request, tag_id: int, tag_data: ForumTagUpdateRequest) -> ForumTagResponse:
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
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tag {tag_id} not found")

        # Prepare update parameters
        update_kwargs: dict[str, Any] = {}
        if tag_data.name is not None:
            update_kwargs["name"] = tag_data.name
        if tag_data.emoji is not None:
            try:
                emoji_value = normalize_emoji(tag_data.emoji)
                update_kwargs["emoji"] = emoji_value
            except Exception as exc:  # pylint: disable=broad-exception-caught
                raise HTTPException(
                    status_code=status.HTTP_422,
                    detail=f"Invalid emoji: {tag_data.emoji}",
                ) from exc

        # Update the tag
        if update_kwargs:
            # Try several library shapes in order of likelihood:
            # 1) Tag object exposes edit()
            # 2) ForumChannel exposes edit_tag()
            # 3) Fallback: edit available_tags list using centralized helper
            try:
                if hasattr(tag, "edit"):
                    await tag.edit(**update_kwargs)
                elif hasattr(parent_channel, "edit_tag"):
                    await parent_channel.edit_tag(tag, **update_kwargs)
                else:
                    # Build updates map keyed by id -> {"name": ..., "emoji": ...}
                    upd_map: dict[Any, dict[str, str | None]] = {}
                    try:
                        upd_map[int(tag_id)] = {"name": update_kwargs.get("name"), "emoji": update_kwargs.get("emoji")}
                    except Exception:  # pylint: disable=broad-exception-caught
                        # If tag_id isn't int-like, keep as-is
                        upd_map[tag_id] = {"name": update_kwargs.get("name"), "emoji": update_kwargs.get("emoji")}

                    payloads = tags_to_edit_payload(parent_channel.available_tags, updates=upd_map)
                    try:
                        await parent_channel.edit(available_tags=payloads)
                    except AttributeError:
                        # Wrap dicts in proxy objects that implement to_dict()
                        class _TagProxy:
                            def __init__(self, d: dict[str, Any]):
                                self._d = d

                            def to_dict(self):
                                out = {"name": self._d.get("name"), "emoji": self._d.get("emoji")}
                                if "id" in self._d and self._d["id"] is not None:
                                    try:
                                        out["id"] = int(self._d["id"])
                                    except Exception:  # pylint: disable=broad-exception-caught
                                        out["id"] = self._d["id"]
                                return out

                        proxy_payloads = [_TagProxy(p) for p in payloads]
                        await parent_channel.edit(available_tags=proxy_payloads)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # let centralized handler map/log/raise
                raise exc from exc

        # Re-fetch the tag to get updated data
        updated_tag = discord.utils.get(parent_channel.available_tags, id=tag_id)
        if not updated_tag and tag_data.name:
            updated_tag = discord.utils.get(parent_channel.available_tags, name=tag_data.name)
        if not updated_tag:
            updated_tag = tag  # Use original if we can't find updated

        # Use converter (delegates to centralized helpers) and ensure channel_id present
        updated_tag_data = ChannelConverter.forum_tag_to_payload(updated_tag, channel_id=parent_channel.id)
        if isinstance(updated_tag_data, dict):
            updated_tag_data["channel_id"] = parent_channel.id
            if updated_tag_data.get("emoji") is not None:
                with suppress(Exception):
                    updated_tag_data["emoji"] = normalize_emoji(updated_tag_data["emoji"])
            elif tag_data.emoji is not None:
                # best-effort: reflect requested emoji when runtime didn't expose it
                try:
                    updated_tag_data["emoji"] = normalize_emoji(tag_data.emoji)
                except Exception:  # pylint: disable=broad-exception-caught
                    updated_tag_data["emoji"] = tag_data.emoji
        else:
            try:
                updated_tag_data.channel_id = parent_channel.id
            except Exception:  # pylint: disable=broad-exception-caught
                updated_tag_data = dict(getattr(updated_tag_data, "__dict__", {}) or {})
                updated_tag_data["channel_id"] = parent_channel.id
                if updated_tag_data.get("emoji") is not None:
                    with suppress(Exception):
                        updated_tag_data["emoji"] = normalize_emoji(updated_tag_data["emoji"])
                elif tag_data.emoji is not None:
                    try:
                        updated_tag_data["emoji"] = normalize_emoji(tag_data.emoji)
                    except Exception:  # pylint: disable=broad-exception-caught
                        updated_tag_data["emoji"] = tag_data.emoji

        flogger.info(f"Successfully updated tag {getattr(updated_tag, 'name', tag_id)}")
        return ForumTagResponse(status="updated", data=updated_tag_data)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in update_tag: {exc}")
        await handle_discord_exception("update tag", exc)


@router.delete(
    "/tags/{tag_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Forum Tag",
    description="Remove a tag from its forum channel",
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
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tag {tag_id} not found")

        tag_name = getattr(tag, "name", str(tag_id))
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
                        with suppress(Exception):
                            payloads.append({"name": t.name, "emoji": getattr(t, "emoji", None)})
                    try:
                        await parent_channel.edit(available_tags=payloads)
                        deleted = True
                    except AttributeError:
                        # Wrap dicts in proxy objects implementing to_dict()
                        class _TagProxy:
                            def __init__(self, d: dict[str, Any]):
                                self._d = d

                            def to_dict(self):
                                out = {"name": self._d.get("name"), "emoji": self._d.get("emoji")}
                                if "id" in self._d and self._d["id"] is not None:
                                    try:
                                        out["id"] = int(self._d["id"])
                                    except Exception:  # pylint: disable=broad-exception-caught
                                        out["id"] = self._d["id"]
                                return out

                        proxy_payloads = [_TagProxy(p) for p in payloads]
                        await parent_channel.edit(available_tags=proxy_payloads)
                        deleted = True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Allow centralized handler below to map/log/raise appropriately
            raise exc from exc

        if not deleted:
            # If none of the strategies worked, raise a server error
            flogger.error(f"Unable to delete tag {tag_id} — unsupported library shape")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unable to delete tag {tag_id}: unsupported runtime",
            )

        message = f"Tag {tag_name} deleted"
        flogger.info(message)
        return DeleteResponse(status="deleted", deleted=True, message=message)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in delete_tag: {exc}")
        await handle_discord_exception("delete tag", exc)
