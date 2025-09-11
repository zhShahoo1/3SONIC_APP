# app/scripts/multisweep.py
from __future__ import annotations

"""
3SONIC — MultiSweep merge
-------------------------
Merges the two most recent VALID scan folders (older = left, newer = right)
into a single DICOM series by horizontally stitching with overlap blending.

Key improvements:
- Robust config.txt parsing by KEY (no fragile row indices).
- Dynamic image size (no 1024×1024 assumption).
- Clears existing DICOMs in the destination folder to avoid mixed sizes.
- Fresh SeriesInstanceUID for the merged volume.
"""

import sys
import re
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pydicom
import pydicom.uid
import shutil as sh
from tqdm import tqdm
from skimage.io import imread
from skimage.color import rgb2gray

# --------------------------------------------------------------------------
# Import handling: supports
#   1) python -m app.scripts.multisweep
#   2) python app/scripts/multisweep.py
# --------------------------------------------------------------------------
if __package__ in (None, ""):
    THIS_FILE = Path(__file__).resolve()
    PROJECT_ROOT = THIS_FILE.parents[2]  # .../<project-root>
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from app.config import Config
    from app.core.ultrasound_sdk import get_mask, draw_scale_bar
    from app.scripts.postprocessing import make_popup_figure
else:
    from ...config import Config
    from ...core.ultrasound_sdk import get_mask, draw_scale_bar
    from .postprocessing import make_popup_figure


# --------------------------------------------------------------------------
# Robust config.txt parsing (key: value; per line)
# --------------------------------------------------------------------------
def _read_config_map(scan_dir: Path) -> dict[str, str]:
    """
    Parse config.txt into {key -> value} by splitting on the first colon
    and trimming the trailing ';'. Tolerates extra keys and arbitrary order.
    """
    cfg: dict[str, str] = {}
    p = scan_dir / "config.txt"
    if not p.exists():
        return cfg
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        k, rest = line.split(":", 1)
        cfg[k.strip()] = rest.strip().rstrip(";")
    return cfg


def _get_float(cfg: dict[str, str], key: str, default: float | None = None) -> float:
    v = cfg.get(key, "")
    try:
        return float(v)
    except Exception:
        if default is not None:
            return float(default)
        raise ValueError(f"Bad float for '{key}': {v!r}")


def _extract_y_from_positions(cfg: dict[str, str]) -> float:
    """
    Parse Y from the 'POSTIONS ' (note trailing space) line, e.g.:
      'X:40.00 Y:63.50 Z:10.00 E:0.00 Count ...'
    Falls back to key without trailing space if needed.
    """
    raw = cfg.get("POSTIONS ", None)
    if raw is None:
        raw = cfg.get("POSTIONS", "")
    m = re.search(r"\bY:([+-]?\d+(?:\.\d+)?)\b", raw)
    if not m:
        raise ValueError(f"Could not extract Y from POSTIONS line: {raw!r}")
    return float(m.group(1))


# --------------------------------------------------------------------------
# Scan folder discovery helpers
# --------------------------------------------------------------------------
def _is_valid_scan_dir(p: Path) -> bool:
    try:
        return (
            p.is_dir()
            and (p / "config.txt").exists()
            and (p / "frames").is_dir()
            and any((p / "frames").glob("*.png"))
        )
    except Exception:
        return False


def _list_recent_scan_dirs(root: Path, limit: int = 10) -> list[Path]:
    scans = [d for d in root.iterdir() if _is_valid_scan_dir(d)]

    def _sort_key(x: Path):
        # Folder names are timestamps → lexicographic works; mtime as tie-breaker
        return (x.name, x.stat().st_mtime)

    scans.sort(key=_sort_key, reverse=True)
    return scans[:limit]


def _frames_sorted_by_index(frames_dir: Path) -> list[str]:
    paths = list(frames_dir.glob("*.png"))
    if not paths:
        return []
    try:
        paths.sort(key=lambda p: int(p.stem))
    except Exception:
        paths.sort(key=lambda p: p.name)
    return [str(p) for p in paths]


