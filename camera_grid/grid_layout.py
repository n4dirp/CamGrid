"""Camera grid layout computation and hit-testing."""

from dataclasses import dataclass

import bpy
from bpy.types import Context, Region

from .grid_state import GridState
from .helpers import (
    _get_asset_shelf_height,
    _get_bottom_header_height,
    _get_left_right_overlap,
    _get_ui_scale,
    _optimize_grid_columns,
    _theme,
)

# ------------------------------------------------------------------------
#    Constants
# ------------------------------------------------------------------------

DOT_WIDTH = 18
DOT_HEIGHT = 9
TILE_HEIGHT = 22
TILE_GAP = 5
BOTTOM_MARGIN = TILE_HEIGHT + TILE_GAP + 4
HORIZONTAL_PADDING = 30
SHADOW_OFFSET = 1

GRID_TOP_SAFE_ZONE = 140

SCROLLBAR_WIDTH = 4
SCROLLBAR_WIDTH_HOVER = 6
SCROLLBAR_PADDING = TILE_GAP
SCROLLBAR_MIN_THUMB = 8

FONT_SIZE = 11
FONT_ID = 0
BADGE_FONT_ID = 0
INFO_TEXT_OFFSET_Y = 18 + TILE_GAP


# ------------------------------------------------------------------------
#    Dataclasses (Layouts)
# ------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class GridLayout:
    """Stores precomputed geometry and metrics for the grid layout."""

    cameras: list[bpy.types.Object]
    total_cameras: int
    columns: int
    start_index: int
    end_index: int
    start_row: int
    origin_x: float
    origin_y: float
    tw: float
    th: float
    gap: float
    radius: float
    panel_radius: float
    scale: float
    region: Region
    active_camera: bpy.types.Object | None
    active_object: bpy.types.Object | None
    active_index: int
    total_rows: int
    font_size: int
    info_offset_y: float
    grid_width: float
    grid_alignment: str
    visible_rows: int
    effective_max_rows: int
    left_overlap: float
    right_overlap: float
    area_pointer: int
    hovered_tile: int | None
    scrollbar_hovered: bool
    master_alpha: float


@dataclass(slots=True, kw_only=True)
class ScrollbarLayout:
    """Stores precomputed geometry and metrics for the scrollbar."""

    track_left: float
    track_bottom: float
    track_top: float
    track_h: float
    thumb_y: float
    thumb_h: float
    hit_left: float
    hit_right: float
    max_scroll: int


# ------------------------------------------------------------------------
#    Layout Computations
# ------------------------------------------------------------------------


def _has_info_content(prefs) -> bool:
    return (
        prefs.settings.show_active_camera_name
        or prefs.settings.show_camera_lens
        or prefs.settings.show_camera_sensor
        or prefs.settings.show_camera_depth_of_field
        or prefs.settings.show_camera_clip
        or prefs.settings.show_camera_count
    )


def _is_redo_panel_visible(context: Context) -> bool:
    """Return True if the Adjust Last Operation panel is currently visible."""
    area = getattr(context, "area", None)
    if not area:
        return False
    for region in area.regions:
        if region.type == "HUD" and region.width > 1 and region.height > 1 and region.x > 0 and region.y > 0:
            return True
    return False


