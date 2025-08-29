# app/scripts/multisweep.py
from __future__ import annotations

import sys
import glob
from pathlib import Path
import numpy as np
import pandas as pd
import pydicom
import shutil as sh
from tqdm import tqdm
from skimage.io import imread
from skimage.color import rgb2gray

# ------------------------------------------------------------------------------
# Import handling: support both
#   1) python -m app.scripts.multisweep
#   2) python app/scripts/multisweep.py
# ------------------------------------------------------------------------------
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


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def _is_valid_scan_dir(p: Path) -> bool:
    """A scan dir must have config.txt and frames/ with at least one PNG."""
    try:
        if not p.is_dir():
            return False
        if not (p / "config.txt").exists():
            return False
        frames_dir = p / "frames"
        if not frames_dir.is_dir():
            return False
        if not any(frames_dir.glob("*.png")):
            return False
        return True
    except Exception:
        return False


def _list_recent_scan_dirs(root: Path, limit: int = 10) -> list[Path]:
    """Return up to `limit` most recent valid scan folders (newest first)."""
    scans = [d for d in root.iterdir() if _is_valid_scan_dir(d)]

    def _sort_key(p: Path):
        # Prefer timestamp-like folder names; fall back to mtime
        return (p.name, p.stat().st_mtime)

    scans.sort(key=_sort_key, reverse=True)
    return scans[:limit]


def _frames_sorted_by_index(frames_dir: Path) -> list[str]:
    """Return frames/*.png sorted by numeric stem; fall back to name."""
    paths = list(frames_dir.glob("*.png"))
    if not paths:
        return []
    try:
        paths.sort(key=lambda p: int(p.stem))
    except Exception:
        paths.sort(key=lambda p: p.name)
    return [str(p) for p in paths]


# ------------------------------------------------------------------------------
# Core flow
# ------------------------------------------------------------------------------

def extract_parameters() -> tuple[list[str], list[str], list[float], Path, Path, float, float]:
    """
    Read recdir → take parent → pick the two most recent VALID scan dirs
    (ignoring 'logs' or any non-scan folders) → return their frames, scales, Y positions.
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

    # newest two (dir2 = newest, dir1 = previous)
    processdir2, processdir1 = candidates[0], candidates[1]
    print("[multisweep] merging:")
    print(" dir1:", processdir1)
    print(" dir2:", processdir2)

    frames1 = _frames_sorted_by_index(processdir1 / "frames")
    frames2 = _frames_sorted_by_index(processdir2 / "frames")
    if not frames1 or not frames2:
        print("[multisweep] one of the scan folders has no frames")
        sys.exit(1)

    # read configs
    try:
        config1 = pd.read_csv(processdir1 / "config.txt", header=None)
        config2 = pd.read_csv(processdir2 / "config.txt", header=None)
    except Exception as e:
        print(f"[multisweep] failed reading config.txt: {e}")
        sys.exit(1)

    # scales (match your original indices)
    try:
        y_res = float(config1.iloc[:, 0][13].split(":")[1][:-1])
        x_res = float(config1.iloc[:, 0][12].split(":")[1][:-1])
        e_r   = float(config1.iloc[:, 0][2].split(":")[1][:-1])
    except Exception as e:
        print(f"[multisweep] failed parsing scales from config1: {e}")
        sys.exit(1)

    # Y positions (used for overlap)
    try:
        ypos1 = float(config1.iloc[9, 0].split()[2][2:])
        ypos2 = float(config2.iloc[9, 0].split()[2][2:])
    except Exception as e:
        print(f"[multisweep] failed parsing Y positions: {e}")
        sys.exit(1)

    return frames1, frames2, [x_res, y_res, e_r], processdir1, processdir2, ypos1, ypos2


def dicom_write_volume_multi_sweep(
    frames1: list[str], frames2: list[str], scales: list[float],
    dst: Path, filetype: str, diff_y: float
) -> None:
    """
    Merge two sweeps by overlap blending and save as DICOM slices.
    frames1 -> left part, frames2 -> right part (newest on the right).
    """
    x_res, y_res, e_r = scales
    dicom_file = pydicom.dcmread(str(Config.dicom_template_path()))

    num_pix = max(0, int(diff_y / max(y_res, 1e-9)))
    overlap = max(0, 1024 - num_pix)
    idx1 = num_pix
    idx2 = 1024

    print("[multisweep] writing merged dicom volume...")
    n = min(len(frames1), len(frames2))
    for idx in tqdm(range(n)):
        if filetype == "raw":
            arr1 = np.flip(np.load(frames1[idx]), axis=0)
            arr2 = np.flip(np.load(frames2[idx]), axis=0)
        elif filetype == "png":
            arr1 = rgb2gray(imread(frames1[idx]))
            arr2 = rgb2gray(imread(frames2[idx]))
        else:
            continue

        # allocate destination (wider by num_pix)
        arr = np.zeros((1024, 1024 + num_pix), dtype=float)
        arr[:, :idx1] = arr1[:, :idx1]
        arr[:, idx2:] = arr2[:, 1024 - num_pix:]

        # smooth overlap blending
        if overlap > 0:
            weights = np.linspace(0, 1, num=overlap, endpoint=True)
            for i in range(overlap):
                # handle zero columns gracefully
                if np.sum(arr1[:, idx1 + i]) == 0.0:
                    w = 1.0
                elif np.sum(arr2[:, i]) == 0.0:
                    w = 0.0
                else:
                    w = weights[i]
                arr[:, idx1 + i] = (1 - w) * arr1[:, idx1 + i] + w * arr2[:, i]

        # add scalebar
        mask = get_mask(arr, (x_res, y_res))
        arr = draw_scale_bar(arr, mask, 1.0)
        arr = np.uint16(256 * arr)

        dicom_file.Rows, dicom_file.Columns = arr.shape
        dicom_file.PixelData = arr.tobytes()
        dicom_file.ImagePositionPatient = [0, 0, idx * e_r]

        try:
            dicom_file.save_as(str(dst / f"slice{idx}.dcm"))
        except OSError as e:
            print(f"[multisweep] error saving slice {idx}: {e}")


def clean_up(processdir1: Path, processdir2: Path) -> None:
    """
    Move frames/raws/config of dir2 into dir1, then remove dir2.
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

    # Place the newer sweep (frames2) on the right-hand side.
    diff_y = float(abs(ypos1 - ypos2))
    dicom_write_volume_multi_sweep(frames1, frames2, scales, dicom_dst, "png", diff_y)

    make_popup_figure(processdir1)
    clean_up(processdir1, processdir2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
