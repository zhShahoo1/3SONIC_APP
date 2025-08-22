from __future__ import annotations
import os
import time
from pathlib import Path
import numpy as np
import pandas as pd
from copy import deepcopy
from matplotlib import pyplot as plt, rcParams
from matplotlib.widgets import Slider
import SimpleITK as sitk

from app.core.ultrasound_sdk import get_mask


def make_popup_figure(processdir: Path) -> None:
    """
    Postprocess a finished scan:
      - Read dicom_series into SimpleITK
      - Save Example_slices.png
      - Add scalebars to volume and export nifti_volume.nii.gz
      - Append total scan time to config.txt
      - Launch interactive matplotlib viewer
    """
    processdir = Path(processdir)

    ########################################################
    #   read dicom series
    ########################################################
    reader = sitk.ImageSeriesReader()
    dicom_names = reader.GetGDCMSeriesFileNames(str(processdir / "dicom_series"))
    reader.SetFileNames(dicom_names)
    image = reader.Execute()

    spacing = image.GetSpacing()[::-1]  # SITK spacing order is z,y,x
    aspect_cor = spacing[2] / spacing[1]
    aspect_sag = spacing[0] / spacing[1]
    aspect_axi = spacing[0] / spacing[2]

    image = sitk.GetArrayFromImage(image)
    image_orig = deepcopy(image)

    ########################################################
    #   save static preview (3x3 grid of slices)
    ########################################################
    coronal_indices = (image.shape[0] * np.array([0.4, 0.55, 0.7])).astype(int)
    sagittal_indices = (image.shape[2] * np.array([0.2, 0.5, 0.8])).astype(int)
    axial_indices = (image.shape[1] * np.array([0.5, 0.57, 0.65])).astype(int)

    fig, ax = plt.subplots(3, 3, figsize=(18, 10))
    ax[0, 0].imshow(image[coronal_indices[0]], aspect=aspect_cor, cmap="gray")
    ax[0, 1].imshow(image[coronal_indices[1]], aspect=aspect_cor, cmap="gray")
    ax[0, 2].imshow(image[coronal_indices[2]], aspect=aspect_cor, cmap="gray")
    ax[1, 0].imshow(image[:, :, sagittal_indices[0]], aspect=aspect_sag, cmap="gray")
    ax[1, 1].imshow(image[:, :, sagittal_indices[1]], aspect=aspect_sag, cmap="gray")
    ax[1, 2].imshow(image[:, :, sagittal_indices[2]], aspect=aspect_sag, cmap="gray")
    ax[2, 0].imshow(image[:, axial_indices[0]], aspect=aspect_axi, cmap="gray")
    ax[2, 1].imshow(image[:, axial_indices[1]], aspect=aspect_axi, cmap="gray")
    ax[2, 2].imshow(image[:, axial_indices[2]], aspect=aspect_axi, cmap="gray")

    [a.axis("off") for a in ax.flatten()]
    plt.suptitle("Slices from Specimen", fontsize=18)
    plt.savefig(processdir / "Example_slices.png")
    plt.close()

    ########################################################
    #   add scalebars + export NIfTI
    ########################################################
    max_val = np.max(image)

    # axial
    mask_axial = get_mask(image[:, 0], [spacing[0], spacing[2]])
    image = np.transpose(image, (1, 0, 2))
    image[:, mask_axial] = max_val
    image = np.transpose(image, (1, 0, 2))

    # sagittal
    mask_sagittal = get_mask(image[:, :, 0], [spacing[0], spacing[1]])
    image[mask_sagittal] = max_val

    sitk_image = sitk.GetImageFromArray(image)
    sitk_image.SetSpacing(spacing[::-1])
    sitk.WriteImage(sitk_image, str(processdir / "nifti_volume.nii.gz"))

    ########################################################
    #   append scan duration to config
    ########################################################
    cfg_path = processdir / "config.txt"
    try:
        configs = pd.read_csv(cfg_path, header=None)
        start_time = float(configs.iloc[:, 0][11].split(":")[1][:-1])
        total_time = time.time() - start_time
        with open(cfg_path, "a") as f:
            f.write(f"Total Time [s]:{total_time};\n")
    except Exception as e:
        print(f"[postprocessing] ⚠ could not append total time: {e}")

    ########################################################
    #   interactive viewer
    ########################################################
    rcParams["image.interpolation"] = "nearest"
    rcParams["image.cmap"] = "gray"
    rcParams["axes.titlesize"] = 10

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 5))
    plt.subplots_adjust(bottom=0.2)

    slice_idx = image_orig.shape[0] // 2
    img1 = ax1.imshow(image_orig[slice_idx, :, :], cmap="gray")
    ax1.set_title(f"Coronal Slice {slice_idx + 1}/{image_orig.shape[0]}")
    ax1.axis("off")

    axial_view = image_orig.transpose(2, 0, 1)
    img2 = ax2.imshow(axial_view[:, :, axial_view.shape[2] // 2], aspect=1 / aspect_axi, cmap="gray")
    marker = ax2.axvline(slice_idx, color="red", lw=2)
    ax2.set_title("Axial View")
    ax2.axis("off")

    ax_slider = plt.axes([0.25, 0.1, 0.5, 0.03], facecolor="lightgoldenrodyellow")
    slider = Slider(ax_slider, "Slice", 0, image_orig.shape[0] - 1, valinit=slice_idx, valstep=1)

    def update(val):
        nonlocal slice_idx
        slice_idx = int(slider.val)
        img1.set_data(image_orig[slice_idx, :, :])
        marker.set_xdata([slice_idx])
        ax1.set_title(f"Coronal Slice {slice_idx + 1}/{image_orig.shape[0]}")
        fig.canvas.draw_idle()

    slider.on_changed(update)
    plt.show()
