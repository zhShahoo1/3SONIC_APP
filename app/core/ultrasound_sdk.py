# app/core/ultrasound_sdk.py
"""
Ultrasound SDK wrapper (resilient singleton with auto-reconnect).

- Initializes usgfw2wrapper.dll once, with thread-safe reinit if the probe
  disconnects/reconnects.
- `generate_image()` never dies: it yields JPEG frames continuously. When the
  probe is missing, it yields a placeholder with "No probe / reconnecting…".
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

from app.config import Config

# -------------------------------------------------------------------
# Digit + scalebar drawing (unchanged from your version)
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
    img = np.flip(image, axis=0)  # original layout (w,h,4)
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
    one_location = (twohalf_mm * 4, col_numbers)
    two_location = (twohalf_mm * 8, col_numbers)

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
# Resilient singleton
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
    inst = _UltrasoundDLL()
    if not inst.ensure_ready():
        # Return a 1x1 blank to avoid exceptions if someone calls this directly
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

def freeze() -> None:
    _UltrasoundDLL().freeze()

def stop() -> None:
    _UltrasoundDLL().stop()

def close() -> None:
    _UltrasoundDLL().close()

def reset() -> None:
    _UltrasoundDLL().reset()

def generate_image(*_unused, **_unused_kw) -> Generator[bytes, None, None]:
    """
    Yield JPEG frames forever.
    - When connected: live frames with scalebar (RGB).
    - When disconnected: periodic reconnect attempts + placeholder frames.
    """
    inst = _UltrasoundDLL()

    # Prime mask once we have a frame; until then, use a placeholder
    mask = None

    while True:
        if not inst.ensure_ready():
            # Yield a low-FPS placeholder while we attempt reconnect
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
            reshaped = np.reshape(buffer, (w, h, 4))

            # build scalebar mask once (cheap to reuse)
            if mask is None:
                mask = get_mask(reshaped, inst.resolution)

            reshaped = draw_scale_bar(reshaped, mask, 255)
            reshaped = np.clip(reshaped, 0, 255).astype(np.uint8)
            reshaped = cv2.flip(reshaped, 0)        # match original orientation
            rgb = reshaped[:, :, :3]                # RGB

            ok, jpeg = cv2.imencode(".jpg", rgb)
            if ok:
                yield jpeg.tobytes()
            else:
                # Rare encoder hiccup: yield placeholder once
                img = inst._placeholder_rgb("Encoder error")
                ok2, jpeg2 = cv2.imencode(".jpg", img)
                if ok2:
                    yield jpeg2.tobytes()

        except Exception as e:
            # Any DLL/USB error -> reset and try to reconnect while streaming placeholders
            print(f"[Ultrasound] stream error: {e}")
            inst.reset()
            # short backoff loop with placeholders
            t0 = time.time()
            while time.time() - t0 < 3.0 and not inst.ensure_ready():
                img = inst._placeholder_rgb("No probe / reconnecting…")
                ok, jpeg = cv2.imencode(".jpg", img)
                if ok:
                    yield jpeg.tobytes()
                time.sleep(0.5)
            # then loop back and try live again
