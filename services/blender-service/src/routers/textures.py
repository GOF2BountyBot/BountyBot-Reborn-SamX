"""
Texture compositing router for the Blender service API.

Provides an endpoint that accepts multipart form uploads and returns
a composited PNG image as a streaming response.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from PIL import Image
from services.aei_conversion_service import (
    SUPPORTED_FORMATS,
    AEIConversionError,
    AEIConversionService,
)
from services.image_utils import crop_to_square, stretch_to_square
from services.texture_compositing_service import TextureCompositingService
from shared import bblogger

flogger = bblogger.get_logger("blender-textures-api-router")

router = APIRouter(
    prefix="/textures",
    tags=["textures"],
    responses={
        400: {"description": "Bad request (invalid parameters or file format)"},
        404: {"description": "Ship path or required asset not found"},
        422: {"description": "Unprocessable entity (validation error)"},
    },
)

_service = TextureCompositingService()
_aei_service = AEIConversionService()


@router.post(
    "/composite",
    summary="Composite ship textures",
    description=(
        "Accepts a base texture (region 0 underlayer), an optional set of region overlay "
        "images, and a ship asset directory path. Loads skinBase.png and mask files from "
        "the ship directory, composites all layers, and returns the result as a PNG image."
    ),
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
)
async def composite_textures(
    base_texture: UploadFile | None = File(
        default=None, description="Region 0 underlayer image (RGBA PNG recommended). "
        "If omitted, base_texture_path must be provided instead."
    ),
    base_texture_path: str = Form(
        default="", description="Absolute path to the base texture file on disk (e.g. the ship's diffuse BMP). "
        "Used when the base texture already exists on disk and does not need to be uploaded. "
        "If base_texture (file upload) is also provided, the upload takes precedence."
    ),
    ship_path: str = Form(
        ..., description="Path to the .bbship directory containing skinBase.png and maskN.jpg files"
    ),
    region_textures: list[UploadFile] = File(
        default=[], description="Optional region overlay images, indexed by region_indices"
    ),
    region_indices: str = Form(
        default="", description="Comma-separated mask index integers corresponding to region_textures"
    ),
    disabled_regions: str = Form(
        default="", description="Comma-separated mask index integers for regions to revert to base_texture"
    ),
    square_mode: str = Form(
        default="none",
        description=(
            "How to handle non-square base_texture before compositing. "
            "Accepted values: 'none' (no change), 'crop' (centre-crop to square), "
            "'stretch' (resize to square)."
        ),
    ),
) -> StreamingResponse:
    """
    Composite ship textures and return a PNG image.

    The compositing algorithm:
    1. Start with base_texture (the underlayer).
    2. Alpha-composite skinBase.png from ship_path on top.
    3. For each numbered mask found in ship_path:
       - If the corresponding region texture was uploaded: apply via mask.
       - If the region is in disabled_regions: apply base_texture via mask.
       - Otherwise: leave as-is.
    4. Return the result as RGB PNG.

    Mask files are expected at ``{ship_path}/maskN.jpg`` (e.g. mask1.jpg, mask2.jpg, ...).
    skinBase.png is expected at ``{ship_path}/skinBase.png``.
    """
    flogger.info(
        f"composite_textures request: ship_path={ship_path!r}, "
        f"region_indices={region_indices!r}, disabled_regions={disabled_regions!r}, "
        f"square_mode={square_mode!r}"
    )

    # --- Validate ship_path ---
    ship_dir = Path(ship_path)
    if not ship_dir.exists():
        flogger.error(f"Ship path not found: {ship_path}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ship path not found: {ship_path}",
        )
    if not ship_dir.is_dir():
        flogger.error(f"Ship path is not a directory: {ship_path}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ship path is not a directory: {ship_path}",
        )

    # --- Load skinBase.png ---
    skin_base_path = ship_dir / "skinBase.png"
    if not skin_base_path.exists():
        flogger.error(f"skinBase.png not found in {ship_path}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"skinBase.png not found in ship path: {ship_path}",
        )
    try:
        skin_base_img = Image.open(skin_base_path)
        skin_base_img.load()
    except Exception as exc:
        flogger.error(f"Failed to open skinBase.png: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to open skinBase.png: {exc}",
        ) from exc

    # --- Parse region_indices ---
    parsed_region_indices: list[int] = []
    if region_indices.strip():
        try:
            parsed_region_indices = [int(i.strip()) for i in region_indices.split(",") if i.strip()]
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid region_indices value: {region_indices!r} — must be comma-separated integers",
            ) from exc

    # --- Parse disabled_regions ---
    parsed_disabled: list[int] = []
    if disabled_regions.strip():
        try:
            parsed_disabled = [int(i.strip()) for i in disabled_regions.split(",") if i.strip()]
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid disabled_regions value: {disabled_regions!r} — must be comma-separated integers",
            ) from exc

    # --- Validate region_textures vs region_indices ---
    if len(region_textures) != len(parsed_region_indices):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Mismatch: {len(region_textures)} region texture file(s) uploaded "
                f"but {len(parsed_region_indices)} index/indices provided in region_indices."
            ),
        )

    # --- Validate square_mode ---
    _valid_square_modes = ("none", "crop", "stretch")
    if square_mode not in _valid_square_modes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid square_mode {square_mode!r}. "
                f"Accepted values: {list(_valid_square_modes)}"
            ),
        )

    # --- Load base_texture (from upload or disk path) ---
    base_img: Image.Image
    if base_texture is not None:
        # Prefer uploaded file when provided
        try:
            base_data = await base_texture.read()
            base_img = Image.open(BytesIO(base_data))
            flogger.debug(f"Loaded base_texture from upload: size={base_img.size}")
        except Exception as exc:
            flogger.error(f"Failed to read base_texture upload: {exc}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to read base_texture upload: {exc}",
            ) from exc
    elif base_texture_path:
        # Fall back to loading from disk (e.g. the ship's diffuse BMP)
        disk_path = Path(base_texture_path)
        if not disk_path.exists():
            flogger.error(f"base_texture_path not found: {base_texture_path}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Base texture file not found on disk: {base_texture_path}",
            )
        try:
            base_img = Image.open(disk_path)
            base_img.load()
            flogger.debug(f"Loaded base_texture from disk: {base_texture_path}, size={base_img.size}")
        except Exception as exc:
            flogger.error(f"Failed to open base_texture from disk: {exc}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to open base texture file: {exc}",
            ) from exc
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either base_texture (file upload) or base_texture_path (disk path) must be provided.",
        )

    # --- Apply square_mode to base_texture ---
    if square_mode == "crop":
        base_img = crop_to_square(base_img)
        flogger.debug(f"Applied crop_to_square → size={base_img.size}")
    elif square_mode == "stretch":
        base_img = stretch_to_square(base_img)
        flogger.debug(f"Applied stretch_to_square → size={base_img.size}")

    # --- Load region texture uploads ---
    region_tex_map: dict[int, Image.Image] = {}
    for idx, (upload, mask_idx) in enumerate(zip(region_textures, parsed_region_indices, strict=True)):
        try:
            data = await upload.read()
            img = Image.open(BytesIO(data))
            region_tex_map[mask_idx] = img
            flogger.debug(f"Loaded region texture {idx}: mask_idx={mask_idx}, size={img.size}")
        except Exception as exc:
            flogger.error(f"Failed to read region_texture[{idx}] (mask_idx={mask_idx}): {exc}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to read region_texture[{idx}] (mask_idx={mask_idx}): {exc}",
            ) from exc

    # --- Load mask files from ship_path ---
    # Determine which mask numbers we actually need
    needed_masks: set[int] = set(parsed_region_indices) | set(parsed_disabled)
    region_mask_map: dict[int, Image.Image] = {}
    for mask_num in needed_masks:
        mask_file = ship_dir / f"mask{mask_num}.jpg"
        if mask_file.exists():
            try:
                mask_img = Image.open(mask_file)
                mask_img.load()
                region_mask_map[mask_num] = mask_img
                flogger.debug(f"Loaded mask{mask_num}.jpg from {ship_path}")
            except Exception as exc:
                flogger.warning(f"Failed to open mask{mask_num}.jpg: {exc} — region will be skipped")
        else:
            flogger.warning(f"mask{mask_num}.jpg not found in {ship_path} — region {mask_num} will be skipped")

    # --- Composite ---
    try:
        result_img = _service.composite_textures(
            base_texture=base_img,
            skin_base=skin_base_img,
            region_textures=region_tex_map,
            region_masks=region_mask_map,
            disabled_regions=parsed_disabled,
        )
    except Exception as exc:
        flogger.error(f"Compositing failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Texture compositing failed: {exc}",
        ) from exc

    # --- Stream PNG response ---
    output = BytesIO()
    result_img.save(output, format="PNG")
    output.seek(0)

    flogger.info("composite_textures response: returning PNG StreamingResponse")
    return StreamingResponse(
        output,
        media_type="image/png",
        headers={"Content-Disposition": "inline; filename=composite.png"},
    )


@router.post(
    "/convert",
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
    summary="Convert PNG to AEI format",
    description=(
        "Converts a PNG image to AEI (Abyss Engine Image) format. "
        "Supports ETC1 (Android) and DXT5/DXT1 (PC) compression."
    ),
)
async def convert_to_aei(
    image: UploadFile = File(..., description="PNG image to convert"),
    format: str = Form(default="dxt5", description="Compression format: etc1, dxt5, dxt1"),
    quality: int = Form(default=3, description="Compression quality: 1 (fast) to 3 (best)"),
) -> StreamingResponse:
    """Convert a PNG image to AEI (Abyss Engine Image) binary format.

    Returns the raw AEI binary as an octet-stream download.
    """
    flogger.info(
        f"convert_to_aei request: format={format!r}, quality={quality!r}, "
        f"filename={image.filename!r}"
    )

    # --- Validate format ---
    if format.lower() not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported format {format!r}. "
                f"Supported formats: {list(SUPPORTED_FORMATS)}"
            ),
        )

    # --- Validate quality ---
    if quality not in (1, 2, 3):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid quality {quality!r}. Must be 1, 2, or 3.",
        )

    # --- Load image ---
    try:
        image_data = await image.read()
        pil_image = Image.open(BytesIO(image_data))
    except Exception as exc:
        flogger.error(f"Failed to read uploaded image: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read uploaded image: {exc}",
        ) from exc

    # --- Perform conversion ---
    try:
        aei_output = _aei_service.convert_to_aei(pil_image, format, quality)
    except AEIConversionError as exc:
        err_msg = str(exc)
        # AEPi not available → 422 Unprocessable Entity
        if "not available" in err_msg:
            flogger.warning(f"AEPi unavailable: {exc}")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=err_msg,
            ) from exc
        # Other conversion errors → 400
        flogger.error(f"AEI conversion error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg,
        ) from exc
    except Exception as exc:
        flogger.error(f"Unexpected error during AEI conversion: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AEI conversion failed: {exc}",
        ) from exc

    # Derive output filename from upload name or use default
    base_name = (image.filename or "output").rsplit(".", 1)[0]
    output_filename = f"{base_name}.aei"

    flogger.info(f"convert_to_aei response: returning AEI file {output_filename!r}")
    return StreamingResponse(
        aei_output,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{output_filename}"',
        },
    )


@router.get(
    "/health",
    summary="Textures router health check",
    description="Quick liveness check for the textures router",
    status_code=status.HTTP_200_OK,
)
async def textures_health() -> dict[str, str]:
    """Simple liveness probe for the textures router."""
    return {"status": "ok"}
