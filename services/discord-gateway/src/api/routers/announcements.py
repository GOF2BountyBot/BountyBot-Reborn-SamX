"""Announcements router — unified rendering for game-layer announcements.

Per A.48 unified-loadout-render spec, bounty spawn announcements (and their
edit-on-capture flow) post structured data here instead of pre-rendered
embed dicts. The gateway assembles the final embed via the shared
`cogs/_shared/loadout_embed.build_loadout_embed`, ensuring consistent
1024-char continuation-field handling across `/criminal-loadout`, `/profile`,
and bounty announcements.
"""

import discord
from cogs._shared.loadout_embed import build_loadout_embed
from fastapi import APIRouter, HTTPException, Request, status
from shared import bblogger
from utils.discord_converters import MessageConverter
from utils.discord_helpers import get_entity_or_404, handle_discord_exception, resolve_bot

from api.schemas.announcement_schemas import BountyAnnouncementRequest
from api.schemas.message_schemas import MessageResponse

flogger = bblogger.get_logger("gateway-announcements-router")

router = APIRouter(
    tags=["announcements"],
    responses={
        400: {"description": "Invalid request"},
        403: {"description": "Insufficient permissions"},
        404: {"description": "Channel/message/guild not found"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"},
    },
)


@router.post(
    "/announcements/bounty/channel/{channel_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Bounty Announcement",
    description=(
        "Render a unified bounty-announcement embed using the shared loadout builder and post it to the given channel."
    ),
)
async def create_bounty_announcement(
    request: Request,
    channel_id: int,
    payload: BountyAnnouncementRequest,
) -> MessageResponse:
    """Render and post a bounty announcement embed."""
    flogger.info(f"create_bounty_announcement called for channel_id={channel_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not hasattr(channel, "send"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel_id} cannot receive messages",
            )

        embed = _build_bounty_embed(payload, captured=payload.metadata.captured)
        payout_embed = _build_payout_embed(payload.metadata)

        if payout_embed is not None:
            message = await channel.send(content=payload.text_content, embeds=[embed, payout_embed])
        else:
            message = await channel.send(content=payload.text_content, embed=embed)

        message_obj = MessageConverter.message_to_payload(message)
        flogger.info(
            f"Created bounty announcement message {message.id} in channel {channel_id} title={payload.metadata.title!r}"
        )
        return MessageResponse(status="created", data=message_obj)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in create_bounty_announcement: {exc}")
        await handle_discord_exception("create bounty announcement", exc)


@router.put(
    "/announcements/bounty/channel/{channel_id}/message/{message_id}",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Edit Bounty Announcement",
    description=(
        "Re-render and edit an existing bounty announcement (e.g. to reflect checked systems or the captured state)."
    ),
)
async def edit_bounty_announcement(
    request: Request,
    channel_id: int,
    message_id: int,
    payload: BountyAnnouncementRequest,
) -> MessageResponse:
    """Edit an existing bounty announcement.

    Image-URL preservation semantics
    ---------------------------------
    ``discord.Message.edit(embed=new_embed)`` **replaces the entire embed**,
    including the image.  If the new embed has no image set, Discord clears
    the previous image — it does NOT preserve it automatically.

    To keep the route-map visible across state-transition edits (e.g.
    "recently visited", "system checked"), this handler applies the following
    rule:

    * If ``payload.metadata.image_url`` is **non-None** → use that URL
      (explicit caller-supplied value, may add or replace the image).
    * If ``payload.metadata.image_url`` is **None** → inspect the *existing*
      message's first embed for its ``image.url`` and carry it forward.
      This preserves the route map that was set when the announcement was
      first posted.  If the existing message has no image, the new embed
      also renders without one (no error, no image).
    """
    flogger.info(f"edit_bounty_announcement called for channel_id={channel_id} message_id={message_id}")
    try:
        bot = await resolve_bot(request)
        channel = await get_entity_or_404(bot.get_channel, bot.fetch_channel, channel_id, "Channel")

        if not hasattr(channel, "fetch_message"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Channel {channel_id} cannot contain messages",
            )

        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {message_id} not found in channel {channel_id}",
            ) from exc

        if not getattr(message, "author", None) or message.author.id != getattr(bot.user, "id", None):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Can only edit messages sent by the bot",
            )

        # Resolve the effective image URL before building the embed.
        # Three cases:
        #   - image_url is a non-empty string → use it explicitly (add/replace image)
        #   - image_url is "" (empty string sentinel) → clear the image (captured state)
        #   - image_url is None → preserve the existing message's image (state-transition edits)
        raw_image_url = payload.metadata.image_url
        if raw_image_url == "":
            # Explicit clear — captured state, no route map wanted.
            effective_image_url = None
            flogger.debug(f"edit_bounty_announcement: clearing image for message_id={message_id} (captured)")
        elif raw_image_url is None:
            # Preserve existing route map image across non-captured state-transition edits.
            effective_image_url = None
            existing_embeds = getattr(message, "embeds", []) or []
            if existing_embeds:
                existing_image = getattr(existing_embeds[0], "image", None)
                if existing_image is not None:
                    existing_url = getattr(existing_image, "url", None)
                    if existing_url:
                        effective_image_url = existing_url
                        flogger.debug(
                            f"edit_bounty_announcement: preserving existing image_url={existing_url!r} "
                            f"for message_id={message_id}"
                        )
        else:
            effective_image_url = raw_image_url

        embed = _build_bounty_embed(payload, image_url_override=effective_image_url, captured=payload.metadata.captured)
        payout_embed = _build_payout_embed(payload.metadata)

        if payout_embed is not None:
            await message.edit(embeds=[embed, payout_embed])
        else:
            await message.edit(embed=embed)

        updated_data = MessageConverter.message_to_payload(message)
        flogger.info(f"Edited bounty announcement message {message_id} in channel {channel_id}")
        return MessageResponse(status="updated", data=updated_data)
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in edit_bounty_announcement: {exc}")
        await handle_discord_exception("edit bounty announcement", exc)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _build_payout_embed(meta) -> discord.Embed | None:
    """Build a payout breakdown embed from BountyAnnouncementMetadata.

    Returns None if any of reward, reward_per_sys, or route_length is missing.
    Otherwise returns a gold-coloured embed with Capture Bonus, Per System Check,
    Route Length, Max System Payout, and Max Total Payout fields (all inline).
    """
    if meta.reward is None or meta.reward_per_sys is None or meta.route_length is None:
        return None

    capture_bonus = int(meta.reward * 0.25)
    max_sys_payout = meta.reward_per_sys * meta.route_length
    max_total = capture_bonus + max_sys_payout

    embed = discord.Embed(title="💰 Payout Breakdown", color=0xFFD700)
    embed.add_field(name="🎯 Capture Bonus", value=f"{capture_bonus:,} cr", inline=True)
    embed.add_field(name="📍 Per System Check", value=f"{meta.reward_per_sys:,} cr", inline=True)
    embed.add_field(name="🗺️ Route Length", value=f"{meta.route_length} systems", inline=True)
    embed.add_field(name="💡 Max System Payout", value=f"{max_sys_payout:,} cr", inline=True)
    embed.add_field(name="🏆 Max Total Payout", value=f"{max_total:,} cr", inline=True)
    return embed


