# app/config.py
import os
import sys
from pathlib import Path
from dataclasses import dataclass


def _detect_base_dir() -> Path:
    """
    Choose a base directory that works both:
      - in development (source tree),
      - and in a frozen EXE (e.g., PyInstaller, cx_Freeze).
    """
    if getattr(sys, "frozen", False):  # Running as bundled EXE
        # sys._MEIPASS is the temp dir with bundled resources
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path.cwd()))
        # put writable data next to the executable (not inside _MEIPASS)
        exe_dir = Path(sys.executable).resolve().parent
        return exe_dir
    # dev: use repo root = folder containing this /app
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> Path:
    """
    Get a path to a bundled/read-only resource (e.g. templates, DLLs, DICOM template).
    In frozen mode, prefer sys._MEIPASS; otherwise use repo paths.
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
    # optionally: match by VID/PID if needed in the future


class Config:
    """
    Centralized, environment-overridable configuration.
    Import anywhere:   from app.config import Config, resource_path
    """

    # ---------------- Paths (exe & dev friendly) ----------------
    BASE_DIR: Path = _detect_base_dir()
    APP_DIR: Path = (BASE_DIR).resolve()
    STATIC_DIR: Path = (APP_DIR / "static").resolve()
    TEMPLATES_DIR: Path = (APP_DIR / "templates").resolve()

    # Data folders (writable). Keep under BASE_DIR/static/data to align with your current app.
    DATA_DIR: Path = (STATIC_DIR / "data").resolve()
    LOGS_DIR: Path = (DATA_DIR / "logs").resolve()

    # Create if missing (safe to call on import)
    for p in (DATA_DIR, LOGS_DIR):
        p.mkdir(parents=True, exist_ok=True)

    # Security
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-key-change-in-production")

    # ---------------- Scanner / Geometry ----------------
    # Physical travel limits (mm)
    X_MAX: float = float(os.environ.get("X_MAX", 118))
    Y_MAX: float = float(os.environ.get("Y_MAX", 118))
    Z_MAX: float = float(os.environ.get("Z_MAX", 160))

    # Offsets to align probe vs nozzle (mm)
    OFFSET_X: float = float(os.environ.get("OFFSET_X", -5.5))
    OFFSET_Y: float = float(os.environ.get("OFFSET_Y", -5.5))
    OFFSET_Z: float = float(os.environ.get("OFFSET_Z", -70.0))

    # ---------------- Feedrates / speeds ----------------
    # Primary scan speed in mm/min (used only for automated ScanPath; unchanged)
    SCAN_SPEED_MM_PER_MIN: float = float(os.environ.get("SCAN_SPEED", 90))
    # Quick move / init feedrate (used by go2StartScan and init; unchanged)
    FAST_FEED_MM_PER_MIN: float = float(os.environ.get("FAST_FEED", 20 * 60))  # 20 mm/s -> 1200 mm/min
    # >>> ONLY NEW/CHANGED LINE: dedicated manual jog feedrate for GUI X/Y/Z moves <<<
    JOG_FEED_MM_PER_MIN: float = float(os.environ.get("JOG_FEED", 2400))       # ~40 mm/s for snappy manual nudges

    # E-axis (nozzle rotation) defaults
    E_AXIS_DEFAULT_STEP: float = float(os.environ.get("E_AXIS_STEP", 0.1))
    E_AXIS_ALLOW_COLD_EXTRUSION: bool = os.environ.get("E_AXIS_COLD", "1") == "1"

    # ---------------- Ultrasound / Acquisition ----------------
    # Live preview frame size (ultrasound.py uses 1024x1024)
    ULTRA_W: int = int(os.environ.get("ULTRASOUND_WIDTH", 1024))
    ULTRA_H: int = int(os.environ.get("ULTRASOUND_HEIGHT", 1024))

    # Record-time parameters (from record.py)
    # travel_speed_x: probe speed along X during capture (mm/s)
    TRAVEL_SPEED_X_MM_PER_S: float = float(os.environ.get("TRAVEL_SPEED_X", 500.0))  # ~5 mm/s (kept as-is)
    # elevation resolution target (mm)
    ELEV_RESOLUTION_MM: float = float(os.environ.get("ELEV_RESOLUTION", 0.06))
    # total X span for sampling (mm)
    DX_MM: float = float(os.environ.get("DX_MM", 118))
    # target frame rate (Hz)
    TARGET_FPS: float = float(os.environ.get("TARGET_FPS", 25))

    # Flags persisted in tiny files (compatible with your current workflow)
    SCANNING_FLAG_FILE: Path = (BASE_DIR / "scanning").resolve()
    MULTISWEEP_FLAG_FILE: Path = (BASE_DIR / "multisweep").resolve()
    RECDIR_FILE: Path = (BASE_DIR / "recdir").resolve()

    # ---------------- Serial / Printer ----------------
    SERIAL_BAUD: int = int(os.environ.get("SERIAL_BAUD", 115200))
    SERIAL_TIMEOUT_S: float = float(os.environ.get("SERIAL_TIMEOUT", 1.0))
    SERIAL_PROFILE: SerialProfile = SerialProfile()
    # If you want to hard-force a specific COM port (e.g., for lab PC), set env SERIAL_PORT.
    SERIAL_PORT: str | None = os.environ.get("SERIAL_PORT") or None

    # Background manager timing
    SERIAL_RECONNECT_PERIOD_S: float = float(os.environ.get("SERIAL_RECONNECT_PERIOD", 3.0))
    SERIAL_RESPONSE_SETTLE_S: float = float(os.environ.get("SERIAL_RESPONSE_SETTLE", 0.05))
    SERIAL_READ_WINDOW_S: float = float(os.environ.get("SERIAL_READ_WINDOW", 0.5))

    # ---------------- DLL / Ultrasound SDK ----------------
    # Name of the wrapper DLL shipped with the app
    US_DLL_NAME: str = os.environ.get("US_DLL_NAME", "usgfw2wrapper.dll")
    # Path where the DICOM template lives (dcmimage.dcm in your repo)
    DICOM_TEMPLATE_NAME: str = os.environ.get("DICOM_TEMPLATE_NAME", "dcmimage.dcm")

    # ---------------- DICOM defaults ----------------
    PATIENT_ID: str = os.environ.get("PATIENT_ID", "3SONIC001")
    STUDY_DESC: str = os.environ.get("STUDY_DESC", "Ultrasound Volume")
    WINDOW_CENTER: int = int(os.environ.get("DICOM_WINDOW_CENTER", 0))
    WINDOW_WIDTH: int = int(os.environ.get("DICOM_WINDOW_WIDTH", 1000))
    BITS_ALLOCATED: int = int(os.environ.get("DICOM_BITS_ALLOCATED", 16))  # imconv uses 16-bit series
    BITS_STORED: int = int(os.environ.get("DICOM_BITS_STORED", 16))
    HIGH_BIT: int = int(os.environ.get("DICOM_HIGH_BIT", 15))
    PHOTOMETRIC: str = os.environ.get("DICOM_PHOTOMETRIC", "MONOCHROME2")
    RESCALE_INTERCEPT: int = int(os.environ.get("DICOM_RS_INTERCEPT", -1024))
    RESCALE_SLOPE: int = int(os.environ.get("DICOM_RS_SLOPE", 1))
    RESCALE_TYPE: str = os.environ.get("DICOM_RS_TYPE", "HU")

    # ---------------- UI / Timings ----------------
    # Delays used in original flows (e.g., wait before record starts)
    DELAY_BEFORE_RECORD_S: float = float(os.environ.get("DELAY_BEFORE_RECORD", 9.0))

    # ---------------- Helpers ----------------
    @staticmethod
    def ensure_measurement_dir() -> Path:
        """
        Create and return a timestamped measurement directory under DATA/.
        (mirrors your record.py behavior but centralized)
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
