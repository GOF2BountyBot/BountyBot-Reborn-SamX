"""Cogs-level test fixtures and factories for httpx response mocking."""

from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def make_mock_response():
    """Factory fixture for creating consistent mock HTTP response objects.

    Returns a factory function that creates MagicMock objects configured
    with .raise_for_status(), .json(), and .status_code attributes.

    Usage in tests:
        def test_example(self, mock_cog, make_mock_response):
            player_resp = make_mock_response({"id": 1})
            cog.http_client.post = AsyncMock(return_value=player_resp)

            items_resp = make_mock_response([{"name": "item1"}])
            cog.http_client.get = AsyncMock(return_value=items_resp)
    """

    def factory(json_data: Any, status_code: int = 200) -> MagicMock:
        """Create a mock HTTP response.

        Args:
            json_data: Data to be returned by .json() method.
            status_code: HTTP status code (default: 200).

        Returns:
            A MagicMock configured as an httpx response.
        """
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = json_data
        resp.status_code = status_code
        return resp

    return factory
