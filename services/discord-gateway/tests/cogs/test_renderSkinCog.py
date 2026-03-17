"""
Tests for the render skin commands and UI views added to skinsCog.

Covers:
- SquareCheckView (crop / stretch / cancel)
- FormatDownloadView (png / etc1 / dxt5)
- skinnable_ship_autocomplete (filtered list)
- render_skin: ship not found, non-skinnable ship, defers interaction
- make_skin_texture: success path (mocked blender calls)
"""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Module-level mock setup — must run before any src imports
# ---------------------------------------------------------------------------
from tests.mocks.discord_mock_utils import DiscordMockUtils

_mock_shared = types.ModuleType("shared")
_mock_shared.__path__ = []

_mock_bblogger = types.ModuleType("shared.bblogger")


def _make_mock_logger(*_args, **_kwargs):
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.trace = MagicMock()
    logger.critical = MagicMock()
    return logger


_mock_bblogger.get_logger = _make_mock_logger

sys.modules["shared"] = _mock_shared
sys.modules["shared.bblogger"] = _mock_bblogger

for _mod in ["discord", "discord.ext", "discord.ext.commands", "discord.app_commands"]:
    sys.modules.pop(_mod, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _close_coro(coro):
    """Close coroutine to prevent 'never awaited' warning."""
    coro.close()
    return MagicMock()


def _evict_discord_modules():
    to_evict = [
        k for k in sys.modules
        if k == "discord" or k.startswith("discord.")
        or k in ("api", "bot", "utils") or k.startswith("api.")
        or k.startswith("utils.") or k.startswith("cogs.")
    ]
    for k in to_evict:
        sys.modules.pop(k, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bot():
    loop = MagicMock()
    loop.create_task = MagicMock(side_effect=_close_coro)
    bot = DiscordMockUtils.create_mock_bot(
        user_id=123456789,
        username="TestBot",
        add_cog=AsyncMock(),
        tree=MagicMock(),
        get_member=MagicMock(),
        flogger=MagicMock(),
        loop=loop,
    )
    bot.wait_for = AsyncMock()
    return bot


@pytest.fixture
def mock_cog(mock_bot):
    _evict_discord_modules()
    from cogs.skinsCog import SkinsCog
    cog = SkinsCog(mock_bot)
    # Pre-populate ship data
    cog._ship_skins = {
        "Skinnable Ship": ["Skin A"],
        "Plain Ship": [],
    }
    cog._ship_render_info = {
        "Skinnable Ship": {
            "skinnable": True,
            "bbship_dir": "/ships/skinnable",
            "model_path": "/models/skinnable.blend",
            "texture_regions": 2,
        }
    }
    return cog


def _make_interaction(user_id: int = 111):
    interaction = DiscordMockUtils.create_mock_interaction(user_id=user_id)
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock())
    interaction.user.id = user_id
    return interaction


# ---------------------------------------------------------------------------
# SquareCheckView tests
# ---------------------------------------------------------------------------


class TestSquareCheckView:
    """Tests for the SquareCheckView UI component."""

    def test_square_check_view_crop(self):
        """SquareCheckView crop button sets result to 'crop'."""
        _evict_discord_modules()
        from cogs.skinsCog import SquareCheckView

        view = SquareCheckView(timeout=60)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()

        # discord.py wraps button methods as _ItemCallback(interaction) -> coro
        asyncio.run(view.crop_button.callback(interaction))

        assert view.result == "crop"
        interaction.response.defer.assert_called_once()

    def test_square_check_view_stretch(self):
        """SquareCheckView stretch button sets result to 'stretch'."""
        _evict_discord_modules()
        from cogs.skinsCog import SquareCheckView

        view = SquareCheckView(timeout=60)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()

        asyncio.run(view.stretch_button.callback(interaction))

        assert view.result == "stretch"
        interaction.response.defer.assert_called_once()

    def test_square_check_view_cancel(self):
        """SquareCheckView cancel button sets result to None."""
        _evict_discord_modules()
        from cogs.skinsCog import SquareCheckView

        view = SquareCheckView(timeout=60)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()

        asyncio.run(view.cancel_button.callback(interaction))

        assert view.result is None
        interaction.response.defer.assert_called_once()


# ---------------------------------------------------------------------------
# FormatDownloadView tests
# ---------------------------------------------------------------------------


class TestFormatDownloadView:
    """Tests for FormatDownloadView AEI conversion buttons."""

    def _make_view(self, cog, texture_bytes: bytes = b"PNG_DATA"):
        _evict_discord_modules()
        from cogs.skinsCog import FormatDownloadView
        return FormatDownloadView(cog, texture_bytes, "TestShip", timeout=120)

    def test_format_download_view_etc1(self, mock_cog):
        """ETC1 button calls blender /textures/convert with format=etc1."""
        fake_response = MagicMock()
        fake_response.content = b"ETC1_AEI"
        fake_response.raise_for_status = MagicMock()
        mock_cog.blender_client = MagicMock()
        mock_cog.blender_client.post = AsyncMock(return_value=fake_response)

        view = self._make_view(mock_cog)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        # _ItemCallback(interaction) -> coroutine
        asyncio.run(view.etc1_button.callback(interaction))

        mock_cog.blender_client.post.assert_called_once()
        call_kwargs = mock_cog.blender_client.post.call_args
        assert "/textures/convert" in call_kwargs[0][0]
        assert call_kwargs[1]["data"]["format"] == "etc1"
        interaction.followup.send.assert_called_once()

    def test_format_download_view_dxt5(self, mock_cog):
        """DXT5 button calls blender /textures/convert with format=dxt5."""
        fake_response = MagicMock()
        fake_response.content = b"DXT5_AEI"
        fake_response.raise_for_status = MagicMock()
        mock_cog.blender_client = MagicMock()
        mock_cog.blender_client.post = AsyncMock(return_value=fake_response)

        view = self._make_view(mock_cog)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        asyncio.run(view.dxt5_button.callback(interaction))

        mock_cog.blender_client.post.assert_called_once()
        call_kwargs = mock_cog.blender_client.post.call_args
        assert call_kwargs[1]["data"]["format"] == "dxt5"
        interaction.followup.send.assert_called_once()

    def test_format_download_view_png(self, mock_cog):
        """PNG button sends the raw texture bytes as a PNG file."""
        texture_data = b"PNG_DATA"
        view = self._make_view(mock_cog, texture_bytes=texture_data)

        interaction = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()

        asyncio.run(view.png_button.callback(interaction))

        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert "TestShip" in call_kwargs[0][0]
        sent_file = call_kwargs[1]["file"]
        assert sent_file.filename == "TestShip_skin.png"


# ---------------------------------------------------------------------------
# skinnable_ship_autocomplete tests
# ---------------------------------------------------------------------------


class TestSkinnableShipAutocomplete:
    """Tests for autocomplete that filters to skinnable ships only."""

    def test_skinnable_autocomplete_filters_non_skinnable(self, mock_cog):
        """skinnable_ship_autocomplete returns only ships with render_info skinnable=True."""
        # Add a non-skinnable ship to render_info
        mock_cog._ship_render_info["Not Skinnable"] = {"skinnable": False}

        choices = asyncio.run(
            mock_cog.skinnable_ship_autocomplete(MagicMock(), "")
        )
        names = [c.name for c in choices]

        assert "Skinnable Ship" in names
        assert "Not Skinnable" not in names

    def test_skinnable_autocomplete_filter_by_text(self, mock_cog):
        """skinnable_ship_autocomplete filters by current text."""
        choices = asyncio.run(
            mock_cog.skinnable_ship_autocomplete(MagicMock(), "skin")
        )
        assert len(choices) == 1
        assert choices[0].name == "Skinnable Ship"

    def test_skinnable_autocomplete_fallback_no_render_info(self, mock_cog):
        """skinnable_ship_autocomplete falls back to all ships when no render info cached."""
        mock_cog._ship_render_info = {}
        choices = asyncio.run(
            mock_cog.skinnable_ship_autocomplete(MagicMock(), "")
        )
        # Should return all ships from _ship_skins
        assert len(choices) == 2


# ---------------------------------------------------------------------------
# render_skin tests
# ---------------------------------------------------------------------------


class TestRenderSkinCommand:
    """Tests for the /render_skin command."""

    def test_render_skin_defers_interaction(self, mock_cog):
        """render_skin defers the interaction at the start."""
        interaction = _make_interaction()

        # Make http_client return 404 for render-info so we exit early
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status = MagicMock(
            side_effect=__import__("httpx").HTTPStatusError(
                "not found",
                request=MagicMock(),
                response=mock_resp,
            )
        )
        mock_cog.http_client = MagicMock()
        mock_cog.http_client.get = AsyncMock(return_value=mock_resp)

        asyncio.run(
            mock_cog.render_skin.callback(
                mock_cog, interaction=interaction, ship="Unknown Ship", autoskin=True
            )
        )

        interaction.response.defer.assert_called_once()

    def test_render_skin_ship_not_found(self, mock_cog):
        """render_skin sends error when ship returns 404."""
        import httpx

        interaction = _make_interaction()

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_cog.http_client = MagicMock()
        mock_cog.http_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "not found", request=MagicMock(), response=mock_resp
            )
        )

        asyncio.run(
            mock_cog.render_skin.callback(
                mock_cog, interaction=interaction, ship="Ghost Ship", autoskin=False
            )
        )

        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called()
        # Check that an error message was sent
        sent_text = str(interaction.followup.send.call_args_list)
        assert "Ghost Ship" in sent_text or "error" in sent_text.lower()

    def test_render_skin_not_skinnable(self, mock_cog):
        """render_skin sends error when ship is not skinnable."""
        interaction = _make_interaction()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"skinnable": False, "name": "Rock Ship"}
        mock_resp.raise_for_status = MagicMock()
        mock_cog.http_client = MagicMock()
        mock_cog.http_client.get = AsyncMock(return_value=mock_resp)

        asyncio.run(
            mock_cog.render_skin.callback(
                mock_cog, interaction=interaction, ship="Rock Ship", autoskin=False
            )
        )

        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called()
        sent_calls = interaction.followup.send.call_args_list
        sent_text = " ".join(str(c) for c in sent_calls)
        assert "not support" in sent_text.lower() or "rock ship" in sent_text.lower()


