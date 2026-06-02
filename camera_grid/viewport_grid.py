"""Viewport camera grid overlay for Camera Grid extension."""

import ctypes
import logging
import math
import time
from enum import Enum
from functools import lru_cache

import blf
import bpy
import gpu
from bpy.types import Operator
from gpu_extras.batch import batch_for_shader
from gpu_extras.presets import draw_texture_2d

# ------------------------------------------------------------------------
#    Helpers (inlined)
# ------------------------------------------------------------------------

_EVENT_TYPE_OFFSET = 16
_VALID_EVENT_TYPES = None


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


logger = logging.getLogger(__package__)

# ------------------------------------------------------------------------
#    Constants & Globals
# ------------------------------------------------------------------------

# Grid Layout
TILE_WIDTH = 120
TILE_HEIGHT = 24
TILE_GAP = 5
BOTTOM_MARGIN = round(TILE_HEIGHT + TILE_GAP * 1.25)
HORIZONTAL_PADDING = BOTTOM_MARGIN * 3  # 1/2 on each side
ROUNDING = 2.0

# Scrollbar
SCROLLBAR_WIDTH = 3
SCROLLBAR_PADDING = TILE_GAP
SCROLLBAR_MIN_THUMB = round(TILE_HEIGHT / 2)

# Font
FONT_SIZE = 10
FONT_ID = 0
BADGE_FONT_ID = 0
INFO_TEXT_OFFSET_Y = (BOTTOM_MARGIN - TILE_HEIGHT + TILE_GAP) * 2

# Text Outline / Readability
LUMINANCE_R = 0.299
LUMINANCE_G = 0.587
LUMINANCE_B = 0.114
OUTLINE_ALPHA = 0.8

_handler = None
_target_area_pointer = None
_target_region_pointer = None
_modal_operator = None

# Track absolute scroll state instead of relative offsets
_current_start_row = -1
_last_active_index = -1

# Track mouse hover for overlay feedback
_mouse_in_grid = False

# Preview thumbnail cache
# Key: Camera name (str)
# Value: tuple(generation, offscreen_buffer, camera_state_signature, last_accessed_time)
_thumbnail_cache: dict[str, tuple[int, gpu.types.GPUOffScreen, tuple, float]] = {}
_thumbnail_gen: int = 0
_thumbnail_pending: set[str] = set()
_thumbnail_stale: set[str] = set()
_in_preview_render: bool = False
_render_timer_active: bool = False
_preview_rendered_count: int = 0
_render_elapsed_ms: float = 0.0
_original_shading_type: str = None
_original_show_overlays: bool = None


class _DragState(Enum):
    IDLE = 0
    LMB_PRESSED = 1
    LMB_DRAGGING = 2
    RMB_PRESSED = 3
    RMB_DRAGGING = 4
    SCROLLBAR_DRAGGING = 5


# Drag state machine for click+drag tile interactions
_drag_state = _DragState.IDLE
_drag_tile = -1  # tile where the press originated
_drag_last_tile = -1  # last tile acted on during active drag
_drag_last_scroll_time = 0.0  # debounce timestamp for edge auto-scroll
_drag_select_value = False  # state to apply when paint-selecting with RMB


# ------------------------------------------------------------------------
#    Helpers & Geometry (Optimized)
# ------------------------------------------------------------------------


@lru_cache(maxsize=32)
def _get_base_rounded_rect(w, h, r, segments=6):
    """Generates and caches static normalized geometry relative to (0, 0) to avoid trig calls."""
    r = max(0, min(r, w / 2, h / 2))

    if r == 0:
        return (
            (w, h),
            (0.0, h),
            (0.0, 0.0),
            (w, 0.0),
        )

    verts = []
    corners = [
        (w - r, h - r, 0.0, math.pi / 2),
        (r, h - r, math.pi / 2, math.pi),
        (r, r, math.pi, math.pi * 1.5),
        (w - r, r, math.pi * 1.5, math.pi * 2.0),
    ]

    for cx, cy, start_angle, end_angle in corners:
        for i in range(segments + 1):
            angle = start_angle + (end_angle - start_angle) * (i / segments)
            verts.append((cx + math.cos(angle) * r, cy + math.sin(angle) * r))

    return tuple(verts)


def get_rounded_rect_perimeter(x, y, w, h, r, segments=6):
    """Translates pre-computed static geometry coordinates to target offsets (Pattern 12)."""
    base_verts = _get_base_rounded_rect(w, h, r, segments)
    return [(x + vx, y + vy) for vx, vy in base_verts]


@lru_cache(maxsize=32)
def _theme(path, default):
    """Accesses and caches dynamic path theme options on standard floats or tuples."""
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


def _get_theme_colors():
    text = _theme("view_3d.space.header_text", (1.0, 1.0, 1.0))

    return {
        "tile_default": _rgba(_theme("view_3d.space.header", (0.25, 0.25, 0.25)), 1.0),
        "tile_hover": _rgba(_theme("view_3d.space.header", (0.35, 0.35, 0.35)), 0.95),
        "tile_picked": _rgba(_theme("user_interface.wcol_regular.inner_sel", (0.28, 0.45, 0.7)), 1.0),
        "border_active": _rgba(_theme("view_3d.object_active", (1.0, 0.63, 0.16)), 1.0),
        "text": _rgba(text, 0.9),
        "info_text": _rgba(text, 0.75),
    }


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


def _draw_text_with_shadow(
    font_id: int,
    text: str,
    x: float,
    y: float,
    color: tuple[float, float, float, float],
    scale: float,
):
    """Draw text with a native BLF shadow for readability against any background."""
    outline = _compute_outline_color(color[:3])

    blf.enable(font_id, blf.SHADOW)

    blf.shadow(
        font_id,
        3,
        int(outline[0] * 255),
        int(outline[1] * 255),
        int(outline[2] * 255),
        int(outline[3] * 255),
    )

    blf.shadow_offset(font_id, int(scale), -int(scale))

    blf.position(font_id, x, y, 0)
    blf.color(font_id, *color)
    blf.draw(font_id, text)

    blf.disable(font_id, blf.SHADOW)


def _optimize_grid_columns(total_items: int, max_cols: int, max_rows: int) -> int:
    """Determine an optimal column count to distribute tiles evenly across rows."""
    if total_items <= 0:
        return 1

    best_c = max_cols
    best_score = float("inf")

    # Evaluate all column counts from 1 up to the physical/preference limit
    for c in range(1, max_cols + 1):
        r = math.ceil(total_items / c)
        empty_slots = (c * r) - total_items

        # Scoring heuristic: prioritizes fewer empty slots and slightly penalizes extra rows
        score = empty_slots + (r * 0.5)
        if r > max_rows:
            score += (r - max_rows) * 100.0

        if score < best_score:
            best_score = score
            best_c = c
        elif score == best_score:
            # On scoring ties, choose the wider layout configuration
            if c > best_c:
                best_c = c

    return best_c


