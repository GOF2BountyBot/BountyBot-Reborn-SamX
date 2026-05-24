import asyncio
import contextlib
import os
from io import BytesIO

import discord
import httpx
from cogs._shared.autocomplete_cache import AutocompleteCache
from discord import app_commands
from discord.ext import commands
from httpx import HTTPStatusError as HttpxHTTPStatusError
from httpx import RequestError as HttpxRequestError
from httpx import TimeoutException as HttpxTimeoutException
from shared import bblogger
from utils.autocomplete_utils import fuzzy_filter, normalize_for_search

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
    async def crop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button  # unused but required by discord.py callback signature
        self.result = "crop"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Stretch", style=discord.ButtonStyle.secondary, emoji="\u2194\ufe0f")
    async def stretch_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button
        self.result = "stretch"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button
        self.result = None
        self.stop()
        await interaction.response.defer()


class RegionModeView(discord.ui.View):
    """Buttons for choosing how to apply a skin to a multi-region ship.

    Presents three options:
    - "Apply to All Regions" (primary) → result = "all"
    - "Customize Per Region" (secondary) → result = "custom"
    - "Cancel" (danger) → result = None
    """

    def __init__(self, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.result: str | None = None

    @discord.ui.button(label="Apply to All Regions", style=discord.ButtonStyle.primary, emoji="\U0001f3a8")
    async def apply_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button
        self.result = "all"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Customize Per Region", style=discord.ButtonStyle.secondary, emoji="\U0001f527")
    async def customize_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button
        self.result = "custom"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="\u274c")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button
        self.result = None
        self.stop()
        await interaction.response.defer()


class RegionOptionView(discord.ui.View):
    """Dropdown select for choosing what texture to apply to a specific region.

    Dynamically builds options from the available skin and region context.
    self.selected_value is set to the chosen option value when the user selects.
    """

    def __init__(
        self,
        region_num: int,
        total_regions: int,
        skin_name: str | None = None,
        compatible_skins: dict | None = None,
        timeout: float = 120,
    ):
        super().__init__(timeout=timeout)
        self.selected_value: str | None = None

        # Build options dynamically
        options: list[discord.SelectOption] = []

        if skin_name:
            options.append(
                discord.SelectOption(
                    label=f"Apply '{skin_name}'",
                    value=f"skin:{skin_name}",
                    emoji="\u2728",  # ✨
                )
            )

        options.append(
            discord.SelectOption(
                label="Upload custom image",
                value="upload",
                emoji="\U0001f4e4",  # 📤
            )
        )
        options.append(
            discord.SelectOption(
                label="Keep default look",
                value="skip",
                emoji="\U0001f532",  # 🔲
            )
        )

        if compatible_skins:
            for name in compatible_skins:
                if name != skin_name and len(options) < 25:
                    options.append(
                        discord.SelectOption(
                            label=name,
                            value=f"skin:{name}",
                            emoji="\U0001f3a8",  # 🎨
                        )
                    )

        select = discord.ui.Select(
            placeholder=f"Region {region_num} of {total_regions}",
            options=options,
        )
        select.callback = self._select_callback
        self.add_item(select)

    async def _select_callback(self, interaction: discord.Interaction):
        # The select item's values attribute holds the chosen option
        select_item = self.children[0]
        self.selected_value = select_item.values[0]
        self.stop()
        await interaction.response.defer()