# --------------------------------------------------------------------------
# Core flow (key-based parsing + safe DICOM writing)
# --------------------------------------------------------------------------
def extract_parameters() -> tuple[List[str], List[str], List[float], Path, Path, float, float]:
    """
    Read recdir → parent → pick the two most recent VALID scan dirs → return:
      frames1, frames2, [x_res, y_res, e_r], processdir1(older), processdir2(newer),
      ypos1, ypos2
    """
    try:
        processdir = Path(Config.RECDIR_FILE.read_text().strip())
    except Exception as e:
        print(f"[multisweep] ⚠ failed reading recdir: {e}")
        sys.exit(1)

    parent = processdir.parent
    candidates = _list_recent_scan_dirs(parent, limit=10)

    if len(candidates) < 2:
        print("[multisweep] not enough valid scan dirs to merge")
        print("  found:", [c.name for c in candidates])
        sys.exit(1)

    # newest first → (dir2 = newest, dir1 = previous/older)
    processdir2, processdir1 = candidates[0], candidates[1]
    print("[multisweep] merging:")
    print(" dir1:", processdir1)
    print(" dir2:", processdir2)

    frames1 = _frames_sorted_by_index(processdir1 / "frames")
    frames2 = _frames_sorted_by_index(processdir2 / "frames")
    if not frames1 or not frames2:
        print("[multisweep] one of the scan folders has no frames")
        sys.exit(1)

    # Parse configs by key
    cfg1 = _read_config_map(processdir1)
    cfg2 = _read_config_map(processdir2)

    try:
        x_res = _get_float(cfg1, "Xres")
        y_res = _get_float(cfg1, "Yres")
        e_r   = _get_float(cfg1, "e_r setpoint")
    except Exception as e:
        print(f"[multisweep] failed parsing scales from config1: {e}")
        sys.exit(1)

    try:
        ypos1 = _extract_y_from_positions(cfg1)
        ypos2 = _extract_y_from_positions(cfg2)
    except Exception as e:
        print(f"[multisweep] failed parsing Y positions: {e}")
        sys.exit(1)

    return frames1, frames2, [x_res, y_res, e_r], processdir1, processdir2, ypos1, ypos2


def _load_frame_png(path: str) -> np.ndarray:
    """Load a PNG frame and return grayscale float32 in [0,1]."""
    img = imread(path)
    if img.ndim == 3:
        img = rgb2gray(img)
    return img.astype(np.float32)


