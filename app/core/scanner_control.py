# app/core/scanner_control.py

from __future__ import annotations

import time
import math
from pathlib import Path
from typing import List, Optional, Tuple, Dict

from app.config import Config

# Shared lock for UI mode changes (G91/G90) to serialize relative vs absolute moves
import threading
UI_MODE_LOCK = threading.Lock()

# --- serial plumbing ---------------------------------------------------------
# We import everything best-effort and stay resilient if some helpers
# are not present on a given branch.
try:
    from app.core.serial_manager import (
        start_serial,               # noqa: F401 (not used directly here)
        send_gcode,
        send_now,
        wait_for_motion_complete,
    )
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"serial_manager not available: {e}")

# Optional helpers (present in many variants of your serial_manager)
try:
    from app.core.serial_manager import connect_serial, connected_event  # type: ignore
except Exception:  # pragma: no cover
    connect_serial = lambda *a, **k: None  # type: ignore
    connected_event = None  # type: ignore


# =============================================================================
# E-axis persistence (absolute 'E' position across processes)
# =============================================================================
_E_AXIS_POS_FILE: Path = (Config.DATA_DIR / "e_axis_position.txt").resolve()


def _read_e_axis_position(default: float = 0.0) -> float:
    try:
        if _E_AXIS_POS_FILE.exists():
            return float(_E_AXIS_POS_FILE.read_text().strip())
    except Exception:
        pass
    return float(default)


def _write_e_axis_position(value: float) -> None:
    try:
        _E_AXIS_POS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _E_AXIS_POS_FILE.write_text(f"{value:.6f}")
    except Exception as e:
        print(f"[E-axis] Warning: failed to persist position: {e}")


if not _E_AXIS_POS_FILE.exists():
    _write_e_axis_position(0.0)


# =============================================================================
# Connection / mode helpers
# =============================================================================
def _connected_event_is_set() -> bool:
    """
    Support both:
      - connected_event() -> threading.Event
      - connected_event   -> threading.Event
    """
    try:
        if connected_event is None:
            return True  # assume managed by start_serial()
        ev = connected_event() if callable(connected_event) else connected_event
        return bool(getattr(ev, "is_set", lambda: True)())
    except Exception:
        return True


def _ensure_connected() -> bool:
    """Ensure we have a live serial connection; try once if not."""
    if _connected_event_is_set():
        return True
    try:
        return connect_serial() is not None
    except Exception:
        return True  # tolerate if not available


def _ensure_units_and_absolute() -> None:
    """Set mm + absolute positioning. Safe to call repeatedly."""
    send_now("G21")  # millimeters
    send_now("G90")  # absolute


def feedrate(feed_mm_per_min: float) -> bool:
    """Set motion feedrate; return True if an 'ok' is observed."""
    try:
        resp = send_gcode(f"G0 F{float(feed_mm_per_min)}")
        return any("ok" in ln.lower() for ln in resp)
    except Exception:
        return False


def home(axis: str) -> bool:
    axis = axis.upper()
    if axis not in ("X", "Y", "Z"):
        print(f"[home] Invalid axis: {axis}")
        return False
    _ensure_units_and_absolute()
    resp = send_gcode(f"G28 {axis}")
    return any("ok" in ln.lower() for ln in resp)


def move_absolute(axis: str, position: float) -> bool:
    """
    Absolute move on an axis with clamping for XYZ; E is unclamped.
    """
    axis = axis.upper()
    if axis not in ("X", "Y", "Z", "E"):
        print(f"[move_absolute] Invalid axis: {axis}")
        return False

    pos = float(position)
    if axis == "X":
        pos = max(0.0, min(float(Config.X_MAX), pos))
    elif axis == "Y":
        pos = max(0.0, min(float(Config.Y_MAX), pos))
    elif axis == "Z":
        pos = max(0.0, min(float(Config.Z_MAX), pos))

    _ensure_units_and_absolute()
    resp = send_gcode(f"G0 {axis}{pos:.3f}")
    return any("ok" in ln.lower() for ln in resp)


# =============================================================================
# Position queries (M114)
# =============================================================================
def get_position() -> List[str]:
    """
    Return raw M114 response lines (list[str]).
    """
    try:
        return send_gcode("M114")
    except Exception:
        return []


