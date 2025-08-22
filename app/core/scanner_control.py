"""
Refactor of the old `pathosonicscannercontrol.py`.

Key changes:
- Uses the centralized Config and the singleton Serial Manager.
- Maintains public API compatibility for functions used by your app:
    - deltaMove(delta, axis)
    - rotate_nozzle_clockwise(step=value)
    - rotate_nozzle_counterclockwise(step=value)
    - go2INIT()
    - go2StartScan()
    - ScanPath()
    - get_position()               # returns list[str], used as get_position()[0] elsewhere
    - get_position_axis(axis)      # helper

- Adds safer bounds checking, absolute/relative mode handling, and persists E-axis position on disk
  so multiple processes (Flask + record.py) share the same rotation reference.

Notes:
- The old "connectprinter()/getresponse()/waitresponses()/returnresponses()" were internal utilities in the
  original script. They are provided here as thin compatibility wrappers but the new code does not need them.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple

import time
import serial
import serial.tools.list_ports

from app.config import Config
from app.core.serial_manager import (
    start_serial,
    connect_serial,
    send_gcode,
    send_now,
    wait_for_motion_complete,
    get_position as _sm_get_position,
    get_position_axis as _sm_get_position_axis,
)

# Start serial services on import so routes can call movement immediately.
start_serial()

# ---------------- Persistence for E-axis (rotation) ----------------

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
        _E_AXIS_POS_FILE.write_text(f"{value:.6f}")
    except Exception as e:
        print(f"[E-axis] Warning: failed to persist position: {e}")

# Initialize if missing
if not _E_AXIS_POS_FILE.exists():
    _write_e_axis_position(0.0)


# ---------------- Basic helpers ----------------

def _ensure_units_and_modes() -> None:
    """
    Ensure printer is in mm and absolute positioning for predictable moves.
    Safe to call repeatedly.
    """
    send_now("G21")  # millimeters
    send_now("G90")  # absolute

def feedrate(Feedrate: float) -> bool:
    """
    Set motion feedrate (mm/min), like the old `feedrate(serialconn, Feedrate)`.
    Returns True on 'ok' detection, else False.
    """
    resp = send_gcode(f"G0 F{float(Feedrate)}")
    ok = any("ok" in (line.lower()) for line in resp)
    return ok

def home(axis: str) -> bool:
    """
    Home a specific axis ('X','Y','Z').
    """
    axis = axis.upper()
    if axis not in ("X", "Y", "Z"):
        print("Invalid axis ID:", axis)
        return False
    _ensure_units_and_modes()
    resp = send_gcode(f"G28 {axis}")
    ok = any("ok" in (line.lower()) for line in resp)
    return ok

def move(axis: str, position: float) -> bool:
    """
    Absolute move to given position on axis (X/Y/Z/E).
    Mirrors old signature but does not require passing a serial object.
    """
    axis = axis.upper()
    if axis not in ("X", "Y", "Z", "E"):
        print("Invalid axis ID:", axis)
        return False

    # Clamp positions for safety (for E we don't clamp)
    pos = float(position)
    if axis == "X":
        pos = max(0.0, min(Config.X_MAX, pos))
    elif axis == "Y":
        pos = max(0.0, min(Config.Y_MAX, pos))
    elif axis == "Z":
        pos = max(0.0, min(Config.Z_MAX, pos))

    _ensure_units_and_modes()
    resp = send_gcode(f"G0 {axis}{pos:.3f}")
    ok = any("ok" in (line.lower()) for line in resp)
    return ok

def get_position() -> List[str]:
    """
    Match old behavior: returns raw response lines (list[str]).
    In your code, you use get_position()[0].
    """
    return _sm_get_position()

def get_position_axis(axis: str) -> Optional[float]:
    """
    Return current numeric coordinate for axis (X/Y/Z/E) or None if unavailable.
    """
    return _sm_get_position_axis(axis)


# ---------------- Compatibility wrappers (not used by new code) ----------------

def connectprinter() -> Optional[serial.Serial]:
    """
    Compatibility: return the underlying serial instance (or None).
    """
    return connect_serial(baudrate=Config.SERIAL_BAUD, timeout=Config.SERIAL_TIMEOUT_S)

def getresponse(serialconn) -> bytes:
    """
    Old helper: read one line (blocking until available).
    Here we just request M114 and return the first line bytes-like for compatibility.
    """
    lines = _sm_get_position()
    first = (lines[0] if lines else "").encode("utf-8", errors="ignore")
    return first

def waitresponses(serialconn, mytext: str) -> bool:
    """
    Old helper: loop-read until 'ok'. We emulate by issuing an M400 and waiting for ok.
    """
    return wait_for_motion_complete(timeout=10.0)

def returnresponses(serialconn, mytext: str) -> List[str]:
    """
    Old helper: collect lines until 'ok'. We emulate by a single M114 grab.
    """
    return _sm_get_position()


# ---------------- Temperature (kept from original) ----------------

def gettemperature() -> Tuple[Optional[float], Optional[float]]:
    """
    Parse temperatures from M105 output (if firmware reports them).
    Returns (T1, T2) or (None, None).
    """
    resp = send_gcode("M105")
    joined = " ".join(resp)
    try:
        parts = joined.replace("==", ":").replace("T0:", "T:").split()
        # Look for patterns like "T:xxx" and "B:xxx" (hotend/bed) – firmware dependent.
        T1 = None
        T2 = None
        for tok in parts:
            if tok.startswith("T:"):
                T1 = float(tok.split(":")[1].split("/")[0])
            if tok.startswith("B:"):
                T2 = float(tok.split(":")[1].split("/")[0])
        return T1, T2
    except Exception:
        return None, None


# ---------------- High-level motions (public API your app uses) ----------------
from app.config import Config


def deltaMove(delta, axis):
    """Move [delta] mm on [axis] with fast jog feedrate."""
    if axis not in ["X", "Y", "Z"]:
        print("Invalid axis ID:", axis)
        return False

    ser = connectprinter()
    try:
        # 1) Use fast jog feedrate
        feedrate(ser, int(Config.JOG_FEED_MM_PER_MIN))

        # 2) Read current pos and clamp target
        pos = getposition_axis(ser, axis)
        print("Current Position:", pos)
        newposition = float(pos) + float(delta)

        if axis in ["X", "Y"]:
            lim = Config.X_MAX if axis == "X" else Config.Y_MAX
            newposition = max(0.0, min(lim, newposition))
        elif axis == "Z":
            newposition = max(0.0, min(Config.Z_MAX, newposition))

        # 3) Execute the move
        move(ser, axis, newposition)
        return True
    finally:
        ser.close()

def _ensure_conected() -> bool:
    if connect_event().is_set():
        return True
    return connect_serial() is not None

# def deltaMove(delta: float, axis: str) -> bool:
#     """
#     Move by a delta (mm) on the specified axis (X/Y/Z).
#     Applies bounds [0, MAX].
#     """
#     axis = axis.upper()
#     if axis not in ("X", "Y", "Z"):
#         print("Invalid axis ID:", axis)
#         return False

