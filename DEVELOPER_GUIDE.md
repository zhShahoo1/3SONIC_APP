3SONIC_APP — Developer Guide
=============================

Purpose
-------
This short developer guide explains where to change UI and motion tunables, how keyboard vs GUI motion is implemented, and the current defaults in the repository. It points to the exact files and symbol names you will edit. The intent is to make safe changes without breaking motion stability.

Files of interest
-----------------
- `app/config.py` — centralized defaults and environment overrides for almost every tunable.
- `app/main.py` — Flask routes and the server-side UI continuous-move orchestration. Look for `_start_ui_continuous_move`, `/move_probe` and `/move_probe/start|/stop` routes.
- `app/core/scanner_control.py` — low-level motion helpers: `deltaMove()`, `move_absolute()`, `jog_once()`, E-axis persistence and parsing of `M114` responses.
- `app/core/keyboard_control.py` — global keyboard hooks and the continuous jog worker used when the user holds arrow keys.
- `templates/main.html` — server-side injection of UI tunables for the browser (variables like `window.__UI_LINEAR_FEED`).
- `static/js/app.js` — client-side wiring for buttons, hold-to-move behavior and debounced single-click moves. Key functions: `bindButtons()`, `sendMove()`, hold-to-start (`/move_probe/start`) and stop (`/move_probe/stop`).

Quick summary — how motion is implemented
----------------------------------------
- Single clicks (X/Y/Z small steps) → frontend calls `POST /move_probe` with `{direction, step}` → handled in `app/main.py` route `move_probe()` which enqueues the jog in `_JOG_Q` and `pssc.jog_once()` executes the motion using `deltaMove()` (fire-and-forget `send_now` G91/G1 + return to G90).
- Hold-to-move (UI continuous): frontend calls `POST /move_probe/start` at pointerdown and `/move_probe/stop` at pointerup. Server-side `_start_ui_continuous_move()` spawns a worker that sends repeated small `G1` increments under `G91` at a cadence `tick_s`.
- Keyboard continuous jog: `app/core/keyboard_control.py` spawns a per-key worker that takes a shared lock `pssc.UI_MODE_LOCK`, switches to relative (`G91`), then repeatedly `send_now` small `G1` steps until release. Keyboard code also has `STEP_CONTINUOUS_MM` and `STEP_INTERVAL_S` that determine the granularity.
- Rotation (E-axis): rotation uses absolute `move_absolute('E', value)` via `rotate_nozzle_clockwise()` / `rotate_nozzle_counterclockwise()` — E position is persisted to `data/e_axis_position.txt`.

Important tunables and defaults (current)
---------------------------------------
These defaults are defined in `app/config.py` and injected to the browser in `templates/main.html`.

- Geometry limits:
  - `X_MAX` default: 118.0 mm
  - `Y_MAX` default: 118.0 mm
  - `Z_MAX` default: 160.0 mm

- Linear feed / scan speeds:
  - `UI_LINEAR_FEED_MM_PER_MIN` default: 360.0 (UI hold-to-move linear feed)
  - `UI_ROTATION_FEED_MM_PER_MIN` default: 80.0 (UI rotation feed)
  - `JOG_FEED_MM_PER_MIN` default: 2400.0 (used by `deltaMove()` and some jog helpers)
  - `SCAN_SPEED_MM_PER_MIN` default: 180.0 (legacy default for scanning when not computed)
  - `FAST_FEED_MM_PER_MIN` default: 1200.0 (alias; used for faster positioning)

- Continuous tick / cadence:
  - `UI_DEFAULT_TICK_S` default: 0.02 (server-side default tick for UI continuous moves)
  - keyboard worker `STEP_INTERVAL_S` default: 0.015 (keyboard per-tick sleep)
  - keyboard per-tick increment `STEP_CONTINUOUS_MM` default: 0.10 mm

- Rotation / E-axis:
  - `E_AXIS_DEFAULT_STEP` default: 0.15 mm (default step for rotate keys)
  - `ELEV_RESOLUTION_MM` default: 0.06 mm (e_r — elevation resolution; used to compute scan feed when `SCAN_FEED_FROM_ER_FPS` is enabled)
  - E-axis persistent file: `app/data/e_axis_position.txt` (the code writes `data/e_axis_position.txt`, check `app/core/scanner_control.py` `_E_AXIS_POS_FILE`)

- Safety / click limits (introduced to prevent accidental large moves):
  - `UI_MAX_CLICK_STEP_MM` default: 20.0 mm — server will clamp single-click `step` values to this maximum in `app/main.py` `move_probe()`.

- Keyboard quick-stop (optional):
  - Environment variable `KEYBOARD_QUICKSTOP_ON_RELEASE` (default: disabled) — when enabled the keyboard release handler will issue a firmware quick-stop command (M410) on release, see `app/core/keyboard_control.py`.

Where to change each value
--------------------------
- Edit `app/config.py` to permanently change defaults. Most values read environment variables via `_env_float()` or `_env_bool()` so you can also override them at runtime without editing the file.
  - Example: `UI_LINEAR_FEED_MM_PER_MIN` is defined in `app/config.py` as `UI_LINEAR_FEED_MM_PER_MIN: float = _env_float("UI_LINEAR_FEED_MM_PER_MIN", 360.0)`

- Frontend injection: `templates/main.html` exposes server `Config` values to the browser. Example (search for `window.__UI_LINEAR_FEED` in `templates/main.html`) — change `Config` in `app/config.py` or the template if you need a different runtime injection.

