# Changelog

## [1.4.0] - 2026-08-14

### Added
- New camera info fields in the info text: sensor size (respecting sensor fit), depth of field, and clipping range
- Individual toggles for each info text field (Lens, Sensor, DoF, Clip, Name, Count)

### Improved
- Lens display now respects the camera's lens unit (millimeters or field of view)

## [1.3.1] - 2026-08-14

### Fixed
- Fixed the active camera border color to use Blender's selection colors (selected vs. selected+active)

## [1.3.0] - 2026-07-30

### Added
- Global keyboard shortcuts for Camera Grid (Toogle panel: Shift+Alt+C, Frame Camera: Shift+Home)
- Added a "Panel Location" option to place grid controls in the 3D viewport header or the right sidebar

## [1.2.1] - 2026-07-21

### Fixed
- Fixed the camera grid panel interaction in new windows

## [1.2.0] - 2026-06-27

### Added
- Support multiple simultaneous viewports in Camera Grid

## [1.1.0] - 2026-06-17

### Added
- Added camera framing options and moved into the popup panel
- Add collection filtering to include only collections with cameras
- Introduce grid-specific framing options and Escape key interaction

### Improved
- Reorganize main UI into collapsible panels for better layout management
- Optimized theme compatibility, UI scaling, and viewport grid rendering/styling
