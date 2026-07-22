"""Tests for the announcements/time_announcement API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.

The time_announcement router uses request.app.state.db_manager for DB access
and makes outbound HTTP calls via httpx to the Discord Gateway. The gateway is
mocked at the httpx transport layer with ``respx`` so the exact route/method/
payload the router emits is asserted. The REAL ``MessageBuilderFactory`` /
``TimeAnnouncementBuilder`` build the embed payload (no builder mock), and
response bodies flow through the REAL ``DiscordMessageResponse`` pydantic model
built from REAL ``DiscordMessage`` ORM rows.

Note: the router imports ``EmbedPayloadDict`` directly from
``api.schemas.discord_message_schema`` (see time_announcement.py), so the old
``routers.discord_message`` re-export workaround this module used to carry has
been removed — it no longer reflects the source.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import api.routers.announcements.time_announcement as ta_module
import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from persist.models.discord_message import DiscordMessage

# Exact gateway endpoint the router POSTs/PUTs/DELETEs to.
GATEWAY_MESSAGES_URL = f"{ta_module.DISCORD_GATEWAY_BASE_URL}/messages"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_UUID = uuid4()


def make_discord_message(**overrides):
    """Build a REAL ``DiscordMessage`` ORM instance (transient, no DB).

    Using the real model means ``DiscordMessageResponse.from_orm`` runs
    unpatched and column drift surfaces in the serialized body.
    """
    defaults = dict(
        id=SAMPLE_UUID,
        guild_id=12345,
        channel_id=67890,
        message_id=11111,
        embed_payload=json.dumps(
            {
                "title": "🕒 Current Time",
                "description": "**Current time:** 12:00 UTC",
                "color": 3447003,
            }
        ),
        message_type="time_announcement",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        updated_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    defaults.update(overrides)
    return DiscordMessage(**defaults)


def make_gateway_response(**overrides):
    """Standard gateway response payload."""
    defaults = dict(guild_id=12345, channel_id=67890, message_id=11111)
    defaults.update(overrides)
    return defaults


# The DELETE handler calls ``client.delete(url, json=gateway_request, ...)`` but
# httpx's ``AsyncClient.delete()`` does not accept a ``json`` (or any body) kwarg
# (httpx 0.28.1). Every delete that reaches the gateway therefore raises
# ``TypeError`` and 500s in production. The old blanket ``httpx.AsyncClient``
# MagicMock hid this (its ``.delete`` accepted anything). These tests are the
# *truer* tests and are expected to fail until the src bug is fixed (see
# FOLLOWUPS.md — R-bc-api). Do NOT paper over with a 500 assertion.
_DELETE_JSON_BUG = (
    "PROD BUG: time_announcement.delete_time_announcement calls "
    "httpx.AsyncClient.delete(url, json=...), which httpx 0.28.1 rejects with "
    "TypeError; the gateway delete never fires. See FOLLOWUPS.md (R-bc-api)."
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db_session():
    """Mock async DB session."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture
def mock_db_manager(mock_db_session):
    """Mock db_manager that provides an async context manager session."""
    manager = MagicMock()
    manager.get_session = MagicMock()
    manager.get_session.return_value.__aenter__ = AsyncMock(return_value=mock_db_session)
    manager.get_session.return_value.__aexit__ = AsyncMock(return_value=False)
    return manager


@pytest.fixture
def mock_message_repo():
    """Mock DiscordMessageRepository — returns REAL DiscordMessage ORM rows."""
    repo = MagicMock()
    repo.create_or_update = AsyncMock(return_value=make_discord_message())
    repo.get_by_composite_key = AsyncMock(return_value=make_discord_message())
    repo.delete_by_composite_key = AsyncMock(return_value=True)
    repo.list_by_guild = AsyncMock(return_value=[make_discord_message()])
    repo.list_by_guild_and_channel = AsyncMock(return_value=[make_discord_message()])
    repo.list_by_guild_and_type = AsyncMock(return_value=[make_discord_message()])
    return repo