#     current = get_position_axis(axis)
#     if current is None:
#         print(f"Could not read current {axis} position.")
#         return False

#     newpos = current + float(delta)

#     # Clamp
#     if axis == "X":
#         newpos = max(0.0, min(Config.X_MAX, newpos))
#     elif axis == "Y":
#         newpos = max(0.0, min(Config.Y_MAX, newpos))
#     elif axis == "Z":
#         newpos = max(0.0, min(Config.Z_MAX, newpos))

#     ok = move(axis, newpos)
#     return ok


# Preserve the default "value" step for rotation (E-axis)
value = Config.E_AXIS_DEFAULT_STEP

def _allow_cold_extrusion_if_needed() -> None:
    if Config.E_AXIS_ALLOW_COLD_EXTRUSION:
        send_now("M302 P1")  # Allow cold extrusion

def rotate_nozzle_clockwise(step: float = value) -> Tuple[bool, str]:
    """
    Advance E-axis positively by `step`.
    Persists the E position to disk so multiple processes share state.
    """
    try:
        _allow_cold_extrusion_if_needed()
        e_pos = _read_e_axis_position(0.0)
        target = e_pos + float(step)
        ok = move("E", target)
        if ok:
            _write_e_axis_position(target)
            return True, "Nozzle rotated clockwise."
        return False, "Failed to rotate nozzle clockwise."
    except Exception as e:
        return False, f"Rotate clockwise error: {e}"

