"""Load icon assets for the camera grid overlay and header UI."""

import os

import bpy
import gpu
from bpy.utils import previews

from .gpu_draw import _draw_texture_2d_with_alpha

_ICON_FILES: tuple[tuple[str, str], ...] = (
    ("anim_data", "blender_icon_anim_data.png"),
    ("constraint", "blender_icon_constraint.png"),
)

_PREVIEW_ICON_FILES: tuple[tuple[str, str], ...] = (("CAMERA_GRID_ICON", "camera_grid_icon.png"),)

_images: dict[str, bpy.types.Image] = {}
_textures: dict[str, gpu.types.GPUTexture] = {}
_previews = None
_preview_icons: dict[str, bpy.types.ImagePreview] = {}


def _icon_dir() -> str:
    """Return the directory containing icon assets."""
    return os.path.join(os.path.dirname(__file__), "icons")


def _load_overlay_icons() -> None:
    """Load overlay icon images and create GPU textures for them."""
    for name, filename in _ICON_FILES:
        if name in _images:
            continue
        try:
            image = bpy.data.images.load(os.path.join(_icon_dir(), filename), check_existing=True)
        except (RuntimeError, AttributeError):
            continue
        _images[name] = image
        _textures[name] = gpu.texture.from_image(image)


def _load_preview_icons() -> None:
    """Load header preview icons, safe to call without a GPU context."""
    global _previews
    if _previews is not None:
        return
    _previews = previews.new()
    for name, filename in _PREVIEW_ICON_FILES:
        icon_path = os.path.join(_icon_dir(), filename)
        if not os.path.isfile(icon_path):
            continue
        _preview_icons[name] = _previews.load(name, icon_path, "IMAGE")


def _unload_icons() -> None:
    """Remove loaded icon images, preview icons, and GPU texture references."""
    for name in list(_images):
        try:
            bpy.data.images.remove(_images.pop(name))
        except (ReferenceError, RuntimeError):
            pass
    _textures.clear()
    global _previews
    _preview_icons.clear()
    if _previews is not None:
        previews.remove(_previews)
        _previews = None


def _icon_id(name: str) -> int:
    """Return the icon id for the named preview icon, or 0 if missing."""
    preview = _preview_icons.get(name)
    return preview.icon_id if preview is not None else 0


def _draw_icon(name: str, x: float, y: float, size: float, alpha: float = 1.0) -> None:
    """Draw the named icon at the given screen position, size, and opacity."""
    if not _textures:
        _load_overlay_icons()
    texture = _textures.get(name)
    if texture is None or size <= 0 or alpha <= 0.0:
        return
    _draw_texture_2d_with_alpha(texture, x, y, size, size, alpha)
