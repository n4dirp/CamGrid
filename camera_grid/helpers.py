"""Shared helper utilities for Camera Grid extension."""

import ctypes
import math

import bpy

_EVENT_TYPE_OFFSET = 16
_VALID_EVENT_TYPES = None

# Text Outline / Readability
LUMINANCE_R = 0.299
LUMINANCE_G = 0.587
LUMINANCE_B = 0.114
OUTLINE_ALPHA = 0.8


def _get_safe_event_type(event) -> str:
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


def _theme(path, default):
    """Accesses dynamic path theme options on standard floats or tuples."""
    prefs = bpy.context.preferences
    if not prefs.themes:
        return default
    value = prefs.themes[0]
    try:
        for part in path.split("."):
            value = getattr(value, part)
        if hasattr(value, "copy"):
            return tuple(value)
        return value
    except AttributeError:
        return default


def _rgba(value, alpha):
    return (*value[:3], alpha)


def _get_ui_scale():
    return bpy.context.preferences.system.ui_scale


def _get_asset_shelf_height(area):
    """Calculate the cumulative height of any active Asset Shelf regions in the given area."""
    shelf_height = 0
    for region in area.regions:
        if "ASSET_SHELF" in region.type:
            shelf_height += region.height
    return shelf_height


def _get_bottom_header_height(area):
    """Height of bottom HEADER that overlaps the WINDOW region, or 0."""
    for region in area.regions:
        if region.type == "HEADER" and getattr(region, "alignment", "") == "BOTTOM":
            return region.height

    return 0


def _get_left_right_overlap(area):
    """Get widths of left (TOOLS) and right (UI) overlapping regions."""
    left = right = 0
    for region in area.regions:
        if region.type == "TOOLS":
            left = region.width
        elif region.type == "UI":
            right = region.width
    return left, right


def _compute_outline_color(rgb: tuple[float, float, float]) -> tuple[float, float, float, float]:
    """Compute an appropriate black or white outline color based on input luminance for contrast."""
    luminance = rgb[0] * LUMINANCE_R + rgb[1] * LUMINANCE_G + rgb[2] * LUMINANCE_B
    if_luminance = luminance > 0.5
    if if_luminance:
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


def color_contrast(color, factor: float = 0.85):
    """Derive a solid outline color from the fill color by darkening it slightly."""
    return (color[0] * factor, color[1] * factor, color[2] * factor, 1.0)
