"""Camera grid interaction, operators, and lifecycle management."""

import logging
import math
import time

import bpy
from bpy.app.handlers import persistent
from bpy.types import Context, Event, Operator

from .grid_draw import _draw_grid
from .grid_layout import (
    PANEL_PADDING,
    GridLayout,
    ScrollbarLayout,
    _compute_grid_layout,
    _get_scrollbar_layout,
    _get_tile_at_mouse,
    _is_mouse_in_grid,
)
from .grid_preview import ThumbnailManager, _depsgraph_update_post_handler, refresh_thumbnail_cache
from .grid_state import AreaGridState, GridState, _DragState, _ensure_area_states
from .helpers import _get_header_heights, _get_left_right_overlap, _get_ui_scale, redraw_ui

logger = logging.getLogger(__package__)


def _get_area_and_region_under_mouse(context: Context, event: Event):
    window = getattr(context, "window", None)
    if not window:
        return None, None
    mouse_x, mouse_y = event.mouse_x, event.mouse_y
    for area in window.screen.areas:
        if area.x <= mouse_x <= area.x + area.width and area.y <= mouse_y <= area.y + area.height:
            for region in area.regions:
                if (
                    region.type == "WINDOW"
                    and region.x <= mouse_x <= region.x + region.width
                    and region.y <= mouse_y <= region.y + region.height
                ):
                    return area, region
    return None, None


# ------------------------------------------------------------------------
#    Interaction Helpers
# ------------------------------------------------------------------------


def _switch_to_camera_view(context: Context, area=None):
    area = area or getattr(context, "area", None)
    if area and area.type == "VIEW_3D":
        space = area.spaces.active
        if space and space.type == "VIEW_3D":
            space.region_3d.view_perspective = "CAMERA"


def _apply_switch_action(context, area=None, region=None):
    prefs = context.preferences.addons.get(__package__).preferences
    match prefs.settings.switch_action:
        case "CAMERA_VIEW":
            _switch_to_camera_view(context, area)
        case "FRAME":
            try:
                if area and region:
                    with context.temp_override(window=context.window, area=area, region=region):
                        bpy.ops.camgrid.frame_camera("INVOKE_DEFAULT")
                else:
                    bpy.ops.camgrid.frame_camera("INVOKE_DEFAULT")
            except Exception:
                pass


def _get_select_button(prefs) -> str:
    """Return the mouse button that selects cameras."""
    return "RIGHTMOUSE" if prefs.settings.use_right_click_select else "LEFTMOUSE"


def _action_switch_camera(layout: GridLayout, tile_index: int, context=None, area=None, region=None):
    context = context or bpy.context
    if 0 <= tile_index < len(layout.cameras):
        context.scene.camera = layout.cameras[tile_index]
    _apply_switch_action(context, area, region)


def _action_select_camera(layout: GridLayout, tile_index: int):
    cam = layout.cameras[tile_index]
    try:
        cam.select_set(GridState.drag_select_value)
        if GridState.drag_select_value:
            bpy.context.view_layer.objects.active = cam
    except RuntimeError:
        pass
    redraw_ui("VIEW_3D", area_pointer=layout.area_pointer)


def _drag_tile_action(layout: GridLayout, mx: float, my: float, ref_index: int, action_fn) -> int:
    if (tile_index := _get_tile_at_mouse(layout, mx, my)) is not None and tile_index != ref_index:
        action_fn(layout, tile_index)
        return tile_index
    return ref_index


# ------------------------------------------------------------------------
#    API
# ------------------------------------------------------------------------


def is_grid_active(context: Context | None = None) -> bool:
    if GridState.handler is None:
        return False
    if context is None:
        return True
    if area := getattr(context, "area", None):
        state = GridState.areas.get(area.as_pointer())
        return state is not None and state.enabled
    return False


def _cancel_interaction(context):
    """Cancel the interactive grid modal operator for the given window immediately."""
    win_ptr = context.window.as_pointer()
    op = GridState.window_operators.pop(win_ptr, None)
    if op and hasattr(op, "_timer"):
        try:
            context.window_manager.event_timer_remove(op._timer)
        except Exception:
            pass


