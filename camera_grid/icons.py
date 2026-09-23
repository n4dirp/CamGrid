"""Load and draw PNG icon assets for the camera grid overlay."""

import os

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from gpu_extras.presets import draw_texture_2d

_ICON_FILES: tuple[tuple[str, str], ...] = (
    ("anim_data", "blender_icon_anim_data.png"),
    ("constraint", "blender_icon_constraint.png"),
)

_images: dict[str, bpy.types.Image] = {}
_textures: dict[str, gpu.types.GPUTexture] = {}


def _icon_dir() -> str:
    """Return the directory containing icon assets."""
    return os.path.join(os.path.dirname(__file__), "icons")


def _load_icons() -> None:
    """Load icon images from the package and create GPU textures for them."""
    for name, filename in _ICON_FILES:
        if name in _images:
            continue
        try:
            image = bpy.data.images.load(os.path.join(_icon_dir(), filename), check_existing=True)
        except (RuntimeError, AttributeError):
            continue
        _images[name] = image
        _textures[name] = gpu.texture.from_image(image)


def _unload_icons() -> None:
    """Remove loaded icon images and drop GPU texture references."""
    for name in list(_images):
        try:
            bpy.data.images.remove(_images.pop(name))
        except (ReferenceError, RuntimeError):
            pass
    _textures.clear()


def _draw_icon(name: str, x: float, y: float, size: float, alpha: float = 1.0) -> None:
    """Draw the named icon at the given screen position, size, and opacity."""
    if not _textures:
        _load_icons()
    texture = _textures.get(name)
    if texture is None or size <= 0 or alpha <= 0.0:
        return
    gpu.state.blend_set("ALPHA")
    try:
        if alpha >= 0.999:
            draw_texture_2d(texture, (x, y), size, size)
        else:
            shader = gpu.shader.from_builtin("IMAGE_COLOR")
            coords = ((0, 0), (1, 0), (1, 1), (0, 1))
            batch = batch_for_shader(
                shader, "TRIS", {"pos": coords, "texCoord": coords}, indices=((0, 1, 2), (2, 3, 0))
            )
            with gpu.matrix.push_pop():
                gpu.matrix.translate((x, y))
                gpu.matrix.scale((size, size))
                shader.bind()
                shader.uniform_sampler("image", texture)
                shader.uniform_float("color", (1.0, 1.0, 1.0, float(alpha)))
                batch.draw(shader)
    finally:
        gpu.state.blend_set("NONE")
