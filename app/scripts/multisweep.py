from __future__ import annotations
import os
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

from app.config import Config
from app.core.ultrasound_sdk import get_mask, draw_scale_bar
from app.scripts.postprocessing import make_popup_figure


def extract_parameters() -> tuple[list[str], list[str], list[float], Path, Path, float, float]:
    """
    Reads recdir → find two most recent scan dirs → return their frames,
    config scales, and Y positions.
    """
    try:
        processdir = Path(Config.RECDIR_FILE.read_text().strip())
    except Exception as e:
        print(f"[multisweep] ⚠ failed reading recdir: {e}")
        sys.exit(1)

    parent = processdir.parent
    dirs = sorted([d for d in parent.iterdir() if d.is_dir()])

    if len(dirs) < 2:
        print("[multisweep] not enough scan dirs to merge")
        sys.exit(1)

    processdir1, processdir2 = dirs[-2], dirs[-1]
    print("[multisweep] merging:")
    print(" dir1:", processdir1)
    print(" dir2:", processdir2)

    # get frames
    frames1 = sorted(glob.glob(str(processdir1 / "frames" / "*.png")), key=lambda p: int(Path(p).stem))
    frames2 = sorted(glob.glob(str(processdir2 / "frames" / "*.png")), key=lambda p: int(Path(p).stem))

    # read configs
    config1 = pd.read_csv(processdir1 / "config.txt", header=None)
    config2 = pd.read_csv(processdir2 / "config.txt", header=None)

    y_res = float(config1.iloc[:, 0][13].split(":")[1][:-1])
    x_res = float(config1.iloc[:, 0][12].split(":")[1][:-1])
    e_r = float(config1.iloc[:, 0][2].split(":")[1][:-1])

    ypos1 = float(config1.iloc[9, 0].split()[2][2:])
    ypos2 = float(config2.iloc[9, 0].split()[2][2:])

    return frames1, frames2, [x_res, y_res, e_r], processdir1, processdir2, ypos1, ypos2


def dicom_write_volume_multi_sweep(
    frames1: list[str], frames2: list[str], scales: list[float],
    dst: Path, filetype: str, diff_y: float
) -> None:
    """
    Merge two sweeps by overlap blending and save as DICOM slices.
    """
    x_res, y_res, e_r = scales
    dicom_file = pydicom.dcmread(str(Config.DCM_TEMPLATE))

    num_pix = int(diff_y / y_res)
    overlap = 1024 - num_pix
    idx1 = num_pix
    idx2 = 1024

    print("[multisweep] writing merged dicom volume...")
    for idx in tqdm(range(len(frames1))):
        if filetype == "raw":
            arr1 = np.flip(np.load(frames1[idx]), axis=0)
            arr2 = np.flip(np.load(frames2[idx]), axis=0)
        elif filetype == "png":
            arr1 = rgb2gray(imread(frames1[idx]))
            arr2 = rgb2gray(imread(frames2[idx]))
        else:
            continue

        arr = np.zeros((1024, 1024 + num_pix))
        arr[:, :idx1] = arr1[:, :idx1]
        arr[:, idx2:] = arr2[:, 1024 - num_pix:]

        # smooth overlap blending
        weights = np.linspace(0, 1, num=overlap, endpoint=True)
        for i in range(overlap):
            if np.sum(arr1[:, idx1 + i]) == 0.0:
                weights[i] = 1
            elif np.sum(arr2[:, i]) == 0.0:
                weights[i] = 0
            arr[:, idx1 + i] = (1 - weights[i]) * arr1[:, idx1 + i] + weights[i] * arr2[:, i]

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

    dicom_write_volume_multi_sweep(frames2, frames1, scales, dicom_dst, "png", np.abs(ypos1 - ypos2))

    make_popup_figure(processdir1)
    clean_up(processdir1, processdir2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
