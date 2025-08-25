/* ==========================================================================
   3SONIC – Frontend Controller (modern, quiet UI)
   - Works with your Flask routes in main.py
   - Debounced jog commands (prevents flooding the controller)
   - Insert Bath button toggles lower / position-for-scan
   - Ultrasound stream: status overlay + smart auto-reload
   - Exit button: confirm → POST /api/exit → window closes
   ========================================================================== */

(() => {
  // ------------------------------ Config -----------------------------------
  const ENDPOINTS = {
    init: "/initscanner",
    scan: "/scanpath",
    multipath: "/multipath",
    move: "/move_probe",                  // expects { direction, step }
    openITK: "/open-itksnap",
    overview: "/overViewImage",
    lowerPlate: "/api/lower-plate",       // POST
    posForScan: "/api/position-for-scan", // POST
    exit: "/api/exit",                    // POST
    usStream: "/ultrasound_video_feed",
  };

  const SELECTORS = {
    stepSelect: "#distance",
    ultrasoundImg: "#im1",
    webcamImg: "#im2",
    scanModal: "#scanInProgress",
    buttons: "[data-action]",
    lowerPlateBtn: "#lower-plate-btn",
  };

  // ------------------------------ Helpers ----------------------------------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const toggle = (el, show) => { if (el) el.style.display = show ? "block" : "none"; };

  const setBusy = (el, busy = true) => {
    if (!el) return;
    el.toggleAttribute("disabled", !!busy);
    el.classList.toggle("is-loading", !!busy);
  };

  function debounce(fn, wait = 120) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
  }

  async function apiGet(url) {
    const res = await fetch(url, { method: "GET" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    // most of our GETs are HTML responses; we don't consume them
    return res.text();
  }

  async function apiPostJSON(url, data = {}) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const isJson = res.headers.get("content-type")?.includes("application/json");
    const payload = isJson ? await res.json() : await res.text();
    if (!res.ok) {
      const msg = isJson ? (payload?.message || "Request failed") : String(payload);
      throw new Error(msg);
    }
    return payload;
  }

  // ------------------------------ State ------------------------------------
  const state = {
    insertBathStage: 0, // 0 = Insert Bath -> lower; 1 = Raise Plate to Scan -> position
    get step() {
      const el = $(SELECTORS.stepSelect);
      const v = parseFloat(el?.value || "1");
      return Number.isFinite(v) ? v : 1;
    },
  };

  // --------------------------- Actions (API) --------------------------------
  async function initScanner(btn) {
    setBusy(btn, true);
    try {
      await apiGet(ENDPOINTS.init);
      console.log("[init] done");
    } catch (e) {
      console.error("[init] failed:", e.message);
    } finally {
      setBusy(btn, false);
    }
  }

  async function startScan(btn) {
    const modal = $(SELECTORS.scanModal);
    setBusy(btn, true);
    toggle(modal, true);
    try {
      await apiGet(ENDPOINTS.scan);
      console.log("[scan] started");
    } catch (e) {
      console.error("[scan] start failed:", e.message);
    } finally {
      setBusy(btn, false);
      toggle(modal, false);
    }
  }

  async function startMultiPath(btn) {
    const modal = $(SELECTORS.scanModal);
    setBusy(btn, true);
    toggle(modal, true);
    try {
      await apiGet(ENDPOINTS.multipath);
      console.log("[multipath] started");
    } catch (e) {
      console.error("[multipath] start failed:", e.message);
    } finally {
      setBusy(btn, false);
      toggle(modal, false);
    }
  }

  async function openITKSnap(btn) {
    setBusy(btn, true);
    try {
      const res = await apiPostJSON(ENDPOINTS.openITK);
      console.log("[ITK] response:", res);
    } catch (e) {
      console.error("[ITK] error:", e.message);
    } finally {
      setBusy(btn, false);
    }
  }

  async function overviewImage(btn) {
    setBusy(btn, true);
    try {
      await apiPostJSON(ENDPOINTS.overview);
      console.log("[overview] requested");
    } catch (e) {
      console.error("[overview] error:", e.message);
    } finally {
      setBusy(btn, false);
    }
  }

  async function exitApp(btn) {
    if (!confirm("Exit the app now? This will close the scanner connection and window.")) return;
    setBusy(btn, true);
    const original = btn.innerHTML;
    try {
      btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Exiting...';
      await fetch(ENDPOINTS.exit, { method: "POST" });
      // If running in a browser (not pywebview), try to close/blank
      setTimeout(() => { try { window.close(); } catch {} try { window.location.href = "about:blank"; } catch {} }, 250);
    } catch (e) {
      console.error("[exit] error:", e.message);
      setTimeout(() => { btn.innerHTML = original; setBusy(btn, false); }, 1100);
    }
  }

  // ----------------------------- Jog / Rotate -------------------------------
  const sendMove = debounce(async (direction, step) => {
    try {
      const res = await apiPostJSON(ENDPOINTS.move, { direction, step });
      console.log("[move]", direction, step, res);
    } catch (e) {
      console.error("[move] error:", direction, e.message);
    }
  }, 80);

  const rotateCW  = debounce(async (step) => {
    try { await apiPostJSON(ENDPOINTS.move, { direction: "rotateClockwise", step }); }
    catch (e) { console.error("[rotate] CW error:", e.message); }
  }, 120);

  const rotateCCW = debounce(async (step) => {
    try { await apiPostJSON(ENDPOINTS.move, { direction: "rotateCounterclockwise", step }); }
    catch (e) { console.error("[rotate] CCW error:", e.message); }
  }, 120);

  // ---------------------- Insert Bath / Scan Toggle -------------------------
  function bindInsertBath() {
    const btn = $(SELECTORS.lowerPlateBtn);
    if (!btn) return;

    const setBtnLabel = (txt) => {
      const span = btn.querySelector("span");
      if (span) span.textContent = txt;
      else btn.textContent = txt;
    };

    async function handleLowerPlate() {
      try {
        setBusy(btn, true);
        if (state.insertBathStage === 0) {
          console.log("[bath] lower plate…");
          await apiPostJSON(ENDPOINTS.lowerPlate);
          setBtnLabel("Raise Plate to Scan");
          state.insertBathStage = 1;
        } else {
          console.log("[bath] position for scan…");
          await apiPostJSON(ENDPOINTS.posForScan);
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
    $$(SELECTORS.buttons).forEach((btn) => {
      const action = btn.dataset.action;
      btn.addEventListener("click", () => {
        switch (action) {
          case "init": return initScanner(btn);
          case "scan": return startScan(btn);
          case "multipath": return startMultiPath(btn);
          case "open-itk": return openITKSnap(btn);
          case "overview": return overviewImage(btn);

          case "x-plus": return sendMove("Xplus",  state.step);
          case "x-minus": return sendMove("Xminus", state.step);
          case "y-plus": return sendMove("Yplus",  state.step);
          case "y-minus": return sendMove("Yminus", state.step);
          case "z-plus": return sendMove("Zplus",  state.step);
          case "z-minus": return sendMove("Zminus", state.step);

          case "rot-cw":  return rotateCW(state.step);
          case "rot-ccw": return rotateCCW(state.step);

          case "show-ultrasound": return showUltrasound();
          case "show-webcam":     return showWebcam();

          case "exit": return exitApp(btn);
        }
      });
    });
  }

  // --------------------------- Keyboard ------------------------------------
  function bindKeyboard() {
    const map = new Map([
      ["ArrowLeft",  () => sendMove("Xminus", state.step)],
      ["ArrowRight", () => sendMove("Xplus",  state.step)],
      ["ArrowUp",    () => sendMove("Yminus", state.step)],
      ["ArrowDown",  () => sendMove("Yplus",  state.step)],
      ["PageUp",     () => sendMove("Zplus",  state.step)],
      ["PageDown",   () => sendMove("Zminus", state.step)],

      ["a", () => sendMove("Xminus", state.step)],
      ["d", () => sendMove("Xplus",  state.step)],
      ["w", () => sendMove("Yminus", state.step)],
      ["s", () => sendMove("Yplus",  state.step)],

      ["r", () => rotateCW(state.step)],
      ["f", () => rotateCCW(state.step)],

      ["1", () => setStep(0.1)],
      ["2", () => setStep(1)],
      ["3", () => setStep(10)],
      ["[", () => cycleStep(-1)],
      ["]", () => cycleStep(+1)],
    ]);

    window.addEventListener("keydown", (ev) => {
      const tag = (ev.target?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "select" || tag === "textarea") return;
      const key = ev.key;
      if (map.has(key)) {
        ev.preventDefault();
        map.get(key)();
      }
    });
  }

  function setStep(val) {
    const el = $(SELECTORS.stepSelect);
    if (el) el.value = String(val);
    console.log("[step] set to", val, "mm");
  }

  function cycleStep(direction = +1) {
    const steps = ["0.1", "0.5", "1", "3", "5", "10"];
    const el = $(SELECTORS.stepSelect);
    if (!el) return;
    const idx = steps.indexOf(el.value);
    const next = Math.min(steps.length - 1, Math.max(0, idx + direction));
    el.value = steps[next];
    el.dispatchEvent(new Event("change"));
  }

  // ----------------------------- Streams -----------------------------------
  function showUltrasound() {
    const us = $(SELECTORS.ultrasoundImg);
    const cam = $(SELECTORS.webcamImg);
    if (!us || !cam) return;
    us.style.display = "";
    cam.style.display = "none";
  }

  function showWebcam() {
    const us = $(SELECTORS.ultrasoundImg);
    const cam = $(SELECTORS.webcamImg);
    if (!us || !cam) return;
    cam.style.display = "";
    us.style.display = "none";
  }

  // Ultrasound status overlay (created dynamically)
  function ensureStreamOverlay(imgEl) {
    if (!imgEl) return { card: null, overlay: null };
    // If already wrapped, return existing
    if (imgEl.parentElement?.classList.contains("stream-wrap")) {
      const overlay = imgEl.parentElement.querySelector(".status-overlay");
      const card = overlay?.querySelector(".status-card");
      return { card, overlay };
    }

    const wrap = document.createElement("div");
    wrap.className = "stream-wrap";
    imgEl.replaceWith(wrap);
    wrap.appendChild(imgEl);

    const overlay = document.createElement("div");
    overlay.className = "status-overlay hidden";
    overlay.innerHTML = `
      <div class="status-card">
        <div class="spinner" aria-hidden="true"></div>
        <div class="title">Ultrasound: reconnecting…</div>
        <div class="subtitle">If the probe was unplugged, it will attempt to recover automatically.</div>
      </div>
    `;
    wrap.appendChild(overlay);

    return { card: overlay.querySelector(".status-card"), overlay };
  }

  // Ultrasound auto-reload + overlay feedback
  function bindUltrasoundAutoReload() {
    const us = $(SELECTORS.ultrasoundImg);
    if (!us) return;

    const { overlay } = ensureStreamOverlay(us);

    let lastReload = 0;
    let backoffIdx = 0;
    const MIN_RELOAD_MS = 1500;
    const BACKOFF_STEPS = [1500, 2500, 4000, 6000, 10000]; // progressive retry

    const showOverlay = () => { overlay && overlay.classList.remove("hidden"); };
    const hideOverlay = () => { overlay && overlay.classList.add("hidden"); };

    const reload = (force = false) => {
      const now = Date.now();
      if (!force && now - lastReload < MIN_RELOAD_MS) return;

      lastReload = now;
      const url = `${ENDPOINTS.usStream}?ts=${now}`;
      us.src = url;
      console.log("[ultrasound] reload →", url);
    };

    // When the stream connection is established (img 'load'), hide overlay and reset backoff
    us.addEventListener("load", () => {
      backoffIdx = 0;
      hideOverlay();
      console.log("[ultrasound] connected");
    });

    // On error: show overlay and retry with backoff
    us.addEventListener("error", () => {
      showOverlay();
      const delay = BACKOFF_STEPS[Math.min(backoffIdx, BACKOFF_STEPS.length - 1)];
      backoffIdx = Math.min(backoffIdx + 1, BACKOFF_STEPS.length - 1);
      setTimeout(() => reload(true), delay);
      console.warn("[ultrasound] stream error; retry in", delay, "ms");
    });

    // Visibility return: try a quick reload
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") reload(true);
    });

    // Periodic keep-alive nudges
    setInterval(() => reload(true), 60000);

    // First connection
    reload(true);
  }

  // ----------------------------- Init ---------------------------------------
  function init() {
    bindButtons();
    bindKeyboard();
    bindUltrasoundAutoReload();
    bindInsertBath();
    showUltrasound(); // default visible

    // Step select: flag risky large steps
    const stepEl = $(SELECTORS.stepSelect);
    if (stepEl) {
      const apply = () => stepEl.classList.toggle("danger-step", parseFloat(stepEl.value || "1") >= 5);
      stepEl.addEventListener("change", apply);
      apply();
    }
  }

  document.addEventListener("DOMContentLoaded", init);

  // ----------------------- Minimal inline styles ----------------------------
  const style = document.createElement("style");
  style.textContent = `
    .is-loading { position: relative; pointer-events: none; opacity: .7; }
    .is-loading::after {
      content: "";
      position: absolute; inset: 0;
      background:
        radial-gradient(circle at 50% 50%, rgba(255,255,255,.9) 0 20%, transparent 21%),
        conic-gradient(from 0turn, rgba(255,255,255,.0) 0 85%, rgba(255,255,255,.9) 86% 100%);
      -webkit-mask: radial-gradient(circle, transparent 58%, #000 59%);
      mask: radial-gradient(circle, transparent 58%, #000 59%);
      animation: spin 1s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .danger-step { border: 2px solid #fa5252; background-color: #fff5f5; }
  `;
  document.head.appendChild(style);
})();
