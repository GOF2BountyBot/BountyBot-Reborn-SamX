"""
Time announcement router for the BountyBot API.

This module provides REST endpoints specifically for time announcement
messages, using the modular message builder pattern.
"""

import json
import os
from typing import List, Optional

import httpx
from shared import bblogger
from fastapi import APIRouter, HTTPException, Query, Request, status
from message_builders.factory import MessageBuilderFactory
from persist.repositories.discord_message_repository import DiscordMessageRepository
from pydantic import BaseModel, Field
from routers.discord_message import DiscordMessageResponse, EmbedPayloadDict

flogger = bblogger.get_logger("bot-time-announcement-router")

router = APIRouter(
    prefix="/time",
    tags=["time-announcements"],
    responses={
        404: {"description": "Time announcement not found"},
        500: {"description": "Internal server error"}
    }
)

# Configuration
DISCORD_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
DISCORD_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
DISCORD_GATEWAY_BASE_URL = f"http://{DISCORD_GATEWAY_HOST}:{DISCORD_GATEWAY_PORT}/api/v1"

class TimeAnnouncementRequest(BaseModel):
    """Request for time announcement creation/update."""
    guild_id: int = Field(..., description="Discord guild ID")
    channel_id: int = Field(..., description="Discord channel ID")
    message_id: Optional[int] = Field(None, description="Discord message ID (use for update/get/delete operations)")
    current_time: str = Field(..., description="Current time to announce")

discord_message_repo = DiscordMessageRepository()

@router.post(
    "",
    response_model=DiscordMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Time Announcement",
    description="Create a time announcement and send to Discord"
)
async def create_time_announcement(
    request: Request,
    announcement_request: TimeAnnouncementRequest
) -> DiscordMessageResponse:
    flogger.info(
        f"Creating time announcement: guild={announcement_request.guild_id}, "
        f"channel={announcement_request.channel_id}, time={announcement_request.current_time}"
    )
    flogger.debug(f"Request body: {announcement_request.dict()}")
    try:
        builder = MessageBuilderFactory.create_builder("time_announcement")
        payload_data = builder.build_payload({"current_time": announcement_request.current_time})
        flogger.debug(f"Built embed payload: {payload_data}")
        embed_payload = EmbedPayloadDict(**payload_data)

        gateway_request = {
            "guild_id": announcement_request.guild_id,
            "channel_id": announcement_request.channel_id,
            "content": embed_payload.dict()
        }
        flogger.debug(f"Forwarding to gateway: {gateway_request}")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{DISCORD_GATEWAY_BASE_URL}/messages",
                json=gateway_request,
                timeout=10
            )
        flogger.debug(f"Gateway HTTP status: {resp.status_code}")
        resp.raise_for_status()
        gateway_data = resp.json()
        flogger.debug(f"Gateway response: {gateway_data}")

        if not all(k in gateway_data for k in ("guild_id", "channel_id", "message_id")):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Discord gateway did not return required message identifiers"
            )

        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            record = {
                "guild_id": gateway_data["guild_id"],
                "channel_id": gateway_data["channel_id"],
                "message_id": gateway_data["message_id"],
                "embed_payload": json.dumps(embed_payload.dict()),
                "message_type": "time_announcement"
            }
            message = await discord_message_repo.create_or_update(db, record)
            await db.commit()
            await db.refresh(message)
            flogger.info(f"Time announcement persisted (db id={message.id})")
            return DiscordMessageResponse.from_orm(message)

    except httpx.HTTPStatusError as e:
        flogger.exception("Discord Gateway API error while creating time announcement")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send message to Discord"
        ) from e
    except Exception as e:
        flogger.exception("Unexpected error creating time announcement")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create time announcement: {e}"
        ) from e

