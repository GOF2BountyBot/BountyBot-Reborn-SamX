"""
Generic Discord message router for the BountyBot API.

This module provides REST endpoints for persisting and retrieving
Discord message information. Message-type specific logic is handled
by dedicated routers in the announcements/ subdirectory.
"""

import json
import os
from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, HTTPException, status, Request
from pydantic import BaseModel, Field
from datetime import datetime
import requests

import shared.bblogger as bblogger
from persist.repositories.discord_message_repository import DiscordMessageRepository

flogger = bblogger.get_logger("bot-discord-message-router")

router = APIRouter(
    prefix="/discord-message",
    tags=["discord-message"],
    responses={
        404: {"description": "Message not found"},
        500: {"description": "Internal server error"}
    }
)

# Configuration
DISCORD_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
DISCORD_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
DISCORD_GATEWAY_BASE_URL = f"http://{DISCORD_GATEWAY_HOST}:{DISCORD_GATEWAY_PORT}/api/v1"

# Generic Pydantic models
class EmbedPayloadDict(BaseModel):
    """Embed payload as dictionary for storage."""
    title: Optional[str] = None
    description: Optional[str] = None
    color: Optional[int] = None
    fields: List[dict] = []
    footer_text: Optional[str] = None
    footer_icon_url: Optional[str] = None
    timestamp: Optional[str] = None
    thumbnail_url: Optional[str] = None
    image_url: Optional[str] = None

class DiscordMessageRequest(BaseModel):
    """Request model for generic Discord message operations."""
    guild_id: int = Field(..., description="Discord guild ID")
    channel_id: int = Field(..., description="Discord channel ID")
    message_id: Optional[int] = Field(None, description="Discord message ID (use for update/get/delete operations)")
    embed_payload: EmbedPayloadDict = Field(..., description="Embed payload for the message")
    message_type: str = Field("general", description="Type of message")

class DiscordMessageResponse(BaseModel):
    """Response model for Discord message operations."""
    id: UUID
    guild_id: int
    channel_id: int
    message_id: int
    embed_payload: str
    message_type: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        json_encoders = { UUID: lambda u: str(u) }

# Initialize repository
discord_message_repo = DiscordMessageRepository()

@router.post(
    "",
    response_model=DiscordMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Discord Message Record",
    description="Create a generic Discord message record and send to Discord"
)
async def create_discord_message(
    request: Request,
    message_request: DiscordMessageRequest
) -> DiscordMessageResponse:
    flogger.info(
        f"Create request received: guild={message_request.guild_id}, "
        f"channel={message_request.channel_id}, type={message_request.message_type}"
    )
    flogger.debug(f"Payload: {message_request.dict()}")
    try:
        # Send to Discord Gateway first
        gateway_request = {
            "guild_id": message_request.guild_id,
            "channel_id": message_request.channel_id,
            "content": message_request.embed_payload.dict()
        }
        flogger.debug(f"Forwarding to gateway: {gateway_request}")
        response = requests.post(
            f"{DISCORD_GATEWAY_BASE_URL}/messages",
            json=gateway_request,
            timeout=10
        )
        flogger.debug(f"Gateway HTTP status: {response.status_code}")
        response.raise_for_status()
        gateway_data = response.json()
        flogger.debug(f"Gateway response: {gateway_data}")

        # Extract IDs from gateway response
        if not all(k in gateway_data for k in ('guild_id','channel_id','message_id')):
            flogger.error("Gateway response missing required identifiers")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Discord gateway did not return required message identifiers"
            )

        # Persist to database
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            record = {
                "guild_id": gateway_data['guild_id'],
                "channel_id": gateway_data['channel_id'],
                "message_id": gateway_data['message_id'],
                "embed_payload": json.dumps(message_request.embed_payload.dict()),
                "message_type": message_request.message_type
            }
            flogger.debug(f"Persisting record: {record}")
            message = await discord_message_repo.create_or_update(db, record)
            await db.commit()
            await db.refresh(message)
            flogger.info(f"Discord message record created (db id={message.id})")
            return DiscordMessageResponse.from_orm(message)

    except requests.HTTPError:
        flogger.exception("Discord Gateway API error while creating message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send message to Discord"
        )
    except HTTPException:
        raise
    except Exception as e:
        flogger.exception("Unexpected error in create_discord_message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create message: {str(e)}"
        )

