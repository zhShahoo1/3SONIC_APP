from __future__ import annotations
import os
import glob
import subprocess
import platform
from pathlib import Path
import tkinter as tk
from tkinter import simpledialog

from app.config import Config


THEME_BG = "#0a0e14"
THEME_CARD = "#121212"
THEME_TEXT = "#e5e9f0"
THEME_TEXT_DIM = "#c0cad3"
THEME_ACCENT = "#008d99"
THEME_ACCENT_ACTIVE = "#00aeb7"
THEME_BORDER = "#0f161f"


class _DicomChooser(simpledialog.Dialog):
    """Custom themed dialog for picking a scan folder."""

    def __init__(self, parent: tk.Tk, dirs: list[Path]):
        self._dirs = dirs
        super().__init__(parent, title="Select DICOM Series")

    def body(self, master: tk.Misc) -> tk.Widget:
        master.configure(bg=THEME_BG)
        self.configure(bg=THEME_BG)
        self.resizable(False, False)

        try:
            self.wm_attributes("-topmost", True)
        except Exception:
            pass

        icon_path = Config.STATIC_DIR / "images" / "favicon.ico"
        if platform.system() == "Windows" and icon_path.exists():
            try:
                self.wm_iconbitmap(str(icon_path))
            except Exception:
                pass

        master.grid_columnconfigure(0, weight=1)
        master.grid_rowconfigure(2, weight=1)

        heading = tk.Label(
            master,
            text="Select a scan to open",
            font=("Segoe UI", 12, "bold"),
            fg=THEME_TEXT,
            bg=THEME_BG,
        )
        heading.grid(row=0, column=0, sticky="w", padx=18, pady=(18, 4))

        helper = tk.Label(
            master,
            text="Double-click a folder to open it in ITK-SNAP.",
            font=("Segoe UI", 9),
            fg=THEME_TEXT_DIM,
            bg=THEME_BG,
            wraplength=320,
            justify="left",
        )
        helper.grid(row=1, column=0, sticky="w", padx=18, pady=(0, 10))

        list_frame = tk.Frame(master, bg=THEME_BG, bd=0)
        list_frame.grid(row=2, column=0, sticky="nsew", padx=18)
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(0, weight=1)

        canvas = tk.Canvas(
            list_frame,
            bg=THEME_BG,
            highlightthickness=0,
            bd=0,
            relief="flat",
        )
        canvas.grid(row=0, column=0, sticky="nsew")

        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        self._items_container = tk.Frame(canvas, bg=THEME_BG)
        canvas.create_window((0, 0), window=self._items_container, anchor="nw")

        list_values = [f"{idx + 1}: {d.name}" for idx, d in enumerate(self._dirs)]
        self._item_vars: list[tk.StringVar] = []
        for idx, label in enumerate(list_values):
            var = tk.StringVar(value=label)
            self._item_vars.append(var)
            btn = tk.Label(
                self._items_container,
                textvariable=var,
                bg=THEME_CARD,
                fg=THEME_TEXT,
                font=("Segoe UI", 10),
                pady=10,
                padx=16,
                anchor="w",
                cursor="hand2",
                bd=0,
                highlightthickness=1,
                highlightbackground=THEME_BORDER,
                highlightcolor=THEME_BORDER,
            )
            btn.grid(row=idx, column=0, sticky="ew", pady=(0, 8))
            btn.bind("<Enter>", lambda e, b=btn: b.configure(bg="#162433"))
            btn.bind("<Leave>", lambda e, b=btn: b.configure(bg=THEME_CARD))
            btn.bind("<Button-1>", lambda e, i=idx: self._on_click(i))
            btn.bind("<Double-Button-1>", lambda e, i=idx: self._on_double_click(i))
            btn.grid_columnconfigure(0, weight=1)

        self._items_container.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
        self._items_container.bind_all("<MouseWheel>", self._on_mousewheel)

        footer = tk.Label(
            master,
            text=f"Found {len(self._dirs)} scan folder(s)",
            font=("Segoe UI", 9),
            fg=THEME_TEXT_DIM,
            bg=THEME_BG,
        )
        footer.grid(row=3, column=0, sticky="w", padx=18, pady=(10, 0))

        self.listbox = None
        self._scroll_canvas = canvas
        self._selected_index = None
        self.bind("<MouseWheel>", self._on_mousewheel)

        self._highlight_selection(0 if self._dirs else None)
        return self._items_container

    def buttonbox(self) -> None:
        self.bind("<Escape>", lambda _e: self.cancel())

    def _highlight_selection(self, index: int | None) -> None:
        self._selected_index = index
        for idx, child in enumerate(self._items_container.winfo_children()):
            if idx == index:
                child.configure(bg="#1f2e41", highlightbackground=THEME_ACCENT, highlightcolor=THEME_ACCENT)
            else:
                child.configure(bg=THEME_CARD, highlightbackground=THEME_BORDER, highlightcolor=THEME_BORDER)

    def _on_click(self, index: int) -> None:
        self._highlight_selection(index)

    def _on_double_click(self, index: int) -> None:
        self._highlight_selection(index)
        self.ok()

    def _on_mousewheel(self, event: tk.Event) -> None:
        try:
            delta = -1 * (event.delta // 120)
        except Exception:
            delta = 0
        if delta:
            self._scroll_canvas.yview_scroll(delta, "units")

    def apply(self) -> None:
        self.result = self._selected_index


def _show_dicom_dialog(sorted_dirs: list[Path]) -> Path | None:
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        dialog = _DicomChooser(root, sorted_dirs)
        if isinstance(dialog.result, int):
            return sorted_dirs[dialog.result]
        return None
    finally:
        root.destroy()


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

    selected_dir = _show_dicom_dialog(sorted_dirs)
    if selected_dir is None:
        return False, "Selection cancelled."

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
