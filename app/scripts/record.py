# app/scripts/record.py
from __future__ import annotations

"""
3SONIC — Recording script (scan-path aware & dynamic frames)
- Uses operator-selected X range (scanplan.json or env: SCAN_X0/SCAN_X1 or SCAN_START_X/SCAN_END_X).
- Frames = ceil(dx / e_r_effective). e_r_effective = ELEV_RESOLUTION_MM,
  or (TRAVEL_SPEED_X_MM_PER_S / TARGET_FPS) if ELEV_RESOLUTION_MM <= 0.
- Keeps original DLL calls, file layout, and config.txt order.
- Stops cleanly if app exits (checks SCANNING flag / signals).
"""

# ── allow running as a module or a script ─────────────────────────────────────
import sys
from pathlib import Path

if __package__ in (None, ""):
    THIS_FILE = Path(__file__).resolve()
    PROJECT_ROOT = THIS_FILE.parents[2]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
# ─────────────────────────────────────────────────────────────────────────────

import ctypes
from ctypes import cdll, c_float, c_uint32, pointer
import os
import time
import json
import math
import signal
from datetime import datetime
import numpy as np
import subprocess as sp
import platform

from app.config import Config
from app.core import scanner_control as pssc


# ------------------------- scan-range helpers -------------------------
def _read_scan_plan() -> tuple[float, float, str]:
    """
    Priority:
      1) Config.SCANPLAN_FILE JSON: {"x0": .., "x1": .., "mode": ...}
      2) Env pairs: (SCAN_X0, SCAN_X1) then (SCAN_START_X, SCAN_END_X)
      3) Default: 0 → X_MAX
    Returns: (x0, x1, mode_str)
    """
    x_max = float(getattr(Config, "X_MAX", 118.0))
    x0, x1, mode = 0.0, x_max, "default"

    # 1) shared file
    try:
        plan = json.loads(Config.SCANPLAN_FILE.read_text(encoding="utf-8"))
        if "x0" in plan and "x1" in plan:
            x0 = float(plan["x0"])
            x1 = float(plan["x1"])
        mode = str(plan.get("mode", mode))
    except Exception:
        pass

    # 2) env fallbacks (try both pairs)
    for A, B in (("SCAN_X0", "SCAN_X1"), ("SCAN_START_X", "SCAN_END_X")):
        sx, ex = os.environ.get(A), os.environ.get(B)
        if sx is not None and ex is not None:
            try:
                x0 = float(sx); x1 = float(ex); mode = "env"
                break
            except Exception:
                pass

    # clamp & ensure forward span
    x0 = max(0.0, min(x0, x_max))
    x1 = max(0.0, min(x1, x_max))
    if x1 <= x0:
        # minimal span to avoid zero frames
        x1 = min(x_max, x0 + 1.0)

    return x0, x1, mode


# ------------------------- stop/abort handling -------------------------
_STOP = False
def _sig_stop(_sig, _frm):
    global _STOP; _STOP = True
for s in ("SIGTERM", "SIGINT"):
    try:
        signal.signal(getattr(signal, s), _sig_stop)
    except Exception:
        pass

def _should_stop() -> bool:
    if _STOP:
        return True
    try:
        return Config.SCANNING_FLAG_FILE.read_text().strip() != "1"
    except Exception:
        return True


