"""
Image utility functions for blender-service.

Provides helpers for checking and adjusting image squareness,
used by Discord cogs to validate and transform user-uploaded images.
"""

from __future__ import annotations

from PIL import Image


def is_square(image: Image.Image) -> bool:
    """Return True if the image is already square (width == height).

    :param Image.Image image: The image to check.
    :return: True if width == height, False otherwise.
    :rtype: bool
    """
    width, height = image.size
    return width == height


def crop_to_square(image: Image.Image) -> Image.Image:
    """Crop a non-square image to a square by removing equal portions from each side
    of the longer dimension.

    The resulting square has side length = min(width, height).

    Cropping rules:
    - Equal amounts are removed from both sides of the longer dimension.
    - If the difference is odd, the extra pixel goes to the bottom (portrait)
      or the right (landscape).
    - A square image is returned unchanged.

    Examples:
        200x150 -> crop 25px from left + 25px from right -> 150x150
        100x201 -> crop 50px from top + 51px from bottom -> 100x100

    :param Image.Image image: The source image.
    :return: The cropped square image.
    :rtype: Image.Image
    """
    width, height = image.size
    if width == height:
        return image

    if width > height:
        # Landscape: crop left and right, keep full height
        diff = width - height
        left = diff // 2
        right = width - (diff - left)  # extra pixel goes to the right
        return image.crop((left, 0, right, height))
    else:
        # Portrait: crop top and bottom, keep full width
        diff = height - width
        top = diff // 2
        bottom = height - (diff - top)  # extra pixel goes to the bottom
        return image.crop((0, top, width, bottom))


def stretch_to_square(image: Image.Image) -> Image.Image:
    """Stretch a non-square image to a square by expanding the shorter dimension
    to match the longer one.

    The resulting square has side length = max(width, height).

    Stretching rules:
    - Only the shorter dimension is stretched; the longer stays as-is.
    - Uses PIL ``Image.LANCZOS`` resampling for quality.
    - A square image is returned unchanged.

    Examples:
        200x150 -> stretch height to 200 -> 200x200
        100x201 -> stretch width to 201 -> 201x201

    :param Image.Image image: The source image.
    :return: The stretched square image.
    :rtype: Image.Image
    """
    width, height = image.size
    if width == height:
        return image

    side = max(width, height)
    return image.resize((side, side), Image.LANCZOS)


def check_and_report_square(image: Image.Image) -> dict:
    """Return a diagnostic dict describing the squareness of an image.

    Used by Discord cogs to inform users whether their image is square and
    by how much it differs.

    Returned dict keys:
        - ``is_square`` (bool): True if width == height.
        - ``width`` (int): Image width in pixels.
        - ``height`` (int): Image height in pixels.
        - ``difference`` (int): abs(width - height).
        - ``longer_side`` (str): ``"width"``, ``"height"``, or ``"equal"``.

    :param Image.Image image: The image to inspect.
    :return: Diagnostic dictionary.
    :rtype: dict
    """
    width, height = image.size
    difference = abs(width - height)

    if width > height:
        longer_side = "width"
    elif height > width:
        longer_side = "height"
    else:
        longer_side = "equal"

    return {
        "is_square": width == height,
        "width": width,
        "height": height,
        "difference": difference,
        "longer_side": longer_side,
    }