@pytest.fixture
def test_app(mock_db_manager, mock_message_repo):
    """Build a minimal FastAPI test application with the time_announcement router.

    The router has prefix="/time" and in production is mounted at "/api/v1".
    So routes are accessible at "/api/v1/time/...".
    """
    app = FastAPI()
    app.include_router(ta_module.router, prefix="/api/v1")
    app.state.db_manager = mock_db_manager

    # Replace the module-level repo instance so all route handlers use our mock
    ta_module.discord_message_repo = mock_message_repo

    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


def _assert_message_body(data):
    """Assert a serialized DiscordMessageResponse body matches the seeded row."""
    assert data["id"] == str(SAMPLE_UUID)
    assert data["guild_id"] == 12345
    assert data["channel_id"] == 67890
    assert data["message_id"] == 11111
    assert data["message_type"] == "time_announcement"
    assert isinstance(data["embed_payload"], str)


# ===========================================================================
# 1. POST /announcements/time  (create_time_announcement)
# ===========================================================================


class TestCreateTimeAnnouncement:
    """Tests for POST /api/v1/announcements/time."""

    @respx.mock
    def test_create_happy_path(self, client, mock_db_session, mock_message_repo):
        """Returns 201 with a real DiscordMessageResponse; asserts the gateway
        route/method/payload and the serialized body. Uses the real builder."""
        route = respx.post(GATEWAY_MESSAGES_URL).mock(return_value=httpx.Response(200, json=make_gateway_response()))

        payload = {"guild_id": 12345, "channel_id": 67890, "current_time": "12:00 UTC"}
        response = client.post("/api/v1/time", json=payload)

        assert response.status_code == 201
        assert route.called
        sent = json.loads(route.calls.last.request.content)
        assert sent["guild_id"] == 12345
        assert sent["channel_id"] == 67890
        # The real TimeAnnouncementBuilder embeds the current_time in the description.
        assert "12:00 UTC" in sent["content"]["description"]
        assert sent["content"]["title"] == "🕒 Current Time"
        _assert_message_body(response.json())

    @respx.mock
    def test_create_gateway_http_error_returns_500(self, client):
        """Returns 500 when the Discord gateway returns a non-2xx status."""
        route = respx.post(GATEWAY_MESSAGES_URL).mock(return_value=httpx.Response(500, json={"error": "boom"}))

        payload = {"guild_id": 12345, "channel_id": 67890, "current_time": "12:00 UTC"}
        response = client.post("/api/v1/time", json=payload)

        assert route.called
        assert response.status_code == 500
        assert "Failed to send message to Discord" in response.json()["detail"]

    @respx.mock
    def test_create_gateway_missing_ids_returns_500(self, client):
        """Returns 500 when gateway response is missing required identifiers."""
        route = respx.post(GATEWAY_MESSAGES_URL).mock(
            return_value=httpx.Response(200, json={"guild_id": 12345, "channel_id": 67890})
        )

        payload = {"guild_id": 12345, "channel_id": 67890, "current_time": "12:00 UTC"}
        response = client.post("/api/v1/time", json=payload)

        assert route.called
        assert response.status_code == 500
        assert "Discord gateway did not return required message identifiers" in response.json()["detail"]

    def test_create_unexpected_exception_returns_500(self, client):
        """Returns 500 when an unexpected exception occurs outside the gateway call.

        The builder factory is forced to raise to exercise the generic
        except-block; this is a targeted internal-failure injection, not a
        stand-in for the builder itself.
        """
        with patch.object(
            ta_module.MessageBuilderFactory, "create_builder", side_effect=RuntimeError("Builder blew up")
        ):
            payload = {"guild_id": 12345, "channel_id": 67890, "current_time": "12:00 UTC"}
            response = client.post("/api/v1/time", json=payload)

        assert response.status_code == 500
        assert "Failed to create time announcement" in response.json()["detail"]

    def test_create_missing_required_fields_returns_422(self, client):
        """Returns 422 when required fields (current_time) are missing."""
        payload = {"guild_id": 12345, "channel_id": 67890}  # missing current_time
        response = client.post("/api/v1/time", json=payload)
        assert response.status_code == 422

    def test_create_missing_guild_id_returns_422(self, client):
        """Returns 422 when guild_id is missing."""
        payload = {"channel_id": 67890, "current_time": "12:00 UTC"}
        response = client.post("/api/v1/time", json=payload)
        assert response.status_code == 422

    def test_create_missing_channel_id_returns_422(self, client):
        """Returns 422 when channel_id is missing."""
        payload = {"guild_id": 12345, "current_time": "12:00 UTC"}
        response = client.post("/api/v1/time", json=payload)
        assert response.status_code == 422


