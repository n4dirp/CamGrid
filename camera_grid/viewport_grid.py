"""Viewport camera grid overlay for Camera Grid extension."""

import logging
import math
import time
from dataclasses import dataclass
from enum import Enum, auto

import blf
import bpy
import gpu
from bpy.types import Context, Event, Operator, Region
from gpu_extras.presets import draw_texture_2d

from .gpu_draw import (
    _draw_filled_rounded_rect,
    _draw_pill,
    _draw_pill_border,
    _draw_rounded_rect_border,
    _draw_text_with_shadow,
    _get_theme_colors,
)
from .helpers import (
    _get_asset_shelf_height,
    _get_bottom_header_height,
    _get_left_right_overlap,
    _get_ui_scale,
    _optimize_grid_columns,
    _rgba,
    _theme,
    # color_contrast,
    redraw_ui,
)

logger = logging.getLogger(__package__)

# ------------------------------------------------------------------------
#    Constants
# ------------------------------------------------------------------------

DOT_WIDTH = 18
DOT_HEIGHT = 9
# TILE_WIDTH = 48
TILE_HEIGHT = 24
TILE_GAP = 4
BOTTOM_MARGIN = TILE_HEIGHT + TILE_GAP + 2
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


class _DragState(Enum):
    IDLE = auto()
    LMB_PRESSED = auto()
    LMB_DRAGGING = auto()
    RMB_PRESSED = auto()
    RMB_DRAGGING = auto()
    SCROLLBAR_DRAGGING = auto()


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
#    State Management
# ------------------------------------------------------------------------


class GridState:
    """Encapsulates all interactive and UI state for the camera grid."""

    handler: object | None = None
    target_area_pointer: int | None = None
    target_region_pointer: int | None = None
    modal_operator: Operator | None = None

    current_start_row: int = -1
    last_active_index: int = -1
    mouse_in_grid: bool = False
    hovered_tile: int | None = None
    scrollbar_hovered: bool = False

    drag_state: _DragState = _DragState.IDLE
    drag_tile: int = -1
    drag_last_tile: int = -1
    drag_last_scroll_time: float = 0.0
    drag_select_value: bool = False

    @classmethod
    def reset(cls):
        cls.handler = None
        cls.target_area_pointer = None
        cls.target_region_pointer = None
        cls.modal_operator = None
        cls.current_start_row = -1
        cls.last_active_index = -1
        cls.mouse_in_grid = False
        cls.scrollbar_hovered = False
        cls.drag_state = _DragState.IDLE
        cls.drag_tile = -1
        cls.drag_last_tile = -1
        cls.drag_last_scroll_time = 0.0
        cls.drag_select_value = False


class ThumbnailManager:
    """Encapsulates offscreen rendering, caching, and state restoration."""

    cache: dict[str, tuple[int, gpu.types.GPUOffScreen, tuple, float]] = {}
    gen: int = 0
    pending: set[str] = set()
    stale: set[str] = set()

    in_preview_render: bool = False
    render_timer_active: bool = False
    preview_rendered_count: int = 0
    render_elapsed_ms: float = 0.0

    original_shading_type: str | None = None
    original_show_overlays: bool | None = None

    @classmethod
    def invalidate(cls):
        for item in list(cls.cache.values()):
            try:
                item[1].free()
            except Exception:
                pass
        cls.cache.clear()
        cls.pending.clear()
        cls.stale.clear()
        cls.gen += 1
        cls.in_preview_render = False
        cls.render_timer_active = False
        cls.preview_rendered_count = 0
        cls.render_elapsed_ms = 0.0

        cls._restore_viewport()

        cls.original_shading_type = None
        cls.original_show_overlays = None
        logger.debug("PREVIEW: Cache invalidated (gen %d)", cls.gen)

    @classmethod
    def queue_render(cls, cam_key: str):
        cls.pending.add(cam_key)
        if not cls.render_timer_active:
            cls.render_timer_active = True
            bpy.app.timers.register(_process_thumbnail_queue, first_interval=0.01)

    @classmethod
    def cleanup_shading(cls, space_view3d=None):
        if cls.original_shading_type is not None:
            if space_view3d:
                if space_view3d.shading.type != cls.original_shading_type:
                    try:
                        space_view3d.shading.type = cls.original_shading_type
                    except ReferenceError:
                        pass
                if (
                    cls.original_show_overlays is not None
                    and space_view3d.overlay.show_overlays != cls.original_show_overlays
                ):
                    try:
                        space_view3d.overlay.show_overlays = cls.original_show_overlays
                        logger.debug("PREVIEW: Overlays restored to %s", cls.original_show_overlays)
                    except ReferenceError:
                        pass
            cls.original_shading_type = None
            cls.original_show_overlays = None

    @classmethod
    def _restore_viewport(cls):
        context = bpy.context
        target_area = next(
            (
                a
                for w in context.window_manager.windows
                for a in w.screen.areas
                if a.as_pointer() == GridState.target_area_pointer
            ),
            None,
        )
        space_view3d = target_area.spaces.active if target_area and target_area.type == "VIEW_3D" else None
        cls.cleanup_shading(space_view3d)


# ------------------------------------------------------------------------
#    Geometry Helpers
# ------------------------------------------------------------------------


def _has_info_content(prefs) -> bool:
    return (
        prefs.settings.show_active_camera_name
        or prefs.settings.show_camera_settings
        or prefs.settings.show_camera_count
    )


# ------------------------------------------------------------------------
#    Layout Computations
# ------------------------------------------------------------------------


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
    if view_layer is not None and not prefs.settings.show_hidden:
        cameras = [cam for cam in cameras if cam.name in view_layer.objects]

    region = region or getattr(context, "region", None)
    area = area or getattr(context, "area", None)
    if not region or not area:
        return None

    try:
        area_ptr = area.as_pointer()
    except ReferenceError:
        return None

    if GridState.target_area_pointer and area_ptr != GridState.target_area_pointer:
        return None

    if GridState.target_region_pointer:
        try:
            if region.as_pointer() != GridState.target_region_pointer:
                return None
        except ReferenceError:
            return None

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
        preview_w, preview_h = (
            (max_side, round(max_side / aspect)) if aspect >= 1.0 else (round(max_side * aspect), max_side)
        )
        tw, th = preview_w * scale, preview_h * scale
        effective_max_rows = prefs.settings.preview_max_rows
    elif prefs.settings.display_type == "DOTS":
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
        if prefs.settings.display_type == "THUMBNAILS"
        else prefs.settings.dots_max_columns
        if prefs.settings.display_type == "DOTS"
        else prefs.settings.max_columns
    )
    max_cols = min(max_cols, max_cols_pref)

    columns = _optimize_grid_columns(total_cameras, max_cols, effective_max_rows, max_available_width, tw, gap)
    active_camera = scene.camera
    active_index = cameras.index(active_camera) if active_camera in cameras else 0

    total_rows = (total_cameras + columns - 1) // columns
    active_row = active_index // columns
    max_scroll = max(0, total_rows - effective_max_rows)

    if active_index != GridState.last_active_index:
        if GridState.current_start_row == -1:
            GridState.current_start_row = max(0, active_row - effective_max_rows // 2)
        else:
            if active_row < GridState.current_start_row:
                GridState.current_start_row = active_row
            elif active_row >= GridState.current_start_row + effective_max_rows:
                GridState.current_start_row = active_row - effective_max_rows + 1
        GridState.last_active_index = active_index

    GridState.current_start_row = max(0, min(GridState.current_start_row, max_scroll))
    start_row = GridState.current_start_row

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
    )


