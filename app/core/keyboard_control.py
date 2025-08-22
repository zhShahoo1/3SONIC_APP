# app/core/keyboard_control.py
from __future__ import annotations

import threading
import time

# third-party (Windows: pip install keyboard pygetwindow)
import keyboard
import pygetwindow as gw

from app.config import Config
from app.core.serial_manager import send_gcode, send_now, connected_event

# ===== Public toggle used by main.py =====
_keyboard_enabled = True
def enable_keyboard(flag: bool) -> None:
    global _keyboard_enabled
    _keyboard_enabled = bool(flag)

# ===== Settings =====
FEEDRATE = int(getattr(Config, "MANUAL_JOG_FEED_MM_PER_MIN", 4000))  # fast manual jogs
STEP_CONTINUOUS_MM = 0.10     # increment per tick for continuous arrows
STEP_INTERVAL_S = 0.015       # tick period (lower = smoother/faster)
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

        if not connected_event().is_set():
            print("[Keyboard] ⚠ Not connected; jog ignored.")
            return

        _active_axis = key_id
        stop_flag = threading.Event()

        def _worker():
            try:
                send_now("G91")  # relative
                while not stop_flag.is_set():
                    send_now(f"G1 {axis}{sign * STEP_CONTINUOUS_MM:.3f} F{FEEDRATE}")
                    time.sleep(STEP_INTERVAL_S)
            finally:
                send_now("G90")  # absolute

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
            _, stop_flag = pair
            stop_flag.set()
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
    elif key == "esc":
        emergency_stop()


def _on_release(key: str) -> None:
    if key == "up":
        _end_continuous_jog("Y", -1)
    elif key == "down":
        _end_continuous_jog("Y", +1)
    elif key == "left":
        _end_continuous_jog("X", +1)
    elif key == "right":
        _end_continuous_jog("X", -1)


def start_keyboard_listener():
    """
    Start global keyboard hooks. Non-blocking; returns the backing thread.
    Requires admin privileges on Windows for `keyboard`.
    """
    print("[Keyboard] Control active. Hold arrow keys for continuous XY jog. ESC = E-stop.")

    # Press hooks
    keyboard.on_press_key("up",    lambda _: _on_press("up"))
    keyboard.on_press_key("down",  lambda _: _on_press("down"))
    keyboard.on_press_key("left",  lambda _: _on_press("left"))
    keyboard.on_press_key("right", lambda _: _on_press("right"))
    keyboard.on_press_key("esc",   lambda _: _on_press("esc"))

    # Release hooks
    keyboard.on_release_key("up",    lambda _: _on_release("up"))
    keyboard.on_release_key("down",  lambda _: _on_release("down"))
    keyboard.on_release_key("left",  lambda _: _on_release("left"))
    keyboard.on_release_key("right", lambda _: _on_release("right"))

    t = threading.Thread(target=keyboard.wait, args=("esc",), daemon=True)
    t.start()
    return t
