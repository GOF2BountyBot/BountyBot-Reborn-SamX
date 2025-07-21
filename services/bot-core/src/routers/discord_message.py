"""
Generic Discord message router for the BountyBot API.

This module provides REST endpoints for persisting and retrieving
Discord message information. Message-type specific logic is handled
by dedicated routers in the announcements/ subdirectory.
"""

import json
import os
from typing import Optional
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
DISCORD_GATEWAY_BASE_URL = os.getenv("DISCORD_GATEWAY_BASE_URL", "http://discord-gateway:8080/api/v1")

# Generic Pydantic models
class EmbedPayloadDict(BaseModel):
    """Embed payload as dictionary for storage."""
    title: Optional[str] = None
    description: Optional[str] = None
    color: Optional[int] = None
    fields: list[dict] = []
    footer_text: Optional[str] = None
    footer_icon_url: Optional[str] = None
    timestamp: Optional[str] = None
    thumbnail_url: Optional[str] = None
    image_url: Optional[str] = None

class DiscordMessageRequest(BaseModel):
    """Request model for generic Discord message operations."""
    guild_id: int = Field(..., description="Discord guild ID")
    channel_id: int = Field(..., description="Discord channel ID")
    embed_payload: EmbedPayloadDict = Field(..., description="Embed payload for the message")
    message_type: str = Field("general", description="Type of message")

class DiscordMessageResponse(BaseModel):
    """Response model for Discord message operations."""
    id: int
    guild_id: int
    channel_id: int
    message_id: int
    embed_payload: str
    message_type: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

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
    """Create a generic Discord message record and send to Discord."""
    flogger.info(f"Creating Discord message: guild={message_request.guild_id}, "
                f"channel={message_request.channel_id}, type={message_request.message_type}")
    
    try:
        # Send to Discord Gateway first
        gateway_request = {
            "guild_id": message_request.guild_id,
            "channel_id": message_request.channel_id,
            "content": message_request.embed_payload.dict()
        }
        
        response = requests.post(
            f"{DISCORD_GATEWAY_BASE_URL}/messages",
            json=gateway_request,
            timeout=10
        )
        response.raise_for_status()
        gateway_data = response.json()
        
        # Extract IDs from gateway response
        if not all(k in gateway_data for k in ['guild_id', 'channel_id', 'message_id']):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Discord gateway did not return required message identifiers"
            )
        
        # Persist to database
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            message = await discord_message_repo.create_or_update(
                db, {
                    "guild_id": gateway_data['guild_id'],
                    "channel_id": gateway_data['channel_id'],
                    "message_id": gateway_data['message_id'],
                    "embed_payload": json.dumps(message_request.embed_payload.dict()),
                    "message_type": message_request.message_type
                }
            )
            await db.commit()
            await db.refresh(message)
            
            return DiscordMessageResponse.from_orm(message)
    
    except requests.HTTPError as e:
        flogger.error(f"Discord Gateway API error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send message to Discord"
        )
    except Exception as e:
        flogger.error(f"Error creating Discord message: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create message: {str(e)}"
        )

@router.get(
    "/{message_record_id}",
    response_model=DiscordMessageResponse,
    summary="Get Discord Message Record",
    description="Get a Discord message record by its database ID"
)
async def get_discord_message(
    request: Request,
    message_record_id: int
) -> DiscordMessageResponse:
    """Get a Discord message record by database ID."""
    flogger.info(f"Getting Discord message record: {message_record_id}")
    
    try:
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            message = await discord_message_repo.get_by_id(db, message_record_id)
            if not message:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Message record {message_record_id} not found"
                )
            
            return DiscordMessageResponse.from_orm(message)
    
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error getting Discord message record: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get message record: {str(e)}"
        )

@router.delete(
    "/{message_record_id}",
    summary="Delete Discord Message Record",
    description="Delete a Discord message record by its database ID"
)
async def delete_discord_message(
    request: Request,
    message_record_id: int
) -> dict:
    """Delete a Discord message record by database ID."""
    flogger.info(f"Deleting Discord message record: {message_record_id}")
    
    try:
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            message = await discord_message_repo.get_by_id(db, message_record_id)
            if not message:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Message record {message_record_id} not found"
                )
            
            await discord_message_repo.remove(db, message)
            await db.commit()
            
            return {"status": "deleted", "message_record_id": message_record_id}
    
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error deleting Discord message record: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete message record: {str(e)}"
        )