# ===========================================================================
# 2. PUT /announcements/time  (update_time_announcement)
# ===========================================================================


class TestUpdateTimeAnnouncement:
    """Tests for PUT /api/v1/announcements/time."""

    def test_update_missing_message_id_returns_400(self, client):
        """Returns 400 when message_id is not provided for update."""
        payload = {
            "guild_id": 12345,
            "channel_id": 67890,
            "current_time": "13:00 UTC",
            # message_id intentionally omitted
        }
        response = client.put("/api/v1/time", json=payload)
        assert response.status_code == 400
        assert "message_id is required for update operations" in response.json()["detail"]

    def test_update_not_found_returns_404(self, client, mock_message_repo):
        """Returns 404 when existing announcement cannot be found."""
        mock_message_repo.get_by_composite_key = AsyncMock(return_value=None)

        payload = {
            "guild_id": 12345,
            "channel_id": 67890,
            "message_id": 11111,
            "current_time": "13:00 UTC",
        }
        response = client.put("/api/v1/time", json=payload)
        assert response.status_code == 404
        assert "No existing time announcement found to update" in response.json()["detail"]

    @respx.mock
    def test_update_happy_path(self, client, mock_db_session, mock_message_repo):
        """Returns 200 with a real DiscordMessageResponse on successful update;
        asserts the gateway PUT route/method/payload."""
        route = respx.put(GATEWAY_MESSAGES_URL).mock(return_value=httpx.Response(200, json=make_gateway_response()))

        payload = {
            "guild_id": 12345,
            "channel_id": 67890,
            "message_id": 11111,
            "current_time": "14:00 UTC",
        }
        response = client.put("/api/v1/time", json=payload)

        assert response.status_code == 200
        assert route.called
        sent = json.loads(route.calls.last.request.content)
        # The router forwards the EXISTING record's ids to the gateway.
        assert sent["guild_id"] == 12345
        assert sent["channel_id"] == 67890
        assert sent["message_id"] == 11111
        assert "14:00 UTC" in sent["content"]["description"]
        _assert_message_body(response.json())

    @respx.mock
    def test_update_gateway_http_error_returns_500(self, client, mock_message_repo):
        """Returns 500 when the Discord gateway returns a non-2xx status during update."""
        route = respx.put(GATEWAY_MESSAGES_URL).mock(return_value=httpx.Response(500, json={"error": "boom"}))

        payload = {
            "guild_id": 12345,
            "channel_id": 67890,
            "message_id": 11111,
            "current_time": "14:00 UTC",
        }
        response = client.put("/api/v1/time", json=payload)

        assert route.called
        assert response.status_code == 500
        assert "Failed to update message in Discord" in response.json()["detail"]

    @respx.mock
    def test_update_gateway_missing_ids_returns_500(self, client, mock_message_repo):
        """Returns 500 when gateway update response is missing required identifiers."""
        route = respx.put(GATEWAY_MESSAGES_URL).mock(
            return_value=httpx.Response(200, json={"guild_id": 12345, "channel_id": 67890})
        )

        payload = {
            "guild_id": 12345,
            "channel_id": 67890,
            "message_id": 11111,
            "current_time": "14:00 UTC",
        }
        response = client.put("/api/v1/time", json=payload)

        assert route.called
        assert response.status_code == 500
        assert "Discord gateway did not return required message identifiers" in response.json()["detail"]

    def test_update_unexpected_exception_returns_500(self, client, mock_message_repo):
        """Returns 500 when an unexpected (non-HTTP) exception occurs during update."""
        mock_message_repo.get_by_composite_key = AsyncMock(side_effect=RuntimeError("DB crashed"))

        payload = {
            "guild_id": 12345,
            "channel_id": 67890,
            "message_id": 11111,
            "current_time": "14:00 UTC",
        }
        response = client.put("/api/v1/time", json=payload)

        assert response.status_code == 500
        assert "Failed to update time announcement" in response.json()["detail"]


