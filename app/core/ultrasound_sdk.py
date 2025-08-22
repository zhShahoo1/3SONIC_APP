"""
Ultrasound SDK wrapper (singleton).

Refactors original `ultrasound.py`:
- Loads `usgfw2wrapper.dll` once (singleton) and initializes the probe.
- Provides a JPEG frame generator for Flask streaming (with scalebar).
- Exposes helpers for grabbing raw RGBA frames, freezing/stopping/closing.
- Keeps the original scalebar logic & digit drawings inlined here.
- Uses Config paths and is EXE-friendly.

Public functions:
    initialize_ultrasound() -> (w, h, (resX, resY))
    generate_image() -> yields JPEG bytes
    grab_rgba_frame() -> numpy array (H, W, 4) uint8
    get_resolution() -> (resX, resY)
    freeze(), stop(), close()  # map to underlying DLL controls
"""

from __future__ import annotations
import ctypes
from ctypes import cdll, c_uint32, c_float, pointer
from typing import Tuple, Generator

import numpy as np
import cv2

from app.config import Config


# -------------------------------------------------------------------
# Singleton Loader / Driver
# -------------------------------------------------------------------

class _UltrasoundDLL:
    _instance: "_UltrasoundDLL" | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_dll()
        return cls._instance

    def _init_dll(self):
        # Accept both old and new naming in Config
        w = getattr(Config, "ULTRASOUND_WIDTH", getattr(Config, "ULTRA_W", 1024))
        h = getattr(Config, "ULTRASOUND_HEIGHT", getattr(Config, "ULTRA_H", 1024))
        self._w = int(w)
        self._h = int(h)

        # Resolve DLL path via Config helper (works in EXE & dev)
        dll_path = Config.dll_path()
        if not dll_path.exists():
            raise RuntimeError(f"Ultrasound DLL not found at {dll_path}")

        self._dll = cdll.LoadLibrary(str(dll_path))
        self._initialized = False
        self._res = (0.0, 0.0)

        # perform initialization sequence (mirrors original with exceptions instead of sys.exit)
        self._on_init()

    def _on_init(self):
        self._dll.on_init()
        ERR = self._dll.init_ultrasound_usgfw2()
        if ERR == 2:
            self._dll.Close_and_release()
            raise RuntimeError("Main usgfw2 library object not created")

        ERR = self._dll.find_connected_probe()
        if ERR != 101:
            self._dll.Close_and_release()
            raise RuntimeError("Ultrasound probe not detected")

        ERR = self._dll.data_view_function()
        if ERR < 0:
            raise RuntimeError("Main ultrasound scanning object for selected probe not created")

        ERR = self._dll.mixer_control_function(0, 0, self._w, self._h, 0, 0, 0)
        if ERR < 0:
            self._dll.Close_and_release()
            raise RuntimeError("B mixer control not returned")

        # query resolution
        res_X = c_float(0.0)
        res_Y = c_float(0.0)
        self._dll.get_resolution(pointer(res_X), pointer(res_Y))
        self._res = (res_X.value, res_Y.value)

        self._initialized = True
        print(f"[Ultrasound] Initialized {self._w}x{self._h}, resolution={self._res}")

    @property
    def dll(self):
        return self._dll

    @property
    def resolution(self) -> Tuple[float, float]:
        return self._res

    @property
    def size(self) -> Tuple[int, int]:
        # return (width, height)
        return (self._w, self._h)

    # Convenience controls used by record/teardown
    def freeze(self):
        try:
            self._dll.Freeze_ultrasound_scanning()
        except Exception:
            pass

    def stop(self):
        try:
            self._dll.Stop_ultrasound_scanning()
        except Exception:
            pass

    def close(self):
        try:
            self._dll.Close_and_release()
        except Exception:
            pass


# -------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------

def initialize_ultrasound() -> Tuple[int, int, Tuple[float, float]]:
    """
    Initialize ultrasound once and return (width, height, (resX, resY)).
    """
    inst = _UltrasoundDLL()
    w, h = inst.size  # (W, H)
    return w, h, inst.resolution


