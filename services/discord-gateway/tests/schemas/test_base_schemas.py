"""Tests for base Pydantic schemas used across the Discord Gateway API."""
from datetime import UTC, datetime

import pytest
from api.schemas.base_schemas import (
    BaseResponse,
    DeleteResponse,
    PaginatedResponse,
    SuccessResponse,
    create_resource_list_response,
    create_resource_response,
)
from pydantic import BaseModel, ValidationError


class MockResourceModel(BaseModel):
    """Mock resource model for testing factory functions."""
    id: int
    name: str


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
        before = datetime.now(UTC)
        resp = BaseResponse(status="ok")
        after = datetime.now(UTC)

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


class TestCreateResourceResponse:
    """Tests for the create_resource_response factory function."""

    def test_create_resource_response_creates_class(self):
        """Factory should create a response class with correct name."""
        ResponseClass = create_resource_response("ship", MockResourceModel)
        assert ResponseClass.__name__ == "ShipResponse"

    def test_create_resource_response_inherits_base(self):
        """Factory-created class should inherit from BaseResponse."""
        ResponseClass = create_resource_response("ship", MockResourceModel)
        assert issubclass(ResponseClass, BaseResponse)

    def test_create_resource_response_has_data_field(self):
        """Factory-created class should have a data field."""
        ResponseClass = create_resource_response("ship", MockResourceModel)
        assert "data" in ResponseClass.model_fields

    def test_create_resource_response_validates_data(self):
        """Factory-created class should validate data against the resource model."""
        ResponseClass = create_resource_response("ship", MockResourceModel)
        resource_data = MockResourceModel(id=1, name="Millennium Falcon")
        resp = ResponseClass(status="ok", data=resource_data)
        assert resp.data.id == 1
        assert resp.data.name == "Millennium Falcon"

    def test_create_resource_response_has_timestamp(self):
        """Factory-created class should inherit timestamp from BaseResponse."""
        ResponseClass = create_resource_response("ship", MockResourceModel)
        resp = ResponseClass(status="ok", data=MockResourceModel(id=1, name="Test"))
        assert resp.timestamp is not None


class TestCreateResourceListResponse:
    """Tests for the create_resource_list_response factory function."""

    def test_create_resource_list_response_creates_class(self):
        """Factory should create a list response class with correct name."""
        ListResponseClass = create_resource_list_response("ship", MockResourceModel)
        assert ListResponseClass.__name__ == "ShipListResponse"

    def test_create_resource_list_response_inherits_paginated(self):
        """Factory-created class should inherit from PaginatedResponse."""
        ListResponseClass = create_resource_list_response("ship", MockResourceModel)
        assert issubclass(ListResponseClass, PaginatedResponse)

    def test_create_resource_list_response_has_data_field(self):
        """Factory-created class should have a data field."""
        ListResponseClass = create_resource_list_response("ship", MockResourceModel)
        assert "data" in ListResponseClass.model_fields

    def test_create_resource_list_response_validates_data_as_list(self):
        """Factory-created class should validate data as a list of resources."""
        ListResponseClass = create_resource_list_response("ship", MockResourceModel)
        ships = [MockResourceModel(id=1, name="Falcon"), MockResourceModel(id=2, name="X-Wing")]
        resp = ListResponseClass(
            status="ok",
            data=ships,
            total_count=2,
            page=1,
            page_size=10,
            has_more=False
        )
        assert len(resp.data) == 2
        assert resp.data[0].name == "Falcon"

    def test_create_resource_list_response_has_pagination_fields(self):
        """Factory-created class should have pagination fields from PaginatedResponse."""
        ListResponseClass = create_resource_list_response("ship", MockResourceModel)
        resp = ListResponseClass(
            status="ok",
            data=[],
            total_count=100,
            page=2,
            page_size=10,
            has_more=True
        )
        assert resp.total_count == 100
        assert resp.page == 2
        assert resp.page_size == 10
        assert resp.has_more is True