def dicom_write_volume_multi_sweep(
    frames1: List[str],
    frames2: List[str],
    scales: List[float],
    dst: Path,
    filetype: str,
    diff_y: float,
) -> None:
    """
    Merge two sweeps by overlap blending and save as DICOM slices.
    frames1 -> left part, frames2 -> right part (newest on the right).
    Image size is determined from the first frame (no hardcoded 1024).
    """
    x_res, y_res, e_r = [float(v) for v in scales]
    template = pydicom.dcmread(str(Config.dicom_template_path()))

    # Determine input frame size from the first frame
    if filetype == "raw":
        arr0 = np.flip(np.load(frames1[0]), axis=0).astype(np.float32)
        h, w = arr0.shape
    else:
        arr0 = _load_frame_png(frames1[0])
        h, w = arr0.shape

    # number of added columns (shift in pixels converted from mm)
    num_pix = max(0, int(round(diff_y / max(y_res, 1e-9))))
    out_w = w + num_pix
    overlap = max(0, w - num_pix)  # region that blends left/right
    idx1 = num_pix                 # start of the overlapped region from the left
    idx2 = w                       # start of the right block in the output

    print(f"[multisweep] writing merged dicom volume... (H={h}, W={out_w}, shift={num_pix}px)")
    series_uid = pydicom.uid.generate_uid()
    series_desc = "3SONIC MultiSweep (merged)"

    n = min(len(frames1), len(frames2))
    for idx in tqdm(range(n)):
        # Load both frames
        if filetype == "raw":
            left  = np.flip(np.load(frames1[idx]), axis=0).astype(np.float32)
            right = np.flip(np.load(frames2[idx]), axis=0).astype(np.float32)
        else:
            left  = _load_frame_png(frames1[idx])
            right = _load_frame_png(frames2[idx])

        # Validate sizes
        if left.shape != (h, w):
            h, w = left.shape
            out_w = w + num_pix
            overlap = max(0, w - num_pix)
            idx2 = w
        if right.shape != (h, w):
            # Resize right via simple crop/pad to match (best-effort)
            rr = np.zeros((h, w), dtype=np.float32)
            hh = min(h, right.shape[0])
            ww = min(w, right.shape[1])
            rr[:hh, :ww] = right[:hh, :ww]
            right = rr

        # Allocate destination
        arr = np.zeros((h, out_w), dtype=np.float32)

        # Left part up to the shift
        if idx1 > 0:
            arr[:, :idx1] = left[:, :idx1]

        # Right tail after the original width boundary
        if num_pix > 0:
            arr[:, idx2:] = right[:, w - num_pix:]

        # Smooth overlap blending across 'overlap' columns
        if overlap > 0:
            weights = np.linspace(0.0, 1.0, num=overlap, endpoint=True, dtype=np.float32)
            for i in range(overlap):
                a = left[:, idx1 + i]   # column from left frame
                b = right[:, i]         # corresponding column from right frame
                if float(np.sum(a)) == 0.0:
                    wgt = 1.0
                elif float(np.sum(b)) == 0.0:
                    wgt = 0.0
                else:
                    wgt = float(weights[i])
                arr[:, idx1 + i] = (1.0 - wgt) * a + wgt * b

        # scale bar and 16-bit
        mask = get_mask(arr, (x_res, y_res))
        arr = draw_scale_bar(arr, mask, 1.0)
        arr16 = np.uint16(np.clip(arr * 256.0, 0, 65535))

        # Prepare a per-slice DICOM object
        dcm = template.copy()
        dcm.Rows = int(h)
        dcm.Columns = int(out_w)
        dcm.SamplesPerPixel = 1
        dcm.PhotometricInterpretation = "MONOCHROME2"
        dcm.BitsStored = 16
        dcm.BitsAllocated = 16
        dcm.HighBit = 15
        dcm.PixelRepresentation = 0
        dcm.PixelSpacing = [float(x_res), float(y_res)]
        # Keep if present in template; avoid adding invalid tag types otherwise
        if hasattr(dcm, "ImagerPixelSpacing"):
            dcm.ImagerPixelSpacing = [float(x_res), float(y_res)]
        dcm.SliceThickness = float(e_r)
        dcm.SeriesInstanceUID = series_uid
        dcm.SeriesDescription = series_desc
        dcm.ImagePositionPatient = [0.0, 0.0, idx * float(e_r)]
        dcm.PixelData = arr16.tobytes()

        dcm.save_as(str(dst / f"slice{idx:04d}.dcm"))


def clean_up(processdir1: Path, processdir2: Path) -> None:
    """
    Move frames/raws/config of dir2 into dir1, then remove dir2.
    Same logic as your original (best-effort).
    """
    print("[multisweep] cleanup...")
    try:
        (processdir2 / "raws").rename(processdir1 / "raws2")
        (processdir2 / "frames").rename(processdir1 / "frames2")
        (processdir2 / "config.txt").rename(processdir1 / "config2.txt")
        sh.rmtree(processdir2)

        (processdir1 / "raws").rename(processdir1 / "raws1")
        (processdir1 / "frames").rename(processdir1 / "frames1")
        (processdir1 / "config.txt").rename(processdir1 / "config1.txt")
    except Exception as e:
        print(f"[multisweep] ⚠ cleanup failed: {e}")


def main(argv: list[str]) -> int:
    frames1, frames2, scales, processdir1, processdir2, ypos1, ypos2 = extract_parameters()
    dicom_dst = processdir1 / "dicom_series"
    dicom_dst.mkdir(exist_ok=True)

    # IMPORTANT: remove any previous DICOMs to avoid mixed sizes in the series
    for f in dicom_dst.glob("*.dcm"):
        try:
            f.unlink()
        except Exception:
            pass

    # newer sweep (frames2) on the right-hand side
    diff_y = float(abs(ypos1 - ypos2))
    dicom_write_volume_multi_sweep(frames1, frames2, scales, dicom_dst, "png", diff_y)

    # Popup on the merged (and now clean) dicom_series
    make_popup_figure(processdir1)

    clean_up(processdir1, processdir2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
