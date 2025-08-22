# app/core/serial_manager.py
from __future__ import annotations

import threading
import queue
import time
import re
from typing import Callable, Optional, List

import serial
import serial.tools.list_ports

from app.config import Config

# -------------------------------------------------------------------
# Shared state (module-level singleton)
# -------------------------------------------------------------------
serial_port: Optional[serial.Serial] = None

# Internal event; all logic uses this one
_connected_event = threading.Event()

# Export a FUNCTION for legacy callers that do `connected_event()`
def connected_event() -> threading.Event:
    """Return the connection Event (legacy callable API)."""
    return _connected_event

# Also export a VARIABLE alias if someone imported it as a variable
connected_event_var: threading.Event = _connected_event

# Internal locks/queues
_command_queue: "queue.Queue[tuple[str, Optional[Callable[[List[str]], None]]]]" = queue.Queue()
_write_lock = threading.Lock()
_send_now_lock = threading.Lock()


# -------------------------------------------------------------------
# Port detection + connect/close
# -------------------------------------------------------------------
def _detect_port() -> Optional[str]:
    if Config.SERIAL_PORT:
        return Config.SERIAL_PORT
    for p in serial.tools.list_ports.comports(include_links=False):
        desc = (p.description or "").upper()
        if any(pattern in desc for pattern in Config.SERIAL_PROFILE.description_patterns):
            return p.device
    return None


def connect_serial(baudrate: int = Config.SERIAL_BAUD,
                   timeout: float = Config.SERIAL_TIMEOUT_S) -> Optional[serial.Serial]:
    global serial_port
    try:
        port = _detect_port()
        if not port:
            print("[Serial] No valid serial port found.")
            _connected_event.clear()
            return None

        sp = serial.Serial(port, baudrate, timeout=timeout)
        time.sleep(2.0)  # allow MCU reset
        with _write_lock:
            serial_port = sp
        _connected_event.set()
        print(f"[Serial] Connected to {port} @ {baudrate}")
        return sp
    except serial.SerialException as e:
        print(f"[Serial] Open error: {e}")
    except Exception as e:
        print(f"[Serial] Unexpected connect error: {e}")

    _connected_event.clear()
    return None


def close_serial() -> None:
    global serial_port
    with _write_lock:
        if serial_port:
            try:
                if serial_port.is_open:
                    serial_port.close()
                    print("[Serial] 🔌 Closed.")
            except Exception as e:
                print(f"[Serial] Close error: {e}")
            finally:
                serial_port = None
                _connected_event.clear()


# -------------------------------------------------------------------
# Background workers (queue + reconnect)
# -------------------------------------------------------------------
def _process_queue() -> None:
    global serial_port
    while True:
        command, callback = _command_queue.get()
        try:
            with _write_lock:
                sp = serial_port

            if not (sp and sp.is_open):
                print("[Serial] ⚠ No active serial connection.")
                if callback:
                    callback([])
                continue

            try:
                sp.write((command.strip() + "\n").encode("ascii", errors="ignore"))
                sp.flush()
                time.sleep(Config.SERIAL_RESPONSE_SETTLE_S)

                lines: List[str] = []
                start = time.time()
                while True:
                    got = False
                    if sp.in_waiting:
                        try:
                            raw = sp.readline()
                            if raw:
                                txt = raw.decode(errors="ignore").strip()
                                if txt:
                                    lines.append(txt)
                                    got = True
                        except Exception:
                            pass
                    if got:
                        start = time.time()
                    else:
                        time.sleep(0.01)
                    if (time.time() - start) >= Config.SERIAL_READ_WINDOW_S:
                        break

                if callback:
                    callback(lines)

            except serial.SerialException as e:
                print(f"[Serial] write/read error: {e}")
                _connected_event.clear()
                with _write_lock:
                    serial_port = None

        finally:
            _command_queue.task_done()


def _reconnect_loop() -> None:
    while True:
        with _write_lock:
            sp = serial_port
        if not (sp and sp.is_open):
            print("[Serial] Attempting to reconnect...")
            try:
                connect_serial()
            except Exception as e:
                print(f"[Serial] Reconnect error: {e}")
        time.sleep(Config.SERIAL_RECONNECT_PERIOD_S)


def start_serial() -> None:
    if not any(t.name == "serial-queue" and t.is_alive() for t in threading.enumerate()):
        threading.Thread(target=_process_queue, daemon=True, name="serial-queue").start()
    if not any(t.name == "serial-reconnect" and t.is_alive() for t in threading.enumerate()):
        threading.Thread(target=_reconnect_loop, daemon=True, name="serial-reconnect").start()
    with _write_lock:
        need_connect = not (serial_port and serial_port.is_open)
    if need_connect:
        connect_serial()


# -------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------
def send_gcode(command: str, timeout: float = 3.0) -> List[str]:
    with _write_lock:
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
    with _write_lock:
        sp = serial_port
    try:
        if sp and sp.is_open:
            with _send_now_lock:
                sp.write((command.strip() + "\n").encode("ascii", errors="ignore"))
                sp.flush()
            return True
        print("[Serial] ⚠ Cannot send_now(): not connected.")
        return False
    except (serial.SerialException, OSError) as e:
        print(f"[Serial] send_now error: {e}")
        _connected_event.clear()
        close_serial()
        return False


def wait_for_motion_complete(timeout: float = 10.0) -> bool:
    if not send_now("M400"):
        return False

    t0 = time.time()
    while (time.time() - t0) < timeout:
        with _write_lock:
            sp = serial_port
        if sp and sp.in_waiting:
            try:
                line = sp.readline().decode(errors="ignore").strip().lower()
                if line and "ok" in line:
                    return True
            except Exception:
                pass
        time.sleep(0.01)

    print("[Serial] ⚠ Timeout waiting for M400 ok")
    return False


def get_position() -> List[str]:
    return send_gcode("M114")


def get_position_axis(axis: str) -> Optional[float]:
    lines = get_position()
    joined = " ".join(lines)
    axis = axis.upper()
    m = re.search(rf"\b{axis}:\s*(-?\d+\.?\d*)", joined)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None
