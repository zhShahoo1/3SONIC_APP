Here’s a polished, repo-ready **README.md** you can drop at the project root.

---

# 3SONIC — Desktop UI for 3D Ultrasound Scanning

A compact desktop application for controlling a benchtop ultrasound scanner. It provides:

* Live ultrasound + webcam streams
* XY-Z jogging & nozzle rotation (G-code over serial)
* One-click **Insert Bath / Position for Scan** workflow
* **Start Scan** / **Dual Sweep** triggering (spawns recorder)
* Automatic post-processing (PNG preview + NIfTI volume)
* **Overview picker** to browse/open `Example_slices.png` from past scans
* Optional **ITK-SNAP** launch on the active DICOM series
* Graceful shutdown with hardware cleanup

Built with **Flask** (backend), a small **vanilla JS** frontend, and optional **pywebview** shell so it feels like a native app.

---

## Contents

* [Architecture](#architecture)
* [Screens & Controls](#screens--controls)
* [Installation](#installation)
* [Configuration](#configuration)
* [Run It](#run-it)
* [Data & Post-Processing](#data--post-processing)
* [Keyboard & Safety](#keyboard--safety)
* [Troubleshooting](#troubleshooting)
* [API Endpoints (dev)](#api-endpoints-dev)
* [Project Layout](#project-layout)
* [License](#license)

---

## Architecture

**Backend** (Python):

* `Flask` app (`app/main.py`)
* **Serial manager** singleton (queued I/O + `send_now` fire-and-forget)
* Scanner control (`app/core/scanner_control.py`) – homing, jogs, scan paths
* Ultrasound SDK wrapper (`app/core/ultrasound_sdk.py`) – MJPEG generator
* Webcam helper (`app/utils/webcam.py`) – MJPEG generator
* ITK-SNAP integration (`app/integrations/itk_snap.py`)
* Post-processing pipeline (`postprocessing.py`) – PNG preview + NIfTI

**Frontend**:

* `templates/main.html`
* `static/css/app.css` – modern, dark “Nordic” theme
* `static/js/app.js` – controller (jogs, toggles, overlays, overview picker)

**Desktop shell**:

* Optional `pywebview` host window (Windows dark titlebar tweak included)

---

## Screens & Controls

### Live views

* **Ultrasound View** (default) – auto-reloads if the stream drops; gentle overlay shows reconnection status; will auto-attempt a driver restart when needed.
* **Camera View** – full-size webcam feed.

Use the header toggle buttons to switch; the active button and title update accordingly.

### Jogging & Rotation

* Minimal axis readout (replaces the old step-size select). The UI now shows live X/Y/Z/E values in the header area. Single-click rotates/moves use server-configured E-axis defaults (see `app/config.py::Config.E_AXIS_DEFAULT_STEP`) and the backend clamps large single-click steps for safety.
* Directional buttons (X/Y/Z) and rotation CW/CCW (E axis).
* **Keyboard shortcuts** (frontend jogs are debounced to avoid flooding):

  * Arrows: `← →` (X), `↑ ↓` (Y)
  * PageUp / PageDown: Z±
  * `WASD`: Y−/Y+/X−/X+
  * `R` / `F`: rotate CW / CCW
  * `[` / `]`: cycle step size, `1`/`2`/`3` = 0.1 / 1 / 10 mm presets

### Scan workflow

* **Initialize** homes & moves to a known center pose, restoring the last E position.
* **Insert Bath** (toggle):

  1. Lower plate to target Z for specimen placement
  2. Return to “position for scan”
* **Start Scan** / **Dual Sweep** spawns the recorder and runs a scan path.

### Overview picker

Tap **Overview Image** to list scans that contain `Example_slices.png`.
Open images in your OS viewer (“Open”) or new tab (“View”).

---

## Installation

> Tested primarily on Windows; Linux/macOS work too (when your ultrasound SDK & serial driver support them).

1. **Clone & venv**

```bash
git clone <your-repo-url> 3sonic
cd 3sonic
python -m venv .venv
. .venv/Scripts/activate   # Windows
# or: source .venv/bin/activate
```

2. **Install deps**

```bash
pip install -r requirements.txt
```

Typical packages used by this app:

* Flask, pyserial, numpy, pandas, matplotlib, SimpleITK
* opencv-python (webcam), pywebview (optional desktop), keyboard/pygetwindow (optional), pillow

> If your ultrasound vendor ships a Python SDK or DLL, follow their install guide and ensure `app/core/ultrasound_sdk.py` can import and initialize it.

---

## Configuration

All tunables live in `app/config.py::Config`. Important ones:

* **Directories**

  * `BASE_DIR`, `STATIC_DIR`, `TEMPLATES_DIR`, `APP_DIR`
  * `DATA_DIR` — where scans are recorded and post-processed
    👉 By default `DATA_DIR` lives under the project root (`data/`). The app exposes
    these files via the `/data/<scan_folder>/...` route (for example:

    ```text
    /data/20250912_092928/Example_slices.png
    ```

    You can override `DATA_DIR` in `app/config.py` if you prefer a different location.

* **Geometry & motion**

  * `X_MAX`, `Y_MAX`, `Z_MAX`, `OFFSET_X`, `OFFSET_Y`, `OFFSET_Z`
  * `JOG_FEED_MM_PER_MIN`, `FAST_FEED_MM_PER_MIN`, `SCAN_SPEED_MM_PER_MIN`
  * `SCAN_POSE = {"X": 53.5, "Y": 53.5, "Z": 10.0}`
  * `POS_TOL_MM`, `POLL_INTERVAL_S`, `POLL_TIMEOUT_S`

* **Rotation (E axis)**

  * `E_AXIS_DEFAULT_STEP` (default step per rotate key)
  * `E_AXIS_ALLOW_COLD_EXTRUSION` (true: allow E moves w/o temp)

* **Recorder**

  * `DELAY_BEFORE_RECORD_S`
  * Script path: `app/scripts/record.py` (spawned with args `"0"`/`"1"`)

* **Flags (files used to signal scan state)**

  * `SCANNING_FLAG_FILE`, `MULTISWEEP_FLAG_FILE`, `RECDIR_FILE`

* **Ultrasound**

  * Anything required by your SDK in `app/core/ultrasound_sdk.py`.

---

## Run It

### Desktop mode (recommended)

```bash
python -m app.main
```

This starts Flask on `http://127.0.0.1:5001` and opens a native window via **pywebview** (if installed). The app now uses a frameless desktop window when pywebview supports it so the UI can provide custom, hideable window controls in the header.

Behavior notes for Desktop mode:

- If `pywebview` is installed and the current backend supports frameless windows, the app will open a frameless window and expose a small JS API that the frontend uses to minimize, maximize/restore, and close the native window.
- The header includes a small toggle that shows/hides the custom window controls (minimize / maximize / close). The visibility preference is persisted in the app's `localStorage`.
- To preserve standard OS resize behavior, only the central header area (a "drag handle") is draggable; the window edges remain non-draggable so the OS resize handles work normally. If you observe different behavior on a platform/backend (CEF/Edge/WinForms), see Troubleshooting below.
- If frameless creation fails or `pywebview` is not installed, the app falls back to opening a normal browser window/tab or a standard framed pywebview window.

Small checklist before using Desktop mode:

- Install `pywebview` (already listed in `requirements.txt`) if you want the native shell integration.
- On Windows, the app attempts a dark-titlebar tweak; this is best-effort and harmless if unsupported.

Example:

```bash
python -m app.main
```

### Browser fallback

If `pywebview` isn’t installed, your default browser will open to the local app.

---

## Data & Post-Processing

When a scan finishes, `postprocessing.py` will:

* Read the `dicom_series/` into SimpleITK
* Save a 3×3 static preview **`Example_slices.png`**
* Add scalebars and export **`nifti_volume.nii.gz`**
* Append total scan time to `config.txt`
* (Optionally) display an interactive Matplotlib viewer

**Folder layout (per scan)**:

```
data/
  2025-08-26_10-31-12/
    dicom_series/ ...
    Example_slices.png
    nifti_volume.nii.gz
    config.txt
```

**Overview picker** uses those `Example_slices.png` files to build the list. The
files are served at `/data/<scan-folder>/Example_slices.png` by the app.

---

## Keyboard & Safety

* Frontend jogs are **debounced** to avoid flooding the controller (`app.js`).
* Backend moves use **relative** `G91`/`G1` jogs and immediately return (`send_now`), so rapid clicks don’t block on reads.
* The ultrasound stream includes a **status overlay** with **auto-reload** and periodic **backend restarts** if reconnection stalls (`/api/us-restart`).
* **Graceful exit** (`/api/exit`) stops ultrasound, releases webcam, disables keyboard hooks, closes serial, and shuts the app down.

If you supply `app/core/keyboard_control.py` (optional), the app will best-effort enable it at startup. Remove/disable it if you don’t want global keyboard hooks.

---

## Troubleshooting

**No serial / timeouts**

* Check Device Manager (Windows) for **CH340** / **USB-SERIAL** devices.
* Verify baudrate matches firmware (default 115200).
* If you see repeated `Timeout waiting for 'G0 F...'`, lower `JOG_FEED_MM_PER_MIN` or ensure firmware returns `ok` promptly. The app already minimizes repeated `F` changes and uses queued reads.

**Ultrasound stream is black/cropped**

* The `<img>` uses `object-fit: contain;` in CSS and a larger container; sizing issues are typically fixed by the shipped `app.css`.
* Cable reseats are detected; the overlay will try reconnection and occasionally restart the driver (`/api/us-restart`).

**Overview images don’t show**

* Ensure `DATA_DIR` points to your scan output root (defaults to project `data/`).
  The app exposes these files at `/data/<scan-folder>/...` so the Overview picker
  can access them without requiring them to live under `static/`.
* Confirm each scan folder contains `Example_slices.png`.

**ITK-SNAP button does nothing**

* Install ITK-SNAP and verify the path/command used in `app/integrations/itk_snap.py`.

---

## API Endpoints (dev)

* **Streams**

  * `GET /ultrasound_video_feed` — MJPEG ultrasound
  * `GET /video_feed` — MJPEG webcam
  * `POST /api/us-restart` — restart ultrasound stack

* **Motion**

  * `POST /move_probe` — `{direction, step}` where direction ∈
    `Xplus|Xminus|Yplus|Yminus|Zplus|Zminus|rotateClockwise|rotateCounterclockwise`
  * `GET /api/position` — returns `{x, y, z, e}` (best-effort floats or nulls); frontend polls this to populate the readout.
  * `GET|POST /scanpath` — now accepts `POST` with JSON body (ranges/params) in addition to existing `GET` query usage.
  * `POST /multisweep` — legacy clients POSTing to `/multisweep` are accepted; this endpoint forwards to the canonical `/multipath` handler for compatibility.

* **Workflow**

  * `GET /initscanner`
  * `GET /scanpath`, `GET /multipath`
  * `POST /api/lower-plate`
  * `POST /api/position-for-scan`

* **Overview**

  * `GET /api/overview/list?limit=50` → `{items:[{folder, png_url, created}]}`
  * `POST /api/overview/open` → `{folder}`

* **Utilities**

  * `POST /open-itksnap`
  * `POST /api/exit`
  * `GET /shutdown` (alias)

All routes bind to `127.0.0.1:5000` by default (not exposed to the network).

---

## Project Layout

```
app/
  main.py                        # Flask app + desktop launcher
  config.py                      # Config class (paths, motion, speeds, etc.)
  core/
    scanner_control.py           # Jogging, homing, scan path, E-axis persistence
    serial_manager.py            # Serial singleton (queue + send_now + wait)
    ultrasound_sdk.py            # Ultrasound initialize/generate/reset
    keyboard_control.py          # (optional) global keyboard hooks
  integrations/
    itk_snap.py                  # Open ITK-SNAP against latest DICOM
  utils/
    webcam.py                    # Webcam MJPEG generator
static/
  css/app.css
  js/app.js
  data/                          # (recommended) scan output root for PNG picker
templates/
  main.html
scripts/
  record.py                      # Recorder invoked by scans (multi arg 0/1)
postprocessing.py                # Example_slices.png + NIfTI + times + viewer
Other top-level folders introduced by recent refactors:

```
src/                              # bundled native resources (DLL, DICOM templates)
  usgfw2wrapper.dll
  dcmimage.dcm

run/                              # ephemeral runtime state (ignored by git)
  scanning
  multisweep
  recdir
  scanplan.json
```

---

## Runtime / State files

The app uses a small `run/` directory at the project root to hold ephemeral
runtime state such as marker files and the active `scanplan.json` that the
recorder reads. These files are created and updated at runtime and are
ignored by Git by default. See `app/config.py::Config` for the exact paths
(`Config.STATE_DIR`, `Config.SCANPLAN_FILE`, etc.).

Do not commit changing values from `run/` — keep only templates/config
defaults under the project root (e.g., sample `scanplan.json` or docs).

```

---

## 


If this project is **proprietary**, keep this section as-is and restrict distribution.
If you intend to open-source it, add an OSI license (e.g. MIT/Apache-2.0) and include the matching `LICENSE` file.

---

### Acknowledgements

* ITK-SNAP (image segmentation/visualization)
* SimpleITK (medical image IO)
* Marlin/RepRap G-code conventions (motion control)

---

**Happy scanning!**
