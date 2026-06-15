"""Tests for the discord_message API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.

The discord_message router uses request.app.state.db_manager (not
get_db_session), and makes outbound HTTP calls via httpx to the
Discord Gateway. Tests mock both of these dependencies.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_UUID = uuid4()


def make_mock_message(**overrides):
    defaults = dict(
        id=SAMPLE_UUID,
        guild_id=67890,
        channel_id=11111,
        message_id=99999,
        embed_payload=json.dumps({"title": "Test", "description": "Hello"}),
        message_type="general",
        created_at=datetime(2026, 1, 1, 0, 0, 0),
        updated_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    defaults.update(overrides)
    msg = MagicMock()
    for k, v in defaults.items():
        setattr(msg, k, v)
    return msg


def make_embed_payload():
    return {
        "title": "Test Embed",
        "description": "A test message",
        "color": 16711680,
        "fields": [],
        "footer_text": None,
        "footer_icon_url": None,
        "timestamp": None,
        "thumbnail_url": None,
        "image_url": None,
    }


def make_gateway_response():
    return {"guild_id": 67890, "channel_id": 11111, "message_id": 99999}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db_session():
    """Mock async db session from app.state.db_manager."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture
def mock_db_manager(mock_db_session):
    manager = MagicMock()
    manager.get_session = MagicMock()
    manager.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_db_session)
    manager.get_session.return_value.__aexit__ = AsyncMock(return_value=False)
    return manager


@pytest.fixture
def mock_message_repo():
    repo = MagicMock()
    repo.create_or_update = AsyncMock(return_value=make_mock_message())
    repo.get_by_id = AsyncMock(return_value=make_mock_message())
    repo.list_by_guild = AsyncMock(return_value=[make_mock_message()])
    repo.list_by_guild_and_channel = AsyncMock(return_value=[make_mock_message()])
    repo.list_by_guild_and_type = AsyncMock(return_value=[make_mock_message()])
    repo.remove = AsyncMock()
    repo.get_by_guild_type_and_reference = AsyncMock(return_value=make_mock_message())
    repo.delete_by_guild_type_and_reference = AsyncMock(return_value=True)
    return repo


@pytest.fixture
def test_app(mock_db_manager, mock_message_repo):
    import api.routers.discord_message as dm_module
    from api.routers.discord_message import router as discord_message_router

    app = FastAPI()
    app.include_router(discord_message_router, prefix="/api/v1")
    app.state.db_manager = mock_db_manager

    # Patch the module-level repository instance
    dm_module.discord_message_repo = mock_message_repo

    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# Gateway HTTP mock helper
# ---------------------------------------------------------------------------


def make_mock_http_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or make_gateway_response()
    resp.raise_for_status = MagicMock()
    return resp


# ===========================================================================
# 1. POST /discord-message  (create)
# ===========================================================================


