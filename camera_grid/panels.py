"""Camera Grid UI panels and header draw."""

from bpy.types import Panel

from . import viewport_grid


class CAMGRID_PT_grid_popup(Panel):
    bl_label = "Camera Grid Options"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_ui_units_x = 11

    def draw(self, context):
        layout = self.layout
        prefs = context.preferences.addons.get(__package__).preferences
        props = context.scene.camgrid_props

        layout.label(text="Camera Grid")

        header, body = layout.panel("CAMGRID_PT_camera_grid_filter_list", default_closed=True)
        header.label(text="Filter")
        if body:
            row = body.row(align=True)
            row.prop(props, "source_collection", text="")
            row.prop(prefs.settings, "filter_camera_collections", text="", icon="VIEW_CAMERA")
            body.prop(prefs.settings, "show_hidden", text="Show Hidden Cameras")

        header, body = layout.panel("CAMGRID_PT_camera_grid_ui", default_closed=False)
        header.label(text="Interface")
        if body:
            col = body.column()
            col.label(text="Alignment")
            col.row().prop(prefs.settings, "alignment", expand=True)

            col = body.column()
            col.label(text="Display Mode")
            col.prop(prefs.settings, "display_type", text="Display Mode", expand=True)

            col = body.column()
            col.label(text="Appearance")

            sub = col.column(align=True)
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
                row = body.row(align=True)
                row.prop(prefs.settings, "preview_disable_overlays", text="Hide Overlays")
                row.prop(prefs.settings, "preview_show_names", text="Show Names")

            body.separator()
            col = body.column(align=True)
            col.label(text="Footer Text")
            row = col.row(align=True)
            row.prop(prefs.settings, "show_active_camera_name", text="Name")
            row.prop(prefs.settings, "show_camera_settings", text="Lens")
            row.prop(prefs.settings, "show_camera_count", text="Count")

        header, body = layout.panel("CAMGRID_PT_camera_grid_interaction", default_closed=False)
        header.label(text="Behavior")
        if body:
            col = body.column()
            col.label(text="Mouse Wheel")
            col.row().prop(prefs.settings, "wheel_mode", text="Mouse Wheel", expand=True)

            col = body.column()
            col.label(text="On Switch")
            col.prop(prefs.settings, "on_switch_action", text="")

            col = body.column()
            col.prop(prefs.settings, "cycle_cameras", text="Loop Through Cameras")
            col.prop(prefs.settings, "close_on_esc", text="Exit with Escape Key")

        header, body = layout.panel("CAMGRID_PT_frame_camera", default_closed=True)
        header.label(text="Frame Padding")
        if body:
            col = body.column(align=True)
            col.prop(prefs.settings, "frame_top_padding", text="Top")
            col.prop(prefs.settings, "frame_bottom_padding", text="Bottom")
            col.prop(prefs.settings, "frame_horizontal_padding", text="Horizontal")

            col = body.column()
            col.prop(prefs.settings, "frame_grid_padding", text="Reserve Grid Space")


def draw_grid_header_button(self, context):
    if context.area.type != "VIEW_3D":
        return
    layout = self.layout
    prefs = context.preferences.addons.get(__package__).preferences
    grid_active = viewport_grid.is_grid_active(context)

    row = layout.row(align=True)
    row.operator("camgrid.toggle_grid", text="", icon="IMGDISPLAY", depress=grid_active)
    if grid_active and prefs.settings.display_type == "THUMBNAILS":
        row.operator("camgrid.refresh_previews", text="", icon="FILE_REFRESH")
    row.operator("camgrid.frame_camera", text="", icon="MOD_LENGTH")
    row.popover("CAMGRID_PT_grid_popup", text="")
