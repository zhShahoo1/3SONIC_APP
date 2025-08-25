/* ==========================================================================
   3SONIC – Frontend Controller (lean, no popups)
   - Works with your Flask routes in main.py
   - Debounced move commands so the controller isn’t flooded
   - Insert Bath button toggles between lower and position-for-scan
   - Ultrasound stream auto-reloads on error and on a timer
   ========================================================================== */

(() => {
  // ------------------------------ Config -----------------------------------
  const ENDPOINTS = {
    init: "/initscanner",
    scan: "/scanpath",
    multipath: "/multipath",
    move: "/move_probe",               // expects { direction, step }
    openITK: "/open-itksnap",
    overview: "/overViewImage",

    lowerPlate: "/api/lower-plate",    // POST
    posForScan: "/api/position-for-scan", // POST
  };

  const SELECTORS = {
    stepSelect: "#distance",
    ultrasoundImg: "#im1",
    webcamImg: "#im2",
    scanModal: "#scanInProgress",
    buttons: "[data-action]",
  };

  // ------------------------------ Helpers ----------------------------------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const toggle = (el, show) => { if (el) el.style.display = show ? "block" : "none"; };
  const setBusy = (el, busy = true) => { if (!el) return; el.toggleAttribute("disabled", !!busy); el.classList.toggle("is-loading", !!busy); };

  function debounce(fn, wait = 120) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
  }

  async function apiGet(url) {
    const res = await fetch(url, { method: "GET" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    // many endpoints return HTML; we don't rely on this value anywhere
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
      await apiPostJSON(ENDPOINTS.overview, {});
      console.log("[overview] opened");
    } catch (e) {
      console.error("[overview] error:", e.message);
    } finally {
      setBusy(btn, false);
    }
  }

  // Movement & rotation (debounced)
  const sendMove = debounce(async (direction, step) => {
    try {
      const res = await apiPostJSON(ENDPOINTS.move, { direction, step });
      console.log("[move]", direction, step, res);
    } catch (e) {
      console.error("[move] error:", direction, e.message);
    }
  }, 80);

  const rotateCW = debounce(async (step) => {
    try {
      const res = await apiPostJSON(ENDPOINTS.move, { direction: "rotateClockwise", step });
      console.log("[rotate] CW", res);
    } catch (e) {
      console.error("[rotate] CW error:", e.message);
    }
  }, 120);

  const rotateCCW = debounce(async (step) => {
    try {
      const res = await apiPostJSON(ENDPOINTS.move, { direction: "rotateCounterclockwise", step });
      console.log("[rotate] CCW", res);
    } catch (e) {
      console.error("[rotate] CCW error:", e.message);
    }
  }, 120);

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
        }
      });
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

      ["1", () => setStep(0.1)],
      ["2", () => setStep(1)],
      ["3", () => setStep(10)],
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

  // Streams
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

  // Ultrasound auto-reload (handles USB disconnects / dead sockets)
  function bindUltrasoundAutoReload() {
    const us = $(SELECTORS.ultrasoundImg);
    if (!us) return;

    const reload = () => { us.src = "/ultrasound_video_feed?ts=" + Date.now(); };

    us.addEventListener("error", reload);   // reload if the stream errors
    setInterval(() => {
      if (document.body.contains(us)) reload(); // periodic nudge
    }, 60000);
  }

  // Insert Bath button (toggle)
  function bindInsertBath() {
    const btn = document.getElementById("lower-plate-btn");
    if (!btn) return;

    let stage = 0; // 0 = insert bath (lower Z) -> then 1 = raise/position for scan

    const setBtnLabel = (txt) => {
      const span = btn.querySelector("span");
      if (span) span.textContent = txt;
      else btn.textContent = txt;
    };

    async function handleClick() {
      try {
        setBusy(btn, true);
        if (stage === 0) {
          console.log("[bath] lower plate…");
          await apiPostJSON(ENDPOINTS.lowerPlate, {});
          setBtnLabel("Raise Plate to Scan");
          stage = 1;
        } else {
          console.log("[bath] position for scan…");
          await apiPostJSON(ENDPOINTS.posForScan, {});
          setBtnLabel("Insert Bath");
          stage = 0;
        }
      } catch (e) {
        console.error("[bath] error:", e.message);
        // Reset UI so user can retry from the beginning
        setBtnLabel("Insert Bath");
        stage = 0;
      } finally {
        setBusy(btn, false);
      }
    }

    btn.addEventListener("click", handleClick);
  }

  // ----------------------------- Init ---------------------------------------
  function init() {
    bindButtons();
    bindKeyboard();
    bindUltrasoundAutoReload();
    bindInsertBath();
    showUltrasound(); // default visible
  }

  document.addEventListener("DOMContentLoaded", init);

  // ----------------------- Minimal Styles (spinner) --------------------------
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
  `;
  document.head.appendChild(style);
})();
