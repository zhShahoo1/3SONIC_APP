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
- Manual jogs use Config.JOG_FEED_MM_PER_MIN (fast), scan path uses Config.SCAN_SPEED_MM_PER_MIN (unchanged).
- E-axis absolute position is persisted to disk to stay in sync across processes.

Compatibility helpers (no-op / thin shims):
    - connectprinter()
    - getresponse(serialconn)
    - waitresponses(serialconn, mytext)
    - returnresponses(serialconn, mytext)
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional, List, Tuple
import time

from app.config import Config
from app.core.serial_manager import (
    connect_serial,
    send_gcode,
    send_now,
    wait_for_motion_complete,
    get_position as _sm_get_position,
    get_position_axis as _sm_get_position_axis,
    connected_event,   # function returning the Event
)

# --------------------------------------------------------------------------------------
# E-axis (rotation) persistence (absolute position in "degrees"/steps you define on E)
# --------------------------------------------------------------------------------------
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

# Ensure file exists
if not _E_AXIS_POS_FILE.exists():
    _write_e_axis_position(0.0)

# --------------------------------------------------------------------------------------
# Connection/Mode helpers
# --------------------------------------------------------------------------------------
def _ensure_connected() -> bool:
    """Make sure we have a serial connection; try once if not."""
    if connected_event().is_set():
        return True
    return connect_serial() is not None

def _ensure_units_and_absolute() -> None:
    """Put controller in mm + absolute positioning (safe to call repeatedly)."""
    send_now("G21")  # millimeters
    send_now("G90")  # absolute

def feedrate(feed_mm_per_min: float) -> bool:
    """Set motion feedrate; returns True if we saw 'ok' in the response."""
    resp = send_gcode(f"G0 F{float(feed_mm_per_min)}")
    return any("ok" in (ln.lower()) for ln in resp)

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
        pos = max(0.0, min(Config.X_MAX, pos))
    elif axis == "Y":
        pos = max(0.0, min(Config.Y_MAX, pos))
    elif axis == "Z":
        pos = max(0.0, min(Config.Z_MAX, pos))

    _ensure_units_and_absolute()
    resp = send_gcode(f"G0 {axis}{pos:.3f}")
    return any("ok" in (ln.lower()) for ln in resp)

# --------------------------------------------------------------------------------------
# Public API (used by your Flask routes)
# --------------------------------------------------------------------------------------
def get_position() -> List[str]:
    """Raw M114 response lines (list[str])."""
    return _sm_get_position()

def get_position_axis(axis: str) -> Optional[float]:
    """Parsed X/Y/Z/E from M114."""
    return _sm_get_position_axis(axis)

def deltaMove(delta: float, axis: str) -> bool:
    """
    Manual jog by delta on axis (X/Y/Z) at FAST manual feed (Config.JOG_FEED_MM_PER_MIN).
    Uses relative move to avoid race with position polling.
    """
    if not _ensure_connected():
        raise RuntimeError("Serial not connected")

    axis = axis.upper()
    if axis not in ("X", "Y", "Z"):
        print(f"[deltaMove] Invalid axis: {axis}")
        return False

    # Clamp target based on current position to keep within bounds.
    current = get_position_axis(axis)
    if current is None:
        print(f"[deltaMove] Couldn't read current {axis} position.")
        return False

    target = current + float(delta)
    if axis == "X":
        target = max(0.0, min(Config.X_MAX, target))
        delta = target - current
    elif axis == "Y":
        target = max(0.0, min(Config.Y_MAX, target))
        delta = target - current
    elif axis == "Z":
        target = max(0.0, min(Config.Z_MAX, target))
        delta = target - current

    # Fast manual jog feed
    feedrate(Config.JOG_FEED_MM_PER_MIN)

    # Relative move for a tight jog
    send_now("G91")
    send_now(f"G1 {axis}{delta:.3f} F{int(Config.JOG_FEED_MM_PER_MIN)}")
    send_now("G90")
    return True

# Default rotation step (kept from config)
value = Config.E_AXIS_DEFAULT_STEP

def _allow_cold_extrusion_if_needed() -> None:
    if Config.E_AXIS_ALLOW_COLD_EXTRUSION:
        send_now("M302 P1")

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
    except Exception as e:
        return False, f"Rotate clockwise error: {e}"

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
    except Exception as e:
        return False, f"Rotate counterclockwise error: {e}"

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

        # Restore E-axis
        e_axis_position = _read_e_axis_position(0.0)
        if not move_absolute("E", e_axis_position):
            return False, "Failed to restore E-axis position."

        # Fast feed then lift Z with X=0, Y=0
        feedrate(Config.FAST_FEED_MM_PER_MIN)
        send_gcode("G0 X0 Y0 Z10")
        wait_for_motion_complete(10.0)

        # INIT coordinates
        Xpos = Config.OFFSET_X + (Config.X_MAX / 2.0)
        Ypos = Config.OFFSET_Y + (Config.Y_MAX / 2.0)
        Zpos = Config.OFFSET_Z + (Config.Z_MAX / 2.0)

        send_gcode(f"G0 X{Xpos:.3f} Y{Ypos:.3f} Z{Zpos:.3f}")
        wait_for_motion_complete(15.0)

        return True, f"Nozzle initialized and set to locked position: {e_axis_position:.3f}"
    except Exception as e:
        return False, f"INIT error: {e}"

def go2StartScan() -> bool:
    """Prepare for scan; move to X=0 at fast feed."""
    if not _ensure_connected():
        return False
    try:
        _ensure_units_and_absolute()
        feedrate(Config.FAST_FEED_MM_PER_MIN)
        send_gcode("G0 X0")
        wait_for_motion_complete(10.0)
        return True
    except Exception as e:
        print(f"[go2StartScan] {e}")
        return False

def ScanPath() -> bool:
    """Traverse X across full span at scan feed (kept as-is)."""
    if not _ensure_connected():
        return False
    try:
        _ensure_units_and_absolute()
        feedrate(Config.SCAN_SPEED_MM_PER_MIN)
        send_gcode(f"G0 X{Config.X_MAX:.3f}")
        wait_for_motion_complete(120.0)
        return True
    except Exception as e:
        print(f"[ScanPath] {e}")
        return False

# --------------------------------------------------------------------------------------
# Compatibility shims (for legacy code paths that might import these names)
# --------------------------------------------------------------------------------------
def connectprinter():
    """Legacy helper. Prefer the singleton. Returns the port or None."""
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

# --------------------------------------------------------------------------------------
# Old demo helper (unchanged)
# --------------------------------------------------------------------------------------
def unknowns() -> None:
    try:
        if not _ensure_connected():
            print("[unknowns] Not connected")
            return
        _ensure_units_and_absolute()
        home("X"); home("Y")
        feedrate(100.0)
        time.sleep(0.5)

        D = 0.0
        for _ in range(15):
            D += 1.0
            move_absolute("X", D)
            _ = get_position()
            time.sleep(0.2)
    except Exception as e:
        print(f"[unknowns] {e}")
