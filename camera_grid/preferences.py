"""Camera Grid add-on preferences, property groups, and logging infrastructure."""

import logging
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty
from bpy.types import AddonPreferences, PropertyGroup

from . import viewport_grid
from .helpers import redraw_ui

TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")


def _trace_logger(self, msg, *args, **kwargs):
    if self.isEnabledFor(TRACE_LEVEL):
        self._log(TRACE_LEVEL, msg, args, **kwargs)


logging.Logger.trace = _trace_logger


def _update_logger_from_prefs():
    """Configures the logger based on user preferences (Opt-in logging)."""
    logger = logging.getLogger(__package__)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    enabled = False
    level = "INFO"
    try:
        prefs = bpy.context.preferences.addons.get(__package__).preferences
        enabled = getattr(prefs, "use_console_logging", False)
        level = getattr(prefs, "logging_level", "INFO")
    except (KeyError, AttributeError, ReferenceError):
        pass

    if not enabled:
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        return

    level_map = {"INFO": logging.INFO, "DEBUG": logging.DEBUG, "TRACE": TRACE_LEVEL}
    handler = logging.StreamHandler()
    handler.setFormatter(AddonLogFormatter(with_level=True))

    logger.addHandler(handler)
    logger.setLevel(level_map[level])


class AddonLogFormatter(logging.Formatter):
    """Custom formatter to provide timestamped and addon-prefixed logs."""

    def __init__(self, with_level=False):
        super().__init__()
        self.start_time = time.time()
        self.with_level = with_level

    def format(self, record):
        """Formats the log record with relative timestamps."""
        rel_time = record.created - self.start_time
        minutes, seconds = divmod(rel_time, 60)
        timestamp = f"{int(minutes):02d}:{seconds:06.3f}"
        short_name = __package__.rsplit(".", 1)[-1]

        if self.with_level:
            return f"{timestamp}  {short_name:<16} | {record.levelname.title()}: {record.getMessage()}"

        return f"{timestamp}  {short_name:<16} | {record.getMessage()}"


def _update_display_mode(self, context):
    if self.display_mode == "THUMBNAILS":
        viewport_grid.refresh_thumbnail_cache()
    else:
        viewport_grid.ThumbnailManager.invalidate()


def _update_auto_refresh(self, context):
    if not self.use_preview_auto_refresh:
        viewport_grid.ThumbnailManager.cancel_auto_refresh()


def _update_redraw(self, context):
    redraw_ui()


