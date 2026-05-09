"""Tests for pure-Python logic extracted from the Blender render script.

``_render.py`` runs inside Blender's Python environment (bpy) and cannot be
imported in a normal test environment.  However, the MTL-patching logic lives
in ``_mtl_utils.py`` — a pure-Python module with no bpy dependency — and is
fully testable here.

Tests trace to the following acceptance criteria from the task specification:
  AC1: map_Kd is injected into EVERY newmtl block (not just the last one)
  AC2: Existing map_Kd lines are removed before re-injection (no duplicates)
  AC3: Works correctly when the MTL file has a single newmtl block
  AC4: Works correctly when the MTL file is empty (no newmtl blocks)
  AC5: The texture relative path is preserved exactly in the output
"""

from __future__ import annotations

import sys
from pathlib import Path

# _mtl_utils.py lives in src/assets/ alongside _render.py.
# It has no bpy dependency and is safe to import in the test environment.
_ASSETS_DIR = Path(__file__).parent.parent.parent / "src" / "assets"
sys.path.insert(0, str(_ASSETS_DIR))

from _mtl_utils import patch_all_mtl_blocks  # noqa: I001


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lines(content: str) -> list[str]:
    """Return non-empty stripped lines from *content*."""
    return [ln.strip() for ln in content.splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# AC1: map_Kd injected into EVERY newmtl block
# ---------------------------------------------------------------------------


class TestPatchAllMtlBlocksMultiBlock:
    """map_Kd is added to every newmtl block in a multi-material MTL."""

    def test_two_blocks_both_get_map_kd(self) -> None:
        """Both material blocks should contain map_Kd after patching."""
        content = "newmtl Hull\nKa 1.0 1.0 1.0\nKd 0.8 0.8 0.8\n\nnewmtl Wings\nKa 0.5 0.5 0.5\nKd 0.6 0.6 0.6\n"
        result = patch_all_mtl_blocks(content, "../texture.png")
        sections = result.split("newmtl ")
        # Drop the empty part before the first newmtl
        blocks = [s for s in sections if s.strip()]
        assert len(blocks) == 2, "Expected exactly 2 material blocks"
        for block in blocks:
            assert "map_Kd ../texture.png" in block, f"map_Kd missing from block:\n{block}"

    def test_three_blocks_all_get_map_kd(self) -> None:
        """Three material blocks should all get map_Kd."""
        content = "newmtl Mat1\nKd 1 0 0\nnewmtl Mat2\nKd 0 1 0\nnewmtl Mat3\nKd 0 0 1\n"
        result = patch_all_mtl_blocks(content, "tex.png")
        assert result.count("map_Kd tex.png") == 3

    def test_map_kd_appears_before_next_newmtl(self) -> None:
        """map_Kd for block N must appear before the 'newmtl' line of block N+1."""
        content = "newmtl A\nKd 1 0 0\n\nnewmtl B\nKd 0 1 0\n"
        result = patch_all_mtl_blocks(content, "tex.png")
        lines = result.splitlines()
        kd_indices = [i for i, ln in enumerate(lines) if "map_Kd" in ln]
        newmtl_b_idx = next(i for i, ln in enumerate(lines) if "newmtl B" in ln)
        # The map_Kd for block A must come before the newmtl B line
        assert any(idx < newmtl_b_idx for idx in kd_indices), "map_Kd for block A should appear before 'newmtl B'"


# ---------------------------------------------------------------------------
# AC2: Existing map_Kd lines are removed (no duplicates)
# ---------------------------------------------------------------------------


class TestPatchAllMtlBlocksExistingMapKd:
    """Existing map_Kd entries are removed and replaced."""

    def test_existing_map_kd_replaced_not_duplicated(self) -> None:
        """An existing map_Kd line must be replaced, not duplicated."""
        content = "newmtl Hull\nKd 1 0 0\nmap_Kd old_texture.bmp\n"
        result = patch_all_mtl_blocks(content, "new_texture.png")
        assert result.count("map_Kd") == 1
        assert "new_texture.png" in result
        assert "old_texture.bmp" not in result

    def test_all_old_map_kd_lines_removed(self) -> None:
        """Multiple existing map_Kd lines across blocks are all removed."""
        content = "newmtl A\nmap_Kd a.bmp\nKd 1 0 0\nnewmtl B\nmap_Kd b.bmp\nKd 0 1 0\n"
        result = patch_all_mtl_blocks(content, "new.png")
        assert "a.bmp" not in result
        assert "b.bmp" not in result
        assert result.count("map_Kd new.png") == 2

    def test_case_insensitive_map_kd_removal(self) -> None:
        """map_Kd matching should be case-insensitive (MAP_KD, Map_Kd, etc.)."""
        content = "newmtl Mat\nMAP_KD old.bmp\nMap_Kd another.bmp\n"
        result = patch_all_mtl_blocks(content, "new.png")
        assert "old.bmp" not in result
        assert "another.bmp" not in result
        assert "map_Kd new.png" in result


# ---------------------------------------------------------------------------
# AC3: Single newmtl block
# ---------------------------------------------------------------------------


class TestPatchAllMtlBlocksSingleBlock:
    """Single-material MTL files are handled correctly."""

    def test_single_block_gets_map_kd(self) -> None:
        """A single newmtl block should get exactly one map_Kd line."""
        content = "newmtl Hull\nKa 1 1 1\nKd 0.8 0.8 0.8\n"
        result = patch_all_mtl_blocks(content, "texture.png")
        assert result.count("map_Kd texture.png") == 1

    def test_single_block_map_kd_at_end(self) -> None:
        """map_Kd should appear after the material properties."""
        content = "newmtl Hull\nKd 0.8 0.8 0.8\n"
        result = patch_all_mtl_blocks(content, "texture.png")
        result_lines = result.strip().splitlines()
        assert result_lines[-1] == "map_Kd texture.png"

    def test_single_block_no_existing_map_kd(self) -> None:
        """Single block without existing map_Kd gets exactly one added."""
        content = "newmtl Material\nNs 100\n"
        result = patch_all_mtl_blocks(content, "tex.png")
        assert result.count("map_Kd") == 1


# ---------------------------------------------------------------------------
# AC4: Empty / no-newmtl MTL files
# ---------------------------------------------------------------------------


class TestPatchAllMtlBlocksEdgeCases:
    """Edge cases: empty content, no newmtl blocks, blank lines."""

    def test_empty_content_unchanged(self) -> None:
        """Empty MTL content should be returned unchanged (no map_Kd added)."""
        result = patch_all_mtl_blocks("", "texture.png")
        assert result == ""

    def test_no_newmtl_blocks_no_map_kd_added(self) -> None:
        """MTL content without any newmtl blocks should not have map_Kd injected."""
        content = "# Just a comment\nKa 1 1 1\n"
        result = patch_all_mtl_blocks(content, "texture.png")
        assert "map_Kd" not in result

    def test_blank_lines_preserved(self) -> None:
        """Blank lines between blocks should be preserved."""
        content = "newmtl A\nKd 1 0 0\n\n\nnewmtl B\nKd 0 1 0\n"
        result = patch_all_mtl_blocks(content, "tex.png")
        # Blank lines between blocks should still be in the output
        assert "\n\n" in result

    def test_trailing_newline_preserved(self) -> None:
        """If original content ends with a newline, structure is preserved."""
        content = "newmtl Mat\nKd 1 1 1\n"
        result = patch_all_mtl_blocks(content, "tex.png")
        assert result.endswith("\n")


# ---------------------------------------------------------------------------
# AC5: Texture relative path preserved exactly
# ---------------------------------------------------------------------------


class TestPatchAllMtlBlocksTexturePath:
    """The texture relative path is written verbatim into the output."""

    def test_simple_relative_path(self) -> None:
        """A simple relative path is written correctly."""
        content = "newmtl Mat\n"
        result = patch_all_mtl_blocks(content, "texture.png")
        assert "map_Kd texture.png" in result

    def test_parent_directory_relative_path(self) -> None:
        """A path with parent directory traversal is written correctly."""
        content = "newmtl Mat\n"
        result = patch_all_mtl_blocks(content, "../textures/skin.png")
        assert "map_Kd ../textures/skin.png" in result

    def test_absolute_path_preserved(self) -> None:
        """An absolute path is preserved as-is (caller's responsibility)."""
        content = "newmtl Mat\n"
        result = patch_all_mtl_blocks(content, "/tmp/render/texture.png")
        assert "map_Kd /tmp/render/texture.png" in result

    def test_path_with_spaces(self) -> None:
        """A path containing spaces is preserved exactly."""
        content = "newmtl Mat\n"
        result = patch_all_mtl_blocks(content, "path with spaces/texture.png")
        assert "map_Kd path with spaces/texture.png" in result
