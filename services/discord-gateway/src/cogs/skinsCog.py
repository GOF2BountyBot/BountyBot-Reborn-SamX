import contextlib
import os
from io import BytesIO

import discord
import httpx
from discord import app_commands
from discord.ext import commands
from httpx import HTTPStatusError as HttpxHTTPStatusError
from httpx import RequestError as HttpxRequestError
from httpx import TimeoutException as HttpxTimeoutException
from shared import bblogger

flogger = bblogger.get_logger("discord-gateway-SkinsCog")
api_base = os.environ.get("BOT_API_BASE_URL", "http://bot-core:8000/api/v1")
BLENDER_API_BASE_URL = os.getenv("BLENDER_API_BASE_URL", "http://blender-service:8001/api/v1")


# ---------------------------------------------------------------------------
# UI Views
# ---------------------------------------------------------------------------


class SquareCheckView(discord.ui.View):
    """Buttons for crop/stretch choice when image isn't square."""

    def __init__(self, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.result: str | None = None

    @discord.ui.button(label="Crop", style=discord.ButtonStyle.primary, emoji="\u2702\ufe0f")
    async def crop_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        _ = button  # unused but required by discord.py callback signature
        self.result = "crop"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Stretch", style=discord.ButtonStyle.secondary, emoji="\u2194\ufe0f")
    async def stretch_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        _ = button
        self.result = "stretch"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        _ = button
        self.result = None
        self.stop()
        await interaction.response.defer()


class FormatDownloadView(discord.ui.View):
    """Buttons for downloading texture in different AEI formats."""

    def __init__(
        self,
        cog: "SkinsCog",
        texture_bytes: bytes,
        ship_name: str,
        timeout: float = 120,
    ):
        super().__init__(timeout=timeout)
        self._cog = cog
        self._texture_bytes = texture_bytes
        self._ship_name = ship_name

    @discord.ui.button(label="AEI (Android/ETC1)", style=discord.ButtonStyle.green)
    async def etc1_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        _ = button
        await self._convert_and_send(interaction, "etc1")

    @discord.ui.button(label="AEI (PC/DXT5)", style=discord.ButtonStyle.blurple)
    async def dxt5_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        _ = button
        await self._convert_and_send(interaction, "dxt5")

    async def _convert_and_send(self, interaction: discord.Interaction, fmt: str):
        await interaction.response.defer()
        try:
            response = await self._cog.blender_client.post(
                "/textures/convert",
                files={"image": ("texture.png", self._texture_bytes, "image/png")},
                data={"format": fmt, "quality": "3"},
            )
            response.raise_for_status()
            aei_bytes = response.content
            file = discord.File(
                BytesIO(aei_bytes), filename=f"{self._ship_name}_{fmt}.aei"
            )
            await interaction.followup.send(
                f"Here's your **{self._ship_name}** skin in **{fmt.upper()}** format!",
                file=file,
            )
        except HttpxHTTPStatusError as e:
            flogger.error(f"AEI convert HTTP error: {e.response.status_code}")
            await interaction.followup.send(
                f"❌ Failed to convert texture to {fmt.upper()}: API error {e.response.status_code}",
                ephemeral=True,
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"AEI convert error: {e}")
            await interaction.followup.send(
                f"❌ Failed to convert texture to {fmt.upper()}. Please try again later.",
                ephemeral=True,
            )


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class SkinsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        self.blender_client = httpx.AsyncClient(
            base_url=BLENDER_API_BASE_URL,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        # map ship name → list of skin names
        self._ship_skins: dict[str, list[str]] = {}
        # map ship name → render-info dict (for skinnable ships)
        self._ship_render_info: dict[str, dict] = {}
        bot.loop.create_task(self._preload_ship_skins())

    async def cog_unload(self):
        await self.http_client.aclose()
        await self.blender_client.aclose()

    async def _preload_ship_skins(self):
        await self.bot.wait_until_ready()
        try:
            flogger.info("Preloading ship skins…")
            resp = await self.http_client.get(
                f"{api_base}/about/categories/ship/objects", timeout=10
            )
            resp.raise_for_status()
            ships = resp.json()
            for sh in ships:
                name = sh.get("name")
                if not name:
                    continue
                try:
                    full = await self.http_client.get(
                        f"{api_base}/about/object/name/{name}", timeout=10
                    )
                    full.raise_for_status()
                    data = full.json()
                    skins = data.get("compatible_skins") or {}
                    self._ship_skins[name] = list(skins.keys())
                except HttpxTimeoutException as e:
                    flogger.warning(f"Timeout loading skins for {name}: {e}")
                    self._ship_skins[name] = []
                except HttpxHTTPStatusError as e:
                    flogger.warning(
                        f"HTTP error loading skins for {name}: {e.response.status_code}"
                    )
                    self._ship_skins[name] = []
                except HttpxRequestError as e:
                    flogger.warning(f"Request error loading skins for {name}: {e}")
                    self._ship_skins[name] = []
                except Exception as e:  # pylint: disable=broad-exception-caught
                    flogger.warning(f"Unexpected error loading skins for {name}: {e}")
                    self._ship_skins[name] = []
            flogger.info(
                f"Finished preloading skins for {len(self._ship_skins)} ships"
            )
        except HttpxTimeoutException as e:
            flogger.error(f"Timeout preloading ship list: {e}")
        except HttpxHTTPStatusError as e:
            flogger.error(f"HTTP error preloading ship list: {e.response.status_code}")
        except HttpxRequestError as e:
            flogger.error(f"Request error preloading ship list: {e}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Could not preload ship list: {e}")

    # ------------------------------------------------------------------
    # Autocomplete helpers
    # ------------------------------------------------------------------

    async def ship_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        txt = current.lower()
        choices = [
            app_commands.Choice(name=name, value=name)
            for name in self._ship_skins
            if txt in name.lower()
        ]
        return choices[:25]

    async def skin_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        # read the already-selected ship from namespace
        ship = getattr(interaction.namespace, "ship", None)
        if not ship:
            return []
        flogger.debug(f"skin_autocomplete: ship={ship!r}, filter={current!r}")
        skins = self._ship_skins.get(ship)
        if skins is None:
            return []
        if not skins:
            return [app_commands.Choice(name="Default", value="Default")]
        txt = current.lower()
        choices = [
            app_commands.Choice(name=s, value=s)
            for s in skins
            if txt in s.lower()
        ]
        if not choices:
            choices = [app_commands.Choice(name="Default", value="Default")]
        return choices[:25]

    async def skinnable_ship_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete that returns only skinnable ships (have render-info)."""
        txt = current.lower()
        choices = [
            app_commands.Choice(name=name, value=name)
            for name, ri in self._ship_render_info.items()
            if txt in name.lower() and ri.get("skinnable", False)
        ]
        # Fall back: if no render-info cached yet, use all ships
        if not choices and not self._ship_render_info:
            choices = [
                app_commands.Choice(name=name, value=name)
                for name in self._ship_skins
                if txt in name.lower()
            ]
        return choices[:25]

    # ------------------------------------------------------------------
    # /ship_skin
    # ------------------------------------------------------------------

    @app_commands.command(
        name="ship_skin", description="Display a ship skin image (or default icon)"
    )
    @app_commands.describe(ship="Select the ship", skin="Select the skin (or Default)")
    @app_commands.autocomplete(ship=ship_autocomplete, skin=skin_autocomplete)
    async def ship_skin(
        self, interaction: discord.Interaction, ship: str, skin: str
    ):
        await interaction.response.defer(thinking=True)
        # fetch full ship object to get URLs
        try:
            resp = await self.http_client.get(
                f"{api_base}/about/object/name/{ship}", timeout=10
            )
            resp.raise_for_status()
            obj = resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return await interaction.followup.send(
                    f"❌ Ship '{ship}' not found.", ephemeral=True
                )
            flogger.error(f"HTTP error fetching ship '{ship}': {e}")
            return await interaction.followup.send(
                "❌ API error fetching ship data. Please try again later.", ephemeral=True
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Error fetching ship '{ship}': {e}")
            return await interaction.followup.send(
                "⚠️ Unexpected error fetching ship data. Please try again later.", ephemeral=True
            )

        # select URL
        if skin == "Default":
            img_url = obj.get("icon")
        else:
            skins_map = obj.get("compatible_skins") or {}
            img_url = skins_map.get(skin)

        if not img_url:
            return await interaction.followup.send(
                f"❌ Skin '{skin}' not found for ship '{ship}'.", ephemeral=True
            )

        embed = discord.Embed(
            title=f"{ship} — {skin}", color=discord.Color.green()
        )
        embed.set_image(url=img_url)
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # Internal: collect textures from user
    # ------------------------------------------------------------------

    async def _collect_base_texture(
        self,
        interaction: discord.Interaction,
        ship: str,
    ) -> tuple[bytes | None, str]:
        """Ask user to upload base texture; return (bytes, square_mode).

        Returns (None, "") on cancel/timeout.
        """
        prompt = await interaction.followup.send(
            f"Please upload your **base texture** image for **{ship}**. "
            "It should be a square image (2048x2048 recommended).",
            wait=True,
        )

        def check_msg(m: discord.Message) -> bool:
            return m.author.id == interaction.user.id and len(m.attachments) > 0

        try:
            msg: discord.Message = await self.bot.wait_for(
                "message", check=check_msg, timeout=120
            )
        except TimeoutError:
            await interaction.followup.send(
                "Timed out waiting for base texture upload.", ephemeral=True
            )
            return None, ""

        attachment = msg.attachments[0]
        img_bytes = await attachment.read()

        # Determine dimensions without Pillow - just pass through to blender
        # but try to detect non-square via Content-Type.
        # Read width/height cheaply using attachment metadata when available.
        width = getattr(attachment, "width", None)
        height = getattr(attachment, "height", None)

        square_mode = "none"
        if width and height and width != height:
            view = SquareCheckView(timeout=60)
            await interaction.followup.send(
                f"Your image is **{width}x{height}** (not square). "
                "Choose how to handle it:",
                view=view,
            )
            await view.wait()
            if view.result is None:
                await interaction.followup.send(
                    "❌ Render cancelled.", ephemeral=True
                )
                return None, ""
            square_mode = view.result

        with contextlib.suppress(Exception):
            await prompt.delete()

        return img_bytes, square_mode

    async def _collect_region_textures(
        self,
        interaction: discord.Interaction,
        num_regions: int,
    ) -> dict[int, bytes]:
        """Collect per-region textures from the user. Returns {region_idx: bytes}."""
        region_textures: dict[int, bytes] = {}

        for region_idx in range(1, num_regions + 1):
            await interaction.followup.send(
                f"Upload texture for **Region {region_idx}** "
                "(or type `skip` to skip, `disable` to revert to base texture):",
            )

            def make_check(uid: int):
                def check(m: discord.Message) -> bool:
                    return m.author.id == uid and (
                        len(m.attachments) > 0
                        or m.content.lower() in ("skip", "disable")
                    )
                return check

            try:
                msg: discord.Message = await self.bot.wait_for(
                    "message",
                    check=make_check(interaction.user.id),
                    timeout=120,
                )
            except TimeoutError:
                await interaction.followup.send(
                    f"Timed out waiting for Region {region_idx} texture - skipping.",
                )
                continue

            if msg.content.lower() == "skip":
                flogger.debug(f"Region {region_idx} skipped")
                continue
            if msg.content.lower() == "disable":
                flogger.debug(f"Region {region_idx} disabled")
                region_textures[region_idx] = b""  # sentinel: disabled
                continue

            attachment = msg.attachments[0]
            region_textures[region_idx] = await attachment.read()

        return region_textures

    # ------------------------------------------------------------------
    # /render_skin
    # ------------------------------------------------------------------

    @app_commands.command(
        name="render_skin",
        description="Render a 3D ship with its default skin (or a custom overlay)",
    )
    @app_commands.describe(
        ship="The ship to render",
        skin="Optional: select a pre-made skin to apply",
    )
    @app_commands.autocomplete(ship=skinnable_ship_autocomplete, skin=skin_autocomplete)
    async def render_skin(
        self,
        interaction: discord.Interaction,
        ship: str,
        skin: str = "Default",
    ):
        await interaction.response.defer()

        # 1. Fetch render-info (provides all asset paths on disk)
        render_info = await self._fetch_render_info(interaction, ship)
        if render_info is None:
            return

        ship_path = render_info.get("bbship_dir", "")
        diffuse_path: str = render_info.get("diffuse_path", "")
        model_path: str = render_info.get("model_path", "")

        if not diffuse_path:
            flogger.error(f"No diffuse_path in render-info for {ship}")
            await interaction.followup.send(
                f"❌ No base texture found for **{ship}**.", ephemeral=True
            )
            return

        # 2. Resolve the skin overlay image (if not Default)
        skin_bytes: bytes | None = None
        if skin != "Default":
            skin_bytes = await self._download_skin_image(interaction, ship, skin, render_info)
            if skin_bytes is None:
                return  # error already sent

        # 3. Composite via blender-service (base texture loaded from disk)
        composite_bytes = await self._composite_textures(
            interaction, ship, ship_path, diffuse_path, skin_bytes
        )
        if composite_bytes is None:
            return

        # 4. Render via blender-service
        await interaction.followup.send("🎨 Rendering your ship… this may take a moment.")
        try:
            render_resp = await self.blender_client.post(
                "/render/",
                files={"texture": ("composite.png", composite_bytes, "image/png")},
                data={"model_path": model_path},
            )
            render_resp.raise_for_status()
            rendered_bytes = render_resp.content
        except HttpxHTTPStatusError as e:
            flogger.error(f"Render API error for {ship}: {e.response.status_code}")
            await interaction.followup.send(
                f"❌ Render failed: API error {e.response.status_code}", ephemeral=True
            )
            return
        except HttpxTimeoutException:
            flogger.error(f"Render timed out for {ship}")
            await interaction.followup.send(
                "❌ Render timed out. Please try again later.", ephemeral=True
            )
            return
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Render error for {ship}: {e}")
            await interaction.followup.send(
                "❌ Render failed. Please try again later.", ephemeral=True
            )
            return

        # 5. Send rendered image + format buttons
        file = discord.File(BytesIO(rendered_bytes), filename=f"{ship}_render.png")
        view = FormatDownloadView(self, composite_bytes, ship)
        await interaction.followup.send(
            f"🚀 Here's your **{ship}** skin render!",
            file=file,
            view=view,
        )

    # ------------------------------------------------------------------
    # /make_skin_texture
    # ------------------------------------------------------------------

    @app_commands.command(
        name="make_skin_texture",
        description="Generate a composited ship skin texture (no 3D render)",
    )
    @app_commands.describe(
        ship="The ship to create a skin texture for",
        skin="Optional: select a pre-made skin to apply",
    )
    @app_commands.autocomplete(ship=skinnable_ship_autocomplete, skin=skin_autocomplete)
    async def make_skin_texture(
        self,
        interaction: discord.Interaction,
        ship: str,
        skin: str = "Default",
    ):
        await interaction.response.defer()

        # 1. Fetch render-info (provides all asset paths on disk)
        render_info = await self._fetch_render_info(interaction, ship)
        if render_info is None:
            return

        ship_path = render_info.get("bbship_dir", "")
        diffuse_path: str = render_info.get("diffuse_path", "")

        if not diffuse_path:
            flogger.error(f"No diffuse_path in render-info for {ship}")
            await interaction.followup.send(
                f"❌ No base texture found for **{ship}**.", ephemeral=True
            )
            return

        # 2. Resolve the skin overlay image (if not Default)
        skin_bytes: bytes | None = None
        if skin != "Default":
            skin_bytes = await self._download_skin_image(interaction, ship, skin, render_info)
            if skin_bytes is None:
                return  # error already sent

        # 3. Composite via blender-service (base texture loaded from disk)
        composite_bytes = await self._composite_textures(
            interaction, ship, ship_path, diffuse_path, skin_bytes
        )
        if composite_bytes is None:
            return

        # 4. Return composited texture + format buttons
        file = discord.File(
            BytesIO(composite_bytes), filename=f"{ship}_texture.png"
        )
        view = FormatDownloadView(self, composite_bytes, ship)
        await interaction.followup.send(
            f"🎨 Here's your composited **{ship}** skin texture!",
            file=file,
            view=view,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_render_info(
        self, interaction: discord.Interaction, ship: str
    ) -> dict | None:
        """Fetch render-info for a ship. Sends error and returns None on failure."""
        try:
            resp = await self.http_client.get(
                f"{api_base}/about/ships/{ship}/render-info", timeout=10
            )
            if resp.status_code == 404:
                await interaction.followup.send(
                    f"❌ Ship **{ship}** not found.", ephemeral=True
                )
                return None
            resp.raise_for_status()
            data = resp.json()
        except HttpxHTTPStatusError as e:
            flogger.error(f"render-info HTTP error for {ship}: {e.response.status_code}")
            await interaction.followup.send(
                f"❌ API error fetching render info: {e.response.status_code}",
                ephemeral=True,
            )
            return None
        except HttpxTimeoutException:
            await interaction.followup.send(
                "❌ Timed out fetching ship info. Please try again.", ephemeral=True
            )
            return None
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Unexpected error fetching render-info for {ship}: {e}")
            await interaction.followup.send(
                "⚠️ Unexpected error fetching ship info.", ephemeral=True
            )
            return None

        if not data.get("skinnable", False):
            await interaction.followup.send(
                f"❌ **{ship}** does not support custom skins.", ephemeral=True
            )
            return None

        # Cache render info for autocomplete
        self._ship_render_info[ship] = data
        return data

    async def _download_skin_image(
        self,
        interaction: discord.Interaction,
        ship: str,
        skin: str,
        render_info: dict,
    ) -> bytes | None:
        """Download a pre-made skin image from its URL. Returns bytes or None on error."""
        skins_map = render_info.get("compatible_skins") or {}
        skin_url = skins_map.get(skin)
        if not skin_url:
            await interaction.followup.send(
                f"❌ Skin **{skin}** not found for **{ship}**.", ephemeral=True
            )
            return None

        try:
            resp = await self.http_client.get(skin_url, timeout=30)
            resp.raise_for_status()
            flogger.debug(f"Downloaded skin image for {ship}/{skin}: {len(resp.content)} bytes")
            return resp.content
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Failed to download skin image for {ship}/{skin}: {e}")
            await interaction.followup.send(
                f"❌ Failed to download skin image for **{skin}**.", ephemeral=True
            )
            return None

    async def _composite_textures(
        self,
        interaction: discord.Interaction,
        ship: str,
        ship_path: str,
        diffuse_path: str,
        skin_bytes: bytes | None = None,
    ) -> bytes | None:
        """Call blender-service composite endpoint.

        Uses the ship's diffuse texture (on disk in blender-service) as the base.
        Optionally applies a skin overlay as region texture 1.
        Returns composited PNG bytes or None on error.
        """
        await interaction.followup.send("🔧 Compositing textures…")

        # Build multipart request — base texture is loaded from disk by blender-service
        files: list[tuple] = []
        data: dict[str, str] = {
            "ship_path": ship_path,
            "base_texture_path": diffuse_path,
            "square_mode": "none",
            "region_indices": "",
            "disabled_regions": "",
        }

        # If a skin overlay was provided, send it as region texture 1
        if skin_bytes is not None:
            files.append(
                ("region_textures", ("region1.png", skin_bytes, "image/png"))
            )
            data["region_indices"] = "1"

        try:
            resp = await self.blender_client.post(
                "/textures/composite",
                files=files if files else None,
                data=data,
            )
            resp.raise_for_status()
            flogger.info(f"Composite successful for {ship}")
            return resp.content
        except HttpxHTTPStatusError as e:
            flogger.error(
                f"Composite API error for {ship}: {e.response.status_code}"
            )
            await interaction.followup.send(
                f"❌ Texture compositing failed: API error {e.response.status_code}",
                ephemeral=True,
            )
            return None
        except HttpxTimeoutException:
            flogger.error(f"Composite timed out for {ship}")
            await interaction.followup.send(
                "❌ Compositing timed out. Please try again.", ephemeral=True
            )
            return None
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Composite error for {ship}: {e}")
            await interaction.followup.send(
                "❌ Texture compositing failed. Please try again later.", ephemeral=True
            )
            return None


async def setup(bot: commands.Bot):
    flogger.debug("Loading SkinsCog…")
    await bot.add_cog(SkinsCog(bot))
    flogger.info("SkinsCog loaded")
