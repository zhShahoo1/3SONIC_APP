# app/main.py
from __future__ import annotations

import os
import sys
import time
import threading
import subprocess as sp
from pathlib import Path
from typing import Tuple, Iterable

from flask import Flask, Response, jsonify, render_template, request

# Optional desktop shell
try:
    import webview  # pip install pywebview
    _HAS_WEBVIEW = True
except Exception:
    _HAS_WEBVIEW = False
import webbrowser
import platform

# ------------------------------------------------------------------------------
# Import handling: allow running both `python -m app.main` and `python app/main.py`
# ------------------------------------------------------------------------------
if __package__ is None or __package__ == "":
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from app.config import Config
    from app.core import scanner_control as pssc
    from app.core import ultrasound_sdk
    from app.utils.webcam import generate_frames, camera
    from app.integrations.itk_snap import open_itksnap_with_dicom_series
    from app.core.serial_manager import (
        start_serial,
        send_now,
        send_gcode,
        wait_for_motion_complete,
        close_serial,          # <-- added
    )
    # keyboard is optional; keep best-effort start
    try:
        from app.core.keyboard_control import start_keyboard_listener, enable_keyboard
    except Exception:
        start_keyboard_listener = None  # type: ignore
        enable_keyboard = lambda *_a, **_k: None  # type: ignore
else:
    from .config import Config
    from .core import scanner_control as pssc
    from .core import ultrasound_sdk
    from .utils.webcam import generate_frames, camera
    from .integrations.itk_snap import open_itksnap_with_dicom_series
    from .core.serial_manager import (
        start_serial,
        send_now,
        send_gcode,
        wait_for_motion_complete,
        close_serial,          # <-- added
    )
    try:
        from .core.keyboard_control import start_keyboard_listener, enable_keyboard
    except Exception:
        start_keyboard_listener = None  # type: ignore
        enable_keyboard = lambda *_a, **_k: None  # type: ignore

# Always have a reliable root (works in dev and PyInstaller due to Config)
PROJECT_ROOT = Path(getattr(Config, "BASE_DIR", Path(__file__).resolve().parents[1]))

# ------------------------------------------------------------------------------
# Flask app
# ------------------------------------------------------------------------------
app = Flask(
    __name__,
    static_folder=str(Config.STATIC_DIR),
    template_folder=str(Config.TEMPLATES_DIR),
)
app.config.from_object(Config)

# ------------------------------------------------------------------------------
# Background services: serial + keyboard (best-effort)
# ------------------------------------------------------------------------------
try:
    start_serial()
except Exception as e:
    print(f"[Serial] background start failed: {e}")

try:
    enable_keyboard(True)  # no-op if stubbed
    if callable(start_keyboard_listener):
        start_keyboard_listener()
except Exception as e:
    print(f"[Keyboard] listener not started: {e}")

# ------------------------------------------------------------------------------
# Ultrasound live stream bootstrap (resilient if probe/DLL missing)
# ------------------------------------------------------------------------------
def _init_ultrasound() -> Tuple[int, int, Tuple[float, float]]:
    try:
        w, h, res = ultrasound_sdk.initialize_ultrasound()
        print(f"[startup] Ultrasound ready: {w}x{h}, res={res}")
        return w, h, res
    except Exception as e:
        print(f"[startup] Ultrasound init warning: {e}")
        return (1024, 1024, (0.0, 0.0))

_UL_W, _UL_H, _UL_RES = _init_ultrasound()

def _ultrasound_mjpeg_stream() -> Iterable[bytes]:
    try:
        try:
            gen = ultrasound_sdk.generate_image()  # type: ignore
        except TypeError:
            gen = ultrasound_sdk.generate_image(_UL_W, _UL_H, _UL_RES)
        for frame in gen:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n\r\n"
            )
    except Exception as e:
        print(f"[ultrasound stream] stopped: {e}")
        return

# ------------------------------------------------------------------------------
# Flag helpers
# ------------------------------------------------------------------------------
def _flag_paths():
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
    try:
        subdirs = [p for p in Config.DATA_DIR.iterdir() if p.is_dir()]
        if not subdirs:
            return ""
        return sorted(subdirs, key=lambda p: p.name)[-1].name
    except Exception:
        return ""

