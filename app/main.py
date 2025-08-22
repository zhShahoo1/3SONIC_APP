# app/main.py
from __future__ import annotations


import sys
import time
import subprocess as sp
from pathlib import Path
from typing import Tuple, Iterable
from flask import Flask, Response, jsonify, render_template, request

# -------------------------------------------------------------------
# Import handling: allow running both `python -m app.main` and `python app/main.py`
# -------------------------------------------------------------------
if __package__ is None or __package__ == "":
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from app.config import Config
    from app.core import scanner_control as pssc
    from app.core import ultrasound_sdk
    from app.utils.webcam import generate_frames, camera
    from app.integrations.itk_snap import open_itksnap_with_dicom_series
    from app.core.serial_manager import start_serial                # ✅ added
    from app.core.keyboard_control import start_keyboard_listener, enable_keyboard  # ✅ added
else:
    from .config import Config
    from .core import scanner_control as pssc
    from .core import ultrasound_sdk
    from .utils.webcam import generate_frames, camera
    from .integrations.itk_snap import open_itksnap_with_dicom_series
    from .core.serial_manager import start_serial                   # ✅ added
    from .core.keyboard_control import start_keyboard_listener, enable_keyboard  # ✅ added



# ------------------------------------------------------------------------------
# Force all paths to use the single, root-level static/ and templates/
# ------------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_FOLDER = (PROJECT_ROOT / "static").resolve()
TEMPLATE_FOLDER = (PROJECT_ROOT / "templates").resolve()

# IMPORTANT: override Config paths so every module/script uses the SAME static/
# (prevents creation of app/static)
Config.BASE_DIR = PROJECT_ROOT
Config.APP_DIR = (PROJECT_ROOT ).resolve()
Config.STATIC_DIR = STATIC_FOLDER
Config.TEMPLATES_DIR = TEMPLATE_FOLDER
Config.DATA_DIR = (STATIC_FOLDER / "data").resolve()
# Config.FRAMES_DIR = (Config.DATA_DIR / "frames").resolve()
# Config.RAWS_DIR = (Config.DATA_DIR / "raws").resolve()
# Config.DICOM_DIR = (Config.DATA_DIR / "dicom").resolve()
# Config.LOGS_DIR = (Config.DATA_DIR / "logs").resolve()

# Ensure data dirs exist at the *root-level* static/
# for p in (Config.DATA_DIR, Config.FRAMES_DIR, Config.RAWS_DIR, Config.DICOM_DIR, Config.LOGS_DIR):
#     p.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------------------
# Flask app setup (serve from root-level static/ and templates/)
# ------------------------------------------------------------------------------
# Flask app setup ...
app = Flask(__name__, static_folder=str(STATIC_FOLDER), template_folder=str(TEMPLATE_FOLDER))
app.config.from_object(Config)


# Start shared serial threads
try:
    start_serial()
except Exception as e:
    print(f"[Serial] background start failed: {e}")

# Start keyboard control (non-blocking)
try:
    enable_keyboard(True)
    start_keyboard_listener()
except Exception as e:
    print(f"[Keyboard] listener not started: {e}")




# ------------------------------------------------------------------------------
# Ultrasound live stream bootstrap (don’t crash if DLL/probe missing)
# ------------------------------------------------------------------------------

def _init_ultrasound() -> Tuple[int, int, Tuple[float, float]]:
    """Initialize ultrasound once; fall back to config dimensions if it fails."""
    try:
        w, h, res = ultrasound_sdk.initialize_ultrasound()
        print(f"[startup] Ultrasound ready: {w}x{h}, res={res}")
        return w, h, res
    except Exception as e:
        print(f"[startup] Ultrasound init warning: {e}")
        return (Config.ULTRA_W if hasattr(Config, "ULTRA_W") else Config.ULTRASOUND_WIDTH,
                Config.ULTRA_H if hasattr(Config, "ULTRA_H") else Config.ULTRASOUND_HEIGHT,
                (0.0, 0.0))

_UL_W, _UL_H, _UL_RES = _init_ultrasound()


def _ultrasound_mjpeg_stream() -> Iterable[bytes]:
    """Stream JPEG frames; supports both new/legacy generator signatures."""
    try:
        gen = ultrasound_sdk.generate_image()  # type: ignore
    except TypeError:
        gen = ultrasound_sdk.generate_image(_UL_W, _UL_H, _UL_RES)

    for frame in gen:
        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n\r\n")


# ------------------------------------------------------------------------------
# Convenience: flag files + helpers (exe-safe)
# ------------------------------------------------------------------------------