def main(argv: list[str]) -> int:
    # ── session timing/flags ──────────────────────────────────────────────────
    start_time = time.time()
    try:
        Config.SCANNING_FLAG_FILE.write_text("1")
    except Exception:
        pass

    # ── paths ─────────────────────────────────────────────────────────────────
    data_root = Config.DATA_DIR
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    measurement_dir = (data_root / ts_str).resolve()
    os.makedirs(measurement_dir, exist_ok=True)

    try:
        Config.RECDIR_FILE.write_text(str(measurement_dir))
    except Exception:
        pass

    # ── resolve scan path ─────────────────────────────────────────────────────
    scan_x0, scan_x1, scan_mode = _read_scan_plan()
    dx_mm = abs(scan_x1 - scan_x0)

    # ── capture current position (best effort; used in config.txt) ────────────
    position_line = os.environ.get("REC_POSITION_STR", "").strip()
    if not position_line:
        try:
            raw = pssc.get_position()
            if isinstance(raw, (list, tuple)) and raw:
                position_line = str(raw[0]).split("\n")[0]
            elif isinstance(raw, str):
                position_line = raw.split("\n")[0]
        except Exception:
            pass
    if not position_line:
        try:
            x = pssc.get_position_axis("X")
            y = pssc.get_position_axis("Y")
            z = pssc.get_position_axis("Z")
            if None not in (x, y, z):
                position_line = f"X{float(x):.3f} Y{float(y):.3f} Z{float(z):.3f}"
        except Exception:
            position_line = ""

    # ── recording parameters ──────────────────────────────────────────────────
    w = int(getattr(Config, "RECORD_W", Config.ULTRA_W))
    h = int(getattr(Config, "RECORD_H", Config.ULTRA_H))

    fps = float(Config.TARGET_FPS)
    e_r_cfg = float(Config.ELEV_RESOLUTION_MM)              # desired mm per frame
    v_mm_s = float(Config.TRAVEL_SPEED_X_MM_PER_S)          # mm/s (informational)

    # Effective mm per frame: prefer configured ELEV_RESOLUTION_MM;
    # if not set/invalid, estimate from motion & fps.
    e_r_eff = e_r_cfg if e_r_cfg > 0 else max(1e-6, v_mm_s / max(fps, 1e-6))

    # Dynamic frame count from distance:
    n_samples = int(math.ceil(dx_mm / e_r_eff))
    n_samples = max(1, n_samples)

    # Timing pacing from fps:
    sample_time_ms = 1000.0 / max(fps, 1e-9)

    print(f"[record] X-range: {scan_x0:.3f} → {scan_x1:.3f}  (dx={dx_mm:.3f} mm)")
    print(f"[record] e_r (cfg)={e_r_cfg:.6f} mm/frame, v={v_mm_s:.3f} mm/s, fps={fps:.3f} Hz")
    print(f"[record] Using e_r_eff={e_r_eff:.6f} mm/frame → Sampling (frames): {n_samples}")

    # ── DLL load & init ───────────────────────────────────────────────────────
    dll_path = str(Config.dll_path())
    usgfw2 = cdll.LoadLibrary(dll_path)
    usgfw2.on_init()
    ERR = usgfw2.init_ultrasound_usgfw2()
    if ERR == 2:
        print("Main Usgfw2 library object not created"); usgfw2.Close_and_release(); sys.exit(1)

    ERR = usgfw2.find_connected_probe()
    if ERR != 101:
        print("Probe not detected"); usgfw2.Close_and_release(); sys.exit(1)

    ERR = usgfw2.data_view_function()
    if ERR < 0:
        print("Main ultrasound scanning object for selected probe not created"); sys.exit(1)

    ERR = usgfw2.mixer_control_function(0, 0, w, h, 0, 0, 0)
    if ERR < 0:
        print("B mixer control not returned"); sys.exit(1)

    # Query initial resolution (reported by SDK)
    res_X = c_float(0.0); res_Y = c_float(0.0)
    usgfw2.get_resolution(pointer(res_X), pointer(res_Y))
    old_resolution_x = res_X.value; old_resolution_y = res_Y.value
    print(old_resolution_y, old_resolution_x)

    # Write config.txt (original order + scan-path annotation)
    cfg_path = (measurement_dir / "config.txt").resolve()
    host_name = platform.node()
    with cfg_path.open("a", encoding="utf-8") as f:
        f.write("W:%s;\n" % w)
        f.write("H:%s;\n" % h)
        f.write("e_r setpoint:%s;\n" % e_r_cfg)
        f.write("dx:%s;\n" % dx_mm)
        f.write("total_samples:%s;\n" % n_samples)
        f.write("frame_rate_aim:%s;\n" % fps)
        f.write("delay at SS:%s;\n" % int(Config.DELAY_BEFORE_RECORD_S))
        f.write("scan speed :%s;\n" % int(Config.SCAN_SPEED_MM_PER_MIN))
        f.write("ID :%s;\n" % measurement_dir.name)
        try:
            f.write("POSTIONS :%s;\n" % (position_line.split("\n")[0]))
        except Exception:
            f.write("POSTIONS :;\n")
        f.write("COMPUTER ID :%s;\n" % host_name)
        f.write("Start Time :%s;\n" % start_time)
        # extra annotations
        f.write("SCAN_MODE:%s;\n" % scan_mode)
        f.write("X0_mm:%s;\n" % scan_x0)
        f.write("X1_mm:%s;\n" % scan_x1)
        f.write("Xres:%s;\n" % old_resolution_x)
        f.write("Yres:%s;\n" % old_resolution_y)

    # Pre-allocate pixel buffer
    p_array = (c_uint32 * w * h * 4)()
    t_list: list[float] = []

    # ── acquisition loop (distance-based trigger: Opzione A) ─────────────────
    frames_written = 0
    SAVE_TOL_FRAC = 0.10
    tol_mm = max(1e-6, SAVE_TOL_FRAC * e_r_eff)
    x_last = scan_x0  # ultimo multiplo salvato
    i = 0
    try:
        while True:
            if _should_stop():
                print("[record] stop requested — breaking")
                break

            # posizione corrente (mm) — best effort
            try:
                x_cur = float(pssc.get_position_axis("X"))
            except Exception:
               # se non leggiamo la posizione, non possiamo fare trigger spaziale
                # esci per sicurezza per non salvare frame "a tempo"
                print("[record] ⚠ no position feedback — aborting distance-based record")
                break

            # stop: raggiunto X1 (con piccola tolleranza)
            if (x_cur + tol_mm) >= scan_x1:
                print("[record] reached X1 — stopping")
                break

            # quante steps e_r abbiamo superato rispetto all'ultimo salvato?
            delta = (x_cur - x_last)
            if delta >= (e_r_eff - tol_mm):
                steps = int(math.floor((delta + tol_mm) / e_r_eff))
                for s in range(steps):
                    x_save = x_last + (s + 1) * e_r_eff
                    if x_save > (scan_x1 + tol_mm):
                        break

                    loop_start = time.time()

                    # Acquisisci pixel per QUESTO frame
                    usgfw2.return_pixel_values(pointer(p_array))
                    buf = np.frombuffer(p_array, np.uint32)
                    reshaped = np.reshape(buf, (w, h, 4))
                    usgfw2.get_resolution(pointer(res_X), pointer(res_Y))

                    # Salva canale 0
                    np.save(str(measurement_dir / f"{i}"), reshaped[:, :, 0].astype(np.uint32))
                    frames_written += 1
                    if i % 10 == 0:
                        print(i, f"@ x={x_save:.3f} mm")
                    i += 1

                    # tempi diagnostici
                    t_list.append((time.time() - loop_start) * 1000.0)

                # avanza l'ultimo multiplo salvato
                x_last += steps * e_r_eff

            # piccolo riposo per non saturare la CPU/seriale
            time.sleep(max(0.0, float(Config.POLL_INTERVAL_S)))
    finally:
        try: usgfw2.Freeze_ultrasound_scanning()
        except Exception: pass
        try: usgfw2.Stop_ultrasound_scanning()
        except Exception: pass
        try: usgfw2.Close_and_release()
        except Exception: pass

    # Spawn imconv only if we actually captured frames and not force-stopped
    if frames_written > 0 and not _STOP:
        try:
            sp.Popen([sys.executable, "-m", "app.scripts.imconv"], cwd=str(Config.BASE_DIR))
        except Exception as e:
            print(f"[record] ⚠ failed to spawn imconv: {e}")
    else:
        print("[record] imconv not started (no frames or stop).")

    # Stats + clear flag
    try:
        if t_list:
            t_arr = np.array(t_list, dtype=float)
            print(np.mean(t_arr), np.std(t_arr))
    except Exception:
        pass

    try:
        Config.SCANNING_FLAG_FILE.write_text("0")
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