def _compute_grid_layout(context: Context, area=None, region=None, scene=None) -> GridLayout | None:
    scene = scene or getattr(context, "scene", None)
    if not scene:
        return None
    props = getattr(scene, "camgrid_props", None)
    if not props:
        return None

    cam_col = props.source_collection
    source_objs = cam_col.objects if cam_col else bpy.data.objects
    cameras = sorted((obj for obj in source_objs if obj.type == "CAMERA"), key=lambda o: o.name)

    prefs = context.preferences.addons.get(__package__).preferences
    view_layer = getattr(context, "view_layer", None)
    if view_layer is not None and not prefs.settings.show_hidden_cameras:
        cameras = [cam for cam in cameras if cam.name in view_layer.objects]

    region = region or getattr(context, "region", None)
    area = area or getattr(context, "area", None)
    if not region or not area:
        return None

    try:
        area_ptr = area.as_pointer()
    except ReferenceError:
        return None

    state = GridState.areas.get(area_ptr)
    if not state or not state.enabled:
        return None

    if state.target_region_pointer:
        try:
            if region.as_pointer() != state.target_region_pointer:
                return None
        except ReferenceError:
            return None

    if not prefs.settings.show_hidden_cameras:
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

    if prefs.settings.display_mode == "THUMBNAILS":
        render = scene.render
        aspect = (render.resolution_x * render.pixel_aspect_x) / (render.resolution_y * render.pixel_aspect_y)
        max_side = prefs.settings.preview_size
        preview_w, preview_h = (
            (max_side, round(max_side / aspect)) if aspect >= 1.0 else (round(max_side * aspect), max_side)
        )
        tw, th = preview_w * scale, preview_h * scale
        effective_max_rows = prefs.settings.preview_max_rows
    elif prefs.settings.display_mode == "DOTS":
        tw = DOT_WIDTH * scale
        th = DOT_HEIGHT * scale
        effective_max_rows = prefs.settings.dots_max_rows
    else:
        tw = prefs.settings.tile_size * scale
        th = TILE_HEIGHT * scale
        effective_max_rows = prefs.settings.max_rows

    gap = TILE_GAP * scale
    widget_roundness = _theme("user_interface.wcol_regular.roundness", 0)
    panel_roundness = _theme("user_interface.panel_roundness", 0)
    tile_radius = widget_roundness * 10.0 * scale
    panel_radius_val = panel_roundness * 10.0 * scale
    bottom_margin = BOTTOM_MARGIN * scale + shelf_height + bottom_header_height
    if not _has_info_content(prefs):
        bottom_margin -= (INFO_TEXT_OFFSET_Y - 3) * scale

    min_region_height = bottom_margin + GRID_TOP_SAFE_ZONE + th + gap
    min_region_width = left_overlap + right_overlap + HORIZONTAL_PADDING * scale + tw
    if region.height < min_region_height or region.width < min_region_width:
        return None

    max_avail_height = float(region.height) - GRID_TOP_SAFE_ZONE - bottom_margin
    max_fit_rows = max(1, int((max_avail_height + gap) / (th + gap)))
    effective_max_rows = min(effective_max_rows, max_fit_rows)

    side_padding = (HORIZONTAL_PADDING * scale) / 2.0
    left_bound = left_overlap + side_padding
    if _is_redo_panel_visible(context):
        left_bound = max(left_bound, left_overlap + 300 * scale)
    right_bound = region.width - right_overlap - side_padding

    if prefs.settings.alignment == "CENTER":
        center_x = region.width / 2.0
        max_half_width = min(center_x - left_bound, right_bound - center_x)
        max_available_width = max(0.0, max_half_width * 2.0)
    else:
        max_available_width = max(0.0, right_bound - left_bound)

    max_cols = max(1, int(max_available_width / (tw + gap)))
    max_cols_pref = (
        prefs.settings.preview_max_columns
        if prefs.settings.display_mode == "THUMBNAILS"
        else prefs.settings.dots_max_columns
        if prefs.settings.display_mode == "DOTS"
        else prefs.settings.max_columns
    )
    max_cols = min(max_cols, max_cols_pref)

    columns = _optimize_grid_columns(total_cameras, max_cols, effective_max_rows, max_available_width, tw, gap)
    active_camera = scene.camera
    active_object = view_layer.objects.active if view_layer else None
    active_index = cameras.index(active_camera) if active_camera in cameras else 0

    total_rows = (total_cameras + columns - 1) // columns
    active_row = active_index // columns
    max_scroll = max(0, total_rows - effective_max_rows)

    if active_index != state.last_active_index:
        if state.current_start_row == -1:
            state.current_start_row = max(0, active_row - effective_max_rows // 2)
        else:
            if active_row < state.current_start_row:
                state.current_start_row = active_row
            elif active_row >= state.current_start_row + effective_max_rows:
                state.current_start_row = active_row - effective_max_rows + 1
        state.last_active_index = active_index

    state.current_start_row = max(0, min(state.current_start_row, max_scroll))
    start_row = state.current_start_row

    start_index = start_row * columns
    end_index = min(total_cameras, start_index + effective_max_rows * columns)
    actual_columns = min(columns, total_cameras)
    grid_width = actual_columns * (tw + gap) - gap

    match prefs.settings.alignment:
        case "LEFT":
            origin_x = round(left_bound)
        case "RIGHT":
            origin_x = round(right_bound - grid_width)
        case _:
            origin_x = round((region.width - grid_width) / 2.0)
            origin_x = max(left_bound, min(origin_x, right_bound - grid_width))

    return GridLayout(
        cameras=cameras,
        total_cameras=total_cameras,
        columns=columns,
        start_index=start_index,
        end_index=end_index,
        start_row=start_row,
        origin_x=origin_x,
        origin_y=bottom_margin,
        tw=tw,
        th=th,
        gap=gap,
        radius=tile_radius,
        panel_radius=panel_radius_val,
        scale=scale,
        region=region,
        active_camera=active_camera,
        active_object=active_object,
        active_index=active_index,
        total_rows=total_rows,
        font_size=max(8, int(FONT_SIZE * scale)),
        info_offset_y=INFO_TEXT_OFFSET_Y * scale,
        grid_width=grid_width,
        grid_alignment=prefs.settings.alignment,
        visible_rows=min(effective_max_rows, total_rows - start_row),
        effective_max_rows=effective_max_rows,
        left_overlap=left_overlap,
        right_overlap=right_overlap,
        area_pointer=area_ptr,
        hovered_tile=state.hovered_tile,
        scrollbar_hovered=state.scrollbar_hovered,
        master_alpha=prefs.settings.master_alpha,
    )


def _get_scrollbar_layout(layout: GridLayout) -> ScrollbarLayout | None:
    if layout.total_rows <= layout.effective_max_rows:
        return None

    sb_pad = SCROLLBAR_PADDING * layout.scale
    sb_w = SCROLLBAR_WIDTH * layout.scale
    track_left = layout.origin_x + layout.grid_width + sb_pad
    track_h = layout.effective_max_rows * (layout.th + layout.gap) - layout.gap

    visible_rows = layout.effective_max_rows
    thumb_ratio = visible_rows / layout.total_rows
    thumb_h = max(track_h * thumb_ratio, SCROLLBAR_MIN_THUMB * layout.scale)
    max_scroll = layout.total_rows - visible_rows

    thumb_t = 1.0 - layout.start_row / max_scroll if max_scroll > 0 else 0.0
    thumb_y = layout.origin_y + (track_h - thumb_h) * thumb_t

    hit_width = 12 * layout.scale
    hit_left = track_left - (hit_width - sb_w) / 2
    return ScrollbarLayout(
        track_left=track_left,
        track_bottom=layout.origin_y,
        track_top=layout.origin_y + track_h,
        track_h=track_h,
        thumb_y=thumb_y,
        thumb_h=thumb_h,
        hit_left=hit_left,
        hit_right=hit_left + hit_width,
        max_scroll=max_scroll,
    )


# ------------------------------------------------------------------------
#    Hit-Testing
# ------------------------------------------------------------------------


def _get_tile_at_mouse(layout: GridLayout, mouse_x: float, mouse_y: float) -> int | None:
    for i in range(layout.start_index, layout.end_index):
        column = i % layout.columns
        drawn_row = (i // layout.columns) - layout.start_row
        box_x = layout.origin_x + column * (layout.tw + layout.gap)
        box_y = layout.origin_y + (layout.visible_rows - 1 - drawn_row) * (layout.th + layout.gap)

        if box_x <= mouse_x <= box_x + layout.tw and box_y <= mouse_y <= box_y + layout.th:
            return i
    return None


def _is_mouse_in_grid(layout: GridLayout, mouse_x: float, mouse_y: float) -> bool:
    grid_left = layout.origin_x - layout.gap
    grid_right = layout.origin_x + layout.grid_width + layout.gap
    grid_bottom = layout.origin_y - layout.gap
    grid_top = layout.origin_y + layout.visible_rows * (layout.th + layout.gap)

    if sb := _get_scrollbar_layout(layout):
        grid_right = max(grid_right, sb.hit_right)

    return grid_left <= mouse_x <= grid_right and grid_bottom <= mouse_y <= grid_top