# ===========================================================================
# 3. DELETE /announcements/time  (delete_time_announcement)
# ===========================================================================


class TestDeleteTimeAnnouncement:
    """Tests for DELETE /api/v1/announcements/time."""

    def test_delete_not_found_returns_404(self, client, mock_message_repo):
        """Returns 404 when announcement to delete is not found."""
        mock_message_repo.get_by_composite_key = AsyncMock(return_value=None)

        response = client.delete("/api/v1/time?guild_id=12345&channel_id=67890&message_id=11111")
        assert response.status_code == 404
        assert "No time announcement found to delete" in response.json()["detail"]

    @pytest.mark.xfail(reason=_DELETE_JSON_BUG, strict=True)
    @respx.mock(assert_all_called=False)
    def test_delete_happy_path(self, client, mock_message_repo):
        """Returns 200 with status 'deleted' and gateway identifiers on success;
        asserts the gateway DELETE route/method/payload."""
        route = respx.delete(GATEWAY_MESSAGES_URL).mock(return_value=httpx.Response(200, json=make_gateway_response()))

        response = client.delete("/api/v1/time?guild_id=12345&channel_id=67890&message_id=11111")

        assert response.status_code == 200
        assert route.called
        sent = json.loads(route.calls.last.request.content)
        assert sent == {"guild_id": 12345, "channel_id": 67890, "message_id": 11111}
        data = response.json()
        assert data["status"] == "deleted"
        assert data["guild_id"] == 12345
        assert data["channel_id"] == 67890
        assert data["message_id"] == 11111

    @pytest.mark.xfail(reason=_DELETE_JSON_BUG, strict=True)
    @respx.mock(assert_all_called=False)
    def test_delete_gateway_http_error_returns_500(self, client, mock_message_repo):
        """Returns 500 when the Discord gateway returns a non-2xx status during delete."""
        route = respx.delete(GATEWAY_MESSAGES_URL).mock(return_value=httpx.Response(500, json={"error": "boom"}))

        response = client.delete("/api/v1/time?guild_id=12345&channel_id=67890&message_id=11111")

        assert route.called
        assert response.status_code == 500
        assert "Failed to delete message from Discord" in response.json()["detail"]

    @pytest.mark.xfail(reason=_DELETE_JSON_BUG, strict=True)
    @respx.mock(assert_all_called=False)
    def test_delete_gateway_missing_ids_returns_500(self, client, mock_message_repo):
        """Returns 500 when gateway delete response is missing required identifiers."""
        route = respx.delete(GATEWAY_MESSAGES_URL).mock(return_value=httpx.Response(200, json={"guild_id": 12345}))

        response = client.delete("/api/v1/time?guild_id=12345&channel_id=67890&message_id=11111")

        assert route.called
        assert response.status_code == 500
        assert "Discord gateway did not return required message identifiers" in response.json()["detail"]

    def test_delete_unexpected_exception_returns_500(self, client, mock_message_repo):
        """Returns 500 when an unexpected exception occurs during delete."""
        mock_message_repo.get_by_composite_key = AsyncMock(side_effect=RuntimeError("DB exploded"))

        response = client.delete("/api/v1/time?guild_id=12345&channel_id=67890&message_id=11111")

        assert response.status_code == 500
        assert "Failed to delete time announcement" in response.json()["detail"]

    def test_delete_missing_query_params_returns_422(self, client):
        """Returns 422 when required query parameters are missing."""
        response = client.delete("/api/v1/time?guild_id=12345")
        assert response.status_code == 422

    @pytest.mark.xfail(reason=_DELETE_JSON_BUG, strict=True)
    @respx.mock(assert_all_called=False)
    def test_delete_db_record_not_found_after_gateway_logs_warning(self, client, mock_message_repo):
        """Returns 200 even when delete_by_composite_key returns falsy (DB record missing).

        Covers the flogger.warning('Discord message deleted but database record not found')
        path: the gateway delete succeeds but the DB repo returns a falsy value from
        delete_by_composite_key (e.g., record was already gone).
        """
        respx.delete(GATEWAY_MESSAGES_URL).mock(return_value=httpx.Response(200, json=make_gateway_response()))

        # delete_by_composite_key returns False → record not found → warning logged
        mock_message_repo.delete_by_composite_key = AsyncMock(return_value=False)

        response = client.delete("/api/v1/time?guild_id=12345&channel_id=67890&message_id=11111")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"


