/* ==========================================================================
   3SONIC – Frontend Controller (with Scan-Path picker)
   - Works with Flask routes in main.py
   - Debounced move commands
   - Insert Bath toggle (lower ↔ position-for-scan)
   - Ultrasound auto-reload + status overlay + restart backoff
   - Exit button confirm → /api/exit
   - Overview Image picker (lists scans, open OS viewer or browser)
   - NEW: Scan-Path selector (Long 0–118, Short 15–90, Custom x0–x1)
          Persists to localStorage and posts to backend with POST→GET fallback
   ========================================================================== */
(() => {
  // ------------------------------ Config -----------------------------------
  const ENDPOINTS = {
    init: "/initscanner",
    scan: "/scanpath",            // accepts POST {mode,x0,x1}; falls back to GET with query
    multipath: "/multipath",      // accepts POST {mode,x0,x1}; falls back to GET with query
    move: "/move_probe",
    openITK: "/open-itksnap",
    lowerPlate: "/api/lower-plate",
    posForScan: "/api/position-for-scan",
    exit: "/api/exit",
    // ultrasound maintenance
    usRestart: "/api/us-restart",
    // overview picker
    overviewList: "/api/overview/list",
    overviewOpen: "/api/overview/open",
  };

  // Let HTML override max X with: <body data-xmax="118">
  const XMAX = (() => {
    const raw =
      document.body?.dataset?.xmax ??
      document.documentElement?.dataset?.xmax ??
      "118";
    const v = parseFloat(raw);
    return Number.isFinite(v) ? v : 118;
  })();

  const DEFAULTS = {
    long: { x0: 0, x1: XMAX },
    short: { x0: 15, x1: Math.min(90, XMAX) },
  };

  const SELECTORS = {
    // stepSelect removed: UI no longer exposes distance dropdown
    ultrasoundImg: "#im1",
    webcamImg: "#im2",
    buttons: "[data-action]",
    lowerPlateBtn: "#lower-plate-btn",
  };

  // ------------------------------ Helpers ----------------------------------
  const $  = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const toggle = (el, show) => { if (el) el.style.display = show ? "block" : "none"; };
  const setBusy = (el, busy = true) => { if (!el) return; el.toggleAttribute("disabled", !!busy); el.classList.toggle("is-loading", !!busy); };

  const debounce = (fn, wait = 120) => {
    let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
  };

  async function apiGet(url) {
    const res = await fetch(url, { method: "GET" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const ct = res.headers.get("content-type") || "";
    return ct.includes("application/json") ? res.json() : res.text();
  }

  async function apiPostJSON(url, data = {}) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const isJson = res.headers.get("content-type")?.includes("application/json");
    const payload = isJson ? await res.json() : await res.text();
    if (!res.ok) throw new Error(isJson ? (payload?.message || "Request failed") : String(payload));
    return payload;
  }

  async function postWithGetFallback(url, data) {
    try { return await apiPostJSON(url, data); }
    catch (e) {
      // graceful fallback for older handlers that expect GET
      const qs = new URLSearchParams(Object.fromEntries(
        Object.entries(data).map(([k, v]) => [k, String(v)])
      )).toString();
      return apiGet(`${url}?${qs}`);
    }
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function parseNum(s, def = 0) { const v = parseFloat(s); return Number.isFinite(v) ? v : def; }

  // ------------------------------ State ------------------------------------
  const state = {
    get step() {
      // Step dropdown removed: rely on global default (injected by template)
      const v = parseFloat(window.__E_AXIS_DEFAULT_STEP || "1");
      return Number.isFinite(v) ? v : 1;
    },
    insertBathStage: 0, // 0 = Insert Bath -> lower; 1 = Raise Plate to Scan -> position
    view: "ultrasound", // "ultrasound" | "camera"
    scan: loadScanSettings(),   // {mode: 'long'|'short'|'custom', x0, x1}
  };

  function loadScanSettings() {
    try {
      const raw = localStorage.getItem("scanSettings_v2");
      if (raw) {
        const s = JSON.parse(raw);
        // sanitize
        const mode = (s.mode || "long").toLowerCase();
        let x0 = parseNum(s.x0, DEFAULTS.long.x0);
        let x1 = parseNum(s.x1, DEFAULTS.long.x1);
        if (mode === "short" && (s.x0 == null || s.x1 == null)) {
          x0 = DEFAULTS.short.x0; x1 = DEFAULTS.short.x1;
        }
        if (mode === "long" && (s.x0 == null || s.x1 == null)) {
          x0 = DEFAULTS.long.x0; x1 = DEFAULTS.long.x1;
        }
        if (x1 <= x0) { x1 = clamp(x0 + 1, 0, XMAX); }
        return { mode: ["long","short","custom"].includes(mode) ? mode : "long", x0: clamp(x0,0,XMAX), x1: clamp(x1,0,XMAX) };
      }
    } catch {}
    return { mode: "long", ...DEFAULTS.long };
  }

  function saveScanSettings(s) {
    state.scan = s;
    try { localStorage.setItem("scanSettings_v2", JSON.stringify(s)); } catch {}
  }

  // --------------------------- UI Helpers ----------------------------------
  function findStreamTitleEl() {
    const us = $(SELECTORS.ultrasoundImg) || $(SELECTORS.webcamImg);
    if (!us) return null;
    const card = us.closest(".card") || document;
    return card.querySelector(".card-header");
  }

  function setStreamTitle(txt) {
    const title = findStreamTitleEl();
    if (!title) return;
    // Keep icons; replace only the text node after them
    let textNode = null;
    for (const n of title.childNodes) {
      if (n.nodeType === Node.TEXT_NODE) { textNode = n; break; }
    }
    if (textNode) {
      textNode.textContent = ` ${txt}`;
    } else {
      title.appendChild(document.createTextNode(` ${txt}`));
    }
  }

  function setActiveToggle(which /* "ultrasound" | "camera" */) {
    state.view = which;
    const usBtn = document.querySelector('[data-action="show-ultrasound"]');
    const camBtn = document.querySelector('[data-action="show-webcam"]');
    usBtn?.classList.toggle("active", which === "ultrasound");
    camBtn?.classList.toggle("active", which === "camera");
    usBtn?.setAttribute("aria-pressed", String(which === "ultrasound"));
    camBtn?.setAttribute("aria-pressed", String(which === "camera"));
    setStreamTitle(which === "ultrasound" ? "Ultrasound View" : "Camera View");
  }

  // ----------------------- Scan-Path Modal (on the fly) ---------------------
  function ensureScanModal() {
    let modal = document.getElementById("scanOptionsModal");
    if (modal) return modal;

    modal = document.createElement("div");
    modal.id = "scanOptionsModal";
    modal.className = "modal";
    modal.innerHTML = `
      <div class="modal-content" style="max-width:560px">
        <h3 style="margin-bottom:10px;font-weight:300">Scan Path</h3>
        <div class="scan-grid">
          <div class="row">
            <label><input type="radio" name="scan-mode" value="long"> Long (0–${XMAX})</label>
            <label><input type="radio" name="scan-mode" value="short"> Short (15–${Math.min(90, XMAX)})</label>
            <label><input type="radio" name="scan-mode" value="custom"> Custom</label>
          </div>
          <div class="row custom-row">
            <label>X start (mm)
              <input type="number" id="scan-x0" min="0" max="${XMAX}" step="0.1" inputmode="decimal">
            </label>
            <label>X end (mm)
              <input type="number" id="scan-x1" min="0" max="${XMAX}" step="0.1" inputmode="decimal">
            </label>
          </div>
          <div class="hint" id="scan-hint"></div>
        </div>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px">
          <button class="button" id="scanCancelBtn" style="width:auto;padding:.5rem .9rem">Cancel</button>
          <button class="button primary" id="scanStartBtn" style="width:auto;padding:.5rem .9rem">
            <i class="fas fa-play-circle"></i> Start
          </button>
        </div>
      </div>`;
    document.body.appendChild(modal);

    // styling (lightweight)
    const st = document.createElement("style");
    st.textContent = `
      .modal{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;align-items:center;justify-content:center;z-index:9999}
      .modal-content{background:#2b303b;color:#e5e9f0;border:1px solid rgba(143,188,187,.3);border-radius:12px;padding:16px 18px}
      .scan-grid .row{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0}
      .scan-grid label{display:flex;align-items:center;gap:8px}
      .scan-grid input[type="number"]{width:120px}
      .scan-grid .hint{font-size:.85rem;opacity:.85;margin-top:6px}
      .button.primary{background:#3cb3ad;border-color:#3cb3ad}
    `;
    document.head.appendChild(st);

    // bind buttons
    $("#scanCancelBtn", modal).addEventListener("click", () => toggle(modal, false));
    return modal;
  }

  function openScanModal(onStart) {
    const modal = ensureScanModal();
    const modeEls = $$('input[name="scan-mode"]', modal);
    const x0El = $("#scan-x0", modal);
    const x1El = $("#scan-x1", modal);
    const hint = $("#scan-hint", modal);

    // seed current values
    const s = state.scan;
    for (const el of modeEls) el.checked = (el.value === s.mode);
    if (s.mode === "custom") {
      x0El.value = String(s.x0 ?? "");
      x1El.value = String(s.x1 ?? "");
    } else if (s.mode === "short") {
      x0El.value = String(DEFAULTS.short.x0);
      x1El.value = String(DEFAULTS.short.x1);
    } else {
      x0El.value = String(DEFAULTS.long.x0);
      x1El.value = String(DEFAULTS.long.x1);
    }

    const applyDisabled = () => {
      const m = getSelectedMode();
      const custom = m === "custom";
      x0El.toggleAttribute("disabled", !custom);
      x1El.toggleAttribute("disabled", !custom);
      hint.textContent = m === "short"
        ? `Will scan X=${DEFAULTS.short.x0}→${DEFAULTS.short.x1} mm`
        : m === "long"
          ? `Will scan X=${DEFAULTS.long.x0}→${DEFAULTS.long.x1} mm`
          : `Set a valid range within 0–${XMAX} mm`;
    };

    function getSelectedMode() {
      const el = modeEls.find(e => e.checked);
      return el ? el.value : "long";
    }

    modeEls.forEach(el => el.addEventListener("change", applyDisabled));
    applyDisabled();

    $("#scanStartBtn", modal).onclick = () => {
      const mode = getSelectedMode();
      let x0, x1;
      if (mode === "custom") {
        x0 = clamp(parseNum(x0El.value, 0), 0, XMAX);
        x1 = clamp(parseNum(x1El.value, XMAX), 0, XMAX);
        if (!Number.isFinite(x0) || !Number.isFinite(x1) || x1 <= x0) {
          alert("Custom range must be numeric and X end must be greater than X start.");
          return;
        }
      } else if (mode === "short") {
        x0 = DEFAULTS.short.x0; x1 = DEFAULTS.short.x1;
      } else {
        x0 = DEFAULTS.long.x0; x1 = DEFAULTS.long.x1;
      }

      const settings = { mode, x0, x1 };
      saveScanSettings(settings);
      toggle(modal, false);
      if (typeof onStart === "function") onStart(settings);
    };

    toggle(modal, true);
  }

  // --------------------------- Actions (API) --------------------------------
  async function initScanner(btn) {
    setBusy(btn, true);
    try { await apiGet(ENDPOINTS.init); console.log("[init] done"); }
    catch (e) { console.error("[init] failed:", e.message); }
    finally { setBusy(btn, false); }
  }

  async function startScan(btn, opts = { prompt: true }) {
    const run = async (settings) => {
      setBusy(btn, true);
      try {
        await postWithGetFallback(ENDPOINTS.scan, settings);
        console.log("[scan] started", settings);
      } catch (e) {
        console.error("[scan] start failed:", e.message);
        alert("Failed to start scan: " + e.message);
      } finally { setBusy(btn, false); }
    };

    if (opts?.prompt !== false) openScanModal(run);
    else run(state.scan);
  }

  async function startMultiPath(btn, opts = { prompt: true }) {
    const run = async (settings) => {
      setBusy(btn, true);
      try {
        await postWithGetFallback(ENDPOINTS.multipath, settings);
        console.log("[multipath] started", settings);
      } catch (e) {
        console.error("[multipath] start failed:", e.message);
        alert("Failed to start dual sweep: " + e.message);
      } finally { setBusy(btn, false); }
    };

    if (opts?.prompt !== false) openScanModal(run);
    else run(state.scan);
  }

  async function openITKSnap(btn) {
    setBusy(btn, true);
    try { const res = await apiPostJSON(ENDPOINTS.openITK); console.log("[ITK] response:", res); }
    catch (e) { console.error("[ITK] error:", e.message); }
    finally { setBusy(btn, false); }
  }

  // ---- Overview picker ----
  async function overviewImage(btn) {
    setBusy(btn, true);
    try {
      const res = await apiGet(ENDPOINTS.overviewList + "?limit=100");
      const items = Array.isArray(res?.items) ? res.items : [];
      buildAndShowOverviewPicker(items);
    } catch (e) {
      console.error("[overview] list error:", e.message);
    } finally {
      setBusy(btn, false);
    }
  }

  async function exitApp(btn) {
    if (!confirm("Exit the app now? This will close the scanner connection and window.")) return;
    setBusy(btn, true); const ROT_TICK = window.__UI_DEFAULT_TICK || 0.02;
    const originalHTML = btn.innerHTML;
    try {
      btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Exiting...';
      await fetch(ENDPOINTS.exit, { method: "POST" });
      setTimeout(() => {
        try { window.close(); } catch {}
        try { window.location.href = "about:blank"; } catch {}
      }, 250);
    } catch (e) {
      console.error("[exit] error:", e.message);
      setTimeout(() => { btn.innerHTML = originalHTML; setBusy(btn, false); }, 1200);
    }
  }

  // Movement & rotation (debounced)
  const sendMove = debounce(async (direction, step) => {
    try { const res = await apiPostJSON(ENDPOINTS.move, { direction, step }); console.log("[move]", direction, step, res); }
    catch (e) { console.error("[move] error:", direction, e.message); }
  }, 80);

  const rotateCW  = debounce(async (step) => { try { await apiPostJSON(ENDPOINTS.move, { direction: "rotateClockwise", step }); } catch (e) { console.error("[rotate] CW error:", e.message); } }, 120);
  const rotateCCW = debounce(async (step) => { try { await apiPostJSON(ENDPOINTS.move, { direction: "rotateCounterclockwise", step }); } catch (e) { console.error("[rotate] CCW error:", e.message); } }, 120);

  // Continuous rotation settings (used when button is held)
  const ROT_FEED = window.__UI_ROTATION_FEED || window.__UI_DEFAULT_FEED || 160.0;
  const ROT_TICK = window.__UI_DEFAULT_TICK || 0.02;
  // ---------------------- Insert Bath / Scan Toggle -------------------------
  function bindInsertBath() {
    const btn = $(SELECTORS.lowerPlateBtn);
    if (!btn) return;

    const setBtnLabel = (txt) => {
      const span = btn.querySelector("span");
      if (span) span.textContent = txt; else btn.textContent = txt;
    };

    async function handleLowerPlate() {
      try {
        setBusy(btn, true);
        if (state.insertBathStage === 0) {
          console.log("[bath] lower plate…");
          await apiPostJSON(ENDPOINTS.lowerPlate, {});
          setBtnLabel("Raise Plate to Scan");
          state.insertBathStage = 1;
        } else {
          console.log("[bath] position for scan…");
          await apiPostJSON(ENDPOINTS.posForScan, {});
          setBtnLabel("Insert Bath");
          state.insertBathStage = 0;
        }
      } catch (e) {
        console.error("[bath] error:", e.message);
        setBtnLabel("Insert Bath");
        state.insertBathStage = 0;
      } finally {
        setBusy(btn, false);
      }
    }

    btn.addEventListener("click", handleLowerPlate);
    setBtnLabel(state.insertBathStage === 0 ? "Insert Bath" : "Raise Plate to Scan");
  }

  // --------------------------- UI Bindings ----------------------------------
  function bindButtons() {
    // Track recent hold timestamps to suppress click after a hold
    const _holdTs = {};
    const HOLD_THRESHOLD_MS = window.__UI_HOLD_THRESHOLD_MS || 150;
    const CLICK_SUPPRESS_MS = window.__UI_CLICK_SUPPRESS_MS || 350;

    $$(SELECTORS.buttons).forEach((btn) => {
      const action = btn.dataset.action;
      btn.addEventListener("click", (ev) => {
        // Holding Shift will reuse last scan settings and skip the dialog
        const quick = ev.shiftKey;
        // If this button was just used as a hold, suppress the click
        const last = _holdTs[action] || 0;
        if (last && (performance.now() - last) < CLICK_SUPPRESS_MS) { ev.preventDefault(); return; }

        switch (action) {
          case "init": return initScanner(btn);
          // case "scan": return startScan(btn, { prompt: !quick });
          // case "multipath": return startMultiPath(btn, { prompt: !quick });
          case "scan": return startScan(btn, { prompt: true });
          case "multipath": return startMultiPath(btn, { prompt: true });
          case "open-itk": return openITKSnap(btn);
          case "overview": return overviewImage(btn);

          case "x-plus": return sendMove("Xplus", state.step);
          case "x-minus": return sendMove("Xminus", state.step);
          case "y-plus": return sendMove("Yplus", state.step);
          case "y-minus": return sendMove("Yminus", state.step);
          case "z-plus": return sendMove("Zplus", state.step);
          case "z-minus": return sendMove("Zminus", state.step);

          case "rot-cw": return rotateCW(state.step);
          case "rot-ccw": return rotateCCW(state.step);

          case "show-ultrasound": return showUltrasound();
          case "show-webcam": return showWebcam();

          case "exit": return exitApp(btn);
        }
      });

      // Add hold-to-rotate for rotation buttons: start continuous rotation on
      // pointerdown/touchstart, stop on pointerup/touchend/window blur. We use
      // the backend `/move_probe/start` and `/move_probe/stop` endpoints so
      // behavior matches the existing hold-to-move implementation.
      if (action === 'rot-cw' || action === 'rot-ccw') {
        let holdActive = false;

        const startContinuous = async (ev) => {
          try {
            ev.preventDefault();
          } catch (_) {}
          if (holdActive) return;
          holdActive = true;
          _holdTs[action] = performance.now();
          btn.classList.add('active-moving');
          try {
            await apiPostJSON(ENDPOINTS.move + '/start', { action: action, speed: ROT_FEED, tick_s: ROT_TICK });
          } catch (e) {
            // Try the root endpoint if the concatenated one is unexpected
            try { await apiPostJSON('/move_probe/start', { action: action, speed: ROT_FEED, tick_s: ROT_TICK }); } catch (_) { console.error('[rotate] start failed', e && e.message); }
          }
        };

        const stopContinuous = async (ev) => {
          if (!holdActive) return;
          holdActive = false;
          // leave a small timestamp so the click handler knows a hold just happened
          _holdTs[action] = performance.now();
          btn.classList.remove('active-moving');
          try {
            await apiPostJSON(ENDPOINTS.move + '/stop', { action: action });
          } catch (e) {
            try { await apiPostJSON('/move_probe/stop', { action: action }); } catch (_) { console.error('[rotate] stop failed', e && e.message); }
          }
          // Clear the hold timestamp shortly after to allow normal clicks later
          setTimeout(() => { _holdTs[action] = 0; }, 400);
        };

        // Pointer events provide unified handling for mouse/touch; fall back to
        // touch/mouse for older browsers. Use document-level end events to
        // ensure stop fires even if pointer leaves the button.
        btn.addEventListener('pointerdown', startContinuous);
        btn.addEventListener('touchstart', startContinuous, { passive: false });
        btn.addEventListener('mousedown', startContinuous);

        document.addEventListener('pointerup', stopContinuous);
        document.addEventListener('touchend', stopContinuous);
        document.addEventListener('mouseup', stopContinuous);
        window.addEventListener('blur', stopContinuous);
      }
    });
  }
  

  // Keyboard shortcuts
  function bindKeyboard() {
    const map = new Map([
      ["ArrowLeft", () => sendMove("Xminus", state.step)],
      ["ArrowRight", () => sendMove("Xplus", state.step)],
      ["ArrowUp", () => sendMove("Yminus", state.step)],
      ["ArrowDown", () => sendMove("Yplus", state.step)],
      ["PageUp", () => sendMove("Zplus", state.step)],
      ["PageDown", () => sendMove("Zminus", state.step)],

      ["a", () => sendMove("Xminus", state.step)],
      ["d", () => sendMove("Xplus", state.step)],
      ["w", () => sendMove("Yminus", state.step)],
      ["s", () => sendMove("Yplus", state.step)],

      ["r", () => rotateCW(state.step)],
      ["f", () => rotateCCW(state.step)],

      // Step keyboard shortcuts removed (dropdown no longer available)

      // quick actions
      // ["Enter", () => startScan(null, { prompt: false })],
    ]);

    window.addEventListener("keydown", (ev) => {
      const tag = (ev.target?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "select" || tag === "textarea") return;
      const fn = map.get(ev.key);
      if (fn) { ev.preventDefault(); fn(); }
    });
  }

  function setStep(val) {
    // Disabled: step dropdown has been removed
    console.log("[step] set requested (ignored) ->", val, "mm");
  }

  function cycleStep(direction = +1) {
    // Disabled: dropdown removed
    console.log("[step] cycle requested (ignored)");
  }

  // ----------------------------- Streams -----------------------------------
  function showUltrasound() {
    const us = $(SELECTORS.ultrasoundImg);
    const cam = $(SELECTORS.webcamImg);
    if (!us || !cam) return;
    us.style.display = "";
    cam.style.display = "none";
    setActiveToggle("ultrasound");
  }

  function showWebcam() {
    const us = $(SELECTORS.ultrasoundImg);
    const cam = $(SELECTORS.webcamImg);
    if (!us || !cam) return;
    cam.style.display = "";
    us.style.display = "none";
    setActiveToggle("camera");
  }

  // ---- Ultrasound status overlay (non-intrusive)
  function ensureUsOverlay() {
    const img = $(SELECTORS.ultrasoundImg);
    if (!img) return null;
    const parent = img.parentElement;
    if (!parent) return null;
    if (!parent.style.position) parent.style.position = "relative";

    let overlay = parent.querySelector(".status-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "status-overlay hidden";
      overlay.innerHTML = `
        <div class="status-card">
          <div class="spinner"></div>
          <div class="title">Reconnecting to ultrasound…</div>
          <div class="subtitle">If the probe was unplugged, please plug it back in.</div>
        </div>`;
      parent.appendChild(overlay);
    }
    return overlay;
  }

  // ----------------------- Connection Badges -------------------------------
  function ensureConnectionBadges() {
    // header-status container created in template; ensure it exists
    const container = document.getElementById('header-status');
    if (!container) return;
    // nothing to create — badges are placed in template; ensure aria roles
    container.setAttribute('role', 'status');
  }

  function updateConnectionBadges(status) {
    try {
      const ser = document.getElementById('status-serial');
      const prb = document.getElementById('status-probe');
      if (!ser || !prb) return;

      // Serial
      const sdot = ser.querySelector('.status-dot');
      if (status && status.serial) {
        ser.classList.add('ok'); ser.classList.remove('off');
        sdot.classList.remove('offline'); sdot.classList.add('online');
        ser.querySelector('.status-text').textContent = 'Serial';
        ser.title = 'Serial: connected';
      } else {
        ser.classList.remove('ok'); ser.classList.add('off');
        sdot.classList.remove('online'); sdot.classList.add('offline');
        ser.querySelector('.status-text').textContent = 'Serial';
        ser.title = 'Serial: disconnected';
      }

      // Probe
      const pdot = prb.querySelector('.status-dot');
      if (status && status.ultrasound) {
        prb.classList.add('ok'); prb.classList.remove('off');
        pdot.classList.remove('offline'); pdot.classList.add('online');
        prb.querySelector('.status-text').textContent = 'Probe';
        prb.title = 'Ultrasound probe: connected';
      } else {
        prb.classList.remove('ok'); prb.classList.add('off');
        pdot.classList.remove('online'); pdot.classList.add('offline');
        prb.querySelector('.status-text').textContent = 'Probe';
        prb.title = 'Ultrasound probe: disconnected';
      }
    } catch (e) { console.error('[connections] update failed', e); }
  }

  let _connPollTimer = null;
  function startConnectionPoll(period = 1500) {
    // avoid duplicate timers
    if (_connPollTimer) return;
    (async function loop() {
      while (true) {
        try {
          const res = await fetch('/api/connections');
          if (res.ok) {
            const j = await res.json();
            if (j && j.success) updateConnectionBadges(j);
          }
        } catch (e) {
          // ignore transient errors
        }
        await new Promise(r => setTimeout(r, period));
      }
    })();
  }

  // ---- Ultrasound auto-reload with backoff and optional backend restart
  function bindUltrasoundAutoReload() {
    const us = $(SELECTORS.ultrasoundImg);
    if (!us) return;

    const overlay = ensureUsOverlay();

    let lastReload = 0;
    let errorCount = 0;
    let watchdog = null;
    const MIN_RELOAD_MS = 1500;
    const WATCHDOG_MS = 5000;

    const showStatus = (title, sub) => {
      if (!overlay) return;
      overlay.classList.remove("hidden");
      const t = overlay.querySelector(".title");
      const s = overlay.querySelector(".subtitle");
      if (t) t.textContent = title || "Reconnecting to ultrasound…";
      if (s) s.textContent = sub || "If the probe was unplugged, please plug it back in.";
    };
    const hideStatus = () => { overlay?.classList.add("hidden"); };

    const reload = (reason = "reload") => {
      const now = Date.now();
      if (now - lastReload < MIN_RELOAD_MS) return; // throttle
      lastReload = now;

      if (state.view === "ultrasound") {
        showStatus("Reconnecting to ultrasound…", "Re-establishing the live stream.");
      }

      clearTimeout(watchdog);
      watchdog = setTimeout(async () => {
        errorCount += 1;
        console.warn(`[ultrasound] watchdog timeout (#${errorCount})`);
        if (errorCount % 3 === 0) {
          try {
            showStatus("Restarting ultrasound driver…", "Please wait a moment.");
            await apiPostJSON(ENDPOINTS.usRestart, {});
          } catch (e) {
            console.error("[ultrasound] restart failed:", e.message);
          }
        }
        lastReload = 0; // allow an immediate retry
        reload("watchdog");
      }, WATCHDOG_MS);

      us.src = "/ultrasound_video_feed?ts=" + now;
      console.log("[ultrasound]", reason, "→ src reset");
    };

    us.addEventListener("load", () => {
      errorCount = 0;
      clearTimeout(watchdog);
      hideStatus();
      console.log("[ultrasound] stream (re)connected");
    });

    us.addEventListener("error", () => {
      errorCount += 1;
      console.warn("[ultrasound] <img> error");
      reload("img-error");
    });

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") reload("tab-visible");
    });

    setInterval(() => reload("periodic"), 60000); // periodic nudge
    reload("init"); // initial kick
  }

  // ---- Overview picker modal (built on the fly) ----
  function buildAndShowOverviewPicker(items) {
    let modal = document.getElementById("overviewPickerModal");
    if (!modal) {
      modal = document.createElement("div");
      modal.id = "overviewPickerModal";
      modal.className = "modal";
      modal.innerHTML = `
        <div class="modal-content" style="max-width:780px">
          <h3 style="margin-bottom:12px;font-weight:300">Choose Overview Image</h3>
          <div id="overviewList" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;max-height:60vh;overflow:auto;margin:10px 0 16px;"></div>
          <div style="display:flex;justify-content:flex-end;gap:8px;">
            <button class="button" id="overviewCloseBtn" style="width:auto;padding:.5rem .9rem">Close</button>
          </div>
        </div>`;
      document.body.appendChild(modal);
      $("#overviewCloseBtn", modal).addEventListener("click", () => toggle(modal, false));
      modal.addEventListener("click", (e) => { if (e.target === modal) toggle(modal, false); });
    }

    const list = $("#overviewList", modal);
    list.innerHTML = "";

    if (!items.length) {
      list.innerHTML = `<div style="grid-column:1/-1;color:#ccc">No overview images found.</div>`;
    } else {
      for (const it of items) {
        const card = document.createElement("div");
        Object.assign(card.style, {
          border: "1px solid rgba(143,188,187,.2)",
          borderRadius: "10px",
          padding: "8px",
          background: "rgba(59,66,82,.5)",
          display: "flex",
          flexDirection: "column",
          gap: "8px",
        });

        const img = document.createElement("img");
        img.src = it.png_url + "?t=" + Date.now();
        img.alt = it.folder;
        Object.assign(img.style, { width: "100%", height: "110px", objectFit: "cover" });
        img.loading = "lazy";

        const meta = document.createElement("div");
        Object.assign(meta.style, { display: "flex", justifyContent: "space-between", alignItems: "center", gap: "6px" });

        const name = document.createElement("div");
        name.textContent = it.folder;
        Object.assign(name.style, { fontSize: ".8rem", opacity: "0.9", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" });

        const actions = document.createElement("div");
        Object.assign(actions.style, { display: "flex", gap: "6px" });

        const openNative = document.createElement("button");
        openNative.className = "button";
        Object.assign(openNative.style, { width: "auto", padding: ".35rem .6rem" });
        openNative.textContent = "Open";
        openNative.title = "Open with system image viewer";
        openNative.addEventListener("click", async (e) => {
          e.stopPropagation(); setBusy(openNative, true);
          try { await apiPostJSON(ENDPOINTS.overviewOpen, { folder: it.folder }); }
          catch (err) { console.error("[overview open] error:", err.message); }
          finally { setBusy(openNative, false); }
        });

        const openBrowser = document.createElement("a");
        openBrowser.className = "button";
        Object.assign(openBrowser.style, { width: "auto", padding: ".35rem .6rem" });
        openBrowser.textContent = "View";
        openBrowser.href = it.png_url;
        openBrowser.target = "_blank";
        openBrowser.rel = "noopener";

        actions.appendChild(openNative);
        actions.appendChild(openBrowser);
        meta.appendChild(name);
        meta.appendChild(actions);

        card.appendChild(img);
        card.appendChild(meta);
        list.appendChild(card);
      }
    }

    toggle(modal, true);
  }

  // ----------------------------- Init ---------------------------------------
  function init() {
    bindButtons();
    bindKeyboard();
    bindInsertBath();
    bindUltrasoundAutoReload();
    bindWindowControls();

    // default visible
    showUltrasound();

    // Start axis position polling and update the display
    try { startPositionPoll(); } catch (e) { console.error('[position] poll start failed', e); }

    // Start connection status badges and polling
    try { ensureConnectionBadges(); startConnectionPoll(); } catch (e) { console.error('[connections] init failed', e); }

    // header auto-hide disabled (restored to previous behaviour)

    
  }

  document.addEventListener("DOMContentLoaded", init);

  // ----------------------- Window Controls (desktop only) -----------------
  function bindWindowControls() {
    const container = document.getElementById('window-controls');
    if (!container) return;
    const toggleBtn = document.getElementById('wc-toggle');
    const btns = document.getElementById('wc-buttons');
    const minBtn = document.getElementById('win-minimize');
    const maxBtn = document.getElementById('win-maximize');
    const closeBtn = document.getElementById('win-close');

    const STORAGE_KEY = 'windowControlsVisible_v1';
    const readVisible = () => {
      try { const v = localStorage.getItem(STORAGE_KEY); if (v === null) return true; return v === '1'; } catch { return true; }
    };
    const setVisible = (v) => {
      try { if (!v) container.classList.add('hidden'); else container.classList.remove('hidden'); localStorage.setItem(STORAGE_KEY, v ? '1' : '0'); } catch { if (!v) container.classList.add('hidden'); else container.classList.remove('hidden'); }
    };

    // Initialize visibility
    setVisible(readVisible());

    toggleBtn?.addEventListener('click', (e) => {
      try { const current = !container.classList.contains('hidden'); setVisible(!current); } catch (err) { console.error('[win-controls] toggle failed', err); }
    });

    function callApi(name) {
      try {
        if (window.pywebview && window.pywebview.api && typeof window.pywebview.api[name] === 'function') {
          window.pywebview.api[name]();
        } else {
          // Fallbacks for non-desktop: try window controls
          if (name === 'close') { try { window.close(); } catch {} }
          if (name === 'minimize') { try { window.blur(); } catch {} }
          if (name === 'maximize' || name === 'toggle_maximize') { try { window.focus(); } catch {} }
        }
      } catch (e) { console.error('[win-controls] api call failed', e); }
    }

    minBtn?.addEventListener('click', () => callApi('minimize'));
    maxBtn?.addEventListener('click', () => callApi('toggle_maximize'));
    closeBtn?.addEventListener('click', () => {
      if (!confirm('Close the app?')) return; callApi('close');
    });
  }

  // ------------------------- Position Polling -------------------------------
  // Fetch /api/position and update the small axis display added to the UI.
  async function fetchPosition() {
    try {
      const res = await fetch('/api/position');
      if (!res.ok) throw new Error('status ' + res.status);
      const j = await res.json();
      return j;
    } catch (e) {
      return null;
    }
  }

  function updateAxisDisplay(pos) {
    if (!pos) return;
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (!el) return;
      // Show placeholder when value is missing
      if (v == null) {
        el.textContent = '—';
        return;
      }
      const n = Number(v);
      if (Number.isFinite(n)) {
        // One decimal place for readability; 0 => "0.0"
        el.textContent = n.toFixed(1);
      } else {
        el.textContent = String(v);
      }
    };
    set('axis-x', pos.x);
    set('axis-y', pos.y);
    set('axis-z', pos.z);
    set('axis-e', pos.e ?? pos.extruder ?? '—');
  }

  let _posPollTimer = null;
  function startPositionPoll(interval = 600) {
    // Avoid multiple timers
    if (_posPollTimer) return;
    (async function loop() {
      while (true) {
        const p = await fetchPosition();
        if (p) updateAxisDisplay(p);
        await new Promise(r => setTimeout(r, interval));
      }
    })();
  }

  // ----------------------- Minimal Styles (spinner & modal) ------------------
  const style = document.createElement("style");
  style.textContent = `
    .is-loading { position: relative; pointer-events: none; opacity:.7; }
    .is-loading::after {
      content:""; position:absolute; inset:0;
      background:
        radial-gradient(circle at 50% 50%, rgba(255,255,255,.9) 0 20%, transparent 21%),
        conic-gradient(from 0turn, rgba(255,255,255,0) 0 85%, rgba(255,255,255,.9) 86% 100%);
      -webkit-mask: radial-gradient(circle, transparent 58%, #000 59%);
      mask: radial-gradient(circle, transparent 58%, #000 59%);
      animation: spin 1s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .danger-step { border: 2px solid #fa5252; background-color: #fff5f5; }
    .status-overlay{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.35)}
    .status-overlay.hidden{display:none}
    .status-card{background:rgba(43,48,59,.95);border:1px solid rgba(143,188,187,.4);padding:12px 16px;border-radius:10px;display:flex;gap:10px;align-items:center}
    .status-card .spinner{width:16px;height:16px;border:3px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin 1s linear infinite}
    .modal{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;align-items:center;justify-content:center;z-index:9999}
  `;
  document.head.appendChild(style);

  
})();