@router.put(
    "",
    response_model=DiscordMessageResponse,
    summary="Update Time Announcement",
    description="Update existing time announcement message"
)
async def update_time_announcement(
    request: Request,
    announcement_request: TimeAnnouncementRequest
) -> DiscordMessageResponse:
    flogger.info(
        f"Updating time announcement: guild={announcement_request.guild_id}, "
        f"channel={announcement_request.channel_id}, time={announcement_request.current_time}"
    )
    flogger.debug(f"Request body: {announcement_request.dict()}")
    try:
        if announcement_request.message_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="message_id is required for update operations"
            )
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            # lookup by composite key instead of by type
            existing = await discord_message_repo.get_by_composite_key(
                db,
                announcement_request.guild_id,
                announcement_request.channel_id,
                announcement_request.message_id,
            )
            if not existing:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No existing time announcement found to update"
                )

            builder = MessageBuilderFactory.create_builder("time_announcement")
            payload_data = builder.build_payload({
                "current_time": announcement_request.current_time
            })
            flogger.debug(f"Built embed payload for update: {payload_data}")
            embed_payload = EmbedPayloadDict(**payload_data)

            gateway_request = {
                "guild_id": existing.guild_id,
                "channel_id": existing.channel_id,
                "message_id": existing.message_id,
                "content": embed_payload.dict()
            }
            flogger.debug(f"Forwarding update to gateway: {gateway_request}")
            async with httpx.AsyncClient() as client:
                resp = await client.put(
                    f"{DISCORD_GATEWAY_BASE_URL}/messages",
                    json=gateway_request,
                    timeout=10
                )
            flogger.debug(f"Gateway HTTP status: {resp.status_code}")
            resp.raise_for_status()
            gateway_data = resp.json()
            flogger.debug(f"Gateway response: {gateway_data}")

            if not all(k in gateway_data for k in ("guild_id", "channel_id", "message_id")):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Discord gateway did not return required message identifiers"
                )

            record = {
                "guild_id": gateway_data["guild_id"],
                "channel_id": gateway_data["channel_id"],
                "message_id": gateway_data["message_id"],
                "embed_payload": json.dumps(embed_payload.dict()),
                "message_type": "time_announcement"
            }
            updated = await discord_message_repo.create_or_update(db, record)
            await db.commit()
            await db.refresh(updated)
            flogger.info(f"Time announcement updated (db id={updated.id})")
            return DiscordMessageResponse.from_orm(updated)

    except httpx.HTTPStatusError as e:
        flogger.exception("Discord Gateway API error while updating time announcement")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update message in Discord"
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        flogger.exception("Unexpected error updating time announcement")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update time announcement: {e}"
        ) from e

@router.delete(
    "",
    summary="Delete Time Announcement",
    description="Delete existing time announcement message"
)
async def delete_time_announcement(
    request: Request,
    guild_id: int = Query(..., description="Discord guild ID"),
    channel_id: int = Query(..., description="Discord channel ID"),
    message_id: int = Query(..., description="Discord message ID")
) -> dict:
    flogger.info(
        f"Deleting time announcement: guild={guild_id}, channel={channel_id}, message={message_id}"
    )
    flogger.debug(f"Query params → guild={guild_id}, channel={channel_id}, message={message_id}")
    try:
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            existing = await discord_message_repo.get_by_composite_key(
                db, guild_id, channel_id, message_id
            )
            if not existing:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No time announcement found to delete"
                )

            gateway_request = {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "message_id": message_id
            }
            flogger.debug(f"Forwarding delete to gateway: {gateway_request}")
            async with httpx.AsyncClient() as client:
                resp = await client.delete(
                    f"{DISCORD_GATEWAY_BASE_URL}/messages",
                    json=gateway_request,
                    timeout=10
                )
            flogger.debug(f"Gateway HTTP status: {resp.status_code}")
            resp.raise_for_status()
            gateway_data = resp.json()
            flogger.debug(f"Gateway response: {gateway_data}")

            if not all(k in gateway_data for k in ("guild_id", "channel_id", "message_id")):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Discord gateway did not return required message identifiers"
                )

            deleted = await discord_message_repo.delete_by_composite_key(
                db, guild_id, channel_id, message_id
            )
            await db.commit()
            if not deleted:
                flogger.warning("Discord message deleted but database record not found")

            flogger.info(
                f"Time announcement deleted: guild={guild_id}, "
                f"channel={channel_id}, message={message_id}"
            )
            return {
                "status": "deleted",
                "guild_id": gateway_data["guild_id"],
                "channel_id": gateway_data["channel_id"],
                "message_id": gateway_data["message_id"]
            }

    except httpx.HTTPStatusError as e:
        flogger.exception("Discord Gateway API error while deleting time announcement")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete message from Discord"
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        flogger.exception("Unexpected error deleting time announcement")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete time announcement: {e}"
        ) from e

