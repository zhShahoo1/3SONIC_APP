# app/core/keyboard_control.py
from __future__ import annotations

import threading
import time
import os

# third-party (Windows: pip install keyboard pygetwindow)
import keyboard
import pygetwindow as gw

from app.config import Config
from app.core.serial_manager import send_gcode, send_now, connected_event
from app.core.scanner_control import go2INIT
from app.core import scanner_control as pssc

# ===== Public toggle used by main.py =====
_keyboard_enabled = True
def enable_keyboard(flag: bool) -> None:
    global _keyboard_enabled
    _keyboard_enabled = bool(flag)

# ===== Settings =====
_ui_feed = float(getattr(Config, "UI_LINEAR_FEED_MM_PER_MIN", 360.0))
# Keyboard manual jog feed: prefer an explicit override `MANUAL_JOG_FEED_MM_PER_MIN`
# but cap it so keyboard-controlled jogs remain slower than UI button-driven moves.
manual_default = int(getattr(Config, "MANUAL_JOG_FEED_MM_PER_MIN", getattr(Config, "JOG_FEED_MM_PER_MIN", 2400)))
# cap at 75% of UI linear feed (but allow a sensible minimum)
cap = max(50, int(_ui_feed * 0.75))
FEEDRATE = int(min(manual_default, cap))

STEP_CONTINUOUS_MM = 0.10     # increment per tick for continuous arrows
STEP_INTERVAL_S = 0.015       # tick period (lower = smoother/faster)
# Optionally perform a quickstop (firmware-dependent) when a key is released
# to halt motion immediately instead of letting queued small relative moves
# drain through the planner. Set via env `KEYBOARD_QUICKSTOP_ON_RELEASE=1`.
USE_QUICKSTOP = str(os.environ.get("KEYBOARD_QUICKSTOP_ON_RELEASE", "")).strip().lower() in ("1", "true", "yes", "on")
WINDOW_TITLE_FRAGMENT = "3SONIC"   # only accept input when the app/window is focused

# internal state
_move_threads: dict[tuple[str, int], tuple[threading.Thread, threading.Event]] = {}
_move_lock = threading.Lock()
_active_axis: tuple[str, int] | None = None


def _is_window_focused() -> bool:
    try:
        active = gw.getActiveWindow()
        return bool(active and (WINDOW_TITLE_FRAGMENT in active.title))
    except Exception:
        return False


def emergency_stop() -> None:
    """Immediate stop."""
    try:
        send_now("M112")
    except Exception as e:
        print(f"[Keyboard] Emergency stop failed: {e}")


def _begin_continuous_jog(axis: str, sign: int) -> None:
    """Start continuous jog on axis with direction sign (+1 / -1)."""
    global _active_axis
    if not _is_window_focused() or not _keyboard_enabled:
        return

    key_id = (axis, sign)
    with _move_lock:
        if _active_axis is not None and _active_axis != key_id:
            # already jogging another axis; ignore until released
            return
        if key_id in _move_threads:
            return

        # Check connection in a resilient way (connected_event may be
        # an Event or a callable that returns an Event). If we cannot
        # determine connectivity, assume connected and let send_now fail
        # gracefully.
        try:
            ev = connected_event() if callable(connected_event) else connected_event
            if ev is not None and hasattr(ev, "is_set") and not ev.is_set():
                print("[Keyboard] ⚠ Not connected; jog ignored.")
                return
        except Exception:
            # conservative: proceed and let serial calls handle failures
            pass

        _active_axis = key_id
        stop_flag = threading.Event()

        def _worker():
            # Acquire the shared UI mode lock so we don't race with other
            # code that switches absolute/relative modes (G90/G91).
            try:
                with getattr(pssc, "UI_MODE_LOCK", threading.Lock()):
                    send_now("G91")
                    while not stop_flag.is_set():
                        send_now(f"G1 {axis}{sign * STEP_CONTINUOUS_MM:.4f} F{FEEDRATE}")
                        time.sleep(STEP_INTERVAL_S)
                    send_now("G90")
            except Exception as e:
                print(f"[Keyboard] continuous jog error: {e}")

        t = threading.Thread(target=_worker, daemon=True)
        _move_threads[key_id] = (t, stop_flag)
        t.start()


