"""Unit tests for cogs._shared.embed_pagination.add_continuation_fields.

These tests exercise the helper with real ``discord.Embed`` objects (no
mocks). They lock in the behaviour that powers the A.26 (duplicate
"Objects" headers) and A.27 (silent truncation) regression fixes in
``aboutCog.list_category``.
"""

from __future__ import annotations

import os
import sys

# Ensure src/ is on the import path.  This mirrors the bootstrap dance used
# by sibling cog test modules; it is safe here because we only import the
# lightweight cogs._shared.embed_pagination module (no Discord bot setup).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

# Other test files under tests/api/ mutate ``sys.modules["discord"]`` to a
# MagicMock at *import* time. When pytest has already imported any of those
# modules before reaching this file (which happens in the full-suite run
# because _shared sorts alphabetically early inside tests/cogs/), a plain
# ``import discord`` below would pick up the mock.  Re-assert the real
# modules cached by the root conftest BEFORE importing.
_conftest = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
if _conftest is not None:
    sys.modules["discord"] = _conftest._REAL_DISCORD
    sys.modules["discord.ext"] = _conftest._REAL_DISCORD_EXT
    sys.modules["discord.ext.commands"] = _conftest._REAL_DISCORD_EXT_COMMANDS
    # Drop any cached import of the helper so it re-binds its module-level
    # ``import discord`` to the real module on the next import below.
    sys.modules.pop("cogs._shared.embed_pagination", None)

import discord
from cogs._shared.embed_pagination import (
    DEFAULT_LIST_CAP,
    MAX_FIELD_VALUE,
    MAX_FIELDS,
    SPACER_NAME,
    add_continuation_fields,
)


def _make_embed() -> discord.Embed:
    return discord.Embed(title="Listing", description="test")


class TestAddContinuationFields:
    """Boundary-case tests for the pagination helper."""

    def test_empty_lines_appends_no_fields(self):
        embed = _make_embed()
        rendered = add_continuation_fields(embed, "Objects", [])
        assert rendered == 0
        assert len(embed.fields) == 0

    def test_single_field_fits_uses_header_only(self):
        """Short content fits in one field — only the header appears, no spacer."""
        embed = _make_embed()
        rendered = add_continuation_fields(
            embed,
            "Objects",
            ["Alpha", "Beta", "Gamma"],
        )
        assert rendered == 3
        assert len(embed.fields) == 1
        assert embed.fields[0].name == "Objects"
        assert embed.fields[0].value == "Alpha\nBeta\nGamma"

    def test_splits_at_1024_boundary_uses_spacer_for_continuation(self):
        """When content exceeds the 1024-char cap, extra fields use SPACER_NAME."""
        embed = _make_embed()
        # Each line is 100 chars → 11 lines ≈ 1100 chars once newlines are added,
        # which forces a split into two fields.
        lines = [f"L{i:02d}_{'x' * 96}" for i in range(12)]
        rendered = add_continuation_fields(embed, "Objects", lines)
        assert rendered == 12
        assert len(embed.fields) >= 2
        assert embed.fields[0].name == "Objects"
        # All continuation fields must carry the zero-width spacer name, never
        # a visible duplicate of "Objects".
        for field in embed.fields[1:]:
            assert field.name == SPACER_NAME
            assert field.name != "Objects"
        # Every field's value stays under the Discord per-field cap.
        for field in embed.fields:
            assert len(field.value) <= MAX_FIELD_VALUE

    def test_caps_at_default_limit(self):
        """Passing more than DEFAULT_LIST_CAP lines renders only the first cap-many."""
        embed = _make_embed()
        lines = [f"item_{i:03d}" for i in range(150)]
        rendered = add_continuation_fields(embed, "Objects", lines)
        assert rendered == DEFAULT_LIST_CAP == 100
        # The combined field values must only contain the first 100 entries.
        rendered_text = "\n".join(f.value for f in embed.fields)
        assert "item_099" in rendered_text
        assert "item_100" not in rendered_text

    def test_preserves_emoji_prefix_verbatim(self):
        """Lines with emoji prefixes are rendered byte-for-byte as provided."""
        embed = _make_embed()
        lines = ["🚀 Eagle", "🦅 Hawk", "⭐ Star", "Plain"]
        rendered = add_continuation_fields(embed, "Objects", lines)
        assert rendered == 4
        assert len(embed.fields) == 1
        assert embed.fields[0].value == "🚀 Eagle\n🦅 Hawk\n⭐ Star\nPlain"

    def test_respects_25_field_ceiling(self):
        """If the embed is already near the 25-field ceiling, we stop adding fields."""
        embed = _make_embed()
        for i in range(MAX_FIELDS - 1):
            embed.add_field(name=f"filler_{i}", value=f"v{i}", inline=False)
        assert len(embed.fields) == MAX_FIELDS - 1

        # Content long enough to force multiple continuation fields if allowed.
        lines = [f"L{i:02d}_{'x' * 96}" for i in range(12)]
        add_continuation_fields(embed, "Objects", lines)

        # We must never exceed the hard 25-field ceiling.
        assert len(embed.fields) <= MAX_FIELDS
        # At least one field beyond the filler should have been added (the header).
        assert any(f.name == "Objects" for f in embed.fields)
