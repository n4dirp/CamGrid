# Camera Grid - Blender Extension
# Viewport camera grid overlay for quick camera switching
# SPDX-License-Identifier: GPL-3.0-or-later

import logging

import bpy
from bpy.props import PointerProperty

from . import viewport_grid
from .panels import CAMGRID_PT_grid_popup, draw_grid_header_button
from .preferences import CAMGRID_PG_scene, _update_logger_from_prefs
from .preferences import classes as prefs_classes

logger = logging.getLogger(__package__)
logger.propagate = False
logger.addHandler(logging.NullHandler())

# ------------------------------------------------------------------------
#    Registration
# ------------------------------------------------------------------------

classes = (
    *prefs_classes,
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
