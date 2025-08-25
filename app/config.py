# app/config.py
from __future__ import annotations

import os
import sys
from pathlib import Path
from dataclasses import dataclass


# ============================== Path helpers ==============================

def _candidate_base_dirs() -> list[Path]:
    """
    Ordered candidates for the project base directory:
      1) Dev repo root (folder that contains /app)  ← preferred in development
      2) Frozen EXE folder (next to the executable) ← preferred when frozen
      3) Current working directory                  ← last resort
    """
    here = Path(__file__).resolve()           # .../app/config.py
    dev_root = here.parent.parent             # .../project-root
    cands: list[Path] = [dev_root]

    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent)

    cands.append(Path.cwd())

    # de-dup while preserving order
    out, seen = [], set()
    for p in cands:
        s = str(p)
        if s not in seen:
            out.append(p)
            seen.add(s)
    return out


def _pick_existing(*paths: Path, fallback: Path) -> Path:
    """
    Return the first path that exists; otherwise the fallback (not created here).
    Useful to *avoid* creating app/static if project-root/static already exists.
    """
    for p in paths:
        if p.exists():
            return p.resolve()
    return fallback.resolve()


def resource_path(relative: str) -> Path:
    """
    Resolve a read-only resource (DLLs, templates, dcmimage.dcm).
    - Frozen mode: use sys._MEIPASS (the bundle temp dir)
    - Dev mode:    resolve from the repo root
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path.cwd()))
        return (base / relative).resolve()
    # dev: project root
    return (Path(__file__).resolve().parent.parent / relative).resolve()


# ================================ Serial =================================

@dataclass(frozen=True)
class SerialProfile:
    """Patterns that identify acceptable printer USB adapters."""
    description_patterns: tuple[str, ...] = (
        "USB-SERIAL", "CH340", "CH341", "USB-SERIAL CH340", "USB SERIAL",
    )
    # Optional future: match by VID/PID


# ================================ Config =================================

class Config:
    """
    Centralized, environment-overridable configuration.

    Import everywhere as:
        from app.config import Config, resource_path
    """

    # ---------------- Paths (dev & EXE friendly) ----------------
    # Allow an explicit override via env if desired
    _ENV_BASE = os.environ.get("APP_BASE_DIR")
    BASE_DIR: Path = Path(_ENV_BASE).resolve() if _ENV_BASE else _candidate_base_dirs()[0]

    # The Python package location (always the /app folder where this file lives)
    APP_DIR: Path = Path(__file__).resolve().parent.parent

    # Prefer project-root/static over app/static — do NOT create STATIC_DIR here
    _ENV_STATIC = os.environ.get("STATIC_DIR")
    STATIC_DIR: Path = Path(_ENV_STATIC).resolve() if _ENV_STATIC else _pick_existing(
        BASE_DIR / "static",           # preferred: project-root/static
        APP_DIR / "static",            # fallback: app/static (only if it already exists)
        fallback=BASE_DIR / "static",  # last fallback; created later only via DATA_DIR.mkdir
    )

    # Prefer project-root/templates over app/templates
    _ENV_TEMPLATES = os.environ.get("TEMPLATES_DIR")
    TEMPLATES_DIR: Path = Path(_ENV_TEMPLATES).resolve() if _ENV_TEMPLATES else _pick_existing(
        BASE_DIR / "templates",            # preferred: project-root/templates
        APP_DIR / "templates",             # fallback: app/templates
        fallback=BASE_DIR / "templates",
    )

    # Writable output (kept under STATIC/data). We create these (not STATIC_DIR itself).
    DATA_DIR: Path = (STATIC_DIR / "data").resolve()
    LOGS_DIR: Path = (DATA_DIR / "logs").resolve()
    for _p in (DATA_DIR, LOGS_DIR):
        _p.mkdir(parents=True, exist_ok=True)

    # Tiny flag files used by your flow (live at repo root / EXE folder)
    SCANNING_FLAG_FILE: Path = (BASE_DIR / "scanning").resolve()
    MULTISWEEP_FLAG_FILE: Path = (BASE_DIR / "multisweep").resolve()
    RECDIR_FILE: Path = (BASE_DIR / "recdir").resolve()

    # Python interpreter to spawn helper scripts (record/imconv/etc.)
    PYTHON_EXE: str = os.environ.get("PYTHON_EXE", sys.executable)

    # Security
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-key-change-in-production")

    # ---------------- Scanner / Geometry ----------------
    X_MAX: float = float(os.environ.get("X_MAX", 118))
    Y_MAX: float = float(os.environ.get("Y_MAX", 118))
    Z_MAX: float = float(os.environ.get("Z_MAX", 160))

    # Offsets to align probe vs nozzle (mm)
    OFFSET_X: float = float(os.environ.get("OFFSET_X", -5.5))
    OFFSET_Y: float = float(os.environ.get("OFFSET_Y", -5.5))
    OFFSET_Z: float = float(os.environ.get("OFFSET_Z", -70.0))

    # ---------------- Feedrates / speeds ----------------
    SCAN_SPEED_MM_PER_MIN: float = float(os.environ.get("SCAN_SPEED", 90))
    FAST_FEED_MM_PER_MIN: float = float(os.environ.get("FAST_FEED", 20 * 60))  # 1200 mm/min
    JOG_FEED_MM_PER_MIN: float = float(os.environ.get("JOG_FEED", 2400))       # ~40 mm/s

    # ---------------- Ultrasound / Live & Recording ----------------
    # Live preview size (also used as recording size unless RECORD_* set)
    ULTRA_W: int = int(os.environ.get("ULTRASOUND_WIDTH", 1024))
    ULTRA_H: int = int(os.environ.get("ULTRASOUND_HEIGHT", 1024))

    # If you want recording to use a different size than live, set these; else ULTRA_* are used
    RECORD_W: int = int(os.environ.get("RECORD_WIDTH", 0)) or ULTRA_W
    RECORD_H: int = int(os.environ.get("RECORD_HEIGHT", 0)) or ULTRA_H

    # Record-time parameters (match original logic/units)
    TRAVEL_SPEED_X_MM_PER_S: float = float(os.environ.get("TRAVEL_SPEED_X", 0.5))  # mm/s
    ELEV_RESOLUTION_MM: float = float(os.environ.get("ELEV_RESOLUTION", 0.06))     # mm
    DX_MM: float = float(os.environ.get("DX_MM", 118))                              # mm span on X
    TARGET_FPS: float = float(os.environ.get("TARGET_FPS", 25))                     # Hz

    # Ultrasound SDK auto-reinit policy (used by ultrasound_sdk if you enable it)
    US_REINIT_PING_MS: int = int(os.environ.get("US_REINIT_PING_MS", 1500))
    US_REINIT_BACKOFF_MS_START: int = int(os.environ.get("US_REINIT_BACKOFF_MS_START", 500))
    US_REINIT_BACKOFF_MS_MAX: int = int(os.environ.get("US_REINIT_BACKOFF_MS_MAX", 5000))
    US_REINIT_MAX_FAILURES: int = int(os.environ.get("US_REINIT_MAX_FAILURES", 10))

    # ---------------- Serial / Printer ----------------
    SERIAL_BAUD: int = int(os.environ.get("SERIAL_BAUD", 115200))
    SERIAL_TIMEOUT_S: float = float(os.environ.get("SERIAL_TIMEOUT", 1.0))
    SERIAL_PROFILE: SerialProfile = SerialProfile()
    SERIAL_PORT: str | None = os.environ.get("SERIAL_PORT") or None

    # Background manager timing
    SERIAL_RECONNECT_PERIOD_S: float = float(os.environ.get("SERIAL_RECONNECT_PERIOD", 3.0))
    SERIAL_RESPONSE_SETTLE_S: float = float(os.environ.get("SERIAL_RESPONSE_SETTLE", 0.05))
    SERIAL_READ_WINDOW_S: float = float(os.environ.get("SERIAL_READ_WINDOW", 0.5))

    # ---------------- DLL / Ultrasound SDK ----------------
    US_DLL_NAME: str = os.environ.get("US_DLL_NAME", "usgfw2wrapper.dll")
    DICOM_TEMPLATE_NAME: str = os.environ.get("DICOM_TEMPLATE_NAME", "dcmimage.dcm")

    # ---------------- DICOM defaults ----------------
    PATIENT_ID: str = os.environ.get("PATIENT_ID", "3SONIC001")
    STUDY_DESC: str = os.environ.get("STUDY_DESC", "Ultrasound Volume")
    WINDOW_CENTER: int = int(os.environ.get("DICOM_WINDOW_CENTER", 0))
    WINDOW_WIDTH: int = int(os.environ.get("DICOM_WINDOW_WIDTH", 1000))
    BITS_ALLOCATED: int = int(os.environ.get("DICOM_BITS_ALLOCATED", 16))
    BITS_STORED: int = int(os.environ.get("DICOM_BITS_STORED", 16))
    HIGH_BIT: int = int(os.environ.get("DICOM_HIGH_BIT", 15))
    PHOTOMETRIC: str = os.environ.get("DICOM_PHOTOMETRIC", "MONOCHROME2")
    RESCALE_INTERCEPT: int = int(os.environ.get("DICOM_RS_INTERCEPT", -1024))
    RESCALE_SLOPE: int = int(os.environ.get("DICOM_RS_SLOPE", 1))
    RESCALE_TYPE: str = os.environ.get("DICOM_RS_TYPE", "HU")

    # ---------------- UI / Timings ----------------
    DELAY_BEFORE_RECORD_S: float = float(os.environ.get("DELAY_BEFORE_RECORD", 9.0))

    # Frontend stream reload hints (used by JS; not injected automatically)
    FRONTEND_US_RELOAD_MIN_MS: int = int(os.environ.get("FRONTEND_US_RELOAD_MIN_MS", 1500))
    FRONTEND_US_RELOAD_PERIOD_MS: int = int(os.environ.get("FRONTEND_US_RELOAD_PERIOD_MS", 60000))

    # Desktop window title (pywebview)
    UI_TITLE: str = os.environ.get("UI_TITLE", "3SONIC 3D Ultrasound app")

    # Exit timing (used by graceful shutdown to let HTTP 200 flush)
    EXIT_GRACE_DELAY_MS: int = int(os.environ.get("EXIT_GRACE_DELAY_MS", 300))

    # ---------------- Service / scan positioning (Insert-Bath button) --------
    TARGET_Z_MM: float = float(os.environ.get("TARGET_Z_MM", 100.0))
    SCAN_POSE: dict[str, float] = {
        "X": float(os.environ.get("SCAN_POSE_X", 53.5)),
        "Y": float(os.environ.get("SCAN_POSE_Y", 53.5)),
        "Z": float(os.environ.get("SCAN_POSE_Z", 10.0)),
    }
    Z_FEED_MM_PER_MIN: int = int(os.environ.get("Z_FEED", 1500))
    XYZ_FEED_MM_PER_MIN: int = int(os.environ.get("XYZ_FEED", 2000))
    POS_TOL_MM: float = float(os.environ.get("POS_TOL_MM", 0.02))
    POLL_INTERVAL_S: float = float(os.environ.get("POLL_INTERVAL_S", 0.10))
    POLL_TIMEOUT_S: float = float(os.environ.get("POLL_TIMEOUT_S", 5.0))

    # ---------------- Helpers ----------------
    @staticmethod
    def ensure_measurement_dir() -> Path:
        """
        Create and return a timestamped measurement directory under DATA/.
        Matches record.py behavior.
        """
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        mdir = (Config.DATA_DIR / ts).resolve()
        (mdir / "frames").mkdir(parents=True, exist_ok=True)
        (mdir / "raws").mkdir(parents=True, exist_ok=True)
        (mdir / "dicom_series").mkdir(parents=True, exist_ok=True)
        return mdir

    @staticmethod
    def dll_path() -> Path:
        """Resolve path to the ultrasound DLL (bundled or source)."""
        return resource_path(Config.US_DLL_NAME)

    @staticmethod
    def dicom_template_path() -> Path:
        """Resolve path to the DICOM template file (dcmimage.dcm)."""
        return resource_path(Config.DICOM_TEMPLATE_NAME)
