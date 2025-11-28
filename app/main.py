
#There is two thing to be fixed: 1. the keyboard active during active screen. 2. apply M400 for many progrestions
# app/main.py
from __future__ import annotations

import os
import sys
import time
import json
import queue
import threading
import subprocess as sp
from pathlib import Path
from typing import Tuple, Iterable, Optional

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
        close_serial,
    )
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
        close_serial,
    )
    try:
        from .core.keyboard_control import start_keyboard_listener, enable_keyboard
    except Exception:
        start_keyboard_listener = None  # type: ignore
        enable_keyboard = lambda *_a, **_k: None  # type: ignore

# Always have a reliable root (works in dev and PyInstaller due to Config)
PROJECT_ROOT = Path(getattr(Config, "BASE_DIR", Path(__file__).resolve().parents[1]))

# Keep track of child processes we spawn (recorders / mergers) to kill on exit
_CHILD_PROCS: list[sp.Popen] = []

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
    """Initialize ultrasound once at startup; not fatal if missing."""
    try:
        w, h, res = ultrasound_sdk.initialize_ultrasound()
        print(f"[startup] Ultrasound ready: {w}x{h}, res={res}")
        return w, h, res
    except Exception as e:
        print(f"[startup] Ultrasound init warning: {e}")
        # Provide a default so the rest of the app still runs
        return (1024, 1024, (0.0, 0.0))

_UL_W, _UL_H, _UL_RES = _init_ultrasound()

def _ultrasound_mjpeg_stream() -> Iterable[bytes]:
    """
    Continuous MJPEG generator.
    If the ultrasound generator errors (e.g., cable unplugged), we back off a bit
    and keep retrying forever so the front-end 'error' handler can reload.
    """
    while True:
        try:
            for frame in ultrasound_sdk.generate_image():
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n\r\n"
                )
        except Exception as e:
            print(f"[ultrasound stream] error (will retry): {e}")
            time.sleep(0.5)  # backoff before retry

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

@app.route("/api/us-restart", methods=["POST"])
def api_us_restart():
    """
    Attempt to recover ultrasound after cable reconnection:
      - close existing DLL/session
      - small delay to let OS re-enumerate
      - re-init the ultrasound stack
    """
    try:
        try: ultrasound_sdk.freeze()
        except Exception: pass
        try: ultrasound_sdk.stop()
        except Exception: pass
        try: ultrasound_sdk.close()
        except Exception: pass

        time.sleep(0.3)
        ultrasound_sdk.initialize_ultrasound()
        return jsonify(success=True, message="Ultrasound restarted")
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500

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

# ------------------------------------------------------------------------------
# Keyboard-friendly jog handling
# - Frontend enqueues /move_probe requests at a steady pace while key is held
# - Here we push them into a background worker that calls pssc.jog_once()
# ------------------------------------------------------------------------------
_JOG_Q: "queue.Queue[tuple[str, float]]" = queue.Queue(maxsize=64)

# Lock to serialize mode changes (G90/G91) so relative-mode loops don't race
# with absolute E-axis moves. Acquired by any code that issues G91/G90 or
# calls rotate helpers which rely on absolute E moves.
_UI_MODE_LOCK = threading.Lock()

def _jog_worker():
    while True:
        direction, step = _JOG_Q.get()
        try:
            # Prevent races between relative-mode jogs (which set G91) and
            # absolute E-axis rotates. Acquire mode lock for rotate operations
            # and for any jogs that will switch into relative mode briefly.
            if isinstance(direction, str) and direction.startswith("rotate"):
                with _UI_MODE_LOCK:
                    pssc.jog_once(direction, step)
            else:
                # For non-rotate jogs, also acquire the lock while we set G91
                # and perform the rapid relative moves to avoid interleaving
                # an absolute E move concurrently.
                with _UI_MODE_LOCK:
                    pssc.jog_once(direction, step)
        except Exception as e:
            print("[jog worker] error:", e)
        finally:
            _JOG_Q.task_done()

threading.Thread(target=_jog_worker, daemon=True).start()


# ------------------------------------------------------------------------------
# UI continuous movement (for button hold behavior)
# ------------------------------------------------------------------------------
_UI_MOVE_THREADS: dict[tuple[str, int], tuple[threading.Thread, threading.Event]] = {}
_UI_MOVE_LOCK = threading.Lock()


