"""Unit tests for Sub-task A: bounty tier color-coding.

Acceptance criteria:
- TIER_COLORS dict exists in bountyCog module-level
- _build_check_embed uses tier color from data['division']
- Capture/correct results use tier color, not generic green
- Incorrect/already_checked results use tier color
- _get_tier_color falls back to blue for unknown/None tier
"""

import os
import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Module-level mock setup — must run before any src imports
# ---------------------------------------------------------------------------

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []
_mock_bblogger = types.ModuleType("shared.bblogger")
_mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _evict_discord_modules():
    to_evict = [
        k
        for k in sys.modules
        if k == "discord"
        or k.startswith("discord.")
        or k in ("api", "bot", "utils")
        or k.startswith("api.")
        or k.startswith("utils.")
        or k.startswith("cogs.")
    ]
    for k in to_evict:
        sys.modules.pop(k, None)


def _make_cog():
    """Return a BountyCog instance with mocked dependencies."""
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger
    _evict_discord_modules()

    bot = MagicMock()
    bot.loop = MagicMock()
    bot.loop.create_task = MagicMock(return_value=None)

    from cogs.bountyCog import BountyCog

    cog = BountyCog(bot)
    cog.http_client = MagicMock()
    return cog


class TestTierColorsConstant:
    """TIER_COLORS is defined at module level with correct values."""

    def test_tier_colors_exists_at_module_level(self):
        """TIER_COLORS dict is present in the bountyCog module."""
        _evict_discord_modules()
        from cogs import bountyCog

        assert hasattr(bountyCog, "TIER_COLORS")

    def test_tier_colors_has_four_tiers(self):
        """TIER_COLORS contains exactly four tier keys."""
        _evict_discord_modules()
        from cogs.bountyCog import TIER_COLORS

        assert set(TIER_COLORS.keys()) == {"bronze", "silver", "gold", "platinum"}

    def test_bronze_color_value(self):
        """Bronze color is 0xCD7F32."""
        _evict_discord_modules()
        from cogs.bountyCog import TIER_COLORS

        assert TIER_COLORS["bronze"] == 0xCD7F32

    def test_silver_color_value(self):
        """Silver color is 0xC0C0C0."""
        _evict_discord_modules()
        from cogs.bountyCog import TIER_COLORS

        assert TIER_COLORS["silver"] == 0xC0C0C0

    def test_gold_color_value(self):
        """Gold color is 0xFFD700."""
        _evict_discord_modules()
        from cogs.bountyCog import TIER_COLORS

        assert TIER_COLORS["gold"] == 0xFFD700

    def test_platinum_color_value(self):
        """Platinum color is 0xE5E4E2."""
        _evict_discord_modules()
        from cogs.bountyCog import TIER_COLORS

        assert TIER_COLORS["platinum"] == 0xE5E4E2


class TestGetTierColor:
    """Tests for BountyCog._get_tier_color."""

    def test_returns_bronze_color(self):
        """Returns correct discord.Color for bronze tier."""
        cog = _make_cog()
        from cogs.bountyCog import TIER_COLORS

        color = cog._get_tier_color("bronze")
        assert color.value == TIER_COLORS["bronze"]

    def test_returns_gold_color(self):
        """Returns correct discord.Color for gold tier."""
        cog = _make_cog()
        from cogs.bountyCog import TIER_COLORS

        color = cog._get_tier_color("gold")
        assert color.value == TIER_COLORS["gold"]

    def test_case_insensitive(self):
        """Tier lookup is case-insensitive."""
        cog = _make_cog()
        from cogs.bountyCog import TIER_COLORS

        assert cog._get_tier_color("Bronze").value == TIER_COLORS["bronze"]
        assert cog._get_tier_color("BRONZE").value == TIER_COLORS["bronze"]

    def test_unknown_tier_falls_back_to_blue(self):
        """Unknown tier returns discord.Color.blue()."""
        cog = _make_cog()
        import discord

        color = cog._get_tier_color("unknown_tier")
        assert color.value == discord.Color.blue().value

    def test_none_tier_falls_back_to_blue(self):
        """None tier returns discord.Color.blue()."""
        cog = _make_cog()
        import discord

        color = cog._get_tier_color(None)
        assert color.value == discord.Color.blue().value