@router.put(
    "",
    response_model=DiscordMessageResponse,
    summary="Update Discord Message Record",
    description="Update a generic Discord message record and send update to Discord"
)
async def update_discord_message(
    request: Request,
    message_request: DiscordMessageRequest
) -> DiscordMessageResponse:
    if message_request.message_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="message_id must be provided to update"
        )
    flogger.info(
        f"Update request received: guild={message_request.guild_id}, "
        f"channel={message_request.channel_id}, "
        f"message={message_request.message_id}, "
        f"type={message_request.message_type}"
    )
    flogger.debug(f"Payload: {message_request.dict()}")
    try:
        # Send update to Discord Gateway
        gateway_request = {
            "guild_id": message_request.guild_id,
            "channel_id": message_request.channel_id,
            "message_id": message_request.message_id,
            "content": message_request.embed_payload.dict()
        }
        flogger.debug(f"Forwarding update to gateway: {gateway_request}")
        resp = requests.put(
            f"{DISCORD_GATEWAY_BASE_URL}/messages",
            json=gateway_request,
            timeout=10
        )
        flogger.debug(f"Gateway HTTP status: {resp.status_code}")
        resp.raise_for_status()
        gateway_data = resp.json()
        flogger.debug(f"Gateway response: {gateway_data}")

        if not all(k in gateway_data for k in ("guild_id","channel_id","message_id")):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Discord gateway did not return required message identifiers"
            )

        # Persist update
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            record = {
                "guild_id": gateway_data["guild_id"],
                "channel_id": gateway_data["channel_id"],
                "message_id": gateway_data["message_id"],
                "embed_payload": json.dumps(message_request.embed_payload.dict()),
                "message_type": message_request.message_type
            }
            msg = await discord_message_repo.create_or_update(db, record)
            await db.commit()
            await db.refresh(msg)
            flogger.info(f"Discord message updated (db id={msg.id})")
            return DiscordMessageResponse.from_orm(msg)

    except requests.HTTPError:
        flogger.exception("Discord Gateway API error while updating message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send update to Discord"
        )
    except HTTPException:
        raise
    except Exception as e:
        flogger.exception("Unexpected error updating Discord message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update message: {e}"
        )

@router.get(
    "/{message_record_id}",
    response_model=DiscordMessageResponse,
    summary="Get Discord Message Record",
    description="Get a Discord message record by its database ID"
)
async def get_discord_message(
    request: Request,
    message_record_id: UUID,   # <- switched from int to UUID
) -> DiscordMessageResponse:
    flogger.info(f"Fetch request for record id={message_record_id}")
    try:
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            message = await discord_message_repo.get_by_id(db, message_record_id)
            if not message:
                flogger.warning(f"Record {message_record_id} not found")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Message record {message_record_id} not found"
                )
            flogger.debug(f"Record found: {message}")
            return DiscordMessageResponse.from_orm(message)

    except HTTPException:
        raise
    except Exception as e:
        flogger.exception("Unexpected error in get_discord_message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get message record: {str(e)}"
        )

@router.get(
    "/guild/{guild_id}",
    response_model=List[DiscordMessageResponse],
    summary="List Discord Messages by Guild",
    description="Return all Discord message records for the given guild."
)
async def list_discord_messages_by_guild(
    request: Request,
    guild_id: int
) -> List[DiscordMessageResponse]:
    db_manager = request.app.state.db_manager
    async with db_manager.get_session() as db:
        records = await discord_message_repo.list_by_guild(db, guild_id)
    return [DiscordMessageResponse.from_orm(r) for r in records]

@router.get(
    "/guild/{guild_id}/channel/{channel_id}",
    response_model=List[DiscordMessageResponse],
    summary="List Discord Messages by Channel",
    description="Return all Discord message records for the given guild and channel."
)
async def list_discord_messages_by_channel(
    request: Request,
    guild_id: int,
    channel_id: int
) -> List[DiscordMessageResponse]:
    db_manager = request.app.state.db_manager
    async with db_manager.get_session() as db:
        records = await discord_message_repo.list_by_guild_and_channel(db, guild_id, channel_id)
    return [DiscordMessageResponse.from_orm(r) for r in records]

@router.get(
    "/guild/{guild_id}/type/{message_type}",
    response_model=List[DiscordMessageResponse],
    summary="List Discord Messages by Type",
    description="Return all Discord message records for the given guild and message type."
)
async def list_discord_messages_by_type(
    request: Request,
    guild_id: int,
    message_type: str
) -> List[DiscordMessageResponse]:
    db_manager = request.app.state.db_manager
    async with db_manager.get_session() as db:
        records = await discord_message_repo.list_by_guild_and_type(db, guild_id, message_type)
    return [DiscordMessageResponse.from_orm(r) for r in records]

@router.delete(
    "/{message_record_id}",
    summary="Delete Discord Message Record",
    description="Delete a Discord message record by its database ID"
)
async def delete_discord_message(
    request: Request,
    message_record_id: UUID,   # <- switched from int to UUID
) -> dict:
    flogger.info(f"Delete request for record id={message_record_id}")
    try:
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            message = await discord_message_repo.get_by_id(db, message_record_id)
            if not message:
                flogger.warning(f"Record {message_record_id} not found for deletion")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Message record {message_record_id} not found"
                )
            await discord_message_repo.remove(db, message)
            await db.commit()
            flogger.info(f"Record {message_record_id} deleted")
            return {"status": "deleted", "message_record_id": message_record_id}

    except HTTPException:
        raise
    except Exception as e:
        flogger.exception("Unexpected error in delete_discord_message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete message record: {str(e)}"
        )