def _parse_m114(lines: List[str]) -> Dict[str, Optional[float]]:
    """
    Parse a variety of M114 formats (Marlin/RepRap).
    Returns dict with X/Y/Z/E floats when found.
    """
    out: Dict[str, Optional[float]] = {"X": None, "Y": None, "Z": None, "E": None}
    text = " ".join(lines)
    # common tokens: "X:12.34 Y:56.78 Z:9.10 E:0.00", sometimes lowercase or spaced
    for ax in ("X", "Y", "Z", "E"):
        try:
            # Split by 'Ax:' and read the next number
            frag = text.split(f"{ax}:")[1].strip().split()[0]
            out[ax] = float(frag)
        except Exception:
            pass
    return out


def get_position_axis(axis: str) -> Optional[float]:
    """Parsed X/Y/Z/E from M114."""
    axis = axis.upper()
    if axis not in ("X", "Y", "Z", "E"):
        return None
    parsed = _parse_m114(get_position())
    return parsed.get(axis)


# =============================================================================
# Manual jogs
# =============================================================================
def deltaMove(delta: float, axis: str) -> bool:
    """
    Fast manual jog: pure-relative fire-and-forget.
    - No M114 polling (prevents queue contention during rapid clicks).
    - Uses Config.JOG_FEED_MM_PER_MIN.
    """
    if not _ensure_connected():
        raise RuntimeError("Serial not connected")

    axis = axis.upper()
    if axis not in ("X", "Y", "Z"):
        print(f"[deltaMove] Invalid axis: {axis}")
        return False

    jog_feed = float(getattr(Config, "JOG_FEED_MM_PER_MIN", 2400))
    # Avoid using send_gcode here (it waits for an 'ok' and can timeout under
    # heavy UI activity). Use fire-and-forget `send_now` to set planner feed
    # and issue the relative move for smooth responsiveness.
    try:
        send_now("G91")  # relative
        # set feed on planner (best-effort)
        try:
            send_now(f"G1 F{int(jog_feed)}")
        except Exception:
            pass
        ok = send_now(f"G1 {axis}{float(delta):.3f} F{int(jog_feed)}")
    finally:
        try:
            send_now("G90")  # restore absolute
        except Exception:
            pass
    return bool(ok)


# Default E rotation step
_E_DEFAULT_STEP = float(getattr(Config, "E_AXIS_DEFAULT_STEP", 0.1))


def _allow_cold_extrusion_if_needed() -> None:
    if bool(getattr(Config, "E_AXIS_ALLOW_COLD_EXTRUSION", True)):
        send_now("M302 P1")  # allow E moves cold


def rotate_nozzle_clockwise(step: float = _E_DEFAULT_STEP) -> Tuple[bool, str]:
    if not _ensure_connected():
        return False, "Serial not connected"
    try:
        _allow_cold_extrusion_if_needed()
        e = _read_e_axis_position(0.0) + float(step)
        if move_absolute("E", e):
            _write_e_axis_position(e)
            return True, "Nozzle rotated clockwise."
        return False, "Failed to rotate nozzle clockwise."
    except Exception as exc:
        return False, f"Rotate clockwise error: {exc}"


def rotate_nozzle_counterclockwise(step: float = _E_DEFAULT_STEP) -> Tuple[bool, str]:
    if not _ensure_connected():
        return False, "Serial not connected"
    try:
        _allow_cold_extrusion_if_needed()
        e = _read_e_axis_position(0.0) - float(step)
        if move_absolute("E", e):
            _write_e_axis_position(e)
            return True, "Nozzle rotated counterclockwise."
        return False, "Failed to rotate nozzle counterclockwise."
    except Exception as exc:
        return False, f"Rotate counterclockwise error: {exc}"


def jog_once(direction: str, step: float) -> None:
    """
    One atomic jog command that matches frontend directions.

    direction ∈ {
        'Xplus','Xminus','Yplus','Yminus','Zplus','Zminus',
        'rotateClockwise','rotateCounterclockwise'
    }
    """
    if direction == "Xplus":
        deltaMove(+step, "X")
    elif direction == "Xminus":
        deltaMove(-step, "X")
    elif direction == "Yplus":
        deltaMove(+step, "Y")
    elif direction == "Yminus":
        deltaMove(-step, "Y")
    elif direction == "Zplus":
        deltaMove(+step, "Z")
    elif direction == "Zminus":
        deltaMove(-step, "Z")
    elif direction == "rotateClockwise":
        rotate_nozzle_clockwise(step)
    elif direction == "rotateCounterclockwise":
        rotate_nozzle_counterclockwise(step)
    else:
        print(f"[jog_once] invalid direction: {direction}")


