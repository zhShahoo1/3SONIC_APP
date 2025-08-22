from __future__ import annotations
import os
import glob
import subprocess
import platform
from pathlib import Path
import tkinter as tk
from tkinter import simpledialog

from app.config import Config


def find_itksnap_executable() -> str | None:
    """
    Try to locate ITK-SNAP executable across platforms.
    Returns full path if found, else None.
    """
    os_type = platform.system()
    executable = "ITK-SNAP.exe" if os_type == "Windows" else "itksnap"

    if os_type == "Windows":
        base_paths = [
            r"C:\Program Files",
            r"C:\Program Files (x86)",
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        ]
    elif os_type == "Linux":
        base_paths = ["/usr/local/bin", "/usr/bin", os.path.expanduser("~/.local/bin")]
    elif os_type == "Darwin":
        base_paths = ["/Applications", os.path.expanduser("~/Applications")]
    else:
        return None

    for base_path in base_paths:
        matches = glob.glob(os.path.join(base_path, "**", executable), recursive=True)
        if matches:
            return matches[0]
    return None


def open_itksnap_with_dicom_series() -> tuple[bool, str]:
    """
    Prompt user to pick a DICOM series and open it in ITK-SNAP.
    Returns (success, message).
    """
    processdir = Config.DATA_DIR
    if not processdir.exists():
        return False, "Data directory does not exist."

    try:
        dirs = [d for d in processdir.iterdir() if d.is_dir()]
        if not dirs:
            return False, "No DICOM series found."
        sorted_dirs = sorted(dirs, key=lambda x: x.name, reverse=True)
    except Exception as e:
        return False, f"Error finding directories: {e}"

    selected_dir = None
    attempts = 0
    while not selected_dir and attempts < 3:
        root = tk.Tk()
        root.attributes("-topmost", True)
        root.withdraw()

        options = "\n".join(f"{i+1}: {d.name}" for i, d in enumerate(sorted_dirs))
        choice = simpledialog.askstring("Select DICOM Series", f"Enter your choice:\n{options}", parent=root)
        root.destroy()

        if choice and choice.isdigit() and 1 <= int(choice) <= len(sorted_dirs):
            selected_dir = sorted_dirs[int(choice) - 1]
        else:
            attempts += 1
            print("[itk_snap] invalid or no selection")

    if not selected_dir:
        return False, "No valid selection made after multiple attempts."

    dicom_file = selected_dir / "dicom_series" / "slice540.dcm"
    if not dicom_file.exists():
        return False, f"DICOM file not found: {dicom_file}"

    exe_path = find_itksnap_executable()
    if not exe_path:
        return False, "ITK-SNAP executable not found."

    try:
        subprocess.run([exe_path, "-g", str(dicom_file)], check=True, capture_output=True, text=True)
        return True, f"ITK-SNAP opened successfully: {dicom_file}"
    except subprocess.CalledProcessError as e:
        return False, f"ITK-SNAP failed: {e.stderr}"


if __name__ == "__main__":
    success, message = open_itksnap_with_dicom_series()
    print("Success:", success, "| Message:", message)