class TestCreateDiscordMessage:
    """Tests for POST /api/v1/discord-message."""

    @patch("api.routers.discord_message.httpx.AsyncClient")
    def test_create_discord_message_happy_path(self, mock_httpx_class, client, mock_message_repo, mock_db_session):
        """Returns 201 with DiscordMessageResponse on success."""
        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=make_mock_http_response())

        mock_db_session.refresh = AsyncMock()
        # DiscordMessageResponse.from_orm needs the object
        with patch(
            "api.routers.discord_message.DiscordMessageResponse.from_orm",
            return_value=MagicMock(
                id=str(SAMPLE_UUID),
                guild_id=67890,
                channel_id=11111,
                message_id=99999,
                embed_payload=json.dumps({"title": "Test"}),
                message_type="general",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ):
            payload = {
                "guild_id": 67890,
                "channel_id": 11111,
                "embed_payload": make_embed_payload(),
                "message_type": "general",
            }

            response = client.post("/api/v1/discord-message", json=payload)

        assert response.status_code == 201

    @patch("api.routers.discord_message.httpx.AsyncClient")
    def test_create_discord_message_gateway_http_error_returns_500(self, mock_httpx_class, client):
        """Returns 500 when the gateway returns a non-2xx response."""
        import httpx as real_httpx

        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)

        bad_resp = MagicMock()
        bad_resp.status_code = 500
        bad_resp.raise_for_status.side_effect = real_httpx.HTTPStatusError(
            "Server error", request=MagicMock(), response=bad_resp
        )
        mock_http.post = AsyncMock(return_value=bad_resp)

        payload = {
            "guild_id": 67890,
            "channel_id": 11111,
            "embed_payload": make_embed_payload(),
        }

        response = client.post("/api/v1/discord-message", json=payload)

        assert response.status_code == 500
        assert "Failed to send message to Discord" in response.json()["detail"]

    @patch("api.routers.discord_message.httpx.AsyncClient")
    def test_create_discord_message_gateway_missing_ids_returns_500(self, mock_httpx_class, client):
        """Returns 500 when gateway response is missing required identifiers."""
        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)

        # Response missing message_id
        incomplete_resp = make_mock_http_response(json_data={"guild_id": 67890, "channel_id": 11111})
        mock_http.post = AsyncMock(return_value=incomplete_resp)

        payload = {
            "guild_id": 67890,
            "channel_id": 11111,
            "embed_payload": make_embed_payload(),
        }

        response = client.post("/api/v1/discord-message", json=payload)

        assert response.status_code == 500

    def test_create_discord_message_missing_required_fields_returns_422(self, client):
        """Returns 422 when required fields are missing."""
        payload = {"guild_id": 67890}  # missing channel_id and embed_payload

        response = client.post("/api/v1/discord-message", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 2. PUT /discord-message  (update)
# ===========================================================================


class TestUpdateDiscordMessage:
    """Tests for PUT /api/v1/discord-message."""

    def test_update_discord_message_missing_message_id_returns_400(self, client):
        """Returns 400 when message_id is not provided."""
        payload = {
            "guild_id": 67890,
            "channel_id": 11111,
            "embed_payload": make_embed_payload(),
            # message_id intentionally omitted
        }

        response = client.put("/api/v1/discord-message", json=payload)

        assert response.status_code == 400
        assert "message_id" in response.json()["detail"]

    @patch("api.routers.discord_message.httpx.AsyncClient")
    def test_update_discord_message_gateway_http_error_returns_500(self, mock_httpx_class, client):
        """Returns 500 when gateway returns a non-2xx response."""
        import httpx as real_httpx

        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)

        bad_resp = MagicMock()
        bad_resp.status_code = 503
        bad_resp.raise_for_status.side_effect = real_httpx.HTTPStatusError(
            "Gateway down", request=MagicMock(), response=bad_resp
        )
        mock_http.put = AsyncMock(return_value=bad_resp)

        payload = {
            "guild_id": 67890,
            "channel_id": 11111,
            "message_id": 99999,
            "embed_payload": make_embed_payload(),
        }

        response = client.put("/api/v1/discord-message", json=payload)

        assert response.status_code == 500
        assert "Failed to send update to Discord" in response.json()["detail"]

    def test_update_discord_message_missing_required_fields_returns_422(self, client):
        """Returns 422 when required fields are missing."""
        payload = {"message_id": 99999}  # missing guild_id, channel_id, embed_payload

        response = client.put("/api/v1/discord-message", json=payload)

        assert response.status_code == 422


# ===========================================================================
# 3. GET /discord-message/{message_record_id}
# ===========================================================================


