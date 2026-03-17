"""
Image utility functions for blender-service.

Provides helpers for checking and adjusting image squareness,
used by Discord cogs to validate and transform user-uploaded images.
"""

from __future__ import annotations

from PIL import Image
from shared import bblogger

flogger = bblogger.get_logger("blender-image-utils")


def is_square(image: Image.Image) -> bool:
    """Return True if the image is already square (width == height).

    :param Image.Image image: The image to check.
    :return: True if width == height, False otherwise.
    :rtype: bool
    """
    width, height = image.size
    flogger.debug(f"is_square() entry: width={width}, height={height}")
    result = width == height
    flogger.debug(f"is_square() exit: result={result}")
    return result


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
    flogger.debug(f"crop_to_square() entry: width={width}, height={height}")

    if width == height:
        flogger.debug("crop_to_square() exit: image already square, returning unchanged")
        return image

    if width > height:
        # Landscape: crop left and right, keep full height
        diff = width - height
        left = diff // 2
        right = width - (diff - left)  # extra pixel goes to the right
        result = image.crop((left, 0, right, height))
        flogger.debug(
            f"crop_to_square() exit: landscape crop left={left} right={right} result_size={result.size}"
        )
        return result
    else:
        # Portrait: crop top and bottom, keep full width
        diff = height - width
        top = diff // 2
        bottom = height - (diff - top)  # extra pixel goes to the bottom
        result = image.crop((0, top, width, bottom))
        flogger.debug(f"crop_to_square() exit: portrait crop top={top} bottom={bottom} result_size={result.size}")
        return result


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
    flogger.debug(f"stretch_to_square() entry: width={width}, height={height}")

    if width == height:
        flogger.debug("stretch_to_square() exit: image already square, returning unchanged")
        return image

    side = max(width, height)
    result = image.resize((side, side), Image.LANCZOS)
    flogger.debug(f"stretch_to_square() exit: resized to {side}x{side} using LANCZOS resampling")
    return result


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
    flogger.debug(f"check_and_report_square() entry: width={width}, height={height}")
    difference = abs(width - height)

    if width > height:
        longer_side = "width"
    elif height > width:
        longer_side = "height"
    else:
        longer_side = "equal"

    result = {
        "is_square": width == height,
        "width": width,
        "height": height,
        "difference": difference,
        "longer_side": longer_side,
    }
    flogger.debug(
        f"check_and_report_square() exit: is_square={result['is_square']} "
        f"difference={result['difference']} longer_side={result['longer_side']}"
    )
    return result