def _compute_grid_layout(context, area=None, region=None, scene=None):
    """Compute grid layout values shared between drawing and click detection."""
    global _current_start_row, _last_active_index

    if scene is None:
        scene = getattr(context, "scene", None)
    if not scene:
        return None

    props = getattr(scene, "camgrid_props", None)
    if not props:
        return None

    camera_collection = props.source_collection
    if camera_collection:
        cameras = sorted(
            (obj for obj in camera_collection.objects if obj.type == "CAMERA"),
            key=lambda o: o.name,
        )
    else:
        cameras = sorted(
            (obj for obj in bpy.data.objects if obj.type == "CAMERA"),
            key=lambda o: o.name,
        )
    total_cameras = len(cameras)
    if total_cameras < 1:
        return None

    if region is None:
        region = getattr(context, "region", None)
    if not region:
        return None

    if area is None:
        area = getattr(context, "area", None)
    if not area:
        return None

    try:
        area_ptr = area.as_pointer()
    except ReferenceError:
        return None
    if _target_area_pointer and area_ptr != _target_area_pointer:
        return None

    if _target_region_pointer:
        try:
            region_ptr = region.as_pointer()
        except ReferenceError:
            return None
        if region_ptr != _target_region_pointer:
            return None

    prefs = context.preferences.addons.get(__package__).preferences

    if not prefs.settings.show_hidden:
        cameras = [cam for cam in cameras if cam.visible_get()]
    total_cameras = len(cameras)
    if total_cameras < 1:
        return None

    scale = _get_ui_scale()
    try:
        shelf_height = _get_asset_shelf_height(area)
    except (ReferenceError, AttributeError):
        shelf_height = 0

    left_overlap, right_overlap = _get_left_right_overlap(area)
    bottom_header_height = _get_bottom_header_height(area)

    if prefs.settings.display_type == "THUMBNAILS":
        render = scene.render
        aspect = (render.resolution_x * render.pixel_aspect_x) / (render.resolution_y * render.pixel_aspect_y)
        max_side = prefs.settings.preview_size
        if aspect >= 1.0:
            preview_w = max_side
            preview_h = round(max_side / aspect)
        else:
            preview_w = round(max_side * aspect)
            preview_h = max_side
        tw = preview_w * scale
        th = preview_h * scale
        effective_max_rows = prefs.settings.preview_max_rows
    else:
        tw = prefs.settings.tile_size * scale
        th = TILE_HEIGHT * scale
        effective_max_rows = prefs.settings.max_rows

    gap = TILE_GAP * scale
    radius = ROUNDING * scale
    bottom_margin = BOTTOM_MARGIN * scale + shelf_height + bottom_header_height

    side_padding = (HORIZONTAL_PADDING * scale) / 2.0
    left_bound = left_overlap + side_padding
    right_bound = region.width - right_overlap - side_padding

    if prefs.settings.alignment == "CENTER":
        center_x = region.width / 2.0
        max_half_width = min(center_x - left_bound, right_bound - center_x)
        max_available_width = max(0.0, max_half_width * 2.0)
    else:
        max_available_width = max(0.0, right_bound - left_bound)

    max_cols = max(1, int(max_available_width / (tw + gap)))
    if prefs.settings.display_type == "THUMBNAILS":
        max_cols = min(max_cols, prefs.settings.preview_max_columns)
    else:
        max_cols = min(prefs.settings.max_columns, max_cols)

    # Calculate the optimized column count based on available space and maximum rows
    columns = _optimize_grid_columns(total_cameras, max_cols, effective_max_rows)

    active_camera = scene.camera
    active_index = 0
    for i, cam in enumerate(cameras):
        if cam == active_camera:
            active_index = i
            break

    total_rows = (total_cameras + columns - 1) // columns
    active_row = active_index // columns
    max_scroll = max(0, total_rows - effective_max_rows)

    if active_index != _last_active_index:
        if _current_start_row == -1:
            _current_start_row = max(0, active_row - effective_max_rows // 2)
        else:
            if active_row < _current_start_row:
                _current_start_row = active_row
            elif active_row >= _current_start_row + effective_max_rows:
                _current_start_row = active_row - effective_max_rows + 1

        _last_active_index = active_index

    _current_start_row = max(0, min(_current_start_row, max_scroll))
    start_row = _current_start_row

    start_index = start_row * columns
    end_index = min(total_cameras, start_index + effective_max_rows * columns)

    actual_columns = min(columns, total_cameras)
    grid_width = actual_columns * (tw + gap) - gap

    if prefs.settings.alignment == "LEFT":
        origin_x = round(left_bound)
    elif prefs.settings.alignment == "RIGHT":
        origin_x = round(right_bound - grid_width)
    else:
        origin_x = round((region.width - grid_width) / 2.0)
        origin_x = max(left_bound, min(origin_x, right_bound - grid_width))

    origin_y = bottom_margin

    return {
        "cameras": cameras,
        "total_cameras": total_cameras,
        "columns": columns,
        "start_index": start_index,
        "end_index": end_index,
        "start_row": start_row,
        "origin_x": origin_x,
        "origin_y": origin_y,
        "tw": tw,
        "th": th,
        "gap": gap,
        "radius": radius,
        "scale": scale,
        "region": region,
        "active_camera": active_camera,
        "active_index": active_index,
        "total_rows": total_rows,
        "font_size": max(8, int(FONT_SIZE * scale)),
        "info_offset_y": INFO_TEXT_OFFSET_Y * scale,
        "grid_width": grid_width,
        "grid_alignment": prefs.settings.alignment,
        "visible_rows": min(effective_max_rows, total_rows - start_row),
        "effective_max_rows": effective_max_rows,
    }


def _get_scrollbar_layout(layout):
    """Calculate interactive geometry and metrics for the scrollbar."""
    grid_max_rows = layout["effective_max_rows"]
    total_rows = layout["total_rows"]
    if total_rows <= grid_max_rows:
        return None

    scale = layout["scale"]
    th = layout["th"]
    gap = layout["gap"]
    origin_x = layout["origin_x"]
    origin_y = layout["origin_y"]

    sb_pad = SCROLLBAR_PADDING * scale
    sb_w = SCROLLBAR_WIDTH * scale
    grid_alignment = layout.get("grid_alignment", "CENTER")
    if grid_alignment == "LEFT":
        track_left = origin_x - sb_pad - sb_w
    else:
        track_left = origin_x + layout["grid_width"] + sb_pad
    track_h = grid_max_rows * (th + gap) - gap
    track_bottom = origin_y
    track_top = origin_y + track_h

    visible_rows = grid_max_rows
    thumb_ratio = visible_rows / total_rows
    thumb_h = max(track_h * thumb_ratio, SCROLLBAR_MIN_THUMB * scale)
    max_scroll = total_rows - visible_rows

    start_row = layout["start_row"]
    thumb_t = start_row / max_scroll if max_scroll > 0 else 0
    thumb_y = track_bottom + (track_h - thumb_h) * thumb_t

    # Expand interactive zone for mouse targeting
    hit_width = 12 * scale
    hit_left = track_left - (hit_width - sb_w) / 2
    hit_right = hit_left + hit_width

    return {
        "track_left": track_left,
        "track_bottom": track_bottom,
        "track_top": track_top,
        "track_h": track_h,
        "thumb_y": thumb_y,
        "thumb_h": thumb_h,
        "hit_left": hit_left,
        "hit_right": hit_right,
        "max_scroll": max_scroll,
    }


def _get_tile_at_mouse(layout, mouse_x, mouse_y):
    """Return the camera index if the mouse is over a visible tile, else None."""
    columns = layout["columns"]
    start_index = layout["start_index"]
    end_index = layout["end_index"]
    start_row = layout["start_row"]
    origin_x = layout["origin_x"]
    origin_y = layout["origin_y"]
    tw = layout["tw"]
    th = layout["th"]
    gap = layout["gap"]

    for i in range(start_index, end_index):
        column = i % columns
        row = i // columns
        drawn_row = row - start_row

        box_x = origin_x + column * (tw + gap)
        box_y = origin_y + drawn_row * (th + gap)

        if box_x <= mouse_x <= box_x + tw and box_y <= mouse_y <= box_y + th:
            return i
    return None


def _is_mouse_in_grid(layout, mouse_x, mouse_y):
    """Return True if the mouse is within the grid panel area (tiles + scrollbar + info text)."""
    gap = layout["gap"]

    grid_left = layout["origin_x"] - gap
    grid_right = layout["origin_x"] + layout["grid_width"] + gap
    grid_bottom = layout["origin_y"] - layout["info_offset_y"] - gap
    grid_top = layout["origin_y"] + layout["visible_rows"] * (layout["th"] + layout["gap"])

    sb_layout = _get_scrollbar_layout(layout)
    if sb_layout:
        grid_alignment = layout.get("grid_alignment", "CENTER")
        if grid_alignment == "LEFT":
            grid_left = min(grid_left, sb_layout["hit_left"])
        else:
            grid_right = max(grid_right, sb_layout["hit_right"])

    return grid_left <= mouse_x <= grid_right and grid_bottom <= mouse_y <= grid_top


def _drag_tile_action(layout, mx, my, ref_index, on_new_tile):
    """If mouse entered a tile different from ref_index, call on_new_tile(layout, tile_index).
    Returns the updated tile index, or ref_index if unchanged."""
    tile_index = _get_tile_at_mouse(layout, mx, my)
    if tile_index is not None and tile_index != ref_index:
        on_new_tile(layout, tile_index)
        return tile_index
    return ref_index


def _switch_to_camera_view(context):
    """Switch the 3D viewport area to camera view if the preference is enabled."""
    prefs = context.preferences.addons.get(__package__).preferences
    if not prefs.settings.view_from_camera:
        return
    area = context.area
    if area and area.type == "VIEW_3D":
        space = area.spaces.active
        if space and space.type == "VIEW_3D":
            space.region_3d.view_perspective = "CAMERA"


def _action_switch_camera(layout, tile_index):
    """Drag action: activate the camera at tile_index."""
    cameras = layout["cameras"]
    if 0 <= tile_index < len(cameras):
        scene = bpy.context.scene
        scene.camera = cameras[tile_index]
    _switch_to_camera_view(bpy.context)


def _action_select_camera(layout, tile_index):
    """Drag action: select or deselect the camera at tile_index."""
    cam = layout["cameras"][tile_index]
    cam.select_set(_drag_select_value)
    if _drag_select_value:
        bpy.context.view_layer.objects.active = cam
    redraw_ui("VIEW_3D", area_pointer=_target_area_pointer)


def color_contrast(color, factor: float = 0.85):
    """Derive a solid outline color from the fill color by darkening it slightly."""
    return (color[0] * factor, color[1] * factor, color[2] * factor, 1.0)


# ------------------------------------------------------------------------
#    Preview Thumbnail Cache
# ------------------------------------------------------------------------


def _get_camera_state_signature(cam, scene):
    """Generate a unique signature capturing camera and scene state for caching (Pattern 10)."""
    mw = cam.matrix_world
    matrix_tuple = (
        mw[0][0],
        mw[0][1],
        mw[0][2],
        mw[0][3],
        mw[1][0],
        mw[1][1],
        mw[1][2],
        mw[1][3],
        mw[2][0],
        mw[2][1],
        mw[2][2],
        mw[2][3],
        mw[3][0],
        mw[3][1],
        mw[3][2],
        mw[3][3],
    )

    cam_data = cam.data
    lens = getattr(cam_data, "lens", 0.0)
    sensor_width = getattr(cam_data, "sensor_width", 0.0)
    shift_x = getattr(cam_data, "shift_x", 0.0)
    shift_y = getattr(cam_data, "shift_y", 0.0)
    ortho_scale = getattr(cam_data, "ortho_scale", 0.0)

    return (matrix_tuple, lens, sensor_width, shift_x, shift_y, ortho_scale)


def _invalidate_thumbnails():
    global _thumbnail_cache, _thumbnail_gen, _thumbnail_pending, _in_preview_render, _render_timer_active
    global _preview_rendered_count, _render_elapsed_ms
    global _original_shading_type, _original_show_overlays
    global _thumbnail_stale
    for item in list(_thumbnail_cache.values()):
        try:
            offscreen = item[1]
            offscreen.free()
        except Exception:
            pass
    _thumbnail_cache.clear()
    _thumbnail_pending.clear()
    _thumbnail_stale.clear()
    _thumbnail_gen += 1
    _in_preview_render = False
    _render_timer_active = False
    _preview_rendered_count = 0
    _render_elapsed_ms = 0.0

    context = bpy.context
    target_area = next(
        (a for w in context.window_manager.windows for a in w.screen.areas if a.as_pointer() == _target_area_pointer),
        None,
    )
    space_view3d = target_area.spaces.active if target_area and target_area.type == "VIEW_3D" else None
    _cleanup_shading_mode(space_view3d)

    _original_shading_type = None
    _original_show_overlays = None
    _theme.cache_clear()
    _get_base_rounded_rect.cache_clear()
    logger.debug("PREVIEW: Cache invalidated (gen %d)", _thumbnail_gen)


def _queue_thumbnail_render(cam_key):
    """Thread-safely append a camera to the render queue and launch the asynchronous timer."""
    global _thumbnail_pending, _render_timer_active
    _thumbnail_pending.add(cam_key)
    if not _render_timer_active:
        _render_timer_active = True
        bpy.app.timers.register(_process_thumbnail_queue, first_interval=0.01)


def _cleanup_shading_mode(space_view3d=None):
    """Restores the original viewport shading mode and overlays from before queue execution."""
    global _original_shading_type, _original_show_overlays
    if _original_shading_type is not None:
        if space_view3d:
            if space_view3d.shading.type != _original_shading_type:
                try:
                    space_view3d.shading.type = _original_shading_type
                except ReferenceError:
                    pass
            if _original_show_overlays is not None and space_view3d.overlay.show_overlays != _original_show_overlays:
                try:
                    space_view3d.overlay.show_overlays = _original_show_overlays
                    logger.debug("PREVIEW: Overlays restored to %s", _original_show_overlays)
                except ReferenceError:
                    pass
        _original_shading_type = None
        _original_show_overlays = None


def _process_thumbnail_queue():
    """Timer callback running on the main thread to render thumbnails safely outside the draw loop."""
    global _render_timer_active, _thumbnail_pending, _in_preview_render, _thumbnail_gen
    global _preview_rendered_count, _render_elapsed_ms
    global _original_shading_type, _original_show_overlays

    if not _thumbnail_pending:
        context = bpy.context
        target_area = next(
            (
                a
                for w in context.window_manager.windows
                for a in w.screen.areas
                if a.as_pointer() == _target_area_pointer
            ),
            None,
        )
        space_view3d = target_area.spaces.active if target_area and target_area.type == "VIEW_3D" else None
        _cleanup_shading_mode(space_view3d)
        _render_timer_active = False
        return None

    context = bpy.context

    target_win = None
    target_area = None
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.as_pointer() == _target_area_pointer:
                target_win = win
                target_area = area
                break
        if target_area:
            break

    if not target_win or not target_area or target_area.type != "VIEW_3D":
        _cleanup_shading_mode(None)
        _render_timer_active = False
        return None

    space_view3d = target_area.spaces.active
    if not space_view3d or space_view3d.type != "VIEW_3D":
        _cleanup_shading_mode(None)
        _render_timer_active = False
        return None

    region = None
    for r in target_area.regions:
        if r.type == "WINDOW":
            region = r
            break

    if not region:
        _cleanup_shading_mode(space_view3d)
        _render_timer_active = False
        return None

    scene = target_win.scene
    layout = _compute_grid_layout(context, area=target_area, region=region, scene=scene)
    if not layout:
        _cleanup_shading_mode(space_view3d)
        _render_timer_active = False
        return None

    cameras = layout["cameras"]
    start_index = layout["start_index"]
    end_index = layout["end_index"]
    tw, th = layout["tw"], layout["th"]

    visible_keys = {cameras[idx].name for idx in range(start_index, end_index)}
    visible_pending = list(_thumbnail_pending.intersection(visible_keys))
    offscreen_pending = list(_thumbnail_pending.difference(visible_keys))

    ordered_pending = visible_pending + offscreen_pending

    if not ordered_pending:
        _thumbnail_pending.clear()
        _cleanup_shading_mode(space_view3d)
        _render_timer_active = False
        return None

    logger.debug(
        "PREVIEW: Queue depth — %d visible, %d offscreen (%d total)",
        len(visible_pending),
        len(offscreen_pending),
        len(ordered_pending),
    )

    prefs = context.preferences.addons.get(__package__).preferences

    batch_to_render = ordered_pending[: prefs.settings.preview_renders_per_tick]

    if _original_shading_type is None:
        _original_shading_type = space_view3d.shading.type
        if _original_shading_type != "SOLID":
            space_view3d.shading.type = "SOLID"
            logger.debug("PREVIEW: Temporarily switched shading to SOLID")
        if prefs.settings.preview_disable_overlays:
            _original_show_overlays = space_view3d.overlay.show_overlays
            if _original_show_overlays:
                space_view3d.overlay.show_overlays = False
                logger.debug("PREVIEW: Temporarily disabled viewport overlays")

    try:
        depsgraph = context.evaluated_depsgraph_get()

        batch_start = time.perf_counter()
        for cam_key in batch_to_render:
            _thumbnail_pending.discard(cam_key)
            cam_obj = bpy.data.objects.get(cam_key)
            if not cam_obj:
                continue

            try:
                offscreen = _render_thumbnail(
                    cam_obj,
                    scene,
                    depsgraph,
                    space_view3d,
                    region,
                    tw,
                    th,
                )

                if offscreen is not None:
                    sig = _get_camera_state_signature(cam_obj, scene)
                    _thumbnail_cache[cam_key] = (_thumbnail_gen, offscreen, sig, time.monotonic())

                    if len(_thumbnail_cache) > prefs.settings.preview_cache_size:
                        oldest_key = min(_thumbnail_cache.keys(), key=lambda k: _thumbnail_cache[k][3])
                        oldest_data = _thumbnail_cache.pop(oldest_key)
                        try:
                            oldest_data[1].free()
                        except Exception:
                            pass
                        logger.debug(
                            "PREVIEW: Evicted '%s' (cache exceeded %d)",
                            oldest_key,
                            prefs.settings.preview_cache_size,
                        )
            except Exception as e:
                logger.error(
                    "PREVIEW: Exception rendering thumbnail for '%s': %s",
                    cam_key,
                    str(e),
                )

        _render_elapsed_ms += (time.perf_counter() - batch_start) * 1000
        redraw_ui("VIEW_3D", area_pointer=_target_area_pointer)

    except Exception as e:
        logger.error("PREVIEW: Exception during batch run: %s", str(e))

    if _thumbnail_pending:
        return 0.01

    _cleanup_shading_mode(space_view3d)

    logger.debug(
        "PREVIEW: All %d thumbnails rendered in %.0f ms (%.1f ms avg)",
        _preview_rendered_count,
        _render_elapsed_ms,
        _render_elapsed_ms / max(_preview_rendered_count, 1),
    )
    _render_timer_active = False
    return None


def _render_thumbnail(cam, scene, depsgraph, space_view3d, region, tw, th):
    """Creates the offscreen buffer and triggers the 3D viewport drawing routines."""
    global _in_preview_render, _preview_rendered_count
    try:
        if _in_preview_render:
            return None
        if space_view3d is None:
            return None

        t0 = time.perf_counter()

        scale = _get_ui_scale()
        prefs = bpy.context.preferences.addons.get(__package__).preferences
        r = scene.render
        aspect = (r.resolution_x * r.pixel_aspect_x) / (r.resolution_y * r.pixel_aspect_y)
        max_side = int(prefs.settings.preview_size * scale)
        if aspect >= 1.0:
            render_w = max_side
            render_h = max(1, round(max_side / aspect))
        else:
            render_w = max(1, round(max_side * aspect))
            render_h = max_side

        _in_preview_render = True

        offscreen = gpu.types.GPUOffScreen(render_w, render_h)

        view_matrix = cam.matrix_world.inverted()
        proj_matrix = cam.calc_matrix_camera(depsgraph, x=render_w, y=render_h)

        offscreen.draw_view3d(
            scene,
            depsgraph.view_layer,
            space_view3d,
            region,
            view_matrix,
            proj_matrix,
            do_color_management=True,
        )

        _in_preview_render = False
        elapsed = (time.perf_counter() - t0) * 1000
        _preview_rendered_count += 1
        logger.debug(
            "PREVIEW: Rendered '%s' in %.1f ms (%dx%d)",
            cam.name,
            elapsed,
            render_w,
            render_h,
        )
        return offscreen
    except Exception:
        import traceback

        traceback.print_exc()
        try:
            if "offscreen" in locals():
                offscreen.free()
        except Exception:
            pass
        _in_preview_render = False
        logger.error("PREVIEW: Failed to render thumbnail for '%s'", cam.name)
        return None


def _draw_grid():
    """Draw the camera grid overlay in the 3D viewport using the GPU/BLF drawing API."""
    layout = _compute_grid_layout(bpy.context)
    if not layout:
        return

    # ---
    # Unpack computed layout values.
    # ---
    cameras = layout["cameras"]
    columns = layout["columns"]
    start_index = layout["start_index"]
    end_index = layout["end_index"]
    start_row = layout["start_row"]
    origin_x = layout["origin_x"]
    origin_y = layout["origin_y"]
    tw = layout["tw"]
    th = layout["th"]
    gap = layout["gap"]
    radius = layout["radius"]
    scale = layout["scale"]
    active_camera = layout["active_camera"]
    total_rows = layout["total_rows"]
    font_size = layout["font_size"]
    info_offset_y = layout["info_offset_y"]
    grid_width = layout["grid_width"]
    region = layout["region"]

    # ---
    # Theme, shader, and font setup for the draw pass.
    # ---
    try:
        colors = _get_theme_colors()
    except (AttributeError, IndexError, ReferenceError):
        return
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    font_id = FONT_ID
    blf.size(font_id, font_size)

    prefs = bpy.context.preferences.addons.get(__package__).preferences

    # ---
    # Evict orphaned thumbnails whose cameras no longer exist.
    # ---
    existing_camera_names = {cam.name for cam in cameras}
    for cam_name in list(_thumbnail_cache.keys()):
        if cam_name not in existing_camera_names:
            try:
                cached_data = _thumbnail_cache.pop(cam_name)
                cached_data[1].free()
            except Exception:
                pass

    # ---
    # Thumbnail mode: detect stale/missing tiles and queue precaching.
    # Precache extends N rows above and below the visible window.
    # ---
    if prefs.settings.display_type == "THUMBNAILS":
        active_scene = bpy.context.scene

        missing_visible = False
        for idx in range(start_index, end_index):
            cam = cameras[idx]
            cam_key = cam.name
            cached = _thumbnail_cache.get(cam_key)
            if cached is None:
                missing_visible = True
            else:
                cached_gen, _, cached_sig, _ = cached
                current_sig = _get_camera_state_signature(cam, active_scene)
                if not (cached_gen == _thumbnail_gen and cached_sig == current_sig):
                    _thumbnail_stale.add(cam_key)

        if missing_visible:
            precache_start_row = max(0, start_row - prefs.settings.preview_precache_rows)
            precache_end_row = start_row + layout["visible_rows"] + prefs.settings.preview_precache_rows

            precache_start_index = precache_start_row * columns
            precache_end_index = min(len(cameras), precache_end_row * columns)

            visible_indices = set(range(start_index, end_index))

            queue_candidates = []
            for idx in range(start_index, end_index):
                queue_candidates.append(idx)
            for idx in range(precache_start_index, precache_end_index):
                if idx not in visible_indices:
                    queue_candidates.append(idx)

            precache_keys = {cameras[idx].name for idx in queue_candidates}

            # Drop queued renders for tiles no longer in the precache window.
            for pending_key in list(_thumbnail_pending):
                if pending_key not in precache_keys:
                    _thumbnail_pending.discard(pending_key)

            for idx in queue_candidates:
                cam = cameras[idx]
                cam_key = cam.name

                cached = _thumbnail_cache.get(cam_key)
                if cached is None:
                    if cam_key not in _thumbnail_pending and not _in_preview_render:
                        _queue_thumbnail_render(cam_key)
                else:
                    cached_gen, _, cached_sig, _ = cached
                    current_sig = _get_camera_state_signature(cam, active_scene)
                    if not (cached_gen == _thumbnail_gen and cached_sig == current_sig):
                        _thumbnail_stale.add(cam_key)

    # ---
    # Draw the semi-transparent background panel that contains all tiles.
    # Extend bounds to include the scrollbar when present.
    # ---
    bg_margin = gap
    grid_left = origin_x - bg_margin
    grid_right = origin_x + grid_width + bg_margin
    grid_bottom = origin_y - bg_margin
    grid_top = origin_y + th * layout["visible_rows"] + (layout["visible_rows"] - 1) * gap + bg_margin

    sb = _get_scrollbar_layout(layout)
    if sb:
        grid_alignment = layout.get("grid_alignment", "CENTER")
        if grid_alignment == "LEFT":
            grid_left = sb["track_left"] - bg_margin
        else:
            sb_w = SCROLLBAR_WIDTH * scale
            grid_right = sb["track_left"] + sb_w + bg_margin

    bg_radius = radius * 2
    perimeter = get_rounded_rect_perimeter(
        grid_left, grid_bottom, grid_right - grid_left, grid_top - grid_bottom, bg_radius
    )
    fill_coords = (
        [(grid_left + (grid_right - grid_left) / 2, grid_bottom + (grid_top - grid_bottom) / 2)]
        + perimeter
        + [perimeter[0]]
    )
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": fill_coords})
    shader.bind()

    shader.uniform_float("color", _rgba(color_contrast(colors["tile_default"], 0.5), 0.75))
    gpu.state.blend_set("ALPHA")
    batch.draw(shader)
    gpu.state.blend_set("NONE")

    active_scene = bpy.context.scene

    # ---
    # Render each visible camera tile: thumbnail or colored panel,
    # selection/active borders, and truncated name label.
    # ---
    for i in range(start_index, end_index):
        cam = cameras[i]
        column = i % columns
        row = i // columns
        drawn_row = row - start_row

        x = origin_x + column * (tw + gap)
        y = origin_y + drawn_row * (th + gap)

        # Skip tiles outside the visible viewport.
        if y > region.height or y + th < 0:
            continue

        selected = cam.select_get()
        is_active = cam == active_camera

        if prefs.settings.display_type == "THUMBNAILS":
            cam_key = cam.name
            cached = _thumbnail_cache.get(cam_key)

            is_valid = False
            is_stale = False
            current_sig = _get_camera_state_signature(cam, active_scene)

            if cached is not None:
                cached_gen, offscreen, cached_sig, _ = cached
                if cached_gen == _thumbnail_gen and cached_sig == current_sig:
                    is_valid = True
                elif cached_gen == _thumbnail_gen:
                    is_stale = True

            # Priority 1: draw a valid cached thumbnail, dimmed if active or not selected.
            if is_valid:
                _thumbnail_cache[cam_key] = (cached[0], cached[1], cached[2], time.monotonic())
                _thumbnail_stale.discard(cam_key)
                offscreen = cached[1]
                gpu.state.depth_mask_set(False)
                gpu.state.blend_set("ALPHA")
                draw_texture_2d(offscreen.texture_color, (x, y), tw, th)

                if (is_active or not selected) and (not _mouse_in_grid or is_active):
                    dim_color = (
                        _rgba(colors["tile_picked"][:3], 0.15) if is_active else _rgba(colors["tile_default"][:3], 0.15)
                    )
                    dim_perimeter = get_rounded_rect_perimeter(x, y, tw, th, radius)
                    dim_coords = [(x + tw / 2, y + th / 2)] + dim_perimeter + [dim_perimeter[0]]
                    dim_batch = batch_for_shader(shader, "TRI_FAN", {"pos": dim_coords})
                    shader.bind()
                    shader.uniform_float("color", dim_color)
                    dim_batch.draw(shader)

                gpu.state.blend_set("NONE")
                gpu.state.depth_mask_set(True)
            # Priority 2: stale thumbnail — show dimmed preview beneath a fallback color.
            elif is_stale:
                _thumbnail_stale.add(cam_key)
                color = colors["tile_default"]
                perimeter = get_rounded_rect_perimeter(x, y, tw, th, radius)
                fill_coords = [(x + tw / 2, y + th / 2)] + perimeter + [perimeter[0]]
                batch = batch_for_shader(shader, "TRI_FAN", {"pos": fill_coords})
                shader.bind()
                shader.uniform_float("color", color)
                gpu.state.blend_set("ALPHA")
                batch.draw(shader)

                offscreen = cached[1]
                draw_texture_2d(offscreen.texture_color, (x, y), tw, th)

                dim_color = _rgba(colors["tile_default"], 0.85)
                dim_perimeter = get_rounded_rect_perimeter(x, y, tw, th, radius)
                dim_coords = [(x + tw / 2, y + th / 2)] + dim_perimeter + [dim_perimeter[0]]
                dim_batch = batch_for_shader(shader, "TRI_FAN", {"pos": dim_coords})
                shader.bind()
                shader.uniform_float("color", dim_color)
                dim_batch.draw(shader)
                gpu.state.blend_set("NONE")
            # Priority 3: no thumbnail available — draw fallback colored tile.
            else:
                color = colors["tile_default"]
                if _mouse_in_grid:
                    color = color_contrast(colors["tile_default"], 1.1)

                perimeter = get_rounded_rect_perimeter(x, y, tw, th, radius)
                fill_coords = [(x + tw / 2, y + th / 2)] + perimeter + [perimeter[0]]
                batch = batch_for_shader(shader, "TRI_FAN", {"pos": fill_coords})
                shader.bind()
                shader.uniform_float("color", color)
                batch.draw(shader)

        else:
            # Draw colored tile — picked color for active camera, hover highlight else.
            color = colors["tile_default"]
            if is_active:
                color = colors["tile_picked"]
            elif _mouse_in_grid:
                color = color_contrast(colors["tile_default"], 1.1)
            perimeter = get_rounded_rect_perimeter(x, y, tw, th, radius)
            fill_coords = [(x + tw / 2, y + th / 2)] + perimeter + [perimeter[0]]
            batch = batch_for_shader(shader, "TRI_FAN", {"pos": fill_coords})
            shader.bind()
            shader.uniform_float("color", color)
            batch.draw(shader)

        # ---
        # Draw thumbnail border, active highlight, and selection outline.
        # ---
        if prefs.settings.display_type == "THUMBNAILS":
            border_coords = [(x + tw, y), (x, y), (x, y + th), (x + tw, y + th), (x + tw, y)]
            line_batch = batch_for_shader(shader, "LINE_STRIP", {"pos": border_coords})
            gpu.state.line_width_set(2.0 * scale)
            shader.bind()
            gpu.state.blend_set("ALPHA")
            shader.uniform_float("color", _rgba(color_contrast(colors["text"], 0.85), 0.05))
            line_batch.draw(shader)
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set("NONE")

        if prefs.settings.display_type == "THUMBNAILS" and is_active and not selected:
            perimeter = get_rounded_rect_perimeter(x, y, tw, th, radius)
            border_coords = perimeter + [perimeter[0]]
            line_batch = batch_for_shader(shader, "LINE_STRIP", {"pos": border_coords})

            gpu.state.line_width_set(1.5 * scale)
            shader.bind()
            shader.uniform_float("color", colors["tile_picked"])
            line_batch.draw(shader)
            gpu.state.line_width_set(1.0)

        if selected:
            perimeter = get_rounded_rect_perimeter(x, y, tw, th, radius)
            border_coords = perimeter + [perimeter[0]]
            line_batch = batch_for_shader(shader, "LINE_STRIP", {"pos": border_coords})

            gpu.state.line_width_set(2.0 * scale)
            shader.bind()
            shader.uniform_float("color", colors["border_active"])
            line_batch.draw(shader)
            gpu.state.line_width_set(1.0)

        if selected and is_active:
            inset_amount = 2.0 * scale
            inner_x = x + inset_amount
            inner_y = y + inset_amount
            inner_tw = tw - 2.0 * inset_amount
            inner_th = th - 2.0 * inset_amount
            if inner_tw > 0 and inner_th > 0:
                inner_radius = max(0.0, radius - inset_amount)
                inner_perimeter = get_rounded_rect_perimeter(inner_x, inner_y, inner_tw, inner_th, inner_radius)
                inner_border_coords = inner_perimeter + [inner_perimeter[0]]
                inner_batch = batch_for_shader(shader, "LINE_STRIP", {"pos": inner_border_coords})
                gpu.state.line_width_set(1.5 * scale)
                shader.bind()
                shader.uniform_float("color", colors["tile_picked"])
                inner_batch.draw(shader)
                gpu.state.line_width_set(1.0)

        # ---
        # Truncate the camera name to fit the tile width.
        # ---
        text = cam.name
        if prefs.settings.display_type == "THUMBNAILS":
            max_text_width = tw - 12 * scale
        else:
            max_text_width = tw - 8 * scale
        if blf.dimensions(font_id, text)[0] > max_text_width:
            while text and blf.dimensions(font_id, text + "...")[0] > max_text_width:
                text = text[:-1]
            text += "..." if text else ""
        text_width, text_height = blf.dimensions(font_id, text)

        # ---
        # Draw the name label — badge overlay in thumbnail mode, centered in tile mode.
        # ---
        if prefs.settings.display_type == "THUMBNAILS":
            badge_font_id = BADGE_FONT_ID
            blf.size(badge_font_id, max(6, int(FONT_SIZE)))
            badge_text_width, badge_text_height = blf.dimensions(badge_font_id, text)

            pad = 4 * scale
            bg_pad = 2 * scale
            bg_w = badge_text_width + pad * 2
            bg_h = badge_text_height + pad * 2
            bg_x = x + bg_pad
            bg_y = y + bg_pad

            if is_active:
                bg_color = colors["tile_picked"]
            elif selected:
                bg_color = colors["border_active"]
            else:
                bg_color = colors["tile_default"]
            bg_perimeter = get_rounded_rect_perimeter(bg_x, bg_y, bg_w, bg_h, bg_pad)
            bg_fill = [(bg_x + bg_w / 2, bg_y + bg_h / 2)] + bg_perimeter + [bg_perimeter[0]]
            bg_batch = batch_for_shader(shader, "TRI_FAN", {"pos": bg_fill})
            gpu.state.blend_set("ALPHA")
            shader.bind()
            shader.uniform_float("color", _rgba(bg_color[:3], 0.85))
            bg_batch.draw(shader)
            gpu.state.blend_set("NONE")

            text_x = bg_x + pad
            text_y = bg_y + pad
            _draw_text_with_shadow(badge_font_id, text, text_x, text_y, colors["text"], scale)
        else:
            text_x = x + (tw - text_width) / 2
            text_y = y + (th - text_height) / 2
            _draw_text_with_shadow(font_id, text, text_x, text_y, colors["text"], scale)

    # ---
    # Build and draw the footer info text: camera count, scroll range,
    # selection count, and loading indicator.
    # ---
    n = len(cameras)
    info_text = f"{n} Camera{'s' if n != 1 else ''}"

    if total_rows > layout["effective_max_rows"]:
        info_text = f"{info_text} ({start_index + 1}-{end_index})"

        sb_layout = _get_scrollbar_layout(layout)
        if sb_layout:
            # Draw the scrollbar thumb for grids with more rows than visible space.
            track_left = sb_layout["track_left"]
            thumb_y = sb_layout["thumb_y"]
            thumb_h = sb_layout["thumb_h"]
            sb_w = SCROLLBAR_WIDTH * scale

            thumb_coords = (
                (track_left, thumb_y),
                (track_left + sb_w, thumb_y),
                (track_left + sb_w, thumb_y + thumb_h),
                (track_left, thumb_y),
                (track_left + sb_w, thumb_y + thumb_h),
                (track_left, thumb_y + thumb_h),
            )
            thumb_batch = batch_for_shader(shader, "TRIS", {"pos": thumb_coords})
            shader.bind()
            shader.uniform_float("color", color_contrast(colors["tile_default"], 1.4))
            thumb_batch.draw(shader)

    selected_count = sum(1 for cam in cameras if cam.select_get())
    if selected_count:
        info_text = f"{selected_count} Selected | {info_text}"

    if _render_timer_active:
        info_text = f"Loading... | {info_text}"

    info_width, info_height = blf.dimensions(font_id, info_text)

    if prefs.settings.alignment == "LEFT":
        info_x = origin_x
    elif prefs.settings.alignment == "RIGHT":
        info_x = origin_x + grid_width - info_width
    else:
        info_x = origin_x + (grid_width - info_width) / 2

    info_y = origin_y - info_offset_y

    _draw_text_with_shadow(font_id, info_text, info_x, info_y, colors["info_text"], scale)


def is_grid_active(context=None) -> bool:
    """Return True if the camera grid overlay is shown for the given context's area."""
    if _handler is None:
        return False
    if context is None:
        return True
    area = getattr(context, "area", None)
    if area is None:
        return False
    return area.as_pointer() == _target_area_pointer


def _reset_grid_state():
    """Reset all grid overlay global state to defaults."""
    global _handler, _target_area_pointer, _target_region_pointer
    global _modal_operator, _current_start_row, _last_active_index, _mouse_in_grid
    global _drag_state, _drag_tile, _drag_last_tile, _drag_last_scroll_time, _drag_select_value
    global _preview_rendered_count, _render_elapsed_ms
    global _original_shading_type, _original_show_overlays
    global _thumbnail_stale

    context = bpy.context
    target_area = next(
        (a for w in context.window_manager.windows for a in w.screen.areas if a.as_pointer() == _target_area_pointer),
        None,
    )
    space_view3d = target_area.spaces.active if target_area and target_area.type == "VIEW_3D" else None
    _cleanup_shading_mode(space_view3d)

    _invalidate_thumbnails()
    _handler = None
    _target_area_pointer = None
    _target_region_pointer = None
    _modal_operator = None
    _current_start_row = -1
    _last_active_index = -1
    _mouse_in_grid = False
    _drag_state = _DragState.IDLE
    _drag_tile = -1
    _drag_last_tile = -1
    _drag_last_scroll_time = 0.0
    _drag_select_value = False
    _preview_rendered_count = 0
    _render_elapsed_ms = 0.0
    _original_shading_type = None
    _original_show_overlays = None


def toggle_grid():
    """Toggle the camera grid draw handler in the active 3D viewport.

    If the grid is currently shown in a different area, it is moved to the
    current area. Only one grid instance exists at a time.
    """
    global _handler, _target_area_pointer, _target_region_pointer
    current_area_ptr = bpy.context.area.as_pointer()

    if _handler is not None:
        if _target_area_pointer == current_area_ptr:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(_handler, "WINDOW")
            except (ValueError, AttributeError):
                pass
            _reset_grid_state()
            redraw_ui("VIEW_3D")
            return
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handler, "WINDOW")
        except (ValueError, AttributeError):
            pass
        redraw_ui("VIEW_3D", area_pointer=_target_area_pointer)

    _reset_grid_state()
    _target_area_pointer = current_area_ptr
    _target_region_pointer = bpy.context.region.as_pointer()
    _handler = bpy.types.SpaceView3D.draw_handler_add(_draw_grid, (), "WINDOW", "POST_PIXEL")
    bpy.ops.camgrid.interactive_grid("INVOKE_DEFAULT")
    redraw_ui("VIEW_3D", area_pointer=_target_area_pointer)