def _map_ui_action_to_direction(action: str) -> str:
    """Map frontend `data-action` to internal direction names used by jog_once.

    Frontend uses kebab-case (e.g. `x-plus`, `rot-cw`). Convert to the
    canonical direction names used elsewhere in the app.
    """
    mapping = {
        "x-plus": "Xplus",
        "x-minus": "Xminus",
        "y-plus": "Yplus",
        "y-minus": "Yminus",
        "z-plus": "Zplus",
        "z-minus": "Zminus",
        "rot-cw": "rotateClockwise",
        "rot-ccw": "rotateCounterclockwise",
    }
    return mapping.get(action, action)


def _start_ui_continuous_move(action: str, feed_mm_per_min: float = 300.0, tick_s: float = 0.02) -> bool:
    """Start continuous motion driven by UI hold events.

    This launches a background thread that issues small relative moves at a
    steady cadence. For XYZ we use `send_now` with `G91`/`G1` small steps for
    smoother motion. For rotation we send small `E` moves similarly.
    """
    direction = _map_ui_action_to_direction(action)

    allowed = {
        "Xplus", "Xminus", "Yplus", "Yminus", "Zplus", "Zminus",
        "rotateClockwise", "rotateCounterclockwise",
    }
    if direction not in allowed:
        raise ValueError("Invalid direction")

    # Clamp feed
    try:
        max_feed = float(getattr(Config, "UI_MAX_FEED_MM_PER_MIN", 5000.0))
    except Exception:
        max_feed = 5000.0
    feed_mm_per_min = max(1.0, min(float(feed_mm_per_min), max_feed))

    # Key to identify running thread: (direction, step-granularity)
    if direction.startswith("rotate"):
        step_key = int(round(float(getattr(Config, "ELEV_RESOLUTION_MM", 0.06)) * 1000))
        key = (direction, step_key)
    else:
        axis = direction[0].upper()
        sign = 1 if direction.endswith("plus") else -1
        key = (axis, sign)

    with _UI_MOVE_LOCK:
        if key in _UI_MOVE_THREADS:
            return False
        stop_flag = threading.Event()

        def _worker():
            try:
                if not direction.startswith("rotate"):
                    axis = direction[0].upper()
                    sign = 1 if direction.endswith("plus") else -1

                    v_mm_s = float(feed_mm_per_min) / 60.0
                    step = max(0.0005, v_mm_s * float(tick_s))

                    # Serialize against absolute E-axis moves so the firmware
                    # mode (G90/G91) cannot be flipped concurrently.
                    with _UI_MODE_LOCK:
                        # Attempt initial setup; if send_now indicates no connection,
                        # abort the worker to avoid spinning with no effect.
                        ok_setup = True
                        if not send_now("G91"):
                            ok_setup = False
                        if not send_now(f"G1 F{int(float(feed_mm_per_min))}"):
                            ok_setup = False

                        if not ok_setup:
                            print("[_ui_move] aborted: serial not available during setup")
                            return

                        while not stop_flag.is_set():
                            ok = send_now(f"G1 {axis}{sign * step:.4f}")
                            if not ok:
                                print("[_ui_move] send_now failed during continuous move; aborting")
                                break
                            time.sleep(float(tick_s))

                        try:
                            send_now("G90")
                        except Exception:
                            pass

                else:
                    sign = 1 if direction == "rotateClockwise" else -1
                    try:
                        e_feed = float(feed_mm_per_min)
                    except Exception:
                        e_feed = float(getattr(Config, "UI_DEFAULT_FEED_MM_PER_MIN", 300.0))

                    v_mm_s = float(e_feed) / 60.0
                    e_step = max(0.0005, v_mm_s * float(tick_s))

                    try:
                        # Allow cold extrusion if configured (mirrors scanner_control behavior)
                        try:
                            if bool(getattr(Config, "E_AXIS_ALLOW_COLD_EXTRUSION", True)):
                                send_now("M302 P1")
                        except Exception:
                            pass

                        # Do not switch to relative mode for rotations; rotate helpers
                        # perform absolute E moves and ensure units/absolute themselves.
                        # We still allow cold extrusion via M302 above.
                    except Exception:
                        pass

                    print(f"[_ui_move] rotate worker started: dir={'CW' if sign==1 else 'CCW'}, feed={e_feed}, tick={tick_s}")

                    start_time = time.time()
                    max_run = float(getattr(Config, "UI_ROTATION_MAX_S", 10.0))
                    try:
                        e_step_precise = float(getattr(Config, "E_AXIS_DEFAULT_STEP", 0.1))
                    except Exception:
                        e_step_precise = 0.1

                    # Serialize with other UI mode-changing operations so absolute
                    # E-axis moves can't be misinterpreted while another worker
                    # temporarily set relative mode.
                    # Mobile-style rotation: use small relative E moves (G91 + G1 E..)
                    # This tends to feel smoother for continuous user presses. We
                    # still acquire _UI_MODE_LOCK so no absolute E moves interleave.
                    with _UI_MODE_LOCK:
                        try:
                            if bool(getattr(Config, "E_AXIS_ALLOW_COLD_EXTRUSION", True)):
                                send_now("M302 P1")
                        except Exception:
                            pass

                        # set relative mode and planner feed (best-effort)
                        if not send_now("G91"):
                            print("[_ui_move] rotate aborted: serial unavailable for G91")
                            return
                        send_now(f"G1 F{int(e_feed)}")

                        # compute step using e_feed and requested tick for smoothness
                        try:
                            v_mm_s = float(e_feed) / 60.0
                            e_step = max(0.0005, v_mm_s * float(tick_s))
                        except Exception:
                            e_step = max(0.0005, 0.1 * float(tick_s))

                        while not stop_flag.is_set() and (time.time() - start_time) < max_run:
                            ok = send_now(f"G1 E{sign * e_step:.4f}")
                            if not ok:
                                print("[_ui_move] rotate send_now failed; aborting")
                                break
                            time.sleep(max(float(tick_s), 0.01))

                        try:
                            send_now("G90")
                        except Exception:
                            pass

                    # rotate helpers persist E internally; ensure absolute mode
                    try:
                        send_now("G90")
                    except Exception:
                        pass

            finally:
                with _UI_MOVE_LOCK:
                    _UI_MOVE_THREADS.pop(key, None)

        t = threading.Thread(target=_worker, daemon=True)
        _UI_MOVE_THREADS[key] = (t, stop_flag)
        t.start()
        return True


