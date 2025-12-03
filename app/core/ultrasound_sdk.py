# app/core/ultrasound_sdk.py
"""
Ultrasound SDK wrapper (resilient singleton with auto-reconnect).

- Initializes usgfw2wrapper.dll once, with thread-safe reinit if the probe
  disconnects/reconnects.
- `generate_image()` never dies: it yields JPEG frames continuously. When the
  probe is missing, it yields a placeholder with "No probe / reconnecting…".
- Orientation for the LIVE stream is configurable via app.config.Config:
    ULTRA_VFLIP_LIVE: bool = False   # flip vertically for live MJPEG
    ULTRA_HFLIP_LIVE: bool = False   # flip horizontally for live MJPEG
- Public helpers:
    initialize_ultrasound() -> (w, h, (resX, resY))
    generate_image() -> yields JPEG bytes (RGB, MJPEG stream-friendly)
    grab_rgba_frame() -> np.ndarray (H, W, 4) uint8
    get_resolution() -> (resX, resY)
    freeze(), stop(), close(), reset()  # controls
"""

from __future__ import annotations
import time
import threading
from typing import Tuple, Generator, Optional

import numpy as np
import cv2
from ctypes import cdll, c_uint32, c_float, pointer
from PIL import Image, ImageDraw, ImageFont

from app.config import Config

# -------------------------------------------------------------------
# Digit + scalebar drawing (kept as in your version)
# -------------------------------------------------------------------

def draw_scale_bar(image: np.ndarray, mask: np.ndarray, value: int) -> np.ndarray:
    image[mask] = value
    return image

def draw_zero(mask, location):
    width = 12
    height = int(1.8 * width)
    t = np.linspace(0, 2 * np.pi, 100)
    for ang in t:
        r = (int(location[0] + height * np.sin(ang)), int(location[1] + width * np.cos(ang)))
        mask[r[0] - 2: r[0] + 2, r[1] - 2: r[1] + 2] = True
    return mask

def draw_one(mask, location):
    height = 35
    mask[int(location[0] - height / 2): int(location[0] + height / 2), location[1] - 2: location[1] + 2] = True
    mask[int(location[0] + height / 1.8 - 2): int(location[0] + height / 1.8 + 3),
         int(location[1] - height / 3): int(location[1] + height / 3)] = True
    for i in range(0, int(height / 3)):
        mask[int(location[0] - height / 2 + i): int(location[0] - height / 2 + i + 6),
             int(location[1] - 2 - i)] = True
    return mask

def draw_two(mask, location):
    height = 45
    for i in range(-8, int(height / 3 - 1)):
        mask[int(location[0] + height / 2 + 2 + i - 18): int(location[0] + height / 2 + 2 + i - 12),
             int(location[1] - 2 - i)] = True
    mask[int(location[0] + height / 2 - 2): int(location[0] + height / 2 + 3),
         int(location[1] - height / 3 - 1): int(location[1] + height / 5.5)] = True
    t = np.linspace(np.pi + 0.2, 2 * np.pi - 0.3, 100)
    for ang in t:
        r = (int(location[0] + (height / 2) * 0.45 * np.sin(ang)) + 2,
             int(location[1] + (height / 2) * 0.45 * np.cos(ang)) - 5)
        mask[r[0] - 2: r[0] + 2, r[1] - 2: r[1] + 2] = True
    return mask

def draw_three(mask, location):
    height = 45
    t = np.linspace(3 * np.pi / 2 - 0.6, 5 * np.pi / 2, 100)
    for ang in t:
        r = (int(location[0] - 5 + height / 2 - (height / 1.8) * 0.45 * np.sin(ang)) - 2,
             int(location[1] + (height / 1.8) * 0.45 * np.cos(ang)) - 5)
        mask[r[0] - 2: r[0] + 2, r[1] - 2: r[1] + 2] = True
    # Rough diagonal + horizontal bars
    p1 = int(location[0] - 5 + height / 2 - (height / 1.8) * 0.55 * np.sin(ang)) - 2
    p2 = int(location[1] - 5 + (height / 1.8) * 0.55 * np.cos(ang))
    for i in range(0, int(height / 3.5)):
        mask[p1 - 2 - i: p1 + 2 - i, p2 - 2 + i: p2 + 2 + i] = True
    mask[int(location[0] - 5 - height / 5): int(location[0] - 5 - height / 5 + 5),
         int(location[1] - height / 3 + 2): int(location[1] + height / 7 + 3)] = True
    return mask

