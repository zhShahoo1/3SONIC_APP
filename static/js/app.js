/* ==========================================================================
   3SONIC – Frontend Controller
   - Works with Flask routes in main.py
   - Debounced move commands
   - Insert Bath toggle (lower ↔ position-for-scan)
   - Ultrasound auto-reload + status overlay + restart backoff
   - Exit button confirm → /api/exit
   - Overview Image picker (lists scans, open OS viewer or browser)
   ========================================================================== */
(() => {
  // ------------------------------ Config -----------------------------------
  const ENDPOINTS = {
    init: "/initscanner",
    scan: "/scanpath",
    multipath: "/multipath",
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

  const SELECTORS = {
    stepSelect: "#distance",
    ultrasoundImg: "#im1",
    webcamImg: "#im2",
    scanModal: "#scanInProgress",
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

  // ------------------------------ State ------------------------------------
  const state = {
    get step() {
      const el = $(SELECTORS.stepSelect);
      const v = parseFloat(el?.value || "1");
      return Number.isFinite(v) ? v : 1;
    },
    insertBathStage: 0, // 0 = Insert Bath -> lower; 1 = Raise Plate to Scan -> position
    view: "ultrasound", // "ultrasound" | "camera"
  };

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
    // Ensure there’s at least one text node to update
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

  // --------------------------- Actions (API) --------------------------------
  async function initScanner(btn) {
    setBusy(btn, true);
    try { await apiGet(ENDPOINTS.init); console.log("[init] done"); }
    catch (e) { console.error("[init] failed:", e.message); }
    finally { setBusy(btn, false); }
  }

  async function startScan(btn) {
    const modal = $(SELECTORS.scanModal);
    setBusy(btn, true); toggle(modal, true);
    try { await apiGet(ENDPOINTS.scan); console.log("[scan] started"); }
    catch (e) { console.error("[scan] start failed:", e.message); }
    finally { setBusy(btn, false); toggle(modal, false); }
  }

  async function startMultiPath(btn) {
    const modal = $(SELECTORS.scanModal);
    setBusy(btn, true); toggle(modal, true);
    try { await apiGet(ENDPOINTS.multipath); console.log("[multipath] started"); }
    catch (e) { console.error("[multipath] start failed:", e.message); }
    finally { setBusy(btn, false); toggle(modal, false); }
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
    setBusy(btn, true);
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

          case "exit": return exitApp(btn);
        }
      });
    });
  }

  // Keyboard shortcuts
  function bindKeyboard() {
    // const map = new Map([
    //   ["ArrowLeft", () => sendMove("Xminus", state.step)],
    //   ["ArrowRight", () => sendMove("Xplus", state.step)],
    //   ["ArrowUp", () => sendMove("Yminus", state.step)],
    //   ["ArrowDown", () => sendMove("Yplus", state.step)],
    //   ["PageUp", () => sendMove("Zplus", state.step)],
    //   ["PageDown", () => sendMove("Zminus", state.step)],

    //   ["a", () => sendMove("Xminus", state.step)],
    //   ["d", () => sendMove("Xplus", state.step)],
    //   ["w", () => sendMove("Yminus", state.step)],
    //   ["s", () => sendMove("Yplus", state.step)],

    //   ["r", () => rotateCW(state.step)],
    //   ["f", () => rotateCCW(state.step)],

    //   ["1", () => setStep(0.1)],
    //   ["2", () => setStep(1)],
    //   ["3", () => setStep(10)],
    //   ["[", () => cycleStep(-1)],
    //   ["]", () => cycleStep(+1)],
    // ]);

    window.addEventListener("keydown", (ev) => {
      const tag = (ev.target?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "select" || tag === "textarea") return;
      const fn = map.get(ev.key);
      if (fn) { ev.preventDefault(); fn(); }
    });
  }

  function setStep(val) {
    const el = $(SELECTORS.stepSelect);
    if (el) el.value = String(val);
    console.log("[step] set to", val, "mm");
  }

  function cycleStep(direction = +1) {
    const steps = ["0.1", "0.5", "1", "3", "5", "10"];
    const el = $(SELECTORS.stepSelect); if (!el) return;
    const idx = steps.indexOf(el.value);
    const next = Math.min(steps.length - 1, Math.max(0, idx + direction));
    el.value = steps[next]; el.dispatchEvent(new Event("change"));
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

    // default visible
    showUltrasound();

    // safety cue for big steps
    const stepEl = $(SELECTORS.stepSelect);
    if (stepEl) {
      const apply = () => stepEl.classList.toggle("danger-step", parseFloat(stepEl.value || "1") >= 5);
      stepEl.addEventListener("change", apply); apply();
    }
  }

  document.addEventListener("DOMContentLoaded", init);

  // ----------------------- Minimal Styles (spinner fallback) -----------------
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
  `;
  document.head.appendChild(style);
})();