def _full_cleanup():
    """Remove draw handler, cancel all operators, and reset state."""
    if GridState.handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(GridState.handler, "WINDOW")
        except (ValueError, AttributeError):
            pass
    ThumbnailManager.invalidate()
    GridState.reset()


@persistent
def _load_post_handler(_dummy):
    """Clear thumbnail cache and grid state when a new blend file is loaded."""
    _full_cleanup()


def toggle_grid(context: Context):
    curr_area_ptr = context.area.as_pointer()
    state = GridState.areas.get(curr_area_ptr)

    if state and state.enabled:
        state.enabled = False
        if not any(s.enabled for s in GridState.areas.values()):
            _cancel_interaction(context)
            _full_cleanup()
        redraw_ui("VIEW_3D", area_pointer=curr_area_ptr)
        return

    # Ensure all areas are registered but enable this one
    _ensure_area_states()

    state = GridState.areas.get(curr_area_ptr)
    if state:
        state.enabled = True
    else:
        GridState.areas[curr_area_ptr] = AreaGridState(
            target_area_pointer=curr_area_ptr,
            target_region_pointer=context.region.as_pointer(),
            enabled=True,
        )

    if GridState.handler is None:
        GridState.handler = bpy.types.SpaceView3D.draw_handler_add(_draw_grid, (), "WINDOW", "POST_PIXEL")

    if context.window.as_pointer() not in GridState.window_operators:
        try:
            bpy.ops.camgrid.interactive_grid("INVOKE_DEFAULT")
        except Exception:
            pass

    redraw_ui("VIEW_3D", area_pointer=curr_area_ptr)


# ------------------------------------------------------------------------
#    Operators
# ------------------------------------------------------------------------


