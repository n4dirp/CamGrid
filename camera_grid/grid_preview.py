"""Camera preview thumbnail rendering pipeline and cache."""

import logging
import time

import bpy
import gpu

from .grid_layout import GridLayout, _compute_grid_layout
from .grid_state import GridState
from .helpers import _get_ui_scale, redraw_ui

logger = logging.getLogger(__package__)


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

    auto_refresh_deadline: float = 0.0
    auto_refresh_timer_active: bool = False
    prefer_non_rendered: bool = False

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
        cls.prefer_non_rendered = False

        cls._restore_viewport()

        cls.original_shading_type = None
        cls.original_show_overlays = None
        cls.cancel_auto_refresh()
        logger.debug("PREVIEW: Cache invalidated (gen %d)", cls.gen)

    @classmethod
    def schedule_auto_refresh(cls):
        prefs = bpy.context.preferences.addons.get(__package__).preferences
        delay = prefs.settings.auto_refresh_delay
        cls.auto_refresh_deadline = time.monotonic() + delay
        if not cls.auto_refresh_timer_active:
            cls.auto_refresh_timer_active = True
            bpy.app.timers.register(_auto_refresh_tick, first_interval=delay)

    @classmethod
    def cancel_auto_refresh(cls):
        cls.auto_refresh_deadline = 0.0
        if cls.auto_refresh_timer_active:
            cls.auto_refresh_timer_active = False
            try:
                bpy.app.timers.unregister(_auto_refresh_tick)
            except Exception:
                pass

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
                        logger.debug(
                            "PREVIEW: Overlays restored to %s",
                            cls.original_show_overlays,
                        )
                    except ReferenceError:
                        pass
            cls.original_shading_type = None
            cls.original_show_overlays = None

    @classmethod
    def _restore_viewport(cls):
        context = bpy.context
        for area_ptr in list(GridState.areas.keys()):
            target_area = next(
                (a for w in context.window_manager.windows for a in w.screen.areas if a.as_pointer() == area_ptr),
                None,
            )
            space_view3d = target_area.spaces.active if target_area and target_area.type == "VIEW_3D" else None
            cls.cleanup_shading(space_view3d)


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


def _auto_refresh_tick():
    """Debounced auto-refresh timer callback that queues stale thumbnails for re-render."""
    if time.monotonic() >= ThumbnailManager.auto_refresh_deadline:
        ThumbnailManager.auto_refresh_timer_active = False
        _queue_stale_thumbnails()
        return None
    return max(0.0, ThumbnailManager.auto_refresh_deadline - time.monotonic())


def _find_thumbnail_render_area(context, prefer_non_rendered: bool = False):
    """Return the first grid viewport suitable for thumbnail rendering."""
    first = None
    for area_ptr, s in GridState.areas.items():
        if not s.enabled:
            continue
        candidate = next(
            (a for w in context.window_manager.windows for a in w.screen.areas if a.as_pointer() == area_ptr),
            None,
        )
        if not candidate or candidate.type != "VIEW_3D":
            continue
        candidate_region = next((r for r in candidate.regions if r.type == "WINDOW"), None)
        if not candidate_region:
            continue
        candidate_layout = _compute_grid_layout(context, area=candidate, region=candidate_region)
        if candidate_layout is None:
            continue
        if first is None:
            first = (candidate, s, candidate_region, candidate_layout)
        if not prefer_non_rendered or candidate.spaces.active.shading.type != "RENDERED":
            return (candidate, s, candidate_region, candidate_layout)
    return first


def _queue_stale_thumbnails():
    """Queue re-renders for thumbnails the draw pass has marked as stale."""
    if not ThumbnailManager.stale:
        return
    prefs = bpy.context.preferences.addons.get(__package__).preferences
    if prefs.settings.auto_refresh_shading_mode == "SKIP_RENDERED":
        found = _find_thumbnail_render_area(bpy.context, prefer_non_rendered=True)
        if not found or found[0].spaces.active.shading.type == "RENDERED":
            logger.debug("PREVIEW: Skipping auto-refresh, all grid viewports use Rendered shading")
            return
        ThumbnailManager.prefer_non_rendered = True
    for cam_name in list(ThumbnailManager.stale):
        if cam_name in ThumbnailManager.cache and cam_name not in ThumbnailManager.pending:
            ThumbnailManager.queue_render(cam_name)
    ThumbnailManager.stale.clear()
    redraw_ui("VIEW_3D")