# =============================================================================
# INIT sequence (robust; mirrors legacy behavior)
# =============================================================================
def _wait_until_xyz(target: Dict[str, float],
                    tol: float = float(getattr(Config, "POS_TOL_MM", 0.02)),
                    timeout_s: float = float(getattr(Config, "POLL_TIMEOUT_S", 5.0)),
                    poll_s: float = float(getattr(Config, "POLL_INTERVAL_S", 0.10))
                    ) -> Tuple[bool, Dict[str, Optional[float]]]:
    """Poll M114 until X/Y/Z within tol of target; return (ok, last_seen)."""
    t0 = time.time()
    last: Dict[str, Optional[float]] = {"X": None, "Y": None, "Z": None}
    # Use a single M114 per poll iteration (avoid calling get_position_axis 3x)
    while (time.time() - t0) <= timeout_s:
        try:
            lines = get_position()
            parsed = _parse_m114(lines)
            cx = parsed.get("X")
            cy = parsed.get("Y")
            cz = parsed.get("Z")
            last = {"X": cx, "Y": cy, "Z": cz}
            if all(v is not None for v in last.values()):
                if (abs(last["X"] - target["X"]) <= tol and
                    abs(last["Y"] - target["Y"]) <= tol and
                    abs(last["Z"] - target["Z"]) <= tol):
                    return True, last
        except Exception:
            # tolerate transient parse errors and continue polling
            pass
        time.sleep(poll_s)
    return False, last


def _home_sequence() -> Tuple[bool, str]:
    """
    Try several homing patterns (covers Marlin/RepRap variants):
      1) G28           ; all axes
      2) G28 X Y  ; then  G28 Z
      3) G28 X   ; G28 Y ; G28 Z
    """
    if send_now("G28") and wait_for_motion_complete(60.0):
        return True, "Homed (G28)."

    if send_now("G28 X Y") and wait_for_motion_complete(60.0):
        if send_now("G28 Z") and wait_for_motion_complete(60.0):
            return True, "Homed (G28 XY + G28 Z)."

    ok_x = send_now("G28 X") and wait_for_motion_complete(60.0)
    ok_y = send_now("G28 Y") and wait_for_motion_complete(60.0)
    ok_z = send_now("G28 Z") and wait_for_motion_complete(60.0)
    if ok_x and ok_y and ok_z:
        return True, "Homed (G28 X, G28 Y, G28 Z)."
    return False, f"Homing failed: X={ok_x}, Y={ok_y}, Z={ok_z}"


def go2INIT() -> Tuple[bool, str]:
    """
    Initialize the probe to center, matching your prior sequence:
      1) Home XYZ
      2) Move to X=0 Y=0 Z=10 at fast feed
      3) Move to center: X=OFFSET_X+X_MAX/2, Y=OFFSET_Y+Y_MAX/2, Z=OFFSET_Z+Z_MAX/2
      4) Verify target within tolerance
    """
    if not _ensure_connected():
        return False, "Serial not connected"
    try:
        print("[INIT] start")
        _ensure_units_and_absolute()

        print("[INIT] homing ...")
        ok, why = _home_sequence()
        if not ok:
            return False, why
        print(f"[INIT] homing done: {why}")

        fast_feed = max(float(getattr(Config, "FAST_FEED_MM_PER_MIN", 1200.0)), 3000.0)
        print(f"[INIT] fast_feed set to {fast_feed} mm/min")
        feedrate(fast_feed)

        # Note: rotation / E-axis actions intentionally omitted from INIT.
        # The E-axis persistent restore and cold-extrusion enable were removed
        # to avoid changing rotation state during initialization.

        # Move to (0,0,10)
        print("[INIT] moving to X0 Y0 Z10 ...")
        send_now(f"G0 X0 Y0 Z10 F{int(fast_feed)}")
        if not wait_for_motion_complete(20.0):
            return False, "Timeout while moving to X0 Y0 Z10"
        ok, last = _wait_until_xyz({"X": 0.0, "Y": 0.0, "Z": 10.0})
        print(f"[INIT] at (0,0,10)? ok={ok}, last={last}")
        if not ok:
            return False, f"Did not reach (0,0,10). Last seen: {last}"

        # Move to center
        Xmax = float(getattr(Config, "X_MAX", 0.0))
        Ymax = float(getattr(Config, "Y_MAX", 0.0))
        Zmax = float(getattr(Config, "Z_MAX", 0.0))
        Xpos = float(getattr(Config, "OFFSET_X", 0.0)) + (Xmax / 2.0)
        Ypos = float(getattr(Config, "OFFSET_Y", 0.0)) + (Ymax / 2.0)
        Zpos = float(getattr(Config, "OFFSET_Z", 0.0)) + (Zmax / 2.0)
        print(f"[INIT] moving to center X={Xpos:.3f}, Y={Ypos:.3f}, Z={Zpos:.3f} ...")

        send_now(f"G0 X{Xpos:.3f} Y{Ypos:.3f} Z{Zpos:.3f} F{int(fast_feed)}")
        if not wait_for_motion_complete(30.0):
            return False, "Timeout while moving to INIT center position"
        ok, last = _wait_until_xyz({"X": Xpos, "Y": Ypos, "Z": Zpos})
        print(f"[INIT] at center? ok={ok}, last={last}")
        if not ok:
            return True, f"Nozzle initialized (center not within tolerance, last: {last})"

        print("[INIT] done")
        return True, "Nozzle initialized"
    except Exception as exc:
        print(f"[INIT] exception: {exc}")
        raise


