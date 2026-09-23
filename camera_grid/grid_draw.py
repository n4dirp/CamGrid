"""Camera grid viewport drawing routines and draw handler."""

import math
import time

import blf
import bpy
from gpu_extras.presets import draw_texture_2d

from .gpu_draw import (
    _draw_filled_rounded_rect,
    _draw_pill,
    _draw_pill_border,
    _draw_rounded_rect_border,
    _draw_text_with_shadow,
    _get_theme_colors,
)
from .grid_layout import (
    BADGE_FONT_ID,
    FONT_ID,
    FONT_SIZE,
    ICON_SIZE,
    PANEL_PADDING,
    SCROLLBAR_WIDTH,
    SCROLLBAR_WIDTH_HOVER,
    SHADOW_OFFSET,
    GridLayout,
    _compute_grid_layout,
    _get_scrollbar_layout,
    _has_info_content,
)
from .grid_preview import (
    ThumbnailManager,
    _evict_orphaned_thumbnails,
    _get_camera_state_signature,
    _queue_missing_thumbnails,
)
from .grid_state import GridState, _ensure_area_states
from .helpers import _alpha_mul, _camera_anim_flags, _rgba
from .icons import _draw_icon


def _draw_background_panel(layout: GridLayout, colors: dict):
    bg_margin = PANEL_PADDING * layout.scale
    g_left = layout.origin_x - bg_margin
    g_right = layout.origin_x + layout.grid_width + bg_margin
    g_bottom = layout.origin_y - bg_margin
    g_top = layout.origin_y + layout.th * layout.visible_rows + (layout.visible_rows - 1) * layout.gap + bg_margin

    radius = layout.panel_radius * 1
    shadow_offset = SHADOW_OFFSET * layout.scale

    _draw_filled_rounded_rect(
        g_left + 1,
        g_bottom - shadow_offset,
        g_right - g_left - 1,
        g_top - g_bottom,
        radius,
        _alpha_mul((0.0, 0.0, 0.0, 0.4), layout.master_alpha),
    )

    _draw_filled_rounded_rect(g_left, g_bottom, g_right - g_left, g_top - g_bottom, radius, colors["bg_color"])

    _draw_rounded_rect_border(
        g_left,
        g_bottom,
        g_right - g_left,
        g_top - g_bottom,
        radius,
        colors["panel_border"],
        0.5 * layout.scale,
    )


