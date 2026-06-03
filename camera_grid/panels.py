"""Camera Grid UI panels and header draw."""

from bpy.types import Panel

from . import viewport_grid


class CAMGRID_PT_grid_popup(Panel):
    bl_label = "Camera Grid Options"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"

    def draw(self, context):
        layout = self.layout
        prefs = context.preferences.addons.get(__package__).preferences
        props = context.scene.camgrid_props

        layout.label(text="Camera Grid")

        layout.separator()

        col = layout.column()
        col.label(text="Alignment")
        col.row().prop(prefs.settings, "alignment", expand=True)

        col = layout.column()
        col.label(text="Display Mode")
        col.row().prop(prefs.settings, "display_type", text="Display Mode", expand=True)

        sub = layout.column(align=True)
        if prefs.settings.display_type == "THUMBNAILS":
            sub.prop(prefs.settings, "preview_size", text="Size")
            sub.prop(prefs.settings, "preview_max_rows", text="Max Rows")
            sub.prop(prefs.settings, "preview_max_columns", text="Max Columns")
        else:
            sub.prop(prefs.settings, "tile_size", text="Size")
            sub.prop(prefs.settings, "max_rows", text="Max Rows")
            sub.prop(prefs.settings, "max_columns", text="Max Columns")

        if prefs.settings.display_type == "THUMBNAILS":
            col = layout.column()
            col.prop(prefs.settings, "preview_disable_overlays", text="Disable Overlays")
            col.prop(prefs.settings, "preview_show_names", text="Show Names")

        layout.separator()
        col = layout.column()
        col.label(text="Interaction")
        col.prop(prefs.settings, "view_from_camera", text="Switch to Camera View")
        col.label(text="Mouse Wheel")
        col.row().prop(prefs.settings, "wheel_mode", text="Mouse Wheel", expand=True)

        layout.separator()
        layout.label(text="Source Collection")
        layout.prop(props, "source_collection", text="")
        layout.prop(prefs.settings, "show_hidden")


def draw_grid_header_button(self, context):
    if context.area.type != "VIEW_3D":
        return
    layout = self.layout

    prefs = context.preferences.addons.get(__package__).preferences
    grid_active = viewport_grid.is_grid_active(context)

    row = layout.row(align=True)
    row.operator("camgrid.toggle_grid", text="", icon="RESTRICT_VIEW_ON", depress=grid_active)
    grid_active = viewport_grid.is_grid_active(context)
    if grid_active and prefs.settings.display_type == "THUMBNAILS":
        row.operator("camgrid.refresh_previews", text="", icon="FILE_REFRESH")
    row.operator("camgrid.frame_camera", text="", icon="MOD_LENGTH")
    row.popover("CAMGRID_PT_grid_popup", text="")
