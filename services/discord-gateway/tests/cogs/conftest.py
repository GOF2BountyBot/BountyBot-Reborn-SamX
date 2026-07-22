"""Cogs-level test fixtures and factories for httpx response mocking."""

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest


@pytest.fixture(scope="module")
def make_mock_response():
    """Factory fixture for creating **real** ``httpx.Response`` objects.

    Returns a factory that builds a genuine ``httpx.Response`` with a real
    ``httpx.Request`` attached.  Because it is a real response:

    - ``.json()`` deserializes the encoded body (identical to the wire path).
    - ``.raise_for_status()`` genuinely raises ``httpx.HTTPStatusError`` for any
      4xx/5xx status — an accept-anything stub can no longer green-light a bug
      that only surfaces when the real client raises on an error status.
    - ``.status_code``, ``.text``, ``.content`` all behave like production.

    The call signature ``factory(json_data, status_code=200)`` is unchanged.

    Usage in tests:
        def test_example(self, mock_cog, make_mock_response):
            player_resp = make_mock_response({"id": 1})
            cog.http_client.post = AsyncMock(return_value=player_resp)

            items_resp = make_mock_response([{"name": "item1"}])
            cog.http_client.get = AsyncMock(return_value=items_resp)
    """

    def factory(json_data: Any, status_code: int = 200) -> httpx.Response:
        """Create a real httpx.Response.

        Args:
            json_data: Data serialized into the JSON body (returned by .json()).
            status_code: HTTP status code (default: 200).

        Returns:
            A real ``httpx.Response`` with a real ``httpx.Request`` attached so
            ``raise_for_status()`` works and raises on status >= 400.
        """
        request = httpx.Request("GET", "http://bot-core.test/api/v1/resource")
        return httpx.Response(status_code, json=json_data, request=request)

    return factory


@pytest.fixture(autouse=True)
def _block_background_tasks():
    """Prevent cog __init__ preload tasks from blocking the event loop.

    Five cogs (AboutCog, AdminCog, BountyCog, DevCog, SkinsCog) call
    bot.loop.create_task(_preload_data()) in __init__. Those preload functions
    contain asyncio.sleep([5,10,20,40,60]s) retry chains. When multiple test
    files run in the same pytest session these tasks accumulate on the shared
    event loop and cause the suite to hang.

    Each per-file mock_bot fixture already patches bot.loop.create_task, but
    that only covers the bot.loop path. This fixture additionally patches
    asyncio.create_task at module level so any coroutine scheduled via that
    path is closed immediately and never reaches the event loop.

    The per-file patches are left in place as belt-and-suspenders — this
    fixture is the safety net that catches anything that slips through.
    """

    def _close_coro(coro):
        coro.close()
        return MagicMock()

    with patch("asyncio.create_task", side_effect=_close_coro):
        yield
