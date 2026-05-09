"""
Unit tests for TextureCompositingService.

Uses real PIL Image objects (no mocks for PIL operations).
Each test has at most 2 mocks total (per project standard).
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image
from services.texture_compositing_service import TextureCompositingService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIZE = (64, 64)  # small images for speed


def solid_rgba(color: tuple[int, int, int, int]) -> Image.Image:
    """Create a solid 64×64 RGBA image."""
    return Image.new("RGBA", SIZE, color)


def solid_rgb(color: tuple[int, int, int]) -> Image.Image:
    """Create a solid 64×64 RGB image."""
    return Image.new("RGB", SIZE, color)


def solid_l(value: int) -> Image.Image:
    """Create a solid 64×64 grayscale (L) image."""
    return Image.new("L", SIZE, value)


def pixel_array(img: Image.Image) -> np.ndarray:
    """Return image data as a NumPy array for easy assertions."""
    return np.array(img)


# ---------------------------------------------------------------------------
# Service fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def svc() -> TextureCompositingService:
    return TextureCompositingService()


# ---------------------------------------------------------------------------
# ensure_image_mode tests
# ---------------------------------------------------------------------------


class TestEnsureImageMode:
    """Tests for TextureCompositingService.ensure_image_mode()."""

    def test_ensure_image_mode_rgba_passthrough(self):
        """RGBA image is returned unchanged (same object)."""
        img = solid_rgba((255, 0, 0, 255))
        result = TextureCompositingService.ensure_image_mode(img, "RGBA")
        assert result is img
        assert result.mode == "RGBA"

    def test_ensure_image_mode_rgb_to_rgba(self):
        """RGB image is converted to RGBA."""
        img = solid_rgb((0, 255, 0))
        result = TextureCompositingService.ensure_image_mode(img, "RGBA")
        assert result.mode == "RGBA"
        assert result.size == SIZE

    def test_ensure_image_mode_l_to_rgba(self):
        """Grayscale (L) image is converted to RGBA."""
        img = solid_l(128)
        result = TextureCompositingService.ensure_image_mode(img, "RGBA")
        assert result.mode == "RGBA"
        assert result.size == SIZE

    def test_ensure_image_mode_to_l(self):
        """Image can also be converted to L (grayscale)."""
        img = solid_rgb((200, 200, 200))
        result = TextureCompositingService.ensure_image_mode(img, "L")
        assert result.mode == "L"
        assert result.size == SIZE

    def test_ensure_image_mode_l_passthrough(self):
        """L image stays L when L is requested."""
        img = solid_l(77)
        result = TextureCompositingService.ensure_image_mode(img, "L")
        assert result is img


# ---------------------------------------------------------------------------
# composite_textures tests
# ---------------------------------------------------------------------------


class TestCompositeTextures:
    """Tests for TextureCompositingService.composite_textures()."""

    def test_composite_no_regions(self, svc: TextureCompositingService):
        """Base + skinBase only, no region masks — result is RGB."""
        base = solid_rgba((255, 0, 0, 255))  # fully opaque red
        skin_base = solid_rgba((0, 0, 255, 0))  # fully transparent blue (won't change result)

        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={},
            region_masks={},
            disabled_regions=None,
        )

        assert result.mode == "RGB"
        assert result.size == SIZE

    def test_composite_returns_rgb(self, svc: TextureCompositingService):
        """Result is always RGB mode, never RGBA."""
        base = solid_rgba((10, 20, 30, 255))
        skin_base = solid_rgba((0, 0, 0, 0))

        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={},
            region_masks={},
        )

        assert result.mode == "RGB", f"Expected RGB, got {result.mode}"

    def test_composite_one_region(self, svc: TextureCompositingService):
        """Base + skinBase + 1 region texture with a fully-opaque mask.

        PIL Image.composite(image1, image2, mask) semantics:
        - mask=white(255) → selects image1 (working_tex)
        - mask=black(0)   → selects image2 (new_tex)
        Legacy pipeline inverts the raw mask first.
        So raw_mask=white(255) → inverted=black(0) → new_tex is applied.
        """
        base = solid_rgba((255, 0, 0, 255))  # red
        skin_base = solid_rgba((0, 0, 0, 0))  # transparent
        region_tex = solid_rgba((0, 255, 0, 255))  # green
        # Raw white mask (255): inverted → black (0) → composite selects image2 (new_tex = green)
        mask = solid_l(255)  # all-white; inverted → all-black → new_tex fully applied

        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={1: region_tex},
            region_masks={1: mask},
        )

        assert result.mode == "RGB"
        arr = pixel_array(result)
        # The region should be green (0, 255, 0) after full new_tex replacement
        assert arr[0, 0, 1] == 255, "Green channel should be 255 (region texture applied)"
        assert arr[0, 0, 0] == 0, "Red channel should be 0"

    def test_composite_two_regions(self, svc: TextureCompositingService):
        """Base + skinBase + 2 region textures both with full masks.

        Raw white masks (255) → inverted=black(0) → new_tex is fully applied.
        Region 2 (blue) applied last, so blue wins.
        """
        base = solid_rgba((255, 0, 0, 255))  # red
        skin_base = solid_rgba((0, 0, 0, 0))  # transparent
        region_tex_1 = solid_rgba((0, 255, 0, 255))  # green
        region_tex_2 = solid_rgba((0, 0, 255, 255))  # blue
        mask_1 = solid_l(255)  # raw white → inverted=black → new_tex (green) fully applied
        mask_2 = solid_l(255)  # raw white → inverted=black → new_tex (blue) fully applied

        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={1: region_tex_1, 2: region_tex_2},
            region_masks={1: mask_1, 2: mask_2},
        )

        assert result.mode == "RGB"
        arr = pixel_array(result)
        # Region 2 (blue) is applied last, so it wins
        assert arr[0, 0, 2] == 255, "Blue channel should be 255 (region 2 applied last)"

    def test_composite_disabled_region(self, svc: TextureCompositingService):
        """Disabled region applies base_texture through the mask.

        Start with green working_tex (via skinBase), then disable region 1
        with a full (white) mask → base_texture (red) replaces it.
        """
        base = solid_rgba((255, 0, 0, 255))  # red
        # Use opaque green skinBase to set working_tex to green after step 2
        skin_base = solid_rgba((0, 255, 0, 255))  # fully opaque green
        # Raw white mask (255) → inverted=black(0) → new_tex (=base=red) fully applied
        mask = solid_l(255)

        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={},
            region_masks={1: mask},
            disabled_regions=[1],
        )

        assert result.mode == "RGB"
        arr = pixel_array(result)
        # Disabled region: base_texture (red) applied via mask → should be red
        assert arr[0, 0, 0] == 255, "Red channel should be 255 (disabled region = base)"
        assert arr[0, 0, 1] == 0, "Green channel should be 0"

    def test_composite_mixed_regions(self, svc: TextureCompositingService):
        """Some skinned, some disabled, some skipped — all handled correctly.

        Region 1: disabled (revert to base=red) — white mask (→ inverted=black → base applied)
        Region 2: skinned blue — white mask (→ inverted=black → blue applied)
        Region 3: skipped (no texture, not disabled)
        Region 2 applied last → blue wins.
        """
        base = solid_rgba((255, 0, 0, 255))  # red
        skin_base = solid_rgba((0, 0, 0, 0))  # transparent
        region_tex_2 = solid_rgba((0, 0, 255, 255))  # blue for region 2
        mask_1 = solid_l(255)  # raw white → inverted=black → new_tex (=base=red) applied
        mask_2 = solid_l(255)  # raw white → inverted=black → new_tex (=blue) applied
        # Region 3 has no texture and is not disabled → should be skipped

        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={2: region_tex_2},
            region_masks={1: mask_1, 2: mask_2},
            disabled_regions=[1],
        )

        assert result.mode == "RGB"
        # After: region 1 disabled (red via mask), region 2 blue via mask
        # Region 2 applied last → blue wins
        arr = pixel_array(result)
        assert arr[0, 0, 2] == 255, "Blue channel should be 255 (region 2 = blue)"

    def test_composite_missing_mask_skipped(self, svc: TextureCompositingService):
        """Region without a matching mask is skipped gracefully (no exception)."""
        base = solid_rgba((255, 0, 0, 255))  # red
        skin_base = solid_rgba((0, 0, 0, 0))  # transparent
        region_tex_1 = solid_rgba((0, 255, 0, 255))  # green for region 1

        # region_textures has index 1, but region_masks is empty → should skip silently
        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={1: region_tex_1},
            region_masks={},  # no masks → region 1 skipped
        )

        assert result.mode == "RGB"
        arr = pixel_array(result)
        # Region was skipped (no mask) so result should still be red (base + transparent skin)
        # base=red, skin_base=transparent → working_tex stays red; region 1 skipped (no mask)
        assert arr[0, 0, 0] == 255, "Red should remain (region skipped — no mask)"
        assert arr[0, 0, 1] == 0, "Green should be 0 (region texture not applied)"

    def test_composite_skin_base_applied(self, svc: TextureCompositingService):
        """skinBase is alpha-composited on top of base_texture."""
        base = solid_rgba((255, 0, 0, 255))  # fully opaque red
        skin_base = solid_rgba((0, 255, 0, 255))  # fully opaque green — replaces base

        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={},
            region_masks={},
        )

        assert result.mode == "RGB"
        arr = pixel_array(result)
        # Fully opaque green skinBase should replace red base
        assert arr[0, 0, 1] == 255, "Green channel should be 255 (skinBase replaced base)"
        assert arr[0, 0, 0] == 0, "Red channel should be 0"

    def test_composite_mask_inversion(self, svc: TextureCompositingService):
        """Mask inversion is correct: black mask (0) → after invert becomes white (255) → keeps working_tex.

        PIL Image.composite(image1, image2, mask):
        - mask=white(255) → selects image1 (working_tex)
        - mask=black(0)   → selects image2 (new_tex)

        So raw_mask=black(0) → inverted=white(255) → composite keeps working_tex (red).
        This confirms the Gimp↔Pillow inversion is applied correctly.
        """
        base = solid_rgba((255, 0, 0, 255))  # red
        skin_base = solid_rgba((0, 0, 0, 0))  # transparent
        region_tex = solid_rgba((0, 255, 0, 255))  # green

        # Black raw mask (0): after ImageOps.invert → white (255)
        # Image.composite(working_tex=red, new_tex=green, mask=white) → selects image1 = red
        black_mask = solid_l(0)
        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={1: region_tex},
            region_masks={1: black_mask},
        )

        arr = pixel_array(result)
        # Black raw mask → inverted to white → composite selects image1 (working_tex = red)
        assert arr[0, 0, 0] == 255, "Red (working_tex) should be selected when raw_mask=black (inverted→white)"
        assert arr[0, 0, 1] == 0, "Green (region_tex) should NOT be selected"

    def test_composite_rgb_base_converted(self, svc: TextureCompositingService):
        """RGB base_texture is accepted and auto-converted to RGBA internally."""
        base = solid_rgb((255, 0, 0))  # RGB (not RGBA)
        skin_base = solid_rgba((0, 0, 0, 0))  # transparent

        # Should not raise even though base is RGB
        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={},
            region_masks={},
        )

        assert result.mode == "RGB"
        assert result.size == SIZE

    def test_composite_disabled_regions_none_default(self, svc: TextureCompositingService):
        """disabled_regions=None (default) is treated as empty list, no errors."""
        base = solid_rgba((0, 128, 0, 255))
        skin_base = solid_rgba((0, 0, 0, 0))

        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={},
            region_masks={},
            disabled_regions=None,
        )

        assert result.mode == "RGB"
        assert result.size == SIZE

    def test_composite_resizes_mismatched_region_texture(self, svc: TextureCompositingService):
        """Region texture smaller than base is resized to match; output keeps base size."""
        base_size = (128, 128)
        small_size = (32, 16)

        base = Image.new("RGBA", base_size, (255, 0, 0, 255))
        skin_base = Image.new("RGBA", base_size, (0, 0, 0, 0))
        region_tex = Image.new("RGBA", small_size, (0, 255, 0, 255))
        mask = Image.new("L", base_size, 255)  # all-white → inverted to black → new_tex applied

        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={1: region_tex},
            region_masks={1: mask},
        )

        assert result.mode == "RGB"
        assert result.size == base_size, "Output must keep the base texture size"

    def test_composite_resizes_mismatched_mask(self, svc: TextureCompositingService):
        """Mask smaller than base is resized to match; compositing succeeds."""
        base_size = (128, 128)
        mask_size = (64, 64)

        base = Image.new("RGBA", base_size, (255, 0, 0, 255))
        skin_base = Image.new("RGBA", base_size, (0, 0, 0, 0))
        region_tex = Image.new("RGBA", base_size, (0, 0, 255, 255))
        mask = Image.new("L", mask_size, 255)

        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={1: region_tex},
            region_masks={1: mask},
        )

        assert result.mode == "RGB"
        assert result.size == base_size

    def test_composite_resizes_mismatched_skin_base(self, svc: TextureCompositingService):
        """skinBase larger than base_texture is resized to match before alpha_composite.

        This prevents the 'images do not match' PIL error that occurs when an uploaded
        skin image (e.g. 512x512) is composited against a 2048x2048 skinBase.png from
        the ship assets directory.
        """
        base_size = (64, 64)
        skin_base_size = (256, 256)  # different size — must be resized before compositing

        base = Image.new("RGBA", base_size, (200, 30, 30, 255))  # opaque red
        # skinBase is larger than base_texture — should be resized, not cause a crash
        skin_base = Image.new("RGBA", skin_base_size, (0, 0, 0, 0))  # fully transparent (won't change pixel values)

        # Must not raise "images do not match"
        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={},
            region_masks={},
        )

        assert result.mode == "RGB"
        assert result.size == base_size, "Output must keep the base_texture size"

    def test_composite_resizes_skin_base_smaller_than_base(self, svc: TextureCompositingService):
        """skinBase smaller than base_texture is also resized upward to match."""
        base_size = (256, 256)
        skin_base_size = (64, 64)

        base = Image.new("RGBA", base_size, (0, 200, 0, 255))  # opaque green
        skin_base = Image.new("RGBA", skin_base_size, (0, 0, 0, 0))  # transparent

        result = svc.composite_textures(
            base_texture=base,
            skin_base=skin_base,
            region_textures={},
            region_masks={},
        )

        assert result.mode == "RGB"
        assert result.size == base_size
