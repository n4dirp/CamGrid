"""Shared helper utilities for Camera Grid extension."""

import ctypes
import math
from typing import Any, TypeVar

import bpy

_EVENT_TYPE_OFFSET: int = 16
_VALID_EVENT_TYPES: dict[int, str] | None = None

# Text Outline / Readability
LUMINANCE_R: float = 0.299
LUMINANCE_G: float = 0.587
LUMINANCE_B: float = 0.114
OUTLINE_ALPHA: float = 0.8


def _get_safe_event_type(event: bpy.types.Event) -> str:
    """Return event type without triggering RNA enum warnings."""
    global _VALID_EVENT_TYPES

    if _VALID_EVENT_TYPES is None:
        try:
            _VALID_EVENT_TYPES = {
                item.value: item.identifier for item in bpy.types.Event.bl_rna.properties["type"].enum_items
            }
        except Exception:
            _VALID_EVENT_TYPES = {}

    try:
        ptr = event.as_pointer()
        if not ptr:
            return "NONE"
        raw_type = ctypes.c_int16.from_address(ptr + _EVENT_TYPE_OFFSET).value
        return _VALID_EVENT_TYPES.get(raw_type, "NONE")
    except Exception:
        return "NONE"


def redraw_ui(mode: str = "VIEW_3D", area_pointer: int | None = None) -> None:
    """Redraw Blender UI areas."""
    ctx = bpy.context
    if not ctx or not ctx.window_manager:
        return
    for window in ctx.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area_pointer is not None:
                try:
                    if area.as_pointer() != area_pointer:
                        continue
                except ReferenceError:
                    continue
            if mode == "ALL" or area.type == mode:
                area.tag_redraw()


# TypeVar allows type checkers to know the return type matches the 'default' parameter type
T = TypeVar("T")


def _theme(path: str, default: T) -> T:
    """Accesses dynamic path theme options on standard floats or tuples."""
    prefs = bpy.context.preferences
    if not prefs.themes:
        return default
    value: Any = prefs.themes[0]
    try:
        for part in path.split("."):
            value = getattr(value, part)
        if hasattr(value, "copy"):
            return tuple(value)  # type: ignore
        return value
    except AttributeError:
        return default


def _rgba(value: tuple[float, ...], alpha: float) -> tuple[float, float, float, float]:
    """Convert an RGB/RGBA sequence to a strict 4-element RGBA float tuple."""
    return (float(value[0]), float(value[1]), float(value[2]), float(alpha))


def _get_ui_scale() -> float:
    """Get the current Blender UI scale."""
    return float(bpy.context.preferences.system.ui_scale)


def _get_asset_shelf_height(area: bpy.types.Area) -> int:
    """Calculate the cumulative height of any active Asset Shelf regions in the given area."""
    shelf_height = 0
    for region in area.regions:
        if "ASSET_SHELF" in region.type:
            shelf_height += region.height
    return shelf_height


def _get_bottom_header_height(area: bpy.types.Area) -> int:
    """Height of bottom HEADER that overlaps the WINDOW region, or 0."""
    for region in area.regions:
        if region.type == "HEADER" and getattr(region, "alignment", "") == "BOTTOM":
            return int(region.height)
    return 0


def _get_left_right_overlap(area: bpy.types.Area) -> tuple[int, int]:
    """Get widths of left (TOOLS) and right (UI) overlapping regions."""
    left = right = 0
    for region in area.regions:
        if region.type == "TOOLS":
            left = int(region.width)
        elif region.type == "UI":
            right = int(region.width)
    return left, right


def _compute_outline_color(rgb: tuple[float, ...]) -> tuple[float, float, float, float]:
    """Compute an appropriate black or white outline color based on input luminance for contrast."""
    luminance = rgb[0] * LUMINANCE_R + rgb[1] * LUMINANCE_G + rgb[2] * LUMINANCE_B
    if luminance > 0.5:
        return (0.0, 0.0, 0.0, OUTLINE_ALPHA)
    return (1.0, 1.0, 1.0, OUTLINE_ALPHA)


def _optimize_grid_columns(total_items: int, max_cols: int, max_rows: int) -> int:
    """Determine an optimal column count to distribute tiles evenly across rows."""
    if total_items <= 0:
        return 1

    best_c = max_cols
    best_score = float("inf")

    for c in range(1, max_cols + 1):
        r = math.ceil(total_items / c)
        empty_slots = (c * r) - total_items

        score = empty_slots + (r * 0.5)
        if r > max_rows:
            score += (r - max_rows) * 100.0

        if score < best_score:
            best_score = score
            best_c = c
        elif score == best_score:
            if c > best_c:
                best_c = c

    return best_c


def color_contrast(color: tuple[float, ...], factor: float = 0.85) -> tuple[float, float, float, float]:
    """Derive a solid color from the input color by darkening/lightening it strictly as a 4-element tuple."""
    return (float(color[0] * factor), float(color[1] * factor), float(color[2] * factor), 1.0)
