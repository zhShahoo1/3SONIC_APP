# app/scripts/record.py
from __future__ import annotations

# ── allow running as:
#    python -m app.scripts.record [0|1]
#    python app/scripts/record.py [0|1]
import sys
from pathlib import Path

if __package__ in (None, ""):
    THIS_FILE = Path(__file__).resolve()
    PROJECT_ROOT = THIS_FILE.parents[2]  # .../<project-root>
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
# ─────────────────────────────────────────────────────────────

import ctypes
from ctypes import cdll, c_float, c_uint32, pointer
import os
import time
from datetime import datetime
import numpy as np
import subprocess as sp
import platform

# --- new app imports (paths only; behavior stays the same)
from app.config import Config
from app.core import scanner_control as pssc


def main(argv: list[str]) -> int:
    # identical vars to original
    start_time = time.time()

    # scanning flag (write to project-root/scanning)
    try:
        Config.SCANNING_FLAG_FILE.write_text("1")
    except Exception:
        pass

    # Paths
    scripts_dir = Path(__file__).resolve().parent              # .../app/scripts
    app_dir = scripts_dir.parent                               # .../app
    static_data_dir = app_dir / "static" / "data"              # .../app/static/data

    print("My path: ", str(scripts_dir) + os.sep)

    ts_measurement = int(time.time())
    ts_measurement_str = datetime.fromtimestamp(ts_measurement)\
                                 .strftime("%Y%m%d_%H%M%S")
    print(ts_measurement_str, ts_measurement)

    measurement_directory = static_data_dir / ts_measurement_str
    os.makedirs(measurement_directory, exist_ok=True)

    # configuration (kept exactly like original)
    w = 512 * 2
    h = 512 * 2
    travel_speed_x = 0.5   # mm/s scanner
    e_r = 0.06             # mm - elevation resolution required
    dx = 118               # mm - probe sampling distance - X axis - scanner
    host_name = platform.node()

    frame_rate_aim = 25  # hz
    sample_time = 1000.0 / frame_rate_aim  # Milliseconds
    time_interval_sampling_actual = sample_time
    time_interval_sampling = e_r / travel_speed_x  # Required to match e_r
    min_required_frames = int(round(dx / e_r))     # Minimum required frames to satisfy e_r regarding dx
    correction_factor_sampling = time_interval_sampling / time_interval_sampling_actual
    total_samples = min_required_frames

    # position string (same behavior: take first line of response)
    try:
        myposition = pssc.get_position()[0]
    except Exception:
        myposition = ""

    # create configuration file (kept same key order/format)
    cfg_path = measurement_directory / "config.txt"
    with cfg_path.open("a", encoding="utf-8") as ffff:
        ffff.write("%s:%s;\n" % ("W", w))
        ffff.write("%s:%s;\n" % ("H", h))
        ffff.write("%s:%s;\n" % ("e_r setpoint", e_r))
        ffff.write("%s:%s;\n" % ("dx", dx))
        ffff.write("%s:%s;\n" % ("total_samples", total_samples))
        ffff.write("%s:%s;\n" % ("frame_rate_aim", frame_rate_aim))
        ffff.write("%s:%s;\n" % ("delay at SS", 9))         # keep comment from original
        ffff.write("%s:%s;\n" % ("scan speed ", 90))        # keep comment from original
        ffff.write("%s:%s;\n" % ("ID ", measurement_directory.name))
        try:
            ffff.write("%s:%s;\n" % ("POSTIONS ", myposition.split("\n")[0]))
        except Exception:
            ffff.write("%s:%s;\n" % ("POSTIONS ", ""))
        ffff.write("%s:%s;\n" % ("COMPUTER ID ", host_name))
        ffff.write("%s:%s;\n" % ("Start Time ", start_time))

    print("min frames", min_required_frames)
    print("time_interval_sampling", time_interval_sampling)
    print("correction_factor_sampling", correction_factor_sampling)
    print("Sampling: ", total_samples)

    n_samples = int(round(total_samples))

    # write recdir file (to project root, like original semantics)
    try:
        Config.RECDIR_FILE.write_text(str(measurement_directory))
    except Exception:
        pass

    # --- DLL loading (use Config.dll_path(); same calls as original)
    dll_path = str(Config.dll_path())
    usgfw2 = cdll.LoadLibrary(dll_path)
    usgfw2.on_init()
    ERR = usgfw2.init_ultrasound_usgfw2()

    # Error handling (unchanged)
    if ERR == 2:
        print('Main Usgfw2 library object not created')
        usgfw2.Close_and_release()
        sys.exit(1)

    ERR = usgfw2.find_connected_probe()
    if ERR != 101:
        print('Probe not detected')
        usgfw2.Close_and_release()
        sys.exit(1)

    ERR = usgfw2.data_view_function()
    if ERR < 0:
        print('Main ultrasound scanning object for selected probe not created')
        sys.exit(1)

    ERR = usgfw2.mixer_control_function(0, 0, w, h, 0, 0, 0)
    if ERR < 0:
        print('B mixer control not returned')
        sys.exit(1)

    # Initialization (unchanged)
    res_X = c_float(0.0)
    res_Y = c_float(0.0)
    usgfw2.get_resolution(pointer(res_X), pointer(res_Y))

    old_resolution_x = res_X.value
    old_resolution_y = res_Y.value
    print(old_resolution_y, old_resolution_x)

    iteration = 0
    run_loop = 1
    threshold = 500
    p_array = (c_uint32 * w * h * 4)()
    t_list = []

    with (measurement_directory / "config.txt").open("a", encoding="utf-8") as ffff:
        ffff.write("%s:%s;\n" % ("Xres", old_resolution_x))
        ffff.write("%s:%s;\n" % ("Yres", old_resolution_y))

    # acquisition loop (unchanged)
    for i in range(0, n_samples):
        loop_start = time.time()
        iteration = iteration + 1

        usgfw2.return_pixel_values(pointer(p_array))  # Get pixels

        buffer_as_numpy_array = np.frombuffer(p_array, np.uint32)
        reshaped_array = np.reshape(buffer_as_numpy_array, (w, h, 4))

        usgfw2.get_resolution(pointer(res_X), pointer(res_Y))  # Get resolution

        # save first channel as uint32 .npy in measurement_directory
        np.save(str(measurement_directory / f"{i}"), reshaped_array[:, :, 0].astype(np.uint32))

        if i % 10 == 0:
            print(i)

        endtime = time.time()
        deltatime = (endtime - loop_start) * 1000.0  # ms
        try:
            time.sleep((sample_time - deltatime) / 1000.0)
        except ValueError:
            # if negative, skip sleeping
            pass

        t_list.append((time.time() - loop_start) * 1000.0)

    # tear down (unchanged)
    usgfw2.Freeze_ultrasound_scanning()
    usgfw2.Stop_ultrasound_scanning()

    try:
        usgfw2.Close_and_release()
    except Exception:
        pass

    # start imconv (path updated to module invocation; behavior same)
    try:
        sp.Popen([sys.executable, "-m", "app.scripts.imconv"], cwd=str(PROJECT_ROOT))
    except Exception as e:
        print(f"[record] ⚠ failed to spawn imconv: {e}")

    # print stats (unchanged)
    try:
        t_arr = np.array(t_list, dtype=float)
        print(np.mean(t_arr), np.std(t_arr))
    except Exception:
        pass

    # explicit delete (as original)
    try:
        del usgfw2
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
