# app/config.py
from __future__ import annotations

import os
import sys
from pathlib import Path
from dataclasses import dataclass


def _detect_base_dir() -> Path:
    """
    Choose a base directory that works in both:
      - development (source tree)
      - frozen EXE (PyInstaller/cx_Freeze)
    In frozen mode we place writable content next to the EXE (NOT _MEIPASS).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # dev: repo root (folder that contains /app)
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> Path:
    """
    Resolve a path to a bundled/read-only resource (DLLs, templates, dcmimage.dcm).
    In frozen mode, prefer sys._MEIPASS (bundle temp dir). In dev, use repo paths.
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path.cwd()))
        return (base / relative).resolve()
    return (Path(__file__).resolve().parent.parent / relative).resolve()


@dataclass(frozen=True)
class SerialProfile:
    """Patterns that identify acceptable printer USB adapters."""
    description_patterns: tuple[str, ...] = (
        "USB-SERIAL", "CH340", "CH341", "USB-SERIAL CH340", "USB SERIAL",
    )
    # Optional future: match by VID/PID


class Config:
    """
    Centralized, environment‑overridable configuration.

    Import everywhere as:
        from app.config import Config, resource_path
    """

    # ---------------- Paths (dev & EXE friendly) ----------------
    BASE_DIR: Path = _detect_base_dir()                 # repo root in dev; EXE folder when frozen
    APP_DIR: Path = (BASE_DIR / "app").resolve() if (BASE_DIR / "app").exists() else BASE_DIR
    STATIC_DIR: Path = (BASE_DIR / "static").resolve()  # we bundle static -> dist/<name>/static
    TEMPLATES_DIR: Path = (BASE_DIR / "templates").resolve()

    # Writable output (keep under static/data to match your current files/links)
    DATA_DIR: Path = (STATIC_DIR / "data").resolve()
    LOGS_DIR: Path = (DATA_DIR / "logs").resolve()

    # Create if missing
    for _p in (DATA_DIR, LOGS_DIR):
        _p.mkdir(parents=True, exist_ok=True)

    # Tiny flag files used by your flow
    SCANNING_FLAG_FILE: Path = (BASE_DIR / "scanning").resolve()
    MULTISWEEP_FLAG_FILE: Path = (BASE_DIR / "multisweep").resolve()
    RECDIR_FILE: Path = (BASE_DIR / "recdir").resolve()

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
    # Automated scan path speed (leave as-is)
    SCAN_SPEED_MM_PER_MIN: float = float(os.environ.get("SCAN_SPEED", 90))
    # Fast feed for init / go2StartScan (leave as-is)
    FAST_FEED_MM_PER_MIN: float = float(os.environ.get("FAST_FEED", 20 * 60))  # 20 mm/s -> 1200 mm/min
    # Dedicated manual jog feed for GUI X/Y/Z moves (snappy but safe)
    JOG_FEED_MM_PER_MIN: float = float(os.environ.get("JOG_FEED", 2400))       # ~40 mm/s

    # ---------------- Ultrasound / Acquisition ----------------
    # Live preview size
    ULTRA_W: int = int(os.environ.get("ULTRASOUND_WIDTH", 1024))
    ULTRA_H: int = int(os.environ.get("ULTRASOUND_HEIGHT", 1024))

    # Record-time parameters (kept consistent with your original)
    TRAVEL_SPEED_X_MM_PER_S: float = float(os.environ.get("TRAVEL_SPEED_X", 500.0))  # ~5 mm/s
    ELEV_RESOLUTION_MM: float = float(os.environ.get("ELEV_RESOLUTION", 0.06))
    DX_MM: float = float(os.environ.get("DX_MM", 118))
    TARGET_FPS: float = float(os.environ.get("TARGET_FPS", 25))

    # ---------------- Serial / Printer ----------------
    SERIAL_BAUD: int = int(os.environ.get("SERIAL_BAUD", 115200))
    SERIAL_TIMEOUT_S: float = float(os.environ.get("SERIAL_TIMEOUT", 1.0))
    SERIAL_PROFILE: SerialProfile = SerialProfile()
    # Optionally pin a COM port via env
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
    BITS_ALLOCATED: int = int(os.environ.get("DICOM_BITS_ALLOCATED", 16))  # 16-bit series
    BITS_STORED: int = int(os.environ.get("DICOM_BITS_STORED", 16))
    HIGH_BIT: int = int(os.environ.get("DICOM_HIGH_BIT", 15))
    PHOTOMETRIC: str = os.environ.get("DICOM_PHOTOMETRIC", "MONOCHROME2")
    RESCALE_INTERCEPT: int = int(os.environ.get("DICOM_RS_INTERCEPT", -1024))
    RESCALE_SLOPE: int = int(os.environ.get("DICOM_RS_SLOPE", 1))
    RESCALE_TYPE: str = os.environ.get("DICOM_RS_TYPE", "HU")

    # ---------------- UI / Timings ----------------
    DELAY_BEFORE_RECORD_S: float = float(os.environ.get("DELAY_BEFORE_RECORD", 9.0))

    # ---------------- Service / scan positioning (for the dynamic Insert‑Bath button) ----
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
        Matches your record.py behavior.
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
        """Resolve path to ultrasound DLL, bundled or source."""
        return resource_path(Config.US_DLL_NAME)

    @staticmethod
    def dicom_template_path() -> Path:
        """Resolve path to the DICOM template file (dcmimage.dcm)."""
        return resource_path(Config.DICOM_TEMPLATE_NAME)
