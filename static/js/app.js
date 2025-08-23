/* ==========================================================================
  3SONIC – Frontend Controller (modern, lightweight, resilient)
  --------------------------------------------------------------------------
  - ES modules style (no framework), clean fetch wrappers
  - Keyboard controls (arrows/WASD for XY, PgUp/PgDn for Z, R/F rotate)
  - Smart toasts + progress modal + button loading states
  - Debounced movement to avoid flooding the printer
  - Works with existing Flask endpoints (no backend changes required)
  ========================================================================== */

  (() => {
   // ------------------------------ Config -----------------------------------
   const ENDPOINTS = {
    init: "/initscanner",
    scan: "/scanpath",
    multipath: "/multipath",
    move: "/move_probe",
    rotateCW: "/rotate_nozzle_cw",
    rotateCCW: "/rotate_nozzle_ccw",
    openITK: "/open-itksnap",
    overview: "/overViewImage",
   };
  
   const SELECTORS = {
    stepSelect: "#distance",
    ultrasoundImg: "#im1",
    webcamImg: "#im2",
    scanModal: "#scanInProgress",
    buttons: "[data-action]",
    startScanBtn: "[data-action='scan']",
    multiScanBtn: "[data-action='multipath']",
   };
  
   // ------------------------------ UX Utils ---------------------------------
   const $ = (sel, root = document) => root.querySelector(sel);
   const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  
   const toggle = (el, show) => {
    if (!el) return;
    el.style.display = show ? "block" : "none";
   };
  
   const setBusy = (el, busy = true) => {
    if (!el) return;
    el.toggleAttribute("disabled", !!busy);
    el.classList.toggle("is-loading", !!busy);
   };
  
   // Toasts (no external lib)
   const toastHost = document.createElement("div");
   toastHost.style.position = "fixed";
   toastHost.style.top = "16px";
   toastHost.style.right = "16px";
   toastHost.style.zIndex = "9999";
   document.addEventListener("DOMContentLoaded", () => document.body.appendChild(toastHost));
  
   function toast(msg, type = "info", timeout = 3000) {
    const card = document.createElement("div");
    card.textContent = msg;
    card.className = `toast toast-${type}`;
    toastHost.appendChild(card);
    requestAnimationFrame(() => card.classList.add("show"));
    setTimeout(() => {
      card.classList.remove("show");
      setTimeout(() => card.remove(), 300);
    }, timeout);
   }
  
   // Debounce helper
   function debounce(fn, wait = 120) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), wait);
    };
   }
  
   // ------------------------------ Fetch API --------------------------------
   async function apiGet(url) {
    const res = await fetch(url, { method: "GET" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.text(); // most of our GETs just return HTML/strings
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
      toast("Scanner initialized.", "success");
    } catch (e) {
      toast(`Init failed: ${e.message}`, "error");
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
      toast("Scan started. This view shows progress.", "info", 4000);
    } catch (e) {
      toast(`Scan failed to start: ${e.message}`, "error");
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
      toast("Multi-path scan running.", "info", 4000);
    } catch (e) {
      toast(`Multi-path failed: ${e.message}`, "error");
    } finally {
      setBusy(btn, false);
      toggle(modal, false);
    }
   }
  
   async function openITKSnap(btn) {
    setBusy(btn, true);
    try {
      const res = await apiPostJSON(ENDPOINTS.openITK);
      const ok = res?.success ?? false;
      toast(ok ? "ITK-SNAP launched." : (res?.message || "ITK-SNAP failed."), ok ? "success" : "error");
    } catch (e) {
      toast(`ITK-SNAP error: ${e.message}`, "error");
    } finally {
      setBusy(btn, false);
    }
   }
  
   async function overviewImage(btn) {
    setBusy(btn, true);
    try {
      await apiPostJSON(ENDPOINTS.overview, {});
      toast("Overview image opened.", "success");
    } catch (e) {
      toast(`Overview failed: ${e.message}`, "error");
    } finally {
      setBusy(btn, false);
    }
   }
  
   // Movements
   const sendMove = debounce(async (direction, step) => {
    try {
      const res = await apiPostJSON(ENDPOINTS.move, { direction, step });
      const ok = res?.success ?? false;
      toast(ok ? res?.message ?? `${direction} ok` : (res?.message || `${direction} failed`), ok ? "success" : "error");
    } catch (e) {
      toast(`${direction} error: ${e.message}`, "error");
    }
   }, 80);
  
   const rotateCW = debounce(async (step) => {
    try {
      const res = await apiPostJSON(ENDPOINTS.rotateCW, { step });
      toast(res?.message || "Rotated CW", res?.success ? "success" : "error");
    } catch (e) {
      toast(`Rotate CW error: ${e.message}`, "error");
    }
   }, 120);
  
   const rotateCCW = debounce(async (step) => {
    try {
      const res = await apiPostJSON(ENDPOINTS.rotateCCW, { step });
      toast(res?.message || "Rotated CCW", res?.success ? "success" : "error");
    } catch (e) {
      toast(`Rotate CCW error: ${e.message}`, "error");
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
  
   // Keyboard controls
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
      // avoid interfering with inputs/selects
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
    toast(`Step set to ${val} mm`, "info", 1200);
   }
  
   // Stream toggles
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
  
   // ----------------------------- Init ---------------------------------------
   function init() {
    bindButtons();
    bindKeyboard();
    // ensure ultrasound is the default visible stream
    showUltrasound();
   }
  
   document.addEventListener("DOMContentLoaded", init);

   // New lower-plate control: inserts user's handler for lower-plate button
   document.addEventListener("DOMContentLoaded", () => {
    const lowerPlateBtn = document.getElementById("lower-plate-btn");
    if (!lowerPlateBtn) return;
   
    let insertBathStage = 0; // 0 = "Insert Bath" → lower; 1 = "Raise Plate to Scan" → position for scan
   
    const updateStatus = (txt) => console.log("[Status]", txt); // replace with your own UI status setter
    const showNotification = (title, msg, type="info") => console.log(`[${type}]`, title, msg);
   
    async function apiPost(url, body = {}) {
      const res = await fetch(url, {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || `Error calling ${url}`);
      return data;
    }
   
    async function handleLowerPlate() {
      try {
       lowerPlateBtn.disabled = true;
   
       if (insertBathStage === 0) {
        updateStatus("Lowering plate for bath...");
        const data = await apiPost("/api/lower-plate");
        showNotification("Step 1 Complete", data.message, "success");
        if (data.status) updateStatus(data.status);
        lowerPlateBtn.querySelector("span").textContent = "Raise Plate to Scan";
        insertBathStage = 1;
       } else {
        updateStatus("Positioning for scan...");
        const data = await apiPost("/api/position-for-scan");
        showNotification("Ready", data.message, "success");
        updateStatus(data.status || "Ready");
        lowerPlateBtn.querySelector("span").textContent = "Insert Bath";
        insertBathStage = 0;
       }
      } catch (err) {
       showNotification("Error", err.message, "error");
       updateStatus("Error");
       insertBathStage = 0;
       lowerPlateBtn.querySelector("span").textContent = "Insert Bath";
      } finally {
       lowerPlateBtn.disabled = false;
      }
    }
   
    lowerPlateBtn.addEventListener("click", handleLowerPlate);
   });
  
   // ----------------------- Minimal Styles (toasts) --------------------------
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
  
    .toast {
      min-width: 220px;
      max-width: 360px;
      margin: 6px 0;
      padding: 10px 12px;
      border-radius: 10px;
      color: #0b0e11;
      background: #dbeafe;
      box-shadow: 0 10px 30px rgba(0,0,0,.25);
      opacity: 0;
      transform: translateY(-8px);
      transition: all .25s ease;
      font: 600 13px/1.4 system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    }
    .toast.show { opacity: 1; transform: translateY(0); }
    .toast-success { background: #dcfce7; }
    .toast-error { background: #fee2e2; }
    .toast-info { background: #e0f2fe; }
    .toast-warning { background: #fef9c3; }
   `;
   document.head.appendChild(style);
  })();