# ------------------------------------------------------------------------------
# Insert Bath / Position-for-Scan (Config-driven with defaults)
# ------------------------------------------------------------------------------
def _wait_until_axis(
    axis: str,
    target: float,
    tol: float = getattr(Config, "POS_TOL_MM", 0.02),
    timeout_s: float = getattr(Config, "POLL_TIMEOUT_S", 5.0),
) -> bool:
    t0 = time.time()
    poll = getattr(Config, "POLL_INTERVAL_S", 0.10)
    while (time.time() - t0) <= timeout_s:
        pos = pssc.get_position_axis(axis)
        if pos is not None and abs(pos - target) <= tol:
            return True
        time.sleep(poll)
    return False

@app.route("/api/lower-plate", methods=["POST"])
def api_lower_plate():
    try:
        target_z = float(getattr(Config, "TARGET_Z_MM", 100.0))
        z_feed = int(getattr(Config, "Z_FEED_MM_PER_MIN", 1500))

        send_now("G90")
        send_now(f"G1 Z{target_z:.3f} F{z_feed}")
        wait_for_motion_complete(10.0)

        if not _wait_until_axis("Z", target_z):
            return jsonify(
                success=False,
                message=f"Timeout: Z did not reach {target_z} mm",
                status="Error",
            ), 500

        return jsonify(
            success=True,
            message="Plate lowered to insert bath",
            status="Place specimen and click again",
        )
    except Exception as e:
        return jsonify(success=False, message=str(e), status="Error"), 500

@app.route("/api/position-for-scan", methods=["POST"])
def api_position_for_scan():
    try:
        pose = getattr(Config, "SCAN_POSE", {"X": 53.5, "Y": 53.5, "Z": 10.0})
        xyz_feed = int(getattr(Config, "XYZ_FEED_MM_PER_MIN", 2000))

        send_now("G90")
        send_now(
            f"G1 X{float(pose['X']):.3f} "
            f"Y{float(pose['Y']):.3f} "
            f"Z{float(pose['Z']):.3f} "
            f"F{xyz_feed}"
        )
        wait_for_motion_complete(15.0)

        ok_x = _wait_until_axis("X", float(pose["X"]))
        ok_y = _wait_until_axis("Y", float(pose["Y"]))
        ok_z = _wait_until_axis("Z", float(pose["Z"]))

        if not (ok_x and ok_y and ok_z):
            return jsonify(
                success=False,
                message="Timeout: scanner did not reach scan pose",
                status="Error",
            ), 500

        return jsonify(success=True, message="Scanner positioned for scan", status="Ready")
    except Exception as e:
        return jsonify(success=False, message=str(e), status="Error"), 500

# ------------------------------------------------------------------------------
# Routes (UI + streams + actions)
# ------------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("main.html")

@app.route("/ultrasound_video_feed")
def ultrasound_video_feed():
    return Response(
        _ultrasound_mjpeg_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )

@app.route("/video_feed")
def video_feed():
    return Response(
        generate_frames(camera),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )

@app.route("/open-itksnap", methods=["POST"])
def handle_open_itksnap():
    success, message = open_itksnap_with_dicom_series()
    return jsonify(success=success, message=message)

@app.route("/move_probe", methods=["POST"])
def move_probe():
    data = request.get_json(silent=True) or {}
    direction = data.get("direction")
    step = float(data.get("step", 1))

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
    try:
        ok, msg = pssc.go2INIT()
        return jsonify(success=bool(ok), message=msg or "Initialized"), (200 if ok else 500)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print("[/initscanner] Exception:\n", tb)
        return jsonify(success=False, message=f"INIT crashed: {e}"), 500