# ===========================================================================
# 4. GET /announcements/time  (get_time_announcement)
# ===========================================================================


class TestGetTimeAnnouncement:
    """Tests for GET /api/v1/announcements/time (single, by composite key)."""

    def test_get_not_found_returns_404(self, client, mock_message_repo):
        """Returns 404 when the announcement doesn't exist in the database."""
        mock_message_repo.get_by_composite_key = AsyncMock(return_value=None)

        response = client.get("/api/v1/time?guild_id=12345&channel_id=67890&message_id=11111")

        assert response.status_code == 404
        assert "No time announcement found" in response.json()["detail"]

    def test_get_happy_path(self, client, mock_message_repo):
        """Returns 200 with a real DiscordMessageResponse when announcement exists."""
        response = client.get("/api/v1/time?guild_id=12345&channel_id=67890&message_id=11111")

        assert response.status_code == 200
        _assert_message_body(response.json())

    def test_get_unexpected_exception_returns_500(self, client, mock_message_repo):
        """Returns 500 when an unexpected exception occurs during get."""
        mock_message_repo.get_by_composite_key = AsyncMock(side_effect=RuntimeError("DB gone"))

        response = client.get("/api/v1/time?guild_id=12345&channel_id=67890&message_id=11111")

        assert response.status_code == 500
        assert "Failed to get time announcement" in response.json()["detail"]

    def test_get_missing_query_params_returns_422(self, client):
        """Returns 422 when required query parameters are missing."""
        response = client.get("/api/v1/time?guild_id=12345")
        assert response.status_code == 422


# ===========================================================================
# 5. GET /announcements/time/guild/{guild_id}
# ===========================================================================


class TestListTimeAnnouncementsByGuild:
    """Tests for GET /api/v1/announcements/time/guild/{guild_id}."""

    def test_list_by_guild_happy_path(self, client, mock_message_repo):
        """Returns 200 with a list of real serialized announcements for the guild."""
        response = client.get("/api/v1/time/guild/12345")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list) and len(data) == 1
        _assert_message_body(data[0])

    def test_list_by_guild_empty(self, client, mock_message_repo):
        """Returns 200 with empty list when guild has no announcements."""
        mock_message_repo.list_by_guild = AsyncMock(return_value=[])

        response = client.get("/api/v1/time/guild/99999")

        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# 6. GET /announcements/time/guild/{guild_id}/channel/{channel_id}
# ===========================================================================


class TestListTimeAnnouncementsByChannel:
    """Tests for GET /api/v1/announcements/time/guild/{guild_id}/channel/{channel_id}."""

    def test_list_by_channel_happy_path(self, client, mock_message_repo):
        """Returns 200 with a list of real serialized announcements for guild+channel."""
        response = client.get("/api/v1/time/guild/12345/channel/67890")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list) and len(data) == 1
        _assert_message_body(data[0])

    def test_list_by_channel_empty(self, client, mock_message_repo):
        """Returns 200 with empty list when no announcements for channel."""
        mock_message_repo.list_by_guild_and_channel = AsyncMock(return_value=[])

        response = client.get("/api/v1/time/guild/12345/channel/67890")

        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# 7. GET /announcements/time/guild/{guild_id}/type/{message_type}
# ===========================================================================


class TestListTimeAnnouncementsByType:
    """Tests for GET /api/v1/announcements/time/guild/{guild_id}/type/{message_type}."""

    def test_list_by_type_happy_path(self, client, mock_message_repo):
        """Returns 200 with a list of real serialized announcements for guild+type."""
        response = client.get("/api/v1/time/guild/12345/type/time_announcement")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list) and len(data) == 1
        _assert_message_body(data[0])

    def test_list_by_type_empty(self, client, mock_message_repo):
        """Returns 200 with empty list when no announcements match the type."""
        mock_message_repo.list_by_guild_and_type = AsyncMock(return_value=[])

        response = client.get("/api/v1/time/guild/12345/type/time_announcement")

        assert response.status_code == 200
        assert response.json() == []