def _stop_ui_continuous_move(action: str | None = None):
    """Stop previously started continuous move(s)."""
    with _UI_MOVE_LOCK:
        if action is None:
            items = list(_UI_MOVE_THREADS.items())
        else:
            direction = _map_ui_action_to_direction(action)
            if direction.startswith("rotate"):
                items = [(k, v) for k, v in _UI_MOVE_THREADS.items() if k[0] in ("rotateClockwise", "rotateCounterclockwise")]
            else:
                axis = direction[0].upper()
                sign = 1 if direction.endswith("plus") else -1
                items = [((axis, sign), _UI_MOVE_THREADS.get((axis, sign)))] if (axis, sign) in _UI_MOVE_THREADS else []

        for k, pair in items:
            if not pair:
                continue
            _, stop_flag = pair
            try:
                stop_flag.set()
            except Exception:
                pass
    # Additionally, ensure firmware is not left in relative mode for safety.
    try:
        send_now("G90")
    except Exception:
        pass


@app.route("/move_probe", methods=["POST"])
def move_probe():
    data = request.get_json(silent=True) or {}
    direction = data.get("direction")
    step = float(data.get("step", 1))

    allowed = {
        "Xplus","Xminus","Yplus","Yminus","Zplus","Zminus",
        "rotateClockwise","rotateCounterclockwise"
    }
    if direction not in allowed:
        return jsonify(success=False, message="Invalid direction"), 400
    # Short debounce on server: ignore repeated identical rotate commands
    # that arrive within a very short window (likely duplicate UI events).
    try:
        if direction in ("rotateClockwise", "rotateCounterclockwise"):
            _last = globals().get("_LAST_ROTATE", None)
            now = time.time()
            if _last and _last.get("direction") == direction and (now - _last.get("time", 0)) < 0.25:
                # Treat as duplicate — acknowledge but do not queue.
                return jsonify(success=True, message="duplicate-ignored"), 200
            globals()['_LAST_ROTATE'] = {"direction": direction, "time": now}
    except Exception:
        pass
    # If a continuous rotate is active, reject single-step rotate requests
    if direction in ("rotateClockwise", "rotateCounterclockwise"):
        with _UI_MOVE_LOCK:
            for k in _UI_MOVE_THREADS.keys():
                if isinstance(k, tuple) and k[0] in ("rotateClockwise", "rotateCounterclockwise"):
                    return jsonify(success=False, message="Rotation already active"), 409

    try:
        _JOG_Q.put_nowait((direction, step))
        return jsonify(success=True, message=f"queued {direction} {step}")
    except queue.Full:
        return jsonify(success=False, message="Jog queue full"), 429



