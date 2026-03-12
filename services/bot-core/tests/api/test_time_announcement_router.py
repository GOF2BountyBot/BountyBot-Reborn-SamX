"""Tests for the announcements/time_announcement API router endpoints.

Import path setup and sqlalchemy_utils mocking are handled by
tests/api/conftest.py which runs before this module is loaded.

The time_announcement router uses request.app.state.db_manager for DB access
and makes outbound HTTP calls via httpx to the Discord Gateway.
Tests mock both of these dependencies, along with the MessageBuilderFactory.

KNOWN PRODUCTION BUG: time_announcement.py line 16 imports EmbedPayloadDict
from `routers.discord_message`, but that class is defined in
`api.schemas.discord_message_schema` and is NOT re-exported from the discord
message router. This means the module cannot be imported under the normal test
path and we must patch the routers.discord_message module namespace before
importing time_announcement. The patch is applied once at module load time
below.
"""

# ---------------------------------------------------------------------------
# Bug workaround: inject EmbedPayloadDict into routers.discord_message so
# that the `from routers.discord_message import ... EmbedPayloadDict` inside
# time_announcement.py succeeds. This is safe to do here because conftest.py
# has already inserted src/ at position 0 on sys.path.
# ---------------------------------------------------------------------------
import importlib as _importlib
import json
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Ensure both api.routers.discord_message and its routers.* alias are loaded
_rdm = _importlib.import_module("api.routers.discord_message")
# Inject into routers.discord_message (the alias the module uses)
if "routers.discord_message" not in sys.modules:
    sys.modules["routers.discord_message"] = _rdm
if "routers" not in sys.modules:
    import api.routers as _ar

    sys.modules["routers"] = _ar

# Import the real EmbedPayloadDict and patch it into the alias
from api.schemas.discord_message_schema import EmbedPayloadDict as _EmbedPayloadDict

if not hasattr(_rdm, "EmbedPayloadDict"):
    _rdm.EmbedPayloadDict = _EmbedPayloadDict
sys.modules["routers.discord_message"].EmbedPayloadDict = _EmbedPayloadDict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_UUID = uuid4()


def make_mock_message(**overrides):
    """Create a mock DiscordMessage ORM object."""
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
    msg = MagicMock()
    for k, v in defaults.items():
        setattr(msg, k, v)
    return msg


def make_gateway_response(**overrides):
    """Standard gateway response payload."""
    defaults = dict(guild_id=12345, channel_id=67890, message_id=11111)
    defaults.update(overrides)
    return defaults


def make_embed_payload_dict():
    """Valid EmbedPayloadDict-compatible payload."""
    return {
        "title": "🕒 Current Time",
        "description": "**Current time:** 12:00 UTC",
        "color": 3447003,
        "fields": [],
        "footer_text": "Time Announcement",
        "footer_icon_url": None,
        "timestamp": None,
        "thumbnail_url": None,
        "image_url": None,
    }


def make_mock_http_response(status_code=200, json_data=None):
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else make_gateway_response()
    resp.raise_for_status = MagicMock()
    return resp


