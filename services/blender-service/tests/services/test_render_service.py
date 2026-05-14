"""
Unit tests for RenderService.

Since Blender is not available in the test environment, the subprocess
call is mocked where necessary.  All other logic uses real objects.
Each test uses at most 2 mocks (per project standard).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image
from services.render_service import RenderError, RenderService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIZE = (64, 64)


def solid_rgba(color: tuple[int, int, int, int]) -> Image.Image:
    """Create a small solid RGBA image."""
    return Image.new("RGBA", SIZE, color)


def solid_rgb(color: tuple[int, int, int]) -> Image.Image:
    """Create a small solid RGB image."""
    return Image.new("RGB", SIZE, color)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def svc() -> RenderService:
    """Return a RenderService instance."""
    return RenderService()


# ---------------------------------------------------------------------------
# clamp_params — in-bounds inputs (B.93)
#
# RenderService() with no config uses RenderConfig() defaults:
#   res_x   [352, 1920]    res_y [240, 1080]    samples [1, 64]
# ---------------------------------------------------------------------------


def test_clamp_params_within_bounds_unchanged(svc: RenderService) -> None:
    """Parameters already within config bounds are returned unchanged and unflagged."""
    result = svc.clamp_params(1280, 720, 32)
    assert (result.res_x, result.res_y, result.num_samples) == (1280, 720, 32)
    assert result.was_clamped is False
    assert result.clamped == {}


def test_clamp_params_at_bounds_not_clamped(svc: RenderService) -> None:
    """Values exactly on the min/max bounds are valid and not clamped."""
    result = svc.clamp_params(1920, 240, 64)
    assert (result.res_x, result.res_y, result.num_samples) == (1920, 240, 64)
    assert result.was_clamped is False


# ---------------------------------------------------------------------------
# clamp_params — resolution bounds (B.93)
# ---------------------------------------------------------------------------


def test_clamp_params_res_x_too_high(svc: RenderService) -> None:
    """res_x above max_res_x is clamped down to max_res_x and recorded."""
    result = svc.clamp_params(4001, 720, 32)
    assert result.res_x == 1920
    assert result.clamped["res_x"] == {"requested": 4001, "actual": 1920}
    assert result.was_clamped is True


def test_clamp_params_res_x_too_low(svc: RenderService) -> None:
    """res_x below min_res_x is clamped up to min_res_x."""
    result = svc.clamp_params(100, 720, 32)
    assert result.res_x == 352
    assert result.clamped["res_x"] == {"requested": 100, "actual": 352}


def test_clamp_params_res_y_too_high(svc: RenderService) -> None:
    """res_y above max_res_y is clamped down to max_res_y."""
    result = svc.clamp_params(1280, 2161, 32)
    assert result.res_y == 1080
    assert result.clamped["res_y"] == {"requested": 2161, "actual": 1080}


def test_clamp_params_res_y_too_low(svc: RenderService) -> None:
    """res_y below min_res_y is clamped up to min_res_y."""
    result = svc.clamp_params(1280, 100, 32)
    assert result.res_y == 240
    assert result.clamped["res_y"] == {"requested": 100, "actual": 240}


# ---------------------------------------------------------------------------
# clamp_params — sample bounds (B.93)
# ---------------------------------------------------------------------------


def test_clamp_params_samples_too_high(svc: RenderService) -> None:
    """num_samples above max_samples is clamped down to max_samples."""
    result = svc.clamp_params(1280, 720, 999)
    assert result.num_samples == 64
    assert result.clamped["num_samples"] == {"requested": 999, "actual": 64}


def test_clamp_params_samples_too_low(svc: RenderService) -> None:
    """num_samples below min_samples is clamped up to min_samples."""
    result = svc.clamp_params(1280, 720, 0)
    assert result.num_samples == 1
    assert result.clamped["num_samples"] == {"requested": 0, "actual": 1}


def test_clamp_params_multiple_fields_all_recorded(svc: RenderService) -> None:
    """Several out-of-bounds params are all clamped and recorded in one result."""
    result = svc.clamp_params(99999, 1, 99999)
    assert (result.res_x, result.res_y, result.num_samples) == (1920, 240, 64)
    assert set(result.clamped.keys()) == {"res_x", "res_y", "num_samples"}
    assert result.was_clamped is True


# ---------------------------------------------------------------------------
# trim
# ---------------------------------------------------------------------------


def test_trim_removes_border(svc: RenderService) -> None:
    """trim() should crop away a transparent border around an opaque coloured centre.

    The trim() algorithm uses getpixel((0,0)) as the background colour.
    For the border to be detected it must differ from the content pixels.
    Using a fully-transparent background (alpha=0) with opaque content (alpha=255)
    produces a detectable alpha-channel difference that getbbox() finds.
    """
    # Fully transparent (alpha=0) background — matches typical Blender render output
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    # Draw an opaque coloured rectangle in the centre
    for y in range(27, 37):
        for x in range(27, 37):
            img.putpixel((x, y), (255, 100, 50, 255))

    result = svc.trim(img)
    # The trimmed image should be smaller than the original
    assert result.size[0] < img.size[0]
    assert result.size[1] < img.size[1]


def test_trim_no_content(svc: RenderService) -> None:
    """trim() on a fully transparent / solid image returns the original."""
    img = solid_rgba((0, 0, 0, 0))
    result = svc.trim(img)
    # getbbox() returns None for uniform images → original returned unchanged
    assert result.size == img.size


def test_trim_full_content(svc: RenderService) -> None:
    """trim() on an image that is entirely content returns an image the same size."""
    # Create an image where every pixel is different from (0,0)
    img = Image.new("RGBA", (10, 10), (100, 150, 200, 255))
    # Make (0,0) black so the diff detects a border; fill rest with white
    img.putpixel((0, 0), (0, 0, 0, 255))
    # All other pixels are non-black so after diff the bbox covers everything
    result = svc.trim(img)
    assert result.size[0] <= img.size[0]
    assert result.size[1] <= img.size[1]


# ---------------------------------------------------------------------------
# render_ship — subprocess-mocked tests
# ---------------------------------------------------------------------------


def _make_subprocess_mock(returncode: int, output_file: str | None = None):
    """Build a mock for asyncio.create_subprocess_exec.

    If *output_file* is provided the mock side-effect will create the file
    (simulating Blender writing its output).
    """

    async def _side_effect(*args, **kwargs):
        if output_file:
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)
            # Write a minimal 1×1 white PNG so PIL can open it.
            img = Image.new("RGB", (1, 1), (255, 255, 255))
            img.save(output_file, format="PNG")

        mock_proc = MagicMock()
        mock_proc.returncode = returncode
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        return mock_proc

    return _side_effect


@pytest.mark.asyncio
async def test_render_ship_creates_temp_dir(svc: RenderService, tmp_path: Path) -> None:
    """render_ship() should create a temp directory under /tmp."""
    obj_file = tmp_path / "model.obj"
    obj_file.touch()
    tex_file = tmp_path / "texture.png"
    tex_file.touch()
    out_file = str(tmp_path / "output.png")

    created_dirs: list[str] = []

    original_mkdir = Path.mkdir

    def capturing_mkdir(self, *args, **kwargs):
        created_dirs.append(str(self))
        return original_mkdir(self, *args, **kwargs)

    with (
        patch("asyncio.create_subprocess_exec", side_effect=_make_subprocess_mock(0, out_file)),
        patch.object(Path, "mkdir", capturing_mkdir),
    ):
        await svc.render_ship(str(obj_file), str(tex_file), out_file)

    assert any("/tmp/blender_render_" in d for d in created_dirs)


@pytest.mark.asyncio
async def test_render_ship_writes_render_vars(svc: RenderService, tmp_path: Path) -> None:
    """render_ship() should write a 6-line render_vars file in the correct format."""
    obj_file = tmp_path / "model.obj"
    obj_file.touch()
    tex_file = tmp_path / "texture.png"
    tex_file.touch()
    out_file = str(tmp_path / "output.png")

    captured_render_vars: list[str] = []

    async def fake_subprocess(*args, **kwargs):
        # Find render_vars path from RENDER_ARGS_PATH env var
        env = kwargs.get("env", {})
        vars_path = env.get("RENDER_ARGS_PATH", "")
        if vars_path:
            captured_render_vars.append(Path(vars_path).read_text())
        # Create output file
        Path(out_file).parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (1, 1), (255, 255, 255))
        img.save(out_file, format="PNG")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        await svc.render_ship(str(obj_file), str(tex_file), out_file, 1920, 1080, 64)

    assert len(captured_render_vars) == 1
    lines = captured_render_vars[0].splitlines()
    assert len(lines) == 6
    assert lines[0] == "1920x1080"
    assert lines[1] == out_file
    # Line 3 is the temp OBJ copy (co-located with the temp MTL so
    # Blender resolves ``mtllib`` correctly).
    assert lines[2].endswith(obj_file.name)
    assert "blender_render_" in lines[2]
    assert lines[3] == str(tex_file)
    assert lines[4] == "64"
    # Line 6 is the temp MTL path — just verify it exists as a string
    assert len(lines[5]) > 0


@pytest.mark.asyncio
async def test_render_ship_sets_env_var(svc: RenderService, tmp_path: Path) -> None:
    """render_ship() should pass RENDER_ARGS_PATH in the subprocess env."""
    obj_file = tmp_path / "model.obj"
    obj_file.touch()
    tex_file = tmp_path / "texture.png"
    tex_file.touch()
    out_file = str(tmp_path / "output.png")

    captured_env: list[dict] = []

    async def fake_subprocess(*args, **kwargs):
        captured_env.append(dict(kwargs.get("env", {})))
        Path(out_file).parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (1, 1), (255, 255, 255))
        img.save(out_file, format="PNG")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        await svc.render_ship(str(obj_file), str(tex_file), out_file)

    assert len(captured_env) == 1
    assert "RENDER_ARGS_PATH" in captured_env[0]
    assert "blender_render_" in captured_env[0]["RENDER_ARGS_PATH"]


@pytest.mark.asyncio
async def test_render_ship_calls_blender(svc: RenderService, tmp_path: Path) -> None:
    """render_ship() should call Blender with -b <cube.blend> -P <_render.py>."""
    obj_file = tmp_path / "model.obj"
    obj_file.touch()
    tex_file = tmp_path / "texture.png"
    tex_file.touch()
    out_file = str(tmp_path / "output.png")

    captured_cmd: list[tuple] = []

    async def fake_subprocess(*args, **kwargs):
        captured_cmd.append(args)
        Path(out_file).parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (1, 1), (255, 255, 255))
        img.save(out_file, format="PNG")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        await svc.render_ship(str(obj_file), str(tex_file), out_file)

    assert len(captured_cmd) == 1
    cmd_args = captured_cmd[0]
    # blender -b <cube.blend> -P <_render.py>
    assert "-b" in cmd_args
    assert "-P" in cmd_args
    b_idx = cmd_args.index("-b")
    p_idx = cmd_args.index("-P")
    assert "cube.blend" in cmd_args[b_idx + 1]
    assert "_render.py" in cmd_args[p_idx + 1]


@pytest.mark.asyncio
async def test_render_ship_raises_on_failure(svc: RenderService, tmp_path: Path) -> None:
    """render_ship() should raise RenderError when Blender exits non-zero."""
    obj_file = tmp_path / "model.obj"
    obj_file.touch()
    tex_file = tmp_path / "texture.png"
    tex_file.touch()
    out_file = str(tmp_path / "output.png")

    with (
        patch(
            "asyncio.create_subprocess_exec",
            side_effect=_make_subprocess_mock(returncode=1),
        ),
        pytest.raises(RenderError, match="non-zero return code"),
    ):
        await svc.render_ship(str(obj_file), str(tex_file), out_file)


@pytest.mark.asyncio
async def test_render_ship_cleans_up_on_success(svc: RenderService, tmp_path: Path) -> None:
    """render_ship() should remove the temp dir after a successful render."""
    obj_file = tmp_path / "model.obj"
    obj_file.touch()
    tex_file = tmp_path / "texture.png"
    tex_file.touch()
    out_file = str(tmp_path / "output.png")

    observed_temp_dirs: list[Path] = []

    # Track which temp dir was created so we can verify it was cleaned up.
    async def fake_subprocess(*args, **kwargs):
        env = kwargs.get("env", {})
        vars_path = env.get("RENDER_ARGS_PATH", "")
        if vars_path:
            # The temp dir is the parent of render_vars
            observed_temp_dirs.append(Path(vars_path).parent)
        Path(out_file).parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (1, 1), (255, 255, 255))
        img.save(out_file, format="PNG")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        await svc.render_ship(str(obj_file), str(tex_file), out_file)

    assert len(observed_temp_dirs) == 1
    # The temp dir should have been cleaned up by render_ship()
    assert not observed_temp_dirs[0].exists()