class CAMGRID_PG_settings(PropertyGroup):
    """Preferences for the Camera Grid."""

    master_alpha: FloatProperty(
        name="Master Alpha",
        description="Multiply the opacity of all camera grid draw items",
        default=0.9,
        min=0.1,
        max=1.0,
        subtype="FACTOR",
    )
    display_mode: EnumProperty(
        name="Display Type",
        description="Camera grid tile display mode",
        items=[
            ("DOTS", "Dots", "Show minimal dots without labels", "SHORTDISPLAY", 0),
            ("TILES", "Labels", "Show simple colored tiles", "LONGDISPLAY", 1),
            ("THUMBNAILS", "Thumbnails", "Show camera viewport preview thumbnails", "IMGDISPLAY", 2),
        ],
        default="TILES",
        update=_update_display_mode,
    )
    alignment: EnumProperty(
        name="Grid Alignment",
        description="Horizontal alignment of the camera grid in the viewport",
        items=[
            ("LEFT", "Left", "Align grid to the left side"),
            ("CENTER", "Center", "Center the grid horizontally"),
            ("RIGHT", "Right", "Align grid to the right side"),
        ],
        default="LEFT",
    )
    max_rows: IntProperty(
        name="Max Rows",
        description="Maximum number of visible rows in the camera grid overlay",
        default=3,
        min=1,
        soft_max=50,
    )
    max_columns: IntProperty(
        name="Max Columns",
        description="Maximum number of columns in the camera grid overlay",
        default=8,
        min=1,
        soft_max=50,
    )
    tile_size: IntProperty(
        name="Tile Size",
        description="Tile width in pixels for colored tile mode",
        default=120,
        min=60,
        max=512,
        soft_max=256,
        subtype="PIXEL",
    )
    preview_max_rows: IntProperty(
        name="Preview Max Rows",
        description="Maximum number of visible rows in preview mode",
        default=2,
        min=1,
        soft_max=3,
    )
    preview_max_columns: IntProperty(
        name="Preview Max Columns",
        description="Maximum number of columns in preview mode",
        default=8,
        min=1,
        soft_max=20,
    )
    preview_size: IntProperty(
        name="Preview Size",
        description="Tile width in pixels for camera preview thumbnails",
        default=128,
        min=32,
        soft_max=512,
        max=1024,
        subtype="PIXEL",
    )
    use_hide_overlays_in_preview: BoolProperty(
        name="Disable Overlays",
        description="Temporarily disable viewport overlays while rendering preview thumbnails",
        default=True,
        update=_update_display_mode,
    )
    show_preview_names: BoolProperty(
        name="Show Names",
        description="Display camera names on tiles in preview mode",
        default=True,
    )
    show_status_icons: BoolProperty(
        name="Show Icons",
        description="Display animation and constraint status icons on camera tiles",
        default=True,
        update=_update_redraw,
    )
    preview_cache_size: IntProperty(
        name="Preview Cache Size",
        description="Maximum number of camera preview thumbnails kept in GPU memory",
        default=256,
        min=16,
        max=1024,
    )
    preview_precache_rows: IntProperty(
        name="Precache Rows",
        description="Number of extra rows above and below the visible area to pre-render",
        default=5,
        min=0,
        max=16,
    )
    preview_renders_per_tick: IntProperty(
        name="Renders Per Tick",
        description="Maximum preview thumbnails to render per frame budget tick",
        default=5,
        min=1,
        soft_max=20,
        max=100,
    )
    use_preview_auto_refresh: BoolProperty(
        name="Auto Refresh",
        description="Re-render camera previews when camera data changes",
        default=True,
        update=_update_auto_refresh,
    )
    auto_refresh_delay: FloatProperty(
        name="Refresh Delay",
        description="Delay after camera changes before previews refresh",
        default=0.3,
        min=0.1,
        max=5.0,
        step=0.1,
        precision=1,
        unit="TIME_ABSOLUTE",
    )
    auto_refresh_shading_mode: EnumProperty(
        name="",
        description="Viewport shading types allowed to trigger preview auto-refresh",
        items=[
            ("ALWAYS", "Always", "Refresh previews regardless of viewport shading"),
            ("SKIP_RENDERED", "Skip Rendered", "Skip auto-refresh when all grid viewports use Rendered shading"),
        ],
        default="SKIP_RENDERED",
    )
    dots_max_rows: IntProperty(
        name="Dots Max Rows",
        description="Maximum number of visible rows in dots mode",
        default=4,
        min=1,
        soft_max=50,
    )
    dots_max_columns: IntProperty(
        name="Dots Max Columns",
        description="Maximum number of columns in dots mode",
        default=16,
        min=1,
        soft_max=50,
    )
    use_filter_camera_collections: BoolProperty(
        name="Filter Camera Collections",
        description="Only show collections containing cameras",
        default=True,
    )
    show_hidden_cameras: BoolProperty(
        name="Show Hidden",
        description="Display hidden cameras in the grid",
        default=False,
    )
    show_camera_lens: BoolProperty(
        name="Show Camera Lens",
        description="Show the camera lens or field of view in the info text",
        default=True,
    )
    show_camera_sensor: BoolProperty(
        name="Show Camera Sensor",
        description="Show the camera sensor size in the info text",
        default=True,
    )
    show_camera_depth_of_field: BoolProperty(
        name="Show Depth of Field",
        description="Show the camera depth of field settings in the info text",
        default=True,
    )
    show_camera_clip: BoolProperty(
        name="Show Camera Clip",
        description="Show the camera clipping range in the info text",
        default=True,
    )
    show_active_camera_name: BoolProperty(
        name="Show Active Camera Name",
        description="Show the active camera name in the info text",
        default=True,
    )
    show_camera_count: BoolProperty(
        name="Show Camera Count",
        description="Show the camera count in the info text",
        default=False,
    )
    switch_action: EnumProperty(
        name="On Switch",
        description="Action to perform when selecting a camera from the grid",
        items=[
            ("NONE", "Keep View", "Keep the current view perspective", "OUTLINER_DATA_CAMERA", 0),
            ("CAMERA_VIEW", "View Camera", "Switch to camera view", "OUTLINER_OB_CAMERA", 1),
            ("FRAME", "Frame Camera", "Switch to camera view and fit it to the viewport", "MOD_LENGTH", 2),
        ],
        default="FRAME",
    )
    use_camera_cycling: BoolProperty(
        name="Cycle Cameras",
        description="Wrap around when reaching the start or end of the camera list",
        default=False,
    )
    wheel_mode: EnumProperty(
        name="Wheel Mode",
        description="Mouse wheel behavior in the camera grid",
        items=[
            ("CAMERA", "Switch Camera", "Wheel switches between cameras; Shift scrolls rows"),
            ("SCROLL", "Scroll Rows", "Wheel scrolls visible rows; Shift switches between cameras"),
        ],
        default="CAMERA",
    )
    use_right_click_select: BoolProperty(
        name="Select with Right Click",
        description=("Use right-click to select cameras and left-click to switch. Disable to swap the roles"),
        default=True,
    )
    frame_horizontal_padding: IntProperty(
        name="Frame Horizontal Padding",
        description="Horizontal padding when framing the camera in the viewport",
        default=0,
        min=0,
        soft_max=100,
        subtype="PIXEL",
    )
    frame_top_padding: IntProperty(
        name="Frame Top Padding",
        description="Top padding when framing the camera in the viewport",
        default=27,
        min=0,
        soft_max=200,
        subtype="PIXEL",
    )
    frame_bottom_padding: IntProperty(
        name="Frame Bottom Padding",
        description="Bottom padding reserved for the grid when framing the camera",
        default=2,
        min=0,
        soft_max=50,
        subtype="PIXEL",
    )
    use_frame_grid_padding: BoolProperty(
        name="Grid Padding",
        description="Reserve camera grid height as bottom padding when framing the camera",
        default=True,
    )
    use_frame_toolbar_margin: BoolProperty(
        name="Toolbar Margin",
        description="Reserve the toolbar width when framing the camera",
        default=True,
    )
    use_frame_sidebar_margin: BoolProperty(
        name="Sidebar Margin",
        description="Reserve the sidebar width when framing the camera",
        default=True,
    )
    use_escape_to_close: BoolProperty(
        name="Close Grid with ESC",
        description="Press ESC to close the camera grid overlay",
        default=False,
    )
    panel_location: EnumProperty(
        name="Panel Location",
        description="Where to show the camera grid controls",
        items=[
            ("HEADER", "Header", "Show controls in the 3D viewport topbar"),
            ("UI", "Sidebar", "Show controls in the right sidebar"),
        ],
        default="HEADER",
    )