def make_http_error_response(status_code=500):
    """Create an httpx response that raises HTTPStatusError on raise_for_status."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("Server error", request=MagicMock(), response=MagicMock())
    )
    return resp


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
    """Mock DiscordMessageRepository."""
    repo = MagicMock()
    repo.create_or_update = AsyncMock(return_value=make_mock_message())
    repo.get_by_composite_key = AsyncMock(return_value=make_mock_message())
    repo.delete_by_composite_key = AsyncMock(return_value=True)
    repo.list_by_guild = AsyncMock(return_value=[make_mock_message()])
    repo.list_by_guild_and_channel = AsyncMock(return_value=[make_mock_message()])
    repo.list_by_guild_and_type = AsyncMock(return_value=[make_mock_message()])
    return repo


@pytest.fixture
def mock_builder():
    """Mock MessagePayloadBuilder that returns a valid embed payload."""
    builder = MagicMock()
    builder.build_payload = MagicMock(
        return_value={
            "title": "🕒 Current Time",
            "description": "**Current time:** 12:00 UTC",
            "color": 3447003,
            "footer_text": "Time Announcement",
            "timestamp": "2026-01-01T12:00:00+00:00",
        }
    )
    return builder


@pytest.fixture
def test_app(mock_db_manager, mock_message_repo):
    """Build a minimal FastAPI test application with the time_announcement router.

    The router has prefix="/time" and in production is mounted at "/api/v1".
    So routes are accessible at "/api/v1/time/...".
    """
    import api.routers.announcements.time_announcement as ta_module
    from api.routers.announcements.time_announcement import router as time_router

    app = FastAPI()
    app.include_router(time_router, prefix="/api/v1")
    app.state.db_manager = mock_db_manager

    # Replace the module-level repo instance so all route handlers use our mock
    ta_module.discord_message_repo = mock_message_repo

    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ===========================================================================
# 1. POST /announcements/time  (create_time_announcement)
# ===========================================================================


class TestCreateTimeAnnouncement:
    """Tests for POST /api/v1/announcements/time."""

    @patch("api.routers.announcements.time_announcement.MessageBuilderFactory.create_builder")
    @patch("api.routers.announcements.time_announcement.httpx.AsyncClient")
    def test_create_happy_path(
        self, mock_httpx_class, mock_create_builder, client, mock_db_session, mock_message_repo, mock_builder
    ):
        """Returns 201 with DiscordMessageResponse on success."""
        # Wire up the builder mock
        mock_create_builder.return_value = mock_builder

        # Wire up the HTTP client mock
        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=make_mock_http_response())

        # Wire up from_orm to return a serialisable object
        from api.routers.announcements.time_announcement import DiscordMessageResponse

        with patch.object(
            DiscordMessageResponse,
            "from_orm",
            return_value=MagicMock(
                id=str(SAMPLE_UUID),
                guild_id=12345,
                channel_id=67890,
                message_id=11111,
                embed_payload=json.dumps({"title": "test"}),
                message_type="time_announcement",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ):
            payload = {
                "guild_id": 12345,
                "channel_id": 67890,
                "current_time": "12:00 UTC",
            }
            response = client.post("/api/v1/time", json=payload)

        assert response.status_code == 201

    @patch("api.routers.announcements.time_announcement.MessageBuilderFactory.create_builder")
    @patch("api.routers.announcements.time_announcement.httpx.AsyncClient")
    def test_create_gateway_http_error_returns_500(self, mock_httpx_class, mock_create_builder, client, mock_builder):
        """Returns 500 when the Discord gateway returns a non-2xx status."""
        mock_create_builder.return_value = mock_builder

        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=make_http_error_response())

        payload = {"guild_id": 12345, "channel_id": 67890, "current_time": "12:00 UTC"}
        response = client.post("/api/v1/time", json=payload)

        assert response.status_code == 500
        assert "Failed to send message to Discord" in response.json()["detail"]

    @patch("api.routers.announcements.time_announcement.MessageBuilderFactory.create_builder")
    @patch("api.routers.announcements.time_announcement.httpx.AsyncClient")
    def test_create_gateway_missing_ids_returns_500(self, mock_httpx_class, mock_create_builder, client, mock_builder):
        """Returns 500 when gateway response is missing required identifiers."""
        mock_create_builder.return_value = mock_builder

        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)
        # Response missing message_id
        mock_http.post = AsyncMock(
            return_value=make_mock_http_response(json_data={"guild_id": 12345, "channel_id": 67890})
        )

        payload = {"guild_id": 12345, "channel_id": 67890, "current_time": "12:00 UTC"}
        response = client.post("/api/v1/time", json=payload)

        assert response.status_code == 500
        assert "Discord gateway did not return required message identifiers" in response.json()["detail"]

    @patch("api.routers.announcements.time_announcement.MessageBuilderFactory.create_builder")
    def test_create_unexpected_exception_returns_500(self, mock_create_builder, client):
        """Returns 500 when an unexpected exception occurs outside gateway call."""
        mock_create_builder.side_effect = RuntimeError("Builder blew up")

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

    @patch("api.routers.announcements.time_announcement.MessageBuilderFactory.create_builder")
    @patch("api.routers.announcements.time_announcement.httpx.AsyncClient")
    def test_update_happy_path(
        self, mock_httpx_class, mock_create_builder, client, mock_db_session, mock_message_repo, mock_builder
    ):
        """Returns 200 with DiscordMessageResponse on successful update."""
        mock_create_builder.return_value = mock_builder

        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.put = AsyncMock(return_value=make_mock_http_response())

        from api.routers.announcements.time_announcement import DiscordMessageResponse

        with patch.object(
            DiscordMessageResponse,
            "from_orm",
            return_value=MagicMock(
                id=str(SAMPLE_UUID),
                guild_id=12345,
                channel_id=67890,
                message_id=11111,
                embed_payload=json.dumps({"title": "test"}),
                message_type="time_announcement",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ):
            payload = {
                "guild_id": 12345,
                "channel_id": 67890,
                "message_id": 11111,
                "current_time": "14:00 UTC",
            }
            response = client.put("/api/v1/time", json=payload)

        assert response.status_code == 200

    @patch("api.routers.announcements.time_announcement.MessageBuilderFactory.create_builder")
    @patch("api.routers.announcements.time_announcement.httpx.AsyncClient")
    def test_update_gateway_http_error_returns_500(
        self, mock_httpx_class, mock_create_builder, client, mock_message_repo, mock_builder
    ):
        """Returns 500 when the Discord gateway returns a non-2xx status during update."""
        mock_create_builder.return_value = mock_builder

        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.put = AsyncMock(return_value=make_http_error_response())

        payload = {
            "guild_id": 12345,
            "channel_id": 67890,
            "message_id": 11111,
            "current_time": "14:00 UTC",
        }
        response = client.put("/api/v1/time", json=payload)

        assert response.status_code == 500
        assert "Failed to update message in Discord" in response.json()["detail"]

    @patch("api.routers.announcements.time_announcement.MessageBuilderFactory.create_builder")
    @patch("api.routers.announcements.time_announcement.httpx.AsyncClient")
    def test_update_gateway_missing_ids_returns_500(
        self, mock_httpx_class, mock_create_builder, client, mock_message_repo, mock_builder
    ):
        """Returns 500 when gateway update response is missing required identifiers."""
        mock_create_builder.return_value = mock_builder

        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)
        # Missing message_id in response
        mock_http.put = AsyncMock(
            return_value=make_mock_http_response(json_data={"guild_id": 12345, "channel_id": 67890})
        )

        payload = {
            "guild_id": 12345,
            "channel_id": 67890,
            "message_id": 11111,
            "current_time": "14:00 UTC",
        }
        response = client.put("/api/v1/time", json=payload)

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

    @patch("api.routers.announcements.time_announcement.httpx.AsyncClient")
    def test_delete_happy_path(self, mock_httpx_class, client, mock_message_repo):
        """Returns 200 with status 'deleted' and gateway identifiers on success."""
        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.delete = AsyncMock(return_value=make_mock_http_response())

        response = client.delete("/api/v1/time?guild_id=12345&channel_id=67890&message_id=11111")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["guild_id"] == 12345
        assert data["channel_id"] == 67890
        assert data["message_id"] == 11111

    @patch("api.routers.announcements.time_announcement.httpx.AsyncClient")
    def test_delete_gateway_http_error_returns_500(self, mock_httpx_class, client, mock_message_repo):
        """Returns 500 when the Discord gateway returns a non-2xx status during delete."""
        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.delete = AsyncMock(return_value=make_http_error_response())

        response = client.delete("/api/v1/time?guild_id=12345&channel_id=67890&message_id=11111")

        assert response.status_code == 500
        assert "Failed to delete message from Discord" in response.json()["detail"]

    @patch("api.routers.announcements.time_announcement.httpx.AsyncClient")
    def test_delete_gateway_missing_ids_returns_500(self, mock_httpx_class, client, mock_message_repo):
        """Returns 500 when gateway delete response is missing required identifiers."""
        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.delete = AsyncMock(return_value=make_mock_http_response(json_data={"guild_id": 12345}))

        response = client.delete("/api/v1/time?guild_id=12345&channel_id=67890&message_id=11111")

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

    @patch("api.routers.announcements.time_announcement.httpx.AsyncClient")
    def test_delete_db_record_not_found_after_gateway_logs_warning(self, mock_httpx_class, client, mock_message_repo):
        """Returns 200 even when delete_by_composite_key returns falsy (DB record missing).

        Covers line 267: flogger.warning('Discord message deleted but database record not found').
        This path is reached when the gateway delete succeeds but the DB repo returns
        a falsy value from delete_by_composite_key (e.g., record was already gone).
        """
        mock_http = AsyncMock()
        mock_httpx_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_httpx_class.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.delete = AsyncMock(return_value=make_mock_http_response())

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
        """Returns 200 with DiscordMessageResponse when announcement exists."""
        from api.routers.announcements.time_announcement import DiscordMessageResponse

        with patch.object(
            DiscordMessageResponse,
            "from_orm",
            return_value=MagicMock(
                id=str(SAMPLE_UUID),
                guild_id=12345,
                channel_id=67890,
                message_id=11111,
                embed_payload=json.dumps({"title": "test"}),
                message_type="time_announcement",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ):
            response = client.get("/api/v1/time?guild_id=12345&channel_id=67890&message_id=11111")

        assert response.status_code == 200

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
        """Returns 200 with list of announcements for the given guild."""
        from api.routers.announcements.time_announcement import DiscordMessageResponse

        with patch.object(
            DiscordMessageResponse,
            "from_orm",
            return_value=MagicMock(
                id=str(SAMPLE_UUID),
                guild_id=12345,
                channel_id=67890,
                message_id=11111,
                embed_payload="{}",
                message_type="time_announcement",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ):
            response = client.get("/api/v1/time/guild/12345")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

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
        """Returns 200 with list of announcements for the given guild+channel."""
        from api.routers.announcements.time_announcement import DiscordMessageResponse

        with patch.object(
            DiscordMessageResponse,
            "from_orm",
            return_value=MagicMock(
                id=str(SAMPLE_UUID),
                guild_id=12345,
                channel_id=67890,
                message_id=11111,
                embed_payload="{}",
                message_type="time_announcement",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ):
            response = client.get("/api/v1/time/guild/12345/channel/67890")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

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
        """Returns 200 with list of announcements for the given guild+type."""
        from api.routers.announcements.time_announcement import DiscordMessageResponse

        with patch.object(
            DiscordMessageResponse,
            "from_orm",
            return_value=MagicMock(
                id=str(SAMPLE_UUID),
                guild_id=12345,
                channel_id=67890,
                message_id=11111,
                embed_payload="{}",
                message_type="time_announcement",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ):
            response = client.get("/api/v1/time/guild/12345/type/time_announcement")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_by_type_empty(self, client, mock_message_repo):
        """Returns 200 with empty list when no announcements match the type."""
        mock_message_repo.list_by_guild_and_type = AsyncMock(return_value=[])

        response = client.get("/api/v1/time/guild/12345/type/time_announcement")

        assert response.status_code == 200
        assert response.json() == []
