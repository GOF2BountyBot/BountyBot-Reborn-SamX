"""
Tests for AEIConversionService.

Uses ≤2 mocks per test. AEPi library calls are mocked at the module
attribute level (services.aei_conversion_service.AEI / CompressionFormat)
to avoid requiring the native codec libraries at test time.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

# Ensure src/ is importable (conftest.py handles this globally, but be
# explicit for IDE/standalone runs)
_SRC = str(Path(__file__).resolve().parents[2] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from services.aei_conversion_service import (
    SUPPORTED_FORMATS,
    AEIConversionError,
    AEIConversionService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rgba_image(width: int = 4, height: int = 4) -> Image.Image:
    """Return a minimal RGBA PIL image."""
    return Image.new("RGBA", (width, height), (255, 0, 128, 200))


def _rgb_image(width: int = 4, height: int = 4) -> Image.Image:
    """Return a minimal RGB PIL image."""
    return Image.new("RGB", (width, height), (128, 64, 32))


def _mock_aei_write_returns(data: bytes = b"FAKE_AEI_BINARY") -> io.BytesIO:
    """Return a BytesIO that mock AEI().write() should return."""
    buf = io.BytesIO(data)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Tests — no mocks needed
# ---------------------------------------------------------------------------


def test_supported_formats_contains_etc1_dxt5() -> None:
    """SUPPORTED_FORMATS must include 'etc1' and 'dxt5' (and 'dxt1')."""
    assert "etc1" in SUPPORTED_FORMATS
    assert "dxt5" in SUPPORTED_FORMATS
    assert "dxt1" in SUPPORTED_FORMATS
    # Values should be the CompressionFormat attribute names
    assert SUPPORTED_FORMATS["etc1"] == "ETC1"
    assert SUPPORTED_FORMATS["dxt5"] == "DXT5"
    assert SUPPORTED_FORMATS["dxt1"] == "DXT1"


def test_convert_invalid_format_raises() -> None:
    """Passing an unsupported format string must raise AEIConversionError."""
    service = AEIConversionService()
    img = _rgba_image()
    with pytest.raises(AEIConversionError, match="Unsupported format"):
        service.convert_to_aei(img, "webp")


def test_convert_invalid_quality_too_low_raises() -> None:
    """quality=0 must raise AEIConversionError."""
    service = AEIConversionService()
    img = _rgba_image()
    with pytest.raises(AEIConversionError, match="Invalid quality"):
        service.convert_to_aei(img, "dxt5", quality=0)


def test_convert_invalid_quality_too_high_raises() -> None:
    """quality=5 must raise AEIConversionError."""
    service = AEIConversionService()
    img = _rgba_image()
    with pytest.raises(AEIConversionError, match="Invalid quality"):
        service.convert_to_aei(img, "dxt5", quality=5)


# ---------------------------------------------------------------------------
# Tests — mocked AEPi (≤2 mocks each)
# ---------------------------------------------------------------------------


def test_convert_valid_quality_accepted() -> None:
    """Quality values 1, 2, 3 must all be accepted (no error raised)."""
    fake_buf = _mock_aei_write_returns()
    mock_aei_instance = MagicMock()
    mock_aei_instance.write.return_value = fake_buf

    mock_aei_cls = MagicMock(return_value=mock_aei_instance)
    mock_cf = MagicMock()
    mock_cf.DXT5 = object()

    with patch("services.aei_conversion_service.AEI", mock_aei_cls), \
         patch("services.aei_conversion_service.CompressionFormat", mock_cf):
        service = AEIConversionService()
        img = _rgba_image()
        for q in (1, 2, 3):
            result = service.convert_to_aei(img, "dxt5", quality=q)
            assert isinstance(result, io.BytesIO)


def test_convert_rgb_to_rgba_conversion() -> None:
    """An RGB image must be automatically converted to RGBA before encoding."""
    captured_images: list[Image.Image] = []

    def _capture_aei(image: Image.Image, /, **kwargs):  # type: ignore[override]
        captured_images.append(image.copy())
        inst = MagicMock()
        inst.write.return_value = _mock_aei_write_returns()
        return inst

    mock_cf = MagicMock()
    mock_cf.DXT5 = object()

    with patch("services.aei_conversion_service.AEI", side_effect=_capture_aei), \
         patch("services.aei_conversion_service.CompressionFormat", mock_cf):
        service = AEIConversionService()
        rgb_img = _rgb_image()
        service.convert_to_aei(rgb_img, "dxt5")

    assert len(captured_images) == 1
    assert captured_images[0].mode == "RGBA"


def test_convert_calls_aepi_correctly() -> None:
    """AEI() must be called with the image and correct CompressionFormat."""
    fake_buf = _mock_aei_write_returns()
    mock_aei_instance = MagicMock()
    mock_aei_instance.write.return_value = fake_buf

    mock_aei_cls = MagicMock(return_value=mock_aei_instance)

    # Build a fake CompressionFormat with a DXT5 attribute
    mock_cf = MagicMock()
    sentinel_fmt = object()
    mock_cf.DXT5 = sentinel_fmt

    with patch("services.aei_conversion_service.AEI", mock_aei_cls), \
         patch("services.aei_conversion_service.CompressionFormat", mock_cf):
        service = AEIConversionService()
        img = _rgba_image()
        service.convert_to_aei(img, "dxt5", quality=2)

    # AEI() called once with the PIL image as first positional arg
    mock_aei_cls.assert_called_once()
    call_args = mock_aei_cls.call_args
    assert call_args.args[0] is img
    assert call_args.kwargs.get("format") is sentinel_fmt
    assert call_args.kwargs.get("quality") == 2


def test_convert_returns_bytesio_seeked_to_zero() -> None:
    """convert_to_aei() must return a BytesIO object seeked to position 0."""
    payload = b"\x00AEimage\x00FAKE"
    fake_buf = io.BytesIO(payload)
    fake_buf.seek(len(payload))  # deliberately at end

    mock_aei_instance = MagicMock()
    mock_aei_instance.write.return_value = fake_buf

    mock_aei_cls = MagicMock(return_value=mock_aei_instance)
    mock_cf = MagicMock()
    mock_cf.DXT5 = object()

    with patch("services.aei_conversion_service.AEI", mock_aei_cls), \
         patch("services.aei_conversion_service.CompressionFormat", mock_cf):
        service = AEIConversionService()
        result = service.convert_to_aei(_rgba_image(), "dxt5")

    assert isinstance(result, io.BytesIO)
    assert result.tell() == 0
    assert result.read() == payload


def test_convert_handles_aepi_error() -> None:
    """If AEI().write() raises, AEIConversionError must be raised."""
    mock_aei_instance = MagicMock()
    mock_aei_instance.write.side_effect = RuntimeError("codec exploded")

    mock_aei_cls = MagicMock(return_value=mock_aei_instance)
    mock_cf = MagicMock()
    mock_cf.DXT5 = object()

    with patch("services.aei_conversion_service.AEI", mock_aei_cls), \
         patch("services.aei_conversion_service.CompressionFormat", mock_cf):
        service = AEIConversionService()
        with pytest.raises(AEIConversionError, match="AEI encoding failed"):
            service.convert_to_aei(_rgba_image(), "dxt5")