@app.route("/move_probe/start", methods=["POST"])
def move_probe_start():
    """Start continuous motion for UI hold. Body JSON: { action: 'x-plus', speed: 300.0, tick_s: 0.02 }
    """
    data = request.get_json(silent=True) or {}
    action = data.get("action") or data.get("direction")
    if not action:
        return jsonify(success=False, message="Missing action"), 400

    try:
        default_speed = float(getattr(Config, "UI_DEFAULT_FEED_MM_PER_MIN", 300.0))
    except Exception:
        default_speed = 300.0

    try:
        speed = float(data.get("speed", default_speed))
    except Exception:
        speed = default_speed

    try:
        max_speed = float(getattr(Config, "UI_MAX_FEED_MM_PER_MIN", 5000.0))
    except Exception:
        max_speed = 5000.0
    speed = max(1.0, min(speed, max_speed))

    try:
        tick = float(data.get("tick_s", 0.02))
    except Exception:
        tick = float(getattr(Config, "UI_DEFAULT_TICK_S", 0.03))
    # Enforce a sensible server-side minimum tick to avoid serial saturation
    try:
        server_min_tick = float(getattr(Config, "UI_DEFAULT_TICK_S", 0.03))
    except Exception:
        server_min_tick = 0.03
    tick = max(server_min_tick, min(tick, 0.5))

    try:
        started = _start_ui_continuous_move(action, feed_mm_per_min=speed, tick_s=tick)
        if not started:
            return jsonify(success=False, message="already running"), 409
        return jsonify(success=True, message="started")
    except ValueError:
        return jsonify(success=False, message="Invalid action"), 400
    except Exception as e:
        print(f"[UI-move] start error: {e}")
        return jsonify(success=False, message=str(e)), 500


@app.route("/move_probe/stop", methods=["POST"])
def move_probe_stop():
    """Stop continuous motion. Body: { action: 'x-plus' } or empty to stop all."""
    data = request.get_json(silent=True) or {}
    action = data.get("action", None)
    try:
        _stop_ui_continuous_move(action)
        return jsonify(success=True, message="stopped")
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


@app.route("/move_probe/status", methods=["GET"])
def move_probe_status():
    """Return currently active UI continuous moves.

    JSON: { active: [ { "key": "X,1" , "direction": "Xplus" }, ... ] }
    """
    out = []
    with _UI_MOVE_LOCK:
        for k in list(_UI_MOVE_THREADS.keys()):
            # represent key for debugging: for rotate keys it's (direction, step_key)
            if isinstance(k, tuple) and len(k) == 2:
                out.append({"key": f"{k[0]}:{k[1]}", "direction": k[0]})
            else:
                out.append({"key": str(k), "direction": str(k[0] if isinstance(k, (list, tuple)) and k else k)})
    return jsonify(success=True, active=out)

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

# ------------------------------------------------------------------------------
# Scan plan helpers
# ------------------------------------------------------------------------------
def _normalize_x_range(start: float, end: float) -> tuple[float, float]:
    xmax = float(getattr(Config, "X_MAX", 118.0))
    s = max(0.0, min(xmax, float(start)))
    e = max(0.0, min(xmax, float(end)))
    if e <= s:
        e = min(xmax, s + 1.0)  # ensure non-zero forward span
    return (s, e)

def _parse_scan_query() -> tuple[float, float, str]:
    """
    Parse /scanpath?start=&end=&mode=…
    Accepts start/end or x0/x1; mode=long|short|custom can be used to fill defaults.
    Returns clamped (x0, x1, mode).
    """
    xmax = float(getattr(Config, "X_MAX", 118.0))
    mode = (request.args.get("mode") or "").strip().lower()

    def _f(name: str) -> Optional[float]:
        v = request.args.get(name, default=None, type=float)
        if v is None:
            return None
        return max(0.0, min(xmax, v))

    s = _f("start")
    e = _f("end")
    if s is None: s = _f("x0")
    if e is None: e = _f("x1")

    if s is None or e is None:
        if mode == "short":
            s, e = 15.0, min(90.0, xmax)
        elif mode == "long" or mode == "":
            s, e = 0.0, xmax
        else:
            s, e = 0.0, xmax
            mode = "long"

    x0, x1 = _normalize_x_range(s, e)

    if mode not in {"long", "short", "custom"}:
        if abs(x0 - 0.0) < 1e-6 and abs(x1 - xmax) < 1e-6:
            mode = "long"
        elif abs(x0 - 15.0) < 1e-6 and abs(x1 - min(90.0, xmax)) < 1e-6:
            mode = "short"
        else:
            mode = "custom"
    return (x0, x1, mode)