class FormatDownloadView(discord.ui.View):
    """Buttons for downloading render/texture in PNG and AEI formats.

    *render_bytes* is the 3D-rendered image (used by the PNG download button
    when available).  *texture_bytes* is always the composited texture map
    (used for AEI conversion, and as PNG fallback when no render is available).
    """

    def __init__(
        self,
        cog: "SkinsCog",
        texture_bytes: bytes,
        ship_name: str,
        timeout: float = 120,
        render_bytes: bytes | None = None,
    ):
        super().__init__(timeout=timeout)
        self._cog = cog
        self._texture_bytes = texture_bytes
        self._render_bytes = render_bytes
        self._ship_name = ship_name

    @discord.ui.button(label="Download PNG", style=discord.ButtonStyle.secondary)
    async def png_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button
        await interaction.response.defer()
        # Use the 3D render when available, otherwise fall back to the texture
        png_data = self._render_bytes if self._render_bytes is not None else self._texture_bytes
        suffix = "render" if self._render_bytes is not None else "skin"
        file = discord.File(
            BytesIO(png_data),
            filename=f"{self._ship_name}_{suffix}.png",
        )
        await interaction.followup.send(
            f"Here's your **{self._ship_name}** as PNG!",
            file=file,
        )

    @discord.ui.button(label="AEI (Android/ETC1)", style=discord.ButtonStyle.green)
    async def etc1_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        _ = button
        await self._convert_and_send(interaction, "etc1")

    @discord.ui.button(label="AEI (PC/DXT5)", style=discord.ButtonStyle.blurple)
    async def dxt5_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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
            file = discord.File(BytesIO(aei_bytes), filename=f"{self._ship_name}_{fmt}.aei")
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
        # Static catalog caches — TTL=None (never expires; only reloaded on /reload_autocomplete)
        # map ship name → list of skin names
        self._ship_skins: AutocompleteCache[str, list[str]] = AutocompleteCache(
            ttl_seconds=None,
            name="skins-render-info",
        )
        # map ship name → render-info dict (for skinnable ships)
        self._ship_render_info: dict[str, dict] = {}
        bot.loop.create_task(self._preload_ship_skins())

    async def cog_unload(self):
        await self.http_client.aclose()
        await self.blender_client.aclose()

    async def _preload_ship_skins(self):
        """Preload ship skin data at startup for autocomplete (with retries)."""
        await self.bot.wait_until_ready()
        delays = [5, 10, 20, 40, 60]
        for attempt, delay in enumerate(delays, start=1):
            try:
                flogger.info("SkinsCog: Starting preload of ship skins (attempt %d/%d)...", attempt, len(delays))
                resp = await self.http_client.get(f"{api_base}/about/categories/ship/objects", timeout=10)
                resp.raise_for_status()
                ships = resp.json()
                for sh in ships:
                    name = sh.get("name")
                    if not name:
                        continue
                    try:
                        full = await self.http_client.get(f"{api_base}/about/object/name/{name}", timeout=10)
                        full.raise_for_status()
                        data = full.json()
                        skins = data.get("compatible_skins") or {}
                        self._ship_skins.set(name, list(skins.keys()))
                    except HttpxTimeoutException as e:
                        flogger.warning(f"Timeout loading skins for {name}: {e}")
                        self._ship_skins.set(name, [])
                    except HttpxHTTPStatusError as e:
                        flogger.warning(f"HTTP error loading skins for {name}: {e.response.status_code}")
                        self._ship_skins.set(name, [])
                    except HttpxRequestError as e:
                        flogger.warning(f"Request error loading skins for {name}: {e}")
                        self._ship_skins.set(name, [])
                    except Exception as e:  # pylint: disable=broad-exception-caught
                        flogger.warning(f"Unexpected error loading skins for {name}: {e}")
                        self._ship_skins.set(name, [])
                flogger.info("SkinsCog: Preloaded skins for %d ships", self._ship_skins.size)
                return
            except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as e:
                flogger.warning(
                    "SkinsCog: Preload attempt %d/%d failed: %s — retrying in %ds", attempt, len(delays), e, delay
                )
                await asyncio.sleep(delay)
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.warning("SkinsCog: Unexpected error on preload attempt %d/%d: %s", attempt, len(delays), e)
                await asyncio.sleep(delay)
        flogger.error("SkinsCog: All preload attempts exhausted. Ship skin autocomplete will be empty.")
        self._ship_skins.clear()

    # ------------------------------------------------------------------
    # Autocomplete helpers
    # ------------------------------------------------------------------

    async def ship_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=name, value=name) for name in fuzzy_filter(current, self._ship_skins.keys())]

    async def skin_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        # read the already-selected ship from namespace
        ship = getattr(interaction.namespace, "ship", None)
        if not ship:
            return []
        flogger.debug(f"skin_autocomplete: ship={ship!r}, filter={current!r}")
        skins = self._ship_skins.peek(ship)
        if skins is None:
            return []
        if not skins:
            return [app_commands.Choice(name="Default", value="Default")]
        norm_current = normalize_for_search(current)
        choices = [app_commands.Choice(name=s, value=s) for s in skins if norm_current in normalize_for_search(s)]
        if not choices:
            choices = [app_commands.Choice(name="Default", value="Default")]
        return choices[:25]

    async def skinnable_ship_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete that returns only skinnable ships.

        Uses the full ``_ship_skins`` list as the candidate pool and filters
        out ships that are known to be non-skinnable (from cached render-info).
        Ships without cached render-info are included so the list is never
        artificially truncated to only previously-rendered ships.
        """
        skinnable_names = [
            name
            for name in self._ship_skins.keys()  # noqa: SIM118 — AutocompleteCache has no __iter__
            if self._ship_render_info.get(name, {}).get("skinnable", True)
        ]
        return [app_commands.Choice(name=name, value=name) for name in fuzzy_filter(current, skinnable_names)]

    # ------------------------------------------------------------------
    # /ship_skin
    # ------------------------------------------------------------------

    @app_commands.command(name="ship_skin", description="Display a ship skin image (or default icon)")
    @app_commands.describe(ship="Select the ship", skin="Select the skin (or Default)")
    @app_commands.autocomplete(ship=ship_autocomplete, skin=skin_autocomplete)
    async def ship_skin(self, interaction: discord.Interaction, ship: str, skin: str):
        await interaction.response.defer(thinking=True)
        # fetch full ship object to get URLs
        try:
            resp = await self.http_client.get(f"{api_base}/about/object/name/{ship}", timeout=10)
            resp.raise_for_status()
            obj = resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return await interaction.followup.send(f"❌ Ship '{ship}' not found.", ephemeral=True)
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
            return await interaction.followup.send(f"❌ Skin '{skin}' not found for ship '{ship}'.", ephemeral=True)

        embed = discord.Embed(title=f"{ship} — {skin}", color=discord.Color.green())
        embed.set_image(url=img_url)
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # Internal: collect base texture from user
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
            msg: discord.Message = await self.bot.wait_for("message", check=check_msg, timeout=120)
        except TimeoutError:
            await interaction.followup.send("Timed out waiting for base texture upload.", ephemeral=True)
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
                f"Your image is **{width}x{height}** (not square). Choose how to handle it:",
                view=view,
            )
            await view.wait()
            if view.result is None:
                await interaction.followup.send("❌ Render cancelled.", ephemeral=True)
                return None, ""
            square_mode = view.result

        with contextlib.suppress(Exception):
            await prompt.delete()

        return img_bytes, square_mode

    # ------------------------------------------------------------------
    # Internal: multi-region helpers
    # ------------------------------------------------------------------

    async def _resolve_region_mode(
        self,
        interaction: discord.Interaction,
        render_info: dict,
        skin_bytes: bytes | None,
        skin_name: str | None,
        image_provided: bool,
    ) -> str | None:
        """Determine how to handle regions for a render/texture command.

        Returns:
            "all"    — apply skin as base texture (single-region or no skin)
            "custom" — enter per-region customization flow
            None     — user cancelled or timed out
        """
        # No skin and no image → default render, skip all region prompts
        if not image_provided and skin_bytes is None:
            return "all"

        # Single region (0 or 1 mask) → no prompt, apply uniformly
        num_regions = len(render_info.get("mask_paths", []))
        if num_regions <= 1:
            return "all"

        # Multi-region ship with a skin/image → present the choice
        skin_label = f"'{skin_name}'" if skin_name else "the custom image"
        view = RegionModeView(timeout=60)
        await interaction.followup.send(
            f"**{render_info.get('ship_name', 'This ship')}** has **{num_regions} skinnable regions**. "
            f"How would you like to apply {skin_label}?",
            view=view,
        )
        await view.wait()

        if view.result is None:
            # timed out or cancelled
            timed_out = view.result is None and not view.is_finished()
            if timed_out:
                await interaction.followup.send("❌ Region mode selection timed out.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Operation cancelled.", ephemeral=True)
        return view.result

    async def _collect_per_region_choices(
        self,
        interaction: discord.Interaction,
        render_info: dict,
        skin_name: str | None,
        skin_bytes: bytes | None,
        compatible_skins: dict,
    ) -> dict[int, dict] | None:
        """Collect per-region texture choices from the user via Select menus.

        For each region 1..N, presents a RegionOptionView dropdown.
        Returns a dict mapping region_index → {action, bytes} or None on full failure.

        region_choices[i] can be:
            {"action": "skin",   "bytes": <bytes>}
            {"action": "upload", "bytes": <bytes>}
            {"action": "skip"}
        """
        mask_paths = render_info.get("mask_paths", [])
        num_regions = len(mask_paths)
        region_choices: dict[int, dict] = {}

        # Cache downloaded skins within this invocation to avoid redundant downloads
        skin_cache: dict[str, bytes] = {}
        if skin_name and skin_bytes is not None:
            skin_cache[skin_name] = skin_bytes

        for region_idx in range(1, num_regions + 1):
            view = RegionOptionView(
                region_num=region_idx,
                total_regions=num_regions,
                skin_name=skin_name,
                compatible_skins=compatible_skins,
                timeout=120,
            )
            await interaction.followup.send(
                f"**Region {region_idx} of {num_regions}** — Select what to apply:",
                view=view,
            )
            await view.wait()

            selected = view.selected_value
            if selected is None:
                # Timed out — skip this region, continue
                flogger.debug(f"Region {region_idx} timed out — skipping")
                await interaction.followup.send(
                    f"⏱️ Timed out for Region {region_idx} — keeping default look.",
                    ephemeral=True,
                )
                region_choices[region_idx] = {"action": "skip"}
                continue

            if selected == "skip":
                flogger.debug(f"Region {region_idx}: keep default")
                region_choices[region_idx] = {"action": "skip"}
                await interaction.followup.send(f"✅ Region {region_idx} → default", ephemeral=True)

            elif selected == "upload":
                flogger.debug(f"Region {region_idx}: upload custom image")
                await interaction.followup.send(
                    f"Upload your image for **Region {region_idx}**:",
                    ephemeral=True,
                )

                def make_check(uid: int):
                    def check(m: discord.Message) -> bool:
                        return m.author.id == uid and len(m.attachments) > 0

                    return check

                try:
                    msg: discord.Message = await self.bot.wait_for(
                        "message",
                        check=make_check(interaction.user.id),
                        timeout=120,
                    )
                    uploaded_bytes = await msg.attachments[0].read()
                    region_choices[region_idx] = {"action": "upload", "bytes": uploaded_bytes}
                    await interaction.followup.send(f"✅ Region {region_idx} → custom upload", ephemeral=True)
                except TimeoutError:
                    flogger.debug(f"Region {region_idx} upload timed out — skipping")
                    await interaction.followup.send(
                        f"⏱️ Upload timed out for Region {region_idx} — keeping default look.",
                        ephemeral=True,
                    )
                    region_choices[region_idx] = {"action": "skip"}

            elif selected.startswith("skin:"):
                chosen_skin = selected[len("skin:") :]
                flogger.debug(f"Region {region_idx}: applying skin '{chosen_skin}'")

                # Download (or use cached) skin bytes
                if chosen_skin not in skin_cache:
                    downloaded = await self._download_skin_image(interaction, "", chosen_skin, render_info)
                    if downloaded is None:
                        # Error already reported; treat as skip
                        flogger.warning(f"Failed to download skin '{chosen_skin}' for region {region_idx} — skipping")
                        region_choices[region_idx] = {"action": "skip"}
                        continue
                    skin_cache[chosen_skin] = downloaded

                region_choices[region_idx] = {"action": "skin", "bytes": skin_cache[chosen_skin]}
                await interaction.followup.send(f"✅ Region {region_idx} → {chosen_skin}", ephemeral=True)

        return region_choices if region_choices else None

    async def _composite_textures_multiregion(
        self,
        interaction: discord.Interaction,
        ship: str,
        ship_path: str,
        diffuse_path: str,
        region_choices: dict[int, dict],
    ) -> bytes | None:
        """Call blender-service composite endpoint for per-region customization.

        Uses the ship's diffuse texture (on disk) as the base and applies
        per-region textures at the specified mask indices.
        Returns composited PNG bytes or None on error.
        """
        await interaction.followup.send("🔧 Compositing textures…")

        data: dict[str, str] = {
            "ship_path": ship_path,
            "base_texture_path": diffuse_path,
            "square_mode": "none",
            "disabled_regions": "",
        }

        files: list[tuple] = []
        active_regions: list[int] = []

        for region_idx, choice in sorted(region_choices.items()):
            action = choice.get("action")
            if action in ("skin", "upload"):
                tex_bytes = choice.get("bytes")
                if tex_bytes:
                    files.append(("region_textures", (f"region_{region_idx}.png", tex_bytes, "image/png")))
                    active_regions.append(region_idx)

        data["region_indices"] = ",".join(str(i) for i in active_regions)

        try:
            resp = await self.blender_client.post(
                "/textures/composite",
                files=files if files else None,
                data=data,
            )
            resp.raise_for_status()
            flogger.info(f"Multi-region composite successful for {ship} (regions: {active_regions})")
            return resp.content
        except HttpxHTTPStatusError as e:
            flogger.error(f"Multi-region composite API error for {ship}: {e.response.status_code}")
            await interaction.followup.send(
                f"❌ Texture compositing failed: API error {e.response.status_code}",
                ephemeral=True,
            )
            return None
        except HttpxTimeoutException:
            flogger.error(f"Multi-region composite timed out for {ship}")
            await interaction.followup.send("❌ Compositing timed out. Please try again.", ephemeral=True)
            return None
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Multi-region composite error for {ship}: {e}")
            await interaction.followup.send("❌ Texture compositing failed. Please try again later.", ephemeral=True)
            return None

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
        image="Optional: upload a custom skin image (overrides skin selection)",
        autoskin="Automatically apply the default skin without prompting",
    )
    @app_commands.autocomplete(ship=skinnable_ship_autocomplete, skin=skin_autocomplete)
    async def render_skin(
        self,
        interaction: discord.Interaction,
        ship: str,
        skin: str = "Default",
        image: discord.Attachment | None = None,
        autoskin: bool = False,
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
            await interaction.followup.send(f"❌ No base texture found for **{ship}**.", ephemeral=True)
            return

        # 2. Resolve the skin overlay image
        skin_bytes: bytes | None = None
        square_mode: str = "none"
        if image is not None:
            # Custom upload takes priority over pre-made skin selection
            try:
                skin_bytes = await image.read()
                flogger.debug(f"Custom skin image uploaded for {ship}: {len(skin_bytes)} bytes")
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.error(f"Failed to read uploaded image for {ship}: {e}")
                await interaction.followup.send("❌ Failed to read the uploaded image.", ephemeral=True)
                return

            # Check if the image is non-square and prompt user for correction
            width = getattr(image, "width", None)
            height = getattr(image, "height", None)
            if width and height and width != height:
                view = SquareCheckView(timeout=60)
                await interaction.followup.send(
                    f"Your image is **{width}x{height}** (not square). "
                    "Ship textures require a square image. Choose how to handle it:",
                    view=view,
                )
                await view.wait()
                if view.result is None:
                    await interaction.followup.send("❌ Render cancelled.", ephemeral=True)
                    return
                square_mode = view.result
        elif skin != "Default":
            skin_bytes = await self._download_skin_image(interaction, ship, skin, render_info)
            if skin_bytes is None:
                return  # error already sent

        # 2b. Resolve region mode for multi-region ships
        skin_name_for_region = skin if skin != "Default" else None
        region_mode = await self._resolve_region_mode(
            interaction, render_info, skin_bytes, skin_name_for_region, image is not None
        )
        if region_mode is None:
            return  # cancelled

        if region_mode == "custom":
            compatible_skins = render_info.get("compatible_skins") or {}
            region_choices = await self._collect_per_region_choices(
                interaction, render_info, skin_name_for_region, skin_bytes, compatible_skins
            )
            if region_choices is None:
                await interaction.followup.send("❌ Region customization cancelled.", ephemeral=True)
                return
            composite_bytes = await self._composite_textures_multiregion(
                interaction, ship, ship_path, diffuse_path, region_choices
            )
        else:
            # "all" mode — existing behavior (skin as base texture, or no skin for default render)
            composite_bytes = await self._composite_textures(
                interaction, ship, ship_path, diffuse_path, skin_bytes, square_mode
            )

        if composite_bytes is None:
            return

        # 4. Render via blender-service
        await interaction.followup.send("🎨 Rendering your ship… this may take a moment.")
        try:
            render_resp = await self.blender_client.post(
                "/render/",
                files={"texture": ("composite.png", composite_bytes, "image/png")},
                data={
                    "model_path": model_path,
                    "res_x": "3840",
                    "res_y": "2160",
                    "num_samples": "128",
                },
            )
            render_resp.raise_for_status()
            rendered_bytes = render_resp.content
        except HttpxHTTPStatusError as e:
            flogger.error(f"Render API error for {ship}: {e.response.status_code}")
            await interaction.followup.send(f"❌ Render failed: API error {e.response.status_code}", ephemeral=True)
            return
        except HttpxTimeoutException:
            flogger.error(f"Render timed out for {ship}")
            await interaction.followup.send("❌ Render timed out. Please try again later.", ephemeral=True)
            return
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Render error for {ship}: {e}")
            await interaction.followup.send("❌ Render failed. Please try again later.", ephemeral=True)
            return

        # 5. Send rendered image + format buttons
        file = discord.File(BytesIO(rendered_bytes), filename=f"{ship}_render.png")
        view = FormatDownloadView(self, composite_bytes, ship, render_bytes=rendered_bytes)
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

        # 2. Collect base texture: from disk path if available, otherwise prompt user upload
        if not diffuse_path:
            flogger.info(f"No diffuse_path for {ship}; requesting user texture upload")
            base_bytes, square_mode = await self._collect_base_texture(interaction, ship)
            if base_bytes is None:
                return  # user cancelled or timed out
            composite_bytes = await self._composite_textures_with_upload(
                interaction, ship, ship_path, base_bytes, square_mode
            )
        else:
            # 2a. Resolve the skin overlay image (if not Default)
            skin_bytes: bytes | None = None
            if skin != "Default":
                skin_bytes = await self._download_skin_image(interaction, ship, skin, render_info)
                if skin_bytes is None:
                    return  # error already sent

            # 2b. Resolve region mode for multi-region ships
            skin_name_for_region = skin if skin != "Default" else None
            region_mode = await self._resolve_region_mode(
                interaction, render_info, skin_bytes, skin_name_for_region, False
            )
            if region_mode is None:
                return  # cancelled

            if region_mode == "custom":
                compatible_skins = render_info.get("compatible_skins") or {}
                region_choices = await self._collect_per_region_choices(
                    interaction, render_info, skin_name_for_region, skin_bytes, compatible_skins
                )
                if region_choices is None:
                    await interaction.followup.send("❌ Region customization cancelled.", ephemeral=True)
                    return
                composite_bytes = await self._composite_textures_multiregion(
                    interaction, ship, ship_path, diffuse_path, region_choices
                )
            else:
                # "all" mode — existing behavior
                composite_bytes = await self._composite_textures(interaction, ship, ship_path, diffuse_path, skin_bytes)

        if composite_bytes is None:
            return

        # 3. Return composited texture + format buttons
        file = discord.File(BytesIO(composite_bytes), filename=f"{ship}_texture.png")
        view = FormatDownloadView(self, composite_bytes, ship)
        await interaction.followup.send(
            f"🎨 Here's your composited **{ship}** skin texture!",
            file=file,
            view=view,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_render_info(self, interaction: discord.Interaction, ship: str) -> dict | None:
        """Fetch render-info for a ship. Sends error and returns None on failure."""
        try:
            resp = await self.http_client.get(f"{api_base}/about/ships/{ship}/render-info", timeout=10)
            if resp.status_code == 404:
                await interaction.followup.send(f"❌ Ship **{ship}** not found.", ephemeral=True)
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
            await interaction.followup.send("❌ Timed out fetching ship info. Please try again.", ephemeral=True)
            return None
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Unexpected error fetching render-info for {ship}: {e}")
            await interaction.followup.send("⚠️ Unexpected error fetching ship info.", ephemeral=True)
            return None

        if not data.get("skinnable", False):
            await interaction.followup.send(f"❌ **{ship}** does not support custom skins.", ephemeral=True)
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
            await interaction.followup.send(f"❌ Skin **{skin}** not found for **{ship}**.", ephemeral=True)
            return None

        try:
            resp = await self.http_client.get(skin_url, timeout=30)
            resp.raise_for_status()
            flogger.debug(f"Downloaded skin image for {ship}/{skin}: {len(resp.content)} bytes")
            return resp.content
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Failed to download skin image for {ship}/{skin}: {e}")
            await interaction.followup.send(f"❌ Failed to download skin image for **{skin}**.", ephemeral=True)
            return None

    async def _composite_textures_with_upload(
        self,
        interaction: discord.Interaction,
        ship: str,
        ship_path: str,
        base_bytes: bytes,
        square_mode: str = "none",
    ) -> bytes | None:
        """Call blender-service composite endpoint with a user-uploaded base texture.

        Used when no diffuse_path is available on disk.
        Returns composited PNG bytes or None on error.
        """
        await interaction.followup.send("🔧 Compositing textures…")

        files: list[tuple] = [
            ("base_texture", ("base.png", base_bytes, "image/png")),
        ]
        data: dict[str, str] = {
            "ship_path": ship_path,
            "square_mode": square_mode,
            "region_indices": "",
            "disabled_regions": "",
        }

        try:
            resp = await self.blender_client.post(
                "/textures/composite",
                files=files,
                data=data,
            )
            resp.raise_for_status()
            flogger.info(f"Composite (upload) successful for {ship}")
            return resp.content
        except HttpxHTTPStatusError as e:
            flogger.error(f"Composite API error for {ship}: {e.response.status_code}")
            await interaction.followup.send(
                f"❌ Texture compositing failed: API error {e.response.status_code}",
                ephemeral=True,
            )
            return None
        except HttpxTimeoutException:
            flogger.error(f"Composite timed out for {ship}")
            await interaction.followup.send("❌ Compositing timed out. Please try again.", ephemeral=True)
            return None
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Composite error for {ship}: {e}")
            await interaction.followup.send("❌ Texture compositing failed. Please try again later.", ephemeral=True)
            return None

    async def _composite_textures(
        self,
        interaction: discord.Interaction,
        ship: str,
        ship_path: str,
        diffuse_path: str,
        skin_bytes: bytes | None = None,
        square_mode: str = "none",
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
            "square_mode": square_mode,
            "region_indices": "",
            "disabled_regions": "",
        }

        # If a skin was provided, send it as the base texture (replaces the diffuse BMP entirely)
        if skin_bytes is not None:
            files.append(("base_texture", ("base_skin.png", skin_bytes, "image/png")))

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
            flogger.error(f"Composite API error for {ship}: {e.response.status_code}")
            await interaction.followup.send(
                f"❌ Texture compositing failed: API error {e.response.status_code}",
                ephemeral=True,
            )
            return None
        except HttpxTimeoutException:
            flogger.error(f"Composite timed out for {ship}")
            await interaction.followup.send("❌ Compositing timed out. Please try again.", ephemeral=True)
            return None
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Composite error for {ship}: {e}")
            await interaction.followup.send("❌ Texture compositing failed. Please try again later.", ephemeral=True)
            return None


async def setup(bot: commands.Bot):
    flogger.debug("Loading SkinsCog…")
    await bot.add_cog(SkinsCog(bot))
    flogger.info("SkinsCog loaded")
