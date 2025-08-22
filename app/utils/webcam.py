# app/utils/webcam.py
from __future__ import annotations

import cv2
import os
import threading
from typing import Optional, Generator


class _Camera:
    """
    Thread-safe wrapper around OpenCV VideoCapture.
    Provides consistent frame grabbing with fallback to placeholder image.
    """

    def __init__(self, index: int, placeholder: Optional[str] = None):
        self.index = index
        self.cap = cv2.VideoCapture(index)
        self._lock = threading.Lock()
        self.placeholder_path = placeholder

        if not self.cap.isOpened():
            raise RuntimeError(f"[Webcam] Failed to open camera at index {index}")

        print(f"[Webcam] External camera opened at index {index}")

    def read(self):
        """Thread-safe frame read."""
        with self._lock:
            ret, frame = self.cap.read()
        return ret, frame

    def release(self):
        """Release the camera resource."""
        with self._lock:
            if self.cap:
                self.cap.release()
                self.cap = None
                print(f"[Webcam] Camera at index {self.index} released.")


def find_external_camera(
    start_index: int = 1,
    max_index: int = 5,
    placeholder_dir: Optional[str] = None
) -> Optional[_Camera]:
    """
    Try to find an external camera.
    If none found, return None.
    """
    for i in range(start_index, max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            cap.release()
            placeholder = None
            if placeholder_dir:
                placeholder = os.path.join(placeholder_dir, "logo.png")
            return _Camera(i, placeholder)
    print("[Webcam] No external camera found, will use placeholder image.")
    return None


def generate_frames(camera: Optional[_Camera]) -> Generator[bytes, None, None]:
    """
    Yield frames for MJPEG streaming.
    If no camera, fall back to placeholder image.
    """
    if camera is None:
        placeholder_path = os.path.join(
            os.path.dirname(__file__), "..", "static", "images", "logo.png"
        )
        if os.path.exists(placeholder_path):
            with open(placeholder_path, "rb") as f:
                placeholder_image = f.read()
            while True:
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + placeholder_image + b"\r\n")
        else:
            print("[Webcam] Placeholder image missing at", placeholder_path)
            return

    while True:
        ret, frame = camera.read()
        if not ret:
            print("[Webcam] Failed to grab frame.")
            break

        ret, buffer = cv2.imencode(".jpg", frame)
        if not ret:
            continue
        frame_bytes = buffer.tobytes()

        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")


# Instantiate global camera on import
camera: Optional[_Camera] = find_external_camera(
    start_index=1,
    max_index=5,
    placeholder_dir=os.path.join(os.path.dirname(__file__), "..", "static", "images")
)