class TestBuildCheckEmbedTierColors:
    """Tests that _build_check_embed uses tier-based colors from data['division']."""

    def test_correct_result_capture_uses_tier_color(self):
        """Capture (result=correct) embed uses tier color from division."""
        cog = _make_cog()
        from cogs.bountyCog import TIER_COLORS

        data = {
            "result": "correct",
            "system_name": "Alpha",
            "message": "",
            "criminal_name": "BlackViper",
            "combat_won": None,  # bronze capture
            "reward": 500,
            "total_reward": 500,
            "bonus_won": False,
            "division": "gold",
        }
        embed = cog._build_check_embed(data)
        assert embed.color.value == TIER_COLORS["gold"]

    def test_captured_result_uses_tier_color(self):
        """Backward-compat 'captured' result uses tier color."""
        cog = _make_cog()
        from cogs.bountyCog import TIER_COLORS

        data = {
            "result": "captured",
            "system_name": "Beta",
            "message": "",
            "criminal_name": "DarkStar",
            "reward": 300,
            "total_reward": 300,
            "bonus_won": False,
            "division": "silver",
        }
        embed = cog._build_check_embed(data)
        assert embed.color.value == TIER_COLORS["silver"]

    def test_combat_win_result_uses_tier_color(self):
        """Backward-compat 'combat_win' result uses tier color."""
        cog = _make_cog()
        from cogs.bountyCog import TIER_COLORS

        data = {
            "result": "combat_win",
            "system_name": "Gamma",
            "message": "",
            "criminal_name": "Razorback",
            "reward": 2000,
            "division": "platinum",
        }
        embed = cog._build_check_embed(data)
        assert embed.color.value == TIER_COLORS["platinum"]

    def test_incorrect_result_uses_tier_color(self):
        """Incorrect result embed uses tier color."""
        cog = _make_cog()
        from cogs.bountyCog import TIER_COLORS

        data = {
            "result": "incorrect",
            "system_name": "Delta",
            "message": "",
            "recently_spotted": False,
            "division": "bronze",
        }
        embed = cog._build_check_embed(data)
        assert embed.color.value == TIER_COLORS["bronze"]

    def test_recently_spotted_uses_tier_color(self):
        """Recently-spotted incorrect result uses tier color."""
        cog = _make_cog()
        from cogs.bountyCog import TIER_COLORS

        data = {
            "result": "incorrect",
            "system_name": "Delta",
            "message": "",
            "recently_spotted": True,
            "division": "silver",
        }
        embed = cog._build_check_embed(data)
        assert embed.color.value == TIER_COLORS["silver"]

    def test_already_checked_uses_tier_color(self):
        """Already-checked result embed uses tier color."""
        cog = _make_cog()
        from cogs.bountyCog import TIER_COLORS

        data = {
            "result": "already_checked",
            "system_name": "Epsilon",
            "message": "",
            "division": "gold",
        }
        embed = cog._build_check_embed(data)
        assert embed.color.value == TIER_COLORS["gold"]

    def test_no_division_falls_back_to_blue(self):
        """When division is absent, tier color falls back gracefully."""
        cog = _make_cog()
        import discord

        data = {
            "result": "incorrect",
            "system_name": "Zeta",
            "message": "",
            "recently_spotted": False,
            # No 'division' key
        }
        embed = cog._build_check_embed(data)
        assert embed.color.value == discord.Color.blue().value

    def test_combat_defeat_uses_dark_red(self):
        """Combat defeat (combat_won=False) keeps dark_red for urgency."""
        cog = _make_cog()
        import discord

        data = {
            "result": "correct",
            "system_name": "Eta",
            "message": "",
            "criminal_name": "Ravager",
            "combat_won": False,
            "division": "gold",
        }
        embed = cog._build_check_embed(data)
        # Combat defeat should stay dark_red regardless of tier
        assert embed.color.value == discord.Color.dark_red().value


class TestGetTierColorAdversarial:
    """Adversarial edge case tests for _get_tier_color."""

    def test_mixed_case_GOLD_is_handled(self):
        """'GOLD' (all caps) correctly returns gold tier color."""
        cog = _make_cog()
        from cogs.bountyCog import TIER_COLORS

        color = cog._get_tier_color("GOLD")
        assert color.value == TIER_COLORS["gold"]

    def test_mixed_case_Platinum_is_handled(self):
        """'Platinum' (title case) returns platinum tier color."""
        cog = _make_cog()
        from cogs.bountyCog import TIER_COLORS

        color = cog._get_tier_color("Platinum")
        assert color.value == TIER_COLORS["platinum"]

    def test_mixed_case_sIlVeR_is_handled(self):
        """Weirdly-cased 'sIlVeR' returns silver tier color."""
        cog = _make_cog()
        from cogs.bountyCog import TIER_COLORS

        color = cog._get_tier_color("sIlVeR")
        assert color.value == TIER_COLORS["silver"]

    def test_empty_string_falls_back_to_blue(self):
        """Empty string tier falls back to blue."""
        cog = _make_cog()
        import discord

        color = cog._get_tier_color("")
        assert color.value == discord.Color.blue().value

    def test_whitespace_tier_falls_back_to_blue(self):
        """Whitespace-only tier falls back to blue."""
        cog = _make_cog()
        import discord

        color = cog._get_tier_color("   ")
        # '   '.lower() is still '   ', not a known tier -> blue fallback
        assert color.value == discord.Color.blue().value

    def test_not_found_result_uses_orange_not_tier_color(self):
        """not_found / unknown result uses orange, not tier color."""
        cog = _make_cog()
        import discord

        data = {
            "result": "not_found",
            "system_name": "Theta",
            "message": "No bounty",
            "division": "gold",
        }
        embed = cog._build_check_embed(data)
        # not_found uses orange regardless of tier
        assert embed.color.value == discord.Color.orange().value
