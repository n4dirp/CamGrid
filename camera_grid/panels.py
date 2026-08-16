"""Camera Grid UI panels and header draw."""

from bpy.types import Panel

from . import viewport_grid

# ---------------------------------------------------------------------------
#  Draw helpers (shared between popup and sidebar panel)
# ---------------------------------------------------------------------------


def draw_filter_section(layout, prefs, props):
    header, body = layout.panel("CAMGRID_PT_camera_grid_filter_list", default_closed=True)
    header.label(text="Filter")
    if body:
        body.prop(props, "source_collection", text="")

        if prefs.settings.panel_location == "UI":
            sub = body.column()
        else:
            sub = body.row()

        sub.prop(prefs.settings, "filter_camera_collections", text="Camera Collections")
        sub.prop(prefs.settings, "show_hidden", text="Hidden Cameras")


def draw_layout_section(layout, prefs):
    header, body = layout.panel("CAMGRID_PT_camera_grid_ui", default_closed=False)
    header.label(text="Layout")
    if body:
        col = body.column()
        col.label(text="Alignment")
        col.row().prop(prefs.settings, "alignment", expand=True)

        col.separator()
        col.label(text="Display Mode")
        col.prop(prefs.settings, "display_type", text="Display Mode", expand=True)

        sub = body.column(align=True)
        if prefs.settings.display_type == "THUMBNAILS":
            sub.prop(prefs.settings, "preview_size", text="Size")
            sub.prop(prefs.settings, "preview_max_rows", text="Max Rows")
            sub.prop(prefs.settings, "preview_max_columns", text="Max Columns")
        elif prefs.settings.display_type == "DOTS":
            sub.prop(prefs.settings, "dots_max_rows", text="Max Rows")
            sub.prop(prefs.settings, "dots_max_columns", text="Max Columns")
        else:
            sub.prop(prefs.settings, "tile_size", text="Size")
            sub.prop(prefs.settings, "max_rows", text="Max Rows")
            sub.prop(prefs.settings, "max_columns", text="Max Columns")

        if prefs.settings.display_type == "THUMBNAILS":
            col = body.column(align=True)
            row = col.row(align=True)
            row.prop(prefs.settings, "preview_disable_overlays", text="Hide Overlays")
            row.prop(prefs.settings, "auto_refresh_previews", text="Auto Refresh")
            col.prop(prefs.settings, "preview_show_names", text="Show Names")

        body.separator()
        col = body.column(align=True)
        col.label(text="Text")
        row = col.row(align=True)
        row.prop(prefs.settings, "show_active_camera_name", text="Name")
        row.prop(prefs.settings, "show_camera_lens", text="Lens")
        row.prop(prefs.settings, "show_camera_sensor", text="Sensor")
        row = col.row(align=True)
        row.prop(prefs.settings, "show_camera_dof", text="DoF")
        row.prop(prefs.settings, "show_camera_clip", text="Clip")
        row.prop(prefs.settings, "show_camera_count", text="Count")

        body.separator()
        body.prop(prefs.settings, "master_alpha", text="Opacity")


def draw_interaction_section(layout, prefs):
    header, body = layout.panel("CAMGRID_PT_camera_grid_interaction", default_closed=True)
    header.label(text="Options")

    if body:
        col = body.column()
        col.label(text="Scroll Wheel")
        col.row().prop(prefs.settings, "wheel_mode", text="Mouse Wheel", expand=True)

        col = body.column()
        col.label(text="On Switch")
        col.row().prop(prefs.settings, "on_switch_action", text="")

        body.separator()
        body.prop(prefs.settings, "cycle_cameras", text="Loop Through Cameras")


def draw_frame_camera_section(layout, prefs):
    header, body = layout.panel("CAMGRID_PT_frame_camera", default_closed=True)
    header.label(text="Frame Camera")
    if body:
        col = body.column(align=True)
        col.label(text="Padding")
        col.prop(prefs.settings, "frame_top_padding", text="Top")
        col.prop(prefs.settings, "frame_horizontal_padding", text="Horizontal")
        col.prop(prefs.settings, "frame_bottom_padding", text="Bottom")

        col = body.column()
        col.prop(prefs.settings, "frame_grid_padding", text="Reserve Grid Space")


# ---------------------------------------------------------------------------
#  Popup panel (shown from the header popover button)
# ---------------------------------------------------------------------------


class CAMGRID_PT_grid_popup(Panel):
    bl_label = "Camera Grid Options"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_ui_units_x = 13

    def draw(self, context):
        layout = self.layout

        prefs = context.preferences.addons.get(__package__).preferences
        props = context.scene.camgrid_props

        layout.label(text="Camera Grid")
        draw_filter_section(layout, prefs, props)
        draw_layout_section(layout, prefs)
        draw_interaction_section(layout, prefs)
        draw_frame_camera_section(layout, prefs)


# ---------------------------------------------------------------------------
#  Sidebar panel (shown in the right panel when panel_location == "UI")
# ---------------------------------------------------------------------------


class CAMGRID_PT_grid_sidebar(Panel):
    bl_label = "Camera Grid"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Camera Grid"

    @classmethod
    def poll(cls, context):
        prefs = context.preferences.addons[__package__].preferences
        return prefs.settings.panel_location == "UI"

    def draw(self, context):
        layout = self.layout
        prefs = context.preferences.addons.get(__package__).preferences
        props = context.scene.camgrid_props
        grid_active = viewport_grid.is_grid_active(context)

        col = layout.column(align=True)
        col.operator("camgrid.toggle_grid", icon="IMGDISPLAY", depress=grid_active)
        if grid_active and prefs.settings.display_type == "THUMBNAILS":
            col.operator("camgrid.refresh_previews", icon="FILE_REFRESH")
        layout.operator("camgrid.frame_camera", icon="MOD_LENGTH")

        layout.separator()
        draw_filter_section(layout, prefs, props)
        draw_layout_section(layout, prefs)
        draw_interaction_section(layout, prefs)
        draw_frame_camera_section(layout, prefs)


def draw_grid_header_button(self, context):
    if context.area.type != "VIEW_3D":
        return
    prefs = context.preferences.addons.get(__package__).preferences
    if prefs.settings.panel_location != "HEADER":
        return
    layout = self.layout
    grid_active = viewport_grid.is_grid_active(context)

    row = layout.row(align=True)
    row.operator("camgrid.toggle_grid", text="", icon="IMGDISPLAY", depress=grid_active)
    if grid_active and prefs.settings.display_type == "THUMBNAILS":
        row.operator("camgrid.refresh_previews", text="", icon="FILE_REFRESH")
    row.operator("camgrid.frame_camera", text="", icon="MOD_LENGTH")
    row.popover("CAMGRID_PT_grid_popup", text="")
