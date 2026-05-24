"""Tests for utils.shop_announcement — per-tier color, inventory fields, embed logic.

Covers:
- Tier colour mapping (each of the 4 tiers gets the right color)
- Inventory fields rendered correctly (items grouped by type)
- Empty items list → "no items stocked" description, no inventory fields
- items=None (legacy call) → existing behavior unchanged
- Role mention only on first tier (Bronze), None on Silver/Gold/Platinum (via executor)
- _truncate_lines truncation with "… and N more" suffix
- _build_inventory_fields groups items correctly
- _format_item_line formats ORM-like objects and plain dicts
- _get_item_attr duck-typing (ORM objects and dicts)
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup and shared stub registration
# ---------------------------------------------------------------------------

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")

    def _make_logger(name="test"):
        logger = MagicMock()
        for m in ("info", "debug", "warning", "error", "trace", "critical"):
            setattr(logger, m, MagicMock())
        return logger

    _mock_bblogger.get_logger = _make_logger
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

# ---------------------------------------------------------------------------
# Application imports
# ---------------------------------------------------------------------------

import respx
from utils.shop_announcement import (
    _DEFAULT_SHOP_COLOR,
    _TIER_COLORS,
    _build_inventory_fields,
    _format_item_line,
    _get_item_attr,
    _truncate_lines,
    announce_shop_refresh,
)

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

GUILD_ID = 9_600_000_001
CHANNEL_ID = 55_500
ROLE_ID = 77_700

GATEWAY_HOST = os.getenv("DISCORD_GATEWAY_HOST", "discord-gateway")
GATEWAY_PORT = os.getenv("GATEWAY_PORT", "7999")
GATEWAY_URL = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/api/v1/channels/{CHANNEL_ID}/messages"


# ===========================================================================
# Pure helpers — Tier A (zero mocks)
# ===========================================================================


class TestTierColors:
    """Verify tier → color mapping."""

    def test_bronze_color(self):
        assert _TIER_COLORS["bronze"] == 13467442

    def test_silver_color(self):
        assert _TIER_COLORS["silver"] == 12632256

    def test_gold_color(self):
        assert _TIER_COLORS["gold"] == 16766720

    def test_platinum_color(self):
        assert _TIER_COLORS["platinum"] == 15066082

    def test_default_color_is_blue(self):
        assert _DEFAULT_SHOP_COLOR == 3447003

    def test_all_four_tiers_present(self):
        assert set(_TIER_COLORS.keys()) == {"bronze", "silver", "gold", "platinum"}


class TestGetItemAttr:
    """Verify _get_item_attr duck-typing for ORM objects and plain dicts."""

    def test_gets_attribute_from_object(self):
        obj = MagicMock()
        obj.item_name = "Ridil Blaster"
        assert _get_item_attr(obj, "item_name") == "Ridil Blaster"

    def test_gets_from_dict_when_attr_missing(self):
        item = {"item_name": "Nirai Impactor", "price": 1000, "quantity": 2}
        assert _get_item_attr(item, "item_name") == "Nirai Impactor"

    def test_returns_none_when_missing_on_object_and_not_dict(self):
        class Obj:
            pass

        assert _get_item_attr(Obj(), "missing_attr") is None

    def test_dict_missing_key_returns_none(self):
        assert _get_item_attr({}, "price") is None


class TestFormatItemLine:
    """Verify _format_item_line formats correctly for both ORM objects and dicts."""

    def test_formats_dict_item(self):
        item = {"item_name": "Ion Blaster", "price": 1500, "quantity": 3}
        line = _format_item_line(item)
        assert line == "Ion Blaster — 1,500c (x3)"

    def test_formats_orm_like_object(self):
        obj = MagicMock()
        obj.item_name = "Rhino"
        obj.price = 25000
        obj.quantity = 1
        line = _format_item_line(obj)
        assert line == "Rhino — 25,000c (x1)"

    def test_price_formatted_with_commas(self):
        item = {"item_name": "Destroyer", "price": 1000000, "quantity": 1}
        line = _format_item_line(item)
        assert "1,000,000c" in line

    def test_unknown_name_fallback(self):
        item = {}
        line = _format_item_line(item)
        assert line.startswith("Unknown")

    def test_zero_price_shown(self):
        item = {"item_name": "TestItem", "price": 0, "quantity": 1}
        line = _format_item_line(item)
        assert "0c" in line


class TestTruncateLines:
    """Verify _truncate_lines respects cap and appends overflow suffix."""

    def test_short_lines_not_truncated(self):
        lines = ["A", "B", "C"]
        text, dropped = _truncate_lines(lines, 1024)
        assert dropped == 0
        assert text == "A\nB\nC"

    def test_truncation_when_over_cap(self):
        # Create lines that exceed a small cap
        lines = ["x" * 30] * 10  # 300 chars without newlines
        text, dropped = _truncate_lines(lines, 50)
        assert dropped > 0
        assert len(text) <= 50

    def test_empty_lines(self):
        text, dropped = _truncate_lines([], 1024)
        assert text == ""
        assert dropped == 0

    def test_single_line_fits(self):
        lines = ["short"]
        text, dropped = _truncate_lines(lines, 100)
        assert dropped == 0
        assert text == "short"

    def test_returns_dropped_count(self):
        # 10 lines of 200 chars each — only first few will fit in cap=300
        lines = ["A" * 200] * 10
        _text, dropped = _truncate_lines(lines, 300)
        # At least some lines should be dropped
        assert dropped >= 8


class TestBuildInventoryFields:
    """Verify _build_inventory_fields groups items by type and uses display order."""

    def test_groups_by_item_type(self):
        items = [
            {"item_type": "module", "item_name": "Shield Mk1", "price": 500, "quantity": 2},
            {"item_type": "primary_weapon", "item_name": "Ion Cannon", "price": 1000, "quantity": 1},
        ]
        fields = _build_inventory_fields(items)
        field_names = [f["name"] for f in fields]
        assert any("Primary Weapons" in n for n in field_names)
        assert any("Modules" in n for n in field_names)

    def test_empty_items_returns_empty_fields(self):
        fields = _build_inventory_fields([])
        assert fields == []

    def test_none_items_returns_empty_fields(self):
        fields = _build_inventory_fields(None)
        assert fields == []

    def test_display_order_ships_first(self):
        items = [
            {"item_type": "module", "item_name": "Mod A", "price": 100, "quantity": 1},
            {"item_type": "ship", "item_name": "Ship A", "price": 5000, "quantity": 1},
        ]
        fields = _build_inventory_fields(items)
        # Ships should appear before modules in the output
        ship_idx = next(i for i, f in enumerate(fields) if "Ships" in f["name"])
        mod_idx = next(i for i, f in enumerate(fields) if "Modules" in f["name"])
        assert ship_idx < mod_idx

    def test_unknown_type_excluded_from_display(self):
        """Items with unknown item_type are grouped under 'unknown' and excluded from display."""
        items = [
            {"item_type": "unknown_type", "item_name": "Mystery Item", "price": 0, "quantity": 1},
        ]
        fields = _build_inventory_fields(items)
        # 'unknown_type' is not in _ITEM_TYPE_DISPLAY, so no fields added
        assert fields == []

    def test_inline_false_for_all_fields(self):
        items = [
            {"item_type": "module", "item_name": "Shield", "price": 500, "quantity": 1},
        ]
        fields = _build_inventory_fields(items)
        for f in fields:
            assert f["inline"] is False

    def test_truncation_suffix_added_when_value_overflows(self):
        """When many items overflow the 1024-char field limit, suffix is added."""
        # 20 items each with long names → should overflow 1024 chars
        items = [
            {"item_type": "module", "item_name": f"VeryLongModuleName{i:04d}Mk2Plus", "price": 99999, "quantity": 5}
            for i in range(30)
        ]
        fields = _build_inventory_fields(items)
        assert len(fields) == 1
        value = fields[0]["value"]
        assert "… and" in value and "more" in value


# ===========================================================================
# Integration tests: announce_shop_refresh embed construction
# ===========================================================================


class TestAnnounceShopRefreshNewPath:
    """Tests for the new inventory-aware embed path (items is not None)."""

    async def test_tier_color_used_in_bronze_embed(self):
        """Bronze tier announcement uses #CD7F32 (13467442)."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Bronze",
                items=[],
            )

        assert len(captured) == 1
        assert captured[0]["content"]["color"] == _TIER_COLORS["bronze"]

    async def test_tier_color_used_in_silver_embed(self):
        """Silver tier announcement uses #C0C0C0 (12632256)."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Silver",
                items=[],
            )

        assert captured[0]["content"]["color"] == _TIER_COLORS["silver"]

    async def test_tier_color_used_in_gold_embed(self):
        """Gold tier announcement uses #FFD700 (16766720)."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Gold",
                items=[],
            )

        assert captured[0]["content"]["color"] == _TIER_COLORS["gold"]

    async def test_tier_color_used_in_platinum_embed(self):
        """Platinum tier announcement uses #E5E4E2 (15066082)."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Platinum",
                items=[],
            )

        assert captured[0]["content"]["color"] == _TIER_COLORS["platinum"]

    async def test_empty_items_shows_no_stock_description(self):
        """Empty items list → 'no items currently stocked' description, no item fields."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Bronze",
                items=[],
            )

        embed = captured[0]["content"]
        assert "no items are currently stocked" in embed["description"]
        # No inventory fields should be added
        assert embed["fields"] == []

    async def test_with_items_shows_restocked_description(self):
        """Non-empty items list → 'has been restocked' description with item fields."""
        items = [
            {"item_type": "module", "item_name": "Shield Mk1", "price": 500, "quantity": 2},
        ]
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Bronze",
                items=items,
            )

        embed = captured[0]["content"]
        assert "restocked" in embed["description"]
        # Inventory fields should be present
        assert len(embed["fields"]) > 0

    async def test_title_includes_tier_and_tech_level(self):
        """When tier and tech_level are both provided, title includes both."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Gold",
                items=[],
                tech_level=7,
            )

        title = captured[0]["content"]["title"]
        assert "Gold" in title
        assert "7" in title
        assert "Tech Level" in title

    async def test_title_includes_tier_only_when_no_tech_level(self):
        """When tier is provided but tech_level is None, title includes tier only."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Silver",
                items=[],
                tech_level=None,
            )

        title = captured[0]["content"]["title"]
        assert "Silver" in title
        assert "Tech Level" not in title

    async def test_footer_text_correct_for_new_path(self):
        """New path uses the /buy footer text."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Bronze",
                items=[],
            )

        footer = captured[0]["content"]["footer_text"]
        assert "/buy" in footer

    async def test_role_mention_included_when_provided(self):
        """Role mention appears in text_content when bounty_hunter_role_id is set."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                bounty_hunter_role_id=ROLE_ID,
                tier="Bronze",
                items=[],
            )

        text_content = captured[0]["text_content"]
        assert text_content == f"<@&{ROLE_ID}>"

    async def test_no_role_mention_when_none(self):
        """text_content is None when bounty_hunter_role_id is None."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                bounty_hunter_role_id=None,
                tier="Bronze",
                items=[],
            )

        assert captured[0]["text_content"] is None

    async def test_default_color_when_tier_is_none(self):
        """When tier is None but items is provided, use default blue color."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier=None,
                items=[],
            )

        assert captured[0]["content"]["color"] == _DEFAULT_SHOP_COLOR

    async def test_case_insensitive_tier_color_lookup(self):
        """Tier name 'BRONZE' (uppercase) maps to correct bronze color."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="BRONZE",
                items=[],
            )

        assert captured[0]["content"]["color"] == _TIER_COLORS["bronze"]


class TestAnnounceShopRefreshLegacyPath:
    """Tests for the legacy path (items=None) — behavior must be unchanged."""

    async def test_legacy_path_uses_blue_color(self):
        """Legacy call (items=None) uses the original blue #3498DB color."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Bronze",
                # items not passed → legacy path
            )

        assert captured[0]["content"]["color"] == 3447003

    async def test_legacy_path_title_is_shop_refreshed(self):
        """Legacy call always uses '🛒 Shop Refreshed!' title regardless of tier."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Gold",
            )

        assert captured[0]["content"]["title"] == "🛒 Shop Refreshed!"

    async def test_legacy_path_tier_none_shows_all_tiers(self):
        """Legacy call with tier=None shows 'Bronze · Silver · Gold · Platinum' field."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier=None,
            )

        fields = captured[0]["content"]["fields"]
        assert len(fields) == 1
        assert "Bronze" in fields[0]["value"]
        assert "Platinum" in fields[0]["value"]

    async def test_legacy_path_with_tier_shows_tier_refreshed_field(self):
        """Legacy call with tier='Silver' shows tier-specific field."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Silver",
            )

        fields = captured[0]["content"]["fields"]
        assert len(fields) == 1
        assert fields[0]["name"] == "Tier Refreshed"
        assert fields[0]["value"] == "Silver"

    async def test_legacy_path_footer_ends_with_exclamation(self):
        """Legacy call uses 'Use /shop to browse!' footer."""
        captured: list[dict] = []

        async def _fake_post(self_client, url, json=None, timeout=None):
            captured.append(json)
            response = MagicMock()
            response.raise_for_status = MagicMock()
            return response

        with patch("httpx.AsyncClient.post", _fake_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
            )

        footer = captured[0]["content"]["footer_text"]
        assert footer == "Use /shop to browse!"


class TestAnnounceShopRefreshChannelNone:
    """Tests that None channel_id skips the HTTP call."""

    async def test_no_http_call_when_channel_none(self):
        """When channel_id is None, no HTTP POST is made."""
        with (
            patch("httpx.AsyncClient.post", new=AsyncMock()) as mock_post,
        ):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=None,
                tier="Bronze",
                items=[],
            )

        mock_post.assert_not_called()

    async def test_no_http_call_legacy_when_channel_none(self):
        """Legacy path: when channel_id is None, no HTTP POST is made."""
        with (
            patch("httpx.AsyncClient.post", new=AsyncMock()) as mock_post,
        ):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=None,
            )

        mock_post.assert_not_called()


class TestAnnounceShopRefreshHttpFailure:
    """Tests that HTTP failures are non-fatal."""

    async def test_http_error_does_not_propagate(self):
        """HTTP failure is caught and logged; function returns normally."""
        import httpx as httpx_mod

        async def _failing_post(self_client, url, json=None, timeout=None):
            raise httpx_mod.ConnectError("connection refused")

        # Should not raise
        with patch("httpx.AsyncClient.post", _failing_post):
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Bronze",
                items=[],
            )

    async def test_500_response_does_not_propagate(self):
        """HTTP 500 response is caught and logged; function returns normally."""
        with respx.mock(assert_all_called=False) as router:
            router.post(GATEWAY_URL).respond(500)
            # Should not raise
            await announce_shop_refresh(
                caller_label="TestCaller",
                guild_id=GUILD_ID,
                channel_id=CHANNEL_ID,
                tier="Bronze",
                items=[],
            )
