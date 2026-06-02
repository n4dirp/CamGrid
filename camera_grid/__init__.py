"""Camera Grid - Standalone viewport camera grid overlay extension."""

import logging
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty
from bpy.types import AddonPreferences, Panel, PropertyGroup

from . import viewport_grid

logger = logging.getLogger(__package__)
logger.propagate = False
logger.addHandler(logging.NullHandler())

TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")


def _trace_logger(self, msg, *args, **kwargs):
    if self.isEnabledFor(TRACE_LEVEL):
        self._log(TRACE_LEVEL, msg, args, **kwargs)


logging.Logger.trace = _trace_logger


def _update_logger_from_prefs():
    """Configures the logger based on user preferences (Opt-in logging)."""
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


# ------------------------------------------------------------------------
#    Property Group - Grid Settings
# ------------------------------------------------------------------------


def _update_display_type(self, context):
    viewport_grid._invalidate_thumbnails()
    viewport_grid.redraw_ui("VIEW_3D")


class CAMGRID_PG_settings(PropertyGroup):
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
        default=96,
        min=48,
        soft_max=128,
        max=256,
        subtype="PIXEL",
    )
    preview_disable_overlays: BoolProperty(
        name="Disable Overlays",
        description="Temporarily disable viewport overlays while rendering preview thumbnails",
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
    view_from_camera: BoolProperty(
        name="View from Camera",
        description="Switch the 3D viewport to camera view when selecting a camera from the grid",
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


# ------------------------------------------------------------------------
#    Preferences
# ------------------------------------------------------------------------


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


# ------------------------------------------------------------------------
#    Scene Properties
# ------------------------------------------------------------------------


class CAMGRID_PG_scene(PropertyGroup):
    source_collection: PointerProperty(
        name="Source Collection",
        description="Collection containing cameras to display in the grid.\n"
        "If empty, all cameras in the scene are shown",
        type=bpy.types.Collection,
    )


# ------------------------------------------------------------------------
#    Panels
# ------------------------------------------------------------------------


# ------------------------------------------------------------------------
#    Header Draw
# ------------------------------------------------------------------------


def draw_grid_header_button(self, context):
    if context.area.type != "VIEW_3D":
        return
    layout = self.layout
    row = layout.row(align=True)
    grid_active = viewport_grid.is_grid_active(context)
    row.operator("camgrid.toggle_grid", text="Cam", icon="IMGDISPLAY", depress=grid_active)
    row.popover("CAMGRID_PT_grid_popup", text="")


class CAMGRID_PT_grid_popup(Panel):
    bl_label = "Camera Grid"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_options = {"HIDE_HEADER"}

    def draw(self, context):
        layout = self.layout
        prefs = context.preferences.addons.get(__package__).preferences

        layout.label(text="Camera Grid")

        col = layout.column()
        col.label(text="Display Mode")
        col.row().prop(prefs.settings, "display_type", text="Display Mode", expand=True)
        if prefs.settings.display_type == "THUMBNAILS":
            layout.prop(prefs.settings, "preview_disable_overlays", text="Disable Overlays")

        layout.separator()
        col = layout.column()
        col.label(text="Grid Alignment")
        col.row().prop(prefs.settings, "alignment", expand=True)

        sub = layout.column(align=True)
        if prefs.settings.display_type == "THUMBNAILS":
            sub.prop(prefs.settings, "preview_size", text="Size")
            sub.prop(prefs.settings, "preview_max_rows", text="Max Rows")
            sub.prop(prefs.settings, "preview_max_columns", text="Max Columns")
        else:
            sub.prop(prefs.settings, "max_rows", text="Max Rows")
            sub.prop(prefs.settings, "max_columns", text="Max Columns")

        layout.separator()

        col = layout.column()
        col.label(text="Mouse Wheel")
        col.row().prop(prefs.settings, "wheel_mode", text="Mouse Wheel", expand=True)

        layout.separator()
        layout.prop(prefs.settings, "view_from_camera", text="View from Camera")

        layout.separator()
        col = layout.column()
        props = context.scene.camgrid_props
        col.label(text="Filter By Collection")
        col.prop(props, "source_collection", text="")

        grid_active = viewport_grid.is_grid_active(context)
        if grid_active and prefs.settings.display_type == "THUMBNAILS":
            layout.separator(type="LINE", factor=2.0)
            layout.operator("camgrid.refresh_previews", text="Refresh Previews", icon="FILE_REFRESH")


# ------------------------------------------------------------------------
#    Registration
# ------------------------------------------------------------------------

classes = (
    CAMGRID_PG_settings,
    CAMGRID_PG_scene,
    CAMGRID_AddonPreferences,
    CAMGRID_PT_grid_popup,
    *viewport_grid.classes,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.camgrid_props = PointerProperty(type=CAMGRID_PG_scene)
    bpy.types.VIEW3D_HT_header.append(draw_grid_header_button)
    _update_logger_from_prefs()
    viewport_grid.register()


def unregister():
    bpy.types.VIEW3D_HT_header.remove(draw_grid_header_button)
    viewport_grid.unregister()

    try:
        del bpy.types.Scene.camgrid_props
    except AttributeError:
        pass

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