def grab_rgba_frame() -> np.ndarray:
    """
    Return a single RGBA frame as numpy array (H, W, 4) uint8.
    """
    inst = _UltrasoundDLL()
    w, h = inst.size
    dll = inst.dll

    p_array = (c_uint32 * w * h * 4)()
    dll.return_pixel_values(pointer(p_array))
    buffer = np.frombuffer(p_array, np.uint32)
    # Original code reshapes as (w, h, 4); we return (h, w, 4) for conventional image layout
    reshaped = np.reshape(buffer, (w, h, 4)).astype(np.uint8)
    return np.transpose(reshaped, (1, 0, 2))  # (H, W, 4)


def get_resolution() -> Tuple[float, float]:
    """
    Return (resX, resY) in mm/pixel (as reported by the DLL).
    """
    inst = _UltrasoundDLL()
    return inst.resolution


def freeze() -> None:
    _UltrasoundDLL().freeze()


def stop() -> None:
    _UltrasoundDLL().stop()


def close() -> None:
    _UltrasoundDLL().close()


def generate_image() -> Generator[bytes, None, None]:
    """
    Generator yielding JPEG-encoded ultrasound frames with a scalebar.
    Matches Flask streaming needs: Content-Type image/jpeg.

    Note: We compute the scalebar mask once (first frame) to save time.
    """
    inst = _UltrasoundDLL()
    w, h = inst.size
    res = inst.resolution
    dll = inst.dll

    p_array = (c_uint32 * w * h * 4)()

    # priming read to build mask
    dll.return_pixel_values(pointer(p_array))
    arr = np.frombuffer(p_array, np.uint32).reshape((w, h, 4))
    mask = get_mask(arr, res)

    while True:
        dll.return_pixel_values(pointer(p_array))
        buffer = np.frombuffer(p_array, np.uint32)

        reshaped = np.reshape(buffer, (w, h, 4))

        # add scalebar
        reshaped = draw_scale_bar(reshaped, mask, 255)
        reshaped = np.clip(reshaped, 0, 255).astype(np.uint8)

        # Flip vertically like original code
        reshaped = cv2.flip(reshaped, 0)

        # Encode as JPEG using only RGB channels
        ok, jpeg = cv2.imencode(".jpg", reshaped[:, :, 0:3])
        if not ok:
            continue
        yield jpeg.tobytes()


# -------------------------------------------------------------------
# Scale Bar + Digits (ported and inlined)
# -------------------------------------------------------------------

def get_mask(image: np.ndarray, resolution: Tuple[float, float]) -> np.ndarray:
    """
    Build a boolean mask for the scalebar and digits (0..6).
    image: numpy array shaped (W, H, 4) as per original DLL reshaping.
    resolution: (resX, resY) mm/pixel
    """
    # The original code flips first on axis=0 based on (w,h,4) layout
    img = np.flip(image, axis=0)

    # how many pixels correspond to 2.5 mm along X resolution
    twohalf_mm = int(2.5 / resolution[0])
    col_idx = int(0.95 * img.shape[1])

    mask = np.zeros(img.shape[:2], dtype=bool)
    for i in range(0, img.shape[0], twohalf_mm):
        if i % 4 == 0:
            mask[i:i + 6, col_idx - 15: col_idx + 15] = True
        elif i % 2 == 0:
            mask[i:i + 4, col_idx - 10: col_idx + 10] = True
        else:
            mask[i:i + 2, col_idx - 5: col_idx + 5] = True

    # add numbers
    col_numbers = int(0.98 * img.shape[1])
    zero_location = (25, col_numbers)
    one_location = (twohalf_mm * 4, col_numbers)
    two_location = (twohalf_mm * 8, col_numbers)

    mask = draw_zero(mask, zero_location)
    mask = draw_one(mask, one_location)
    mask = draw_two(mask, two_location)

    if twohalf_mm >= 12:
        three_location = (twohalf_mm * 12, col_numbers)
        mask = draw_three(mask, three_location)

    if twohalf_mm >= 16:
        four_location = (twohalf_mm * 16 + 10, col_numbers)
        mask = draw_four(mask, four_location)

    if twohalf_mm >= 20:
        five_location = (twohalf_mm * 20, col_numbers)
        mask = draw_five(mask, five_location)

    if twohalf_mm >= 24:
        six_location = (twohalf_mm * 24, col_numbers)
        mask = draw_six(mask, six_location)

    # flip mask back
    return np.flip(mask, axis=0)


