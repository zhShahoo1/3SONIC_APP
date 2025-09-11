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
import re
import numpy as np
from PIL import Image
import pydicom
from multiprocessing import Pool

# app-relative imports
from app.config import Config
from app.scripts.dicomwritevolume import dicom_write_slice
from app.scripts.postprocessing import make_popup_figure
from app.core.ultrasound_sdk import get_mask, draw_scale_bar


# --------------------------- Config parsing (robust) -------------------------

def _to_float(s: str, *, default: float | None = None) -> float:
    """
    Extract first floating number from a string like '1.23 mm' or 'default'.
    Returns default if provided and no number found; otherwise raises.
    """
    if s is None:
        if default is not None:
            return default
        raise ValueError("None cannot be converted to float")
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(s))
    if m:
        return float(m.group(0))
    if default is not None:
        return default
    raise ValueError(f"not a number: {s!r}")


def read_config_map(cfg_path: str) -> dict[str, str]:
    """
    Read 'config.txt' into a dict of lowercase keys -> stripped values.
    Accepts lines in the form 'Key:Value;' and ignores others.
    """
    kv: dict[str, str] = {}
    try:
        with open(cfg_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                k, v = line.split(":", 1)
                v = v.strip()
                if v.endswith(";"):
                    v = v[:-1]
                kv[k.strip().lower()] = v
    except FileNotFoundError:
        pass
    return kv


def cfg_get_float(cfg: dict[str, str], keys: list[str], default: float) -> float:
    """
    Try multiple key aliases; if none exist or not numeric, return default.
    """
    for k in keys:
        if k in cfg:
            try:
                return _to_float(cfg[k], default=default)
            except Exception:
                # fall through to next alias/default
                pass
    return default


# --------------------------- Image helpers ----------------------------------

def tiff_force_8bit(image: Image.Image) -> Image.Image:
    """
    If a 16-bit TIFF sneaks in, normalize to 8-bit for preview.
    For array-originated images (mode='I'/'I;16'), produce an 8-bit PNG-friendly image.
    """
    if image.mode in ("I;16", "I"):
        arr = np.array(image)
        arr = arr.astype(np.float32)
        vmin, vmax = float(np.min(arr)), float(np.max(arr))
        if vmax <= vmin:
            return Image.fromarray(np.zeros_like(arr, dtype=np.uint8))
        norm = (arr - vmin) * (255.0 / (vmax - vmin))
        return Image.fromarray(norm.astype(np.uint8))
    return image


# --------------------------- Per-slice processing ---------------------------

def process_file(args):
    """
    Convert one .npy frame into:
      - frames/<idx>.png (8-bit preview)
      - raws/<idx>.npy  (moved original)
      - dicom_series/<idx>.dcm (DICOM; now for ALL frames, single or multi)
    """
    file, processdir = args
    processdir = str(processdir)

    # Read configuration robustly
    cfg = read_config_map(os.path.join(processdir, "config.txt"))

    # Pixel spacing & slice thickness
    # Fall back to sensible defaults if missing
    x_res = cfg_get_float(cfg, ["xres", "x_res", "x res", "x resolution"], default=0.10)  # mm/px
    y_res = cfg_get_float(cfg, ["yres", "y_res", "y res", "y resolution"], default=0.10)  # mm/px
    e_r   = cfg_get_float(
        cfg,
        ["e_r setpoint", "elev_resolution", "elev resolution", "elevation_resolution", "slice_thickness"],
        default=Config.ELEV_RESOLUTION_MM,
    )

    # Prepare base DICOM dataset (template + our overrides)
    dicom_template_path = str(Config.dicom_template_path())
    dicom_file = pydicom.dcmread(dicom_template_path)

    # Core photometric/bit depth (use Config where available)
    dicom_file.PhotometricInterpretation = getattr(Config, "PHOTOMETRIC", "MONOCHROME2")
    dicom_file.SamplesPerPixel = 1
    dicom_file.BitsAllocated = getattr(Config, "BITS_ALLOCATED", 16)
    dicom_file.BitsStored = getattr(Config, "BITS_STORED", 16)
    dicom_file.HighBit = getattr(Config, "HIGH_BIT", 15)
    dicom_file.PixelRepresentation = 0  # unsigned

    dicom_file.WindowCenter = getattr(Config, "WINDOW_CENTER", 0)
    dicom_file.WindowWidth = getattr(Config, "WINDOW_WIDTH", 1000)

    dicom_file.RescaleIntercept = getattr(Config, "RESCALE_INTERCEPT", -1024)
    dicom_file.RescaleSlope = getattr(Config, "RESCALE_SLOPE", 1)
    if hasattr(Config, "RESCALE_TYPE"):
        dicom_file[0x00281054] = pydicom.DataElement(0x00281054, "LO", getattr(Config, "RESCALE_TYPE"))  # RescaleType

    # Patient / Study tags (optional, from Config)
    if hasattr(Config, "PATIENT_ID"):
        dicom_file.PatientID = Config.PATIENT_ID
    if hasattr(Config, "STUDY_DESC"):
        dicom_file.StudyDescription = Config.STUDY_DESC

    # Geometry
    dicom_file.SliceThickness = float(e_r)
    try:
        dicom_file.SpacingBetweenSlices = float(e_r)
    except Exception:
        pass
    # Many toolchains use [row_spacing, col_spacing]; we retain historical order for continuity
    dicom_file.ImagerPixelSpacing = [float(x_res), float(y_res)]
    dicom_file.PixelSpacing = [float(x_res), float(y_res)]

    # Check multisweep flag (no longer affects DICOM creation; kept for popup behavior later)
    try:
        with Config.MULTISWEEP_FLAG_FILE.open("r", encoding="utf-8") as ms:
            multisweep = bool(int(ms.read().strip()))
    except Exception:
        multisweep = False

    # Process image
    idx = int(os.path.splitext(os.path.basename(file))[0])

    try:
        tfile = np.load(os.path.join(processdir, file))  # uint32 2D
        # Flip to match historical orientation
        arr = np.flipud(tfile)

        # 8-bit preview
        preview_img = tiff_force_8bit(Image.fromarray(arr))
        preview_path = os.path.join(processdir, "frames", f"{idx}.png")
        # palette conversion (historical)
        preview_img.convert("P").save(preview_path)

        # Move raw to raws/
        try:
            os.replace(
                os.path.join(processdir, file),
                os.path.join(processdir, "raws", file),
            )
        except Exception:
            try:
                os.rename(os.path.join(processdir, file), os.path.join(processdir, "raws", file))
            except Exception:
                pass

        # DICOM for ALL frames (single & multisweep)
        mask = get_mask(tfile, (x_res, y_res))
        im_scalebar = draw_scale_bar(tfile, mask, 255)  # preserve existing visual overlay
        dicom_write_slice(
            im_scalebar,
            dicom_file,
            str(idx),  # Slice ID
            os.path.join(processdir, "dicom_series"),
            e_r,
        )

    except PermissionError:
        # Silent skip (historical behavior)
        pass
    except Exception as e:
        # Keep going on other slices
        print(f"[imconv] ⚠ process_file({idx}) failed: {e}")


# ----------------------------- Cleanup --------------------------------------

def cleanup(processdir: str):
    # Remove any leftover .npy in the root (most were moved to raws/)
    raw_files = glob.glob(os.path.join(processdir, "*.npy"))
    for file in raw_files:
        try:
            os.remove(file)
        except Exception:
            pass


# ------------------------------ Main ----------------------------------------

if __name__ == "__main__":
    processdir = ""
    # Read recdir path from the new location (Config.RECDIR_FILE)
    try:
        processdir = Config.RECDIR_FILE.read_text().splitlines()[0].strip()
    except Exception:
        # fallback to legacy file in cwd
        try:
            with open("recdir", "r", encoding="utf-8", errors="ignore") as f:
                processdir = f.readlines()[0].split("\n")[0]
        except Exception:
            print("[imconv] ⚠ could not locate recdir file")
            sys.exit(1)

    print(processdir)

    # Ensure subdirs exist
    try:
        os.makedirs(os.path.join(processdir, "frames"), exist_ok=True)
        os.makedirs(os.path.join(processdir, "raws"), exist_ok=True)
        os.makedirs(os.path.join(processdir, "dicom_series"), exist_ok=True)
    except FileExistsError:
        pass

    # Collect .npy frames (only those still in root; they will be moved to raws/)
    all_files = [f for f in os.listdir(processdir) if f.endswith(".npy")]
    all_files.sort(key=lambda s: int(os.path.splitext(s)[0]) if os.path.splitext(s)[0].isdigit() else s)

    # Map: (file, processdir)
    args_for_map = [(file, processdir) for file in all_files]
    if args_for_map:
        with Pool() as p:
            p.map(process_file, args_for_map)

    # Clean any leftovers
    cleanup(processdir)

    # Popup only for single sweep (unchanged policy)
    try:
        with Config.MULTISWEEP_FLAG_FILE.open("r", encoding="utf-8") as ms:
            multisweep = bool(int(ms.read().strip()))
    except Exception:
        multisweep = False

    if not multisweep:
        try:
            make_popup_figure(processdir)
        except Exception as e:
            print(f"[imconv] ⚠ make_popup_figure failed: {e}")

    # Indicate we are no longer scanning
    try:
        Config.SCANNING_FLAG_FILE.write_text("0")
    except Exception:
        try:
            with open("scanning", "w") as fs:
                fs.write("0")
        except Exception:
            pass
