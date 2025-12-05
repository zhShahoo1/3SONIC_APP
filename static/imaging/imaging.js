(function () {
  "use strict";

  const JSON_HEADERS = { "Content-Type": "application/json" };

  async function getJson(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  async function postJson(url, payload) {
    const resp = await fetch(url, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload ?? {}),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  let backendState = null;
  let backendReady = false;

  function setControlValue(key, text) {
    document.querySelectorAll(`[data-control="${CSS.escape(key)}"] [data-value]`).forEach((el) => {
      el.textContent = text;
    });
  }

  function setSliderValue(key, value) {
    const wrapper = document.querySelector(`[data-slider="${CSS.escape(key)}"]`);
    if (!wrapper) return;
    const input = wrapper.querySelector("input[type=range]");
    const valueEl = wrapper.querySelector("[data-slider-value]");
    if (sliderConfig[key]) sliderConfig[key].value = Number(value);
    if (input) {
      input.value = value;
      updateSliderVisual(input);
    }
    if (valueEl) valueEl.textContent = value;
  }

  function togglePanelAvailability(enabled) {
    const panel = document.querySelector("[data-imaging-panel]");
    if (!panel) return;
    panel.classList.toggle("imaging-disabled", !enabled);
    panel.querySelectorAll("button, input").forEach((el) => {
      const locked = el.dataset && el.dataset.locked === "true";
      const optional = Boolean(el.closest("[data-optional]"));
      if (locked) {
        el.disabled = true;
      } else {
        el.disabled = !enabled && !optional;
      }
    });
  }

  function applyBackendState(state) {
    backendState = state;
    backendReady = Boolean(state?.initialized && state?.probe_connected);
    togglePanelAvailability(backendReady);

    if (!state) return;

    if (state.frequency_label != null) {
      const label = String(state.frequency_label).includes("MHz") ? state.frequency_label : `${state.frequency_label} MHz`;
      setControlValue("frequency", label);
    }
    if (state.depth_mm != null) setControlValue("depth", `${state.depth_mm} mm`);
    if (state.dynamic_range_db != null) setControlValue("dynamicRange", `${state.dynamic_range_db} dB`);
    if (state.focal_depth_mm != null) setControlValue("focus", `${state.focal_depth_mm} mm`);
    if (state.focal_zones_count != null) setControlValue("focusesNumber", String(state.focal_zones_count));
    if (state.focal_zone_idx != null) setControlValue("focusSet", String(Number(state.focal_zone_idx) + 1));
    if (state.steering_angle_deg != null) setControlValue("angle", `${state.steering_angle_deg} °`);

    if (typeof state.gain_percent === "number") setSliderValue("gain", state.gain_percent);
    if (typeof state.power_db === "number") setSliderValue("power", state.power_db);
    if (Array.isArray(state.tgc_profile)) {
      state.tgc_profile.forEach((percent, idx) => setSliderValue(`tgc${idx}`, percent));
    }

    const scanToggle = document.querySelector('[data-toggle="scanDirection"]');
    if (scanToggle) scanToggle.checked = Boolean(state.scan_direction_inverted);
  }

  async function refreshBackendState() {
    try {
      const data = await getJson("/api/state");
      applyBackendState(data);
    } catch (err) {
      console.error("[imaging] state refresh failed", err);
      applyBackendState(null);
    }
  }

  const directionEndpoints = {
    focus: {
      endpoint: "/api/focus",
      apply: (res) => {
        if (typeof res?.value === "number") setControlValue("focus", `${res.value} mm`);
      },
    },
    focusSet: {
      endpoint: "/api/focus_zone",
      apply: (res) => {
        if (typeof res?.zone_idx === "number") setControlValue("focusSet", String(res.zone_idx + 1));
        if (typeof res?.zones_count === "number") setControlValue("focusesNumber", String(res.zones_count));
        if (typeof res?.focal_depth_mm === "number") setControlValue("focus", `${res.focal_depth_mm} mm`);
      },
    },
    depth: {
      endpoint: "/api/depth",
      apply: (res) => {
        if (typeof res?.value === "number") setControlValue("depth", `${res.value} mm`);
      },
    },
    dynamicRange: {
      endpoint: "/api/dynamic_range",
      apply: (res) => {
        if (typeof res?.value === "number") setControlValue("dynamicRange", `${res.value} dB`);
      },
    },
    frequency: {
      endpoint: "/api/frequency",
      apply: (res) => {
        if (res?.label) {
          const txt = String(res.label).includes("MHz") ? res.label : `${res.label} MHz`;
          setControlValue("frequency", txt);
        }
      },
    },
    angle: {
      endpoint: "/api/steering",
      apply: (res) => {
        if (typeof res?.value === "number") setControlValue("angle", `${res.value} °`);
      },
    },
  };

  const directionKeys = new Set(Object.keys(directionEndpoints));
  const readonlyControls = new Set(["focusesNumber"]);

  async function handleSliderAction(key, value) {
    let endpoint = null;
    let payload = null;

    if (key === "power") {
      endpoint = "/api/power";
      payload = { value: Number(value) };
    } else if (key === "gain") {
      endpoint = "/api/gain";
      payload = { value: Number(value) };
    } else if (key.startsWith("tgc")) {
      const idx = Number(key.replace("tgc", ""));
      if (Number.isInteger(idx)) {
        endpoint = `/api/tgc/${idx}`;
        payload = { percent: Number(value) };
      }
    }

    if (!endpoint) return;

    try {
      await postJson(endpoint, payload);
      void refreshBackendState();
    } catch (err) {
      console.error(`[imaging] slider ${key} update failed`, err);
    }
  }

  async function handleDirectionAction(key, direction) {
    const cfg = directionEndpoints[key];
    if (!cfg) {
      stepOption(key, direction);
      return;
    }
    try {
      const res = await postJson(cfg.endpoint, { direction });
      if (typeof cfg.apply === "function") cfg.apply(res);
      if (cfg.refresh !== false) void refreshBackendState();
    } catch (err) {
      console.error(`[imaging] ${key} update failed`, err);
    }
  }

  const optionMap = {
    focus: ["0 - 11 mm", "3 - 14 mm", "7 - 20 mm", "11 - 32 mm", "14 - 36 mm"],
    focusesNumber: ["1", "2", "3", "4", "5", "6", "7"],
    focusSet: ["1", "2", "3", "4", "5", "6", "7"],
    depth: [
      "20 mm",
      "25 mm",
      "30 mm",
      "35 mm",
      "40 mm",
      "45 mm",
      "50 mm",
      "60 mm",
      "70 mm",
      "80 mm",
      "90 mm",
      "100 mm",
    ],
    dynamicRange: ["36 dB", "42 dB", "48 dB", "54 dB", "60 dB", "66 dB", "72 dB", "78 dB", "84 dB", "90 dB", "96 dB", "102 dB"],
    frequency: ["7.5 MHz", "10 MHz", "14 MHz", "18 MHz", "14 MHz (THI)", "7.5 MHz (ITHi)", "8 MHz (ITHi)"],
    angle: ["-20 °", "-15 °", "-10 °", "-5 °", "0 °", "5 °", "10 °", "15 °", "20 °"],
    enhancementMethod: ["1", "2", "3"],
    speckleLevel: ["1 Pure View", "2 Pure View", "3 Pure View", "4 Pure View", "5 Pure View"],
    frameAveraging: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
  };

  const state = {
    focus: "7 - 20 mm",
    focusesNumber: "3",
    focusSet: "1",
    depth: "40 mm",
    dynamicRange: "54 dB",
    frequency: "14 MHz",
    angle: "0 °",
    enhancementMethod: "3",
    speckleLevel: "1 Pure View",
    frameAveraging: "5",
    rotation: "0°",
  };

  const sliderConfig = {
    power: { value: 0, unit: " dB" },
    gain: { value: 22, unit: " %" },
    rejection: { value: 19, unit: "" },
  };

  const toggles = {
    imageEnhancement: false,
    speckleReduction: false,
    scanDirection: false,
    negative: false,
  };

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    const panel = document.querySelector("[data-imaging-panel]");
    if (!panel) return;

    setupTabs(panel);
    setupPages(panel);
    setupControls(panel);
    setupSliders(panel);
    setupToggles(panel);
    setupRotation(panel);
    setupRecording(panel);
    setupMeasurements(panel);

    void refreshBackendState();
    window.setInterval(refreshBackendState, 5000);
  }

  function setupTabs(panel) {
    const tabs = Array.from(panel.querySelectorAll(".imaging-tab"));
    const tabPanels = Array.from(panel.querySelectorAll(".imaging-tab-panel"));

    function activateTab(name) {
      tabs.forEach((btn) => {
        const isActive = btn.dataset.tab === name;
        btn.classList.toggle("active", isActive);
        btn.setAttribute("aria-selected", String(isActive));
      });
      tabPanels.forEach((tabpanel) => {
        const isActive = tabpanel.dataset.panel === name;
        tabpanel.classList.toggle("active", isActive);
        tabpanel.setAttribute("aria-hidden", String(!isActive));
      });
      closeDropdown();
    }

    tabs.forEach((btn) => {
      btn.addEventListener("click", () => activateTab(btn.dataset.tab));
    });

    activateTab("imaging");
  }

  function setupPages(panel) {
    const pages = Array.from(panel.querySelectorAll("[data-imaging-page]"));
    const prevBtn = panel.querySelector('[data-page-nav="prev"]');
    const nextBtn = panel.querySelector('[data-page-nav="next"]');
    const indicator = panel.querySelector("[data-page-indicator]");
    let currentPage = 1;

    function render() {
      pages.forEach((page, idx) => {
        page.style.display = idx + 1 === currentPage ? "" : "none";
      });
      if (indicator) indicator.textContent = `${currentPage} / ${pages.length}`;
      if (prevBtn) prevBtn.disabled = currentPage === 1;
      if (nextBtn) nextBtn.disabled = currentPage === pages.length;
      if (prevBtn) prevBtn.style.opacity = prevBtn.disabled ? "0.35" : "1";
      if (nextBtn) nextBtn.style.opacity = nextBtn.disabled ? "0.35" : "1";
      closeDropdown();
    }

    if (prevBtn) {
      prevBtn.addEventListener("click", () => {
        if (currentPage > 1) {
          currentPage -= 1;
          render();
        }
      });
    }

    if (nextBtn) {
      nextBtn.addEventListener("click", () => {
        if (currentPage < pages.length) {
          currentPage += 1;
          render();
        }
      });
    }

    render();
  }

  function setupControls(panel) {
    panel.querySelectorAll("[data-control]").forEach((control) => {
      const key = control.dataset.control;
      if (!key) return;

      if (state[key] && control.querySelector("[data-value]")) {
        control.querySelector("[data-value]").textContent = state[key];
      }

      const prev = control.querySelector('[data-dir="prev"]');
      const next = control.querySelector('[data-dir="next"]');
      const toggle = control.querySelector(".imaging-dropdown-toggle");

      if (readonlyControls.has(key)) {
        if (prev) {
          prev.disabled = true;
          prev.dataset.locked = "true";
        }
        if (next) {
          next.disabled = true;
          next.dataset.locked = "true";
        }
      } else if (directionKeys.has(key)) {
        if (prev) prev.addEventListener("click", () => { void handleDirectionAction(key, -1); });
        if (next) next.addEventListener("click", () => { void handleDirectionAction(key, 1); });
      } else {
        if (prev) prev.addEventListener("click", () => { stepOption(key, -1); });
        if (next) next.addEventListener("click", () => { stepOption(key, 1); });
      }
      if (toggle) {
          if (directionKeys.has(key)) {
            toggle.disabled = true;
          toggle.dataset.locked = "true";
          }
        toggle.addEventListener("click", (event) => {
          event.stopPropagation();
          toggleDropdown(key, control, toggle);
        });
      }
    });

    document.addEventListener("click", (event) => {
      if (openDropdown && !openDropdown.contains(event.target)) {
        const toggle = event.target.closest(".imaging-dropdown-toggle");
        if (!toggle) closeDropdown();
      }
    });
  }

  function stepOption(key, dir) {
    const options = optionMap[key];
    if (!options) return;
    const current = state[key] ?? options[0];
    let idx = options.indexOf(current);
    if (idx === -1) idx = 0;
    const nextIdx = Math.min(options.length - 1, Math.max(0, idx + dir));
    if (nextIdx !== idx) {
      state[key] = options[nextIdx];
      renderValue(key);
    }
  }

  let openDropdown = null;

  function toggleDropdown(key, control, toggle) {
    if (openDropdown && openDropdown.parentElement === control) {
      closeDropdown();
      return;
    }

    closeDropdown();

    const options = optionMap[key];
    if (!options) return;

    const dropdown = document.createElement("div");
    dropdown.className = "imaging-dropdown";

    options.forEach((opt) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = opt;
      if (state[key] === opt) btn.classList.add("active");
      btn.addEventListener("click", () => {
        state[key] = opt;
        renderValue(key);
        closeDropdown();
      });
      dropdown.appendChild(btn);
    });

    const host = control;
    host.appendChild(dropdown);
    openDropdown = dropdown;
  }

  function closeDropdown() {
    if (openDropdown && openDropdown.parentElement) {
      openDropdown.parentElement.removeChild(openDropdown);
    }
    openDropdown = null;
  }

  function renderValue(key) {
    document.querySelectorAll(`[data-control="${CSS.escape(key)}"] [data-value]`).forEach((el) => {
      el.textContent = state[key];
    });

    if (key === "frameAveraging") {
      closeDropdown();
    }
  }

  function updateSliderVisual(input) {
    if (!input) return;
    const min = Number(input.min ?? 0);
    const max = Number(input.max ?? 100);
    const range = max - min;
    const value = Number(input.value ?? min);
    const safeRange = range === 0 ? 1 : range;
    const percent = Math.min(100, Math.max(0, ((value - min) / safeRange) * 100));
    input.style.setProperty("--ratio", `${percent}%`);
  }

  function setupSliders(panel) {
    panel.querySelectorAll("[data-slider]").forEach((wrapper) => {
      const key = wrapper.dataset.slider;
      if (!key || !sliderConfig[key]) return;

      const input = wrapper.querySelector("input[type=range]");
      const valueEl = wrapper.querySelector("[data-slider-value]");
      if (!input || !valueEl) return;

      input.value = sliderConfig[key].value;
      valueEl.textContent = sliderConfig[key].value;
      updateSliderVisual(input);

      input.addEventListener("input", () => {
        sliderConfig[key].value = Number(input.value);
        valueEl.textContent = sliderConfig[key].value;
        updateSliderVisual(input);
      });

      input.addEventListener("change", () => {
        sliderConfig[key].value = Number(input.value);
        valueEl.textContent = sliderConfig[key].value;
        updateSliderVisual(input);
        void handleSliderAction(key, input.value);
      });
    });
  }

  function setupToggles(panel) {
    panel.querySelectorAll("[data-toggle]").forEach((toggle) => {
      const name = toggle.dataset.toggle;
      if (!name) return;
      toggle.checked = !!toggles[name];
      toggle.addEventListener("change", () => {
        toggles[name] = toggle.checked;
        syncDependents(name, toggle.checked);
        if (name === "scanDirection") {
          void postJson("/api/scan_direction", { inverted: toggle.checked })
            .then(refreshBackendState)
            .catch((err) => { console.error("[imaging] scan direction update failed", err); });
        }
      });
      syncDependents(name, toggle.checked);
    });
  }

  function syncDependents(name, active) {
    document.querySelectorAll(`[data-dependent="${CSS.escape(name)}"]`).forEach((element) => {
      element.style.display = active ? "" : "none";
    });
  }

  function setupRotation(panel) {
    const rotationButtons = panel.querySelectorAll("[data-rotation]");
    const display = panel.querySelector("[data-rotation-value]");

    function setRotation(value) {
      state.rotation = value;
      if (display) display.textContent = value;
      rotationButtons.forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.rotation === value);
      });
    }

    rotationButtons.forEach((btn) => {
      btn.addEventListener("click", () => setRotation(btn.dataset.rotation));
    });

    setRotation(state.rotation);
  }

  function setupRecording(panel) {
    const toggleBtn = panel.querySelector("[data-recording-toggle]");
    const statusBox = panel.querySelector("[data-recording-status]");
    const timeEl = panel.querySelector("[data-recording-time]");
    const dot = panel.querySelector("[data-recording-dot]");
    const formatBtns = panel.querySelectorAll("[data-recording-format]");
    const formatDisplay = panel.querySelector("[data-recording-format-display]");

    const recordingState = {
      active: false,
      seconds: 0,
      timer: null,
      format: "mp4",
    };

    function updateTime() {
      const hrs = String(Math.floor(recordingState.seconds / 3600)).padStart(2, "0");
      const mins = String(Math.floor((recordingState.seconds % 3600) / 60)).padStart(2, "0");
      const secs = String(recordingState.seconds % 60).padStart(2, "0");
      if (timeEl) timeEl.textContent = `${hrs}:${mins}:${secs}`;
    }

    function startRecording() {
      if (recordingState.active) return;
      recordingState.active = true;
      recordingState.seconds = 0;
      updateTime();
      if (dot) dot.style.display = "inline-block";
      if (statusBox) statusBox.classList.remove("paused");
      if (toggleBtn) toggleBtn.textContent = "Stop Recording";
      recordingState.timer = window.setInterval(() => {
        recordingState.seconds += 1;
        updateTime();
      }, 1000);
    }

    function stopRecording() {
      if (!recordingState.active) return;
      recordingState.active = false;
      if (recordingState.timer) window.clearInterval(recordingState.timer);
      recordingState.timer = null;
      if (dot) dot.style.display = "none";
      if (statusBox) statusBox.classList.add("paused");
      if (toggleBtn) toggleBtn.textContent = "Start Recording";
    }

    if (toggleBtn) {
      toggleBtn.addEventListener("click", () => {
        if (recordingState.active) stopRecording();
        else startRecording();
      });
    }

    if (formatBtns.length) {
      formatBtns.forEach((btn) => {
        btn.addEventListener("click", () => {
          recordingState.format = btn.dataset.recordingFormat;
          formatBtns.forEach((b) => b.classList.toggle("active", b === btn));
          if (formatDisplay) formatDisplay.textContent = recordingState.format.toUpperCase();
        });
      });
    }

    panel.querySelectorAll("[data-recording-action]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const action = btn.dataset.recordingAction;
        console.log(`[recording] action: ${action}`);
      });
    });

    updateTime();
    if (formatDisplay) formatDisplay.textContent = recordingState.format.toUpperCase();
  }

  function setupMeasurements(panel) {
    const buttons = panel.querySelectorAll("[data-measure-mode]");
    const card = panel.querySelector("[data-measure-card]");
    const modeDisplay = panel.querySelector("[data-measure-mode-display]");

    const measurementState = {
      mode: "none",
    };

    function renderMeasurement() {
      buttons.forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.measureMode === measurementState.mode);
      });
      if (card) {
        card.style.display = measurementState.mode !== "none" ? "" : "none";
      }
      if (modeDisplay) {
        modeDisplay.textContent = measurementState.mode === "none"
          ? "—"
          : capitalize(measurementState.mode);
      }
    }

    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        measurementState.mode = btn.dataset.measureMode;
        renderMeasurement();
      });
    });

    panel.querySelectorAll("[data-measure-action]").forEach((btn) => {
      btn.addEventListener("click", () => {
        console.log(`[measurements] action: ${btn.dataset.measureAction}`);
      });
    });

    renderMeasurement();
  }

  function capitalize(str) {
    return str ? str.charAt(0).toUpperCase() + str.slice(1) : str;
  }
})();