# ------------------------------------------------------------------------------
# Single-scan orchestration
# ------------------------------------------------------------------------------
def _persist_scanplan(x0: float, x1: float, mode: str) -> None:
    plan = {"x0": x0, "x1": x1, "mode": mode}
    try:
        Config.SCANPLAN_FILE.write_text(json.dumps(plan), encoding="utf-8")
        print(f"[/scan] wrote scanplan.json: {plan}")
    except Exception as e:
        print(f"[scan] couldn't write scanplan.json: {e}")

def _launch_recorder(multi: bool, x0: float, x1: float, pos_str: str) -> None:
    """Spawn record.py and remember the process so we can kill it on exit."""
    rec_path = (Config.APP_DIR / "scripts" / "record.py").resolve()
    python_exe = getattr(Config, "PYTHON_EXE", sys.executable)

    env = os.environ.copy()
    env["REC_POSITION_STR"] = pos_str
    # Set both naming styles for maximum compatibility with record.py
    env["SCAN_X0"] = str(x0)
    env["SCAN_X1"] = str(x1)
    env["SCAN_START_X"] = env["SCAN_X0"]
    env["SCAN_END_X"] = env["SCAN_X1"]
    env["SCAN_MODE"] = "custom"

    try:
        proc = sp.Popen([python_exe, str(rec_path), "1" if multi else "0"],
                        cwd=str(PROJECT_ROOT), env=env)
        _CHILD_PROCS.append(proc)
    except Exception as e:
        print(f"[scan] failed to spawn recorder: {e}")

def _start_scan(multi: bool, start_x: float | None = None, end_x: float | None = None):
    scanning_f, multisweep_f, _ = _flag_paths()
    _set_flag(scanning_f, "1")
    _set_flag(multisweep_f, "1" if multi else "0")

    xmax = float(getattr(Config, "X_MAX", 118.0))
    if start_x is None or end_x is None:
        x0, x1, mode = 0.0, xmax, "long"
    else:
        x0, x1 = _normalize_x_range(start_x, end_x)
        mode = "custom" if not (abs(x0 - 0.0) < 1e-6 and abs(x1 - xmax) < 1e-6) else "long"

    # Persist the operator selection for recorder (file + env)
    _persist_scanplan(x0, x1, mode)

    # Move to chosen start X
    try:
        pssc.go2StartScan(x0)
    except Exception as e:
        print(f"[scan] go2StartScan failed: {e}")
    time.sleep(4)

    # Capture current position (best-effort)
    pos_str = ""
    try:
        pos_val = pssc.get_position()
        if isinstance(pos_val, (list, tuple)) and pos_val:
            pos_str = str(pos_val[0]).split("\n")[0]
        elif isinstance(pos_val, str):
            pos_str = pos_val.split("\n")[0]
        if not pos_str:
            x = pssc.get_position_axis("X"); y = pssc.get_position_axis("Y"); z = pssc.get_position_axis("Z")
            if None not in (x, y, z):
                pos_str = f"X{float(x):.3f} Y{float(y):.3f} Z{float(z):.3f}"
    except Exception:
        pass

    _launch_recorder(multi, x0, x1, pos_str)

    # Let recorder spin up, then execute the motion
    delay_s = int(getattr(Config, "DELAY_BEFORE_RECORD_S", 9))
    time.sleep(delay_s)
    try:
        pssc.ScanPath(x0, x1)
    except Exception as e:
        print(f"[scan] ScanPath failed: {e}")

    newest = _newest_data_folder_name()
    return render_template("scanning.html", link2files=str(Config.DATA_DIR / newest), linkshort=newest)

@app.route("/scanpath", methods=["GET"])
def scanpath():
    x0, x1, _mode = _parse_scan_query()
    return _start_scan(multi=False, start_x=x0, end_x=x1)