@router.get(
    "",
    response_model=DiscordMessageResponse,
    summary="Get Time Announcement",
    description="Get existing time announcement message"
)
async def get_time_announcement(
    request: Request,
    guild_id: int = Query(..., description="Discord guild ID"),
    channel_id: int = Query(..., description="Discord channel ID"),
    message_id: int = Query(..., description="Discord message ID")
) -> DiscordMessageResponse:
    flogger.info(
        f"Getting time announcement: guild={guild_id}, "
        f"channel={channel_id}, message={message_id}"
    )
    flogger.debug(f"Query params → guild={guild_id}, channel={channel_id}, message={message_id}")
    try:
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            existing = await discord_message_repo.get_by_composite_key(
                db, guild_id, channel_id, message_id
            )
            if not existing:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No time announcement found"
                )
            flogger.info(f"Time announcement found (db id={existing.id})")
            return DiscordMessageResponse.from_orm(existing)

    except HTTPException:
        raise
    except Exception as e:
        flogger.exception("Unexpected error getting time announcement")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get time announcement: {e}"
        ) from e

@router.get(
    "/guild/{guild_id}",
    response_model=List[DiscordMessageResponse],
    summary="List Time Announcements by Guild",
    description="Returns all time‐announcement messages for the given guild."
)
async def list_time_announcements_by_guild(
    request: Request,
    guild_id: int
) -> List[DiscordMessageResponse]:
    """
    GET /time/guild/{guild_id}
    """
    db_manager = request.app.state.db_manager
    async with db_manager.get_session() as db:
        records = await discord_message_repo.list_by_guild(db, guild_id)
    return [DiscordMessageResponse.from_orm(r) for r in records]


@router.get(
    "/guild/{guild_id}/channel/{channel_id}",
    response_model=List[DiscordMessageResponse],
    summary="List TimeAnanouncements by Channel",
    description="Returns all time‐announcement messages for the given guild and channel."
)
async def list_time_announcements_by_channel(
    request: Request,
    guild_id: int,
    channel_id: int
) -> List[DiscordMessageResponse]:
    """
    GET /time/guild/{guild_id}/channel/{channel_id}
    """
    db_manager = request.app.state.db_manager
    async with db_manager.get_session() as db:
        records = await discord_message_repo.list_by_guild_and_channel(
            db, guild_id, channel_id
        )
    return [DiscordMessageResponse.from_orm(r) for r in records]


@router.get(
    "/guild/{guild_id}/type/{message_type}",
    response_model=List[DiscordMessageResponse],
    summary="List Time Announcements by Type",
    description="Returns all time‐announcement messages for the given guild and message type."
)
async def list_time_announcements_by_type(
    request: Request,
    guild_id: int,
    message_type: str
) -> List[DiscordMessageResponse]:
    """
    GET /time/guild/{guild_id}/type/{message_type}
    """
    db_manager = request.app.state.db_manager
    async with db_manager.get_session() as db:
        records = await discord_message_repo.list_by_guild_and_type(
            db, guild_id, message_type
        )
    return [DiscordMessageResponse.from_orm(r) for r in records]