def draw_scale_bar(image: np.ndarray, mask: np.ndarray, value: int) -> np.ndarray:
    image[mask] = value
    return image


# ---- Digit drawing routines (0..6), ported from original ----

def draw_zero(mask: np.ndarray, location: tuple) -> np.ndarray:
    width = 12
    height = int(1.8 * width)
    t = np.linspace(0, 2 * np.pi, 100)
    for ang in t:
        r = (int(location[0] + height * np.sin(ang)), int(location[1] + width * np.cos(ang)))
        mask[r[0] - 2: r[0] + 2, r[1] - 2: r[1] + 2] = True
    return mask


def draw_one(mask: np.ndarray, location: tuple) -> np.ndarray:
    height = 35
    mask[int(location[0] - height / 2): int(location[0] + height / 2), location[1] - 2: location[1] + 2] = True
    mask[int(location[0] + height / 1.8 - 2): int(location[0] + height / 1.8 + 3),
         int(location[1] - height / 3): int(location[1] + height / 3)] = True
    for i in range(0, int(height / 3)):
        mask[int(location[0] - height / 2 + i): int(location[0] - height / 2 + i + 6),
             int(location[1] - 2 - i)] = True
    return mask


def draw_two(mask: np.ndarray, location: tuple) -> np.ndarray:
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


def draw_three(mask: np.ndarray, location: tuple) -> np.ndarray:
    height = 45
    t = np.linspace(3 * np.pi / 2 - 0.6, 5 * np.pi / 2, 100)
    for ang in t:
        r = (int(location[0] - 5 + height / 2 - (height / 1.8) * 0.45 * np.sin(ang)) - 2,
             int(location[1] + (height / 1.8) * 0.45 * np.cos(ang)) - 5)
        mask[r[0] - 2: r[0] + 2, r[1] - 2: r[1] + 2] = True

    # last point approximations reused to compute diagonal/horizontal; this mimics original logic
    p1 = int(location[0] - 5 + height / 2 - (height / 1.8) * 0.55 * np.sin(ang)) - 2
    p2 = int(location[1] - 5 + (height / 1.8) * 0.55 * np.cos(ang))

    for i in range(0, int(height / 3.5)):
        mask[p1 - 2 - i: p1 + 2 - i, p2 - 2 + i: p2 + 2 + i] = True

    mask[int(location[0] - 5 - height / 5): int(location[0] - 5 - height / 5 + 5),
         int(location[1] - height / 3 + 2): int(location[1] + height / 7 + 3)] = True
    return mask


def draw_four(mask: np.ndarray, location: tuple) -> np.ndarray:
    height = 45
    mask[int(location[0] - height / 2): int(location[0] + height / 2), location[1]: location[1] + 6] = True
    mask[int(location[0] + height / 8): int(location[0] + height / 8 + 6),
         int(location[1] - height / 2.5): location[1] + 14] = True
    for i in range(0, int(height / 1.8)):
        mask[int(location[0] - height / 8 - i + 8): int(location[0] - height / 8 - i + 14),
             int(location[1] - height / 2.5 + 1 + 0.72 * i - 1): int(location[1] - height / 2.5 + 0.72 * i + 4)] = True
    return mask


def draw_five(mask: np.ndarray, location: tuple) -> np.ndarray:
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


def draw_six(mask: np.ndarray, location: tuple) -> np.ndarray:
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
