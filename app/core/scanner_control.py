# app/core/scanner_control.py
"""
Refactor of the old `pathosonicscannercontrol.py`.

Key points:
- Uses centralized Config + the shared serial connection from app.core.serial_manager.
- Public API kept:
    - deltaMove(delta, axis)
    - rotate_nozzle_clockwise(step=value)
    - rotate_nozzle_counterclockwise(step=value)
    - go2INIT()
    - go2StartScan()
    - ScanPath()
    - get_position()                # returns list[str]
    - get_position_axis(axis)
- Manual jogs use Config.JOG_FEED_MM_PER_MIN (fast),
  scan path uses Config.SCAN_SPEED_MM_PER_MIN (unchanged).
- E-axis absolute position is persisted to disk to stay in sync across processes.

Compatibility shims retained:
    - connectprinter()
    - getresponse(serialconn)
    - waitresponses(serialconn, mytext)
    - returnresponses(serialconn, mytext)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional, Tuple

from app.config import Config
from app.core.serial_manager import (
    connect_serial,
    send_gcode,
    send_now,
    wait_for_motion_complete,
    get_position as _sm_get_position,
    get_position_axis as _sm_get_position_axis,
    connected_event,  # function in our serial_manager; some older builds exposed an Event
)

# ======================================================================================
# E-axis (rotation) persistence (absolute position in whatever unit your E represents)
# ======================================================================================

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


# Ensure file exists once
if not _E_AXIS_POS_FILE.exists():
    _write_e_axis_position(0.0)

# ======================================================================================
# Connection / mode helpers
# ======================================================================================


def _connected_event_is_set() -> bool:
    """
    Support both:
      - connected_event() -> threading.Event
      - connected_event  -> threading.Event
    """
    try:
        ev = connected_event() if callable(connected_event) else connected_event
        return bool(ev.is_set())
    except Exception:
        return False


def _ensure_connected() -> bool:
    """Ensure we have a live serial connection; try once if not."""
    if _connected_event_is_set():
        return True
    return connect_serial() is not None


def _ensure_units_and_absolute() -> None:
    """Make controller use mm + absolute positioning (safe to call repeatedly)."""
    send_now("G21")  # millimeters
    send_now("G90")  # absolute


def feedrate(feed_mm_per_min: float) -> bool:
    """Set motion feedrate; returns True if an 'ok' is seen in the response."""
    try:
        resp = send_gcode(f"G0 F{float(feed_mm_per_min)}")
        return any("ok" in (ln.lower()) for ln in resp)
    except Exception:
        return False


def home(axis: str) -> bool:
    axis = axis.upper()
    if axis not in ("X", "Y", "Z"):
        print(f"[home] Invalid axis: {axis}")
        return False
    _ensure_units_and_absolute()
    resp = send_gcode(f"G28 {axis}")
    return any("ok" in (ln.lower()) for ln in resp)


def move_absolute(axis: str, position: float) -> bool:
    """Absolute move on an axis with clamping for XYZ; E is unclamped."""
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
    return any("ok" in (ln.lower()) for ln in resp)


# ======================================================================================
# Public API (used by Flask routes)
# ======================================================================================

def get_position() -> List[str]:
    """Raw M114 response lines (list[str])."""
    return _sm_get_position()


def get_position_axis(axis: str) -> Optional[float]:
    """Parsed X/Y/Z/E from M114."""
    return _sm_get_position_axis(axis)


def deltaMove(delta: float, axis: str) -> bool:
    """
    Manual jog by delta on axis (X/Y/Z) at fast manual feed (Config.JOG_FEED_MM_PER_MIN).
    Uses relative move to avoid race conditions with position polling.
    Clamps the target to [0, MAX] for XYZ.
    """
    if not _ensure_connected():
        raise RuntimeError("Serial not connected")

    axis = axis.upper()
    if axis not in ("X", "Y", "Z"):
        print(f"[deltaMove] Invalid axis: {axis}")
        return False

    # Read current absolute pos, clamp final target, derive safe delta
    current = get_position_axis(axis)
    if current is None:
        print(f"[deltaMove] Couldn't read current {axis} position.")
        return False

    target = current + float(delta)
    if axis == "X":
        target = max(0.0, min(float(Config.X_MAX), target))
    elif axis == "Y":
        target = max(0.0, min(float(Config.Y_MAX), target))
    elif axis == "Z":
        target = max(0.0, min(float(Config.Z_MAX), target))

    safe_delta = target - current

    # Fast manual jog
    jog_feed = float(getattr(Config, "JOG_FEED_MM_PER_MIN", 2400))
    feedrate(jog_feed)

    # Relative nudge
    send_now("G91")
    send_now(f"G1 {axis}{safe_delta:.3f} F{int(jog_feed)}")
    send_now("G90")
    return True


# Default rotation step (use Config if present)
value = float(getattr(Config, "E_AXIS_DEFAULT_STEP", 0.1))


def _allow_cold_extrusion_if_needed() -> None:
    if bool(getattr(Config, "E_AXIS_ALLOW_COLD_EXTRUSION", True)):
        send_now("M302 P1")  # allow E moves "cold"


def rotate_nozzle_clockwise(step: float = value) -> Tuple[bool, str]:
    if not _ensure_connected():
        return False, "Serial not connected"
    try:
        _allow_cold_extrusion_if_needed()
        e = _read_e_axis_position(0.0) + float(step)
        ok = move_absolute("E", e)
        if ok:
            _write_e_axis_position(e)
            return True, "Nozzle rotated clockwise."
        return False, "Failed to rotate nozzle clockwise."
    except Exception as exc:
        return False, f"Rotate clockwise error: {exc}"


def rotate_nozzle_counterclockwise(step: float = value) -> Tuple[bool, str]:
    if not _ensure_connected():
        return False, "Serial not connected"
    try:
        _allow_cold_extrusion_if_needed()
        e = _read_e_axis_position(0.0) - float(step)
        ok = move_absolute("E", e)
        if ok:
            _write_e_axis_position(e)
            return True, "Nozzle rotated counterclockwise."
        return False, "Failed to rotate nozzle counterclockwise."
    except Exception as exc:
        return False, f"Rotate counterclockwise error: {exc}"


def go2INIT() -> Tuple[bool, str]:
    """Home XYZ, restore E, lift Z, move to INIT (center + offsets)."""
    if not _ensure_connected():
        return False, "Serial not connected"
    try:
        _ensure_units_and_absolute()
        _allow_cold_extrusion_if_needed()

        for ax in ("X", "Y", "Z"):
            if not home(ax):
                return False, f"Failed to home {ax}"

        # Restore E-axis absolute position
        e_axis_position = _read_e_axis_position(0.0)
        if not move_absolute("E", e_axis_position):
            return False, "Failed to restore E-axis position."

        # Fast feed then lift Z with X=0, Y=0
        feedrate(float(getattr(Config, "FAST_FEED_MM_PER_MIN", 1200)))
        send_gcode("G0 X0 Y0 Z10")
        wait_for_motion_complete(10.0)

        # INIT coordinates (center + offsets)
        Xpos = float(Config.OFFSET_X) + (float(Config.X_MAX) / 2.0)
        Ypos = float(Config.OFFSET_Y) + (float(Config.Y_MAX) / 2.0)
        Zpos = float(Config.OFFSET_Z) + (float(Config.Z_MAX) / 2.0)

        send_gcode(f"G0 X{Xpos:.3f} Y{Ypos:.3f} Z{Zpos:.3f}")
        wait_for_motion_complete(15.0)

        return True, f"Nozzle initialized and set to locked position: {e_axis_position:.3f}"
    except Exception as exc:
        return False, f"INIT error: {exc}"


def go2StartScan() -> bool:
    """Prepare for scan; move to X=0 at fast feed."""
    if not _ensure_connected():
        return False
    try:
        _ensure_units_and_absolute()
        feedrate(float(getattr(Config, "FAST_FEED_MM_PER_MIN", 1200)))
        send_gcode("G0 X0")
        wait_for_motion_complete(10.0)
        return True
    except Exception as exc:
        print(f"[go2StartScan] {exc}")
        return False


def ScanPath() -> bool:
    """Traverse X across full span at configured scan feed."""
    if not _ensure_connected():
        return False
    try:
        _ensure_units_and_absolute()
        scan_feed = float(getattr(Config, "SCAN_SPEED_MM_PER_MIN", 90))
        feedrate(scan_feed)
        send_gcode(f"G0 X{float(Config.X_MAX):.3f}")
        # generous timeout for full-span move
        wait_for_motion_complete(120.0)
        return True
    except Exception as exc:
        print(f"[ScanPath] {exc}")
        return False


# ======================================================================================
# Compatibility shims (for legacy code)
# ======================================================================================

def connectprinter():
    """Legacy helper. Prefer the singleton. Returns the serial port or None."""
    return connect_serial()


def getresponse(_serialconn=None) -> bytes:
    """Legacy helper: return one 'line' from M114 as bytes."""
    lines = _sm_get_position()
    first = (lines[0] if lines else "").encode("utf-8", errors="ignore")
    return first


def waitresponses(_serialconn=None, _mytext: str = "") -> bool:
    """Legacy helper: wait until moves finished (M400 ok)."""
    return wait_for_motion_complete(timeout=10.0)


def returnresponses(_serialconn=None, _mytext: str = "") -> List[str]:
    """Legacy helper: return M114 lines."""
    return _sm_get_position()


# ======================================================================================
# Old demo helper (unchanged)
# ======================================================================================

def unknowns() -> None:
    try:
        if not _ensure_connected():
            print("[unknowns] Not connected")
            return
        _ensure_units_and_absolute()
        home("X")
        home("Y")
        feedrate(100.0)
        time.sleep(0.5)

        D = 0.0
        for _ in range(15):
            D += 1.0
            move_absolute("X", D)
            _ = get_position()
            time.sleep(0.2)
    except Exception as exc:
        print(f"[unknowns] {exc}")
