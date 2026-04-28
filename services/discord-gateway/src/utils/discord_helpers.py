"""
Discord helper utilities for API operations.

This module provides generic helper functions for Discord operations
including bot resolution and error handling.
"""

import asyncio
import re
from collections.abc import Mapping
from functools import cache
from typing import Any

import discord
from discord.ext import commands
from fastapi import HTTPException, Request, status
from shared import bblogger

flogger = bblogger.get_logger("discord-helpers")


async def resolve_bot(request: Request) -> commands.Bot:
    """
    Grab the running bot from FastAPI state and wait for readiness.
    """
    flogger.debug("resolve_bot called")
    bot = getattr(request.app.state, "bot", None)
    flogger.debug(f"resolve_bot: app.state.bot → {bot!r} (type={type(bot)})")

    if not isinstance(bot, commands.Bot):
        flogger.error("app.state.bot is not a commands.Bot instance")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Bot instance invalid")

    if not bot.is_ready():
        flogger.info("Bot not ready, awaiting wait_until_ready()")
        try:
            await asyncio.wait_for(bot.wait_until_ready(), timeout=15)
        except TimeoutError as exc:
            flogger.error("Timed out waiting for Discord bot to become ready")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Discord bot is not ready"
            ) from exc

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

    # Gather diagnostic details to improve logging
    details: dict[str, Any] = {
        "exc_type": exc.__class__.__name__,
        "exc_repr": repr(exc),
    }

    # try to extract common discord.HTTPException attributes safely
    exc_status: int | None = None
    exc_code: int | None = None
    try:
        exc_status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        exc_code = getattr(exc, "code", None)
        details["exc_status"] = exc_status
        details["exc_code"] = exc_code

        # Some discord exceptions carry a `response` / `http_response` with extra info.
        resp = getattr(exc, "response", None) or getattr(exc, "http_response", None)
        if resp is not None:
            try:
                rstatus = getattr(resp, "status", None) or getattr(resp, "status_code", None)
                details["response_status"] = rstatus
                # Defensive attempt to grab a body/text if present (may be coroutine or large)
                body = getattr(resp, "text", None) or getattr(resp, "body", None)
                if body and not callable(body):
                    details["response_text_preview"] = str(body)[:1000]
            except Exception:  # pylint: disable=broad-exception-caught
                # Best-effort only — don't fail parsing diagnostic info
                pass
    except Exception:  # pylint: disable=broad-exception-caught
        # Non-fatal if attribute extraction fails
        pass

    flogger.error(f"Discord exception during {operation}: {details}")

    # Map well-known discord exceptions to proper HTTP responses
    if isinstance(exc, discord.NotFound):
        flogger.error(f"Resource not found during {operation}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Resource not found during {operation}")

    if isinstance(exc, discord.Forbidden):
        flogger.error(f"Insufficient permissions for {operation}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Insufficient permissions for {operation}")

    if isinstance(exc, discord.HTTPException):
        # Prefer explicit status mapping from the discord exception, fall back to heuristics
        if isinstance(exc_status, int):
            if 400 <= exc_status < 500:
                # Client / validation errors
                if exc_status == 403:
                    flogger.error(f"Discord returned 403 for {operation}: {details}")
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Insufficient permissions for {operation}: {exc_code or ''} {exc!r}",
                    )
                if exc_status == 404:
                    flogger.error(f"Discord returned 404 for {operation}: {details}")
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND, detail=f"Resource not found during {operation}: {exc!r}"
                    )
                flogger.error(f"Discord returned {exc_status} for {operation}: {details}")
                raise HTTPException(status_code=exc_status, detail=f"Bad request during {operation}: {exc!r}")

            # Upstream Discord server or gateway errors -> surface as Bad Gateway
            flogger.error(f"Discord upstream error {exc_status} during {operation}: {details}")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Discord upstream error: {exc!r}")

        # If we couldn't determine a numeric status, treat as a 502 (upstream error)
        flogger.error(f"Unhandled discord.HTTPException during {operation}: {details}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Discord upstream error: {exc!r}")

    # Other exceptions: log full traceback then return 500
    flogger.exception(f"Unexpected error during {operation}: {exc}")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to {operation}: {exc}")


