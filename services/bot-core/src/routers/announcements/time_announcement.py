"""
Time announcement router for the BountyBot API.

This module provides REST endpoints specifically for time announcement
messages, using the modular message builder pattern.
"""

import json
import os
from fastapi import APIRouter, HTTPException, status, Request, Query
from pydantic import BaseModel, Field
from datetime import datetime
import requests

import shared.bblogger as bblogger
from persist.repositories.discord_message_repository import DiscordMessageRepository
from message_builders.factory import MessageBuilderFactory
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
DISCORD_GATEWAY_BASE_URL = os.getenv("DISCORD_GATEWAY_BASE_URL", "http://discord-gateway:8080/api/v1")

# Time announcement specific models
class TimeAnnouncementRequest(BaseModel):
    """Request for time announcement creation/update."""
    guild_id: int = Field(..., description="Discord guild ID")
    channel_id: int = Field(..., description="Discord channel ID")
    current_time: str = Field(..., description="Current time to announce")

# Initialize repository and builder
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
    """Create a time announcement message."""
    flogger.info(f"Creating time announcement: guild={announcement_request.guild_id}, "
                f"channel={announcement_request.channel_id}, time={announcement_request.current_time}")
    
    try:
        # Get the time announcement builder
        builder = MessageBuilderFactory.create_builder("time_announcement")
        
        # Build the embed payload using the builder
        payload_data = builder.build_payload({"current_time": announcement_request.current_time})
        embed_payload_dict = EmbedPayloadDict(**payload_data)
        
        # Send to Discord Gateway
        gateway_request = {
            "guild_id": announcement_request.guild_id,
            "channel_id": announcement_request.channel_id,
            "content": embed_payload_dict.dict()
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
                    "embed_payload": json.dumps(embed_payload_dict.dict()),
                    "message_type": "time_announcement"
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
        flogger.error(f"Error creating time announcement: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create time announcement: {str(e)}"
        )

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
    """Update the existing time announcement message."""
    flogger.info(f"Updating time announcement: guild={announcement_request.guild_id}, "
                f"channel={announcement_request.channel_id}, time={announcement_request.current_time}")
    
    try:
        # Get existing message from database
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            existing_message = await discord_message_repo.get_by_type(
                db, "time_announcement", announcement_request.guild_id, announcement_request.channel_id
            )
            
            if not existing_message:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No existing time announcement found to update"
                )
            
            # Get the time announcement builder and build updated payload
            builder = MessageBuilderFactory.create_builder("time_announcement")
            payload_data = builder.build_payload({"current_time": announcement_request.current_time})
            embed_payload_dict = EmbedPayloadDict(**payload_data)
            
            # Send update to Discord Gateway
            gateway_request = {
                "guild_id": existing_message.guild_id,
                "channel_id": existing_message.channel_id,
                "message_id": existing_message.message_id,
                "content": embed_payload_dict.dict()
            }
            
            response = requests.put(
                f"{DISCORD_GATEWAY_BASE_URL}/messages",
                json=gateway_request,
                timeout=10
            )
            response.raise_for_status()
            gateway_data = response.json()
            
            # Verify gateway response has required fields
            if not all(k in gateway_data for k in ['guild_id', 'channel_id', 'message_id']):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Discord gateway did not return required message identifiers"
                )
            
            # Update in database
            updated_message = await discord_message_repo.create_or_update(
                db, {
                    "guild_id": gateway_data['guild_id'],
                    "channel_id": gateway_data['channel_id'],
                    "message_id": gateway_data['message_id'],
                    "embed_payload": json.dumps(embed_payload_dict.dict()),
                    "message_type": "time_announcement"
                }
            )
            await db.commit()
            await db.refresh(updated_message)
            
            return DiscordMessageResponse.from_orm(updated_message)
    
    except HTTPException:
        raise
    except requests.HTTPError as e:
        flogger.error(f"Discord Gateway API error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update message in Discord"
        )
    except Exception as e:
        flogger.error(f"Error updating time announcement: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update time announcement: {str(e)}"
        )

@router.delete(
    "",
    summary="Delete Time Announcement",
    description="Delete existing time announcement message"
)
async def delete_time_announcement(
    request: Request,
    guild_id: int = Query(..., description="Discord guild ID"),
    channel_id: int = Query(..., description="Discord channel ID")
) -> dict:
    """Delete the existing time announcement message."""
    flogger.info(f"Deleting time announcement: guild={guild_id}, channel={channel_id}")
    
    try:
        # Get existing message from database
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            existing_message = await discord_message_repo.get_by_type(
                db, "time_announcement", guild_id, channel_id
            )
            
            if not existing_message:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No time announcement found to delete"
                )
            
            # Send delete to Discord Gateway
            gateway_request = {
                "guild_id": existing_message.guild_id,
                "channel_id": existing_message.channel_id,
                "message_id": existing_message.message_id
            }
            
            response = requests.delete(
                f"{DISCORD_GATEWAY_BASE_URL}/messages",
                json=gateway_request,
                timeout=10
            )
            response.raise_for_status()
            gateway_data = response.json()
            
            # Verify gateway response
            if not all(k in gateway_data for k in ['guild_id', 'channel_id', 'message_id']):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Discord gateway did not return required message identifiers"
                )
            
            # Delete from database
            deleted = await discord_message_repo.delete_by_composite_key(
                db, existing_message.guild_id, existing_message.channel_id, existing_message.message_id
            )
            await db.commit()
            
            if not deleted:
                flogger.warning(f"Message was deleted from Discord but not found in database")
            
            return {
                "status": "deleted",
                "guild_id": gateway_data['guild_id'],
                "channel_id": gateway_data['channel_id'],
                "message_id": gateway_data['message_id']
            }
    
    except HTTPException:
        raise
    except requests.HTTPError as e:
        flogger.error(f"Discord Gateway API error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete message from Discord"
        )
    except Exception as e:
        flogger.error(f"Error deleting time announcement: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete time announcement: {str(e)}"
        )

@router.get(
    "",
    response_model=DiscordMessageResponse,
    summary="Get Time Announcement",
    description="Get existing time announcement message"
)
async def get_time_announcement(
    request: Request,
    guild_id: int = Query(..., description="Discord guild ID"),
    channel_id: int = Query(..., description="Discord channel ID")
) -> DiscordMessageResponse:
    """Get the existing time announcement message."""
    flogger.info(f"Getting time announcement: guild={guild_id}, channel={channel_id}")
    
    try:
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            message = await discord_message_repo.get_by_type(
                db, "time_announcement", guild_id, channel_id
            )
            
            if not message:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No time announcement found"
                )
            
            return DiscordMessageResponse.from_orm(message)
    
    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error getting time announcement: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get time announcement: {str(e)}"
        )
