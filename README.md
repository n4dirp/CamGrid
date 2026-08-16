# Camera Grid

A viewport camera grid overlay for Blender that lets you switch between cameras quickly by clicking, dragging, scrolling, or using keyboard shortcuts.

## Features

- **Quick switching** -- Click, drag, scroll, or keybind to switch between cameras
- **Preview thumbnails** -- Shows live viewport thumbnails for every camera, or use solid-tile and dots display modes
- **Grid navigation** -- Arrow keys step between cameras and the grid scrolls for scenes with many cameras
- **Customizable** -- Configure tile size, rows, columns, alignment, and display mode
- **Theme-aware** -- Matches your Blender theme while supporting custom colors
- **Collection filtering** -- Restrict the grid to a specific collection and choose whether hidden cameras appear
- **Framing controls** -- Frame the active camera to the viewport with configurable padding

## Location

**How do I toggle the grid?** Click the grid icon at the right end of the 3D viewport header, or in the right sidebar (configurable), or press `Alt+Shift+C`.

**Where are the settings?** The options appear as a popover next to the header toggle, or as a panel in the right sidebar; advanced options are in `Edit > Preferences > Extensions > Camera Grid`.

## Shortcuts

Input | Shortcut | Action
--- | --- | ---
Keyboard | `Alt+Shift+C` | Toggle camera grid
Keyboard | `Shift+Home` | Frame active camera to viewport
Keyboard · Grid focused | `Arrow Keys` | Navigate between cameras
Keyboard · Grid focused | `F5` | Refresh camera preview thumbnails
Keyboard · Grid focused | `Esc` | Close grid (optional)
Grid | `Primary Click` | Switch to camera
Grid | `Primary Drag` | Quick-switch through cameras
Grid | `Secondary Click` | Select camera
Grid | `Secondary Drag` | Paint-select cameras
Grid | `Scroll` | Switch camera or scroll rows (configurable)
Grid | `Shift+Scroll` | Switch camera or scroll rows (inverted)
Grid | `Ctrl+Scroll` | Resize tiles
Grid | `Scrollbar` | Drag to scroll rows
Grid | `Ctrl+Shift+1/2/3` | Switch display mode (Dots / Labels / Thumbnails)

Primary/secondary click roles can be swapped with the "Select with Right Click" option in the add-on Options.

## Requirements

- Blender 5.1.0+

## Build from Source

```bash
git clone https://github.com/n4dirp/CamGrid.git
cd CamGrid/camera_grid
blender --command extension build
```
