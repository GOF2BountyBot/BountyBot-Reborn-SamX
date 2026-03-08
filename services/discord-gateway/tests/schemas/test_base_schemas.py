"""Tests for base Pydantic schemas used across the Discord Gateway API."""
import pytest
from datetime import datetime
from pydantic import ValidationError
from api.schemas.base_schemas import (
    BaseResponse,
    PaginatedResponse,
    SuccessResponse,
    DeleteResponse,
)


class TestBaseResponse:
    def test_base_response_required_fields(self):
        """BaseResponse requires a `status` field."""
        resp = BaseResponse(status="ok")
        assert resp.status == "ok"

        # Omitting status should raise a validation error
        with pytest.raises(ValidationError):
            BaseResponse()  # type: ignore[call-arg]

    def test_base_response_default_timestamp(self):
        """BaseResponse should auto-populate a timestamp when not provided."""
        before = datetime.utcnow()
        resp = BaseResponse(status="ok")
        after = datetime.utcnow()

        assert resp.timestamp is not None
        assert isinstance(resp.timestamp, datetime)
        assert before <= resp.timestamp <= after

    def test_base_response_optional_message(self):
        """message defaults to None and can be set."""
        resp = BaseResponse(status="ok")
        assert resp.message is None

        resp_with_msg = BaseResponse(status="ok", message="hello")
        assert resp_with_msg.message == "hello"


class TestPaginatedResponse:
    def test_paginated_response_optional_fields(self):
        """PaginatedResponse pagination fields should default to None."""
        resp = PaginatedResponse(status="ok")
        assert resp.total_count is None
        assert resp.page is None
        assert resp.page_size is None
        assert resp.has_more is None

    def test_paginated_response_with_values(self):
        """PaginatedResponse should accept explicit pagination values."""
        resp = PaginatedResponse(
            status="ok", total_count=100, page=2, page_size=10, has_more=True
        )
        assert resp.total_count == 100
        assert resp.page == 2
        assert resp.page_size == 10
        assert resp.has_more is True


class TestSuccessResponse:
    def test_success_response_requires_message(self):
        """SuccessResponse must have a message."""
        resp = SuccessResponse(status="ok", message="done")
        assert resp.message == "done"

        with pytest.raises(ValidationError):
            SuccessResponse(status="ok")  # type: ignore[call-arg]


class TestDeleteResponse:
    def test_delete_response_defaults(self):
        """deleted should default to True; message is required."""
        resp = DeleteResponse(status="ok", message="removed")
        assert resp.deleted is True
        assert resp.message == "removed"

    def test_delete_response_explicit_deleted(self):
        """deleted can be overridden to False."""
        resp = DeleteResponse(status="ok", deleted=False, message="not removed")
        assert resp.deleted is False