def _get_scrollbar_layout(layout: GridLayout) -> ScrollbarLayout | None:
    if layout.total_rows <= layout.effective_max_rows:
        return None

    sb_pad = SCROLLBAR_PADDING * layout.scale
    sb_w = SCROLLBAR_WIDTH * layout.scale
    track_left = (
        layout.origin_x - sb_pad - sb_w
        if layout.grid_alignment == "LEFT"
        else layout.origin_x + layout.grid_width + sb_pad
    )
    track_h = layout.effective_max_rows * (layout.th + layout.gap) - layout.gap

    visible_rows = layout.effective_max_rows
    thumb_ratio = visible_rows / layout.total_rows
    thumb_h = max(track_h * thumb_ratio, SCROLLBAR_MIN_THUMB * layout.scale)
    max_scroll = layout.total_rows - visible_rows

    thumb_t = layout.start_row / max_scroll if max_scroll > 0 else 0
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
#    Interaction / Interaction Helpers
# ------------------------------------------------------------------------


def _get_tile_at_mouse(layout: GridLayout, mouse_x: float, mouse_y: float) -> int | None:
    for i in range(layout.start_index, layout.end_index):
        column = i % layout.columns
        drawn_row = (i // layout.columns) - layout.start_row
        box_x = layout.origin_x + column * (layout.tw + layout.gap)
        box_y = layout.origin_y + drawn_row * (layout.th + layout.gap)

        if box_x <= mouse_x <= box_x + layout.tw and box_y <= mouse_y <= box_y + layout.th:
            return i
    return None


def _is_mouse_in_grid(layout: GridLayout, mouse_x: float, mouse_y: float) -> bool:
    grid_left = layout.origin_x - layout.gap
    grid_right = layout.origin_x + layout.grid_width + layout.gap
    grid_bottom = layout.origin_y - layout.gap
    grid_top = layout.origin_y + layout.visible_rows * (layout.th + layout.gap)

    if sb := _get_scrollbar_layout(layout):
        if layout.grid_alignment == "LEFT":
            grid_left = min(grid_left, sb.hit_left)
        else:
            grid_right = max(grid_right, sb.hit_right)

    return grid_left <= mouse_x <= grid_right and grid_bottom <= mouse_y <= grid_top


def _switch_to_camera_view(context: Context):
    area = context.area
    if area and area.type == "VIEW_3D":
        space = area.spaces.active
        if space and space.type == "VIEW_3D":
            space.region_3d.view_perspective = "CAMERA"


def _apply_on_switch_action(context):
    prefs = context.preferences.addons.get(__package__).preferences
    match prefs.settings.on_switch_action:
        case "CAMERA_VIEW":
            _switch_to_camera_view(context)
        case "FRAME":
            try:
                bpy.ops.camgrid.frame_camera("INVOKE_DEFAULT")
            except Exception:
                pass


def _action_switch_camera(layout: GridLayout, tile_index: int, context=None):
    context = context or bpy.context
    if 0 <= tile_index < len(layout.cameras):
        context.scene.camera = layout.cameras[tile_index]
    _apply_on_switch_action(context)


def _action_select_camera(layout: GridLayout, tile_index: int):
    cam = layout.cameras[tile_index]
    try:
        cam.select_set(GridState.drag_select_value)
        if GridState.drag_select_value:
            bpy.context.view_layer.objects.active = cam
    except RuntimeError:
        pass
    redraw_ui("VIEW_3D", area_pointer=GridState.target_area_pointer)


def _drag_tile_action(layout: GridLayout, mx: float, my: float, ref_index: int, action_fn) -> int:
    if (tile_index := _get_tile_at_mouse(layout, mx, my)) is not None and tile_index != ref_index:
        action_fn(layout, tile_index)
        return tile_index
    return ref_index


# ------------------------------------------------------------------------
#    Preview Rendering
# ------------------------------------------------------------------------


def _get_camera_state_signature(cam: bpy.types.Object, scene: bpy.types.Scene) -> tuple:
    mw = cam.matrix_world
    matrix_tuple = tuple(mw[r][c] for r in range(4) for c in range(4))
    cd = cam.data
    return (
        matrix_tuple,
        getattr(cd, "lens", 0.0),
        getattr(cd, "sensor_width", 0.0),
        getattr(cd, "shift_x", 0.0),
        getattr(cd, "shift_y", 0.0),
        getattr(cd, "ortho_scale", 0.0),
    )


def _process_thumbnail_queue():
    if not ThumbnailManager.pending:
        ThumbnailManager._restore_viewport()
        ThumbnailManager.render_timer_active = False
        return None

    context = bpy.context
    target_area = next(
        (
            a
            for w in context.window_manager.windows
            for a in w.screen.areas
            if a.as_pointer() == GridState.target_area_pointer
        ),
        None,
    )
    if not target_area or target_area.type != "VIEW_3D":
        ThumbnailManager.cleanup_shading()
        ThumbnailManager.render_timer_active = False
        return None

    space_view3d = target_area.spaces.active
    region = next((r for r in target_area.regions if r.type == "WINDOW"), None)
    layout = _compute_grid_layout(context, area=target_area, region=region)
    if not space_view3d or not region or not layout:
        ThumbnailManager.cleanup_shading(space_view3d)
        ThumbnailManager.render_timer_active = False
        return None

    visible_keys = {layout.cameras[idx].name for idx in range(layout.start_index, layout.end_index)}
    visible_pending = list(ThumbnailManager.pending.intersection(visible_keys))
    offscreen_pending = list(ThumbnailManager.pending.difference(visible_keys))
    ordered_pending = visible_pending + offscreen_pending

    if not ordered_pending:
        ThumbnailManager.pending.clear()
        ThumbnailManager.cleanup_shading(space_view3d)
        ThumbnailManager.render_timer_active = False
        return None

    logger.trace(
        "PREVIEW: Queue depth — %d visible, %d offscreen (%d total)",
        len(visible_pending),
        len(offscreen_pending),
        len(ordered_pending),
    )

    prefs = context.preferences.addons.get(__package__).preferences
    batch_to_render = ordered_pending[: prefs.settings.preview_renders_per_tick]

    if ThumbnailManager.original_shading_type is None:
        ThumbnailManager.original_shading_type = space_view3d.shading.type
        if ThumbnailManager.original_shading_type != "SOLID":
            space_view3d.shading.type = "SOLID"
            logger.debug("PREVIEW: Temporarily switched shading to SOLID")

        if prefs.settings.preview_disable_overlays:
            ThumbnailManager.original_show_overlays = space_view3d.overlay.show_overlays
            if ThumbnailManager.original_show_overlays:
                space_view3d.overlay.show_overlays = False
                logger.debug("PREVIEW: Temporarily disabled viewport overlays")

    try:
        depsgraph = context.evaluated_depsgraph_get()
        batch_start = time.perf_counter()

        for cam_key in batch_to_render:
            ThumbnailManager.pending.discard(cam_key)
            if cam_obj := bpy.data.objects.get(cam_key):
                offscreen = _render_thumbnail(
                    cam_obj, context.scene, depsgraph, space_view3d, region, layout.tw, layout.th
                )
                if offscreen:
                    sig = _get_camera_state_signature(cam_obj, context.scene)
                    ThumbnailManager.cache[cam_key] = (ThumbnailManager.gen, offscreen, sig, time.monotonic())

                    if len(ThumbnailManager.cache) > prefs.settings.preview_cache_size:
                        oldest_key = min(ThumbnailManager.cache.keys(), key=lambda k: ThumbnailManager.cache[k][3])
                        oldest_data = ThumbnailManager.cache.pop(oldest_key)
                        try:
                            oldest_data[1].free()
                        except Exception:
                            pass
                        logger.trace(
                            "PREVIEW: Evicted '%s' (cache exceeded %d)", oldest_key, prefs.settings.preview_cache_size
                        )

        ThumbnailManager.render_elapsed_ms += (time.perf_counter() - batch_start) * 1000
        redraw_ui("VIEW_3D", area_pointer=GridState.target_area_pointer)
    except Exception as e:
        logger.error("PREVIEW: Exception during batch run: %s", str(e))

    if ThumbnailManager.pending:
        return 0.01

    ThumbnailManager.cleanup_shading(space_view3d)

    logger.trace(
        "PREVIEW: All %d thumbnails rendered in %.0f ms (%.1f ms avg)",
        ThumbnailManager.preview_rendered_count,
        ThumbnailManager.render_elapsed_ms,
        ThumbnailManager.render_elapsed_ms / max(ThumbnailManager.preview_rendered_count, 1),
    )

    ThumbnailManager.render_timer_active = False
    return None


def _render_thumbnail(cam, scene, depsgraph, space_view3d, region, tw, th):
    if ThumbnailManager.in_preview_render or not space_view3d:
        return None
    try:
        ThumbnailManager.in_preview_render = True
        t0 = time.perf_counter()

        scale = _get_ui_scale()
        prefs = bpy.context.preferences.addons.get(__package__).preferences
        r = scene.render
        aspect = (
            (r.resolution_x * r.pixel_aspect_x) / (r.resolution_y * r.pixel_aspect_y) if r.resolution_y > 0 else 1.0
        )
        max_side = int(prefs.settings.preview_size * scale)
        render_w, render_h = (
            (max_side, max(1, round(max_side / aspect)))
            if aspect >= 1.0
            else (max(1, round(max_side * aspect)), max_side)
        )

        offscreen = gpu.types.GPUOffScreen(render_w, render_h)
        view_matrix = cam.matrix_world.inverted()
        proj_matrix = cam.calc_matrix_camera(depsgraph, x=render_w, y=render_h)

        offscreen.draw_view3d(
            scene, depsgraph.view_layer, space_view3d, region, view_matrix, proj_matrix, do_color_management=True
        )

        ThumbnailManager.in_preview_render = False
        ThumbnailManager.preview_rendered_count += 1

        elapsed = (time.perf_counter() - t0) * 1000
        logger.trace("PREVIEW: Rendered '%s' in %.1f ms (%dx%d)", cam.name, elapsed, render_w, render_h)

        return offscreen
    except Exception as e:
        import traceback

        traceback.print_exc()
        try:
            offscreen.free()
        except Exception:
            pass
        ThumbnailManager.in_preview_render = False
        logger.error("PREVIEW: Failed to render thumbnail for '%s': %s", cam.name, str(e))
        return None


# ------------------------------------------------------------------------
#    Drawing Sub-routines
# ------------------------------------------------------------------------


def _evict_orphaned_thumbnails(cameras: list[bpy.types.Object]):
    existing = {c.name for c in cameras}
    for cam_name in list(ThumbnailManager.cache.keys()):
        if cam_name not in existing:
            try:
                ThumbnailManager.cache.pop(cam_name)[1].free()
            except Exception:
                pass


def _queue_missing_thumbnails(layout: GridLayout, prefs, active_scene):
    if prefs.settings.display_type != "THUMBNAILS":
        return

    missing_visible = False
    for idx in range(layout.start_index, layout.end_index):
        cam = layout.cameras[idx]
        if cached := ThumbnailManager.cache.get(cam.name):
            if not (cached[0] == ThumbnailManager.gen and cached[2] == _get_camera_state_signature(cam, active_scene)):
                ThumbnailManager.stale.add(cam.name)
        else:
            missing_visible = True

    if missing_visible:
        p_start_idx = max(0, layout.start_row - prefs.settings.preview_precache_rows) * layout.columns
        p_end_idx = min(
            len(layout.cameras),
            (layout.start_row + layout.visible_rows + prefs.settings.preview_precache_rows) * layout.columns,
        )

        candidates = list(range(layout.start_index, layout.end_index)) + [
            i for i in range(p_start_idx, p_end_idx) if i < layout.start_index or i >= layout.end_index
        ]
        precache_keys = {layout.cameras[i].name for i in candidates}

        for p_key in list(ThumbnailManager.pending):
            if p_key not in precache_keys:
                ThumbnailManager.pending.discard(p_key)

        for idx in candidates:
            cam = layout.cameras[idx]
            if cached := ThumbnailManager.cache.get(cam.name):
                if not (
                    cached[0] == ThumbnailManager.gen and cached[2] == _get_camera_state_signature(cam, active_scene)
                ):
                    ThumbnailManager.stale.add(cam.name)
            elif cam.name not in ThumbnailManager.pending and not ThumbnailManager.in_preview_render:
                ThumbnailManager.queue_render(cam.name)


def _draw_background_panel(layout: GridLayout, colors: dict):
    bg_margin = layout.gap + 1
    g_left = layout.origin_x - bg_margin
    g_right = layout.origin_x + layout.grid_width + bg_margin
    g_bottom = layout.origin_y - bg_margin
    g_top = layout.origin_y + layout.th * layout.visible_rows + (layout.visible_rows - 1) * layout.gap + bg_margin
    if sb := _get_scrollbar_layout(layout):
        if layout.grid_alignment == "LEFT":
            g_left = sb.track_left - bg_margin
        else:
            g_right = sb.track_left + SCROLLBAR_WIDTH * layout.scale + bg_margin

    radius = layout.panel_radius * 1
    shadow_offset = SHADOW_OFFSET * layout.scale

    _draw_filled_rounded_rect(
        g_left + 1, g_bottom - shadow_offset, g_right - g_left - 1, g_top - g_bottom, radius, (0.0, 0.0, 0.0, 0.4)
    )

    _draw_filled_rounded_rect(g_left, g_bottom, g_right - g_left, g_top - g_bottom, radius, colors["bg_color"])

    _draw_rounded_rect_border(
        g_left, g_bottom, g_right - g_left, g_top - g_bottom, radius, colors["panel_border"], 0.5 * layout.scale
    )


def _draw_dot_tiles(layout: GridLayout, colors: dict):
    """Draw camera tiles in DOTS mode — pill shapes with no text labels."""
    shadow_offset = SHADOW_OFFSET * layout.scale
    line_width = 0.15 * layout.scale

    for i in range(layout.start_index, layout.end_index):
        cam = layout.cameras[i]
        x = round(layout.origin_x + (i % layout.columns) * (layout.tw + layout.gap))
        y = round(layout.origin_y + ((i // layout.columns) - layout.start_row) * (layout.th + layout.gap))

        if y > layout.region.height or y + layout.th < 0:
            continue

        selected = cam.select_get()
        is_active = cam == layout.active_camera
        is_hovered = i == GridState.hovered_tile

        if is_active:
            base_col = colors["tile_picked"]
        else:
            base_col = colors["tile_default"]

        draw_pill = False
        if draw_pill:
            # Draw the background shadow
            _draw_pill(x, y - shadow_offset, layout.tw, layout.th, (0.0, 0.0, 0.0, 0.4))

            # Draw the tile background
            _draw_pill(x, y, layout.tw, layout.th, base_col)

            # Draw the tile highlight (if hovered)
            if is_hovered:
                _draw_pill(x, y, layout.tw, layout.th, _rgba(colors["text"], 0.04))

            _draw_pill_border(x, y, layout.tw, layout.th, colors["tile_border"], line_width)

            if selected:
                _draw_pill_border(x, y, layout.tw, layout.th, colors["border_active"], line_width)
        else:
            radius = layout.radius
            # Draw the background shadow
            _draw_filled_rounded_rect(x, y - shadow_offset, layout.tw, layout.th, radius, (0.0, 0.0, 0.0, 0.4))

            # Draw the tile background
            _draw_filled_rounded_rect(x, y, layout.tw, layout.th, radius, base_col)

            # Draw the tile highlight (if hovered)
            if is_hovered:
                _draw_filled_rounded_rect(x, y, layout.tw, layout.th, radius, _rgba(colors["text"], 0.04))

            _draw_rounded_rect_border(x, y, layout.tw, layout.th, radius, colors["tile_border"], line_width)

            if selected:
                _draw_rounded_rect_border(x, y, layout.tw, layout.th, radius, colors["border_active"], line_width)


def _draw_label_tiles(layout: GridLayout, colors: dict):
    """Draw camera tiles in TILES mode — rounded rects with centered text labels."""
    font_id = FONT_ID
    blf.size(font_id, layout.font_size)
    _, ref_font_h = blf.dimensions(font_id, "Ag")

    shadow_offset = SHADOW_OFFSET * layout.scale
    line_width = 0.5 * layout.scale
    ellipsis_width = blf.dimensions(font_id, "...")[0]
    inset = line_width * 2
    max_t_w = layout.tw - 8 * layout.scale
    radius = layout.radius

    for i in range(layout.start_index, layout.end_index):
        cam = layout.cameras[i]
        x = layout.origin_x + (i % layout.columns) * (layout.tw + layout.gap)
        y = layout.origin_y + ((i // layout.columns) - layout.start_row) * (layout.th + layout.gap)

        if y > layout.region.height or y + layout.th < 0:
            continue

        selected = cam.select_get()
        is_active = cam == layout.active_camera
        is_hovered = i == GridState.hovered_tile

        if is_active:
            base_col = colors["tile_picked"]
        else:
            base_col = colors["tile_default"]

        # Draw the background shadow
        _draw_filled_rounded_rect(x, y - shadow_offset, layout.tw, layout.th, radius, (0.0, 0.0, 0.0, 0.4))

        # Draw the tile background
        _draw_filled_rounded_rect(x, y, layout.tw, layout.th, radius, base_col)

        # Draw the tile highlight (if hovered)
        if is_hovered:
            _draw_filled_rounded_rect(x, y, layout.tw, layout.th, radius, _rgba(colors["text"], 0.04))

        _draw_rounded_rect_border(x, y, layout.tw, layout.th, radius, colors["tile_border"], line_width * 0.5)

        # Draw the tile border (if selected or active)
        if selected:
            _draw_rounded_rect_border(x, y, layout.tw, layout.th, radius, colors["border_active"], line_width)

            if selected and is_active:
                if layout.tw - 2 * inset > 0 and layout.th - 2 * inset > 0:
                    _draw_rounded_rect_border(
                        x + inset,
                        y + inset,
                        layout.tw - 2 * inset,
                        layout.th - 2 * inset,
                        max(0.0, layout.radius - inset),
                        colors["tile_picked"],
                        line_width,
                    )

        text = cam.name
        if blf.dimensions(font_id, text)[0] > max_t_w:
            max_w_no_ell = max_t_w - ellipsis_width
            left, right = len(text) // 2, len(text) // 2 + 1
            while (
                left > 0 and right < len(text) and blf.dimensions(font_id, text[:left] + text[right:])[0] > max_w_no_ell
            ):
                left -= 1
                right += 1
            text = text[:left] + "..." + text[right:]

        tw, _ = blf.dimensions(font_id, text)
        if selected:
            text_color = colors["border_active"]
        elif is_active:
            text_color = colors["tile_text"]
        else:
            text_color = colors["tile_text_inactive"]
        _draw_text_with_shadow(
            font_id, text, x + (layout.tw - tw) / 2, y + (layout.th - ref_font_h) / 2 + 1, text_color, layout.scale
        )


def _draw_thumbnail_tiles(layout: GridLayout, colors: dict, prefs, active_scene):
    """Draw camera tiles in THUMBNAILS mode — cached preview images with badge labels."""
    font_id = FONT_ID
    blf.size(font_id, layout.font_size)

    line_width = 0.5 * layout.scale
    radius = 0
    ellipsis_width = blf.dimensions(font_id, "...")[0]
    badge_pad = 4 * layout.scale
    max_t_w = layout.tw - 12 * layout.scale
    badge_font_size = max(6, int(FONT_SIZE * layout.scale))
    shadow_offset = SHADOW_OFFSET * layout.scale

    blf.size(BADGE_FONT_ID, badge_font_size)

    for i in range(layout.start_index, layout.end_index):
        cam = layout.cameras[i]
        x = layout.origin_x + (i % layout.columns) * (layout.tw + layout.gap)
        y = layout.origin_y + ((i // layout.columns) - layout.start_row) * (layout.th + layout.gap)

        if y > layout.region.height or y + layout.th < 0:
            continue

        selected = cam.select_get()
        is_active = cam == layout.active_camera
        is_hovered = i == GridState.hovered_tile

        cached = ThumbnailManager.cache.get(cam.name)
        is_valid, is_stale = False, False
        if cached:
            sig = _get_camera_state_signature(cam, active_scene)
            if cached[0] == ThumbnailManager.gen and cached[2] == sig:
                is_valid = True
            elif cached[0] == ThumbnailManager.gen:
                is_stale = True

        if is_valid:
            ThumbnailManager.cache[cam.name] = (cached[0], cached[1], cached[2], time.monotonic())
            ThumbnailManager.stale.discard(cam.name)
        elif is_stale:
            ThumbnailManager.stale.add(cam.name)

        # Tile Shadow
        _draw_filled_rounded_rect(x, y - shadow_offset, layout.tw, layout.th, radius, (0.0, 0.0, 0.0, 0.4))

        # Tile Background
        _draw_filled_rounded_rect(x, y, layout.tw, layout.th, radius, colors["tile_default"])

        # Draw Tile Texture
        if cached:
            draw_texture_2d(cached[1].texture_color, (x, y), layout.tw, layout.th)

        # Stale Tile Overlay
        if not is_valid and is_stale:
            _draw_filled_rounded_rect(x, y, layout.tw, layout.th, radius, _rgba(colors["tile_default"], 0.5))

        # Active Tile Overlay
        if is_active:
            _draw_filled_rounded_rect(x, y, layout.tw, layout.th, radius, _rgba(colors["tile_picked"], 0.25))

        # Hovered Tile Overlay
        if is_hovered:
            _draw_filled_rounded_rect(x, y, layout.tw, layout.th, radius, _rgba(colors["text"], 0.04))

        # Draw Light Tile Border
        _draw_rounded_rect_border(x, y, layout.tw, layout.th, radius, colors["tile_border"], line_width)

        # Selected Tile Border
        if selected or is_active:
            if selected and is_active:
                _draw_rounded_rect_border(x, y, layout.tw, layout.th, radius, colors["tile_picked"], line_width * 2)

            border_col = colors["border_active"] if selected else colors["tile_picked"]
            _draw_rounded_rect_border(x, y, layout.tw, layout.th, radius, border_col, line_width)

        # Tile Camera Name
        if prefs.settings.preview_show_names:
            text = cam.name
            if blf.dimensions(font_id, text)[0] > max_t_w:
                max_w_no_ell = max_t_w - ellipsis_width
                left, right = len(text) // 2, len(text) // 2 + 1
                while (
                    left > 0
                    and right < len(text)
                    and blf.dimensions(font_id, text[:left] + text[right:])[0] > max_w_no_ell
                ):
                    left -= 1
                    right += 1
                text = text[:left] + "..." + text[right:]

            btw, bth = (blf.dimensions(BADGE_FONT_ID, text)[0], 8 * layout.scale)
            bw, bh = btw + badge_pad * 2, bth + badge_pad * 2
            bx, by = x + round((layout.tw - bw) / 2), y + badge_pad

            bg_col = colors["tile_picked"] if is_active else colors["tile_default"]
            _draw_filled_rounded_rect(bx, by, bw, bh, badge_pad, _rgba(bg_col, 0.5))

            if selected:
                text_color = colors["border_active"]
            elif is_active:
                text_color = colors["tile_text"]
            else:
                text_color = colors["tile_text_inactive"]
            _draw_text_with_shadow(BADGE_FONT_ID, text, bx + badge_pad, by + badge_pad, text_color, layout.scale)


def _draw_camera_tiles(layout: GridLayout, colors: dict, prefs, active_scene):
    """Dispatch to the appropriate display-type draw function."""
    display_type = prefs.settings.display_type
    if display_type == "THUMBNAILS":
        _draw_thumbnail_tiles(layout, colors, prefs, active_scene)
    elif display_type == "TILES":
        _draw_label_tiles(layout, colors)
    else:  # DOTS
        _draw_dot_tiles(layout, colors)


def _draw_scrollbar(layout: GridLayout, colors: dict):
    if layout.total_rows <= layout.effective_max_rows:
        return
    if sb := _get_scrollbar_layout(layout):
        sb_w = SCROLLBAR_WIDTH * layout.scale
        is_hovered = GridState.scrollbar_hovered
        if is_hovered:
            sb_w_hover = SCROLLBAR_WIDTH_HOVER * layout.scale
            bar_left = sb.track_left + (sb_w - sb_w_hover) / 2
            bar_right = bar_left + sb_w_hover
        else:
            bar_left = sb.track_left
            bar_right = bar_left + sb_w
        alpha = 1.0 if is_hovered else 0.6
        color = _rgba(colors["scroll_bar"], alpha)
        _draw_pill(round(bar_left), round(sb.thumb_y), round(bar_right - bar_left), round(sb.thumb_h), color)


def _draw_footer_info(layout: GridLayout, colors: dict):
    font_id = FONT_ID
    blf.size(font_id, layout.font_size)
    prefs = bpy.context.preferences.addons.get(__package__).preferences
    parts = []

    if active_cam := layout.active_camera:
        data = active_cam.data
        cam_type = getattr(data, "type", "PERSP")

        if prefs.settings.show_active_camera_name:
            parts.append(active_cam.name)

        if prefs.settings.show_camera_settings:
            if cam_type == "PERSP":
                lens = getattr(data, "lens", 0)
                sensor = getattr(data, "sensor_width", 0)
                if lens > 0:
                    parts.append(f"Lens: {int(lens)} mm")
                if sensor > 0:
                    parts.append(f"Sensor: {sensor:.0f} mm")
            elif cam_type == "ORTHO":
                ortho_scale = getattr(data, "ortho_scale", None)
                if ortho_scale:
                    parts.append(f"Scale: {ortho_scale:.2f}")
            elif cam_type == "PANO":
                lens = getattr(data, "lens", 0)
                if lens > 0:
                    parts.append(f"Lens: {int(lens)} mm")

    if prefs.settings.show_camera_count:
        n = len(layout.cameras)
        count_str = f"Cameras: {n}"
        if layout.total_rows > layout.effective_max_rows:
            count_str = f"Cameras: {n} ({layout.start_index + 1}/{layout.end_index})"
        parts.append(count_str)

        if sel_count := sum(1 for cam in layout.cameras if cam.select_get()):
            parts.append(f"Selected: {sel_count}")

    if ThumbnailManager.render_timer_active:
        parts.append("Loading...")

    if not parts:
        return

    info_text = " | ".join(parts)
    iw, _ = blf.dimensions(font_id, info_text)

    if layout.grid_alignment == "LEFT":
        ix = layout.origin_x
    elif layout.grid_alignment == "RIGHT":
        ix = layout.origin_x + layout.grid_width - iw
    else:
        ix = layout.origin_x + (layout.grid_width - iw) / 2

    iy = layout.origin_y - layout.info_offset_y

    # Text Background
    # ih = layout.font_size
    # pad = 5 * layout.scale
    # _draw_filled_rounded_rect(
    #     round(ix - pad),
    #     round(iy - pad),
    #     round(iw + pad * 2),
    #     round(ih + pad * 1.5),
    #     layout.radius,
    #     _rgba(colors["bg_color"], 0.5),
    # )

    _draw_text_with_shadow(font_id, info_text, ix, iy, colors["info_text"], layout.scale)


def _draw_grid():
    layout = _compute_grid_layout(bpy.context)
    if not layout:
        return

    try:
        colors = _get_theme_colors()
    except (AttributeError, IndexError, ReferenceError):
        return

    prefs = bpy.context.preferences.addons.get(__package__).preferences
    active_scene = bpy.context.scene

    _evict_orphaned_thumbnails(layout.cameras)
    _queue_missing_thumbnails(layout, prefs, active_scene)

    _draw_background_panel(layout, colors)
    _draw_camera_tiles(layout, colors, prefs, active_scene)
    _draw_scrollbar(layout, colors)
    if _has_info_content(prefs):
        _draw_footer_info(layout, colors)


# ------------------------------------------------------------------------
#    API & Operators
# ------------------------------------------------------------------------


def refresh_thumbnail_cache():
    """Clear thumbnail cache and queue all cameras for re-render."""
    ThumbnailManager.invalidate()
    for cam in bpy.data.objects:
        if cam.type == "CAMERA":
            ThumbnailManager.queue_render(cam.name)
    redraw_ui("VIEW_3D")


def is_grid_active(context: Context | None = None) -> bool:
    if GridState.handler is None:
        return False
    if context is None:
        return True
    if area := getattr(context, "area", None):
        return area.as_pointer() == GridState.target_area_pointer
    return False


def _is_grid_key_event(context: Context, event: Event) -> bool:
    """Return True if a keyboard event targets the grid (mouse is hovering over it)."""
    if not is_grid_active(context):
        return False
    layout = _compute_grid_layout(context)
    return layout is not None and _is_mouse_in_grid(layout, event.mouse_region_x, event.mouse_region_y)


def toggle_grid(context: Context):
    curr_area_ptr = context.area.as_pointer()

    if GridState.handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(GridState.handler, "WINDOW")
        except (ValueError, AttributeError):
            pass

        target_ptr = GridState.target_area_pointer
        ThumbnailManager.invalidate()
        GridState.reset()

        if target_ptr == curr_area_ptr:
            redraw_ui("VIEW_3D")
            return
        redraw_ui("VIEW_3D", area_pointer=target_ptr)

    ThumbnailManager.invalidate()
    GridState.reset()
    GridState.target_area_pointer = curr_area_ptr
    GridState.target_region_pointer = context.region.as_pointer()
    GridState.handler = bpy.types.SpaceView3D.draw_handler_add(_draw_grid, (), "WINDOW", "POST_PIXEL")
    bpy.ops.camgrid.interactive_grid("INVOKE_DEFAULT")
    redraw_ui("VIEW_3D", area_pointer=GridState.target_area_pointer)


class CAMGRID_OT_toggle_grid(Operator):
    bl_idname = "camgrid.toggle_grid"
    bl_label = "Camera Grid"
    bl_description = (
        "Toggle the camera grid overlay.\n\n"
        "Shortcuts (Over-Grid):\n"
        "LMB / Wheel / Arrows - Switch camera.\n"
        "LMB+Drag - Quick-switch through cameras.\n"
        "RMB+Drag - Paint-select cameras.\n"
        "Ctrl+Wheel - Resize tiles.\n"
        "HOME - Frame camera.\n"
        "F5 - Refresh previews."
    )
    bl_options = {"INTERNAL"}

    def execute(self, context):
        toggle_grid(context)
        return {"FINISHED"}


class CAMGRID_OT_interactive_grid(Operator):
    bl_idname = "camgrid.interactive_grid"
    bl_label = "Interactive Camera Grid"

    bl_options = {"INTERNAL"}

    def modal(self, context: Context, event: Event):
        if GridState.modal_operator is not self:
            return {"CANCELLED"}

        area = getattr(context, "area", None)
        region = getattr(context, "region", None)
        if not area or not region or area.as_pointer() != GridState.target_area_pointer or region.type != "WINDOW":
            return {"PASS_THROUGH"}

        event_type = event.type

        match event_type:
            case "ESC" if event.value == "PRESS":
                prefs = context.preferences.addons.get(__package__).preferences
                if prefs.settings.close_on_esc:
                    if is_grid_active(context):
                        toggle_grid(context)
                    return {"CANCELLED"}
                return {"PASS_THROUGH"}

            case "MOUSEMOVE":
                return self._handle_mousemove(context, event)

            # Handle MOUSE PRESS and RELEASE using pattern guards
            case "LEFTMOUSE" | "RIGHTMOUSE" if event.value == "PRESS":
                return self._handle_mouse_press(context, event, event_type)

            case "LEFTMOUSE" | "RIGHTMOUSE" if event.value == "RELEASE":
                return self._handle_mouse_release(context, event, event_type)

            case "WHEELUPMOUSE" | "WHEELDOWNMOUSE":
                return self._handle_wheel(context, event, event_type)

            case "LEFT_ARROW" | "RIGHT_ARROW" | "UP_ARROW" | "DOWN_ARROW" if event.value == "PRESS":
                return self._handle_arrow(context, event, event_type)

            case "HOME" if event.value == "PRESS" and _is_grid_key_event(context, event):
                try:
                    bpy.ops.camgrid.frame_camera("INVOKE_DEFAULT")
                except Exception:
                    pass
                return {"RUNNING_MODAL"}

            case "F5" if event.value == "PRESS" and _is_grid_key_event(context, event):
                prefs = context.preferences.addons.get(__package__).preferences
                if prefs.settings.display_type == "THUMBNAILS":
                    try:
                        bpy.ops.camgrid.refresh_previews("INVOKE_DEFAULT")
                    except Exception:
                        pass
                return {"RUNNING_MODAL"}

            case _:
                return {"PASS_THROUGH"}

    def _update_scrollbar_scroll(self, layout: GridLayout, sb: ScrollbarLayout, my: float):
        travel = sb.track_h - sb.thumb_h
        if travel <= 0:
            return
        t = max(0.0, min(1.0, (my - sb.track_bottom - sb.thumb_h / 2) / travel))
        new_row = round(t * sb.max_scroll)
        if GridState.current_start_row != new_row:
            GridState.current_start_row = new_row
            redraw_ui("VIEW_3D", area_pointer=GridState.target_area_pointer)

    def _handle_mousemove(self, context: Context, event: Event):
        layout = _compute_grid_layout(context)
        if layout:
            in_grid = _is_mouse_in_grid(layout, event.mouse_region_x, event.mouse_region_y)
            hovered = _get_tile_at_mouse(layout, event.mouse_region_x, event.mouse_region_y)

            mx, my = event.mouse_region_x, event.mouse_region_y
            sb_hovered = False
            if GridState.drag_state == _DragState.SCROLLBAR_DRAGGING:
                sb_hovered = True
            elif sb := _get_scrollbar_layout(layout):
                if sb.hit_left <= mx <= sb.hit_right and sb.track_bottom <= my <= sb.track_top:
                    sb_hovered = True

            needs_redraw = False
            if in_grid != GridState.mouse_in_grid:
                GridState.mouse_in_grid = in_grid
                needs_redraw = True

            if hovered != GridState.hovered_tile:
                GridState.hovered_tile = hovered
                needs_redraw = True

            if sb_hovered != GridState.scrollbar_hovered:
                GridState.scrollbar_hovered = sb_hovered
                needs_redraw = True

            if needs_redraw:
                redraw_ui("VIEW_3D", area_pointer=GridState.target_area_pointer)
        elif GridState.mouse_in_grid or GridState.hovered_tile is not None or GridState.scrollbar_hovered:
            GridState.mouse_in_grid = False
            GridState.hovered_tile = None
            if GridState.drag_state != _DragState.SCROLLBAR_DRAGGING:
                GridState.scrollbar_hovered = False
            redraw_ui("VIEW_3D", area_pointer=GridState.target_area_pointer)

        if GridState.drag_state == _DragState.IDLE:
            return {"PASS_THROUGH"}

        mx, my = event.mouse_region_x, event.mouse_region_y

        match GridState.drag_state:
            case _DragState.SCROLLBAR_DRAGGING if layout:
                if sb := _get_scrollbar_layout(layout):
                    self._update_scrollbar_scroll(layout, sb, my)
            case _DragState.LMB_PRESSED if layout:
                if (t := _get_tile_at_mouse(layout, mx, my)) is not None and t != GridState.drag_tile:
                    GridState.drag_state = _DragState.LMB_DRAGGING
                    GridState.drag_last_tile = t
                    _action_switch_camera(layout, t, context)
            case _DragState.LMB_DRAGGING if layout:
                GridState.drag_last_tile = _drag_tile_action(
                    layout, mx, my, GridState.drag_last_tile, _action_switch_camera
                )
            case _DragState.RMB_PRESSED if layout:
                if (t := _get_tile_at_mouse(layout, mx, my)) is not None and t != GridState.drag_tile:
                    GridState.drag_state = _DragState.RMB_DRAGGING
                    GridState.drag_last_tile = t
                    _action_select_camera(layout, t)
            case _DragState.RMB_DRAGGING if layout:
                GridState.drag_last_tile = _drag_tile_action(
                    layout, mx, my, GridState.drag_last_tile, _action_select_camera
                )

        if (
            GridState.drag_state in (_DragState.LMB_DRAGGING, _DragState.RMB_DRAGGING)
            and layout
            and layout.total_rows > layout.visible_rows
        ):
            bottom_edge = layout.origin_y
            top_edge = layout.origin_y + layout.visible_rows * (layout.th + layout.gap)
            now = time.monotonic()
            if my < bottom_edge and GridState.current_start_row > 0 and now - GridState.drag_last_scroll_time > 0.12:
                GridState.current_start_row -= 1
                GridState.drag_last_scroll_time = now
                redraw_ui("VIEW_3D", area_pointer=GridState.target_area_pointer)
            elif (
                my > top_edge
                and GridState.current_start_row < (layout.total_rows - layout.visible_rows)
                and now - GridState.drag_last_scroll_time > 0.12
            ):
                GridState.current_start_row += 1
                GridState.drag_last_scroll_time = now
                redraw_ui("VIEW_3D", area_pointer=GridState.target_area_pointer)

        return {"RUNNING_MODAL"}

    def _handle_mouse_press(self, context: Context, event: Event, event_type: str):
        if GridState.drag_state != _DragState.IDLE:
            return {"RUNNING_MODAL"}
        layout = _compute_grid_layout(context)
        if not layout:
            return {"PASS_THROUGH"}

        mx, my = event.mouse_region_x, event.mouse_region_y
        if sb := _get_scrollbar_layout(layout):
            if (
                event_type == "LEFTMOUSE"
                and sb.hit_left <= mx <= sb.hit_right
                and sb.track_bottom <= my <= sb.track_top
            ):
                GridState.drag_state = _DragState.SCROLLBAR_DRAGGING
                self._update_scrollbar_scroll(layout, sb, my)
                return {"RUNNING_MODAL"}

        tile_index = _get_tile_at_mouse(layout, mx, my)
        if tile_index is not None:
            cam = layout.cameras[tile_index]
            if event_type == "RIGHTMOUSE":
                GridState.drag_state, GridState.drag_tile, GridState.drag_last_tile = (
                    _DragState.RMB_PRESSED,
                    tile_index,
                    -1,
                )
                GridState.drag_select_value = not cam.select_get()
                try:
                    cam.select_set(GridState.drag_select_value)
                    if GridState.drag_select_value:
                        context.view_layer.objects.active = cam
                except RuntimeError:
                    pass
                redraw_ui("VIEW_3D", area_pointer=GridState.target_area_pointer)
                return {"RUNNING_MODAL"}

            GridState.drag_state, GridState.drag_tile, GridState.drag_last_tile = _DragState.LMB_PRESSED, tile_index, -1
            if cam != layout.active_camera:
                context.scene.camera = cam
            _apply_on_switch_action(context)

            # Auto-Reload Thumbnail (Temporarily Disabled)
            # if context.preferences.addons.get(__package__).preferences.settings.display_type == "THUMBNAILS":
            #     if cam.name in ThumbnailManager.stale:
            #         ThumbnailManager.stale.discard(cam.name)
            #         if cam.name not in ThumbnailManager.pending and not ThumbnailManager.in_preview_render:
            #             ThumbnailManager.queue_render(cam.name)
            return {"RUNNING_MODAL"}

        if _is_mouse_in_grid(layout, mx, my):
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def _handle_mouse_release(self, context: Context, event: Event, event_type: str):
        if (
            GridState.drag_state in (_DragState.LMB_PRESSED, _DragState.LMB_DRAGGING, _DragState.SCROLLBAR_DRAGGING)
            and event_type == "LEFTMOUSE"
        ) or (GridState.drag_state in (_DragState.RMB_PRESSED, _DragState.RMB_DRAGGING) and event_type == "RIGHTMOUSE"):
            GridState.drag_state, GridState.drag_tile, GridState.drag_last_tile = _DragState.IDLE, -1, -1
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def _handle_wheel(self, context: Context, event: Event, event_type: str):
        if GridState.drag_state != _DragState.IDLE:
            return {"RUNNING_MODAL"}
        layout = _compute_grid_layout(context)
        if not layout or not _is_mouse_in_grid(layout, event.mouse_region_x, event.mouse_region_y):
            return {"PASS_THROUGH"}

        prefs = context.preferences.addons.get(__package__).preferences

        if event.ctrl:
            delta = 8 if event_type == "WHEELUPMOUSE" else -8
            if prefs.settings.display_type == "THUMBNAILS":
                prefs.settings.preview_size = max(64, min(512, prefs.settings.preview_size + delta))
            elif prefs.settings.display_type == "DOTS":
                return {"RUNNING_MODAL"}
            else:
                prefs.settings.tile_size = max(60, min(512, prefs.settings.tile_size + delta))
            redraw_ui("VIEW_3D", area_pointer=GridState.target_area_pointer)
            return {"RUNNING_MODAL"}

        should_scroll = event.shift != (prefs.settings.wheel_mode == "SCROLL")

        if sb := _get_scrollbar_layout(layout):
            if (
                sb.hit_left <= event.mouse_region_x <= sb.hit_right
                and sb.track_bottom <= event.mouse_region_y <= sb.track_top
            ):
                should_scroll = True

        if should_scroll:
            if (max_scroll := layout.total_rows - layout.effective_max_rows) > 0:
                old_row = GridState.current_start_row
                GridState.current_start_row = max(
                    0, min(old_row + (-1 if event_type == "WHEELDOWNMOUSE" else 1), max_scroll)
                )
                if GridState.current_start_row != old_row:
                    redraw_ui("VIEW_3D", area_pointer=GridState.target_area_pointer)
            return {"RUNNING_MODAL"}

        delta = 1 if event_type == "WHEELUPMOUSE" else -1
        if prefs.settings.cycle_cameras:
            new_idx = (layout.active_index + delta) % layout.total_cameras
        else:
            new_idx = max(0, min(layout.total_cameras - 1, layout.active_index + delta))

        if new_idx != layout.active_index and 0 <= new_idx < layout.total_cameras:
            context.scene.camera = layout.cameras[new_idx]
            _apply_on_switch_action(context)
        return {"RUNNING_MODAL"}

    def _handle_arrow(self, context: Context, event: Event, event_type: str):
        if GridState.drag_state != _DragState.IDLE:
            return {"RUNNING_MODAL"}
        layout = _compute_grid_layout(context)
        if not layout or not _is_mouse_in_grid(layout, event.mouse_region_x, event.mouse_region_y):
            return {"PASS_THROUGH"}

        idx = layout.active_index
        tot, cols = layout.total_cameras, layout.columns

        prefs = context.preferences.addons.get(__package__).preferences

        match event_type:
            case "LEFT_ARROW":
                new_idx = (idx - 1 + tot) % tot if prefs.settings.cycle_cameras else max(0, idx - 1)
            case "RIGHT_ARROW":
                new_idx = (idx + 1) % tot if prefs.settings.cycle_cameras else min(tot - 1, idx + 1)
            case "UP_ARROW":
                new_idx = idx + cols if idx + cols < tot else idx
            case "DOWN_ARROW":
                new_idx = idx - cols if idx - cols >= 0 else idx

        if new_idx != idx and 0 <= new_idx < tot:
            context.scene.camera = layout.cameras[new_idx]
            _apply_on_switch_action(context)
        return {"RUNNING_MODAL"}

    def invoke(self, context, event):
        if GridState.modal_operator is not None:
            return {"CANCELLED"}
        context.window_manager.modal_handler_add(self)
        GridState.modal_operator = self
        return {"RUNNING_MODAL"}


class CAMGRID_OT_refresh_previews(Operator):
    bl_idname = "camgrid.refresh_previews"
    bl_label = "Refresh Previews"
    bl_description = "Clear the camera preview thumbnail cache and regenerate them"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        refresh_thumbnail_cache()
        return {"FINISHED"}


class CAMGRID_OT_frame_camera(Operator):
    bl_idname = "camgrid.frame_camera"
    bl_label = "Frame Camera"
    bl_description = "Fit camera view to the viewport with margins"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return (
            getattr(getattr(context, "space_data", None), "region_3d", None) is not None
            and context.area.type == "VIEW_3D"
            and context.scene.camera
        )

    def execute(self, context):
        region = next((r for r in context.area.regions if r.type == "WINDOW"), None)
        if not region or region.height <= 0 or region.width <= 0:
            return {"CANCELLED"}

        prefs = context.preferences.addons.get(__package__).preferences

        rv3d = context.space_data.region_3d
        if rv3d.view_perspective != "CAMERA":
            try:
                bpy.ops.view3d.view_camera("EXEC_DEFAULT")
            except Exception:
                pass

        layout = _compute_grid_layout(context, area=context.area, region=region) if is_grid_active(context) else None
        scale = layout.scale if layout else _get_ui_scale()
        grid_top = (
            (
                layout.origin_y
                + layout.visible_rows * (layout.th + layout.gap)
                + prefs.settings.frame_bottom_padding * scale
            )
            if layout and prefs.settings.frame_grid_padding
            else prefs.settings.frame_bottom_padding * scale
        )

        top_margin = prefs.settings.frame_top_padding * scale
        grid_frac = min(0.6, grid_top / float(region.height))

        left_overlap, right_overlap = _get_left_right_overlap(context.area)
        avail_w = max(
            1.0, float(region.width) - left_overlap - right_overlap - prefs.settings.frame_horizontal_padding * scale
        )
        avail_vh = max(1.0, (1.0 - grid_frac) * float(region.height) - top_margin)

        try:
            bpy.ops.view3d.view_center_camera("EXEC_DEFAULT")
        except Exception:
            pass

        z_base = float(rv3d.view_camera_zoom)
        sqrt2_100 = math.sqrt(2.0) / 100.0
        zf_base = max(0.01, (sqrt2_100 * z_base + 1.0) ** 2)

        r = context.scene.render
        c_asp = (r.resolution_x * r.pixel_aspect_x) / (r.resolution_y * r.pixel_aspect_y) if r.resolution_y > 0 else 1.0
        v_asp = float(region.width) / float(region.height)

        fw, fh = (
            (float(region.width), float(region.width) / c_asp)
            if c_asp > v_asp
            else (float(region.height) * c_asp, float(region.height))
        )
        s = min(avail_w / fw, avail_vh / fh, 1.0)

        rv3d.view_camera_zoom = max(-29.9, (1.0 / sqrt2_100) * (math.sqrt(zf_base * s) - 1.0)) if s < 1.0 else z_base
        zf_final = (sqrt2_100 * rv3d.view_camera_zoom + 1.0) ** 2

        rv3d.view_camera_offset[0] = ((right_overlap - left_overlap) / 2.0) / (zf_final * float(region.width))
        rv3d.view_camera_offset[1] = -((grid_top - top_margin) / 2.0) / (zf_final * float(region.height))
        return {"FINISHED"}


classes = (
    CAMGRID_OT_toggle_grid,
    CAMGRID_OT_interactive_grid,
    CAMGRID_OT_refresh_previews,
    CAMGRID_OT_frame_camera,
)


def register():
    pass


def unregister():
    ThumbnailManager.invalidate()
    if GridState.handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(GridState.handler, "WINDOW")
        except (ValueError, AttributeError):
            pass
    GridState.reset()
