"""Camera grid interactive state and per-area tracking."""

from dataclasses import dataclass
from enum import Enum, auto

import bpy
from bpy.types import Operator


class _DragState(Enum):
    IDLE = auto()
    SWITCH_PRESSED = auto()
    SWITCH_DRAGGING = auto()
    SELECT_PRESSED = auto()
    SELECT_DRAGGING = auto()
    SCROLLBAR_DRAGGING = auto()


@dataclass
class AreaGridState:
    """Area-specific interactive state for a camera grid."""

    target_area_pointer: int
    target_region_pointer: int
    current_start_row: int = -1
    last_active_index: int = -1
    mouse_in_grid: bool = False
    hovered_tile: int | None = None
    scrollbar_hovered: bool = False
    enabled: bool = True


class GridState:
    """Encapsulates all interactive and UI state for the camera grid."""

    handler: object | None = None
    window_operators: dict[int, Operator] = {}

    # Track states for each active 3D Viewport area pointer
    areas: dict[int, AreaGridState] = {}

    drag_state: _DragState = _DragState.IDLE
    drag_tile: int = -1
    drag_last_tile: int = -1
    drag_last_scroll_time: float = 0.0
    drag_select_value: bool = False

    @classmethod
    def reset(cls):
        cls.handler = None
        cls.window_operators.clear()
        cls.areas.clear()
        cls.drag_state = _DragState.IDLE
        cls.drag_tile = -1
        cls.drag_last_tile = -1
        cls.drag_last_scroll_time = 0.0
        cls.drag_select_value = False


def _ensure_area_states():
    """Register AreaGridState for any VIEW_3D area that lacks one."""
    if not GridState.areas or not any(s.enabled for s in GridState.areas.values()):
        return
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            ptr = area.as_pointer()
            if ptr in GridState.areas:
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if not region:
                continue
            GridState.areas[ptr] = AreaGridState(
                target_area_pointer=ptr,
                target_region_pointer=region.as_pointer(),
                enabled=False,
            )
