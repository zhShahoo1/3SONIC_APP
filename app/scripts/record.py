# app/scripts/record.py
from __future__ import annotations

"""
3SONIC — Recording script
-------------------------
- Behavior identical to the original: same file layout, loop logic, and DLL calls.
- All tunables (sizes, speeds, sampling, paths) come from app.config.Config.
- Safe to run as:
    python -m app.scripts.record
    python app/scripts/record.py
"""

# ── allow running as a module or a script ─────────────────────────────────────
import sys
from pathlib import Path

if __package__ in (None, ""):
    THIS_FILE = Path(__file__).resolve()
    PROJECT_ROOT = THIS_FILE.parents[2]  # .../<project-root>
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
# ─────────────────────────────────────────────────────────────────────────────

import ctypes
from ctypes import cdll, c_float, c_uint32, pointer
import os
import time
from datetime import datetime
import numpy as np
import subprocess as sp
import platform

from app.config import Config
from app.core import scanner_control as pssc


def main(argv: list[str]) -> int:
    # ── session timing/flags (same semantics as before) ───────────────────────
    start_time = time.time()

    try:
        Config.SCANNING_FLAG_FILE.write_text("1")
    except Exception:
        pass

    # ── paths ─────────────────────────────────────────────────────────────────
    scripts_dir = Path(__file__).resolve().parent    # .../app/scripts
    data_root = Config.DATA_DIR                      # .../<project-root>/static/data
    print("My path: ", str(scripts_dir) + os.sep)

    ts_epoch = int(time.time())
    ts_str = datetime.fromtimestamp(ts_epoch).strftime("%Y%m%d_%H%M%S")
    print(ts_str, ts_epoch)

    measurement_dir = data_root / ts_str
    os.makedirs(measurement_dir, exist_ok=True)

    # Also write the most-recent directory path like the original flow
    try:
        Config.RECDIR_FILE.write_text(str(measurement_dir))
    except Exception:
        pass

    # ── recording parameters (read from Config; unchanged logic) ──────────────
    # If you added Config.RECORD_W/RECORD_H they will be used; otherwise ULTRA_*.
    w: int = int(getattr(Config, "RECORD_W", Config.ULTRA_W))
    h: int = int(getattr(Config, "RECORD_H", Config.ULTRA_H))

    travel_speed_x: float = Config.TRAVEL_SPEED_X_MM_PER_S      # mm/s
    elev_resolution_mm: float = Config.ELEV_RESOLUTION_MM       # mm (e_r)
    dx_mm: float = Config.DX_MM                                 # mm span on X
    frame_rate_aim: float = Config.TARGET_FPS                   # Hz

    # Derived sampling values (identical math)
    sample_time_ms = 1000.0 / frame_rate_aim
    time_interval_sampling_actual = sample_time_ms
    time_interval_sampling = elev_resolution_mm / travel_speed_x
    min_required_frames = int(round(dx_mm / elev_resolution_mm))
    correction_factor_sampling = time_interval_sampling / time_interval_sampling_actual
    total_samples = min_required_frames
    n_samples = int(round(total_samples))

    host_name = platform.node()

    # ── capture current position string (first line like original) ────────────
        # ── capture current position string (prefer env from main.py) ─────────────
    position_line = os.environ.get("REC_POSITION_STR", "").strip()

    if not position_line:
        # fallback: try asking the controller here (may be unavailable)
        try:
            raw = pssc.get_position()
            if isinstance(raw, (list, tuple)) and raw:
                position_line = str(raw[0]).split("\n")[0]
            elif isinstance(raw, str):
                position_line = raw.split("\n")[0]
        except Exception:
            position_line = ""

    if not position_line:
        # last-resort per-axis read to avoid blank field
        try:
            x = pssc.get_position_axis("X")
            y = pssc.get_position_axis("Y")
            z = pssc.get_position_axis("Z")
            if None not in (x, y, z):
                position_line = f"X{float(x):.3f} Y{float(y):.3f} Z{float(z):.3f}"
        except Exception:
            position_line = ""


    # ── write config.txt (same keys/format/order as original) ─────────────────
    cfg_path = measurement_dir / "config.txt"
    with cfg_path.open("a", encoding="utf-8") as f:
        f.write("%s:%s;\n" % ("W", w))
        f.write("%s:%s;\n" % ("H", h))
        f.write("%s:%s;\n" % ("e_r setpoint", elev_resolution_mm))
        f.write("%s:%s;\n" % ("dx", dx_mm))
        f.write("%s:%s;\n" % ("total_samples", total_samples))
        f.write("%s:%s;\n" % ("frame_rate_aim", frame_rate_aim))
        f.write("%s:%s;\n" % ("delay at SS", int(Config.DELAY_BEFORE_RECORD_S)))   # unchanged label
        f.write("%s:%s;\n" % ("scan speed ", int(Config.SCAN_SPEED_MM_PER_MIN)))   # unchanged label (trailing space)
        f.write("%s:%s;\n" % ("ID ", measurement_dir.name))
        try:
            f.write("%s:%s;\n" % ("POSTIONS ", position_line.split("\n")[0]))      # original key spelling kept
        except Exception:
            f.write("%s:%s;\n" % ("POSTIONS ", ""))
        f.write("%s:%s;\n" % ("COMPUTER ID ", host_name))
        f.write("%s:%s;\n" % ("Start Time ", start_time))

    print("min frames", min_required_frames)
    print("time_interval_sampling", time_interval_sampling)
    print("correction_factor_sampling", correction_factor_sampling)
    print("Sampling: ", total_samples)

    # ── DLL load & init (unchanged behavior; just uses Config.dll_path()) ─────
    dll_path = str(Config.dll_path())
    usgfw2 = cdll.LoadLibrary(dll_path)
    usgfw2.on_init()
    ERR = usgfw2.init_ultrasound_usgfw2()

    if ERR == 2:
        print("Main Usgfw2 library object not created")
        usgfw2.Close_and_release()
        sys.exit(1)

    ERR = usgfw2.find_connected_probe()
    if ERR != 101:
        print("Probe not detected")
        usgfw2.Close_and_release()
        sys.exit(1)

    ERR = usgfw2.data_view_function()
    if ERR < 0:
        print("Main ultrasound scanning object for selected probe not created")
        sys.exit(1)

    ERR = usgfw2.mixer_control_function(0, 0, w, h, 0, 0, 0)
    if ERR < 0:
        print("B mixer control not returned")
        sys.exit(1)

    # Query initial resolution (same)
    res_X = c_float(0.0)
    res_Y = c_float(0.0)
    usgfw2.get_resolution(pointer(res_X), pointer(res_Y))
    old_resolution_x = res_X.value
    old_resolution_y = res_Y.value
    print(old_resolution_y, old_resolution_x)

    # Pre-allocate and append these two to config.txt (as before)
    p_array = (c_uint32 * w * h * 4)()
    t_list: list[float] = []
    with (measurement_dir / "config.txt").open("a", encoding="utf-8") as f:
        f.write("%s:%s;\n" % ("Xres", old_resolution_x))
        f.write("%s:%s;\n" % ("Yres", old_resolution_y))

    # ── acquisition loop (identical logic) ────────────────────────────────────
    for i in range(0, n_samples):
        loop_start = time.time()

        usgfw2.return_pixel_values(pointer(p_array))  # Get pixels

        buffer_as_numpy_array = np.frombuffer(p_array, np.uint32)
        reshaped_array = np.reshape(buffer_as_numpy_array, (w, h, 4))

        usgfw2.get_resolution(pointer(res_X), pointer(res_Y))  # Get resolution

        # save first channel as uint32 .npy in measurement_directory
        np.save(str(measurement_dir / f"{i}"), reshaped_array[:, :, 0].astype(np.uint32))

        if i % 10 == 0:
            print(i)

        endtime = time.time()
        deltatime_ms = (endtime - loop_start) * 1000.0  # ms
        try:
            time.sleep((sample_time_ms - deltatime_ms) / 1000.0)
        except ValueError:
            # if negative, skip sleeping
            pass

        t_list.append((time.time() - loop_start) * 1000.0)

    # ── tear down (unchanged) ─────────────────────────────────────────────────
    usgfw2.Freeze_ultrasound_scanning()
    usgfw2.Stop_ultrasound_scanning()
    try:
        usgfw2.Close_and_release()
    except Exception:
        pass

    # Start imconv as a separate process (same behavior)
    try:
        sp.Popen([sys.executable, "-m", "app.scripts.imconv"], cwd=str(Config.BASE_DIR))
    except Exception as e:
        print(f"[record] ⚠ failed to spawn imconv: {e}")

    # Simple stats (unchanged)
    try:
        t_arr = np.array(t_list, dtype=float)
        print(np.mean(t_arr), np.std(t_arr))
    except Exception:
        pass

    try:
        del usgfw2
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
