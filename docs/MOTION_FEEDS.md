MOTION FEEDS — Quick Reference
==============================

Generated: 2025-12-01

This file lists the current motion speeds, feedrates, ticks and related timing values used by the application.
Each entry includes the symbol name and the file where it is defined so developers can change them safely.

- UI (hold-to-move) — linear (X/Y/Z)
  - Value: 360.0 mm/min
  - Symbol: `Config.UI_LINEAR_FEED_MM_PER_MIN`
  - File: `app/config.py`
  - Equivalent: 6.0 mm/s (360.0 ÷ 60)

- UI (hold-to-move) — rotation (E / nozzle)
  - Value: 80.0 mm/min
  - Symbol: `Config.UI_ROTATION_FEED_MM_PER_MIN`
  - File: `app/config.py`
  - Equivalent: 1.33 mm/s (80.0 ÷ 60)

- Keyboard jog (computed default)
  - Value: 270 mm/min (computed)
  - Computation: `cap = max(50, int(Config.UI_LINEAR_FEED_MM_PER_MIN * 0.75))` → cap = 270; `manual_default` defaults to 2400; `FEEDRATE = int(min(manual_default, cap))` → 270
  - Symbol: `FEEDRATE`
  - File: `app/core/keyboard_control.py`
  - Equivalent: 4.50 mm/s (270 ÷ 60)

- JOG feed (explicit jog constant)
  - Value: 2400.0 mm/min
  - Symbol: `Config.JOG_FEED_MM_PER_MIN`
  - File: `app/config.py`
  - Equivalent: 40.0 mm/s (2400.0 ÷ 60)

- Fast positioning feed (alias)
  - Value: 1200.0 mm/min
  - Symbol: `Config.FAST_FEED_MM_PER_MIN`
  - File: `app/config.py`
  - Equivalent: 20.0 mm/s (1200.0 ÷ 60)

- Scan speed (explicit)
  - Value: 180.0 mm/min
  - Symbol: `Config.SCAN_SPEED_MM_PER_MIN` (env name `SCAN_SPEED`)
  - File: `app/config.py`
  - Equivalent: 3.0 mm/s (180.0 ÷ 60)

- Computed synchronized scan feed (if `SCAN_FEED_FROM_ER_FPS=True`)
  - Formula: F(mm/min) = 60 * (ELEV_RESOLUTION_MM * TARGET_FPS)
  - Example with defaults: 60 * (0.06 mm * 25 Hz) = 90.0 mm/min
  - Equivalent (example): 1.50 mm/s (90.0 ÷ 60)
  - Helper: `Config.computed_scan_feed_mm_per_min()`
  - File: `app/config.py`

- UI maximum allowed feed
 - UI maximum allowed feed
  - Value: 1200.0 mm/min
  - Symbol: `Config.UI_MAX_FEED_MM_PER_MIN`
  - File: `app/config.py`
  - Equivalent: 20.0 mm/s (1200.0 ÷ 60)

- Hold-to-move tick (server/client cadence)
  - Value: 0.02 s
  - Symbol: `Config.UI_DEFAULT_TICK_S`
  - File: `app/config.py`

- Keyboard continuous jog tick / increment
  - Tick (sleep between steps): 0.015 s
    - Symbol: `STEP_INTERVAL_S`
    - File: `app/core/keyboard_control.py`
  - Per-step increment (distance per tick): 0.10 mm
    - Symbol: `STEP_CONTINUOUS_MM`
    - File: `app/core/keyboard_control.py`

- Rotation (E-axis) step default
  - Value: 0.15 mm per step
  - Symbol: `Config.E_AXIS_DEFAULT_STEP`
  - File: `app/config.py`
  - Note: E-axis persistent file referenced in `app/core/scanner_control.py` (see `_E_AXIS_POS_FILE`).

- Z / explicit positioning feeds
 - Z / explicit positioning feeds
  - `Z_FEED_MM_PER_MIN`: 1200 mm/min
  - `XYZ_FEED_MM_PER_MIN`: 1200 mm/min
  - File: `app/config.py`
  - Equivalent: 20.0 mm/s (1200 ÷ 60)

- Travel speed (recorder legacy; mm/s)
  - Value: 0.5 mm/s (legacy `TRAVEL_SPEED_X` default)
  - Symbol: `Config.TRAVEL_SPEED_X_MM_PER_S`
  - File: `app/config.py`

- Safety: maximum single-click GUI step (server clamp)
  - Value: 20.0 mm
  - Symbol: `Config.UI_MAX_CLICK_STEP_MM`
  - File: `app/config.py`
  - Note: `app/main.py` clamps single-click `step` values to this maximum before enqueuing moves.

- UI rotation duration safety
  - Value: 100.0 s
  - Symbol: `Config.UI_ROTATION_MAX_S`
  - File: `app/config.py`

Where to change them
---------------------
- Edit `app/config.py` (preferred) or set the corresponding environment variable before launching the app (e.g. `UI_LINEAR_FEED_MM_PER_MIN`, `UI_DEFAULT_TICK_S`, etc.).
- Keyboard-specific settings are in `app/core/keyboard_control.py` (`STEP_CONTINUOUS_MM`, `STEP_INTERVAL_S`, `USE_QUICKSTOP`).

Safety recommendations
----------------------
- Increase feeds slowly and test small moves first (0.1 mm) to check for jitter.
- Avoid reducing tick times too aggressively — very small ticks will flood the serial interface with many small `send_now` writes and may cause firmware instability.
- Prefer env var overrides rather than committing `app/config.py` edits for temporary tuning.

— End of file
