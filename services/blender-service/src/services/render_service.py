"""
Render service for blender-service.

Orchestrates the 3D ship render pipeline:
1. Validate render parameters.
2. Prepare a temp working directory (concurrent-safe).
3. Copy the model's MTL to the temp dir.
4. Write render_vars for the Blender script.
5. Invoke Blender asynchronously via asyncio.create_subprocess_exec().
6. Load the output PNG, trim to content, save.
7. Clean up temp files on success.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops
from shared import bblogger
from utils.safe_path import validate_user_path

from services.render_config_service import RenderConfig

flogger = bblogger.get_logger("blender-render-service")


class RenderError(Exception):
    """Raised when a Blender render fails."""


@dataclass(frozen=True)
class ClampResult:
    """Result of clamping a render request's parameters to config bounds (B.93).

    ``res_x`` / ``res_y`` / ``num_samples`` are the *final* values to render
    with — already clamped to the configured ``[min, max]`` range.

    ``clamped`` maps each adjusted field name to ``{"requested": x, "actual": y}``;
    it is empty when every parameter was already in range.
    """

    res_x: int
    res_y: int
    num_samples: int
    clamped: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def was_clamped(self) -> bool:
        """True if at least one parameter was adjusted."""
        return bool(self.clamped)


class RenderService:
    """Service for 3D ship rendering using Blender.

    Wraps the Blender subprocess call and all supporting file I/O required
    to produce a trimmed PNG of a rendered ship model.
    """

    def __init__(self, config: RenderConfig | None = None) -> None:
        self._assets_dir = Path(__file__).parent.parent / "assets"
        self._cube_blend = self._assets_dir / "cube.blend"
        self._render_script = self._assets_dir / "_render.py"
        self._blender_path = self._find_blender()

        # Accept an injected RenderConfig; fall back to defaults when not provided.
        self._config: RenderConfig = config if config is not None else RenderConfig()

        flogger.info(
            f"RenderService initialised: blender={self._blender_path!r}, "
            f"cube_blend={self._cube_blend}, render_script={self._render_script}"
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_blender(self) -> str:
        """Find the Blender executable.

        Checks common installation paths then falls back to ``blender``
        (assumes it is on ``$PATH``).

        :return: Path string for the Blender executable.
        :rtype: str
        """
        for candidate in ["/usr/bin/blender", "/usr/local/bin/blender", "blender"]:
            if Path(candidate).exists() or shutil.which(candidate):
                flogger.debug(f"Found Blender at: {candidate}")
                return candidate
        flogger.warning("Blender not found at known paths — defaulting to 'blender' on PATH")
        return "blender"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def clamp_params(self, res_x: int, res_y: int, num_samples: int) -> ClampResult:
        """Clamp render parameters to the configured bounds (B.93).

        Out-of-bounds ``res_x`` / ``res_y`` / ``num_samples`` are clamped to the
        nearest valid bound rather than rejected — a too-large or too-small
        request still produces a render, just at the closest allowed size /
        sample count. Each clamp is logged at WARNING level.

        :param int res_x: Requested render width in pixels.
        :param int res_y: Requested render height in pixels.
        :param int num_samples: Requested CYCLES samples per pixel.
        :return: A :class:`ClampResult` with the final values and a record of
            any adjustments made.
        :rtype: ClampResult
        """
        cfg = self._config
        clamped: dict[str, dict[str, int]] = {}

        def _clamp(name: str, value: int, lower: int, upper: int) -> int:
            adjusted = min(max(value, lower), upper)
            if adjusted != value:
                clamped[name] = {"requested": value, "actual": adjusted}
                flogger.warning(
                    f"clamp_params: {name}={value} out of bounds [{lower}, {upper}] — clamped to {adjusted}"
                )
            return adjusted

        final_res_x = _clamp("res_x", res_x, cfg.min_res_x, cfg.max_res_x)
        final_res_y = _clamp("res_y", res_y, cfg.min_res_y, cfg.max_res_y)
        final_samples = _clamp("num_samples", num_samples, cfg.min_samples, cfg.max_samples)

        return ClampResult(
            res_x=final_res_x,
            res_y=final_res_y,
            num_samples=final_samples,
            clamped=clamped,
        )

    @staticmethod
    def trim(image: Image.Image) -> Image.Image:
        """Crop image to content by removing transparent/background borders.

        For RGBA images (rendered with ``film_transparent = True``), uses the
        alpha channel directly to detect content.  This preserves anti-aliased
        edge pixels that would otherwise be discarded by a colour-difference
        threshold approach.

        For non-RGBA images, falls back to a colour-difference method using
        pixel (0, 0) as the background reference.

        :param Image.Image image: The image to crop.
        :return: The cropped image, or the original if no bounding box is found.
        :rtype: Image.Image
        """
        if image.mode == "RGBA":
            # Use the alpha channel: any pixel with alpha > 0 is content.
            alpha = image.split()[3]
            bbox = alpha.getbbox()
        else:
            # Fallback for RGB/other modes: difference against top-left pixel.
            bg = Image.new(image.mode, image.size, image.getpixel((0, 0)))
            diff = ImageChops.difference(image, bg)
            bbox = diff.getbbox()
        return image.crop(bbox) if bbox else image

    async def render_ship(
        self,
        model_path: str,
        texture_path: str,
        output_path: str,
        res_x: int = 1920,
        res_y: int = 1080,
        num_samples: int = 64,
    ) -> Path:
        """Render a ship model with a texture using Blender.

        Render parameters are expected to be already clamped to the configured
        bounds by the caller (the router layer calls :meth:`clamp_params` — see
        B.93). This method renders at whatever ``res_x`` / ``res_y`` /
        ``num_samples`` it is given.

        Steps:
        1. Create a temp directory under ``/tmp/blender_render_{uuid}/``.
        2. Copy the OBJ's MTL file to the temp directory.
        3. Write the render_vars file.
        4. Invoke Blender asynchronously via ``asyncio.create_subprocess_exec()``.
        5. Load the rendered PNG, trim to content, save back.
        6. Clean up the temp directory (only on success — keep on failure for debugging).

        :param str model_path: Path to the ``.obj`` file.
        :param str texture_path: Path to the composited texture (PNG/JPG).
        :param str output_path: Destination path for the rendered image.
        :param int res_x: Render width in pixels. Defaults to 1920.
        :param int res_y: Render height in pixels. Defaults to 1080.
        :param int num_samples: CYCLES samples per pixel. Defaults to 64.
        :return: Path to the rendered output image.
        :rtype: Path
        :raises RenderError: If Blender exits with a non-zero return code or
            the output file is not produced.
        """
        # Validate model_path is within the allowed data directory.
        # This is a defence-in-depth check; the router layer also validates
        # before calling this service.
        validated_obj_path = validate_user_path(model_path, description="model_path")

        render_id = str(uuid.uuid4())
        temp_dir = Path(f"/tmp/blender_render_{render_id}")
        temp_dir.mkdir(parents=True, exist_ok=True)
        flogger.info(f"[{render_id}] Render started: model={model_path!r}, temp_dir={temp_dir}")

        render_vars_path = temp_dir / "render_vars"

        # Copy the OBJ and MTL to the temp dir so that _render.py can
        # append ``map_Kd`` to the temp MTL and have Blender resolve it
        # correctly.  Blender's OBJ importer reads the ``mtllib`` directive
        # relative to the OBJ file, so both files must be co-located.
        obj_path = validated_obj_path

        temp_obj_path = temp_dir / obj_path.name
        shutil.copy2(str(obj_path), str(temp_obj_path))
        flogger.debug(f"[{render_id}] Copied OBJ {obj_path} → {temp_obj_path}")

        original_mtl = obj_path.with_suffix(".mtl")
        if not original_mtl.exists():
            # Fallback: look for material.mtl in the same directory
            original_mtl = obj_path.parent / "material.mtl"

        temp_mtl_path = temp_dir / (original_mtl.name if original_mtl.exists() else "material.mtl")
        if original_mtl.exists():
            shutil.copy2(str(original_mtl), str(temp_mtl_path))
            flogger.debug(f"[{render_id}] Copied MTL {original_mtl} → {temp_mtl_path}")
        else:
            # Create an empty MTL so _render.py can append to it
            temp_mtl_path.touch()
            flogger.warning(f"[{render_id}] Original MTL not found at {original_mtl} — created empty temp MTL")

        # Write render_vars (6-line format expected by _render.py).
        resolution_str = f"{res_x}x{res_y}"
        render_vars_content = (
            f"{resolution_str}\n{output_path}\n{temp_obj_path}\n{texture_path}\n{num_samples}\n{temp_mtl_path}\n"
        )
        render_vars_path.write_text(render_vars_content)
        flogger.debug(f"[{render_id}] render_vars written to {render_vars_path}")

        # Build the Blender command.
        cmd = [
            self._blender_path,
            "-b",
            str(self._cube_blend),
            "-P",
            str(self._render_script),
        ]
        flogger.info(f"[{render_id}] Invoking Blender: {' '.join(cmd)}")

        # Inherit the current environment and add RENDER_ARGS_PATH.
        env = {**os.environ, "RENDER_ARGS_PATH": str(render_vars_path)}

        start_time = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        elapsed = time.monotonic() - start_time

        if stdout:
            flogger.debug(f"[{render_id}] Blender stdout:\n{stdout.decode(errors='replace')}")
        if stderr:
            flogger.debug(f"[{render_id}] Blender stderr:\n{stderr.decode(errors='replace')}")

        flogger.info(f"[{render_id}] Blender exited with code {proc.returncode} in {elapsed:.1f}s")

        if proc.returncode != 0:
            flogger.error(
                f"[{render_id}] Blender failed (rc={proc.returncode}). "
                "Temp dir preserved for debugging: " + str(temp_dir)
            )
            raise RenderError(f"Blender exited with non-zero return code {proc.returncode}. See logs for details.")

        # Verify the output file was produced.
        output_file = Path(output_path)
        if not output_file.exists():
            flogger.error(f"[{render_id}] Output file not found at {output_path}. Temp dir preserved: " + str(temp_dir))
            raise RenderError(f"Blender completed but output file was not produced: {output_path}")

        # Load, trim, and save the result.
        flogger.info(f"[{render_id}] Trimming rendered image: {output_path}")
        with Image.open(output_path) as rendered_img:
            trimmed_img = self.trim(rendered_img)
            original_size = rendered_img.size
        trimmed_img.save(output_path)
        flogger.info(f"[{render_id}] Trimmed image saved: {original_size} → {trimmed_img.size}")

        # Clean up temp directory on success.
        shutil.rmtree(temp_dir, ignore_errors=True)
        flogger.info(f"[{render_id}] Temp dir cleaned up: {temp_dir}")

        return output_file
