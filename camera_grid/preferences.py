"""Camera Grid add-on preferences, property groups, and logging infrastructure."""

import logging
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty
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
        enabled = getattr(prefs, "logging_enabled", False)
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


def _update_display_type(self, context):
    viewport_grid.ThumbnailManager.invalidate()
    redraw_ui("VIEW_3D")


class CAMGRID_PG_settings(PropertyGroup):
    """Preferences for the Camera Grid."""

    display_type: EnumProperty(
        name="Display Type",
        description="Camera grid tile display mode",
        items=[
            ("TILES", "Tiles", "Show simple colored tiles", "SHORTDISPLAY", 0),
            ("THUMBNAILS", "Thumbnails", "Show camera viewport preview thumbnails", "IMGDISPLAY", 1),
        ],
        default="TILES",
        update=_update_display_type,
    )
    alignment: EnumProperty(
        name="Grid Alignment",
        description="Horizontal alignment of the camera grid in the viewport",
        items=[
            ("LEFT", "Left", "Align grid to the left side"),
            ("CENTER", "Center", "Center the grid horizontally"),
            ("RIGHT", "Right", "Align grid to the right side"),
        ],
        default="RIGHT",
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
        default=10,
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
        default=10,
        min=1,
        soft_max=20,
    )
    preview_size: IntProperty(
        name="Preview Size",
        description="Tile width in pixels for camera preview thumbnails",
        default=128,
        min=64,
        soft_max=256,
        max=512,
        subtype="PIXEL",
    )
    preview_disable_overlays: BoolProperty(
        name="Disable Overlays",
        description="Temporarily disable viewport overlays while rendering preview thumbnails",
        default=True,
    )
    preview_show_names: BoolProperty(
        name="Show Names",
        description="Display camera names on tiles in preview mode",
        default=True,
    )
    preview_cache_size: IntProperty(
        name="Preview Cache Size",
        description="Maximum number of camera preview thumbnails kept in GPU memory",
        default=200,
        min=10,
        max=1000,
    )
    preview_precache_rows: IntProperty(
        name="Precache Rows",
        description="Number of extra rows above and below the visible area to pre-render",
        default=4,
        min=0,
        max=10,
    )
    preview_renders_per_tick: IntProperty(
        name="Renders Per Tick",
        description="Maximum preview thumbnails to render per frame budget tick",
        default=5,
        min=1,
        soft_max=20,
        max=100,
    )
    show_hidden: BoolProperty(
        name="Show Hidden",
        description="Include cameras that are hidden in the viewport in the grid",
        default=False,
    )
    show_info_text: BoolProperty(
        name="Show Info Text",
        description="Show camera count and selection info below the grid",
        default=True,
    )
    on_switch_action: EnumProperty(
        name="On Switch",
        description="Action to perform when selecting a camera from the grid",
        items=[
            ("NONE", "Keep View", "Keep the current view perspective", "OUTLINER_DATA_CAMERA", 0),
            ("CAMERA_VIEW", "Camera View", "Switch to camera view", "OUTLINER_OB_CAMERA", 1),
            ("FRAME", "Frame Camera", "Switch to camera view and fit it to the viewport", "MOD_LENGTH", 2),
        ],
        default="NONE",
    )
    cycle_cameras: BoolProperty(
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


class CAMGRID_PG_scene(PropertyGroup):
    source_collection: PointerProperty(
        name="Source Collection",
        description="Collection containing cameras to display in the grid.\n"
        "If empty, all cameras in the scene are shown",
        type=bpy.types.Collection,
    )


class CAMGRID_AddonPreferences(AddonPreferences):
    bl_idname = __package__

    settings: PointerProperty(type=CAMGRID_PG_settings)

    logging_enabled: BoolProperty(
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

        layout.label(text="Preview Mode")
        box = layout.box()
        col = box.column(align=True)
        col.prop(self.settings, "preview_cache_size", text="Cache Size")
        col.prop(self.settings, "preview_precache_rows", text="Pre-cache Rows")
        col.prop(self.settings, "preview_renders_per_tick", text="Renders per Tick")

        layout.separator()
        layout.label(text="Development")
        row = layout.row(align=True, heading="Console Logging")
        row.prop(self, "logging_enabled", text="")
        sub = row.row(align=True)
        sub.active = self.logging_enabled
        sub.prop(self, "logging_level", text="")


classes = (
    CAMGRID_PG_settings,
    CAMGRID_PG_scene,
    CAMGRID_AddonPreferences,
)
