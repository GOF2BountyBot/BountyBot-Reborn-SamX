"""
Generic Discord message router for the BountyBot API.

This module provides REST endpoints for persisting and retrieving
Discord message information. Message-type specific logic is handled
by dedicated routers in the announcements/ subdirectory.
"""

import asyncio
import json
import os
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from persist.repositories.discord_message_repository import DiscordMessageRepository
from shared import bblogger

from api.schemas.discord_message_schema import (
    DiscordMessageRequest,
    DiscordMessageResponse,
)

flogger = bblogger.get_logger("bot-discord-message-router")

router = APIRouter(
    prefix="/discord-message",
    tags=["discord-message"],
    responses={404: {"description": "Message not found"}, 500: {"description": "Internal server error"}},
)

# Configuration
DISCORD_GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
DISCORD_GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
DISCORD_GATEWAY_BASE_URL = f"http://{DISCORD_GATEWAY_HOST}:{DISCORD_GATEWAY_PORT}/api/v1"

_MAX_RETRIES = 2
_RETRY_DELAY = 1.0  # seconds

# Initialize repository
discord_message_repo = DiscordMessageRepository()


async def _post_with_retry(url: str, payload: dict, timeout: float = 10.0) -> httpx.Response:
    """POST to url with up to _MAX_RETRIES retries on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(1 + _MAX_RETRIES):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                flogger.warning(f"Gateway POST attempt {attempt + 1} failed ({exc!r}), retrying in {_RETRY_DELAY}s...")
                await asyncio.sleep(_RETRY_DELAY)
        except httpx.HTTPStatusError:  # pylint: disable=try-except-raise
            raise
    raise last_exc  # type: ignore[misc]


async def _put_with_retry(url: str, payload: dict, timeout: float = 10.0) -> httpx.Response:
    """PUT to url with up to _MAX_RETRIES retries on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(1 + _MAX_RETRIES):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.put(url, json=payload, timeout=timeout)
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                flogger.warning(f"Gateway PUT attempt {attempt + 1} failed ({exc!r}), retrying in {_RETRY_DELAY}s...")
                await asyncio.sleep(_RETRY_DELAY)
        except httpx.HTTPStatusError:  # pylint: disable=try-except-raise
            raise
    raise last_exc  # type: ignore[misc]


@router.post(
    "",
    response_model=DiscordMessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Discord Message Record",
    description="Create a generic Discord message record and send to Discord",
)
async def create_discord_message(request: Request, message_request: DiscordMessageRequest) -> DiscordMessageResponse:
    flogger.info(
        f"Create request received: guild={message_request.guild_id}, "
        f"channel={message_request.channel_id}, type={message_request.message_type}"
    )
    flogger.debug(f"Payload: {message_request.model_dump()}")
    try:
        # Send to Discord Gateway first
        gateway_request = {
            "guild_id": message_request.guild_id,
            "channel_id": message_request.channel_id,
            "content": message_request.embed_payload.model_dump(),
        }
        flogger.debug(f"Forwarding to gateway: {gateway_request}")
        response = await _post_with_retry(
            f"{DISCORD_GATEWAY_BASE_URL}/messages",
            gateway_request,
        )
        flogger.debug(f"Gateway HTTP status: {response.status_code}")
        gateway_data = response.json()
        flogger.debug(f"Gateway response: {gateway_data}")

        # Extract IDs from gateway response
        if not all(k in gateway_data for k in ("guild_id", "channel_id", "message_id")):
            flogger.error("Gateway response missing required identifiers")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Discord gateway did not return required message identifiers",
            )

        # Persist to database
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            record = {
                "guild_id": gateway_data["guild_id"],
                "channel_id": gateway_data["channel_id"],
                "message_id": gateway_data["message_id"],
                "embed_payload": json.dumps(message_request.embed_payload.model_dump()),
                "message_type": message_request.message_type,
            }
            flogger.debug(f"Persisting record: {record}")
            message = await discord_message_repo.create_or_update(db, record)
            await db.commit()
            await db.refresh(message)
            flogger.info(f"Discord message record created (db id={message.id})")
            return DiscordMessageResponse.from_orm(message)

    except httpx.HTTPStatusError as e:
        flogger.exception("Discord Gateway API error while creating message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to send message to Discord"
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        flogger.exception("Unexpected error in create_discord_message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create message: {e!s}"
        ) from e


@router.put(
    "",
    response_model=DiscordMessageResponse,
    summary="Update Discord Message Record",
    description="Update a generic Discord message record and send update to Discord",
)
async def update_discord_message(request: Request, message_request: DiscordMessageRequest) -> DiscordMessageResponse:
    if message_request.message_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message_id must be provided to update")
    flogger.info(
        f"Update request received: guild={message_request.guild_id}, "
        f"channel={message_request.channel_id}, "
        f"message={message_request.message_id}, "
        f"type={message_request.message_type}"
    )
    flogger.debug(f"Payload: {message_request.model_dump()}")
    try:
        # Send update to Discord Gateway
        gateway_request = {
            "guild_id": message_request.guild_id,
            "channel_id": message_request.channel_id,
            "message_id": message_request.message_id,
            "content": message_request.embed_payload.model_dump(),
        }
        flogger.debug(f"Forwarding update to gateway: {gateway_request}")
        resp = await _put_with_retry(
            f"{DISCORD_GATEWAY_BASE_URL}/messages",
            gateway_request,
        )
        flogger.debug(f"Gateway HTTP status: {resp.status_code}")
        gateway_data = resp.json()
        flogger.debug(f"Gateway response: {gateway_data}")

        if not all(k in gateway_data for k in ("guild_id", "channel_id", "message_id")):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Discord gateway did not return required message identifiers",
            )

        # Persist update
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            record = {
                "guild_id": gateway_data["guild_id"],
                "channel_id": gateway_data["channel_id"],
                "message_id": gateway_data["message_id"],
                "embed_payload": json.dumps(message_request.embed_payload.model_dump()),
                "message_type": message_request.message_type,
            }
            msg = await discord_message_repo.create_or_update(db, record)
            await db.commit()
            await db.refresh(msg)
            flogger.info(f"Discord message updated (db id={msg.id})")
            return DiscordMessageResponse.from_orm(msg)

    except httpx.HTTPStatusError as e:
        flogger.exception("Discord Gateway API error while updating message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to send update to Discord"
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        flogger.exception("Unexpected error updating Discord message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update message: {e}"
        ) from e


@router.get(
    "/{message_record_id}",
    response_model=DiscordMessageResponse,
    summary="Get Discord Message Record",
    description="Get a Discord message record by its database ID",
)
async def get_discord_message(
    request: Request,
    message_record_id: UUID,
) -> DiscordMessageResponse:
    flogger.info(f"Fetch request for record id={message_record_id}")
    try:
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            message = await discord_message_repo.get_by_id(db, message_record_id)
            if not message:
                flogger.warning(f"Record {message_record_id} not found")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Message record {message_record_id} not found"
                )
            flogger.debug(f"Record found: {message}")
            return DiscordMessageResponse.from_orm(message)

    except HTTPException:
        raise
    except Exception as e:
        flogger.exception("Unexpected error in get_discord_message")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get message record: {e!s}"
        ) from e


