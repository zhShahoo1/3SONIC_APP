Perfect 👌 — here’s an upgraded **README.md (Markdown)** with **image placeholders** for diagrams/screenshots.
I’ll add sections where you can later drop UI screenshots and workflow diagrams.

```markdown
# 3SONIC Scanner Application  

🚀 **3SONIC Scanner** is a Python-based control and imaging application for an ultrasound scanning system with integrated motion control.  
It provides a **web-based interface (Flask)** to:  

- Control the scanner axes (X, Y, Z, rotation) via GUI or **keyboard shortcuts**  
- Stream **live ultrasound video** and **external camera feed**  
- Manage **scanning workflows** (single scan, multi-path scans)  
- Export results as **DICOM series** and open them in **ITK-SNAP** for visualization  
- Perform hardware tasks such as initialization, homing, jogging, and specimen placement  

---

## ✨ Features  

- 🔧 **Motion Control**  
  - Axis jogging (X/Y/Z) via GUI buttons or **arrow keys** (continuous jog)  
  - Nozzle rotation control (clockwise/counterclockwise)  
  - Safety features: homing, bounds checking, cold extrusion protection  

- 🎥 **Imaging**  
  - Live ultrasound feed (via SDK/DLL integration)  
  - External webcam integration (or placeholder if missing)  
  - Real-time streaming in the web UI  

- 📂 **Data Management**  
  - Automatic saving of scan data into timestamped folders  
  - Conversion to **DICOM** format for medical imaging compatibility  
  - Launch results directly in **ITK-SNAP**  

- 🖥️ **User Interface**  
  - Web GUI served via Flask (`http://127.0.0.1:5000`)  
  - Responsive interface styled with modern CSS  
  - Keyboard shortcuts:  
    - **Arrow Keys**: Continuous jog X/Y  
    - **W/S** (or UI buttons): Jog Z  
    - **ESC**: Emergency Stop  

---

## 🖼️ UI Preview  

> _Screenshots from the web app interface_  

![Main UI Screenshot](static/images/readme_ui_main.png)  
*Main control panel with jog buttons and live feed.*  

![Scanning Screenshot](static/images/readme_ui_scanning.png)  
*Live scanning view with ultrasound video feed.*  

---

## 🔄 Scanning Workflow  

> _Typical sequence when performing a scan_  

![Workflow Diagram](static/images/readme_workflow.png)  

1. **Initialize Scanner** → Homes axes, positions probe  
2. **Insert Specimen** → Plate lowers automatically  
3. **Start Scan** → Probe traverses X-axis at scan speed  
4. **Record Ultrasound Data** → Stored in timestamped data folder  
5. **Export to DICOM** → View results in ITK-SNAP  

---

## 🏗️ Project Structure  

```

3Sonic\_App/
│
├── app/
│   ├── main.py                # Flask app entrypoint
│   ├── config.py              # Centralized configuration
│   ├── core/                  # Core logic
│   │   ├── serial\_manager.py      # Serial connection & G-code I/O
│   │   ├── scanner\_control.py     # High-level scanner movement API
│   │   ├── keyboard\_control.py    # Keyboard jogging support
│   │   └── ultrasound\_sdk.py      # Ultrasound DLL integration
│   │
│   ├── integrations/
│   │   └── itk\_snap.py        # ITK-SNAP launcher
│   │
│   ├── scripts/
│   │   └── record.py          # Data recording (Numpy → DICOM)
│   │
│   ├── utils/
│   │   └── webcam.py          # Webcam stream generator
│   │
│   ├── templates/             # HTML views
│   │   ├── main.html
│   │   └── scanning.html
│   │
│   └── static/                # Frontend assets
│       ├── css/app.css
│       ├── js/app.js
│       └── images/
│           ├── 3SONICLogo.png
│           ├── scan.png
│           └── ...
│
├── requirements.txt           # Python dependencies
└── README.md                  # Project documentation

````

---

## ⚙️ Installation  

### 1. Clone the repository  
```bash
git clone https://github.com/<your-org>/3sonic-scanner.git
cd 3sonic-scanner
````

### 2. Create a virtual environment

```bash
python -m venv .venv
```

### 3. Activate the environment

* **Windows (PowerShell):**

  ```bash
  .venv\Scripts\Activate.ps1
  ```
* **Linux/macOS:**

  ```bash
  source .venv/bin/activate
  ```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

---

## ▶️ Running the App

Start the Flask server:

```bash
python app/main.py
```

Open your browser at:
👉 [http://127.0.0.1:5000](http://127.0.0.1:5000)

---

## 🎮 Controls

* **Web UI**

  * Axis jog buttons with selectable step size
  * Rotation controls
  * Scan management (start/stop, multipath)
  * Overview and specimen placement

* **Keyboard** (when the browser window titled `3SONIC Scanner` is focused):

  * ⬆️ **Up Arrow** → Y- continuous jog
  * ⬇️ **Down Arrow** → Y+ continuous jog
  * ⬅️ **Left Arrow** → X+ continuous jog
  * ➡️ **Right Arrow** → X- continuous jog
  * **ESC** → Emergency stop

---

## 🧩 Configuration

All system parameters are centralized in **`app/config.py`**:

* **Serial connection**

  * `SERIAL_PORT` (or auto-detects CH340/USB-SERIAL)
  * `SERIAL_BAUD` (default: 115200)

* **Motion limits:**

  * `X_MAX`, `Y_MAX`, `Z_MAX`

* **Feedrates:**

  * `JOG_FEED_MM_PER_MIN` → Manual jog speed
  * `FAST_FEED_MM_PER_MIN` → Initialization moves
  * `SCAN_SPEED_MM_PER_MIN` → Scan path moves

* **Ultrasound settings**

  * Frame size: `ULTRA_W`, `ULTRA_H`
  * DLL: `US_DLL_NAME`

* **Data directories**

  * Frames, raws, dicom, logs automatically created in `static/data/`

---

## 🛡️ Safety

* **Emergency stop** (`ESC` key or UI button) sends `M112` immediately
* Axis moves are clamped to `[0, MAX]` bounds
* E-axis (rotation) persists to disk to avoid drift between runs
* Serial connection automatically reconnects if unplugged

---

## 🧪 Development Notes

* Start the app with `use_reloader=False` to avoid multiple DLL/serial initializations.
* The **keyboard listener** requires admin privileges on Windows.
* Placeholder images are shown if the webcam/ultrasound probe is not detected.
* Tested with Python **3.10–3.12**.

---

## 📸 Screenshots / Demo Video

You can add more media here:

* [ ] GIF of live scanning
* [ ] Screenshot of ITK-SNAP with exported DICOM
* [ ] Short demo video

---

## 📜 License

This project is proprietary to **3SONIC**.
For licensing inquiries, please contact the maintainers.

```

---

Would you like me to also **make a simple `workflow.png` diagram** (axes → ultrasound → DICOM → ITK-SNAP) in Mermaid/Graphviz so you don’t need to design it manually?
```
