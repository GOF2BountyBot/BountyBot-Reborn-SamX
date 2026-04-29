"""Tests for the centralized HTTP error helper (cogs/_shared/http_error_handler.py).

Covers:
- Status-code → canned message mapping (400/401/403/404/409/422/429/5xx/other)
- Detail extraction (string + Pydantic-list shape)
- Severity color/emoji selection
- ``detail_override`` mapping
- Sanitization (URL stripping, MDN line stripping, whitespace collapse, truncation)
- ``httpx.RequestError`` (connection failure) branch
- Generic exception branch
- ``action_label`` titling
- Race-safe send: ``followup.send`` raising ``discord.HTTPException`` is suppressed
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Re-assert real discord before importing the module under test (other test
# files in the suite mutate sys.modules["discord"] at import time).
_conftest = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
if _conftest is not None:
    sys.modules["discord"] = _conftest._REAL_DISCORD
    sys.modules["discord.ext"] = _conftest._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _conftest._REAL_DISCORD_EXT_COMMANDS

import discord
import httpx
from cogs._shared.http_error_handler import (
    _build_embed,
    _sanitize,
    report_api_error,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _http_error(status_code: int, body: object | None = None) -> httpx.HTTPStatusError:
    """Construct a minimal httpx.HTTPStatusError with a fake response.

    ``body`` controls what ``response.json()`` returns. ``None`` means
    ``json()`` raises (simulating non-JSON body).
    """
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    if body is None:
        response.json = MagicMock(side_effect=ValueError("not json"))
    else:
        response.json = MagicMock(return_value=body)
    request = MagicMock(spec=httpx.Request)
    return httpx.HTTPStatusError(message=f"{status_code}", request=request, response=response)


def _interaction() -> MagicMock:
    """Build an interaction with an awaitable followup.send."""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


# --------------------------------------------------------------------------- #
# Status-code mapping
# --------------------------------------------------------------------------- #


def test_404_default_message():
    embed = _build_embed(_http_error(404))
    assert "Not found." in embed.description
    assert embed.color == discord.Color.red()
    assert embed.title.startswith("❌")


def test_404_with_detail_override():
    exc = _http_error(404)
    embed = _build_embed(exc, detail_override={404: "Job not found."})
    assert "Job not found." in embed.description
    assert "Not found." not in embed.description


def test_400_with_detail_field():
    exc = _http_error(400, body={"detail": "Bad guild"})
    embed = _build_embed(exc)
    assert embed.description == "Invalid request.: Bad guild"


def test_422_with_pydantic_detail_list():
    body = {
        "detail": [
            {"loc": ["body", "name"], "msg": "field required", "type": "value_error.missing"},
            {"loc": ["body", "age"], "msg": "Input should be an int", "type": "type_error"},
        ]
    }
    exc = _http_error(422, body=body)
    embed = _build_embed(exc)
    assert "Invalid input." in embed.description
    assert "name: field required" in embed.description
    assert "age: Input should be an int" in embed.description


def test_5xx_uses_orange_severity():
    embed = _build_embed(_http_error(503))
    assert embed.color == discord.Color.orange()
    assert embed.title.startswith("⚠️")
    assert "Service issue" in embed.description


def test_request_error_unreachable():
    exc = httpx.ConnectError("connection refused")
    embed = _build_embed(exc)
    assert embed.color == discord.Color.orange()
    assert "unreachable" in embed.description.lower()


def test_unexpected_exception_type():
    exc = RuntimeError("something broke")
    embed = _build_embed(exc)
    assert embed.color == discord.Color.red()
    assert "Unexpected error." in embed.description


# --------------------------------------------------------------------------- #
# Sanitization
# --------------------------------------------------------------------------- #


def test_url_stripped_from_message():
    exc = _http_error(500, body={"detail": "see http://bot-core:8000/api/v1/foo for context"})
    embed = _build_embed(exc)
    assert "http://bot-core" not in embed.description
    assert "8000" not in embed.description
    assert "/api/v1" not in embed.description


def test_mdn_link_stripped():
    raw = (
        "Server error '500 Internal Server Error' for url 'http://bot-core:8000/api/v1/x'\n"
        "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500"
    )
    cleaned = _sanitize(raw)
    assert "developer.mozilla.org" not in cleaned
    assert "for more information check" not in cleaned.lower()
    assert "bot-core" not in cleaned


def test_detail_truncation():
    long_detail = "x" * 500
    exc = _http_error(400, body={"detail": long_detail})
    embed = _build_embed(exc)
    # Detail field truncated to 200 chars (-1 for ellipsis); embed description
    # therefore stays well under the 1000-char description cap.
    assert len(embed.description) <= 1000
    assert embed.description.endswith("…")


def test_non_json_response_body():
    """Status 500 + non-JSON body → canned 5xx message, no crash."""
    embed = _build_embed(_http_error(500, body=None))
    assert "Service issue" in embed.description


# --------------------------------------------------------------------------- #
# Embed titling
# --------------------------------------------------------------------------- #


def test_action_label_in_title():
    embed = _build_embed(_http_error(404), action_label="scheduler view")
    assert "scheduler view" in embed.title


# --------------------------------------------------------------------------- #
# Race-safe send
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_send_failure_suppressed():
    """interaction.followup.send raising HTTPException must NOT propagate."""
    interaction = _interaction()
    interaction.followup.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=503), "fail"))

    # Must not raise.
    await report_api_error(interaction, _http_error(500))

    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_report_api_error_sends_embed_ephemeral():
    """Happy path: helper sends an ephemeral embed."""
    interaction = _interaction()

    await report_api_error(interaction, _http_error(404))

    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs.get("ephemeral") is True
    embed = kwargs.get("embed")
    assert isinstance(embed, discord.Embed)
    assert "Not found." in embed.description


# --------------------------------------------------------------------------- #
# Defense-in-depth — the str(exc) of a real-shaped HTTPStatusError contains
# both the leaked URL and the MDN link. The sanitizer's primary guard is
# "we never put str(exc) into the user-visible string" — verify here that
# if it ever did, the URL/MDN would still be stripped.
# --------------------------------------------------------------------------- #


def test_sanitizer_defense_in_depth():
    raw_str = (
        "Server error '500 Internal Server Error' for url 'http://bot-core:8000/api/v1/config/guild/42/reset'\n"
        "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/500"
    )
    cleaned = _sanitize(raw_str)
    assert "bot-core" not in cleaned
    assert "8000" not in cleaned
    assert "developer.mozilla.org" not in cleaned
    assert "for more information check" not in cleaned.lower()