# ---------------------------------------------------------------------------
# make_skin_texture tests
# ---------------------------------------------------------------------------


class TestMakeSkinTextureCommand:
    """Tests for the /make_skin_texture command."""

    def test_make_skin_texture_success(self, mock_bot, mock_cog):
        """make_skin_texture calls composite and returns PNG."""
        interaction = _make_interaction()

        # Bot returns render-info
        render_info_resp = MagicMock()
        render_info_resp.status_code = 200
        render_info_resp.json.return_value = {
            "skinnable": True,
            "bbship_dir": "/ships/test",
            "model_path": "/models/test.blend",
            "texture_regions": 0,
        }
        render_info_resp.raise_for_status = MagicMock()
        mock_cog.http_client = MagicMock()
        mock_cog.http_client.get = AsyncMock(return_value=render_info_resp)

        # Build a fake attachment
        fake_attachment = MagicMock()
        fake_attachment.read = AsyncMock(return_value=b"FAKE_PNG")
        fake_attachment.width = 512
        fake_attachment.height = 512  # square — no SquareCheckView needed

        # Bot wait_for returns a message with attachment
        fake_msg = MagicMock()
        fake_msg.attachments = [fake_attachment]
        fake_msg.content = ""
        mock_bot.wait_for = AsyncMock(return_value=fake_msg)

        # Blender composite returns PNG bytes
        composite_resp = MagicMock()
        composite_resp.content = b"COMPOSITE_PNG"
        composite_resp.raise_for_status = MagicMock()
        mock_cog.blender_client = MagicMock()
        mock_cog.blender_client.post = AsyncMock(return_value=composite_resp)

        asyncio.run(
            mock_cog.make_skin_texture.callback(
                mock_cog, interaction=interaction, ship="Skinnable Ship"
            )
        )

        interaction.response.defer.assert_called_once()
        # Composite should have been called
        mock_cog.blender_client.post.assert_called()
        # A file should have been sent
        send_calls = interaction.followup.send.call_args_list
        # At minimum, followup.send was called (with the texture)
        assert len(send_calls) > 0