- Per-keyboard behavior: `app/core/keyboard_control.py` defines `STEP_CONTINUOUS_MM` and `STEP_INTERVAL_S`. If you modify these, the keyboard jog cadence/granularity will change. The file also computes a capped `FEEDRATE` based on `Config.UI_LINEAR_FEED_MM_PER_MIN`.

- Server continuous UI behavior: `app/main.py` contains `_start_ui_continuous_move(action, feed_mm_per_min, tick_s)` — this implements the hold-to-move worker. If you want to tune hold behavior, adjust `UI_DEFAULT_TICK_S` or the `feed_mm_per_min` sent from the frontend (the frontend uses `window.__UI_LINEAR_FEED`).

- Low-level motion primitive: `deltaMove(delta, axis)` in `app/core/scanner_control.py` performs the relative `G1` step under `G91`; `jog_once()` maps directions to `deltaMove()` or rotation helpers.

How to safely change speeds (recommended workflow)
-----------------------------------------------
1. Edit `app/config.py` or set the environment variable (preferred to avoid committing config changes):
   - PowerShell example (temporary for session):
     ```powershell
     $env:UI_LINEAR_FEED_MM_PER_MIN = "420"
     $env:UI_DEFAULT_TICK_S = "0.02"
     python -m app.main
     ```
2. Restart the application.
3. Test on hardware with small steps first (e.g., `distance` step = `0.1 mm`) and observe stability.
4. Increase `UI_LINEAR_FEED_MM_PER_MIN` in small increments and re-test.

Developer tips and gotchas
-------------------------
- Always prefer changing `app/config.py` via env vars in production; editing the file is fine for development but less flexible.
- Don't increase tick frequency (smaller `UI_DEFAULT_TICK_S`) too aggressively — very small ticks will flood the serial interface with many small `send_now` writes and can cause motion jitter or serial failures. If you need smoother motion, increase `feed_mm_per_min` paired with a reasonable tick (0.02–0.05s).
- The app uses two sending styles:
  - `send_now(...)` (fire-and-forget) — used for responsive UI moves. It does not wait for firmware `ok`. Overuse can saturate serial if performed too frequently.
  - `send_gcode(...)` (queued + read window) — used for commands where we expect feedback (M114, M400). It is serialized and slower.
- When changing keyboard workers, ensure you preserve the shared `UI_MODE_LOCK` in `app/core/scanner_control.py` to avoid G90/G91 races between code paths.

Example: change rotation step to 1 degree equivalent
---------------------------------------------------
If your hardware uses 0.02 mm per degree for E-axis, set:
```powershell
$env:E_AXIS_MM_PER_DEG = "0.02"
```
Then you can compute the mm per degree and update `E_AXIS_DEFAULT_STEP` or adjust the frontend rotation step exposure.

Where to add diagnostics
------------------------
- To debug suspicious large-step requests, add a short logging line in `app/main.py` `move_probe()` that logs the incoming `direction`, `step`, and the remote IP. The server already clamps by `UI_MAX_CLICK_STEP_MM`, but logging helps find buggy clients.

Notes about safety
------------------
- `UI_MAX_CLICK_STEP_MM` is a server-side safety net — it clamps single-click requested steps on the backend. Continuous hold behavior is intentionally left unchanged for responsiveness; the backend enforces a minimum tick and will clamp feedrates to `UI_MAX_FEED_MM_PER_MIN`.
- If you need software soft-limits on axes to prevent crossing physical boundaries, implement them in `app/core/scanner_control.py` by checking `get_position_axis(axis)` and rejecting moves that would cross `Config.X_MAX`, `Config.Y_MAX`, `Config.Z_MAX` — but do this carefully to avoid race conditions and unnecessary M114 polling.

Appendix — Key symbols quick reference
-------------------------------------
- `app/config.py`:
  - `UI_LINEAR_FEED_MM_PER_MIN` (float)
  - `UI_ROTATION_FEED_MM_PER_MIN` (float)
  - `UI_DEFAULT_TICK_S` (float)
  - `UI_MAX_CLICK_STEP_MM` (float)
  - `JOG_FEED_MM_PER_MIN` (float)
  - `E_AXIS_DEFAULT_STEP` (float)

- `app/main.py`:
  - `move_probe()` — single-click route (enqueues jog)
  - `/move_probe/start` & `/move_probe/stop` — hold-to-move API
  - `_start_ui_continuous_move()` — worker that sends repeated relative G1 steps

- `app/core/scanner_control.py`:
  - `deltaMove(delta, axis)` — low-level relative fire-and-forget
  - `rotate_nozzle_clockwise(step)` / `rotate_nozzle_counterclockwise(step)` — E-axis absolute moves
  - `get_position()` / `get_position_axis(axis)` — M114 query and parsing

- `app/core/keyboard_control.py`:
  - `STEP_CONTINUOUS_MM`, `STEP_INTERVAL_S` — keyboard jog tick and increment
  - optional env `KEYBOARD_QUICKSTOP_ON_RELEASE`

If anything above is unclear or you want, I can:
- Add a small `docs/CHANGELOG.md` summarizing recent changes, or
- Create a script `scripts/set_ui_config.ps1` to quickly set recommended env vars when launching the app, or
- Add light-weight logging for motion requests to help debug unexpected large steps.

— End of guide