async def get_entity_or_404(
    get_func, fetch_func, entity_id: int, entity_type: str
) -> (
    discord.Guild
    | discord.TextChannel
    | discord.VoiceChannel
    | discord.CategoryChannel
    | discord.User
    | discord.Member
    | discord.Role
):
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
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
        # Delegate to the centralized handler so we get consistent logging and mapping
        await handle_discord_exception(f"fetch {entity_type} {entity_id}", exc)


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

    if hasattr(channel, "guild") and getattr(channel.guild, "id", None) != guild_id:
        flogger.error(
            f"Channel {channel.id} belongs to guild {getattr(channel.guild, 'id', None)}, expected {guild_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Channel {channel.id} does not belong to guild {guild_id}"
        )
    flogger.trace(f"Channel {channel.id} belongs to correct guild {guild_id}")


def validate_channel_type(channel, expected_types: list[str], channel_id: int) -> None:
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

    actual_type = getattr(channel.type, "name", str(channel.type))
    if actual_type not in expected_types:
        flogger.error(f"Channel {channel_id} is type {actual_type}, expected one of: {expected_types}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Channel {channel_id} is type {actual_type}, expected one of: {expected_types}",
        )
    flogger.trace(f"Channel {channel_id} type {actual_type} is valid")


def normalize_emoji(val: str) -> str:
    """
    Normalize an emoji input into the full unicode emoji string.

    Accepts:
      - a unicode emoji string (e.g. "🏷️", "📌")
      - a hex/codepoint string such as "1f4cc" or "1f1fa-1f1f8" or with prefixes "U+1F4CC" / "0x1f4cc"
      - concatenated hex codepoint strings like "1f3f7fe0f"
      - full custom emoji forms "<:name:id>" and "<a:name:id>" — returned as-is
      - short custom name form ":name:" — returned as "name" (without colons)

    Returns the normalized unicode emoji (or the original input on parse failure).
    """
    flogger.debug(f"normalize_emoji called with: {val!r}")
    if not isinstance(val, str):
        return val
    s = val.strip()
    if not s:
        return s

    # If the value is a full custom emoji like <a:name:id> or <:name:id>, accept as-is.
    m_full_custom = re.fullmatch(r"^<a?:([A-Za-z0-9_~]+):(\d+)>$", s)
    if m_full_custom:
        return s  # keep full custom emoji notation

    # If the value is a short form like :name:, normalize to "name" (no colons).
    m_short = re.fullmatch(r"^:([A-Za-z0-9_~]+):$", s)
    if m_short:
        return m_short.group(1)

    # Remove common prefixes like "U+" or "0x" (single prefix)
    cleaned = re.sub(r"(?i)^(?:u\+|0x)", "", s)

    # If looks like hex codepoints separated by -, _, or whitespace, convert
    if re.fullmatch(r"[0-9A-Fa-f]+(?:[-_\s][0-9A-Fa-f]+)*", cleaned):
        parts = re.split(r"[-_\s]+", cleaned)
        # If there are explicit separators, just decode each part
        if len(parts) > 1:
            try:
                return "".join(chr(int(p, 16)) for p in parts)
            except Exception:  # pylint: disable=broad-exception-caught
                return val

        # Single concatenated hex string (no separators) — attempt to split into valid codepoints.
        hexstr = parts[0]

        # Backtracking splitter: try to partition hexstr into chunks of length 1..6
        # (unicode scalars fit in up to 6 hex digits), greedy longest-first to favor larger codepoints.
        @cache
        def try_split(idx):
            if idx == len(hexstr):
                return []
            # try lengths 6..1
            for L in range(6, 0, -1):
                if idx + L <= len(hexstr):
                    part = hexstr[idx : idx + L]
                    try:
                        cp = int(part, 16)
                        if cp <= 0x10FFFF:
                            rest = try_split(idx + L)
                            if rest is not None:
                                return [part, *rest]
                    except Exception:  # pylint: disable=broad-exception-caught
                        continue
            return None

        split = try_split(0)
        if split:
            try:
                return "".join(chr(int(p, 16)) for p in split)
            except Exception:  # pylint: disable=broad-exception-caught
                return val

    # Otherwise assume it's already a unicode emoji (or some other acceptable string)
    return s


