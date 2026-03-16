"""AEI (Abyss Engine Image) format conversion service.

Converts PNG/RGBA images to AEI format using the AEPi library.
Supports ETC1 (Android) and DXT5 (PC) compression formats.
"""
from __future__ import annotations

import io
import os
import sys

from PIL import Image
from shared import bblogger

flogger = bblogger.get_logger("blender-aei-conversion-service")

# ---------------------------------------------------------------------------
# AEPi import — graceful fallback if library not available
# ---------------------------------------------------------------------------
_aepi_src = os.path.join(os.path.dirname(__file__), "..", "lib", "AEPi", "src")
if os.path.isdir(_aepi_src) and _aepi_src not in sys.path:
    sys.path.insert(0, os.path.abspath(_aepi_src))

try:
    from AEPi import AEI, CompressionFormat  # type: ignore[import]
except ImportError:
    AEI = None  # type: ignore[assignment,misc]
    CompressionFormat = None  # type: ignore[assignment,misc]
    flogger.warning("AEPi not available — AEI conversion will not work")

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

# Supported format names mapping to AEPi CompressionFormat attribute names
SUPPORTED_FORMATS: dict[str, str] = {
    "etc1": "ETC1",  # Android
    "dxt5": "DXT5",  # PC
    "dxt1": "DXT1",  # PC (no alpha)
}

_VALID_QUALITY_VALUES = frozenset({1, 2, 3})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AEIConversionError(Exception):
    """Raised when AEI conversion fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AEIConversionService:
    """Service for converting images to AEI format."""

    def convert_to_aei(
        self,
        image: Image.Image,
        target_format: str,  # "etc1", "dxt5", "dxt1"
        quality: int = 3,  # 1-3, default best
    ) -> io.BytesIO:
        """Convert a PIL Image to AEI format.

        :param image: Source image (will be converted to RGBA if needed).
        :param target_format: Compression format key — one of ``SUPPORTED_FORMATS``.
        :param quality: Compression quality (1-3, where 3 is highest quality).
        :returns: BytesIO containing the AEI binary data, seeked to position 0.
        :raises AEIConversionError: If the format is unsupported, quality is
            out of range, AEPi is unavailable, or the underlying codec fails.
        """
        # --- Validate format ---
        target_format_lower = target_format.lower()
        if target_format_lower not in SUPPORTED_FORMATS:
            raise AEIConversionError(
                f"Unsupported format {target_format!r}. "
                f"Supported formats: {list(SUPPORTED_FORMATS)}"
            )

        # --- Validate quality ---
        if quality not in _VALID_QUALITY_VALUES:
            raise AEIConversionError(
                f"Invalid quality {quality!r}. Must be one of {sorted(_VALID_QUALITY_VALUES)}."
            )

        # --- Check AEPi availability ---
        if AEI is None or CompressionFormat is None:
            raise AEIConversionError(
                "AEPi library is not available. Cannot perform AEI conversion."
            )

        # --- Ensure RGBA mode ---
        if image.mode != "RGBA":
            flogger.debug(
                f"Converting image from {image.mode} to RGBA before AEI encoding"
            )
            image = image.convert("RGBA")

        # --- Resolve CompressionFormat enum member ---
        format_attr = SUPPORTED_FORMATS[target_format_lower]
        try:
            compression_format = getattr(CompressionFormat, format_attr)
        except AttributeError as exc:
            raise AEIConversionError(
                f"CompressionFormat.{format_attr} not found in AEPi — "
                "library version mismatch?"
            ) from exc

        # --- Build AEI and write ---
        flogger.info(
            f"Converting image size={image.size} to AEI "
            f"format={target_format_lower!r} quality={quality}"
        )
        try:
            aei = AEI(image, format=compression_format, quality=quality)
            output = aei.write()
        except Exception as exc:
            flogger.error(f"AEI write failed: {exc}")
            raise AEIConversionError(f"AEI encoding failed: {exc}") from exc

        # Ensure we have a BytesIO and it is seeked to 0
        if not isinstance(output, io.BytesIO):
            # AEI.write() returns BinaryIO; wrap if necessary
            data = output.read() if hasattr(output, "read") else bytes(output)
            output = io.BytesIO(data)

        output.seek(0)
        flogger.info(
            f"AEI conversion complete: output_size={output.getbuffer().nbytes} bytes"
        )
        return output