def draw_four(mask, location):
    height = 45
    mask[int(location[0] - height / 2): int(location[0] + height / 2), location[1]: location[1] + 6] = True
    mask[int(location[0] + height / 8): int(location[0] + height / 8 + 6),
         int(location[1] - height / 2.5): location[1] + 14] = True
    for i in range(0, int(height / 1.8)):
        mask[int(location[0] - height / 8 - i + 8): int(location[0] - height / 8 - i + 14),
             int(location[1] - height / 2.5 + 1 + 0.72 * i - 1): int(location[1] - height / 2.5 + 0.72 * i + 4)] = True
    return mask

def draw_five(mask, location):
    height = 45
    mask[int(location[0] - height / 2.5): int(location[0] - height / 2.5 + 6),
         location[1]: int(location[1] + height / 3)] = True
    mask[int(location[0] - height / 2.5): int(location[0]),
         location[1] - 2: location[1] + 3] = True
    t = np.linspace(-np.pi / 2, 1.1 * np.pi / 2, 100)
    for ang in t:
        r = (int(location[0] + height / 5 + (height / 5) * np.sin(ang)),
             int(location[1] + (height / 3) * np.cos(ang)))
        mask[r[0] - 2: r[0] + 2, r[1] - 2: r[1] + 2] = True
    return mask

def draw_six(mask, location):
    height = 45
    t = np.linspace(0, 2 * np.pi, 100)
    for ang in t:
        r = (int(location[0] + height / 3.5 * np.sin(ang)),
             int(location[1] + 5 + height / 4 * np.cos(ang)))
        mask[r[0] - 2: r[0] + 2, r[1] - 2: r[1] + 2] = True
    for i in range(0, int(height / 3)):
        mask[int(location[0] - 5 - 2 * i): int(location[0] - 5 - 2 * i + 10),
             int(location[1] - height / 8 - 2 + i)] = True
    return mask

def get_mask(image: np.ndarray, resolution: Tuple[float, float]) -> np.ndarray:
    # Build tick/number mask on a vertically flipped copy, then unflip back.
    img = np.flip(image, axis=0)
    twohalf_mm = max(1, int(2.5 / max(resolution[0], 1e-6)))
    col_idx = int(0.95 * img.shape[1])

    mask = np.zeros(img.shape[:2], dtype=bool)
    for i in range(0, img.shape[0], twohalf_mm):
        if i % 4 == 0:
            mask[i:i + 6, col_idx - 15: col_idx + 15] = True
        elif i % 2 == 0:
            mask[i:i + 4, col_idx - 10: col_idx + 10] = True
        else:
            mask[i:i + 2, col_idx - 5: col_idx + 5] = True

    col_numbers = int(0.98 * img.shape[1])
    zero_location = (25, col_numbers)
    one_location  = (twohalf_mm * 4, col_numbers)
    two_location  = (twohalf_mm * 8, col_numbers)

    mask = draw_zero(mask, zero_location)
    mask = draw_one(mask, one_location)
    mask = draw_two(mask, two_location)

    if twohalf_mm >= 12:
        mask = draw_three(mask, (twohalf_mm * 12, col_numbers))
    if twohalf_mm >= 16:
        mask = draw_four(mask, (twohalf_mm * 16 + 10, col_numbers))
    if twohalf_mm >= 20:
        mask = draw_five(mask, (twohalf_mm * 20, col_numbers))
    if twohalf_mm >= 24:
        mask = draw_six(mask, (twohalf_mm * 24, col_numbers))

    return np.flip(mask, axis=0)

# -------------------------------------------------------------------
# Resilient singleton for the DLL
# -------------------------------------------------------------------