@router.get(
    "/guild/{guild_id}",
    response_model=list[DiscordMessageResponse],
    summary="List Discord Messages by Guild",
    description="Return all Discord message records for the given guild.",
)
async def list_discord_messages_by_guild(request: Request, guild_id: int) -> list[DiscordMessageResponse]:
    db_manager = request.app.state.db_manager
    async with db_manager.get_session() as db:
        records = await discord_message_repo.list_by_guild(db, guild_id)
    return [DiscordMessageResponse.from_orm(r) for r in records]


@router.get(
    "/guild/{guild_id}/channel/{channel_id}",
    response_model=list[DiscordMessageResponse],
    summary="List Discord Messages by Channel",
    description="Return all Discord message records for the given guild and channel.",
)
async def list_discord_messages_by_channel(
    request: Request, guild_id: int, channel_id: int
) -> list[DiscordMessageResponse]:
    db_manager = request.app.state.db_manager
    async with db_manager.get_session() as db:
        records = await discord_message_repo.list_by_guild_and_channel(db, guild_id, channel_id)
    return [DiscordMessageResponse.from_orm(r) for r in records]


@router.get(
    "/guild/{guild_id}/type/{message_type}",
    response_model=list[DiscordMessageResponse],
    summary="List Discord Messages by Type",
    description="Return all Discord message records for the given guild and message type.",
)
async def list_discord_messages_by_type(
    request: Request, guild_id: int, message_type: str
) -> list[DiscordMessageResponse]:
    db_manager = request.app.state.db_manager
    async with db_manager.get_session() as db:
        records = await discord_message_repo.list_by_guild_and_type(db, guild_id, message_type)
    return [DiscordMessageResponse.from_orm(r) for r in records]


@router.delete(
    "/{message_record_id}",
    summary="Delete Discord Message Record",
    description="Delete a Discord message record by its database ID",
)
async def delete_discord_message(
    request: Request,
    message_record_id: UUID,
) -> dict:
    flogger.info(f"Delete request for record id={message_record_id}")
    try:
        db_manager = request.app.state.db_manager
        async with db_manager.get_session() as db:
            message = await discord_message_repo.get_by_id(db, message_record_id)
            if not message:
                flogger.warning(f"Record {message_record_id} not found for deletion")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Message record {message_record_id} not found"
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
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete message record: {e!s}"
        ) from e
