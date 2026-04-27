"""Schemas for the announcement endpoints (e.g. unified bounty announcement).

Per A.48 unified-loadout-render spec, bounty announcements emit a structured
payload (LoadoutResponse + bounty metadata) that the gateway renders into a
single embed using the shared `build_loadout_embed`. This file defines the
request body for the new endpoint.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BountyAnnouncementMetadata(BaseModel):
    """Bounty-specific metadata that wraps the LoadoutResponse render."""

    model_config = ConfigDict(from_attributes=True)

    title: str = Field(..., description="Embed title (criminal name or '✅ Name — CAPTURED')")
    color: int = Field(..., description="Embed color (faction color, or green when captured)")
    footer_text: str | None = Field(None, description="Embed footer text (criminal faction)")
    image_url: str | None = Field(None, description="Embed large image URL (route map)")
    prefix_fields: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Fields rendered before the loadout sections (Difficulty, Reward, Bounty Ends, etc.)",
    )
    suffix_fields: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Fields rendered after the loadout sections (Route, Checked Systems, etc.)",
    )


class BountyAnnouncementRequest(BaseModel):
    """Request body for POST /announcements/bounty/channel/{channel_id}.

    `loadout_response` is a JSON-decoded LoadoutResponse body (subject_kind=criminal).
    Treated as opaque on this side; passed verbatim to `build_loadout_embed`.
    """

    model_config = ConfigDict(from_attributes=True)

    text_content: str | None = Field(None, description="Plain text content (e.g. role mention)")
    loadout_response: dict[str, Any] = Field(..., description="LoadoutResponse-shaped JSON")
    metadata: BountyAnnouncementMetadata = Field(..., description="Bounty-specific embed overrides")