def _depsgraph_update_post_handler(scene, depsgraph):
    """Schedule a debounced thumbnail refresh when any camera object or data changes."""
    if not any(s.enabled for s in GridState.areas.values()):
        return
    if not ThumbnailManager.cache and not ThumbnailManager.stale:
        return
    try:
        settings = bpy.context.preferences.addons.get(__package__).preferences.settings
    except (KeyError, AttributeError, ReferenceError):
        return
    if not (settings.use_preview_auto_refresh and settings.display_mode == "THUMBNAILS"):
        return
    for upd in depsgraph.updates:
        orig = getattr(getattr(upd, "id", None), "original", None)
        if orig is None:
            continue
        if isinstance(orig, bpy.types.Camera) or getattr(orig, "type", None) == "CAMERA":
            ThumbnailManager.schedule_auto_refresh()
            return


def _process_thumbnail_queue():
    if not ThumbnailManager.pending:
        ThumbnailManager._restore_viewport()
        ThumbnailManager.render_timer_active = False
        ThumbnailManager.prefer_non_rendered = False
        return None

    context = bpy.context
    found = _find_thumbnail_render_area(context, prefer_non_rendered=ThumbnailManager.prefer_non_rendered)
    if not found:
        ThumbnailManager.cleanup_shading()
        ThumbnailManager.render_timer_active = False
        ThumbnailManager.prefer_non_rendered = False
        return None
    target_area, state, region, layout = found

    space_view3d = target_area.spaces.active
    if not space_view3d or not layout:
        ThumbnailManager.cleanup_shading(space_view3d)
        ThumbnailManager.render_timer_active = False
        ThumbnailManager.prefer_non_rendered = False
        return None

    visible_keys = {layout.cameras[idx].name for idx in range(layout.start_index, layout.end_index)}
    visible_pending = list(ThumbnailManager.pending.intersection(visible_keys))
    offscreen_pending = list(ThumbnailManager.pending.difference(visible_keys))
    ordered_pending = visible_pending + offscreen_pending

    if not ordered_pending:
        ThumbnailManager.pending.clear()
        ThumbnailManager.cleanup_shading(space_view3d)
        ThumbnailManager.render_timer_active = False
        ThumbnailManager.prefer_non_rendered = False
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

        if prefs.settings.use_hide_overlays_in_preview:
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
                    cam_obj,
                    context.scene,
                    depsgraph,
                    space_view3d,
                    region,
                    layout.tw,
                    layout.th,
                )
                if offscreen:
                    sig = _get_camera_state_signature(cam_obj, context.scene)
                    ThumbnailManager.cache[cam_key] = (
                        ThumbnailManager.gen,
                        offscreen,
                        sig,
                        time.monotonic(),
                    )

                    if len(ThumbnailManager.cache) > prefs.settings.preview_cache_size:
                        oldest_key = min(
                            ThumbnailManager.cache.keys(),
                            key=lambda k: ThumbnailManager.cache[k][3],
                        )
                        oldest_data = ThumbnailManager.cache.pop(oldest_key)
                        try:
                            oldest_data[1].free()
                        except Exception:
                            pass
                        logger.trace(
                            "PREVIEW: Evicted '%s' (cache exceeded %d)",
                            oldest_key,
                            prefs.settings.preview_cache_size,
                        )

        ThumbnailManager.render_elapsed_ms += (time.perf_counter() - batch_start) * 1000
        redraw_ui("VIEW_3D")
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
    ThumbnailManager.prefer_non_rendered = False
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
            scene,
            depsgraph.view_layer,
            space_view3d,
            region,
            view_matrix,
            proj_matrix,
            do_color_management=True,
        )

        ThumbnailManager.in_preview_render = False
        ThumbnailManager.preview_rendered_count += 1

        elapsed = (time.perf_counter() - t0) * 1000
        logger.trace(
            "PREVIEW: Rendered '%s' in %.1f ms (%dx%d)",
            cam.name,
            elapsed,
            render_w,
            render_h,
        )

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
#    Cache Management (driven by the draw pass)
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
    if prefs.settings.display_mode != "THUMBNAILS":
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

    if ThumbnailManager.stale and prefs.settings.use_preview_auto_refresh:
        ThumbnailManager.schedule_auto_refresh()


def refresh_thumbnail_cache():
    """Clear thumbnail cache and queue all cameras for re-render."""
    ThumbnailManager.invalidate()
    for cam in bpy.data.objects:
        if cam.type == "CAMERA":
            ThumbnailManager.queue_render(cam.name)
    redraw_ui("VIEW_3D")