# ------------------------------------------------------------------------------
# MultiSweep orchestration (now uses the SAME scan range logic as single sweep)
# ------------------------------------------------------------------------------
def _is_scanning() -> bool:
    try:
        return (Config.SCANNING_FLAG_FILE.read_text().strip() == "1")
    except Exception:
        return False

def _wait_until_not_scanning(timeout_s: float = 600.0) -> bool:
    t0 = time.time()
    while time.time() - t0 <= timeout_s:
        if not _is_scanning():
            return True
        time.sleep(0.5)
    return False

def _latest_two_scan_dirs():
    """Return the two newest scan folders (older_first, newer_second)."""
    try:
        subdirs = [p for p in Config.DATA_DIR.iterdir() if p.is_dir()]
        if len(subdirs) < 2:
            return (None, None)
        two = sorted(subdirs, key=lambda p: p.name)[-2:]
        return (two[0], two[1])  # (older, newer)
    except Exception:
        return (None, None)

def _run_multisweep_sequence(start_x: float | None = None, end_x: float | None = None) -> tuple[bool, str]:
    try:
        # Sweep 1: Y offset -
        pssc.deltaMove(-10.0, "Y")
        time.sleep(4.0)
        _start_scan(multi=True, start_x=start_x, end_x=end_x)
        if not _wait_until_not_scanning():
            return False, "Timeout waiting for sweep #1 to finish"

        # Sweep 2: Y offset +
        pssc.deltaMove(+20.0, "Y")
        time.sleep(2.0)
        _start_scan(multi=True, start_x=start_x, end_x=end_x)
        if not _wait_until_not_scanning():
            return False, "Timeout waiting for sweep #2 to finish"

        older, newer = _latest_two_scan_dirs()
        if older is None or newer is None:
            return False, "Not enough scan folders for MultiSweep merge"

        multi_path = (Config.APP_DIR / "scripts" / "multisweep.py").resolve()
        python_exe = getattr(Config, "PYTHON_EXE", sys.executable)
        sp.Popen([python_exe, str(multi_path)], cwd=str(PROJECT_ROOT))

        time.sleep(2.0)
        return True, older.name

    except Exception as e:
        return False, f"MultiSweep error: {e}"


@app.route("/multipath", methods=["GET", "POST"])
def multipath():
    xmax = float(getattr(Config, "X_MAX", 118.0))
    data = request.get_json(silent=True) or {}
    mode = (request.args.get("mode") or data.get("mode") or "").lower()

    def _get(name):
        v = request.args.get(name, type=float)
        if v is None:
            v = data.get(name, None)
        if v is None:
            return None
        return max(0.0, min(xmax, float(v)))

    # accept both naming styles
    s = _get("start") or _get("x0")
    e = _get("end")   or _get("x1")

    # preset by mode if range not given
    if (s is None or e is None) and mode in {"long", "short"}:
        if mode == "short":
            s, e = 15.0, min(90.0, xmax)
        else:  # long
            s, e = 0.0, xmax

    if s is not None and e is not None and e <= s:
        return jsonify(success=False, message="end must be > start"), 400

    ok, payload = _run_multisweep_sequence(start_x=s, end_x=e)
    if not ok:
        return jsonify(success=False, message=str(payload)), 500

    folder = payload
    return render_template(
        "scanning.html",
        link2files=str(Config.DATA_DIR / folder),
        linkshort=folder,
    )


# Legacy hook (button now opens a picker, but keep this endpoint harmless)
@app.route("/overViewImage", methods=["POST"])
def overview_image():
    return jsonify(success=True, message="Overview requested")

# ------------------------------------------------------------------------------
# Overview PNG picker/list + open (for the Overview Image button)
# ------------------------------------------------------------------------------
def _open_native(path: Path) -> None:
    """Open file with the OS default image viewer."""
    if platform.system() == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif platform.system() == "Darwin":
        sp.Popen(["open", str(path)])
    else:
        sp.Popen(["xdg-open", str(path)])

@app.route("/api/overview/list")
def api_overview_list():
    """
    Return a list of scans that contain Example_slices.png
    JSON: { success: bool, items: [ {folder, png_url, created}, ... ] }
    """
    try:
        n = int(request.args.get("limit", "50"))
    except ValueError:
        n = 50

    data_dir = Config.DATA_DIR
    items = {}
    try:
        out = []
        for p in sorted([d for d in data_dir.iterdir() if d.is_dir()],
                        key=lambda x: x.name, reverse=True):
            png = p / "Example_slices.png"
            if png.exists():
                out.append({
                    "folder": p.name,
                    "png_url": f"/static/data/{p.name}/Example_slices.png",
                    "created": png.stat().st_mtime
                })
            if len(out) >= n:
                break
        items = out
    except Exception as e:
        return jsonify(success=False, message=str(e), items=[]), 500

    return jsonify(success=True, items=items)

