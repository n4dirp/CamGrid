# Camera Grid - Blender Extension
# Viewport camera grid overlay for quick camera switching
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

import bpy
from bpy.props import PointerProperty

from . import viewport_grid
from .panels import CAMGRID_PT_grid_popup, CAMGRID_PT_grid_sidebar, draw_grid_header_button
from .preferences import CAMGRID_PG_scene, _update_logger_from_prefs
from .preferences import classes as prefs_classes

logger = logging.getLogger(__package__)
logger.propagate = False
logger.addHandler(logging.NullHandler())

# ------------------------------------------------------------------------
#    Keymap
# ------------------------------------------------------------------------

addon_keymaps: list[tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []


def _register_keymaps():
    """Register Camera Grid keymap items into the addon keyconfig."""
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return
    km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
    kmi = km.keymap_items.new("camgrid.toggle_grid", type="C", value="PRESS", alt=True, shift=True)
    addon_keymaps.append((km, kmi))
    kmi = km.keymap_items.new("camgrid.frame_camera", type="HOME", value="PRESS", shift=True)
    addon_keymaps.append((km, kmi))


def _unregister_keymaps():
    """Remove all registered keymap items."""
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()


# ------------------------------------------------------------------------
#    Registration
# ------------------------------------------------------------------------

classes = (
    *prefs_classes,
    CAMGRID_PT_grid_popup,
    CAMGRID_PT_grid_sidebar,
    *viewport_grid.classes,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.camgrid_props = PointerProperty(type=CAMGRID_PG_scene)
    bpy.types.VIEW3D_HT_header.append(draw_grid_header_button)
    _update_logger_from_prefs()
    viewport_grid.register()
    _register_keymaps()


def unregister():
    _unregister_keymaps()
    bpy.types.VIEW3D_HT_header.remove(draw_grid_header_button)
    viewport_grid.unregister()

    try:
        del bpy.types.Scene.camgrid_props
    except AttributeError:
        pass

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