def _flag_paths():
    """Return paths for flag files (scanning, multisweep, recdir)."""
    scan_f = getattr(Config, "SCANNING_FLAG_FILE", PROJECT_ROOT / "scanning")
    multi_f = getattr(Config, "MULTISWEEP_FLAG_FILE", PROJECT_ROOT / "multisweep")
    recdir_f = getattr(Config, "RECDIR_FILE", PROJECT_ROOT / "recdir")
    return Path(scan_f), Path(multi_f), Path(recdir_f)


def _set_flag(path: Path, value: str) -> None:
    try:
        path.write_text(value)
    except Exception as e:
        print(f"[flags] failed writing {path}: {e}")


def _newest_data_folder_name() -> str:
    """Return newest timestamp folder name under DATA_DIR (as string)."""
    subdirs = [p for p in Config.DATA_DIR.iterdir() if p.is_dir()]
    if not subdirs:
        return ""
    return sorted(subdirs, key=lambda p: p.name)[-1].name


# ------------------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("main.html")


@app.route("/ultrasound_video_feed")
def ultrasound_video_feed():
    return Response(_ultrasound_mjpeg_stream(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/video_feed")
def video_feed():
    return Response(generate_frames(camera),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/open-itksnap", methods=["POST"])
def handle_open_itksnap():
    success, message = open_itksnap_with_dicom_series()
    return jsonify(success=success, message=message)


@app.route("/move_probe", methods=["POST"])
def move_probe():
    """
    Unified movement endpoint. Accepts JSON:
    { "direction": "<Xplus|Xminus|Yplus|Yminus|Zplus|Zminus|rotateClockwise|rotateCounterclockwise>",
      "step": <float> }
    """
    direction = request.json.get("direction")
    step = float(request.json.get("step", 1))

    mapping = {
        "Xplus": lambda: pssc.deltaMove(step, "X"),
        "Xminus": lambda: pssc.deltaMove(-step, "X"),
        "Yplus": lambda: pssc.deltaMove(step, "Y"),
        "Yminus": lambda: pssc.deltaMove(-step, "Y"),
        "Zplus": lambda: pssc.deltaMove(step, "Z"),
        "Zminus": lambda: pssc.deltaMove(-step, "Z"),
        "rotateClockwise": lambda: pssc.rotate_nozzle_clockwise(step),
        "rotateCounterclockwise": lambda: pssc.rotate_nozzle_counterclockwise(step),
    }

    if direction not in mapping:
        return jsonify(success=False, message="Invalid direction"), 400

    try:
        result = mapping[direction]()
        return jsonify(success=True, message=f"Moved {direction} by {step}", result=str(result))
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


@app.route("/initscanner")
def initscanner():
    """Home axes, move to INIT, set feedrate, restore current E position."""
    try:
        ok, msg = pssc.go2INIT()
        if isinstance(ok, tuple):  # handle any legacy returns
            ok, msg = ok
        return jsonify(success=bool(ok), message=msg or "Initialized")
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


def _start_scan(multi: bool):
    """
    Orchestrate a scan:
      - set flags
      - move to StartScan
      - spawn record.py in background
      - wait a small delay
      - move across ScanPath
      - render scanning page
    """
    scanning_f, multisweep_f, _ = _flag_paths()
    _set_flag(scanning_f, "1")
    _set_flag(multisweep_f, "1" if multi else "0")

    # Move to the start of scan (safe speed)
    pssc.go2StartScan()
    time.sleep(4)

    # Spawn recorder from project root, pass multi flag
    rec_path = (Config.APP_DIR / "scripts" / "record.py").resolve()
    python_exe = getattr(Config, "PYTHON_EXE", sys.executable)
    try:
        sp.Popen([python_exe, str(rec_path), "1" if multi else "0"], cwd=str(PROJECT_ROOT))
    except Exception as e:
        print(f"[scan] failed to spawn recorder: {e}")

    # Let the recorder warm up, then perform the scan path
    delay_s = int(getattr(Config, "DELAY_BEFORE_RECORD_S", 9))
    time.sleep(delay_s)
    pssc.ScanPath()

    newest = _newest_data_folder_name()
    return render_template("scanning.html",
                           link2files=str(Config.DATA_DIR / newest),
                           linkshort=newest)


@app.route("/scanpath")
def scanpath():
    return _start_scan(multi=False)


@app.route("/multipath")
def multipath():
    return _start_scan(multi=True)


@app.route("/overViewImage", methods=["POST"])
def overview_image():
    # Stub endpoint for overview action; actual image is generated by postprocessing
    return jsonify(success=True, message="Overview requested")


# ------------------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # no auto-reloader so DLL/serial init happens once
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