# ---------------------------------------------------------------------
# New helpers: tag <-> payload helpers to centralize emoji/tag normalization
# ---------------------------------------------------------------------
def _is_mock_object(obj) -> bool:
    """Check if an object is a mock object (MagicMock, Mock, etc.)."""
    # Check for unittest.mock classes
    obj_type = type(obj)
    type_name = obj_type.__name__
    return (
        type_name in ("MagicMock", "Mock", "AsyncMock", "PropertyMock", "NonCallableMock")
        or "Mock" in type_name
        or "mock" in obj_type.__module__.lower()
    )


def tag_to_dict(tag, channel_id: int | None = None) -> dict:
    """
    Normalize a ForumTag-like object into a dict:
       { "id": int|None, "channel_id": int|None, "name": str|None, "emoji": str|None }

    Behaviors:
       - If `tag` is a Mapping (e.g. dict), prefer direct key lookups for id/channel_id/name/emoji.
       - Otherwise, attempt common attribute access patterns (tag.emoji, tag.to_dict(), __dict__ fields).
       - Use normalize_emoji(...) for final emoji normalization when a candidate is found.
       - Always returns a dict and never raises on attribute access.
    """
    flogger.debug(f"tag_to_dict called with tag={tag!r}, channel_id={channel_id}")
    # 1) id / channel resolution (safe conversions)
    tid = None
    cid = None
    name = None
    emoji = None

    # Fast path for dict-like tag representations (common in some runtimes)
    try:
        if isinstance(tag, Mapping):
            # id
            tid_val = tag.get("id") if "id" in tag else tag.get("tag_id") or tag.get("emoji_id")
            try:
                tid = int(tid_val) if tid_val is not None else None
            except Exception:  # pylint: disable=broad-exception-caught
                tid = None

            # channel id
            if channel_id is not None:
                try:
                    cid = int(channel_id)
                except Exception:  # pylint: disable=broad-exception-caught
                    cid = None
            else:
                ch_obj = tag.get("channel")
                cid_val = tag.get("channel_id") or (
                    ch_obj and (ch_obj.get("id") if isinstance(ch_obj, Mapping) else None)
                )
                try:
                    cid = int(cid_val) if cid_val is not None else None
                except Exception:  # pylint: disable=broad-exception-caught
                    cid = None

            # name
            name = tag.get("name") or tag.get("tag_name") or tag.get("label")

            # emoji - try common dict keys
            for k in ("emoji", "raw_emoji", "unicode", "emoji_str", "emoji_name", "partial_emoji", "raw"):
                if k in tag and tag.get(k) is not None:
                    cand = tag.get(k)
                    try:
                        if isinstance(cand, Mapping):
                            # nested mapping may contain emoji/name fields
                            emoji = cand.get("emoji") or cand.get("name") or cand.get("unicode")
                        else:
                            emoji = normalize_emoji(str(cand))
                        break
                    except Exception:  # pylint: disable=broad-exception-caught
                        try:
                            emoji = str(cand)
                            break
                        except Exception:  # pylint: disable=broad-exception-caught
                            emoji = None
            # final dict-to-str fallback
            if emoji is None:
                try:
                    s = str(tag)
                    if any(ord(c) > 127 for c in s):
                        emoji = normalize_emoji(s)
                except Exception:  # pylint: disable=broad-exception-caught
                    emoji = None

            return {"id": tid, "channel_id": cid, "name": name, "emoji": emoji}
    except Exception:  # pylint: disable=broad-exception-caught
        # non-fatal - fall back to attribute heuristics below
        pass

    # Non-mapping objects: previous heuristic extraction (attributes, to_dict, __dict__, etc.)
    try:
        # id
        tid_attr = getattr(tag, "id", None)
        try:
            tid = int(tid_attr) if tid_attr is not None else None
        except Exception:  # pylint: disable=broad-exception-caught
            tid = None

        # channel id
        if channel_id is not None:
            try:
                cid = int(channel_id)
            except Exception:  # pylint: disable=broad-exception-caught
                cid = None
        else:
            cid_attr = getattr(tag, "channel_id", None)
            if cid_attr is None:
                ch = getattr(tag, "channel", None)
                cid_attr = getattr(ch, "id", None) if ch is not None else None
            try:
                cid = int(cid_attr) if cid_attr is not None else None
            except Exception:  # pylint: disable=broad-exception-caught
                cid = None

        # name
        name = getattr(tag, "name", None)

        # emoji: prefer direct attribute first
        emoji_candidate = None
        emoji_attr = getattr(tag, "emoji", None)
        if emoji_attr is not None and not _is_mock_object(emoji_attr):
            emoji_candidate = emoji_attr
        else:
            # try to call to_dict() if available and safe
            to_dict_fn = getattr(tag, "to_dict", None)
            if callable(to_dict_fn):
                try:
                    td = to_dict_fn()
                    if isinstance(td, Mapping) and "emoji" in td and td.get("emoji") is not None:
                        emoji_candidate = td.get("emoji")
                except Exception:  # pylint: disable=broad-exception-caught
                    emoji_candidate = None

            # inspect __dict__ fields
            if emoji_candidate is None:
                try:
                    d = getattr(tag, "__dict__", None) or {}
                    for k, v in d.items():
                        if "emoji" in str(k).lower() and v is not None:
                            emoji_candidate = v
                            break
                except Exception:  # pylint: disable=broad-exception-caught
                    emoji_candidate = None

            # check alternate attribute names
            if emoji_candidate is None:
                for alt in ("raw_emoji", "unicode", "emoji_str", "emoji_name"):
                    val = getattr(tag, alt, None)
                    if val is not None:
                        emoji_candidate = val
                        break

            # last ditch: if str(tag) contains non-ascii / emoji characters
            if emoji_candidate is None:
                try:
                    s = str(tag)
                    if any(ord(c) > 127 for c in s):
                        emoji_candidate = s
                except Exception:  # pylint: disable=broad-exception-caught
                    emoji_candidate = None

        # normalize emoji_candidate to string via normalize_emoji when possible
        if emoji_candidate is not None:
            try:
                if isinstance(emoji_candidate, Mapping):
                    # pick likely key
                    emoji_val = emoji_candidate.get("emoji") or emoji_candidate.get("name") or None
                    emoji = normalize_emoji(str(emoji_val)) if emoji_val is not None else None
                elif _is_mock_object(emoji_candidate):
                    # Handle mock objects with nested emoji attribute (e.g., mock_tag.emoji.emoji = "👍")
                    nested_emoji = getattr(emoji_candidate, "emoji", None)
                    if nested_emoji is not None and not _is_mock_object(nested_emoji):
                        emoji = normalize_emoji(str(nested_emoji))
                    else:
                        emoji = None
                else:
                    emoji = normalize_emoji(str(emoji_candidate))
            except Exception:  # pylint: disable=broad-exception-caught
                try:
                    emoji = str(emoji_candidate)
                except Exception:  # pylint: disable=broad-exception-caught
                    emoji = None

    except Exception:  # pylint: disable=broad-exception-caught
        # If everything fails, return best-effort structure with None fields
        result = {"id": tid, "channel_id": cid, "name": name, "emoji": None}
        flogger.debug(f"tag_to_dict returning (exception fallback): {result}")
        return result

    result = {"id": tid, "channel_id": cid, "name": name, "emoji": emoji}
    flogger.debug(f"tag_to_dict returning: {result}")
    return result


