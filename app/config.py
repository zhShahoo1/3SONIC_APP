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


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    v = v.strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return float(default)


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
    _ENV_BASE = os.environ.get("APP_BASE_DIR")
    BASE_DIR: Path = Path(_ENV_BASE).resolve() if _ENV_BASE else _candidate_base_dirs()[0]

    # The Python package location (always the /app folder where this file lives)
    APP_DIR: Path = Path(__file__).resolve().parent

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

    # Orientation for LIVE ultrasound stream (does not affect saved data)
    # Back-compat: keep ULTRA_VFLIP / ULTRA_HFLIP and mirror to *_LIVE.
    ULTRA_VFLIP: bool = _env_bool("ULTRA_VFLIP", True)
    ULTRA_HFLIP: bool = _env_bool("ULTRA_HFLIP", False)
    ULTRA_VFLIP_LIVE: bool = _env_bool("ULTRA_VFLIP_LIVE", ULTRA_VFLIP)
    ULTRA_HFLIP_LIVE: bool = _env_bool("ULTRA_HFLIP_LIVE", ULTRA_HFLIP)

    # Writable output (kept under STATIC/data). We create these (not STATIC_DIR itself).
    DATA_DIR: Path = (STATIC_DIR / "data").resolve()
    LOGS_DIR: Path = (DATA_DIR / "logs").resolve()
    for _p in (DATA_DIR, LOGS_DIR):
        _p.mkdir(parents=True, exist_ok=True)

    # Runtime/state directory for flag files and ephemeral runtime artifacts.
    # Keep this separate from source so these files can be ignored by VCS.
    STATE_DIR: Path = (BASE_DIR / "run").resolve()
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # Tiny flag files used by your flow (live under `run/`)
    SCANNING_FLAG_FILE: Path = (STATE_DIR / "scanning").resolve()
    MULTISWEEP_FLAG_FILE: Path = (STATE_DIR / "multisweep").resolve()
    RECDIR_FILE: Path = (STATE_DIR / "recdir").resolve()
    SCANPLAN_FILE: Path = (STATE_DIR / "scanplan.json").resolve()

    # Python interpreter to spawn helper scripts (record/imconv/etc.)
    PYTHON_EXE: str = os.environ.get("PYTHON_EXE", sys.executable)

    # Security
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-key-change-in-production")

    # ---------------- Scanner / Geometry ----------------
    X_MAX: float = _env_float("X_MAX", 118.0)
    Y_MAX: float = _env_float("Y_MAX", 118.0)
    Z_MAX: float = _env_float("Z_MAX", 160.0)

    # Offsets to align probe vs nozzle (mm)
    OFFSET_X: float = _env_float("OFFSET_X", -5.5)
    OFFSET_Y: float = _env_float("OFFSET_Y", -5.5)
    OFFSET_Z: float = _env_float("OFFSET_Z", -70.0)

    # ---- Scan-path presets (mm) ----
    # Env overrides (optional):
    # Default E-axis step for rotation (mm). Increase for slightly faster rotation.
    E_AXIS_DEFAULT_STEP: float = _env_float("E_AXIS_DEFAULT_STEP", 0.15)
    #   SCAN_LONG_START_X / SCAN_LONG_END_X
    #   SCAN_SHORT_START_X / SCAN_SHORT_END_X
    LONG_PATH_X: tuple[float, float] = (
        _env_float("SCAN_LONG_START_X", 0.0),
        _env_float("SCAN_LONG_END_X", X_MAX),
    )
    SHORT_PATH_X: tuple[float, float] = (
        _env_float("SCAN_SHORT_START_X", 15.0),
        _env_float("SCAN_SHORT_END_X", 90.0),
    )

    # ---------------- Feedrates / speeds ----------------
    SCAN_SPEED_MM_PER_MIN: float = _env_float("SCAN_SPEED", 180.0)     # used if SCAN_FEED_FROM_ER_FPS=False
    FAST_FEED_MM_PER_MIN: float = _env_float("FAST_FEED", 20.0 * 60)  # 1200 mm/min
    JOG_FEED_MM_PER_MIN: float = _env_float("JOG_FEED", 2400.0)       # ~40 mm/s

    # Compute scan feed from e_r and FPS for motion–ultrasound sync
    # Prefer explicit SCAN_SPEED over computed e_r*FPS when set to False.
    SCAN_FEED_FROM_ER_FPS: bool = _env_bool("SCAN_FEED_FROM_ER_FPS", False)

    # ---------------- Ultrasound / Live & Recording ----------------
    # Live preview size (also used as recording size unless RECORD_* set)
    ULTRA_W: int = int(_env_float("ULTRASOUND_WIDTH", 1024))
    ULTRA_H: int = int(_env_float("ULTRASOUND_HEIGHT", 1024))

    # If you want recording to use a different size than live, set these; else ULTRA_* are used
    RECORD_W: int = int(_env_float("RECORD_WIDTH", 0)) or ULTRA_W
    RECORD_H: int = int(_env_float("RECORD_HEIGHT", 0)) or ULTRA_H

    # Record-time parameters
    TRAVEL_SPEED_X_MM_PER_S: float = _env_float("TRAVEL_SPEED_X", 0.5)  # legacy; recorder derives v = e_r * fps
    ELEV_RESOLUTION_MM: float = _env_float("ELEV_RESOLUTION", 0.06)     # e_r (mm)
    DX_MM: float = _env_float("DX_MM", 118.0)                            # default span if not overridden
    TARGET_FPS: float = _env_float("TARGET_FPS", 25.0)                   # Hz

    # Ultrasound SDK auto-reinit policy
    US_REINIT_PING_MS: int = int(_env_float("US_REINIT_PING_MS", 1500))
    US_REINIT_BACKOFF_MS_START: int = int(_env_float("US_REINIT_BACKOFF_MS_START", 500))
    US_REINIT_BACKOFF_MS_MAX: int = int(_env_float("US_REINIT_BACKOFF_MS_MAX", 5000))
    US_REINIT_MAX_FAILURES: int = int(_env_float("US_REINIT_MAX_FAILURES", 10))

    # ---------------- Serial / Printer ----------------
    SERIAL_BAUD: int = int(_env_float("SERIAL_BAUD", 115200))
    SERIAL_TIMEOUT_S: float = _env_float("SERIAL_TIMEOUT", 1.0)
    SERIAL_PROFILE: SerialProfile = SerialProfile()
    SERIAL_PORT: str | None = os.environ.get("SERIAL_PORT") or None

    # Background manager timing
    SERIAL_RECONNECT_PERIOD_S: float = _env_float("SERIAL_RECONNECT_PERIOD", 3.0)
    SERIAL_RESPONSE_SETTLE_S: float = _env_float("SERIAL_RESPONSE_SETTLE", 0.05)
    SERIAL_READ_WINDOW_S: float = _env_float("SERIAL_READ_WINDOW", 0.5)

    # ---------------- DLL / Ultrasound SDK ----------------
    # Resources moved into the `src/` folder for packaging and clarity.
    US_DLL_NAME: str = os.environ.get("US_DLL_NAME", "src/usgfw2wrapper.dll")
    DICOM_TEMPLATE_NAME: str = os.environ.get("DICOM_TEMPLATE_NAME", "src/dcmimage.dcm")

    # ---------------- DICOM defaults ----------------
    PATIENT_ID: str = os.environ.get("PATIENT_ID", "3SONIC001")
    STUDY_DESC: str = os.environ.get("STUDY_DESC", "Ultrasound Volume")
    WINDOW_CENTER: int = int(_env_float("DICOM_WINDOW_CENTER", 0))
    WINDOW_WIDTH: int = int(_env_float("DICOM_WINDOW_WIDTH", 1000))
    BITS_ALLOCATED: int = int(_env_float("DICOM_BITS_ALLOCATED", 16))
    BITS_STORED: int = int(_env_float("DICOM_BITS_STORED", 16))
    HIGH_BIT: int = int(_env_float("DICOM_HIGH_BIT", 15))
    PHOTOMETRIC: str = os.environ.get("DICOM_PHOTOMETRIC", "MONOCHROME2")
    RESCALE_INTERCEPT: int = int(_env_float("DICOM_RS_INTERCEPT", -1024))
    RESCALE_SLOPE: int = int(_env_float("DICOM_RS_SLOPE", 1))
    RESCALE_TYPE: str = os.environ.get("DICOM_RS_TYPE", "HU")

    # ---------------- UI / Timings ----------------
    DELAY_BEFORE_RECORD_S: float = _env_float("DELAY_BEFORE_RECORD", 9.0)

    # Frontend stream reload hints (used by JS; not injected automatically)
    FRONTEND_US_RELOAD_MIN_MS: int = int(_env_float("FRONTEND_US_RELOAD_MIN_MS", 1500))
    FRONTEND_US_RELOAD_PERIOD_MS: int = int(_env_float("FRONTEND_US_RELOAD_PERIOD_MS", 60000))

    # UI continuous-move defaults (used by client/server hold-to-move logic)
    # Slightly higher default for snappier UI control; still clampable by env
    # UI feedrates: separate linear-axis (X/Y/Z) defaults from rotation (E-axis)
    # Linear feed (used for X/Y/Z hold-to-move). Keep slightly faster than scan.
    # Default set a bit above the computed scan-feed (default scan ≈ 90 mm/min).
    # Increase UI linear feed for snappier X/Y/Z control
    UI_LINEAR_FEED_MM_PER_MIN: float = _env_float("UI_LINEAR_FEED_MM_PER_MIN", 360.0)
    # Rotation feed (E-axis) used for nozzle/probe rotation — keep reduced for finer control
    UI_ROTATION_FEED_MM_PER_MIN: float = _env_float("UI_ROTATION_FEED_MM_PER_MIN", 80.0)

    # Back-compat alias (some code/clients may still read UI_DEFAULT_FEED_MM_PER_MIN)
    UI_DEFAULT_FEED_MM_PER_MIN: float = float(UI_LINEAR_FEED_MM_PER_MIN)
    # Limit the maximum feed rate the UI can request for hold-to-move controls.
    # Lower default to a conservative value (safer for printer-based hardware).
    UI_MAX_FEED_MM_PER_MIN: float = _env_float("UI_MAX_FEED_MM_PER_MIN", 1200.0)
    # UI tick (seconds) used for continuous hold-to-move (higher = fewer writes)
    # Smaller tick means slightly higher command cadence — keep reasonable lower bound
    UI_DEFAULT_TICK_S: float = _env_float("UI_DEFAULT_TICK_S", 0.02)
    # Rotation safety: maximum continuous rotation duration (seconds)
    UI_ROTATION_MAX_S: float = _env_float("UI_ROTATION_MAX_S", 100.0)

    # UI click/hold timing (ms) used by frontend for consistent UX
    # HOLD_THRESHOLD_MS: threshold to consider a press a 'hold' (suppress click)
    UI_HOLD_THRESHOLD_MS: int = int(_env_float("UI_HOLD_THRESHOLD_MS", 150))
    # CLICK_SUPPRESS_MS: time after a hold during which clicks are suppressed
    UI_CLICK_SUPPRESS_MS: int = int(_env_float("UI_CLICK_SUPPRESS_MS", 350))

    # Maximum step (mm) accepted from a single GUI click to avoid dangerous
    # large moves if the client sends an unexpected value. Continuous hold
    # movements still use their own cadence and are unaffected.
    UI_MAX_CLICK_STEP_MM: float = _env_float("UI_MAX_CLICK_STEP_MM", 20.0)

    # Desktop window title (pywebview)
    UI_TITLE: str = os.environ.get("UI_TITLE", "3SONIC 3D Ultrasound app")

    # Exit timing (used by graceful shutdown to let HTTP 200 flush)
    EXIT_GRACE_DELAY_MS: int = int(_env_float("EXIT_GRACE_DELAY_MS", 300))

    # ---------------- Service / scan positioning (Insert-Bath button) --------
    TARGET_Z_MM: float = _env_float("TARGET_Z_MM", 100.0)
    SCAN_POSE: dict[str, float] = {
        "X": _env_float("SCAN_POSE_X", 53.5),
        "Y": _env_float("SCAN_POSE_Y", 53.5),
        "Z": _env_float("SCAN_POSE_Z", 10.0),
    }
    # Raise Z and XYZ default feeds for explicit positioning.
    # Lowered default to a conservative value for safer operation on unknown hardware.
    Z_FEED_MM_PER_MIN: int = int(_env_float("Z_FEED", 1200))
    XYZ_FEED_MM_PER_MIN: int = int(_env_float("XYZ_FEED", 1200))
    POS_TOL_MM: float = _env_float("POS_TOL_MM", 0.02)
    POLL_INTERVAL_S: float = _env_float("POLL_INTERVAL_S", 0.10)
    POLL_TIMEOUT_S: float = _env_float("POLL_TIMEOUT_S", 5.0)

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

    # -------- Scan-path & feed helpers (used by scanner_control/record) ------
    @staticmethod
    def clamp_x(x: float) -> float:
        return max(0.0, min(float(Config.X_MAX), float(x)))

    @staticmethod
    def normalize_x_range(start: float, end: float) -> tuple[float, float]:
        """Clamp to [0, X_MAX] and ensure start < end."""
        s = Config.clamp_x(start)
        e = Config.clamp_x(end)
        if e <= s:
            # minimal nonzero span if inverted/equal
            e = min(Config.X_MAX, s + 0.1)
        return (s, e)

    @staticmethod
    def x_range_for_mode(mode: str) -> tuple[float, float]:
        """
        Convenience for UI presets.
        mode ∈ {'long','short'} → returns clamped (start,end).
        Unknown mode → full range (0, X_MAX).
        """
        m = (mode or "").strip().lower()
        if m == "short":
            s, e = Config.SHORT_PATH_X
        elif m == "long":
            s, e = Config.LONG_PATH_X
        else:
            s, e = (0.0, float(Config.X_MAX))
        return Config.normalize_x_range(s, e)

    @staticmethod
    def computed_scan_feed_mm_per_min() -> float:
        """
        Compute synchronized scan feed from e_r and frame rate:
            v (mm/s) = e_r * fps
            F (mm/min) = 60 * v
        """
        v = float(Config.ELEV_RESOLUTION_MM) * float(Config.TARGET_FPS)  # mm/s
        return 60.0 * v