# ------------------------------------------------------------------------
#    Operators
# ------------------------------------------------------------------------


class CAMGRID_OT_toggle_grid(Operator):
    bl_idname = "camgrid.toggle_grid"
    bl_label = "Camera Grid"
    bl_description = (
        "Toggle the camera grid.\n\n"
        "LMB / Wheel / Arrows - Switch camera\n"
        "LMB+Drag - Quick-switch through cameras\n"
        "Ctrl+Wheel - Navigate up/down\n"
        "Shift+Wheel - Scroll rows\n"
        "Drag Scrollbar - Scroll cameras\n"
        "RMB+Drag - Paint-select cameras\n"
        "ESC - Turn off"
    )
    bl_options = {"INTERNAL"}

    def execute(self, context):
        toggle_grid()
        return {"FINISHED"}


class CAMGRID_OT_interactive_grid(Operator):
    """Clickable camera grid overlay — click a tile to activate that camera."""

    bl_idname = "camgrid.interactive_grid"
    bl_label = "Interactive Camera Grid"
    bl_options = {"INTERNAL"}

    def modal(self, context, event):
        global _modal_operator

        if _modal_operator is not self:
            return {"CANCELLED"}

        if _target_area_pointer is not None:
            area = getattr(context, "area", None)
            if area is None or area.as_pointer() != _target_area_pointer:
                return {"PASS_THROUGH"}

        region = getattr(context, "region", None)
        if region is None or region.type != "WINDOW":
            return {"PASS_THROUGH"}

        event_type = _get_safe_event_type(event)

        if event_type == "ESC" and event.value == "PRESS":
            if is_grid_active(context):
                toggle_grid()
            return {"CANCELLED"}

        if event_type == "MOUSEMOVE":
            return self._handle_mousemove(context, event)
        if event_type in {"LEFTMOUSE", "RIGHTMOUSE"}:
            if event.value == "PRESS":
                return self._handle_mouse_press(context, event, event_type)
            if event.value == "RELEASE":
                return self._handle_mouse_release(context, event, event_type)
        if event_type == "MIDDLEMOUSE":
            return {"PASS_THROUGH"}
        if event_type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return self._handle_wheel(context, event, event_type)
        if event_type in {"LEFT_ARROW", "RIGHT_ARROW", "UP_ARROW", "DOWN_ARROW"} and event.value == "PRESS":
            return self._handle_arrow(context, event, event_type)
        return {"PASS_THROUGH"}

    def _update_scrollbar_scroll(self, layout, sb_layout, mouse_y):
        """Update absolute start row mapping based on mouse coordinate relative to scrollbar."""
        global _current_start_row
        track_bottom = sb_layout["track_bottom"]
        track_h = sb_layout["track_h"]
        thumb_h = sb_layout["thumb_h"]
        max_scroll = sb_layout["max_scroll"]

        travel = track_h - thumb_h
        if travel <= 0:
            return

        y_rel = (mouse_y - track_bottom - thumb_h / 2) / travel
        t = max(0.0, min(1.0, y_rel))

        new_row = round(t * max_scroll)
        if _current_start_row != new_row:
            _current_start_row = new_row
            redraw_ui("VIEW_3D", area_pointer=_target_area_pointer)

    def _handle_mousemove(self, context, event):
        global _mouse_in_grid, _drag_state, _drag_tile, _drag_last_tile, _drag_last_scroll_time, _current_start_row
        layout = _compute_grid_layout(context)
        if layout:
            in_grid = _is_mouse_in_grid(layout, event.mouse_region_x, event.mouse_region_y)
            if in_grid != _mouse_in_grid:
                _mouse_in_grid = in_grid
                redraw_ui("VIEW_3D", area_pointer=_target_area_pointer)
        elif _mouse_in_grid:
            _mouse_in_grid = False
            redraw_ui("VIEW_3D", area_pointer=_target_area_pointer)

        if _drag_state == _DragState.IDLE:
            return {"PASS_THROUGH"}

        if _drag_state == _DragState.SCROLLBAR_DRAGGING and layout:
            sb_layout = _get_scrollbar_layout(layout)
            if sb_layout:
                self._update_scrollbar_scroll(layout, sb_layout, event.mouse_region_y)
            return {"RUNNING_MODAL"}

        if _drag_state == _DragState.LMB_PRESSED and layout:
            tile = _get_tile_at_mouse(layout, event.mouse_region_x, event.mouse_region_y)
            if tile is not None and tile != _drag_tile:
                _drag_state = _DragState.LMB_DRAGGING
                _drag_last_tile = tile
                _action_switch_camera(layout, tile)

        elif _drag_state == _DragState.LMB_DRAGGING and layout:
            _drag_last_tile = _drag_tile_action(
                layout,
                event.mouse_region_x,
                event.mouse_region_y,
                _drag_last_tile,
                _action_switch_camera,
            )

        elif _drag_state == _DragState.RMB_PRESSED and layout:
            tile = _get_tile_at_mouse(layout, event.mouse_region_x, event.mouse_region_y)
            if tile is not None and tile != _drag_tile:
                _drag_state = _DragState.RMB_DRAGGING
                _drag_last_tile = tile
                _action_select_camera(layout, tile)

        elif _drag_state == _DragState.RMB_DRAGGING and layout:
            _drag_last_tile = _drag_tile_action(
                layout,
                event.mouse_region_x,
                event.mouse_region_y,
                _drag_last_tile,
                _action_select_camera,
            )

        if _drag_state in (_DragState.LMB_DRAGGING, _DragState.RMB_DRAGGING) and layout:
            total_rows = layout["total_rows"]
            if total_rows > layout["visible_rows"]:
                th = layout["th"]
                gap = layout["gap"]
                my = event.mouse_region_y
                bottom_edge = layout["origin_y"]
                top_edge = layout["origin_y"] + layout["visible_rows"] * (th + gap)
                max_scroll = total_rows - layout["visible_rows"]
                now = time.monotonic()

                if my < bottom_edge and _current_start_row > 0:
                    if now - _drag_last_scroll_time > 0.12:
                        _current_start_row -= 1
                        _drag_last_scroll_time = now
                        redraw_ui("VIEW_3D", area_pointer=_target_area_pointer)
                elif my > top_edge and _current_start_row < max_scroll:
                    if now - _drag_last_scroll_time > 0.12:
                        _current_start_row += 1
                        _drag_last_scroll_time = now
                        redraw_ui("VIEW_3D", area_pointer=_target_area_pointer)

        return {"RUNNING_MODAL"}

    def _handle_mouse_press(self, context, event, event_type):
        global _drag_state, _drag_tile, _drag_last_tile, _drag_select_value

        if _drag_state != _DragState.IDLE:
            return {"RUNNING_MODAL"}

        layout = _compute_grid_layout(context)
        if not layout:
            _drag_state = _DragState.IDLE
            _drag_tile = -1
            _drag_last_tile = -1
            return {"PASS_THROUGH"}

        mx = event.mouse_region_x
        my = event.mouse_region_y

        sb_layout = _get_scrollbar_layout(layout)
        if sb_layout and event_type == "LEFTMOUSE":
            if (
                sb_layout["hit_left"] <= mx <= sb_layout["hit_right"]
                and sb_layout["track_bottom"] <= my <= sb_layout["track_top"]
            ):
                _drag_state = _DragState.SCROLLBAR_DRAGGING
                self._update_scrollbar_scroll(layout, sb_layout, my)
                return {"RUNNING_MODAL"}

        cameras = layout["cameras"]
        active_camera = layout["active_camera"]
        prefs = context.preferences.addons.get(__package__).preferences
        tile_index = _get_tile_at_mouse(layout, mx, my)

        if tile_index is not None:
            cam = cameras[tile_index]
            if event_type == "RIGHTMOUSE":
                _drag_state = _DragState.RMB_PRESSED
                _drag_tile = tile_index
                _drag_last_tile = -1

                _drag_select_value = not cam.select_get()
                cam.select_set(_drag_select_value)
                if _drag_select_value:
                    context.view_layer.objects.active = cam
                redraw_ui("VIEW_3D", area_pointer=_target_area_pointer)
                return {"RUNNING_MODAL"}

            _drag_state = _DragState.LMB_PRESSED
            _drag_tile = tile_index
            _drag_last_tile = -1
            if cam != active_camera:
                context.scene.camera = cam
                _switch_to_camera_view(context)
            if prefs.settings.display_type == "THUMBNAILS":
                cam_key = cameras[tile_index].name
                if cam_key in _thumbnail_stale:
                    _thumbnail_stale.discard(cam_key)
                    if cam_key not in _thumbnail_pending and not _in_preview_render:
                        _queue_thumbnail_render(cam_key)
            return {"RUNNING_MODAL"}

        _drag_state = _DragState.IDLE
        _drag_tile = -1
        _drag_last_tile = -1
        if _is_mouse_in_grid(layout, mx, my):
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def _handle_mouse_release(self, context, event, event_type):
        global _drag_state, _drag_tile, _drag_last_tile
        if _drag_state in (_DragState.LMB_PRESSED, _DragState.LMB_DRAGGING) and event_type == "LEFTMOUSE":
            _drag_state = _DragState.IDLE
            _drag_tile = -1
            _drag_last_tile = -1
            return {"RUNNING_MODAL"}
        if _drag_state in (_DragState.RMB_PRESSED, _DragState.RMB_DRAGGING) and event_type == "RIGHTMOUSE":
            _drag_state = _DragState.IDLE
            _drag_tile = -1
            _drag_last_tile = -1
            return {"RUNNING_MODAL"}
        if _drag_state == _DragState.SCROLLBAR_DRAGGING and event_type == "LEFTMOUSE":
            _drag_state = _DragState.IDLE
            _drag_tile = -1
            _drag_last_tile = -1
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def _handle_wheel(self, context, event, event_type):
        global _drag_state
        if _drag_state != _DragState.IDLE:
            return {"RUNNING_MODAL"}

        layout = _compute_grid_layout(context)
        if not layout:
            return {"PASS_THROUGH"}

        if not _is_mouse_in_grid(layout, event.mouse_region_x, event.mouse_region_y):
            return {"PASS_THROUGH"}

        prefs = context.preferences.addons.get(__package__).preferences
        wheel_scrolls = prefs.settings.wheel_mode == "SCROLL"

        sb_layout = _get_scrollbar_layout(layout)
        is_over_scrollbar = False
        if sb_layout:
            mx = event.mouse_region_x
            my = event.mouse_region_y
            if (
                sb_layout["hit_left"] <= mx <= sb_layout["hit_right"]
                and sb_layout["track_bottom"] <= my <= sb_layout["track_top"]
            ):
                is_over_scrollbar = True

        should_scroll = is_over_scrollbar or (event.shift != wheel_scrolls)

        if should_scroll:
            grid_max_rows = layout["effective_max_rows"]
            total_rows = layout["total_rows"]
            max_scroll = total_rows - grid_max_rows
            if max_scroll > 0:
                global _current_start_row
                delta = -1 if event_type == "WHEELDOWNMOUSE" else 1
                old_row = _current_start_row
                _current_start_row = max(0, min(_current_start_row + delta, max_scroll))
                if _current_start_row != old_row:
                    redraw_ui("VIEW_3D", area_pointer=_target_area_pointer)
            return {"RUNNING_MODAL"}

        cameras = layout["cameras"]
        total = len(cameras)
        active_index = layout["active_index"]

        if event.ctrl:
            columns = layout["columns"]
            full_rows = total // columns
            rem = total % columns
            col_order = []
            for c in range(columns):
                n = full_rows + (1 if c < rem else 0)
                for r in range(n):
                    col_order.append(r * columns + c)
            vert_idx = col_order.index(active_index)
            if event_type == "WHEELUPMOUSE":
                new_index = col_order[(vert_idx + 1) % total]
            else:
                new_index = col_order[(vert_idx - 1 + total) % total]
        else:
            if event_type == "WHEELUPMOUSE":
                new_index = (active_index + 1) % total
            else:
                new_index = (active_index - 1 + total) % total

        if new_index != active_index and 0 <= new_index < total:
            context.scene.camera = cameras[new_index]
        return {"RUNNING_MODAL"}

    def _handle_arrow(self, context, event, event_type):
        global _drag_state
        if _drag_state != _DragState.IDLE:
            return {"RUNNING_MODAL"}

        layout = _compute_grid_layout(context)
        if not layout:
            return {"PASS_THROUGH"}

        if not _is_mouse_in_grid(layout, event.mouse_region_x, event.mouse_region_y):
            return {"PASS_THROUGH"}

        cameras = layout["cameras"]
        total = len(cameras)
        columns = layout["columns"]
        active_index = layout["active_index"]

        if event_type == "LEFT_ARROW":
            new_index = (active_index - 1 + total) % total
        elif event_type == "RIGHT_ARROW":
            new_index = (active_index + 1) % total
        elif event_type == "UP_ARROW":
            new_index = active_index + columns if active_index + columns < total else active_index
        else:
            new_index = active_index - columns if active_index - columns >= 0 else active_index

        if new_index != active_index and 0 <= new_index < total:
            context.scene.camera = cameras[new_index]
        return {"RUNNING_MODAL"}

    def invoke(self, context, event):
        global _modal_operator
        if _modal_operator is not None:
            return {"CANCELLED"}
        context.window_manager.modal_handler_add(self)
        _modal_operator = self
        return {"RUNNING_MODAL"}