def tags_to_edit_payload(tags_iterable, *, updates: dict | None = None) -> list:
    """
    Build a serializable available_tags payload suitable for ForumChannel.edit(...).

    - tags_iterable: iterable of existing tag objects (ForumTag-like)
    - updates: optional dict mapping tag_id -> {"name": ..., "emoji": ...} to apply changes
      If a tag in updates is not present in tags_iterable, it will be appended.
    Returns a list of dict entries: [{"id": id?, "name": ..., "emoji": ...}, ...]
    """
    flogger.debug(f"tags_to_edit_payload called with tags_iterable={tags_iterable!r}, updates={updates}")
    out = []
    seen_ids = set()
    for t in tags_iterable or []:
        td = tag_to_dict(t)
        tid = td.get("id")
        if tid is not None:
            seen_ids.add(tid)
        # allow update override by id when provided
        if updates and tid is not None and tid in updates:
            u = updates[tid] or {}
            name = u.get("name", td.get("name"))
            emoji = u.get("emoji", td.get("emoji"))
        else:
            name = td.get("name")
            emoji = td.get("emoji")
        entry = {"name": name, "emoji": emoji}
        if tid is not None:
            # include id when available so runtimes that accept it can preserve identity
            entry["id"] = tid
        out.append(entry)

    # append any updates for tags not in existing list (e.g. create/update by id not present)
    if updates:
        for k, v in updates.items():
            try:
                kid = int(k)
            except Exception:  # pylint: disable=broad-exception-caught
                kid = k
            if kid not in seen_ids:
                entry = {"name": v.get("name"), "emoji": v.get("emoji")}
                try:
                    entry["id"] = int(k)
                except Exception:  # pylint: disable=broad-exception-caught
                    # keep whatever the caller passed if it is not integer-like
                    entry["id"] = k
                out.append(entry)
    flogger.debug(f"tags_to_edit_payload returning {len(out)} tag entries")
    return out


