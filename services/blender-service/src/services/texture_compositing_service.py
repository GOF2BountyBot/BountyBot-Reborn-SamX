"""
Texture compositing service for blender-service.

Ports the legacy compositeTextures() function to a clean service class
that operates on PIL Image objects rather than file paths.
"""

from __future__ import annotations

from PIL import Image, ImageOps
from shared import bblogger

flogger = bblogger.get_logger("blender-texture-compositing-service")


class TextureCompositingService:
    """Service for compositing ship textures using PIL/Pillow.

    Ports the legacy compositeTextures() algorithm from the original
    shipRenderer.py into a stateless service class that accepts PIL
    Image objects directly (file I/O is handled by the router layer).
    """

    @staticmethod
    def ensure_image_mode(image: Image.Image, mode: str = "RGBA") -> Image.Image:
        """Ensure image is in the specified mode, converting if needed.

        Ports ensureImageMode() from the legacy shipRenderer.py.
        Reference: https://pillow.readthedocs.io/en/stable/handbook/concepts.html#concept-modes

        :param Image.Image image: The image whose mode to check.
        :param str mode: The mode to ensure, converting if needed. Defaults to "RGBA".
        :return: image unchanged if already in mode, otherwise image.convert(mode).
        :rtype: Image.Image
        """
        return image if image.mode == mode else image.convert(mode)

    def composite_textures(
        self,
        base_texture: Image.Image,
        skin_base: Image.Image,
        region_textures: dict[int, Image.Image],
        region_masks: dict[int, Image.Image],
        disabled_regions: list[int] | None = None,
    ) -> Image.Image:
        """Composite textures according to the legacy compositeTextures() algorithm.

        Algorithm:
        1. Start with base_texture (region 0 / underlayer) in RGBA mode.
        2. Alpha-composite skinBase.png on top.
        3. For each region mask (1 to N):
           - If region has a texture in region_textures: apply it using the mask.
           - If region is in disabled_regions: apply base_texture using the mask.
           - Otherwise: skip (leave region as-is).
        4. Masks are INVERTED (Gimp uses opposite convention to Pillow).
        5. Return final image converted to RGB (no alpha channel).

        :param Image.Image base_texture: The underlayer (region 0) in RGBA mode.
        :param Image.Image skin_base: The skinBase.png from ship assets.
        :param dict[int, Image.Image] region_textures: Mapping of mask index → texture image.
        :param dict[int, Image.Image] region_masks: Mapping of mask index → mask image (grayscale).
        :param list[int] | None disabled_regions: Region indices to set to base_texture. Defaults to None.
        :return: Composited image in RGB mode.
        :rtype: Image.Image
        """
        if disabled_regions is None:
            disabled_regions = []

        flogger.debug(
            f"composite_textures called: region_textures={list(region_textures.keys())}, "
            f"region_masks={list(region_masks.keys())}, disabled_regions={disabled_regions}"
        )

        # Step 1: Start with base_texture in RGBA mode
        working_tex = self.ensure_image_mode(base_texture)
        flogger.debug(f"Working texture initialised: size={working_tex.size}, mode={working_tex.mode}")

        # Step 2: Alpha-composite skinBase on top of the underlayer
        skin_base_rgba = self.ensure_image_mode(skin_base)
        working_tex = Image.alpha_composite(working_tex, skin_base_rgba)
        flogger.debug("skinBase composited onto underlayer")

        # Step 3: Determine the max region index to iterate up to
        all_indices = list(region_textures.keys()) + disabled_regions
        if not all_indices:
            flogger.debug("No region textures or disabled regions — returning base + skinBase")
            return working_tex.convert("RGB")

        max_layer_num = max(all_indices)
        flogger.debug(f"Iterating mask regions 1..{max_layer_num}")

        target_size = working_tex.size

        for mask_num in range(1, max_layer_num + 1):
            if mask_num in region_textures:
                # Apply the region's custom texture
                new_tex = self.ensure_image_mode(region_textures[mask_num])
                flogger.debug(f"Region {mask_num}: applying region texture")
            elif mask_num in disabled_regions:
                # Disabled region: revert to base_texture
                new_tex = self.ensure_image_mode(base_texture)
                flogger.debug(f"Region {mask_num}: applying base_texture (disabled)")
            else:
                # Neither skinned nor disabled — skip
                flogger.debug(f"Region {mask_num}: skipping (not in textures or disabled)")
                continue

            # Check that a mask exists for this region
            if mask_num not in region_masks:
                flogger.warning(
                    f"Region {mask_num}: no mask provided — skipping "
                    f"({'render' if mask_num in region_textures else 'disable'} requested)"
                )
                continue

            # Image.composite requires all three images to be the same size.
            # Resize the region texture and mask to match the working texture
            # if they differ (e.g. a small skin overlay applied to a 2048×2048 base).
            if new_tex.size != target_size:
                flogger.debug(f"Region {mask_num}: resizing region texture {new_tex.size} → {target_size}")
                new_tex = new_tex.resize(target_size, Image.LANCZOS)

            # Invert mask: Gimp and Pillow use opposite conventions for opacity
            raw_mask = region_masks[mask_num]
            # Ensure mask is in RGB/L mode before inverting (invert does not support RGBA)
            if raw_mask.mode == "RGBA":
                raw_mask = raw_mask.convert("RGB")
            mask = self.ensure_image_mode(ImageOps.invert(raw_mask), "L")

            if mask.size != target_size:
                flogger.debug(f"Region {mask_num}: resizing mask {mask.size} → {target_size}")
                mask = mask.resize(target_size, Image.LANCZOS)

            flogger.debug(f"Region {mask_num}: mask inverted, applying composite")

            # Apply texture through mask
            working_tex = Image.composite(working_tex, new_tex, mask)

        # Step 5: Return final image as RGB (strip alpha)
        result = working_tex.convert("RGB")
        flogger.info(f"composite_textures complete: result size={result.size}, mode={result.mode}")
        return result