class CAMGRID_OT_refresh_previews(Operator):
    bl_idname = "camgrid.refresh_previews"
    bl_label = "Refresh Previews"
    bl_description = "Clear the camera preview thumbnail cache and regenerate them"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        _invalidate_thumbnails()
        _thumbnail_stale.clear()
        for cam in bpy.data.objects:
            if cam.type == "CAMERA":
                _queue_thumbnail_render(cam.name)
        redraw_ui("VIEW_3D")
        return {"FINISHED"}


class CAMGRID_OT_frame_camera_above_grid(Operator):
    bl_idname = "camgrid.frame_camera_above_grid"
    bl_label = "Frame Camera Above Grid"
    bl_description = "Fit camera view to the viewport with margins, respecting sidebar and toolbar panels"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        if not context.area or context.area.type != "VIEW_3D":
            return False
        if not context.scene.camera:
            return False
        space = context.space_data
        if not space or not hasattr(space, "region_3d") or not space.region_3d:
            return False
        return True

    def execute(self, context):
        area = context.area
        region = context.region

        if not region or region.type != "WINDOW":
            for r in area.regions:
                if r.type == "WINDOW":
                    region = r
                    break

        if not region:
            self.report({"WARNING"}, "No viewport region found")
            return {"CANCELLED"}

        # Switch to camera view if not already
        space = context.space_data
        if space.region_3d.view_perspective != "CAMERA":
            try:
                bpy.ops.view3d.view_camera("EXEC_DEFAULT")
            except Exception:
                pass

        region_h = float(region.height)
        region_w = float(region.width)
        if region_h <= 0 or region_w <= 0:
            return {"CANCELLED"}

        if is_grid_active(context):
            layout = _compute_grid_layout(context, area=area, region=region)
        else:
            layout = None

        if layout:
            margin = 15 * layout["scale"]
            grid_top = layout["origin_y"] + layout["visible_rows"] * (layout["th"] + layout["gap"]) + margin
            scale = layout["scale"]
        else:
            scale = _get_ui_scale()
            grid_top = 30.0 * scale

        top_margin = 30.0 * scale
        grid_frac = min(0.6, grid_top / region_h)

        left_overlap, right_overlap = _get_left_right_overlap(area)
        ui_scale = _get_ui_scale()
        side_padding = (HORIZONTAL_PADDING * ui_scale) / 2.0
        avail_w = max(1.0, region_w - left_overlap - right_overlap - 2.0 * side_padding)
        avail_vh = max(1.0, (1.0 - grid_frac) * region_h - top_margin)

        if grid_frac <= 0:
            return {"CANCELLED"}

        # 1. Reset framing to standard perfect center (auto-frame bounds)
        try:
            bpy.ops.view3d.view_center_camera("EXEC_DEFAULT")
        except Exception:
            pass

        rv3d = context.space_data.region_3d

        # 2. Extract baseline zoom level established by centering.
        # Blender's view_camera_zoom-to-scale conversion:
        #   zoom_factor = (√2/100 * zoom + 1)²     (see Blender SE #332311)
        # At zoom=0, zoom_factor=1.0 (no scaling).
        z_base = float(rv3d.view_camera_zoom)
        sqrt2_100 = math.sqrt(2.0) / 100.0
        zoom_factor_base = max(0.01, (sqrt2_100 * z_base + 1.0) ** 2)

        # 3. Determine aspect ratios
        render = context.scene.render
        if render.resolution_y > 0 and render.pixel_aspect_y > 0:
            cam_aspect = (render.resolution_x * render.pixel_aspect_x) / (render.resolution_y * render.pixel_aspect_y)
        else:
            cam_aspect = 1.0

        view_aspect = region_w / region_h

        # 4. Calculate frame size at baseline
        if cam_aspect > view_aspect:
            fit_frame_w = region_w
            fit_frame_h = region_w / cam_aspect
        else:
            fit_frame_h = region_h
            fit_frame_w = region_h * cam_aspect

        # 5. Determine tighter constraint: frame width (sidebar/toolbar) vs. height (grid)
        scale_h = avail_w / fit_frame_w
        scale_v = avail_vh / fit_frame_h
        scale = min(scale_h, scale_v, 1.0)

        if scale < 1.0:
            zoom_factor_new = zoom_factor_base * scale
            z_new = (1.0 / sqrt2_100) * (math.sqrt(zoom_factor_new) - 1.0)
            rv3d.view_camera_zoom = max(-29.9, z_new)
        else:
            scale = 1.0
            zoom_factor_new = zoom_factor_base
            rv3d.view_camera_zoom = z_base

        # 6. Center frame horizontally between toolbar/sidebar, then shift it up
        #    so the frame bottom aligns with the grid top.
        # Blender's view_camera_offset sensitivity (per StackExchange #332311):
        #   1.0 offset = zoom_factor × region_dim pixels
        # This holds for any zoom level and both axes — no magic constants.
        zoom_factor_final = (sqrt2_100 * rv3d.view_camera_zoom + 1.0) ** 2

        K_h = zoom_factor_final * region_w
        shift_x = (right_overlap - left_overlap) / 2.0
        offset_x = shift_x / K_h

        K_v = zoom_factor_final * region_h
        shift_needed = (grid_top - top_margin) / 2.0
        offset_y = shift_needed / K_v

        rv3d.view_camera_offset[0] = offset_x
        rv3d.view_camera_offset[1] = -offset_y

        return {"FINISHED"}


classes = (
    CAMGRID_OT_toggle_grid,
    CAMGRID_OT_interactive_grid,
    CAMGRID_OT_refresh_previews,
    CAMGRID_OT_frame_camera_above_grid,
)


def register():
    pass


def unregister():
    global _handler, _target_area_pointer, _target_region_pointer
    global _modal_operator, _current_start_row, _last_active_index, _mouse_in_grid
    global _drag_state, _drag_tile, _drag_last_tile, _drag_last_scroll_time, _drag_select_value
    global _preview_rendered_count, _render_elapsed_ms
    global _original_shading_type, _original_show_overlays
    global _thumbnail_stale
    _invalidate_thumbnails()
    _modal_operator = None
    _current_start_row = -1
    _last_active_index = -1
    _mouse_in_grid = False
    _drag_state = _DragState.IDLE
    _drag_tile = -1
    _drag_last_tile = -1
    _drag_last_scroll_time = 0.0
    _drag_select_value = False
    _preview_rendered_count = 0
    _original_shading_type = None
    _original_show_overlays = None
    if _handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handler, "WINDOW")
        except (ValueError, AttributeError):
            pass
        _handler = None
        _target_area_pointer = None
        _target_region_pointer = None
