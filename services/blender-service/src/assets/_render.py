"""Galaxy on Fire 2 ship skin renderer — Blender script.

This script is executed by Blender via the ``-P`` flag.  It runs INSIDE
Blender's Python environment and therefore uses ``bpy``.  Do NOT import
any FastAPI / service code here.

Updated for blender-service:
- RENDER_ARGS_PATH is read from the ``RENDER_ARGS_PATH`` environment variable
  (falls back to ``/tmp/render_vars`` if the variable is not set).
- render_vars format (6 lines):
    Line 1:  "WIDTHxHEIGHT"  (e.g. "1920x1080")
    Line 2:  /path/to/output.png
    Line 3:  /path/to/model.obj
    Line 4:  /path/to/texture.png
    Line 5:  64  (numSamples)
    Line 6:  /path/to/temp_copy.mtl  (temp MTL file — service pre-created this)
- render_service.py copies both the OBJ and MTL to a temp directory.
  The script rewrites the temp MTL so that every ``newmtl`` block contains a
  ``map_Kd`` line pointing to the composited texture.  The OBJ's ``mtllib``
  directive resolves to this co-located temp MTL.  After the render the
  original MTL content is restored to aid debugging.
  The texture is also applied via an Emission shader node to ensure colours
  are reproduced accurately regardless of scene lighting.
"""

try:
    import bpy  # type: ignore[reportMissingImports]
except ImportError as _err:
    raise ValueError("This script can only be run by Blender.") from _err

import os
import sys
from math import radians

# Add the directory containing this script to sys.path so we can import
# _mtl_utils (a pure-Python module with no bpy dependency).
_ASSETS_DIR = os.path.dirname(os.path.abspath(__file__))
if _ASSETS_DIR not in sys.path:
    sys.path.insert(0, _ASSETS_DIR)

from _mtl_utils import patch_all_mtl_blocks

##### CONFIG VARIABLES #####

# Camera clip distance — 5000 covers even large models like the Vossk Battlecruiser.
CAM_CLIP = 5000
# Default camera lens value (49 instead of 50 to account for chromatic aberration).
DEFAULT_LENS = 49

# Path to the render-variables file.  The service passes this via env var so
# that concurrent renders each get their own temp directory.
RENDER_ARGS_PATH = os.environ.get("RENDER_ARGS_PATH", "/tmp/render_vars")

print(f"RENDER_ARGS_PATH: {RENDER_ARGS_PATH}")

##### UTIL OBJECTS #####


class RenderArgs:
    """Data class representing arguments passed to the renderer.

    :var res_x: Render width in pixels.
    :vartype res_x: int
    :var res_y: Render height in pixels.
    :vartype res_y: int
    :var output_file_path: Full path (including filename) where the render is saved.
    :vartype output_file_path: str
    :var model_fullpath: Absolute path to the .obj file.
    :vartype model_fullpath: str
    :var texture_path: Path to the composited texture PNG.
    :vartype texture_path: str
    :var numSamples: Number of CYCLES samples per pixel.
    :vartype numSamples: int
    :var mtl_path: Path to the *temp copy* of the MTL file that the service prepared.
    :vartype mtl_path: str
    """

    def __init__(
        self,
        res_x: int,
        res_y: int,
        output_file_path: str,
        model_path: str,
        texture_path: str,
        numSamples: int,
        mtl_path: str,
    ):
        self.res_x = res_x
        self.res_y = res_y
        self.output_file_path = output_file_path
        self.model_fullpath = model_path
        self.texture_path = texture_path
        self.numSamples = numSamples
        self.mtl_path = mtl_path


def getRenderArgs() -> RenderArgs:
    """Parse the render-variables file and return a RenderArgs object.

    :return: Populated RenderArgs instance.
    :rtype: RenderArgs
    """
    args = []
    with open(RENDER_ARGS_PATH) as f:
        for line in f.readlines():
            args.append(line.rstrip("\n"))
    print(f"Raw render args: {args}")
    return RenderArgs(
        res_x=int(args[0].split("x")[0]),
        res_y=int(args[0].split("x")[1]),
        output_file_path=args[1],
        model_path=args[2],
        texture_path=args[3],
        numSamples=int(args[4]),
        mtl_path=args[5],
    )


##### LOAD RENDERER ARGUMENTS #####

args = getRenderArgs()

print(f"args.model_fullpath:    {args.model_fullpath}")
print(f"args.output_file_path:  {args.output_file_path}")
print(f"args.texture_path:      {args.texture_path}")
print(f"args.mtl_path:          {args.mtl_path}")

##### CONFIGURE THE SCENE #####

# Rewrite the temp MTL so every ``newmtl`` block has a ``map_Kd`` entry
# pointing to the composited skin texture.  Blender's OBJ/MTL importer
# resolves ``map_Kd`` paths RELATIVE to the MTL file's directory, so we
# compute a relative path from the temp MTL directory to the texture file.
_mtl_dir = os.path.dirname(os.path.abspath(args.mtl_path))
_tex_abs = os.path.abspath(args.texture_path)
_tex_rel = os.path.relpath(_tex_abs, _mtl_dir)
print(f"map_Kd relative path: {_tex_rel}  (from {_mtl_dir})")

# Save the original MTL content so we can restore it in CLEANUP.
with open(args.mtl_path) as _f:
    _original_mtl_content = _f.read()

# Inject map_Kd into EVERY newmtl block (not just the last one).
_patched_content = patch_all_mtl_blocks(_original_mtl_content, _tex_rel)
with open(args.mtl_path, "w") as _f:
    _f.write(_patched_content)

ctx = bpy.context