@app.route("/api/overview/open", methods=["POST"])
def api_overview_open():
    """
    Open Example_slices.png for a given scan folder in the OS viewer.
    Body: { "folder": "<timestamp-folder>" }
    """
    data = request.get_json(silent=True) or {}
    folder = (data.get("folder") or "").strip()
    if not folder:
        return jsonify(success=False, message="Missing 'folder'."), 400

    png_path = (Config.DATA_DIR / folder / "Example_slices.png").resolve()
    if not png_path.exists():
        return jsonify(success=False, message="Overview PNG not found."), 404

    try:
        _open_native(png_path)
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500

# ------------------------------------------------------------------------------
# Health
# ------------------------------------------------------------------------------
@app.route("/health")
def health():
    return jsonify(ok=True)

# ------------------------------------------------------------------------------
# Graceful Exit API
# ------------------------------------------------------------------------------
_APP_SHUTTING_DOWN = False
_WEBVIEW_WINDOW = None  # type: ignore

def _terminate_children():
    """Best-effort termination of spawned child processes."""
    for proc in list(_CHILD_PROCS):
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
    # give them a moment
    t0 = time.time()
    while time.time() - t0 < 1.5:
        alive = [p for p in _CHILD_PROCS if p.poll() is None]
        if not alive:
            break
        time.sleep(0.1)
    # kill any stubborn ones
    for proc in list(_CHILD_PROCS):
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

def _graceful_shutdown_async():
    """Best-effort cleanup, then terminate the process."""
    global _APP_SHUTTING_DOWN
    if _APP_SHUTTING_DOWN:
        return
    _APP_SHUTTING_DOWN = True
    print("[Shutdown] initiating…")

    try:
        # 0) Tell recorders to stop ASAP
        try:
            Config.SCANNING_FLAG_FILE.write_text("0")
        except Exception:
            pass

        # 1) Disable keyboard control (ignore if not available)
        try:
            enable_keyboard(False)  # no-op if stubbed
            try:
                import keyboard as _kbd  # if the module is present
                _kbd.unhook_all()
            except Exception:
                pass
        except Exception as e:
            print("[Shutdown] keyboard disable error:", e)

        # 2) Ultrasound: freeze/stop/close (ignore errors)
        try:
            try: ultrasound_sdk.freeze()
            except Exception: pass
            try: ultrasound_sdk.stop()
            except Exception: pass
            try: ultrasound_sdk.close()
            except Exception: pass
        except Exception as e:
            print("[Shutdown] ultrasound close error:", e)

        # 3) Webcam: release camera if present
        try:
            if hasattr(camera, "release"):
                camera.release()
        except Exception as e:
            print("[Shutdown] webcam release error:", e)

        # 4) Serial
        try:
            close_serial()
        except Exception as e:
            print("[Shutdown] close_serial error:", e)

        # 5) Terminate child worker processes we spawned
        _terminate_children()

        # 6) Clear simple flag files (best effort)
        try:
            for f in (Config.SCANNING_FLAG_FILE, Config.MULTISWEEP_FLAG_FILE):
                try:
                    f.write_text("0")
                except Exception:
                    pass
        except Exception:
            pass

        # 7) Close the desktop window if we’re in pywebview
        if _HAS_WEBVIEW:
            try:
                webview.destroy_window()  # thread-safe in recent pywebview
            except Exception as e1:
                print("[Shutdown] destroy_window error:", e1)
                try:
                    if _WEBVIEW_WINDOW is not None:
                        _WEBVIEW_WINDOW.destroy()
                except Exception as e2:
                    print("[Shutdown] webview window destroy error:", e2)

        # Let the HTTP response flush before exiting hard
        time.sleep(0.3)

    finally:
        # Use hard exit to avoid hanging threads (keyboard hooks, webview loop, etc.)
        os._exit(0)

@app.route("/api/exit", methods=["POST"])
def api_exit():
    """
    Frontend shows a confirm() first, then POSTs here.
    We return immediately while cleanup runs in a thread.
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