def _draw_dot_tiles(layout: GridLayout, colors: dict):
    """Draw camera tiles in DOTS mode — pill shapes with no text labels."""
    shadow_offset = SHADOW_OFFSET * layout.scale
    line_width = 0.15 * layout.scale

    for i in range(layout.start_index, layout.end_index):
        cam = layout.cameras[i]
        x = round(layout.origin_x + (i % layout.columns) * (layout.tw + layout.gap))
        y = round(
            layout.origin_y
            + (layout.visible_rows - 1 - ((i // layout.columns) - layout.start_row)) * (layout.th + layout.gap)
        )

        if y > layout.region.height or y + layout.th < 0:
            continue

        selected = cam.select_get()
        is_active = cam == layout.active_camera
        is_active_obj = cam == layout.active_object
        is_hovered = i == layout.hovered_tile

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
                _draw_pill_border(
                    x,
                    y,
                    layout.tw,
                    layout.th,
                    colors["border_active"] if is_active_obj else colors["border_selected"],
                    line_width,
                )
        else:
            radius = layout.radius
            # Draw the background shadow
            _draw_filled_rounded_rect(
                x,
                y - shadow_offset,
                layout.tw,
                layout.th,
                radius,
                _alpha_mul((0.0, 0.0, 0.0, 0.4), layout.master_alpha),
            )

            # Draw the tile background
            _draw_filled_rounded_rect(x, y, layout.tw, layout.th, radius, base_col)

            # Draw the tile highlight (if hovered)
            if is_hovered:
                _draw_filled_rounded_rect(
                    x, y, layout.tw, layout.th, radius, _rgba(colors["text"], 0.04 * layout.master_alpha)
                )

            _draw_rounded_rect_border(x, y, layout.tw, layout.th, radius, colors["tile_border"], line_width)

            if selected:
                _draw_rounded_rect_border(
                    x,
                    y,
                    layout.tw,
                    layout.th,
                    radius,
                    colors["border_active"] if is_active_obj else colors["border_selected"],
                    line_width,
                )


def _draw_label_tiles(layout: GridLayout, colors: dict, prefs):
    """Draw camera tiles in TILES mode — rounded rects with centered text labels."""
    font_id = FONT_ID
    blf.size(font_id, layout.font_size)
    _, ref_font_h = blf.dimensions(font_id, "Ag")

    shadow_offset = SHADOW_OFFSET * layout.scale
    line_width = 0.5 * layout.scale
    ellipsis_width = blf.dimensions(font_id, "...")[0]
    inset = line_width * 2
    max_t_w = layout.tw - 8 * layout.scale
    icon_size = ICON_SIZE * layout.scale
    icon_pad = 5 * layout.scale
    radius = layout.radius

    for i in range(layout.start_index, layout.end_index):
        cam = layout.cameras[i]
        x = layout.origin_x + (i % layout.columns) * (layout.tw + layout.gap)
        y = layout.origin_y + (layout.visible_rows - 1 - ((i // layout.columns) - layout.start_row)) * (
            layout.th + layout.gap
        )

        if y > layout.region.height or y + layout.th < 0:
            continue

        selected = cam.select_get()
        is_active = cam == layout.active_camera
        is_active_obj = cam == layout.active_object
        is_hovered = i == layout.hovered_tile

        if is_active:
            base_col = colors["tile_picked"]
        else:
            base_col = colors["tile_default"]

        # Draw the background shadow
        _draw_filled_rounded_rect(
            x, y - shadow_offset, layout.tw, layout.th, radius, _alpha_mul((0.0, 0.0, 0.0, 0.4), layout.master_alpha)
        )

        # Draw the tile background
        _draw_filled_rounded_rect(x, y, layout.tw, layout.th, radius * 1.25, base_col)

        # Draw the tile highlight (if hovered)
        if is_hovered:
            _draw_filled_rounded_rect(
                x, y, layout.tw, layout.th, radius, _rgba(colors["text"], 0.04 * layout.master_alpha)
            )

        _draw_rounded_rect_border(x, y, layout.tw, layout.th, radius, colors["tile_border"], line_width * 0.5)

        # Draw the tile border (if selected or active)
        if selected:
            border_col = colors["border_active"] if is_active_obj else colors["border_selected"]
            _draw_rounded_rect_border(x, y, layout.tw, layout.th, radius, border_col, line_width)

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

        anim_flags = _camera_anim_flags(cam) if prefs.settings.show_status_icons else (False, False)
        icon_count = int(anim_flags[0]) + int(anim_flags[1])
        icon_w = icon_count * (icon_size + icon_pad)
        if icon_count and layout.tw <= icon_w + 19 * layout.scale:
            anim_flags = (False, False)
            icon_w = 0.0
        avail_w = max_t_w - icon_w
        text = cam.name
        if blf.dimensions(font_id, text)[0] > avail_w:
            max_w_no_ell = max(1.0, avail_w - ellipsis_width)
            left, right = len(text) // 2, len(text) // 2 + 1
            while (
                left > 0 and right < len(text) and blf.dimensions(font_id, text[:left] + text[right:])[0] > max_w_no_ell
            ):
                left -= 1
                right += 1
            text = text[:left] + "..." + text[right:]

        tw, _ = blf.dimensions(font_id, text)
        if selected:
            text_color = colors["border_active"] if is_active_obj else colors["border_selected"]
        elif is_active:
            text_color = colors["tile_text"]
        else:
            text_color = colors["tile_text_inactive"]
        _draw_text_with_shadow(
            font_id,
            text,
            x + (layout.tw - icon_w - tw) / 2,
            y + (layout.th - ref_font_h) / 2 + 1,
            text_color,
            layout.scale,
        )
        icon_x = x + layout.tw - icon_w
        if anim_flags[0]:
            _draw_icon("anim_data", icon_x, y + (layout.th - icon_size) / 2, icon_size, layout.master_alpha)
            icon_x += icon_size + icon_pad
        if anim_flags[1]:
            _draw_icon(
                "constraint",
                icon_x,
                y + (layout.th - icon_size) / 2,
                icon_size,
                layout.master_alpha,
            )


def _draw_thumbnail_tiles(layout: GridLayout, colors: dict, prefs, active_scene):
    """Draw camera tiles in THUMBNAILS mode — cached preview images with badge labels."""
    font_id = FONT_ID
    blf.size(font_id, layout.font_size)

    line_width = 0.5 * layout.scale
    radius = 0
    ellipsis_width = blf.dimensions(font_id, "...")[0]
    badge_pad = 3 * layout.scale
    max_t_w = layout.tw - (badge_pad * 2) * layout.scale
    badge_font_size = max(6, int(FONT_SIZE * layout.scale))
    shadow_offset = SHADOW_OFFSET * layout.scale

    blf.size(BADGE_FONT_ID, badge_font_size)

    for i in range(layout.start_index, layout.end_index):
        cam = layout.cameras[i]
        x = layout.origin_x + (i % layout.columns) * (layout.tw + layout.gap)
        y = layout.origin_y + (layout.visible_rows - 1 - ((i // layout.columns) - layout.start_row)) * (
            layout.th + layout.gap
        )

        if y > layout.region.height or y + layout.th < 0:
            continue

        selected = cam.select_get()
        is_active = cam == layout.active_camera
        is_active_obj = cam == layout.active_object
        is_hovered = i == layout.hovered_tile

        cached = ThumbnailManager.cache.get(cam.name)
        is_valid, is_stale = False, False
        if cached:
            sig = _get_camera_state_signature(cam, active_scene)
            if cached[0] == ThumbnailManager.gen and cached[2] == sig:
                is_valid = True
            elif cached[0] == ThumbnailManager.gen:
                is_stale = True

        if is_valid:
            ThumbnailManager.cache[cam.name] = (
                cached[0],
                cached[1],
                cached[2],
                time.monotonic(),
            )
            ThumbnailManager.stale.discard(cam.name)
        elif is_stale:
            ThumbnailManager.stale.add(cam.name)

        # Tile Shadow
        _draw_filled_rounded_rect(
            x, y - shadow_offset, layout.tw, layout.th, radius, _alpha_mul((0.0, 0.0, 0.0, 0.4), layout.master_alpha)
        )

        # Tile Background
        _draw_filled_rounded_rect(x, y, layout.tw, layout.th, radius, colors["tile_default"])

        # Draw Tile Texture
        if cached:
            draw_texture_2d(cached[1].texture_color, (x, y), layout.tw, layout.th)

        # Stale Tile Overlay
        if not is_valid and is_stale:
            _draw_filled_rounded_rect(
                x, y, layout.tw, layout.th, radius, _rgba(colors["tile_default"], 0.5 * layout.master_alpha)
            )

        # Active Tile Overlay
        if is_active:
            _draw_filled_rounded_rect(
                x, y, layout.tw, layout.th, radius, _rgba(colors["tile_picked"], 0.15 * layout.master_alpha)
            )

        # Hovered Tile Overlay
        if is_hovered:
            _draw_filled_rounded_rect(
                x, y, layout.tw, layout.th, radius, _rgba(colors["text"], 0.02 * layout.master_alpha)
            )

        # Animated Camera Markers
        anim_flags = _camera_anim_flags(cam) if prefs.settings.show_status_icons else (False, False)
        if any(anim_flags):
            glyph = ICON_SIZE * layout.scale
            gpad = 4 * layout.scale
            if anim_flags[1]:
                _draw_icon(
                    "constraint",
                    x + layout.tw - glyph - gpad,
                    y + layout.th - glyph - gpad,
                    glyph,
                    layout.master_alpha,
                )
            if anim_flags[0]:
                offset = glyph + gpad * 2 if anim_flags[1] else 0.0
                _draw_icon(
                    "anim_data",
                    x + layout.tw - glyph - gpad - offset,
                    y + layout.th - glyph - gpad,
                    glyph,
                    layout.master_alpha,
                )

        # Draw Light Tile Border
        _draw_rounded_rect_border(x, y, layout.tw, layout.th, radius, colors["tile_border"], line_width)

        # Selected Tile Border
        if selected or is_active:
            if selected and is_active:
                _draw_rounded_rect_border(
                    x,
                    y,
                    layout.tw,
                    layout.th,
                    radius,
                    colors["tile_picked"],
                    line_width * 2,
                )

            border_col = (
                (colors["border_active"] if is_active_obj else colors["border_selected"])
                if selected
                else colors["tile_picked"]
            )
            _draw_rounded_rect_border(x, y, layout.tw, layout.th, radius, border_col, line_width)

        # Tile Camera Name
        if prefs.settings.show_preview_names:
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
            bw, _ = btw + badge_pad * 2, bth + badge_pad * 2
            bx, by = x + round((layout.tw - bw) / 2), y + badge_pad

            # bg_col = colors["tile_picked"] if is_active else colors["tile_default"]
            # _draw_filled_rounded_rect(bx, by, bw, bh, badge_pad, _rgba(bg_col, 0.5 * layout.master_alpha))

            if selected:
                text_color = colors["border_active"] if is_active_obj else colors["border_selected"]
            elif is_active:
                text_color = colors["tile_text"]
            else:
                text_color = colors["tile_text_inactive"]
            _draw_text_with_shadow(
                BADGE_FONT_ID,
                text,
                bx + badge_pad,
                by + badge_pad,
                text_color,
                layout.scale,
            )


def _draw_camera_tiles(layout: GridLayout, colors: dict, prefs, active_scene):
    """Dispatch to the appropriate display-type draw function."""
    display_mode = prefs.settings.display_mode
    if display_mode == "THUMBNAILS":
        _draw_thumbnail_tiles(layout, colors, prefs, active_scene)
    elif display_mode == "TILES":
        _draw_label_tiles(layout, colors, prefs)
    else:  # DOTS
        _draw_dot_tiles(layout, colors)


def _draw_scrollbar(layout: GridLayout, colors: dict):
    if layout.total_rows <= layout.effective_max_rows:
        return
    if sb := _get_scrollbar_layout(layout):
        is_hovered = layout.scrollbar_hovered
        bar_right = sb.track_right
        bar_width = (SCROLLBAR_WIDTH_HOVER if is_hovered else SCROLLBAR_WIDTH) * layout.scale
        bar_left = bar_right - bar_width

        inner = colors["scroll_inner"]
        track_alpha = (min(inner[3], 0.15) if not is_hovered else max(inner[3], 0.15)) * layout.master_alpha
        fill_color = _rgba(inner, track_alpha)
        _draw_filled_rounded_rect(
            round(bar_left),
            round(sb.track_bottom),
            round(bar_right - bar_left),
            round(sb.track_h),
            bar_width / 2.0,
            fill_color,
        )

        alpha = 1.0 if is_hovered else 0.6
        color = _rgba(colors["scroll_bar"], alpha * layout.master_alpha)
        _draw_pill(
            round(bar_left),
            round(sb.thumb_y),
            round(bar_right - bar_left),
            round(sb.thumb_h),
            color,
        )


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

        if cam_type == "PERSP":
            if prefs.settings.show_camera_lens:
                lens = getattr(data, "lens", 0)
                lens_unit = getattr(data, "lens_unit", "MILLIMETERS")
                if lens_unit == "FOV":
                    angle = getattr(data, "angle", 0)
                    if angle > 0:
                        parts.append(f"Lens: {math.degrees(angle):.1f}°")
                elif lens > 0:
                    parts.append(f"Lens: {int(lens)} mm")

        if prefs.settings.show_camera_sensor:
            sensor_w = getattr(data, "sensor_width", 0)
            sensor_h = getattr(data, "sensor_height", 0)
            sensor_fit = getattr(data, "sensor_fit", "AUTO")

            if sensor_fit == "AUTO":
                render = bpy.context.scene.render
                aspect = (
                    (render.resolution_x * render.pixel_aspect_x) / (render.resolution_y * render.pixel_aspect_y)
                    if render.resolution_y > 0
                    else 1.0
                )
                if sensor_w > 0 and sensor_h > 0:
                    if aspect >= 1.0:
                        parts.append(f"Sensor: {sensor_w:g} mm")
                    else:
                        parts.append(f"Sensor: {sensor_h:g} mm")
                elif sensor_w > 0:
                    parts.append(f"Sensor: {sensor_w:g} mm")
            elif sensor_fit == "HORIZONTAL":
                if sensor_w > 0:
                    parts.append(f"Sensor: {sensor_w:g} mm")
            elif sensor_fit == "VERTICAL":
                if sensor_h > 0:
                    parts.append(f"Sensor: {sensor_h:g} mm")

            if prefs.settings.show_camera_depth_of_field:
                dof = getattr(data, "dof", None)
                if dof and getattr(dof, "use_dof", False):
                    fstop = getattr(dof, "aperture_fstop", 0)
                    if fstop > 0:
                        if focus_obj := getattr(dof, "focus_object", None):
                            focus_str = focus_obj.name
                        else:
                            focus_str = f"{getattr(dof, 'focus_distance', 0):g} m"
                        parts.append(f"DoF: f/{fstop:g}, {focus_str}")
        elif cam_type == "ORTHO":
            if prefs.settings.show_camera_lens:
                ortho_scale = getattr(data, "ortho_scale", None)
                if ortho_scale:
                    parts.append(f"Scale: {ortho_scale:.2f}")
        elif cam_type == "PANO":
            if prefs.settings.show_camera_lens:
                lens = getattr(data, "lens", 0)
                lens_unit = getattr(data, "lens_unit", "MILLIMETERS")
                if lens_unit == "FOV":
                    angle = getattr(data, "angle", 0)
                    if angle > 0:
                        parts.append(f"Lens: {math.degrees(angle):.1f}°")
                elif lens > 0:
                    parts.append(f"Lens: {int(lens)} mm")

        if prefs.settings.show_camera_clip:
            clip_start = getattr(data, "clip_start", 0)
            clip_end = getattr(data, "clip_end", 0)
            if clip_start > 0 and clip_end > 0:
                parts.append(f"Clip: {clip_start:g}-{clip_end:g}")

        if prefs.settings.show_camera_collection:
            try:
                collections = [coll.name for coll in getattr(active_cam, "users_collection", [])]
            except (AttributeError, ReferenceError):
                collections = []
            if collections:
                parts.append(f"Collection: {', '.join(collections)}")

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
    gap = PANEL_PADDING * layout.scale

    if layout.grid_alignment == "LEFT":
        ix = layout.origin_x - gap
    elif layout.grid_alignment == "RIGHT":
        ix = layout.origin_x + layout.grid_width - iw + gap
    else:
        ix = layout.origin_x + (layout.grid_width - iw) / 2

    iy = layout.origin_y - layout.info_offset_y

    _draw_text_with_shadow(font_id, info_text, ix, iy, colors["info_text"], layout.scale)


def _draw_grid():
    _ensure_area_states()
    if not GridState.areas or not any(s.enabled for s in GridState.areas.values()):
        return

    # Auto-spawn modal operator for any window that lacks one
    if (win := bpy.context.window) and win.as_pointer() not in GridState.window_operators:
        try:
            bpy.ops.camgrid.interactive_grid("INVOKE_DEFAULT")
        except Exception:
            pass

    layout = _compute_grid_layout(bpy.context)
    if not layout:
        return

    try:
        colors = _get_theme_colors()
    except (AttributeError, IndexError, ReferenceError):
        return

    prefs = bpy.context.preferences.addons.get(__package__).preferences
    active_scene = bpy.context.scene

    if layout.master_alpha < 1.0:
        colors = {name: _alpha_mul(color, layout.master_alpha) for name, color in colors.items()}

    _evict_orphaned_thumbnails(layout.cameras)
    _queue_missing_thumbnails(layout, prefs, active_scene)

    _draw_background_panel(layout, colors)
    _draw_camera_tiles(layout, colors, prefs, active_scene)
    _draw_scrollbar(layout, colors)
    if _has_info_content(prefs):
        _draw_footer_info(layout, colors)