class _UltrasoundDLL:
    _instance: Optional["_UltrasoundDLL"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._dll = None
                inst._initialized = False
                inst._res = (0.0, 0.0)
                inst._w = int(getattr(Config, "ULTRASOUND_WIDTH", getattr(Config, "ULTRA_W", 1024)))
                inst._h = int(getattr(Config, "ULTRASOUND_HEIGHT", getattr(Config, "ULTRA_H", 1024)))
                inst._placeholders = {}
                cls._instance = inst
        return cls._instance

    # ---------- internal init / teardown ----------

    def _attempt_init(self) -> bool:
        """Try full init once. Returns True on success."""
        dll_path = Config.dll_path()
        if not dll_path.exists():
            print(f"[Ultrasound] DLL not found at {dll_path}")
            return False

        try:
            self._dll = cdll.LoadLibrary(str(dll_path))
            self._dll.on_init()

            err = self._dll.init_ultrasound_usgfw2()
            if err == 2:
                print("[Ultrasound] Main library object not created")
                self._dll.Close_and_release()
                self._dll = None
                return False

            err = self._dll.find_connected_probe()
            if err != 101:
                print("[Ultrasound] Probe not detected")
                try:
                    self._dll.Close_and_release()
                except Exception:
                    pass
                self._dll = None
                return False

            err = self._dll.data_view_function()
            if err < 0:
                print("[Ultrasound] data_view_function failed")
                return False

            err = self._dll.mixer_control_function(0, 0, self._w, self._h, 0, 0, 0)
            if err < 0:
                print("[Ultrasound] mixer_control_function failed")
                try:
                    self._dll.Close_and_release()
                except Exception:
                    pass
                self._dll = None
                return False

            res_X = c_float(0.0)
            res_Y = c_float(0.0)
            self._dll.get_resolution(pointer(res_X), pointer(res_Y))
            self._res = (float(res_X.value), float(res_Y.value))
            self._initialized = True
            print(f"[Ultrasound] Initialized {self._w}x{self._h}, res={self._res}")
            return True
        except Exception as e:
            print(f"[Ultrasound] Init error: {e}")
            self._dll = None
            self._initialized = False
            return False

    def _reconnect_with_backoff(self, max_wait_s: float = 30.0):
        """Loop with backoff until initialized (or time budget exhausted)."""
        t0 = time.time()
        delay = 0.5
        while time.time() - t0 < max_wait_s and not self._initialized:
            print(f"[Ultrasound] Reconnect attempt, waiting {delay:.1f}s…")
            time.sleep(delay)
            with self._lock:
                if self._initialized:
                    break
                self._initialized = self._attempt_init()
            if self._initialized:
                break
            delay = min(delay * 1.6, 5.0)  # bounded exponential backoff

    # ---------- public control ----------

    def ensure_ready(self) -> bool:
        with self._lock:
            if self._initialized:
                return True
            return self._attempt_init()

    def reset(self):
        """Force close + allow next call to re-init."""
        with self._lock:
            try:
                if self._dll:
                    try:
                        self._dll.Freeze_ultrasound_scanning()
                    except Exception:
                        pass
                    try:
                        self._dll.Stop_ultrasound_scanning()
                    except Exception:
                        pass
                    try:
                        self._dll.Close_and_release()
                    except Exception:
                        pass
            finally:
                self._dll = None
                self._initialized = False
                print("[Ultrasound] Reset.")

    def freeze(self):
        try:
            if self._dll:
                self._dll.Freeze_ultrasound_scanning()
        except Exception:
            pass

    def stop(self):
        try:
            if self._dll:
                self._dll.Stop_ultrasound_scanning()
        except Exception:
            pass

    def close(self):
        self.reset()

    # ---------- data paths ----------

    @property
    def dll(self):  # may be None if not initialized
        return self._dll

    @property
    def resolution(self) -> Tuple[float, float]:
        return self._res

    @property
    def size(self) -> Tuple[int, int]:
        return (self._w, self._h)

    def _placeholder_rgb(self, msg: str) -> np.ndarray:
        """Build or reuse a simple placeholder RGB image."""
        key = (self._w, self._h, msg)
        if key in self._placeholders:
            return self._placeholders[key]
        img = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        cv2.putText(img, msg, (20, int(self._h * 0.5)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (200, 200, 200), 2, cv2.LINE_AA)
        self._placeholders[key] = img
        return img

# -------------------------------------------------------------------
# Public API (module functions)
# -------------------------------------------------------------------

def initialize_ultrasound() -> Tuple[int, int, Tuple[float, float]]:
    inst = _UltrasoundDLL()
    inst.ensure_ready()  # don't raise; just try once
    w, h = inst.size
    return w, h, inst.resolution

def grab_rgba_frame() -> np.ndarray:
    """
    Return a single RGBA frame as (H, W, 4) uint8 WITHOUT applying live-view flips.
    Use generate_image() for the MJPEG stream with optional flipping.
    """
    inst = _UltrasoundDLL()
    if not inst.ensure_ready():
        # Return a tiny blank to avoid exceptions if called before init
        return np.zeros((1, 1, 4), dtype=np.uint8)

    w, h = inst.size
    dll = inst.dll
    p_array = (c_uint32 * w * h * 4)()
    dll.return_pixel_values(pointer(p_array))
    buffer = np.frombuffer(p_array, np.uint32)
    reshaped = np.reshape(buffer, (w, h, 4)).astype(np.uint8)
    return np.transpose(reshaped, (1, 0, 2))  # (H, W, 4)

def get_resolution() -> Tuple[float, float]:
    return _UltrasoundDLL().resolution


def is_connected() -> bool:
    """Return True if the ultrasound DLL/probe is currently initialized.

    This is a lightweight, non-blocking check that inspects the singleton
    state rather than attempting a full re-initialization.
    """
    try:
        inst = _UltrasoundDLL()
        return bool(getattr(inst, "_initialized", False) or getattr(inst, "_dll", None) is not None)
    except Exception:
        return False

def freeze() -> None:
    _UltrasoundDLL().freeze()

def stop() -> None:
    _UltrasoundDLL().stop()

def close() -> None:
    _UltrasoundDLL().close()

def reset() -> None:
    _UltrasoundDLL().reset()

def _apply_live_orientation(rgb: np.ndarray) -> np.ndarray:
    """
    Apply optional flips for the LIVE MJPEG stream only.
    Controlled by Config.ULTRA_VFLIP_LIVE and ULTRA_HFLIP_LIVE (default False).
    """
    if getattr(Config, "ULTRA_VFLIP_LIVE", False):
        rgb = cv2.flip(rgb, 0)  # vertical
    if getattr(Config, "ULTRA_HFLIP_LIVE", False):
        rgb = cv2.flip(rgb, 1)  # horizontal
    return rgb

def generate_image(*_unused, **_unused_kw) -> Generator[bytes, None, None]:
    """
    Yield JPEG frames forever.
    - Connected: live frames (RGB) with scalebar, forced vertical flip applied.
    - Disconnected: placeholder frames with periodic reconnect attempts.
    """
    inst = _UltrasoundDLL()
    mask = None

    while True:
        if not inst.ensure_ready():
            img = inst._placeholder_rgb("No probe / reconnecting…")
            ok, jpeg = cv2.imencode(".jpg", img)
            if ok:
                yield jpeg.tobytes()
            inst._reconnect_with_backoff(max_wait_s=5.0)
            continue

        try:
            w, h = inst.size
            dll = inst.dll
            p_array = (c_uint32 * w * h * 4)()
            dll.return_pixel_values(pointer(p_array))

            buffer = np.frombuffer(p_array, np.uint32)
            reshaped = np.reshape(buffer, (w, h, 4))  # keep native orientation

            # Refresh resolution from DLL in case user changed depth/settings
            try:
                res_X = c_float(0.0)
                res_Y = c_float(0.0)
                dll.get_resolution(pointer(res_X), pointer(res_Y))
                inst._res = (float(res_X.value), float(res_Y.value))
            except Exception:
                # keep previous resolution if query fails
                pass

            # RGB (drop alpha) and ensure 8-bit per channel
            rgb = reshaped[:, :, :3].astype(np.uint8)

            # Force the correct view: vertical flip (top↔bottom)
            rgb = cv2.flip(rgb, 0)

            # Overlay a professional depth sidebar using PIL drawing.
            try:
                rgb = _render_with_scale_pil(rgb, inst.resolution)
            except Exception:
                # If overlay fails, fall back to raw rgb
                pass

            ok, jpeg = cv2.imencode(".jpg", rgb)
            if ok:
                yield jpeg.tobytes()
            else:
                ph = inst._placeholder_rgb("Encoder error")
                ok2, jpeg2 = cv2.imencode(".jpg", ph)
                if ok2:
                    yield jpeg2.tobytes()

        except Exception as e:
            print(f"[Ultrasound] stream error: {e}")
            inst.reset()
            t0 = time.time()
            while time.time() - t0 < 3.0 and not inst.ensure_ready():
                ph = inst._placeholder_rgb("No probe / reconnecting…")
                ok, jpeg = cv2.imencode(".jpg", ph)
                if ok:
                    yield jpeg.tobytes()
                time.sleep(0.5)


def _render_with_scale_pil(rgb: np.ndarray, resolution: Tuple[float, float]) -> np.ndarray:
    """
    Render an RGB numpy image with a professional depth sidebar on the right.
    Returns a new RGB numpy array.
    """
    # Convert to PIL RGBA for overlay work
    img = Image.fromarray(rgb.astype('uint8'), mode="RGB").convert("RGBA")
    w, h = img.size

    # Estimate display depth (mm) from resolution if available
    display_depth_mm = None
    px_per_mm = None
    try:
        if resolution and resolution[1] and resolution[1] > 0:
            mm_per_px = float(resolution[1])
            px_per_mm = 1.0 / mm_per_px
            display_depth_mm = h / px_per_mm
    except Exception:
        display_depth_mm = None

    if display_depth_mm is None or display_depth_mm <= 0:
        display_depth_mm = 120.0
    if px_per_mm is None or px_per_mm <= 0:
        px_per_mm = h / display_depth_mm

    # Compute nice major tick interval to yield ~6 ticks
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

    major_mm = _nice_interval(display_depth_mm, target_ticks=6)
    minor_div = 5
    minor_mm = max(1, major_mm // minor_div)

    # Prepare overlay
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Sidebar area: rightmost 8% of width (tunable)
    sidebar_w = max(64, int(w * 0.08))
    bg_x0 = w - sidebar_w
    bg_x1 = w
    # No filled background: render ticks/labels transparently over the image

    # Tick geometry
    tick_x = bg_x0 + int(sidebar_w * 0.08)
    tick_major_len = int(sidebar_w * 0.35)
    tick_minor_len = int(sidebar_w * 0.18)

    # Font for labels (prefer a clean sans-serif if available)
    font = None
    font_size = max(18, int(sidebar_w * 0.45))
    for fname in ("DejaVuSans-Bold.ttf", "Arial.ttf", "LiberationSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(fname, font_size)
            break
        except Exception:
            font = None
    if font is None:
        font = ImageFont.load_default()

    # Draw ticks and labels
    import math
    max_depth = int(math.ceil(display_depth_mm))
    for depth in range(0, max_depth + 1, minor_mm):
        y = int(round((depth / display_depth_mm) * h))
        if y < 0 or y > h:
            continue
        if depth % major_mm == 0:
            # major tick
            draw.line([(tick_x, y), (tick_x + tick_major_len, y)], fill=(255, 255, 255, 220), width=2)
            # label (right-aligned inside sidebar)
            label = f"{depth}" if depth < 1000 else f"{depth/10:.1f}cm"
            tx = w - int(sidebar_w * 0.08)
            ty = y - font_size // 2
            # text shadow for contrast
            draw.text((tx - 1, ty + 1), label, font=font, fill=(0, 0, 0, 180), anchor="rm")
            draw.text((tx, ty), label, font=font, fill=(255, 255, 255, 230), anchor="rm")
        else:
            # minor tick
            draw.line([(tick_x, y), (tick_x + tick_minor_len, y)], fill=(220, 220, 220, 160), width=1)

    # Depth range label at top-left of sidebar
    try:
        depth_label = f"0 - {int(round(display_depth_mm))} mm"
        draw.text((bg_x0 + 6, 6), depth_label, font=font, fill=(200, 200, 200, 220))
    except Exception:
        pass

    # Composite and return RGB uint8 numpy
    composed = Image.alpha_composite(img, overlay).convert("RGB")
    return np.array(composed, dtype=np.uint8)



