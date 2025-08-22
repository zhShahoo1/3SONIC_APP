# app/scripts/imconv.py
from __future__ import annotations

# ── allow running as:
#    python -m app.scripts.imconv
#    python app/scripts/imconv.py
import sys
from pathlib import Path

if __package__ in (None, ""):
    THIS_FILE = Path(__file__).resolve()
    PROJECT_ROOT = THIS_FILE.parents[2]  # .../<project-root>
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
# ─────────────────────────────────────────────────────────────

import os
import glob
import numpy as np
from PIL import Image
import pydicom
import pandas as pd
from multiprocessing import Pool

# updated app-relative imports (paths only; behavior preserved)
from app.config import Config
from app.scripts.dicomwritevolume import dicom_write_slice
from app.scripts.postprocessing import make_popup_figure
from app.core.ultrasound_sdk import get_mask, draw_scale_bar


def tiff_force_8bit(image, **kwargs):
    """
    Keep original behavior: if TIFF I;16, normalize to 8-bit for preview.
    (Even though here we feed it a PIL Image made from uint32 → it’s fine.)
    """
    if image.format == 'TIFF' and image.mode == 'I;16':
        array = np.array(image)
        normalized = (array.astype(np.uint16) - array.min()) * 255.0 / (array.max() - array.min())
        image = Image.fromarray(normalized.astype(np.uint8))
    return image


# Modified the function to accept a tuple (file, processdir)
def process_file(args):
    # Unpack arguments  (unchanged signature)
    file, processdir = args
    processdir = str(processdir)  # ensure str for os.path ops

    # read from configuration file (same indexing as original)
    configurations = pd.read_csv(os.path.join(processdir, "config.txt"), header=None)
    y_res = float(configurations.iloc[:, 0][13].split(":")[1][:-1])
    x_res = float(configurations.iloc[:, 0][12].split(":")[1][:-1])
    e_r = float(configurations.iloc[:, 0][2].split(":")[1][:-1])

    # prepare dicom header (use template path from Config)
    dicom_template_path = str(Config.dicom_template_path())
    dicom_file = pydicom.dcmread(dicom_template_path)
    dicom_file.PhotometricInterpretation = "MONOCHROME2"
    dicom_file.SamplesPerPixel = 1
    dicom_file.BitsStored = 16
    dicom_file.BitsAllocated = 16
    dicom_file.HighBit = 15
    dicom_file.PixelRepresentation = 0
    dicom_file.WindowCenter = 0
    dicom_file.WindowWidth = 1000
    dicom_file.SOPClassUID = '1.2.840.10008.5.1.4.1.1.2'
    dicom_file.RescaleIntercept = -1024
    dicom_file.RescaleSlope = 1
    dicom_file.RescaleType = 'HU'
    dicom_file.SliceThickness = e_r
    dicom_file.ImagerPixelSpacing = [x_res, y_res]
    dicom_file.PixelSpacing = [x_res, y_res]

    # check for multisweep (read from new flag location)
    try:
        with Config.MULTISWEEP_FLAG_FILE.open("r", encoding="utf-8") as ms:
            multisweep = bool(int(ms.read().strip()))
    except Exception:
        multisweep = False

    # get index of file (unchanged)
    idx = int(file.split(".")[0])
    try:
        tfile = np.load(os.path.join(processdir, file))  # uint32 2D
        im = np.flipud(tfile)
        im = Image.fromarray(im)
        im2 = tiff_force_8bit(im)

        # move raw to raws/
        try:
            os.replace(
                os.path.join(processdir, file),
                os.path.join(processdir, "raws", file),
            )
        except Exception:
            # fallback if replace not supported
            try:
                os.rename(os.path.join(processdir, file), os.path.join(processdir, "raws", file))
            except Exception:
                pass

        # save PNG preview to frames/
        im2.convert('P').save(os.path.join(processdir, "frames", file.split(".")[0] + ".png"))

        # don't save dicom series if multisweep
        if (not multisweep) & (idx > 540) & (idx <= 1540):
            # add scalebar (same call pattern as original)
            mask = get_mask(tfile, (x_res, y_res))
            im_scalebar = draw_scale_bar(tfile, mask, 255)
            dicom_write_slice(
                im_scalebar,
                dicom_file,
                file.split(".")[0],
                os.path.join(processdir, "dicom_series"),
                e_r,
            )

    except PermissionError:
        # identical silent skip on PermissionError
        pass


def cleanup(processdir):
    raw_files = glob.glob(os.path.join(processdir, "*.npy"))
    for file in raw_files:
        try:
            os.remove(file)
        except Exception:
            pass


if __name__ == "__main__":
    processdir = ""
    # read recdir path from new location
    try:
        processdir = Config.RECDIR_FILE.read_text().splitlines()[0].strip()
    except Exception:
        # fallback to old behavior if file not found (rare)
        try:
            with open("recdir", "r") as f:
                processdir = f.readlines()[0].split("\n")[0]
        except Exception:
            print("[imconv] ⚠ could not locate recdir file")
            sys.exit(1)

    print(processdir)

    # create subdirs if missing (same names)
    if not os.path.exists(os.path.join(processdir, "processed")):
        try:
            os.makedirs(os.path.join(processdir, "frames"), exist_ok=True)
            os.makedirs(os.path.join(processdir, "raws"), exist_ok=True)
            os.makedirs(os.path.join(processdir, "dicom_series"), exist_ok=True)
        except FileExistsError:
            pass

    all_files = [f for f in os.listdir(processdir) if f.endswith(".npy")]
    all_files.sort()

    # Package each file name together with the processdir into a tuple
    args_for_map = [(file, processdir) for file in all_files]
    if args_for_map:
        with Pool() as p:
            p.map(process_file, args_for_map)

    # remove extra files
    cleanup(processdir)

    # read multisweep flag again (same logic as original)
    try:
        with Config.MULTISWEEP_FLAG_FILE.open("r", encoding="utf-8") as ms:
            multisweep = bool(int(ms.read().strip()))
    except Exception:
        multisweep = False

    # make the pop-up figure, add scalebar to volume and create rendering figure
    if not multisweep:
        try:
            make_popup_figure(processdir)
        except Exception as e:
            print(f"[imconv] ⚠ make_popup_figure failed: {e}")

    # indicate we are no longer scanning
    try:
        Config.SCANNING_FLAG_FILE.write_text("0")
    except Exception:
        # fallback to old path if needed
        try:
            with open("scanning", "w") as fs:
                fs.write("0")
        except Exception:
            pass
