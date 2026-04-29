"""Centralized HTTP error helper for Discord cogs.

Provides a single public function — :func:`report_api_error` — that converts
``httpx`` exceptions (and other unexpected exceptions) into sanitized,
status-aware ephemeral error embeds.

The helper:

* Never leaks internal hostnames / ports / paths (e.g. ``http://bot-core:8000/...``).
* Strips the MDN documentation hyperlink that newer ``httpx`` versions append
  to ``HTTPStatusError.__str__()``.
* Surfaces the FastAPI ``detail`` field (already-sanitized by bot-core) when
  present, while falling back to a friendly canned phrase keyed by HTTP
  status code.
* Wraps the actual ``followup.send`` in :func:`contextlib.suppress` so a
  transient post-defer Discord race cannot bubble out and trigger
  "This interaction failed" UX.

Designed as a near-mechanical 1:1 substitution for the legacy
``await interaction.followup.send(f"❌ API Error: {e}", ephemeral=True)``
pattern used at 53 sites across 9 cogs.

See ``/proj/recon/B31b-design.md`` for the full design rationale.
"""

from __future__ import annotations

import contextlib
import re

import discord
import httpx
from shared import bblogger

flogger = bblogger.get_logger("discord-gateway-http-error-helper")

# --------------------------------------------------------------------------- #
# Sanitization
# --------------------------------------------------------------------------- #

# Match URLs (http/https) up to the next whitespace or quote-like character.
_URL_PATTERN = re.compile(r"https?://[^\s'\"]+")

# Match (and remove) any line containing the literal MDN-hint phrase emitted
# by httpx for non-2xx responses, case-insensitive, line-granular.
_MDN_LINE_PATTERN = re.compile(r"(?im)^.*for more information check:.*$")

# Collapse runs of any whitespace to a single space.
_WS_PATTERN = re.compile(r"\s+")

# Hard cap for the embed description (Discord allows up to 4096; 1000 is
# plenty for a sanitized error message and keeps logs sane).
_MAX_DESCRIPTION = 1000


def _sanitize(text: str) -> str:
    """Strip URLs / MDN hints, collapse whitespace, and truncate."""
    if not text:
        return ""
    text = _MDN_LINE_PATTERN.sub("", text)
    text = _URL_PATTERN.sub("", text)
    text = _WS_PATTERN.sub(" ", text).strip()
    if len(text) > _MAX_DESCRIPTION:
        text = text[: _MAX_DESCRIPTION - 1].rstrip() + "…"
    return text


# --------------------------------------------------------------------------- #
# Status-code mapping
# --------------------------------------------------------------------------- #

# Severity → ("error", "warning"). Drives both the leading emoji and the
# embed color. "warning" is used for transient/server-side conditions where
# the user has done nothing wrong.
_DEFAULT_BRANCHES: dict[int, tuple[str, str]] = {
    400: ("Invalid request.", "error"),
    401: ("Permission denied.", "error"),
    403: ("Permission denied.", "error"),
    404: ("Not found.", "error"),
    409: ("Conflict — please retry.", "warning"),
    422: ("Invalid input.", "error"),
    429: ("Rate limited — please wait.", "warning"),
}

_FALLBACK_5XX = ("Service issue, please try again.", "warning")
_FALLBACK_OTHER = ("Unexpected error.", "error")
_REQUEST_ERROR_MSG = ("Service unreachable, please try again.", "warning")


def _classify(status: int) -> tuple[str, str]:
    """Return (canned_message, severity) for a given HTTP status code."""
    if status in _DEFAULT_BRANCHES:
        return _DEFAULT_BRANCHES[status]
    if 500 <= status < 600:
        return _FALLBACK_5XX
    return _FALLBACK_OTHER


# --------------------------------------------------------------------------- #
# Detail extraction
# --------------------------------------------------------------------------- #

_DETAIL_MAX_CHARS = 200


def _extract_detail(exc: httpx.HTTPStatusError) -> str | None:
    """Return a short string built from a FastAPI-style ``detail`` body, or None.

    Handles both string and list (Pydantic 422) shapes. Returns ``None`` when
    the body is not JSON, has no ``detail``, or the detail is otherwise
    unusable.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    try:
        body = response.json()
    except Exception:  # pylint: disable=broad-exception-caught
        return None
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    if isinstance(detail, str):
        text = detail.strip()
    elif isinstance(detail, list):
        parts: list[str] = []
        for item in detail:
            if not isinstance(item, dict):
                continue
            loc = item.get("loc") or []
            field = ".".join(str(p) for p in loc[1:]) if isinstance(loc, list) and len(loc) > 1 else None
            msg = item.get("msg") or ""
            parts.append(f"{field}: {msg}" if field else str(msg))
        text = "; ".join(p for p in parts if p)
    else:
        return None
    if not text:
        return None
    if len(text) > _DETAIL_MAX_CHARS:
        text = text[: _DETAIL_MAX_CHARS - 1].rstrip() + "…"
    return text


# --------------------------------------------------------------------------- #
# Embed construction
# --------------------------------------------------------------------------- #


def _build_embed(
    exc: Exception,
    *,
    action_label: str | None = None,
    detail_override: dict[int, str] | None = None,
) -> discord.Embed:
    detail: str | None = None
    if isinstance(exc, httpx.HTTPStatusError):
        status = getattr(exc.response, "status_code", 0) or 0
        canned, severity = _classify(status)
        if detail_override and status in detail_override:
            canned = detail_override[status]
        detail = _extract_detail(exc)
    elif isinstance(exc, httpx.RequestError):
        canned, severity = _REQUEST_ERROR_MSG
    else:
        canned, severity = _FALLBACK_OTHER

    description = f"{canned}: {detail}" if detail else canned
    description = _sanitize(description)

    if severity == "warning":
        title = "⚠️ Warning"
        color = discord.Color.orange()
    else:
        title = "❌ Error"
        color = discord.Color.red()
    if action_label:
        title = f"{title}: {action_label}"

    return discord.Embed(title=title, description=description, color=color)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


async def report_api_error(
    interaction: discord.Interaction,
    exc: Exception,
    *,
    action_label: str | None = None,
    detail_override: dict[int, str] | None = None,
) -> None:
    """Send a sanitized ephemeral error reply for a failed API call.

    Safe to call after :meth:`Interaction.response.defer`; swallows
    :class:`discord.HTTPException` so a followup race cannot bubble out and
    trigger "This interaction failed" UX.

    Args:
        interaction: The Discord interaction. Must already have been deferred
            OR be in a state where ``followup.send`` is valid; the helper
            does **not** call ``defer`` itself.
        exc: The exception caught. Typically :class:`httpx.HTTPStatusError`.
            Other types (:class:`httpx.RequestError`, generic
            :class:`Exception`) are accepted and produce a generic
            "service unreachable" / "unexpected error" message.
        action_label: Optional short verb-phrase for the failed action
            (e.g. ``"scheduler view"``). Used as embed title context only;
            never echoed into a URL or path.
        detail_override: Optional ``{status_code: friendly_message}`` mapping
            that wins over the default canned message for the matching
            status code. JSON-body detail extraction still applies on top.
    """
    embed = _build_embed(exc, action_label=action_label, detail_override=detail_override)
    try:
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception:  # pylint: disable=broad-exception-caught
        # Defense in depth: anything else (e.g. mock misconfiguration in tests
        # or unexpected runtime error inside discord.py) must not propagate.
        flogger.exception("report_api_error: followup.send failed unexpectedly")
