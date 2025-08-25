# app/core/serial_manager.py
from __future__ import annotations

import threading
import queue
import time
import re
from typing import Callable, Optional, List, Tuple

import serial
import serial.tools.list_ports

from app.config import Config

# =============================================================================
# Shared state (module-level singleton)
# =============================================================================
serial_port: Optional[serial.Serial] = None

# Queue for G-code requests that expect a small response window
_command_queue: "queue.Queue[tuple[str, Optional[Callable[[List[str]], None]]]]" = queue.Queue()

# Locks:
# - lock: guards queued G-code write + read window (serialized)
# - send_now_lock: guards immediate "fire-and-forget" writes
lock = threading.Lock()
send_now_lock = threading.Lock()

# Connection state event (keep your legacy accessor)
_connected_event = threading.Event()

def connected_event() -> threading.Event:
    """Return the connection Event (legacy callable API)."""
    return _connected_event

# Also export a variable alias for code that imported it as a variable
connected_event_var: threading.Event = _connected_event


# =============================================================================
# Port detection + connect/close
# =============================================================================
def _detect_port() -> Optional[str]:
    """Pick an explicit port from Config or auto-detect by description patterns."""
    if Config.SERIAL_PORT:
        return Config.SERIAL_PORT
    for p in serial.tools.list_ports.comports(include_links=False):
        desc = (p.description or "").upper()
        if any(pattern in desc for pattern in Config.SERIAL_PROFILE.description_patterns):
            return p.device
    return None


def connect_serial(baudrate: int = Config.SERIAL_BAUD,
                   timeout: float = Config.SERIAL_TIMEOUT_S) -> Optional[serial.Serial]:
    """Open serial port; blocking writes (write_timeout=None) for robustness."""
    global serial_port
    try:
        port = _detect_port()
        if not port:
            print("[Serial] No valid serial port found.")
            _connected_event.clear()
            serial_port = None
            return None

        ser = serial.Serial(port, baudrate, timeout=timeout)
        # Ensure blocking writes like in the stable project
        try:
            ser.write_timeout = None
        except Exception:
            pass

        # Give MCU time to reset if needed
        time.sleep(2.0)

        # Clear any stale input
        try:
            ser.reset_input_buffer()
        except Exception:
            pass

        serial_port = ser
        _connected_event.set()
        print(f"[Serial] Connected to {port} @ {baudrate}")
        return ser

    except serial.SerialException as e:
        print(f"[Serial] Open error: {e}")
    except Exception as e:
        print(f"[Serial] Unexpected connect error: {e}")

    _connected_event.clear()
    serial_port = None
    return None


def close_serial() -> None:
    """Close the serial handle and clear connection state."""
    global serial_port
    sp = serial_port
    serial_port = None
    _connected_event.clear()
    if sp:
        try:
            if sp.is_open:
                sp.close()
                print("[Serial] 🔌 Closed.")
        except Exception as e:
            print(f"[Serial] Close error: {e}")


# =============================================================================
# Background workers (queue + reconnect)
# =============================================================================
def _process_queue() -> None:
    """Serialize queued G-code: write + short read window (like the other project)."""
    global serial_port
    while True:
        command, callback = _command_queue.get()
        try:
            # One-at-a-time for queued ops to keep write/read windows clean
            with lock:
                sp = serial_port
                if not (sp and sp.is_open):
                    print("[Serial] ⚠ No active serial connection.")
                    if callback:
                        callback([])
                    continue

                # ---- Write ----
                try:
                    sp.write((command.strip() + "\n").encode("ascii", errors="ignore"))
                    sp.flush()
                except (serial.SerialException, serial.SerialTimeoutException, OSError) as e:
                    print(f"[Serial] ⚠ Serial write error: {e}")
                    try:
                        sp.close()
                    except Exception:
                        pass
                    serial_port = None
                    _connected_event.clear()
                    if callback:
                        callback([])
                    continue

                # ---- Let responses accumulate briefly ----
                time.sleep(float(getattr(Config, "SERIAL_RESPONSE_SETTLE_S", 0.05)))

                # ---- Read window ----
                lines: List[str] = []
                start = time.time()
                read_window = float(getattr(Config, "SERIAL_READ_WINDOW_S", 0.5))
                while (time.time() - start) < read_window:
                    try:
                        if sp.in_waiting > 0:
                            raw = sp.readline()
                            if raw:
                                txt = raw.decode(errors="ignore").strip()
                                if txt:
                                    lines.append(txt)
                                    # Extend the quiet window if we keep receiving
                                    start = time.time()
                        else:
                            time.sleep(0.01)
                    except (serial.SerialException, OSError) as e:
                        print(f"[Serial] ⚠ Serial read error: {e}")
                        try:
                            sp.close()
                        except Exception:
                            pass
                        serial_port = None
                        _connected_event.clear()
                        lines = []
                        break

            # Callback outside the lock
            if callback:
                callback(lines)

        finally:
            _command_queue.task_done()