def rotate_nozzle_counterclockwise(step: float = value) -> Tuple[bool, str]:
    """
    Advance E-axis negatively by `step`.
    """
    try:
        _allow_cold_extrusion_if_needed()
        e_pos = _read_e_axis_position(0.0)
        target = e_pos - float(step)
        ok = move("E", target)
        if ok:
            _write_e_axis_position(target)
            return True, "Nozzle rotated counterclockwise."
        return False, "Failed to rotate nozzle counterclockwise."
    except Exception as e:
        return False, f"Rotate counterclockwise error: {e}"


def go2INIT() -> Tuple[bool, str]:
    """
    Homes XYZ, restores E to persisted angle, lifts Z, then moves to INIT (center + offsets).
    Mirrors the original flow and messages.
    """
    try:
        _ensure_units_and_modes()
        _allow_cold_extrusion_if_needed()

        # Home XYZ
        for ax in ("X", "Y", "Z"):
            if not home(ax):
                return False, f"Failed to home {ax}"

        # Restore E-axis
        e_axis_position = _read_e_axis_position(0.0)
        if not move("E", e_axis_position):
            return False, "Failed to restore E-axis position."

        # Set a fast feedrate, then lift Z with X=0 Y=0
        feedrate(Config.FAST_FEED_MM_PER_MIN)
        send_gcode("G0 X0 Y0 Z10")
        wait_for_motion_complete(10.0)

        # Compute INIT (center + offsets)
        Xpos = Config.OFFSET_X + (Config.X_MAX / 2.0)
        Ypos = Config.OFFSET_Y + (Config.Y_MAX / 2.0)
        Zpos = Config.OFFSET_Z + (Config.Z_MAX / 2.0)

        # Move to INIT
        cmd = f"G0 X{Xpos:.3f} Y{Ypos:.3f} Z{Zpos:.3f}"
        send_gcode(cmd)
        wait_for_motion_complete(15.0)

        return True, f"Nozzle initialized and set to locked position: {e_axis_position:.3f} degrees"
    except Exception as e:
        return False, f"INIT error: {e}"


def go2StartScan() -> bool:
    """
    As in original: set fast feed, move X to 0 (manual adjustment allowed).
    """
    try:
        _ensure_units_and_modes()
        feedrate(Config.FAST_FEED_MM_PER_MIN)
        send_gcode("G0 X0")
        wait_for_motion_complete(10.0)
        return True
    except Exception as e:
        print(f"[go2StartScan] {e}")
        return False


def ScanPath() -> bool:
    """
    Move across full X span at scan feedrate.
    """
    try:
        _ensure_units_and_modes()
        feedrate(Config.SCAN_SPEED_MM_PER_MIN)
        send_gcode(f"G0 X{Config.X_MAX:.3f}")
        wait_for_motion_complete(60.0)  # generous timeout for full-span move
        return True
    except Exception as e:
        print(f"[ScanPath] {e}")
        return False


# ---------------- Old demo / test helper (kept) ----------------

def unknowns() -> None:
    """
    Old demo showing a sequence of moves.
    """
    try:
        _ensure_units_and_modes()
        # Home XY and set slow feed
        home("X")
        home("Y")
        feedrate(100.0)  # mm/min
        time.sleep(1.0)

        D = 0.0
        for _ in range(15):
            D += 1.0
            move("X", D)
            _ = get_position()
            time.sleep(0.2)
    except Exception as e:
        print(f"[unknowns] {e}")
