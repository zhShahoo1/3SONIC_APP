# usg_realtime.py
from __future__ import annotations

import ctypes
from ctypes import c_uint
import threading
import io
import logging
from pathlib import Path
from typing import Callable, Optional, Tuple
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.config import Config, resource_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DLL configuration
# ---------------------------------------------------------------------------

# Primary: vendor MATLAB realtime wrapper (resolved via project resources)
_HERE = Path(__file__).resolve().parent
DLL_PRIMARY = resource_path("static/dll/usgfw2MATLAB_wrapper.dll")

# Fallback: optimization / control wrapper already used elsewhere
DLL_FALLBACK = resource_path("static/dll/usgfw2wrapperOp.dll")


class RealTimeUltrasound:
    """
    Realtime B-mode ultrasound streaming wrapper.

    The class tries to use the vendor MATLAB realtime DLL first
    and falls back to the Op wrapper if needed.

    Public contract used by Flask:

    - get_status() -> dict with at least:
        * 'initialized': bool
        * 'error': Optional[str]

    - get_frame_png() -> (png_bytes: bytes, ready: bool)
        * ready == True  -> bytes contain a real ultrasound frame
        * ready == False -> bytes contain a black placeholder frame
    """

    # ------------------------------------------------------------------ #
    # Construction & basic properties
    # ------------------------------------------------------------------ #

    def __init__(
        self,
        width: int = 1024,
        height: int = 1024,
        dll_path: str | None = None,
    ) -> None:
        self.width: int = width
        self.height: int = height

        # Resolve DLL path (explicit > primary > fallback)
        self._dll_path: str = self._resolve_dll_path(dll_path)

        self._usg: Optional[ctypes.CDLL] = None
        self._initialized: bool = False
        self._init_attempted: bool = False
        self._error: Optional[str] = None

        self._lock = threading.Lock()

        # Background capture thread and cache for low-latency delivery
        self._capture_thread: Optional[threading.Thread] = None
        self._capture_stop = threading.Event()
        self._last_png: Optional[bytes] = None
        self._last_ready: bool = False
        self._target_fps = 20

        # Cache placeholder so we don't regenerate it
        self._placeholder_png: bytes = self._create_placeholder_png()

        logger.debug(
            "RealTimeUltrasound created (width=%d, height=%d, dll='%s')",
            self.width,
            self.height,
            self._dll_path,
        )

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def error(self) -> Optional[str]:
        return self._error

    # ------------------------------------------------------------------ #
    # Public API for Flask
    # ------------------------------------------------------------------ #

    def get_status(self) -> dict:
        """
        Status dict consumed by the REST layer / frontend.
        """
        return {
            "initialized": self._initialized,
            "error": self._error,
            "dll_path": self._dll_path,
        }

    def get_frame_png(self) -> Tuple[bytes, bool]:
        """
        Grab a single frame as PNG bytes.

        Returns:
            (png_bytes, ready_flag)

        - ready_flag == True   -> real frame from probe
        - ready_flag == False  -> black placeholder frame
        """
        # Lazy initialization (do not hold lock for long operations)
        if not self._init_attempted:
            with self._lock:
                if not self._init_attempted:
                    self._init_attempted = True
                    self._initialize()

        # If initialization failed, return placeholder
        if not self._initialized or self._usg is None:
            return self._placeholder_png, False

        # Ensure capture thread is running
        if self._capture_thread is None or not self._capture_thread.is_alive():
            with self._lock:
                if self._capture_thread is None or not self._capture_thread.is_alive():
                    self._start_capture_thread()

        # Return last cached frame for low-latency response
        if self._last_png is not None:
            return self._last_png, bool(self._last_ready)
        return self._placeholder_png, False

    # ------------------------------------------------------------------ #
    # Internal: initialization & DLL handling
    # ------------------------------------------------------------------ #

    def _resolve_dll_path(self, dll_path: Optional[str]) -> str:
        """
        Decide which DLL path to use.

        Precedence:
        1. Explicit path provided by caller (if exists)
        2. Primary MATLAB realtime DLL (if exists)
        3. Fallback Op wrapper (if exists)
        4. Last resort: explicit path even if missing (so error is explicit)
        """
        if dll_path:
            p = Path(dll_path)
            if p.exists():
                logger.info("Using explicit realtime DLL: %s", p)
                return str(p)
            logger.warning(
                "Explicit DLL path '%s' does not exist; will still attempt to load it",
                p,
            )
            return str(p)

        configured_primary = Config.realtime_dll_path()
        if configured_primary.exists():
            logger.info("Using configured realtime DLL: %s", configured_primary)
            return str(configured_primary)

        configured_fallback = Config.control_dll_path()
        if configured_fallback.exists():
            logger.warning(
                "Realtime DLL not found; falling back to control DLL '%s'",
                configured_fallback,
            )
            return str(configured_fallback)

        if DLL_PRIMARY.exists():
            logger.info("Using bundled realtime DLL: %s", DLL_PRIMARY)
            return str(DLL_PRIMARY)

        if DLL_FALLBACK.exists():
            logger.warning(
                "Bundled realtime DLL missing; using fallback '%s'",
                DLL_FALLBACK,
            )
            return str(DLL_FALLBACK)

        # Neither file exists; choose primary name so the error message is clear
        logger.error(
            "Neither primary DLL '%s' nor fallback DLL '%s' exists",
            DLL_PRIMARY,
            DLL_FALLBACK,
        )
        return str(configured_primary)

    def _initialize(self) -> None:
        """
        Initialize DLL, find probe, and set up data view.

        Mirrors vendor realtime logic but with:
        - robust error handling (no sys.exit)
        - more tolerant symbol name resolution
        - detailed logging
        """
        logger.info("Initializing realtime ultrasound (DLL='%s')", self._dll_path)

        # 1. Load DLL
        try:
            self._usg = ctypes.CDLL(self._dll_path)
        except OSError as exc:
            logger.exception("Could not load DLL '%s'", self._dll_path)
            self._error = f"Could not load DLL '{self._dll_path}': {exc}"
            self._initialized = False
            return

        usg = self._usg

        # 2. Resolve core functions
        try:
            usg.on_init.restype = None
        except AttributeError:
            logger.error("DLL missing required function 'on_init'")
            self._error = "DLL missing required function 'on_init'"
            self._initialized = False
            return

        init_func = self._resolve_init_function(usg)
        if init_func is None:
            self._error = "No suitable init_ultrasound_* function found in DLL"
            self._initialized = False
            return

        init_func.restype = ctypes.c_int

        # Optional / expected symbols:
        usg.find_connected_probe.restype = ctypes.c_int
        usg.data_view_function.restype = ctypes.c_int
        usg.mixer_control_function.restype = ctypes.c_int
        usg.get_resolution.restype = None
        usg.return_pixel_values.restype = None
        usg.return_pixel_values.argtypes = [ctypes.POINTER(c_uint)]

        # 3. Call vendor init sequence with checks
        try:
            usg.on_init()
        except Exception:  # noqa: BLE001
            logger.exception("on_init() call failed")
            self._error = "DLL on_init() failed"
            self._initialized = False
            return

        init_rc = init_func()
        # Historically some vendor builds return non-zero but still allow
        # subsequent setup (we previously tolerated non-zero returns).
        # Only treat code 2 as a fatal failure where the main object wasn't created.
        if init_rc == 2:
            self._error = "Main Usgfw2 library object not created (err=2)."
            logger.error("Initialization function returned error code 2 (fatal)")
            self._initialized = False
            return
        if init_rc != 0:
            logger.warning("Initialization function returned non-zero code: %s", init_rc)

        # 4. Probe detection
        try:
            probe_rc = usg.find_connected_probe()
        except Exception as exc:  # noqa: BLE001
            logger.exception("find_connected_probe() call failed")
            self._error = f"Probe detection call failed: {exc}"
            self._initialized = False
            return

        # Historically 101 was treated as success; some builds use 0/1.
        if probe_rc not in (0, 1, 101):
            logger.warning(
                "find_connected_probe() returned %s (treated as failure)", probe_rc
            )
            self._error = f"Probe not detected (code {probe_rc})"
            self._initialized = False
            return
        logger.info("Probe detected (code %s)", probe_rc)

        # 5. Create data view / scanning object
        view_rc = usg.data_view_function()
        if view_rc < 0:
            logger.error("data_view_function() returned error code %s", view_rc)
            self._error = (
                "Main ultrasound scanning object for selected probe not created"
            )
            self._initialized = False
            return

        # 6. Mixer / viewport configuration
        # Call mixer_control_function if available, but protect against
        # runtime errors (some DLL builds may not support this symbol).
        try:
            if hasattr(usg, 'mixer_control_function'):
                mix_rc = usg.mixer_control_function(0, 0, self.width, self.height, 0, 0, 0)
                if isinstance(mix_rc, int) and mix_rc < 0:
                    logger.error("mixer_control_function() returned error code %s", mix_rc)
                    self._error = "B-mode mixer control not returned"
                    self._initialized = False
                    return
            else:
                logger.warning("DLL does not expose 'mixer_control_function'; skipping mixer setup")
        except Exception:
            logger.exception("mixer_control_function() call failed; aborting init")
            self._error = "mixer_control_function() call failed"
            self._initialized = False
            return

        # 7. Try to read geometric resolution (mm per pixel). Not all
        # wrappers export this; if available, store for scale bar drawing.
        try:
            if hasattr(usg, "get_resolution"):
                rx = ctypes.c_float(0.0)
                ry = ctypes.c_float(0.0)
                usg.get_resolution(ctypes.byref(rx), ctypes.byref(ry))
                self._resolution = (float(rx.value), float(ry.value))
                logger.info("Device resolution (mm/px): %s", self._resolution)
            else:
                self._resolution = None
        except Exception:
            logger.exception("Failed to read device resolution")
            self._resolution = None

        # If we got here, we are ready.
        self._initialized = True
        self._error = None
        logger.info(
            "Realtime ultrasound successfully initialized (resolution=%dx%d)",
            self.width,
            self.height,
        )
        # Start background capture once initialized
        try:
            self._start_capture_thread()
        except Exception:
            logger.exception("Failed to start capture thread after init")

    def _resolve_init_function(self, usg: ctypes.CDLL) -> Optional[Callable[[], int]]:
        """
        Try to resolve the appropriate init function from the DLL.

        Different builds may export slightly different names, so we walk
        through a list of known possibilities.
        """
        candidate_names = [
            # Original / legacy symbols
            "init_ultrasound_ultrasound_usgfw2",
            "init_ultrasound_usgfw2",
            # Common MATLAB / wrapper variants
            "init_ultrasound_usgfw2MATLAB_wrapper",
            "init_ultrasound_wrapper",
        ]

        for name in candidate_names:
            if hasattr(usg, name):
                logger.info("Using init function '%s' from DLL", name)
                return getattr(usg, name)

        logger.error(
            "None of the expected init functions are present in DLL: %s",
            ", ".join(candidate_names),
        )
        return None

    # ------------------------------------------------------------------ #
    # Internal: frame acquisition
    # ------------------------------------------------------------------ #

    def _grab_frame_from_device(self) -> bytes:
        """
        Grab a single B-mode frame and return it as PNG bytes.

        Follows vendor realtime script logic:

        - allocate (c_uint * w*h*4)
        - call return_pixel_values()
        - reshape to (w, h, 4)
        - use first channel as grayscale
        - flip vertically to match origin='lower'
        """
        if self._usg is None:
            raise RuntimeError("DLL not loaded")

        usg = self._usg
        w, h = self.width, self.height

        # Allocate buffer: (c_uint * (w * h * 4))
        buffer_len = w * h * 4
        p_array = (c_uint * buffer_len)()

        # The DLL expects a `POINTER(c_uint)`. Passing `pointer(p_array)` can
        # create a pointer-to-array type which some ctypes signatures reject
        # (ArgumentError). Cast the array to the correct pointer type instead.
        rc = usg.return_pixel_values(ctypes.cast(p_array, ctypes.POINTER(c_uint)))

        # Some DLLs return void; others may return a status code.
        # Only treat negative as hard error if rc is an int.
        if isinstance(rc, int) and rc < 0:
            raise RuntimeError(f"return_pixel_values() returned error code {rc}")

        # Convert ctypes array to numpy array efficiently and safely
        buffer_np = np.ctypeslib.as_array(p_array)

        # Defensive check: if size mismatch, bail out
        expected = w * h * 4
        if buffer_np.size != expected:
            raise RuntimeError(
                f"Unexpected buffer size from device: got {buffer_np.size}, "
                f"expected {expected}"
            )

        reshaped = np.reshape(buffer_np, (h, w, 4))  # note: (height, width, channels)

        # Use first channel as grayscale
        bmode = reshaped[:, :, 0].astype(np.uint8)

        # Flip vertically to match origin='lower'
        bmode_flipped = np.flipud(bmode)

        # Overlay professional depth scale / markings
        png_bytes = self._render_with_scale(bmode_flipped)
        return png_bytes

    # ------------------------------------------------------------------ #
    # Background capture thread
    # ------------------------------------------------------------------ #
    def _start_capture_thread(self) -> None:
        if self._capture_thread is not None and self._capture_thread.is_alive():
            return
        self._capture_stop.clear()
        t = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread = t
        t.start()

    def _stop_capture_thread(self) -> None:
        self._capture_stop.set()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=1.0)
        self._capture_thread = None

    def _capture_loop(self) -> None:
        target_delay = 1.0 / max(1, int(self._target_fps))
        while not self._capture_stop.is_set() and self._initialized and self._usg is not None:
            start = time.time()
            try:
                png = self._grab_frame_from_device()
                # store last frame atomically
                with self._lock:
                    self._last_png = png
                    self._last_ready = True
            except Exception:
                logger.exception("Background frame capture failed")
                with self._lock:
                    self._last_png = self._placeholder_png
                    self._last_ready = False
            # Sleep to maintain target FPS
            end = time.time()
            elapsed = end - start
            to_sleep = target_delay - elapsed
            if to_sleep > 0:
                time.sleep(to_sleep)

    def _render_with_scale(self, bmode: np.ndarray) -> bytes:
        """
        Render the grayscale frame with an overlaid depth scale (mm markings).
        Returns PNG bytes.
        """
        # Create PIL image and convert to RGBA for overlay drawing
        img = Image.fromarray(bmode, mode="L").convert("RGBA")
        w, h = img.size

        # Determine vertical scaling. Prefer the probe's current depth setting
        # (from the B-mode control module) if available, otherwise use device
        # reported resolution (mm/px). If both are available, use the depth
        # setting since the user expects the ruler to match the displayed depth.
        display_depth_mm: Optional[float] = None
        px_per_mm: Optional[float] = None

        # Try to get the B-mode control depth setting (preferred)
        try:
            # import here to avoid circular imports at module load time
            from app.usg_ultrasound import get_usg_instance

            usg = get_usg_instance()
            if usg is not None and usg.initialized:
                st = usg.get_state()
                if getattr(st, "depth_mm", None):
                    display_depth_mm = float(st.depth_mm)
        except Exception:
            # Best-effort: ignore errors and continue with other fallbacks
            logger.debug("Could not query B-mode depth from control module")

        # If depth setting is not available, try device resolution
        if display_depth_mm is None and getattr(self, "_resolution", None):
            mm_per_px = float(self._resolution[1])
            if mm_per_px and mm_per_px > 0:
                px_per_mm = 1.0 / mm_per_px
                display_depth_mm = h / px_per_mm

        # Final fallback: assume a sensible default depth and resolution
        if display_depth_mm is None or display_depth_mm <= 0:
            display_depth_mm = 120.0  # default visible depth in mm
        if px_per_mm is None or px_per_mm <= 0:
            px_per_mm = h / display_depth_mm

        total_depth_mm = display_depth_mm

        # Choose a "nice" major interval (in mm) so we have ~4-8 ticks
        def _nice_interval(max_mm: float, target_ticks: int = 6) -> int:
            import math

            raw = max_mm / target_ticks
            if raw <= 0:
                return 10
            magnitude = 10 ** math.floor(math.log10(raw))
            for factor in (1, 2, 5):
                interval = factor * magnitude
                if raw <= interval:
                    return int(interval)
            return int(10 * magnitude)

        major_mm = _nice_interval(total_depth_mm, target_ticks=6)
        minor_div = 5
        minor_mm = max(1, major_mm // minor_div)

        # Visual positions
        margin_x = int(w * 0.93)
        tick_x = margin_x
        tick_length_major = int(w * 0.04)
        tick_length_minor = int(w * 0.02)

        # Prepare overlay and drawing context
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)

        # Semi-transparent background for the scale area for readability
        bg_x0 = margin_x - int(w * 0.01)
        bg_x1 = w
        od.rectangle([bg_x0, 0, bg_x1, h], fill=(0, 0, 0, 90))

        # Load a TTF font if available; fallback to default font
        font = None
        font_size = max(12, int(w * 0.018))
        for fname in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
            try:
                font = ImageFont.truetype(fname, font_size)
                break
            except Exception:
                font = None
        if font is None:
            font = ImageFont.load_default()

        # Draw ticks and labels
        # iterate depth from 0 to total_depth_mm
        import math

        # ensure label at depth 0 (top) and at increasing depths
        max_depth = int(math.ceil(total_depth_mm))
        for depth in range(0, max_depth + 1, minor_mm):
            # depth is in mm from top; convert to pixel y coordinate
            y = int(round((depth / total_depth_mm) * h))
            if y < 0 or y > h:
                continue
            if depth % major_mm == 0:
                # major tick
                od.line([(tick_x, y), (tick_x + tick_length_major, y)], fill=(255, 255, 255, 220), width=2)
                # label
                label = f"{depth} mm" if depth < 1000 else f"{depth/10:.1f} cm"
                # draw shadow then text
                tx = tick_x + tick_length_major + int(w * 0.01)
                ty = y - font_size // 2
                od.text((tx + 1, ty + 1), label, font=font, fill=(0, 0, 0, 180))
                od.text((tx, ty), label, font=font, fill=(255, 255, 255, 230))
            else:
                # minor tick
                od.line([(tick_x, y), (tick_x + tick_length_minor, y)], fill=(200, 200, 200, 180), width=1)

        # Draw a depth range label at the top of the scale for clarity
        try:
            depth_label = f"Depth: 0 - {int(round(total_depth_mm))} mm"
            od.text((bg_x0 + 6, 6), depth_label, font=font, fill=(255, 255, 255, 230))
        except Exception:
            pass

        # Composite overlay onto image
        composed = Image.alpha_composite(img, overlay).convert("RGB")

        # Save to PNG
        buf = io.BytesIO()
        composed.save(buf, format="PNG")
        return buf.getvalue()

    # ------------------------------------------------------------------ #
    # Internal: placeholder frame
    # ------------------------------------------------------------------ #

    def _create_placeholder_png(self) -> bytes:
        """
        Create a black image used when the probe is not ready or an error occurs.
        """
        w, h = self.width, self.height
        black = np.zeros((h, w), dtype=np.uint8)  # L-mode grayscale
        img = Image.fromarray(black, mode="L")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()