# Import the OBJ model into Blender's scene.
bpy.ops.wm.obj_import(
    filepath=args.model_fullpath,
    forward_axis="NEGATIVE_Z",
    up_axis="Y",
    filter_glob="*.obj;*.mtl",
)

# Apply the skin texture to every mesh material using a pure Emission shader.
#
# WHY EMISSION, NOT PRINCIPLED BSDF:
# The cube.blend scene has a single Sun light pointing straight down (-Z axis,
# rotation [0,0,0]).  After camera_to_view_selected() the camera is placed
# looking at the ship from a front/side angle.  Principled BSDF requires
# physical light to hit a surface before it shows colour — with the sun shining
# straight down, all front/side faces visible to the camera are in shadow and
# render near-black regardless of texture.  An Emission shader outputs the
# texture colour directly, independent of scene lighting, which is the correct
# behaviour for a skin preview render.
texture_image = bpy.data.images.load(args.texture_path)
# Force pixel data into memory so CYCLES can access it during rendering.
_ = list(texture_image.pixels[:4])


def _apply_emission_shader(mat, texture_image) -> None:
    """Set up an Emission shader node tree on *mat* using *texture_image*.

    Replaces the material's entire node tree with:
        Image Texture → Emission → Material Output

    This is lighting-independent and faithfully reproduces the skin texture.

    :param mat: Blender material to configure (must support ``use_nodes``).
    :param texture_image: ``bpy.types.Image`` instance to use as the texture.
    """
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    out_node = tree.nodes.new("ShaderNodeOutputMaterial")
    emit_node = tree.nodes.new("ShaderNodeEmission")
    tex_node = tree.nodes.new("ShaderNodeTexImage")
    tex_node.image = texture_image
    tree.links.new(tex_node.outputs["Color"], emit_node.inputs["Color"])
    tree.links.new(emit_node.outputs["Emission"], out_node.inputs["Surface"])


_mat_count = 0
_mesh_count = 0

for obj in bpy.data.objects:
    if obj.type != "MESH":
        continue
    _mesh_count += 1

    if len(obj.material_slots) == 0:
        # Gap 1: Mesh with no material slots — create and assign a new skin material
        # so the surface renders with the texture rather than Blender's default grey.
        print(f"  Creating new emission material for mesh with no slots: {obj.name}")
        _new_mat = bpy.data.materials.new(name=f"SkinMat_{obj.name}")
        _apply_emission_shader(_new_mat, texture_image)
        obj.data.materials.append(_new_mat)
        _mat_count += 1
    else:
        for _slot_idx, slot in enumerate(obj.material_slots):
            mat = slot.material
            if mat is None:
                # Gap 2: Slot with None material — create and assign a new skin
                # material so the surface renders with the texture instead of grey.
                print(f"  Replacing None material in slot {_slot_idx} on mesh: {obj.name}")
                _new_mat = bpy.data.materials.new(name=f"SkinMat_{obj.name}_{_slot_idx}")
                _apply_emission_shader(_new_mat, texture_image)
                slot.material = _new_mat
                _mat_count += 1
            else:
                _apply_emission_shader(mat, texture_image)
                _mat_count += 1

print(f"✓ Applied texture to {_mat_count} materials across {_mesh_count} mesh objects")

# Deselect everything (the camera might be selected after import).
bpy.ops.object.select_all(action="DESELECT")

# Select the imported model and configure it.
for obj in ctx.visible_objects:
    if obj.type != "CAMERA":
        obj.select_set(True)
        obj.rotation_euler[0] = radians(0)
        ctx.scene.camera.data.clip_end = CAM_CLIP

##### RENDER THE MODEL #####

# Set render resolution.
ctx.scene.render.resolution_x = args.res_x
ctx.scene.render.resolution_y = args.res_y
ctx.scene.render.resolution_percentage = 100
bpy.context.scene.cycles.samples = args.numSamples

# Use CYCLES engine (EEVEE produces unexpected perspective artefacts).
ctx.scene.render.engine = "CYCLES"

###### SIMPLE GPU SETUP ######
try:
    bpy.context.preferences.addons["cycles"].preferences.compute_device_type = "CUDA"
    bpy.context.preferences.addons["cycles"].preferences.get_devices()

    gpu_found = False
    for device in bpy.context.preferences.addons["cycles"].preferences.devices:
        if device.type == "CUDA":
            device.use = True
            gpu_found = True
            print(f"✓ Using GPU: {device.name}")

    if gpu_found:
        bpy.context.scene.cycles.device = "GPU"
        print("✓ GPU rendering enabled")
    else:
        print("⚠ No CUDA GPU found, falling back to CPU")

except Exception as _gpu_err:
    print(f"⚠ GPU setup failed, using CPU: {_gpu_err}")
###### END GPU SETUP ######

# Set the render output path.
ctx.scene.render.filepath = args.output_file_path
print(f"Render output will be saved to: {args.output_file_path}")

# Ensure the output directory exists.
output_dir = os.path.dirname(args.output_file_path)
if output_dir and not os.path.exists(output_dir):
    os.makedirs(output_dir)
    print(f"Created output directory: {output_dir}")

# Auto-position camera so the model fills the frame.
bpy.ops.view3d.camera_to_view_selected()

# Adjust lens (49 instead of 50 to account for chromatic aberration).
bpy.data.cameras.values()[0].lens = DEFAULT_LENS

# Render the scene.
bpy.ops.render.render(write_still=True)

##### CLEANUP #####

# Restore the original MTL content.  The temp directory is cleaned up by
# render_service.py on success, but restoring the file aids debugging when
# the directory is preserved on failure.
with open(args.mtl_path, "w") as _f:
    _f.write(_original_mtl_content)