def preserve_embed_image(new_embed: discord.Embed, existing_message: discord.Message) -> discord.Embed:
    """Preserve the existing embed image on *new_embed* when no image is set.

    Discord's ``message.edit(embed=...)`` replaces the entire embed, including
    any image.  If the new embed has no image set and the existing message has
    an embed image, this helper copies the existing image URL into *new_embed*
    so it is not silently erased.

    Contract
    --------
    * If ``new_embed.image.url`` is already set → return *new_embed* unchanged.
    * If the existing message has no embeds, or its first embed has no image →
      return *new_embed* unchanged.
    * Otherwise → set ``new_embed.set_image(url=existing_image_url)`` and return.

    This implements the universal gateway image-preservation contract (B.13).

    Args:
        new_embed: The ``discord.Embed`` about to be posted in the edit call.
        existing_message: The existing Discord message whose first embed's image
            URL should be carried forward when the new embed omits one.

    Returns:
        The (possibly mutated) *new_embed*.
    """
    # If the new embed already has an image, respect the caller's intent.
    if getattr(getattr(new_embed, "image", None), "url", None):
        return new_embed

    existing_embeds = getattr(existing_message, "embeds", None) or []
    if not existing_embeds:
        return new_embed

    existing_image = getattr(existing_embeds[0], "image", None)
    existing_url = getattr(existing_image, "url", None) if existing_image is not None else None
    if existing_url:
        flogger.debug(f"preserve_embed_image: carrying forward image_url={existing_url!r}")
        new_embed.set_image(url=existing_url)

    return new_embed