def _reconnect_loop() -> None:
    """Reconnect loop similar to your other project."""
    while True:
        sp = serial_port
        if not (sp and sp.is_open):
            print("[Serial] Attempting to reconnect...")
            try:
                connect_serial()
            except Exception as e:
                print(f"[Serial] Reconnect error: {e}")
        time.sleep(float(getattr(Config, "SERIAL_RECONNECT_PERIOD_S", 3.0)))


def start_serial() -> None:
    """Start worker + reconnect threads once, then connect if needed."""
    if not any(t.name == "serial-queue" and t.is_alive() for t in threading.enumerate()):
        threading.Thread(target=_process_queue, daemon=True, name="serial-queue").start()
    if not any(t.name == "serial-reconnect" and t.is_alive() for t in threading.enumerate()):
        threading.Thread(target=_reconnect_loop, daemon=True, name="serial-reconnect").start()

    sp = serial_port
    if not (sp and sp.is_open):
        connect_serial()


# =============================================================================
# Public API
# =============================================================================
def send_gcode(command: str, timeout: float = 3.0) -> List[str]:
    """
    Enqueue a G-code (serialized write + small read window) and wait for its response.
    Mirrors the working project's behavior.
    """
    sp = serial_port
    if not (sp and sp.is_open):
        print("[Serial] ⚠ No connection. Cannot send G-code.")
        return []

    done = threading.Event()
    out: List[str] = []

    def cb(lines: List[str]):
        out.extend(lines)
        done.set()

    _command_queue.put((command, cb))
    if not done.wait(timeout):
        print(f"[Serial] ⚠ Timeout waiting for '{command}'")
    return out


def send_now(command: str) -> bool:
    """
    Immediate fire-and-forget write (no read). Safe to spam from UI.
    Uses a separate lock so it won't starve the queue worker.
    """
    global serial_port
    try:
        sp = serial_port
        if sp and sp.is_open:
            with send_now_lock:
                sp.write((command.strip() + "\n").encode("ascii", errors="ignore"))
                sp.flush()
            return True
        print("[Serial] ⚠ Cannot send_now(): not connected.")
        return False
    except (serial.SerialException, serial.SerialTimeoutException, OSError) as e:
        print(f"[Serial] ⚠ send_now error: {e}")
        try:
            sp.close()
        except Exception:
            pass
        serial_port = None
        _connected_event.clear()
        return False


def wait_for_motion_complete(timeout: float = 10.0) -> bool:
    """
    Issue M400 and watch for 'ok' in the incoming stream.
    Kept simple—like the stable project. We clear stale input first to avoid false positives.
    """
    sp = serial_port
    if not (sp and sp.is_open):
        print("[Serial] ⚠ Cannot M400: not connected.")
        return False

    # Clear stale bytes BEFORE sending M400
    try:
        sp.reset_input_buffer()
    except Exception:
        pass

    if not send_now("M400"):
        return False

    start = time.time()
    while time.time() - start < timeout:
        sp = serial_port
        if sp and sp.in_waiting:
            try:
                line = sp.readline().decode(errors="ignore").strip()
                # print("[M400] ←", line)  # enable for debugging
                if "ok" in line.lower():
                    return True
            except Exception:
                # Port died mid-wait
                return False
        time.sleep(0.01)
    print("[Serial] ⚠ Timeout waiting for M400 ok")
    return False


def get_position() -> List[str]:
    """Return raw M114 response lines (firmware-dependent)."""
    return send_gcode("M114")


def get_position_axis(axis: str) -> Optional[float]:
    """
    Extract a single axis value from M114 output. Handle 'X:0.00' and 'X 0.00' variants.
    """
    lines = get_position()
    joined = " ".join(lines)
    ax = axis.upper()
    m = re.search(rf"\b{ax}\s*:\s*(-?\d+(?:\.\d+)?)", joined, flags=re.IGNORECASE)
    if not m:
        m = re.search(rf"\b{ax}\s+(-?\d+(?:\.\d+)?)", joined, flags=re.IGNORECASE)
        if not m:
            return None
    try:
        return float(m.group(1))
    except Exception:
        return None