class TestGetDiscordMessage:
    """Tests for GET /api/v1/discord-message/{message_record_id}."""

    def test_get_discord_message_happy_path(self, client, mock_message_repo):
        """Returns 200 with DiscordMessageResponse when record exists."""
        with patch(
            "api.routers.discord_message.DiscordMessageResponse.from_orm",
            return_value=MagicMock(
                id=str(SAMPLE_UUID),
                guild_id=67890,
                channel_id=11111,
                message_id=99999,
                embed_payload="{}",
                message_type="general",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ):
            response = client.get(f"/api/v1/discord-message/{SAMPLE_UUID}")

        assert response.status_code == 200

    def test_get_discord_message_not_found_returns_404(self, client, mock_message_repo):
        """Returns 404 when message record doesn't exist."""
        mock_message_repo.get_by_id = AsyncMock(return_value=None)

        response = client.get(f"/api/v1/discord-message/{SAMPLE_UUID}")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_get_discord_message_invalid_uuid_returns_422(self, client):
        """Returns 422 when message_record_id is not a valid UUID."""
        response = client.get("/api/v1/discord-message/not-a-uuid")

        assert response.status_code == 422

    def test_get_discord_message_server_error_returns_500(self, client, mock_message_repo):
        """Returns 500 when an unexpected exception is raised."""
        mock_message_repo.get_by_id = AsyncMock(side_effect=RuntimeError("DB failure"))

        response = client.get(f"/api/v1/discord-message/{SAMPLE_UUID}")

        assert response.status_code == 500


# ===========================================================================
# 4. GET /discord-message/guild/{guild_id}
# ===========================================================================


class TestListDiscordMessagesByGuild:
    """Tests for GET /api/v1/discord-message/guild/{guild_id}."""

    def test_list_by_guild_happy_path(self, client, mock_message_repo):
        """Returns 200 with list of messages for the guild."""
        with patch(
            "api.routers.discord_message.DiscordMessageResponse.from_orm",
            return_value=MagicMock(
                id=str(SAMPLE_UUID),
                guild_id=67890,
                channel_id=11111,
                message_id=99999,
                embed_payload="{}",
                message_type="general",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ):
            response = client.get("/api/v1/discord-message/guild/67890")

        assert response.status_code == 200

    def test_list_by_guild_empty(self, client, mock_message_repo):
        """Returns 200 with empty list when no messages for guild."""
        mock_message_repo.list_by_guild = AsyncMock(return_value=[])

        response = client.get("/api/v1/discord-message/guild/67890")

        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# 5. GET /discord-message/guild/{guild_id}/channel/{channel_id}
# ===========================================================================


class TestListDiscordMessagesByChannel:
    """Tests for GET /api/v1/discord-message/guild/{guild_id}/channel/{channel_id}."""

    def test_list_by_channel_happy_path(self, client, mock_message_repo):
        """Returns 200 with messages for the guild+channel."""
        with patch(
            "api.routers.discord_message.DiscordMessageResponse.from_orm",
            return_value=MagicMock(
                id=str(SAMPLE_UUID),
                guild_id=67890,
                channel_id=11111,
                message_id=99999,
                embed_payload="{}",
                message_type="general",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ):
            response = client.get("/api/v1/discord-message/guild/67890/channel/11111")

        assert response.status_code == 200

    def test_list_by_channel_empty(self, client, mock_message_repo):
        """Returns 200 with empty list when no messages for channel."""
        mock_message_repo.list_by_guild_and_channel = AsyncMock(return_value=[])

        response = client.get("/api/v1/discord-message/guild/67890/channel/11111")

        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# 6. GET /discord-message/guild/{guild_id}/type/{message_type}
# ===========================================================================


class TestListDiscordMessagesByType:
    """Tests for GET /api/v1/discord-message/guild/{guild_id}/type/{message_type}."""

    def test_list_by_type_happy_path(self, client, mock_message_repo):
        """Returns 200 with messages for the guild+type."""
        with patch(
            "api.routers.discord_message.DiscordMessageResponse.from_orm",
            return_value=MagicMock(
                id=str(SAMPLE_UUID),
                guild_id=67890,
                channel_id=11111,
                message_id=99999,
                embed_payload="{}",
                message_type="shop",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ):
            response = client.get("/api/v1/discord-message/guild/67890/type/shop")

        assert response.status_code == 200

    def test_list_by_type_empty(self, client, mock_message_repo):
        """Returns 200 with empty list when no messages for type."""
        mock_message_repo.list_by_guild_and_type = AsyncMock(return_value=[])

        response = client.get("/api/v1/discord-message/guild/67890/type/shop")

        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# 7. DELETE /discord-message/{message_record_id}
# ===========================================================================


class TestDeleteDiscordMessage:
    """Tests for DELETE /api/v1/discord-message/{message_record_id}."""

    def test_delete_discord_message_happy_path(self, client, mock_message_repo):
        """Returns 200 with status 'deleted' when record exists."""
        response = client.delete(f"/api/v1/discord-message/{SAMPLE_UUID}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"

    def test_delete_discord_message_not_found_returns_404(self, client, mock_message_repo):
        """Returns 404 when message record doesn't exist."""
        mock_message_repo.get_by_id = AsyncMock(return_value=None)

        response = client.delete(f"/api/v1/discord-message/{SAMPLE_UUID}")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_delete_discord_message_invalid_uuid_returns_422(self, client):
        """Returns 422 when message_record_id is not a valid UUID."""
        response = client.delete("/api/v1/discord-message/not-a-uuid")

        assert response.status_code == 422

    def test_delete_discord_message_server_error_returns_500(self, client, mock_message_repo):
        """Returns 500 when an unexpected exception is raised."""
        mock_message_repo.get_by_id = AsyncMock(side_effect=RuntimeError("DB error"))

        response = client.delete(f"/api/v1/discord-message/{SAMPLE_UUID}")

        assert response.status_code == 500


# ===========================================================================
# Additional tests for uncovered branches
# ===========================================================================


class TestCreateDiscordMessageUnexpectedError:
    """Tests for the unexpected-exception branch in create_discord_message (lines 110-115)."""

    @patch("api.routers.discord_message.httpx.AsyncClient")
    def test_create_discord_message_unexpected_exception_returns_500(self, mock_httpx_class, client, mock_message_repo):
        """Returns 500 when an unexpected (non-HTTP) exception occurs after the gateway call.

        This exercises lines 110-115: the generic except block that catches any
        Exception other than httpx.HTTPStatusError and HTTPException.
        """
        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)

        # Gateway returns OK so we proceed past it; then the repo raises
        mock_http.post = AsyncMock(return_value=make_mock_http_response())
        mock_message_repo.create_or_update = AsyncMock(side_effect=RuntimeError("Unexpected DB crash"))

        payload = {
            "guild_id": 67890,
            "channel_id": 11111,
            "embed_payload": make_embed_payload(),
            "message_type": "general",
        }

        response = client.post("/api/v1/discord-message", json=payload)

        assert response.status_code == 500
        assert "Failed to create message" in response.json()["detail"]


class TestUpdateDiscordMessageHappyPath:
    """Tests for the happy-path branches in update_discord_message (lines 156-179)."""

    @patch("api.routers.discord_message.httpx.AsyncClient")
    def test_update_discord_message_happy_path(self, mock_httpx_class, client, mock_message_repo, mock_db_session):
        """Returns 200 with DiscordMessageResponse when update succeeds.

        Covers lines 156-179: the gateway PUT call, response parsing,
        missing-ids check, and the DB persistence block.
        """
        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.put = AsyncMock(return_value=make_mock_http_response())

        with patch(
            "api.routers.discord_message.DiscordMessageResponse.from_orm",
            return_value=MagicMock(
                id=str(SAMPLE_UUID),
                guild_id=67890,
                channel_id=11111,
                message_id=99999,
                embed_payload=json.dumps({"title": "Updated"}),
                message_type="general",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ):
            payload = {
                "guild_id": 67890,
                "channel_id": 11111,
                "message_id": 99999,
                "embed_payload": make_embed_payload(),
                "message_type": "general",
            }
            response = client.put("/api/v1/discord-message", json=payload)

        assert response.status_code == 200

    @patch("api.routers.discord_message.httpx.AsyncClient")
    def test_update_discord_message_gateway_missing_ids_returns_500(self, mock_httpx_class, client):
        """Returns 500 when gateway update response is missing required identifiers.

        Covers lines 159-163: the missing-ids check in update_discord_message.
        """
        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)

        incomplete_resp = make_mock_http_response(json_data={"guild_id": 67890, "channel_id": 11111})
        mock_http.put = AsyncMock(return_value=incomplete_resp)

        payload = {
            "guild_id": 67890,
            "channel_id": 11111,
            "message_id": 99999,
            "embed_payload": make_embed_payload(),
        }
        response = client.put("/api/v1/discord-message", json=payload)

        assert response.status_code == 500
        assert "Discord gateway did not return required message identifiers" in response.json()["detail"]


class TestUpdateDiscordMessageUnexpectedError:
    """Tests for the unexpected-exception branch in update_discord_message (lines 187-194)."""

    @patch("api.routers.discord_message.httpx.AsyncClient")
    def test_update_discord_message_unexpected_exception_returns_500(self, mock_httpx_class, client, mock_message_repo):
        """Returns 500 when an unexpected exception occurs after the gateway PUT call.

        Covers lines 187-194: the generic except block in update_discord_message.
        """
        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)

        # Gateway returns OK, but the repo raises an unexpected error
        mock_http.put = AsyncMock(return_value=make_mock_http_response())
        mock_message_repo.create_or_update = AsyncMock(side_effect=RuntimeError("DB crash on update"))

        payload = {
            "guild_id": 67890,
            "channel_id": 11111,
            "message_id": 99999,
            "embed_payload": make_embed_payload(),
        }
        response = client.put("/api/v1/discord-message", json=payload)

        assert response.status_code == 500
        assert "Failed to update message" in response.json()["detail"]


# ===========================================================================
# 8. GET /discord-message/guild/{guild_id}/type/{message_type}/reference/{reference_id}
# ===========================================================================


class TestGetDiscordMessageByReference:
    """Tests for GET /api/v1/discord-message/guild/{guild_id}/type/{message_type}/reference/{reference_id}."""

    def test_get_by_reference_happy_path(self, client, mock_message_repo):
        """Returns 200 with DiscordMessageResponse when record exists."""
        with patch(
            "api.routers.discord_message.DiscordMessageResponse.from_orm",
            return_value=MagicMock(
                id=str(SAMPLE_UUID),
                guild_id=67890,
                channel_id=11111,
                message_id=99999,
                embed_payload="{}",
                message_type="bounty_announcement",
                reference_id=42,
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ):
            response = client.get("/api/v1/discord-message/guild/67890/type/bounty_announcement/reference/42")

        assert response.status_code == 200

    def test_get_by_reference_not_found_returns_404(self, client, mock_message_repo):
        """Returns 404 when no matching record exists."""
        mock_message_repo.get_by_guild_type_and_reference = AsyncMock(return_value=None)

        response = client.get("/api/v1/discord-message/guild/67890/type/bounty_announcement/reference/999")

        assert response.status_code == 404
        assert "No message found" in response.json()["detail"]

    def test_get_by_reference_server_error_returns_500(self, client, mock_message_repo):
        """Returns 500 when an unexpected exception is raised."""
        mock_message_repo.get_by_guild_type_and_reference = AsyncMock(side_effect=RuntimeError("DB failure"))

        response = client.get("/api/v1/discord-message/guild/67890/type/bounty_announcement/reference/42")

        assert response.status_code == 500
        assert "Failed to look up message record" in response.json()["detail"]


# ===========================================================================
# 9. DELETE /discord-message/guild/{guild_id}/type/{message_type}/reference/{reference_id}
# ===========================================================================


class TestDeleteDiscordMessageByReference:
    """Tests for DELETE /api/v1/discord-message/guild/{guild_id}/type/{message_type}/reference/{reference_id}."""

    def test_delete_by_reference_happy_path(self, client, mock_message_repo):
        """Returns 200 with status dict when record is deleted."""
        response = client.delete("/api/v1/discord-message/guild/67890/type/bounty_announcement/reference/42")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["guild_id"] == 67890
        assert data["message_type"] == "bounty_announcement"
        assert data["reference_id"] == 42

    def test_delete_by_reference_not_found_returns_404(self, client, mock_message_repo):
        """Returns 404 when no matching record exists."""
        mock_message_repo.delete_by_guild_type_and_reference = AsyncMock(return_value=False)

        response = client.delete("/api/v1/discord-message/guild/67890/type/bounty_announcement/reference/999")

        assert response.status_code == 404
        assert "No message found" in response.json()["detail"]

    def test_delete_by_reference_server_error_returns_500(self, client, mock_message_repo):
        """Returns 500 when an unexpected exception is raised."""
        mock_message_repo.delete_by_guild_type_and_reference = AsyncMock(side_effect=RuntimeError("DB error"))

        response = client.delete("/api/v1/discord-message/guild/67890/type/bounty_announcement/reference/42")

        assert response.status_code == 500
        assert "Failed to delete message record" in response.json()["detail"]
