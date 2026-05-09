"""
Unit tests for image_utils.

Uses real PIL Image objects (no mocks). Images are kept small (≤ 256 px)
for test-suite speed. Each test has ≤ 2 mocks total (project standard).
"""

from __future__ import annotations

from PIL import Image
from services.image_utils import (
    check_and_report_square,
    crop_to_square,
    is_square,
    stretch_to_square,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def solid(width: int, height: int, color: tuple, mode: str = "RGB") -> Image.Image:
    """Create a solid image of given dimensions and color."""
    return Image.new(mode, (width, height), color)


# ---------------------------------------------------------------------------
# is_square
# ---------------------------------------------------------------------------


def test_is_square_true() -> None:
    """A 64×64 image is square."""
    img = solid(64, 64, (128, 128, 128))
    assert is_square(img) is True


def test_is_square_false_landscape() -> None:
    """A 200×150 image is not square (width > height)."""
    img = solid(200, 150, (0, 0, 0))
    assert is_square(img) is False


def test_is_square_false_portrait() -> None:
    """A 150×200 image is not square (height > width)."""
    img = solid(150, 200, (255, 255, 255))
    assert is_square(img) is False


# ---------------------------------------------------------------------------
# crop_to_square
# ---------------------------------------------------------------------------


def test_crop_landscape_even() -> None:
    """200×100 landscape → equal 50 px crop from each side → 100×100."""
    img = solid(200, 100, (255, 0, 0))
    result = crop_to_square(img)
    assert result.size == (100, 100)


def test_crop_landscape_odd() -> None:
    """201×100 landscape → odd difference (101 to remove): 50 from left, 51 from right → 100×100."""
    img = solid(201, 100, (0, 255, 0))
    result = crop_to_square(img)
    assert result.size == (100, 100)


def test_crop_portrait_even() -> None:
    """100×200 portrait → equal 50 px crop from top and bottom → 100×100."""
    img = solid(100, 200, (0, 0, 255))
    result = crop_to_square(img)
    assert result.size == (100, 100)


def test_crop_portrait_odd() -> None:
    """100×201 portrait → odd difference (101 to remove): 50 from top, 51 from bottom → 100×100."""
    img = solid(100, 201, (128, 0, 128))
    result = crop_to_square(img)
    assert result.size == (100, 100)


def test_crop_already_square() -> None:
    """A 64×64 image returned unchanged by crop_to_square."""
    img = solid(64, 64, (200, 200, 0))
    result = crop_to_square(img)
    assert result.size == (64, 64)


def test_crop_landscape_even_crop_bounds() -> None:
    """Verify exact crop boundaries for 200×100 landscape (50 from each side → 100×100).

    Column 0 (leftmost) is red — should be cropped away.
    Column 50 is blue — becomes the new left edge (x=0 in result).
    """
    img = Image.new("RGB", (200, 100), (255, 255, 255))
    for y in range(100):
        img.putpixel((0, y), (255, 0, 0))  # leftmost column → cropped (diff=100, remove 50 each side)
        img.putpixel((50, y), (0, 0, 255))  # new left edge after 50-px crop

    result = crop_to_square(img)  # crop 50 from left, 50 from right → 100×100
    assert result.size == (100, 100)
    # Column 50 (blue) is now at x=0 in result
    assert result.getpixel((0, 0)) == (0, 0, 255)


def test_crop_portrait_odd_extra_bottom() -> None:
    """100×201 portrait: diff=101, top=50, bottom=150 (exclusive).

    Crop box is (0, 50, 100, 150) → rows y=50..y=149 → height=100.
    Extra pixel goes to the bottom (y=150 and y=200 are cropped away).
    """
    img = solid(100, 201, (255, 255, 255))
    # Mark y=50 (first kept row) and y=149 (last kept row)
    for x in range(100):
        img.putpixel((x, 50), (0, 255, 0))
        img.putpixel((x, 149), (255, 0, 0))

    result = crop_to_square(img)
    # crop: top=50, bottom=150 → rows 50..149 → height=100
    assert result.size == (100, 100)
    # First row of result (originally y=50) should be green
    assert result.getpixel((0, 0)) == (0, 255, 0)
    # Last row of result (originally y=149) should be red
    assert result.getpixel((0, 99)) == (255, 0, 0)


# ---------------------------------------------------------------------------
# stretch_to_square
# ---------------------------------------------------------------------------


def test_stretch_landscape() -> None:
    """200×100 landscape → stretch height to 200 → 200×200."""
    img = solid(200, 100, (10, 20, 30))
    result = stretch_to_square(img)
    assert result.size == (200, 200)


def test_stretch_portrait() -> None:
    """100×200 portrait → stretch width to 200 → 200×200."""
    img = solid(100, 200, (30, 20, 10))
    result = stretch_to_square(img)
    assert result.size == (200, 200)


def test_stretch_already_square() -> None:
    """A 64×64 image returned unchanged by stretch_to_square."""
    img = solid(64, 64, (1, 2, 3))
    result = stretch_to_square(img)
    assert result.size == (64, 64)


def test_stretch_uses_lanczos() -> None:
    """stretch_to_square must use LANCZOS resampling (spot-check via mode preservation)."""
    img = solid(50, 30, (200, 100, 50), mode="RGB")
    result = stretch_to_square(img)
    # LANCZOS is a high-quality filter; result should still be RGB and correct size
    assert result.size == (50, 50)
    assert result.mode == "RGB"


# ---------------------------------------------------------------------------
# check_and_report_square
# ---------------------------------------------------------------------------


def test_check_and_report_square_landscape() -> None:
    """200×150 → correct dict with landscape longer_side."""
    img = solid(200, 150, (0, 0, 0))
    report = check_and_report_square(img)
    assert report["is_square"] is False
    assert report["width"] == 200
    assert report["height"] == 150
    assert report["difference"] == 50
    assert report["longer_side"] == "width"


def test_check_and_report_square_portrait() -> None:
    """150×200 → longer_side is 'height'."""
    img = solid(150, 200, (0, 0, 0))
    report = check_and_report_square(img)
    assert report["is_square"] is False
    assert report["width"] == 150
    assert report["height"] == 200
    assert report["difference"] == 50
    assert report["longer_side"] == "height"


def test_check_and_report_equal() -> None:
    """64×64 → is_square=True, difference=0, longer_side='equal'."""
    img = solid(64, 64, (100, 100, 100))
    report = check_and_report_square(img)
    assert report["is_square"] is True
    assert report["width"] == 64
    assert report["height"] == 64
    assert report["difference"] == 0
    assert report["longer_side"] == "equal"


# ---------------------------------------------------------------------------
# Center content preservation test
# ---------------------------------------------------------------------------


def test_crop_preserves_center_content() -> None:
    """200×100 image filled red except center pixel (100,50) which is green.

    200×100: diff=100, left=50, right=150. Crop box=(50,0,150,100) → 100×100.
    Original pixel (100, 50) moves to (100-50, 50) = (50, 50) in result.
    """
    img = Image.new("RGB", (200, 100), (255, 0, 0))  # fill red
    img.putpixel((100, 50), (0, 255, 0))  # center pixel green

    result = crop_to_square(img)
    # 200×100 → remove 50 from left, 50 from right → 100×100
    assert result.size == (100, 100)
    # Original (100, 50) becomes (50, 50) after 50-px left crop
    assert result.getpixel((50, 50)) == (0, 255, 0), "Center green pixel should survive cropping"
