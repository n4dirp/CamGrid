"""Camera Grid add-on preferences and logging infrastructure."""

import logging
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty
from bpy.types import AddonPreferences

from . import properties

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


class CAMGRID_AddonPreferences(AddonPreferences):
    bl_idname = __package__

    settings: PointerProperty(type=properties.CAMGRID_PG_settings)

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