def _end_continuous_jog(axis: str, sign: int) -> None:
    """Stop continuous jog for given axis/sign."""
    global _active_axis
    key_id = (axis, sign)
    with _move_lock:
        pair = _move_threads.pop(key_id, None)
        if pair:
            t, stop_flag = pair
            # signal the worker to stop
            stop_flag.set()
            # join briefly to allow the worker to restore G90 and finish cleanup
            try:
                t.join(timeout=0.5)
            except Exception:
                pass
            # small settle to avoid immediate mode switching races
            try:
                import time as _time
                _time.sleep(0.02)
            except Exception:
                pass
            # If configured, send a quick-stop command to the firmware to halt
            # motion immediately. This is firmware-dependent (Marlin supports
            # M410). Enable via environment variable only when known safe.
            if USE_QUICKSTOP:
                try:
                    send_now("M410")
                except Exception:
                    pass
        if _active_axis == key_id:
            _active_axis = None


def _on_press(key: str) -> None:
    if not _is_window_focused() or not _keyboard_enabled:
        return

    if key == "up":
        _begin_continuous_jog("Y", -1)
    elif key == "down":
        _begin_continuous_jog("Y", +1)
    elif key == "left":
        _begin_continuous_jog("X", +1)
    elif key == "right":
        _begin_continuous_jog("X", -1)
    elif key == "page up":
        _begin_continuous_jog("Z", -1)   # Z-
    elif key == "page down":
        _begin_continuous_jog("Z", +1)   # Z+
    elif key == "esc":
        emergency_stop()
    elif key == "home":
        try:
            # local import to avoid circular imports at module import time
            go2INIT()
        except Exception as e:
            print(f"[Keyboard] Failed to run go2INIT: {e}")


def _on_release(key: str) -> None:
    if key == "up":
        _end_continuous_jog("Y", -1)
    elif key == "down":
        _end_continuous_jog("Y", +1)
    elif key == "left":
        _end_continuous_jog("X", +1)
    elif key == "right":
        _end_continuous_jog("X", -1)
    elif key == "page up":
        _end_continuous_jog("Z", -1)
    elif key == "page down":
        _end_continuous_jog("Z", +1)



def start_keyboard_listener():
    """
    Start global keyboard hooks. Non-blocking; returns the backing thread.
    Requires admin privileges on Windows for `keyboard`.
    """
    print("[Keyboard] Control active. Hold arrow keys for XY, PgUp/PgDn for Z, ESC = E-stop. Home = initialize scanner.")

    # Press hooks
    keyboard.on_press_key("up",        lambda _: _on_press("up"))
    keyboard.on_press_key("down",      lambda _: _on_press("down"))
    keyboard.on_press_key("left",      lambda _: _on_press("left"))
    keyboard.on_press_key("right",     lambda _: _on_press("right"))
    keyboard.on_press_key("page up",   lambda _: _on_press("page up"))
    keyboard.on_press_key("page down", lambda _: _on_press("page down"))
    keyboard.on_press_key("esc",       lambda _: _on_press("esc"))
    keyboard.on_press_key("home",      lambda _: _on_press("home"))

    # Release hooks
    keyboard.on_release_key("up",        lambda _: _on_release("up"))
    keyboard.on_release_key("down",      lambda _: _on_release("down"))
    keyboard.on_release_key("left",      lambda _: _on_release("left"))
    keyboard.on_release_key("right",     lambda _: _on_release("right"))
    keyboard.on_release_key("page up",   lambda _: _on_release("page up"))
    keyboard.on_release_key("page down", lambda _: _on_release("page down"))

    t = threading.Thread(target=keyboard.wait, args=("esc",), daemon=True)
    t.start()
    return t

