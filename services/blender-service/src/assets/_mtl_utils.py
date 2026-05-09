"""MTL file utilities for the Blender render script.

This module is pure Python (no bpy dependency) so it can be imported and
tested outside of Blender's embedded Python environment.
"""


def patch_all_mtl_blocks(content: str, tex_rel_path: str) -> str:
    """Inject ``map_Kd`` into every ``newmtl`` block in MTL file content.

    Existing ``map_Kd`` lines are removed and replaced with the new reference.
    The texture path is appended at the end of each block (i.e. just before
    the next ``newmtl`` keyword, or at EOF for the final block).

    :param content: Raw MTL file content as a string.
    :param tex_rel_path: Relative path to the texture file (relative to the
        MTL file's directory).
    :return: Modified MTL file content with ``map_Kd`` in every block.
    """
    lines = content.splitlines(keepends=True)
    result: list[str] = []
    found_newmtl = False

    for line in lines:
        stripped = line.strip()
        # Drop any existing map_Kd — we inject a canonical one per block.
        if stripped.lower().startswith("map_kd"):
            continue
        # When a new block starts, close the previous block with map_Kd.
        if stripped.lower().startswith("newmtl") and found_newmtl:
            result.append(f"map_Kd {tex_rel_path}\n")
        if stripped.lower().startswith("newmtl"):
            found_newmtl = True
        result.append(line)

    # Close the final (or only) block.
    if found_newmtl:
        result.append(f"map_Kd {tex_rel_path}\n")

    return "".join(result)