def go2StartScan(start_x: float | None = None) -> bool:
    """Move to start_x (or X=0) at fast feed."""
    if not _ensure_connected():
        return False
    try:
        _ensure_units_and_absolute()
        feedrate(float(getattr(Config, "FAST_FEED_MM_PER_MIN", 1200)))
        x0 = 0.0 if start_x is None else max(0.0, min(float(Config.X_MAX), float(start_x)))
        send_gcode(f"G0 X{x0:.3f}")
        wait_for_motion_complete(10.0)
        return True
    except Exception as exc:
        print(f"[go2StartScan] {exc}")
        return False


def ScanPath(start_x: float | None = None, end_x: float | None = None) -> bool:
    """
    Traverse X from start_x to end_x at the (possibly computed) scan feed.
    Defaults to legacy 0 → X_MAX if None.
    """
    if not _ensure_connected():
        return False
    try:
        _ensure_units_and_absolute()
        # feedrate F = 60 * (e_r * fps) if enabled
        if bool(getattr(Config, "SCAN_FEED_FROM_ER_FPS", True)):
            scan_feed = float(Config.computed_scan_feed_mm_per_min())
        else:
            scan_feed = float(getattr(Config, "SCAN_SPEED_MM_PER_MIN", 90))
        feedrate(scan_feed)

        x0 = 0.0 if start_x is None else float(start_x)
        x1 = float(getattr(Config, "X_MAX", 118.0)) if end_x is None else float(end_x)
        x0 = max(0.0, min(float(Config.X_MAX), x0))
        x1 = max(0.0, min(float(Config.X_MAX), x1))

        send_gcode(f"G0 X{x0:.3f}")
        wait_for_motion_complete(10.0)

        send_gcode(f"G0 X{x1:.3f}")
        # generous timeout: distance / (feed mm/min) * 60 + margin
        dist = abs(x1 - x0)
        secs = (dist / max(scan_feed, 1e-6)) * 60.0 + 30.0
        wait_for_motion_complete(secs)
        return True
    except Exception as exc:
        print(f"[ScanPath] {exc}")
        return False


# =============================================================================
# Legacy compatibility shims
# =============================================================================
def connectprinter():
    """Legacy helper. Prefer the singleton. Returns the serial port or None."""
    try:
        return connect_serial()
    except Exception:
        return None


def getresponse(_serialconn=None) -> bytes:
    """Legacy helper: return one 'line' from M114 as bytes."""
    lines = get_position()
    first = (lines[0] if lines else "").encode("utf-8", errors="ignore")
    return first


def waitresponses(_serialconn=None, _mytext: str = "") -> bool:
    """Legacy helper: wait until moves finished (M400 ok)."""
    return wait_for_motion_complete(timeout=10.0)


def returnresponses(_serialconn=None, _mytext: str = "") -> List[str]:
    """Legacy helper: return M114 lines."""
    return get_position()