class CAMGRID_OT_toggle_grid(Operator):
    bl_idname = "camgrid.toggle_grid"
    bl_label = "Camera Grid"
    bl_description = (
        "Toggle the camera grid overlay.\n\n"
        "Shortcuts (Over-Grid):\n"
        "Click / Wheel / Arrows - Switch camera.\n"
        "Click+Drag - Quick-switch through cameras.\n"
        "Select+Drag - Paint-select cameras.\n"
        "Ctrl+Wheel - Resize tiles.\n"
        "Ctrl+Shift+1/2/3 - Switch display mode.\n"
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

    def invoke(self, context, event):
        self._timer = context.window_manager.event_timer_add(0.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        self._target_window_ptr = context.window.as_pointer()
        GridState.window_operators[self._target_window_ptr] = self
        return {"RUNNING_MODAL"}

    def modal(self, context: Context, event: Event):
        win_ptr = context.window.as_pointer()
        if GridState.window_operators.get(win_ptr) is not self:
            if hasattr(self, "_timer"):
                context.window_manager.event_timer_remove(self._timer)
            return {"CANCELLED"}

        if not GridState.areas or not any(s.enabled for s in GridState.areas.values()):
            GridState.window_operators.pop(win_ptr, None)
            if hasattr(self, "_timer"):
                context.window_manager.event_timer_remove(self._timer)
            return {"CANCELLED"}

        if event.type == "TIMER":
            return {"PASS_THROUGH"}

        # Dynamically discover which viewport the mouse is currently moving within
        area, region = _get_area_and_region_under_mouse(context, event)

        if not area or not region or area.as_pointer() not in GridState.areas:
            if event.value == "RELEASE" and GridState.drag_state != _DragState.IDLE:
                GridState.drag_state = _DragState.IDLE
                GridState.drag_tile = -1
                GridState.drag_last_tile = -1
            return {"PASS_THROUGH"}

        state = GridState.areas[area.as_pointer()]
        event_type = event.type

        mx = event.mouse_x - region.x
        my = event.mouse_y - region.y

        match event_type:
            case "ESC" if event.value == "PRESS":
                prefs = context.preferences.addons.get(__package__).preferences
                if prefs.settings.use_escape_to_close:
                    layout = _compute_grid_layout(context, area=area, region=region)
                    if layout and _is_mouse_in_grid(layout, mx, my):
                        with context.temp_override(window=context.window, area=area, region=region):
                            toggle_grid(context)
                        return {"CANCELLED"}
                return {"PASS_THROUGH"}

            case "MOUSEMOVE":
                return self._handle_mousemove(context, event, state, area, region, mx, my)

            case "LEFTMOUSE" | "RIGHTMOUSE" if event.value == "PRESS":
                return self._handle_mouse_press(context, event, event_type, state, area, region, mx, my)

            case "LEFTMOUSE" | "RIGHTMOUSE" if event.value == "RELEASE":
                return self._handle_mouse_release(context, event, event_type, state, area, region, mx, my)

            case "WHEELUPMOUSE" | "WHEELDOWNMOUSE":
                return self._handle_wheel(context, event, event_type, state, area, region, mx, my)

            case "LEFT_ARROW" | "RIGHT_ARROW" | "UP_ARROW" | "DOWN_ARROW" if event.value == "PRESS":
                return self._handle_arrow(context, event, event_type, state, area, region, mx, my)

            case "F5" if event.value == "PRESS":
                layout = _compute_grid_layout(context, area=area, region=region)
                if layout and _is_mouse_in_grid(layout, mx, my):
                    prefs = context.preferences.addons.get(__package__).preferences
                    if prefs.settings.display_mode == "THUMBNAILS":
                        try:
                            bpy.ops.camgrid.refresh_previews("INVOKE_DEFAULT")
                        except Exception:
                            pass
                    return {"RUNNING_MODAL"}
                return {"PASS_THROUGH"}

            case "ONE" | "TWO" | "THREE" if event.value == "PRESS" and event.ctrl and event.shift:
                return self._handle_display_mode_switch(context, event, event_type, state, area, region, mx, my)
            case _:
                return {"PASS_THROUGH"}

    def _update_scrollbar_scroll(self, layout: GridLayout, sb: ScrollbarLayout, my: float, state: AreaGridState):
        travel = sb.track_h - sb.thumb_h
        if travel <= 0:
            return
        t = max(0.0, min(1.0, (my - sb.track_bottom - sb.thumb_h / 2) / travel))
        new_row = round((1.0 - t) * sb.max_scroll)
        if state.current_start_row != new_row:
            state.current_start_row = new_row
            redraw_ui("VIEW_3D", area_pointer=state.target_area_pointer)

    def _handle_mousemove(
        self, context: Context, event: Event, state: AreaGridState, area, region, mx: float, my: float
    ):
        layout = _compute_grid_layout(context, area=area, region=region)
        if layout:
            in_grid = _is_mouse_in_grid(layout, mx, my)
            hovered = _get_tile_at_mouse(layout, mx, my)

            sb_hovered = False
            if GridState.drag_state == _DragState.SCROLLBAR_DRAGGING:
                sb_hovered = True
            elif sb := _get_scrollbar_layout(layout):
                if sb.hit_left <= mx <= sb.hit_right and sb.track_bottom <= my <= sb.track_top:
                    sb_hovered = True

            if sb_hovered:
                hovered = None

            needs_redraw = False
            if in_grid != state.mouse_in_grid:
                state.mouse_in_grid = in_grid
                needs_redraw = True

            if hovered != state.hovered_tile:
                state.hovered_tile = hovered
                needs_redraw = True

            if sb_hovered != state.scrollbar_hovered:
                state.scrollbar_hovered = sb_hovered
                needs_redraw = True

            if needs_redraw:
                redraw_ui("VIEW_3D", area_pointer=state.target_area_pointer)
        elif state.mouse_in_grid or state.hovered_tile is not None or state.scrollbar_hovered:
            state.mouse_in_grid = False
            state.hovered_tile = None
            if GridState.drag_state != _DragState.SCROLLBAR_DRAGGING:
                state.scrollbar_hovered = False
            redraw_ui("VIEW_3D", area_pointer=state.target_area_pointer)

        if GridState.drag_state == _DragState.IDLE:
            return {"PASS_THROUGH"}

        match GridState.drag_state:
            case _DragState.SCROLLBAR_DRAGGING if layout:
                if sb := _get_scrollbar_layout(layout):
                    self._update_scrollbar_scroll(layout, sb, my, state)
            case _DragState.SWITCH_PRESSED if layout:
                if (t := _get_tile_at_mouse(layout, mx, my)) is not None and t != GridState.drag_tile:
                    GridState.drag_state = _DragState.SWITCH_DRAGGING
                    GridState.drag_last_tile = t
                    _action_switch_camera(layout, t, context, area, region)
            case _DragState.SWITCH_DRAGGING if layout:
                GridState.drag_last_tile = _drag_tile_action(
                    layout,
                    mx,
                    my,
                    GridState.drag_last_tile,
                    lambda cam_idx, idx: _action_switch_camera(cam_idx, idx, context, area, region),
                )
            case _DragState.SELECT_PRESSED if layout:
                if (t := _get_tile_at_mouse(layout, mx, my)) is not None and t != GridState.drag_tile:
                    GridState.drag_state = _DragState.SELECT_DRAGGING
                    GridState.drag_last_tile = t
                    _action_select_camera(layout, t)
            case _DragState.SELECT_DRAGGING if layout:
                GridState.drag_last_tile = _drag_tile_action(
                    layout, mx, my, GridState.drag_last_tile, _action_select_camera
                )

        if (
            GridState.drag_state in (_DragState.SWITCH_DRAGGING, _DragState.SELECT_DRAGGING)
            and layout
            and layout.total_rows > layout.visible_rows
        ):
            bottom_edge = layout.origin_y
            top_edge = layout.origin_y + layout.visible_rows * (layout.th + layout.gap)
            now = time.monotonic()
            if (
                my < bottom_edge
                and state.current_start_row < (layout.total_rows - layout.visible_rows)
                and now - GridState.drag_last_scroll_time > 0.12
            ):
                state.current_start_row += 1
                GridState.drag_last_scroll_time = now
                redraw_ui("VIEW_3D", area_pointer=state.target_area_pointer)
            elif my > top_edge and state.current_start_row > 0 and now - GridState.drag_last_scroll_time > 0.12:
                state.current_start_row -= 1
                GridState.drag_last_scroll_time = now
                redraw_ui("VIEW_3D", area_pointer=state.target_area_pointer)

        return {"RUNNING_MODAL"}

    def _handle_mouse_press(
        self, context: Context, event: Event, event_type: str, state: AreaGridState, area, region, mx: float, my: float
    ):
        if GridState.drag_state != _DragState.IDLE:
            return {"RUNNING_MODAL"}
        layout = _compute_grid_layout(context, area=area, region=region)
        if not layout:
            return {"PASS_THROUGH"}

        if sb := _get_scrollbar_layout(layout):
            if (
                event_type == "LEFTMOUSE"
                and sb.hit_left <= mx <= sb.hit_right
                and sb.track_bottom <= my <= sb.track_top
            ):
                GridState.drag_state = _DragState.SCROLLBAR_DRAGGING
                self._update_scrollbar_scroll(layout, sb, my, state)
                return {"RUNNING_MODAL"}

        tile_index = _get_tile_at_mouse(layout, mx, my)
        if tile_index is not None:
            cam = layout.cameras[tile_index]
            prefs = context.preferences.addons.get(__package__).preferences
            if event_type == _get_select_button(prefs):
                GridState.drag_state, GridState.drag_tile, GridState.drag_last_tile = (
                    _DragState.SELECT_PRESSED,
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
                redraw_ui("VIEW_3D", area_pointer=state.target_area_pointer)
                return {"RUNNING_MODAL"}

            GridState.drag_state, GridState.drag_tile, GridState.drag_last_tile = (
                _DragState.SWITCH_PRESSED,
                tile_index,
                -1,
            )
            if cam != layout.active_camera:
                context.scene.camera = cam
            _apply_switch_action(context, area, region)

            return {"RUNNING_MODAL"}

        if _is_mouse_in_grid(layout, mx, my):
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def _handle_mouse_release(
        self, context: Context, event: Event, event_type: str, state: AreaGridState, area, region, mx: float, my: float
    ):
        prefs = context.preferences.addons.get(__package__).preferences
        select_button = _get_select_button(prefs)
        switch_button = "RIGHTMOUSE" if select_button == "LEFTMOUSE" else "LEFTMOUSE"
        if (
            (
                GridState.drag_state in (_DragState.SWITCH_PRESSED, _DragState.SWITCH_DRAGGING)
                and event_type == switch_button
            )
            or (
                GridState.drag_state in (_DragState.SELECT_PRESSED, _DragState.SELECT_DRAGGING)
                and event_type == select_button
            )
            or (GridState.drag_state == _DragState.SCROLLBAR_DRAGGING and event_type == "LEFTMOUSE")
        ):
            GridState.drag_state, GridState.drag_tile, GridState.drag_last_tile = (
                _DragState.IDLE,
                -1,
                -1,
            )
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def _handle_wheel(
        self, context: Context, event: Event, event_type: str, state: AreaGridState, area, region, mx: float, my: float
    ):
        if GridState.drag_state != _DragState.IDLE:
            return {"RUNNING_MODAL"}
        layout = _compute_grid_layout(context, area=area, region=region)
        if not layout or not _is_mouse_in_grid(layout, mx, my):
            return {"PASS_THROUGH"}

        prefs = context.preferences.addons.get(__package__).preferences

        if event.ctrl:
            delta = 8 if event_type == "WHEELUPMOUSE" else -8
            if prefs.settings.display_mode == "THUMBNAILS":
                prefs.settings.preview_size = max(64, min(512, prefs.settings.preview_size + delta))
            elif prefs.settings.display_mode == "DOTS":
                return {"RUNNING_MODAL"}
            else:
                prefs.settings.tile_size = max(60, min(512, prefs.settings.tile_size + delta))
            redraw_ui("VIEW_3D", area_pointer=state.target_area_pointer)
            return {"RUNNING_MODAL"}

        should_scroll = event.shift != (prefs.settings.wheel_mode == "SCROLL")

        if sb := _get_scrollbar_layout(layout):
            if sb.hit_left <= mx <= sb.hit_right and sb.track_bottom <= my <= sb.track_top:
                should_scroll = True

        if should_scroll:
            if (max_scroll := layout.total_rows - layout.effective_max_rows) > 0:
                old_row = state.current_start_row
                state.current_start_row = max(
                    0,
                    min(
                        old_row + (1 if event_type == "WHEELDOWNMOUSE" else -1),
                        max_scroll,
                    ),
                )
                if state.current_start_row != old_row:
                    redraw_ui("VIEW_3D", area_pointer=state.target_area_pointer)
            return {"RUNNING_MODAL"}

        delta = 1 if event_type == "WHEELUPMOUSE" else -1
        if prefs.settings.use_camera_cycling:
            new_idx = (layout.active_index + delta) % layout.total_cameras
        else:
            new_idx = max(0, min(layout.total_cameras - 1, layout.active_index + delta))

        if new_idx != layout.active_index and 0 <= new_idx < layout.total_cameras:
            context.scene.camera = layout.cameras[new_idx]
            _apply_switch_action(context, area, region)
        return {"RUNNING_MODAL"}

    def _handle_arrow(
        self, context: Context, event: Event, event_type: str, state: AreaGridState, area, region, mx: float, my: float
    ):
        if GridState.drag_state != _DragState.IDLE:
            return {"RUNNING_MODAL"}
        layout = _compute_grid_layout(context, area=area, region=region)
        if not layout or not _is_mouse_in_grid(layout, mx, my):
            return {"PASS_THROUGH"}

        idx = layout.active_index
        tot, cols = layout.total_cameras, layout.columns

        prefs = context.preferences.addons.get(__package__).preferences

        match event_type:
            case "LEFT_ARROW":
                new_idx = (idx - 1 + tot) % tot if prefs.settings.use_camera_cycling else max(0, idx - 1)
            case "RIGHT_ARROW":
                new_idx = (idx + 1) % tot if prefs.settings.use_camera_cycling else min(tot - 1, idx + 1)
            case "UP_ARROW":
                new_idx = idx - cols if idx - cols >= 0 else idx
            case "DOWN_ARROW":
                new_idx = idx + cols if idx + cols < tot else idx

        if new_idx != idx and 0 <= new_idx < tot:
            context.scene.camera = layout.cameras[new_idx]
            _apply_switch_action(context, area, region)
        return {"RUNNING_MODAL"}

    def _handle_display_mode_switch(
        self, context: Context, event: Event, event_type: str, state: AreaGridState, area, region, mx: float, my: float
    ):
        if GridState.drag_state != _DragState.IDLE:
            return {"RUNNING_MODAL"}
        layout = _compute_grid_layout(context, area=area, region=region)
        if not layout or not _is_mouse_in_grid(layout, mx, my):
            return {"PASS_THROUGH"}
        prefs = context.preferences.addons.get(__package__).preferences
        new_type = {"ONE": "DOTS", "TWO": "TILES", "THREE": "THUMBNAILS"}[event_type]
        if prefs.settings.display_mode != new_type:
            prefs.settings.display_mode = new_type
            redraw_ui("VIEW_3D", area_pointer=state.target_area_pointer)
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

        header_top = header_bottom = 0
        if prefs.settings.use_frame_header_margin:
            try:
                header_top, header_bottom = _get_header_heights(context.area)
            except (ReferenceError, AttributeError):
                header_top = header_bottom = 0
        grid_reserves_bottom = layout is not None and prefs.settings.use_frame_grid_padding
        bottom_reserve = 0 if grid_reserves_bottom else header_bottom
        # Mirror _draw_background_panel top edge, then add user padding.
        grid_top = (
            (
                layout.origin_y
                + layout.visible_rows * (layout.th + layout.gap)
                - layout.gap
                + PANEL_PADDING * scale
                + prefs.settings.frame_bottom_padding * scale
            )
            if layout and prefs.settings.use_frame_grid_padding
            else prefs.settings.frame_bottom_padding * scale + bottom_reserve
        )

        top_margin = prefs.settings.frame_top_padding * scale + header_top
        grid_frac = min(0.6, grid_top / float(region.height))

        left_overlap, right_overlap = _get_left_right_overlap(context.area)
        if not prefs.settings.use_frame_toolbar_margin:
            left_overlap = 0
        if not prefs.settings.use_frame_sidebar_margin:
            right_overlap = 0
        avail_w = max(
            1.0,
            float(region.width) - left_overlap - right_overlap - prefs.settings.frame_horizontal_padding * scale,
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


class CAMGRID_OT_restore_grid_keymap(Operator):
    """Restore the default Camera Grid keymap shortcuts in the user keyconfig."""

    bl_idname = "camgrid.restore_grid_keymap"
    bl_label = "Restore Default Shortcuts"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        wm = context.window_manager
        kc = wm.keyconfigs.user
        if not kc:
            return False
        km = kc.keymaps.get("3D View")
        if km:
            return (
                km.keymap_items.get("camgrid.toggle_grid") is None
                or km.keymap_items.get("camgrid.frame_camera") is None
            )
        return True

    def execute(self, context):
        wm = context.window_manager
        kc = wm.keyconfigs.user
        km = kc.keymaps.get("3D View")
        if not km:
            km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        if km.keymap_items.get("camgrid.toggle_grid") is None:
            km.keymap_items.new("camgrid.toggle_grid", type="C", value="PRESS", alt=True, shift=True)
        if km.keymap_items.get("camgrid.frame_camera") is None:
            km.keymap_items.new("camgrid.frame_camera", type="HOME", value="PRESS", shift=True)
        return {"FINISHED"}


classes = (
    CAMGRID_OT_toggle_grid,
    CAMGRID_OT_interactive_grid,
    CAMGRID_OT_refresh_previews,
    CAMGRID_OT_frame_camera,
    CAMGRID_OT_restore_grid_keymap,
)


def register():
    bpy.app.handlers.depsgraph_update_post.append(_depsgraph_update_post_handler)
    bpy.app.handlers.load_post.append(_load_post_handler)


def unregister():
    ThumbnailManager.cancel_auto_refresh()
    try:
        bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_update_post_handler)
    except ValueError:
        pass
    try:
        bpy.app.handlers.load_post.remove(_load_post_handler)
    except ValueError:
        pass
    ThumbnailManager.invalidate()
    if GridState.handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(GridState.handler, "WINDOW")
        except (ValueError, AttributeError):
            pass
    GridState.reset()
