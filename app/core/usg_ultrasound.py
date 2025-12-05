# usg_ultrasound.py
from __future__ import annotations

import ctypes
from ctypes import (
    c_int,
    c_int32,
    c_long,
    POINTER,
    byref,
)
import threading
from dataclasses import dataclass
from typing import Optional

from pathlib import Path
import logging
import json
from typing import List

from app.config import Config, resource_path


class UltrasoundError(RuntimeError):
    pass


@dataclass
class UsgState:
    initialized: bool
    init_error: Optional[str]
    probe_connected: bool
    running: bool
    # Basic imaging parameters
    frequency_label: Optional[str] = None
    depth_mm: Optional[int] = None
    gain_percent: Optional[int] = None
    power_db: Optional[int] = None
    lines_density_label: Optional[str] = None
    view_area_percent: Optional[int] = None
    focal_depth_mm: Optional[int] = None
    focal_zones_count: Optional[int] = None
    focal_zone_idx: Optional[int] = None
    steering_angle_deg: Optional[int] = None
    dynamic_range_db: Optional[int] = None
    scan_direction_inverted: Optional[bool] = None
    scan_type: Optional[str] = None  # "standard", "wideview", "compound"


class UsgUltrasound:
    """
    High-level wrapper around usgfw2wrapper.dll.

    It exposes most of the controls from the original Tkinter demo as simple
    methods which are then used by the Flask API.
    """

    def __init__(
        self,
        dll_path: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
    ):
        # compute a sensible default path for the wrapper DLL if none provided

        dll_name = "usgfw2wrapperOp.dll"
        alt_name = "usgfw2wrapperOP.dll"  # fallback with different casing
        here = Path(__file__).resolve().parent
        parent = here.parent
        cwd = Path.cwd()
        static_root = Config.BASE_DIR / "static" / "dll"

        if dll_path:
            self.dll_path = str(Path(dll_path))
            logging.getLogger(__name__).debug(
                "Using user-specified DLL path: %s", self.dll_path
            )
        else:
            search_order = [
                Config.control_dll_path(),
                Config.dll_path(),
                static_root / dll_name,
                static_root / alt_name,
                Config.BASE_DIR / "src" / dll_name,
                Config.BASE_DIR / "src" / "usgfw2wrapper.dll",
                resource_path(f"static/dll/{dll_name}"),
                resource_path(f"static/dll/{alt_name}"),
                parent / "static" / "dll" / dll_name,
                cwd / "static" / "dll" / dll_name,
                here / "static" / "dll" / dll_name,
                here / "dll" / dll_name,
                parent / "dll" / dll_name,
                here / dll_name,
                parent / dll_name,
                cwd / dll_name,
            ]

            normalized: list[Path] = []
            seen: set[str] = set()
            for candidate in search_order:
                try:
                    resolved = Path(candidate)
                except Exception:
                    continue
                key = str(resolved.resolve())
                if key in seen:
                    continue
                seen.add(key)
                normalized.append(resolved)

            found = next((p for p in normalized if p.exists()), None)
            if not found:
                found = next((p.with_name(alt_name) for p in normalized if p.with_name(alt_name).exists()), None)

            self.dll_path = str(found) if found is not None else str(normalized[0] if normalized else dll_name)
            if found is not None:
                logging.getLogger(__name__).debug("Found DLL at: %s", self.dll_path)
            else:
                logging.getLogger(__name__).warning(
                    "Could not find '%s' in candidate locations; will try '%s' by name",
                    dll_name,
                    dll_name,
                )

        self.width = int(width)
        self.height = int(height)

        self._lock = threading.Lock()
        self._dll = None
        self._initialized = False
        self._probe_connected = False
        self._running = False
        self._init_error: Optional[str] = None

        # cached ctypes values
        self._freq = c_int(0)
        self._freq_no = c_long(0)
        self._depth = c_int(0)
        self._gain = c_int(0)
        self._power = c_int(0)
        self._lines_density = c_int(0)
        self._view_area = c_int(0)
        self._focal_depth = c_int(0)
        self._focal_zones_count = c_int(0)
        self._focal_zone_idx = c_int(0)
        self._steering_angle = c_int(0)
        self._dynamic_range = c_int(0)

        self._scan_direction_inverted = False
        self._scan_type = 0  # 0=Standard,1=WideView,2=Compound

        # TGC depths
        self._tgc_depth = [c_int(0) for _ in range(5)]

        # Focus zones persisted locally (max 5)
        self._focus_zones: List[int] = []
        self._focus_store = (Config.STATE_DIR / "focus_zones.json").resolve()

        self._load_and_init()

        # Load persisted focus zones after initialization attempt
        try:
            self._load_focus_zones()
        except Exception:
            logging.getLogger(__name__).exception("Failed to load persisted focus zones")

    # ------------------------------------------------------------------ #
    # Low-level setup
    # ------------------------------------------------------------------ #

    def _load_and_init(self):
        try:
            self._dll = ctypes.CDLL(self.dll_path)
        except OSError as exc:
            logging.getLogger(__name__).exception(
                "Could not load DLL '%s': %s", self.dll_path, exc
            )
            self._init_error = f"Could not load DLL '{self.dll_path}': {exc}"
            return

        d = self._dll

        # Return values
        d.on_init.restype = None
        d.init_ultrasound_usgfw2.restype = c_int32
        d.find_connected_probe.restype = c_int32
        d.data_view_function.restype = c_int32
        d.mixer_control_function.restype = c_int32

        d.frequency_control.restype = None
        d.B_FrequencySetPrevNext.restype = None

        d.depth_control.restype = None
        d.DepthSetPrevNext.restype = None

        d.gain_control.restype = None
        d.B_GainSetByIdx.restype = None

        d.power_control.restype = None
        d.B_PowerSetByIdx.restype = None

        d.lines_density.restype = None
        d.B_LinesDensitySetPrevNext.restype = None

        d.view_area.restype = None
        d.B_view_areaSetPrevNext.restype = None

        d.focus_control.restype = None
        d.B_FocusSetPrevNext.restype = None

        d.steering_angle.restype = c_int32
        d.B_SteeringAngleSetPrevNext.restype = None

        d.B_dynamic_range.restype = None
        d.B_DynamicRangeSetPrevNext.restype = None

        d.image_orientation.restype = None
        d.ChangeScanDirection.restype = None

        d.TGC_control.restype = None
        d.adjust_TGC.restype = None

        d.UsgQualProp_control.restype = None

        d.scan_type_control.restype = None
        d.turn_on_scan_type.restype = None

        d.wide_view_angle.restype = None
        d.WideViewAngleSetPrevNext.restype = None

        d.compound_angle.restype = None
        d.CompoundAngleSetPrevNext.restype = None

        d.compound_frames_number.restype = None
        d.CompoundFramesSetPrevNext.restype = None
        d.CompoundSubframeSetPrevNext.restype = None

        d.cine_loop_controls.restype = None

        d.Run_ultrasound_scanning.restype = None
        d.Freeze_ultrasound_scanning.restype = None
        d.Stop_ultrasound_scanning.restype = None
        d.Close_and_release.restype = None

        # argument types for the functions we actually use
        d.mixer_control_function.argtypes = [
            c_int32,
            c_int32,
            c_int32,
            c_int32,
            c_int32,
            c_int32,
            c_int32,
        ]
        d.B_FrequencySetPrevNext.argtypes = [
            c_int32,
            POINTER(c_int),
            POINTER(c_long),
        ]
        d.DepthSetPrevNext.argtypes = [c_int32, POINTER(c_int)]
        d.B_GainSetByIdx.argtypes = [c_int32, POINTER(c_int)]
        d.B_PowerSetByIdx.argtypes = [c_int32, POINTER(c_int)]
        d.B_LinesDensitySetPrevNext.argtypes = [c_int32, POINTER(c_int)]
        d.B_view_areaSetPrevNext.argtypes = [c_int32, POINTER(c_int)]
        d.B_FocusSetPrevNext.argtypes = [
            c_int32,
            POINTER(c_int),
            POINTER(c_int),
            POINTER(c_int),
        ]
        d.B_SteeringAngleSetPrevNext.argtypes = [c_int32, POINTER(c_int)]
        d.B_DynamicRangeSetPrevNext.argtypes = [c_int32, POINTER(c_int)]
        d.ChangeScanDirection.argtypes = [c_int32]
        d.adjust_TGC.argtypes = [c_int32, c_int32, POINTER(c_int)]
        d.turn_on_scan_type.argtypes = [c_int32]
        d.WideViewAngleSetPrevNext.argtypes = [c_int32, POINTER(c_int)]
        d.CompoundAngleSetPrevNext.argtypes = [c_int32, POINTER(c_int)]
        d.CompoundFramesSetPrevNext.argtypes = [c_int32, POINTER(c_int)]
        d.CompoundSubframeSetPrevNext.argtypes = [c_int32, POINTER(c_int)]

        # -------------------------------------------------------------- #
        # Initialization sequence (no sys.exit – we keep errors in state)
        # -------------------------------------------------------------- #
        try:
            d.on_init()

            err = d.init_ultrasound_usgfw2()
            if err == 2:
                self._init_error = "Main Usgfw2 library object not created (err=2)."
                d.Close_and_release()
                return

            err = d.find_connected_probe()
            if err != 101:
                self._init_error = f"Probe not detected (err={err})."
                d.Close_and_release()
                return
            self._probe_connected = True

            err = d.data_view_function()
            if err < 0:
                self._init_error = (
                    "Main ultrasound scanning object for selected probe not created."
                )
                d.Close_and_release()
                return

            err = d.mixer_control_function(
                0, 0, self.width, self.height, 0, 0, 0
            )  # black background
            if err < 0:
                self._init_error = "B mixer control not returned."
                d.Close_and_release()
                return

            # Configure controls like in the original Tkinter script
            self._initial_setup_controls()

            self._initialized = True

        except Exception as exc:
            self._init_error = f"Initialization exception: {exc}"
            try:
                d.Close_and_release()
            except Exception:
                pass

    def _initial_setup_controls(self):
        d = self._dll

        # Frequency
        d.frequency_control()
        d.B_FrequencySetPrevNext(0, byref(self._freq), byref(self._freq_no))

        # Depth
        d.depth_control()
        d.DepthSetPrevNext(0, byref(self._depth))

        # Gain
        d.gain_control()
        d.B_GainSetByIdx(70, byref(self._gain))

        # Power
        d.power_control()
        d.B_PowerSetByIdx(20, byref(self._power))

        # Lines density
        d.lines_density()
        d.B_LinesDensitySetPrevNext(0, byref(self._lines_density))

        # View area
        d.view_area()
        self._view_area.value = 0
        d.B_view_areaSetPrevNext(5, byref(self._view_area))

        # Focus
        d.focus_control()
        d.B_FocusSetPrevNext(
            0,
            byref(self._focal_depth),
            byref(self._focal_zones_count),
            byref(self._focal_zone_idx),
        )

        # Steering
        available_angle = d.steering_angle()
        if available_angle == 1:
            d.B_SteeringAngleSetPrevNext(0, byref(self._steering_angle))

        # Dynamic range
        d.B_dynamic_range()
        d.B_DynamicRangeSetPrevNext(0, byref(self._dynamic_range))

        # Scan direction
        d.image_orientation()
        d.ChangeScanDirection(0)
        self._scan_direction_inverted = False

        # TGC
        d.TGC_control()
        for idx, default in enumerate([50, 60, 60, 60, 60]):
            d.adjust_TGC(idx, default, byref(self._tgc_depth[idx]))

        # Scan type and quality props
        d.UsgQualProp_control()
        d.scan_type_control()
        d.turn_on_scan_type(0)  # Standard
        self._scan_type = 0

        d.cine_loop_controls()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def init_error(self) -> Optional[str]:
        return self._init_error

    @property
    def probe_connected(self) -> bool:
        return self._probe_connected

    @property
    def running(self) -> bool:
        return self._running

    # ------------- frequency label formatting ------------------------- #
    def _format_frequency(self) -> str:
        """
        Reproduce the logic of:
        frequency = str((freq.value)/1000000)
        and THI suffixes.
        """
        raw = str(self._freq.value / 1_000_000)
        if len(raw) > 3:
            if raw[-1] == "1":
                return raw[:-1] + " ITHI"
            if raw[-1] == "2":
                return raw[:-1] + " THI"
        return raw

    def _lines_density_label(self) -> str:
        ld = self._lines_density.value
        if ld == 8:
            return "Low"
        if ld == 16:
            return "Medium"
        if ld == 22:
            return "Standard S"
        if ld == 24:
            return "Standard"
        if ld == 32:
            return "High"
        return str(ld)

    def _scan_type_label(self) -> str:
        return {0: "standard", 1: "wideview", 2: "compound"}.get(self._scan_type, "unknown")

    # ------------------------------------------------------------------ #
    # Public state getter
    # ------------------------------------------------------------------ #

    def get_state(self) -> UsgState:
        return UsgState(
            initialized=self._initialized,
            init_error=self._init_error,
            probe_connected=self._probe_connected,
            running=self._running,
            frequency_label=self._format_frequency() if self._initialized else None,
            depth_mm=self._depth.value if self._initialized else None,
            gain_percent=self._gain.value if self._initialized else None,
            power_db=self._power.value if self._initialized else None,
            lines_density_label=self._lines_density_label() if self._initialized else None,
            view_area_percent=self._view_area.value if self._initialized else None,
            focal_depth_mm=self._focal_depth.value if self._initialized else None,
            focal_zones_count=self._focal_zones_count.value if self._initialized else None,
            focal_zone_idx=self._focal_zone_idx.value if self._initialized else None,
            steering_angle_deg=self._steering_angle.value if self._initialized else None,
            dynamic_range_db=self._dynamic_range.value if self._initialized else None,
            scan_direction_inverted=self._scan_direction_inverted if self._initialized else None,
            scan_type=self._scan_type_label() if self._initialized else None,
        )

    # ------------------------------------------------------------------ #
    # Focus zone persistence and helpers
    # ------------------------------------------------------------------ #
    def _load_focus_zones(self) -> None:
        """Load persisted focus zone depths from disk if present."""
        try:
            if self._focus_store.exists():
                data = json.loads(self._focus_store.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    # keep only up to 5 integer depths
                    self._focus_zones = [int(x) for x in data[:5]]
        except Exception:
            logging.getLogger(__name__).exception("Error reading focus zones file")

    def _save_focus_zones(self) -> None:
        try:
            self._focus_store.parent.mkdir(parents=True, exist_ok=True)
            self._focus_store.write_text(json.dumps(self._focus_zones), encoding="utf-8")
        except Exception:
            logging.getLogger(__name__).exception("Error saving focus zones file")

    def get_focus_zones(self) -> List[dict]:
        """Return persisted focus zones as list of {index, depth_mm} dicts.

        If the device exposes a different number of zones, the frontend can
        still use the stored list (up to 5)."""
        # Ensure list length is at least 0..5
        zones = []
        for idx in range(len(self._focus_zones)):
            zones.append({"index": idx, "depth_mm": int(self._focus_zones[idx])})
        return zones

    def set_focus_zone_local(self, idx: int, depth_mm: int) -> None:
        """Update the stored focus zone depth locally and persist it.

        This does not necessarily apply the change to the hardware unless
        `set_focus_zone_depth(..., apply=True)` is used.
        """
        idx = int(max(0, min(4, idx)))
        depth_mm = int(max(0, depth_mm))
        # Ensure list length
        while len(self._focus_zones) <= idx:
            self._focus_zones.append(0)
        self._focus_zones[idx] = depth_mm
        self._save_focus_zones()

    def set_focus_zone_depth(self, idx: int, depth_mm: int, apply: bool = True) -> dict:
        """Attempt to set focus zone `idx` to `depth_mm` on the device.

        Because the vendor wrapper only exposes prev/next style controls, this
        method will try to select the requested zone and then step its depth
        until it matches the requested mm value or until a safety iteration
        limit is reached. The final stored value is persisted locally.

        Returns a status dict with keys: zones_count, zone_idx, focal_depth_mm, applied
        """
        self.ensure_ready()
        idx = int(max(0, min(4, idx)))
        target = int(max(0, depth_mm))

        applied = False
        with self._lock:
            # First, ensure we know current zones count and selected index
            zones_count = int(self._focal_zones_count.value)
            # If the device reports fewer zones than our requested index, clamp
            if zones_count > 0 and idx >= zones_count:
                idx = zones_count - 1

            # Try to select the requested zone by cycling prev/next
            max_iter = 40
            iter_count = 0
            while int(self._focal_zone_idx.value) != idx and iter_count < max_iter:
                # choose direction: +1 advances, -1 goes back
                cur = int(self._focal_zone_idx.value)
                direction = 1 if (idx > cur) else -1
                try:
                    self._dll.B_FocusSetPrevNext(direction, byref(self._focal_depth), byref(self._focal_zones_count), byref(self._focal_zone_idx))
                except Exception:
                    logging.getLogger(__name__).exception("Error cycling focus zone index")
                    break
                iter_count += 1

            # Now adjust depth by stepping prev/next until within tolerance
            iter_count = 0
            tolerance_mm = 1
            while iter_count < max_iter and abs(int(self._focal_depth.value) - target) > tolerance_mm:
                cur_depth = int(self._focal_depth.value)
                direction = 1 if target > cur_depth else -1
                try:
                    self._dll.B_FocusSetPrevNext(direction, byref(self._focal_depth), byref(self._focal_zones_count), byref(self._focal_zone_idx))
                except Exception:
                    logging.getLogger(__name__).exception("Error adjusting focus depth")
                    break
                iter_count += 1

            # Persist local desired value regardless of whether device reached it
            self.set_focus_zone_local(idx, target)
            applied = True

        return {
            "zones_count": int(self._focal_zones_count.value),
            "zone_idx": int(self._focal_zone_idx.value),
            "focal_depth_mm": int(self._focal_depth.value),
            "applied": applied,
        }

    # --- Focus zones: allow cycling selected focal zone index ------------- #
    def adjust_focal_zone_index(self, direction: int) -> dict:
        """
        Move the selected focal zone index prev/next.

        direction: -1 or +1
        returns dict with keys: `zones_count`, `zone_idx`, `focal_depth_mm`
        """
        self.ensure_ready()
        direction = -1 if direction < 0 else 1
        with self._lock:
            # B_FocusSetPrevNext(direction, byref(focal_depth), byref(zones_count), byref(zone_idx))
            self._dll.B_FocusSetPrevNext(
                direction,
                byref(self._focal_depth),
                byref(self._focal_zones_count),
                byref(self._focal_zone_idx),
            )
        return {
            "zones_count": int(self._focal_zones_count.value),
            "zone_idx": int(self._focal_zone_idx.value),
            "focal_depth_mm": int(self._focal_depth.value),
        }

    # ------------------------------------------------------------------ #
    # High-level controls called by Flask
    # ------------------------------------------------------------------ #

    def ensure_ready(self):
        if not self._initialized:
            raise UltrasoundError(self._init_error or "Ultrasound not initialized")

    def run(self):
        self.ensure_ready()
        with self._lock:
            self._dll.Run_ultrasound_scanning()
            self._running = True

    def freeze(self):
        self.ensure_ready()
        with self._lock:
            self._dll.Freeze_ultrasound_scanning()
            self._running = False

    # --- Frequency ---------------------------------------------------- #
    def adjust_frequency(self, direction: int) -> str:
        """
        direction: -1 or +1
        returns display label.
        """
        self.ensure_ready()
        direction = -1 if direction < 0 else 1
        with self._lock:
            self._dll.B_FrequencySetPrevNext(direction, byref(self._freq), byref(self._freq_no))
        return self._format_frequency()

    # --- Depth -------------------------------------------------------- #
    def adjust_depth(self, direction: int) -> int:
        self.ensure_ready()
        direction = -1 if direction < 0 else 1
        with self._lock:
            self._dll.DepthSetPrevNext(direction, byref(self._depth))
        return self._depth.value

    # --- Gain & Power sliders ---------------------------------------- #
    def set_gain(self, percent: int) -> int:
        self.ensure_ready()
        percent = int(max(0, min(90, percent)))
        with self._lock:
            self._dll.B_GainSetByIdx(percent, byref(self._gain))
        return self._gain.value

    def set_power(self, db: int) -> int:
        self.ensure_ready()
        db = int(max(0, min(20, db)))
        with self._lock:
            self._dll.B_PowerSetByIdx(db, byref(self._power))
        return self._power.value

    # --- Lines density ------------------------------------------------ #
    def adjust_lines_density(self, direction: int) -> str:
        self.ensure_ready()
        direction = -1 if direction < 0 else 1
        with self._lock:
            self._dll.B_LinesDensitySetPrevNext(direction, byref(self._lines_density))
        return self._lines_density_label()

    # --- View area ---------------------------------------------------- #
    def adjust_view_area(self, direction: int) -> int:
        self.ensure_ready()
        direction = -1 if direction < 0 else 1
        with self._lock:
            self._dll.B_view_areaSetPrevNext(direction, byref(self._view_area))
        return self._view_area.value

    # --- Focus depth -------------------------------------------------- #
    def adjust_focal_depth(self, direction: int) -> int:
        self.ensure_ready()
        direction = -1 if direction < 0 else 1
        with self._lock:
            self._dll.B_FocusSetPrevNext(
                direction,
                byref(self._focal_depth),
                byref(self._focal_zones_count),
                byref(self._focal_zone_idx),
            )
        return self._focal_depth.value

    # --- Steering angle ----------------------------------------------- #
    def adjust_steering_angle(self, direction: int) -> int:
        self.ensure_ready()
        direction = -1 if direction < 0 else 1
        with self._lock:
            self._dll.B_SteeringAngleSetPrevNext(direction, byref(self._steering_angle))
        return self._steering_angle.value

    # --- Dynamic range ------------------------------------------------ #
    def adjust_dynamic_range(self, direction: int) -> int:
        self.ensure_ready()
        direction = -1 if direction < 0 else 1
        with self._lock:
            self._dll.B_DynamicRangeSetPrevNext(direction, byref(self._dynamic_range))
        return self._dynamic_range.value

    # --- Scan direction ----------------------------------------------- #
    def set_scan_direction_inverted(self, inverted: bool) -> bool:
        self.ensure_ready()
        value = 1 if inverted else 0
        with self._lock:
            self._dll.ChangeScanDirection(value)
        self._scan_direction_inverted = inverted
        return inverted

    # --- TGC sliders -------------------------------------------------- #
    def set_tgc(self, idx: int, percent: int) -> dict:
        """
        idx: 0..4
        percent: 0..100
        returns dict {percent, depth_mm}
        """
        self.ensure_ready()
        idx = int(max(0, min(4, idx)))
        percent = int(max(0, min(100, percent)))
        with self._lock:
            self._dll.adjust_TGC(idx, percent, byref(self._tgc_depth[idx]))
        return {"percent": percent, "depth_mm": self._tgc_depth[idx].value}

    def get_tgc_profile(self) -> list:
        """Return the current TGC profile as a list of 5 percent values."""
        # return as plain ints even if uninitialized
        return [int(x.value) if hasattr(x, 'value') else int(x) for x in self._tgc_depth]

    def apply_tgc_profile(self, profile: list) -> dict:
        """Apply a profile (list-like of up to 5 percentages) to the device.

        The profile is applied in order 0..4. Returns the final applied values.
        """
        self.ensure_ready()
        applied = []
        with self._lock:
            for i in range(5):
                pct = int(max(0, min(100, profile[i] if i < len(profile) else 50)))
                try:
                    self._dll.adjust_TGC(i, pct, byref(self._tgc_depth[i]))
                except Exception:
                    logging.getLogger(__name__).exception("Error applying TGC index %s", i)
                applied.append({"index": i, "percent": int(self._tgc_depth[i].value)})
        return {"applied": applied}

    # --- Scan type and extra modes ----------------------------------- #
    def set_scan_type(self, scan_type: str) -> str:
        """
        scan_type: "standard", "wideview", "compound"
        """
        self.ensure_ready()
        mapping = {"standard": 0, "wideview": 1, "compound": 2}
        idx = mapping.get(scan_type, 0)
        with self._lock:
            self._dll.turn_on_scan_type(idx)
        self._scan_type = idx
        return self._scan_type_label()

    def adjust_wideview_angle(self, direction: int) -> int:
        self.ensure_ready()
        angle = c_int(0)
        direction = -1 if direction < 0 else 1
        with self._lock:
            self._dll.WideViewAngleSetPrevNext(direction, byref(angle))
        return angle.value

    def adjust_compound_angle(self, direction: int) -> int:
        self.ensure_ready()
        angle = c_int(0)
        direction = -1 if direction < 0 else 1
        with self._lock:
            self._dll.CompoundAngleSetPrevNext(direction, byref(angle))
        return angle.value

    def adjust_compound_frames(self, direction: int) -> int:
        self.ensure_ready()
        frames = c_int(0)
        direction = -1 if direction < 0 else 1
        with self._lock:
            self._dll.CompoundFramesSetPrevNext(direction, byref(frames))
        return frames.value

    def adjust_compound_subframe(self, direction: int) -> int:
        self.ensure_ready()
        subframe = c_int(0)
        direction = -1 if direction < 0 else 1
        with self._lock:
            self._dll.CompoundSubframeSetPrevNext(direction, byref(subframe))
        return subframe.value

    # --- Cleanup ------------------------------------------------------ #
    def close(self):
        if self._dll is None:
            return
        with self._lock:
            try:
                self._dll.Stop_ultrasound_scanning()
            except Exception:
                pass
            try:
                self._dll.Close_and_release()
            except Exception:
                pass
        self._dll = None
        self._initialized = False
        self._running = False


# ---------------------------------------------------------------------- #
# Module-level singleton used by Flask
# ---------------------------------------------------------------------- #

_instance: Optional[UsgUltrasound] = None
_instance_lock = threading.Lock()


def get_usg_instance() -> Optional[UsgUltrasound]:
    global _instance
    with _instance_lock:
        if _instance is not None and _instance.initialized:
            return _instance
        # Try to (re)create
        _instance = UsgUltrasound()
        return _instance