# Camera Grid

Viewport camera grid overlay for quick camera switching.

## Features

- Click, drag, scroll, or keybind camera switching
- Thumbnail or solid-tile display modes
- Scrollable layout for scenes with many cameras

## Shortcuts

### Keyboard *(Global)*

| Shortcut | Action |
| --- | --- |
| `Alt+Shift+C` | Toggle camera grid on/off |
| `Shift+Home` | Frame active camera to viewport |

### Keyboard *(when the cursor is over the grid)*

| Shortcut | Action |
| --- | --- |
| `Left/Right/Up/Down Arrow` | Navigate between cameras |
| `F5` | Refresh camera preview thumbnails |
| `Esc` | Close grid (requires *Close Grid with ESC* in prefs) |

### Mouse *(inside the grid)*

| Input | Action |
| --- | --- |
| `Left Click` | Switch to camera |
| `Left Drag` | Quick-switch through cameras |
| `Right Click` | Select camera |
| `Right Drag` | Paint-select cameras |
| `Scroll` | Switch camera (or scroll rows, configurable) |
| `Shift+Scroll` | Switch camera (or scroll rows, inverted) |
| `Ctrl+Scroll` | Resize tiles |
| `Scrollbar` | Drag to scroll rows |

## Requirements

- Blender 5.1.0+

## Build from Source

```bash
git clone https://github.com/n4dirp/CamGrid.git
cd CamGrid/camera_grid
blender --command extension build
```