def _build_bounty_embed(
    payload: BountyAnnouncementRequest,
    image_url_override: str | None = None,
    captured: bool = False,
) -> discord.Embed:
    """Render a `discord.Embed` from a BountyAnnouncementRequest using build_loadout_embed.

    Bounty announcements always treat the cargo section as visible (matches the
    `/criminal-loadout` callsite which passes viewer_is_owner_or_admin=True).

    Args:
        payload: The structured announcement request from bot-core.
        image_url_override: When provided, this value is used as the embed image URL
            instead of ``payload.metadata.image_url``.  The edit handler uses this
            to forward the URL recovered from the existing message so that the route
            map image is preserved across state-transition edits.
        captured: When True, the loadout sections (Active Ship, Ship Stats, weapons,
            modules, cargo) are suppressed — only title, description, prefix fields,
            and suffix fields are shown.
    """
    meta = payload.metadata
    resolved_image_url = image_url_override if image_url_override is not None else meta.image_url
    return build_loadout_embed(
        payload.loadout_response,
        viewer_is_owner_or_admin=True,
        title_override=meta.title,
        color_override=meta.color,
        footer_text=meta.footer_text,
        image_url=resolved_image_url,
        prefix_fields=list(meta.prefix_fields),
        suffix_fields=list(meta.suffix_fields),
        captured=captured,
    )
