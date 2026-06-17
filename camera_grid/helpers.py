"""Shared helper utilities."""

import math
from typing import Any, TypeVar

import bpy

# Text Outline / Readability
LUMINANCE_R: float = 0.299
LUMINANCE_G: float = 0.587
LUMINANCE_B: float = 0.114
OUTLINE_ALPHA: float = 0.8


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
        try:
            return tuple(value)  # type: ignore
        except TypeError:
            return value
    except AttributeError:
        return default


def _srgb_to_linear(c: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Convert sRGB float color (0-1) to linear color space for GPU rendering."""

    def _conv(ch: float) -> float:
        return ch / 12.92 if ch <= 0.04045 else ((ch + 0.055) / 1.055) ** 2.4

    return (_conv(c[0]), _conv(c[1]), _conv(c[2]), c[3])


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


def _optimize_grid_columns(
    total_items: int,
    max_cols: int,
    max_rows: int,
    available_width: float,
    tile_width: float,
    gap: float,
) -> int:
    """
    Choose a column count for a vertically scrolling grid.

    Priorities:
        1. Minimize row count.
        2. Prefer a fuller last row.
        3. Minimize empty slots.
        4. Prefer more columns on ties.
        5. Fill available horizontal space.
    """
    if total_items <= 0:
        return 1

    best_c = 1
    best_score = float("inf")

    for c in range(1, max_cols + 1):
        r = math.ceil(total_items / c)

        empty_slots = (c * r) - total_items

        last_row_items = total_items % c
        if last_row_items == 0:
            last_row_items = c

        fill_ratio = last_row_items / c

        score = (
            r * 10.0  # strongly prefer fewer rows
            + empty_slots  # secondary preference
            + (1.0 - fill_ratio) * 5.0  # penalize sparse last rows
        )

        if r > max_rows:
            score += (r - max_rows) * 100.0

        if c > 0 and available_width > 0:
            grid_w = c * tile_width + (c - 1) * gap
            unused_w = max(0.0, available_width - grid_w)
            unused_ratio = unused_w / available_width
            score += unused_ratio * 2.0

        if score < best_score:
            best_score = score
            best_c = c
        elif score == best_score and c > best_c:
            best_c = c

    return best_c


def color_contrast(color: tuple[float, ...], factor: float = 0.85) -> tuple[float, float, float, float]:
    """Derive a solid color from the input color by darkening/lightening it strictly as a 4-element tuple."""
    return (float(color[0] * factor), float(color[1] * factor), float(color[2] * factor), 1.0)
