"""Tests for thread Pydantic schemas."""

import pytest
from api.schemas.channel_schemas import (
    Thread,
    ThreadCreateRequest,
    ThreadListResponse,
    ThreadResponse,
    ThreadUpdateRequest,
)
from pydantic import ValidationError

VALID_THREAD = {
    "id": 1,
    "name": "Test Thread",
    "channel_id": 10,
    "owner_id": 20,
    "archived": False,
    "locked": False,
    "created_at": "2021-01-01T00:00:00Z",
}


class TestThread:
    def test_valid_instantiation(self):
        thread = Thread(**VALID_THREAD)
        assert thread.id == 1
        assert thread.name == "Test Thread"

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            Thread()  # missing all fields


class TestThreadCreateRequest:
    def test_valid_instantiation_defaults(self):
        req = ThreadCreateRequest(name="New Thread")
        assert req.name == "New Thread"
        assert req.type == "public_thread"

    def test_valid_with_all_fields(self):
        req = ThreadCreateRequest(name="T", auto_archive_duration=60, type="private_thread")
        assert req.auto_archive_duration == 60
        assert req.type == "private_thread"

    def test_missing_name(self):
        with pytest.raises(ValidationError):
            ThreadCreateRequest()

    def test_invalid_auto_archive_duration_type(self):
        with pytest.raises(ValidationError):
            ThreadCreateRequest(name="T", auto_archive_duration="sixty")

    def test_invalid_type_type(self):
        with pytest.raises(ValidationError):
            ThreadCreateRequest(name="T", type=123)


class TestThreadUpdateRequest:
    def test_valid_instantiation(self):
        req = ThreadUpdateRequest(name="X", archived=True, locked=True)
        assert req.name == "X"
        assert req.archived is True

    def test_empty_instantiation(self):
        req = ThreadUpdateRequest()
        assert req.name is None
        assert req.archived is None
        assert req.locked is None


class TestThreadResponse:
    def test_serialization(self):
        resp = ThreadResponse(status="ok", data=VALID_THREAD)
        result = resp.model_dump()
        assert result["status"] == "ok"
        assert result["data"]["id"] == 1
        assert result["data"]["name"] == "Test Thread"


class TestThreadListResponse:
    def test_list_response_defaults(self):
        resp = ThreadListResponse(status="ok", data=[VALID_THREAD])
        assert resp.status == "ok"
        assert isinstance(resp.data, list)
        assert len(resp.data) == 1
        assert resp.total_count is None
        assert resp.page is None
        assert resp.page_size is None
        assert resp.has_more is None

    def test_list_response_with_pagination(self):
        resp = ThreadListResponse(
            status="ok",
            data=[VALID_THREAD, VALID_THREAD],
            total_count=2,
            page=1,
            page_size=10,
            has_more=False,
        )
        assert resp.total_count == 2
        assert resp.page == 1
        assert resp.page_size == 10
        assert resp.has_more is False
        assert len(resp.data) == 2