def _poll_collection_with_cameras(self, obj):
    """Only show collections that contain at least one camera when filter is enabled."""
    prefs = bpy.context.preferences.addons.get(__package__).preferences
    if not prefs.settings.use_filter_camera_collections:
        return True
    return any(o.type == "CAMERA" for o in obj.all_objects)


class CAMGRID_PG_scene(PropertyGroup):
    source_collection: PointerProperty(
        name="Source Collection",
        description="Filter grid cameras by collection",
        type=bpy.types.Collection,
        poll=_poll_collection_with_cameras,
    )


class CAMGRID_AddonPreferences(AddonPreferences):
    bl_idname = __package__

    settings: PointerProperty(type=CAMGRID_PG_settings)

    use_console_logging: BoolProperty(
        name="Console Logging",
        description="Print addon messages to the system console",
        default=False,
        update=lambda self, context: _update_logger_from_prefs(),
    )
    logging_level: EnumProperty(
        name="Verbosity",
        description="Level of detail for console output",
        items=[
            ("INFO", "Info", "General events, warnings, and errors"),
            ("DEBUG", "Debug", "+ Detailed diagnostics for troubleshooting"),
            ("TRACE", "Verbose", "+ Performance timing and cache operations"),
        ],
        default="DEBUG",
        update=lambda self, context: _update_logger_from_prefs(),
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.label(text="Settings")
        layout.row().prop(self.settings, "panel_location", expand=True)

        layout.separator()
        layout.label(text="Viewport Shortcuts")

        wm = context.window_manager
        kc = wm.keyconfigs.user
        from . import addon_keymaps

        col = layout.column()
        col.use_property_split = False
        needs_restore = False
        for km_addon, kmi_addon in addon_keymaps:
            km = kc.keymaps.get(km_addon.name)
            if not km:
                continue
            kmi = km.keymap_items.get(kmi_addon.idname)
            if kmi:
                from rna_keymap_ui import draw_kmi

                draw_kmi([], kc, km, kmi, col, 0)
            else:
                needs_restore = True
        if needs_restore:
            layout.operator("camgrid.restore_grid_keymap", text="Restore Default Shortcuts")

        layout.prop(self.settings, "use_escape_to_close", text="Exit with Escape Key")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Thumbnails")
        col.prop(self.settings, "preview_cache_size", text="Cache Size")
        col.prop(self.settings, "preview_precache_rows", text="Pre-cache Rows")
        col.prop(self.settings, "preview_renders_per_tick", text="Renders per Tick")

        col = layout.column()
        col.prop(self.settings, "auto_refresh_delay", text="Auto-Refresh Delay")

        layout.label(text="Development")
        row = layout.row(align=True, heading="Console Logging")
        row.prop(self, "use_console_logging", text="")
        sub = row.row(align=True)
        sub.active = self.use_console_logging
        sub.prop(self, "logging_level", text="")


classes = (
    CAMGRID_PG_settings,
    CAMGRID_PG_scene,
    CAMGRID_AddonPreferences,
)
