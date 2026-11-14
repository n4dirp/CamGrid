# ![Alt text](camera_grid/icons/camera_grid_icon_light.png) Camera Grid

**A camera grid overlay for Blender's 3D Viewport.**
Browse and switch between all your scene cameras without leaving the viewport.


[![Blender 4.5+](https://img.shields.io/badge/Blender-4.5%2B-E87D0D?logo=blender&logoColor=white)](https://www.blender.org/)
[![Version](https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fn4dirp%2FCamGrid%2Fmain%2Fcamera_grid%2Fblender_manifest.toml&query=%24.version&label=version&color=blue)](CHANGELOG.md)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)](LICENSE)
[![Blender Extensions](https://img.shields.io/badge/Blender_Extensions-Camera_Grid-E87D0D?logo=blender&logoColor=white)](https://extensions.blender.org/add-ons/camera-grid/)

## Features

- **Quick switching** - Click, drag, scroll, or keybind to switch between cameras
- **Preview thumbnails** - Shows live viewport thumbnails for every camera, or use solid-tile and dots display modes
- **Customizable** - Configure tile size, rows, columns, alignment, and display mode
- **Collection filtering** - Restrict the grid to a specific collection and choose whether hidden cameras appear
- **Framing controls** - Frame the active camera to the viewport with configurable padding

## Installation

### From Blender Extensions (Recommended)

Install from [Blender Extensions](https://extensions.blender.org/add-ons/camera-grid/) by dragging the **Install** button into Blender, or search for **Camera Grid** under `Edit > Preferences > Get Extensions`. This also enables automatic updates.

### Build from source (Latest Development Version)

Requires Blender on your PATH (or use the full path to the executable):

```bash
git clone https://github.com/n4dirp/CamGrid.git
cd "CamGrid/camera_grid"
blender --command extension build
```

Drag the generated `camera_grid-<version>.zip` into Blender.

## Usage

- **Toggle the grid:** click the grid icon at the right end of the 3D Viewport header, or press `Alt+Shift+C`. The toggle can also live in the right sidebar (configurable).
- **Quick options:** the popover next to the header toggle, or the panel in the right sidebar.
- **Advanced options:** `Edit > Preferences > Extensions > Camera Grid`.

## Shortcuts

### Viewport

| Shortcut      | Action                          |
| ------------- | ------------------------------- |
| `Alt+Shift+C` | Toggle camera grid              |
| `Shift+Home`  | Frame active camera to viewport |

### Camera Grid — Keyboard

| Shortcut            | Action                                          |
| ------------------- | ----------------------------------------------- |
| `F5`                | Refresh camera preview thumbnails               |
| `Ctrl+Shift+1/2/3`  | Switch display mode (Dots / Labels / Thumbnails)|

### Camera Grid — Mouse

| Input          | Action                                       |
| -------------- | -------------------------------------------- |
| Left click     | Switch to camera                             |
| Left drag      | Quick-switch through cameras                 |
| Right click    | Select camera                                |
| Right drag     | Paint-select cameras                         |
| Scroll         | Switch camera or scroll rows (configurable)  |
| `Shift+Scroll` | Switch camera or scroll rows (inverted)      |
| `Ctrl+Scroll`  | Resize tiles                                 |

## Changelog

See [CHANGELOG.md](CHANGELOG.md) or the [version history](https://extensions.blender.org/add-ons/camera-grid/) on Blender Extensions.

## Support & Contributing

- Found a bug or have a feature request? [Open an issue](https://github.com/n4dirp/CamGrid/issues) — please include your Blender version, OS, and steps to reproduce.
- Pull requests are welcome.
- Enjoying Camera Grid? A rating on [Blender Extensions](https://extensions.blender.org/add-ons/camera-grid/) helps a lot.

## License

[GPL-3.0-or-later](LICENSE)