def _start_scan(multi: bool):
    scanning_f, multisweep_f, _ = _flag_paths()
    _set_flag(scanning_f, "1")
    _set_flag(multisweep_f, "1" if multi else "0")

    pssc.go2StartScan()
    time.sleep(4)

    rec_path = (Config.APP_DIR / "scripts" / "record.py").resolve()
    python_exe = getattr(Config, "PYTHON_EXE", sys.executable)
    try:
        sp.Popen([python_exe, str(rec_path), "1" if multi else "0"], cwd=str(PROJECT_ROOT))
    except Exception as e:
        print(f"[scan] failed to spawn recorder: {e}")

    delay_s = int(getattr(Config, "DELAY_BEFORE_RECORD_S", 9))
    time.sleep(delay_s)
    pssc.ScanPath()

    newest = _newest_data_folder_name()
    return render_template(
        "scanning.html",
        link2files=str(Config.DATA_DIR / newest),
        linkshort=newest,
    )

@app.route("/scanpath")
def scanpath():
    return _start_scan(multi=False)

@app.route("/multipath")
def multipath():
    return _start_scan(multi=True)

@app.route("/overViewImage", methods=["POST"])
def overview_image():
    return jsonify(success=True, message="Overview requested")

# ------------------------------------------------------------------------------
# Graceful Exit API
# ------------------------------------------------------------------------------
_APP_SHUTTING_DOWN = False
_WEBVIEW_WINDOW = None  # type: ignore

def _graceful_shutdown_async():
    """Do cleanup then terminate the process."""
    global _APP_SHUTTING_DOWN
    if _APP_SHUTTING_DOWN:
        return
    _APP_SHUTTING_DOWN = True

    try:
        try:
            enable_keyboard(False)  # no-op if stub
        except Exception:
            pass

        try:
            close_serial()
        except Exception as e:
            print("[Shutdown] close_serial error:", e)

        if _HAS_WEBVIEW:
            try:
                # Prefer the thread-safe helper if available
                webview.destroy_window()
            except Exception:
                try:
                    if _WEBVIEW_WINDOW is not None:
                        _WEBVIEW_WINDOW.destroy()
                except Exception as ee:
                    print("[Shutdown] webview destroy error:", ee)
        # Small delay so the HTTP 200 can flush
        time.sleep(0.3)
    finally:
        os._exit(0)

@app.route("/api/exit", methods=["POST"])
def api_exit():
    """
    Frontend should show a confirm() first, then POST here.
    This returns immediately while cleanup runs in a thread.
    """
    threading.Thread(target=_graceful_shutdown_async, daemon=True).start()
    return jsonify(success=True, message="Shutting down...")

# Back-compat alias (GET /shutdown)
@app.route("/shutdown")
def shutdown_alias():
    threading.Thread(target=_graceful_shutdown_async, daemon=True).start()
    return "Shutting down..."

# ------------------------------------------------------------------------------
# Desktop launcher (pywebview) + fallback to browser
# ------------------------------------------------------------------------------
_UI_TITLE = "3SONIC 3D Ultrasound app"  # Keep consistent with Win32 dark-titlebar tweak

def _launch_desktop():
    """
    Launch a native desktop window that hosts the Flask UI.
    """
    global _WEBVIEW_WINDOW

    url = "http://127.0.0.1:5000"
    if _HAS_WEBVIEW:
        _WEBVIEW_WINDOW = webview.create_window(
            title=_UI_TITLE,
            url=url,
            width=380,
            height=680,
            resizable=True,
            min_size=(360, 620),
        )

        # Optional: small Windows dark-titlebar tweak
        if platform.system() == "Windows":
            try:
                import ctypes
                time.sleep(0.4)
                hwnd = ctypes.windll.user32.FindWindowW(None, _UI_TITLE)
                if hwnd:
                    enabled = ctypes.c_int(1)
                    for attr in (19, 20):  # DWMWA_USE_IMMERSIVE_DARK_MODE (varies by Windows)
                        ctypes.windll.dwmapi.DwmSetWindowAttribute(
                            ctypes.c_void_p(hwnd),
                            ctypes.c_uint(attr),
                            ctypes.byref(enabled),
                            ctypes.sizeof(enabled),
                        )
            except Exception:
                pass

        webview.start()
    else:
        print("[Desktop] pywebview not installed. Opening browser instead.")
        webbrowser.open(url)

# ------------------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    # Start Flask in a background thread that pywebview can attach to
    from threading import Thread

    def _run_flask():
        app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)

    Thread(target=_run_flask, daemon=True).start()
    _launch_desktop()
