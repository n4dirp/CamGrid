"""Camera Grid property groups."""

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty
from bpy.types import PropertyGroup

from . import viewport_grid


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
    show_hidden: BoolProperty(
        name="Show Hidden",
        description="Include cameras that are hidden in the viewport in the grid",
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
