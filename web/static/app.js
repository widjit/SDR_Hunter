/* SDR Hunter web client: WebSocket streaming + canvas waterfall/spectrum. */
(function () {
  "use strict";

  const wfCanvas = document.getElementById("waterfall");
  const specCanvas = document.getElementById("spectrum");
  const wfCtx = wfCanvas.getContext("2d");
  const specCtx = specCanvas.getContext("2d");
  const signalList = document.getElementById("signalList");
  const statusDot = document.getElementById("statusDot");
  const statusText = document.getElementById("statusText");

  let peakHold = null;
  let wfImage = null;
  const MAX_SIGNALS = 60;
  const signals = [];

  function resizeCanvases() {
    for (const c of [wfCanvas, specCanvas]) {
      c.width = c.clientWidth;
    }
    wfImage = wfCtx.createImageData(wfCanvas.width, 1);
  }
  window.addEventListener("resize", resizeCanvases);

  // ---- color map (viridis-ish) -------------------------------------
  function colorMap(v) {
    v = Math.max(0, Math.min(1, v));
    const r = Math.floor(255 * Math.min(1, Math.max(0, 1.5 * v - 0.3)));
    const g = Math.floor(255 * Math.min(1, Math.max(0, 1.2 * v)));
    const b = Math.floor(255 * Math.min(1, Math.max(0, 1.0 - 1.3 * v)));
    return [r, g, b];
  }

  // ---- waterfall ----------------------------------------------------
  function pushWaterfall(psd) {
    const w = wfCanvas.width;
    const h = wfCanvas.height;
    // Scroll existing image down by 1px.
    const prev = wfCtx.getImageData(0, 0, w, h - 1);
    wfCtx.putImageData(prev, 0, 1);
    // Draw new top row.
    const row = wfCtx.createImageData(w, 1);
    const n = psd.length;
    const vmin = -100, vmax = -20;
    for (let x = 0; x < w; x++) {
      const idx = Math.floor((x / w) * n);
      const norm = (psd[idx] - vmin) / (vmax - vmin);
      const [r, g, b] = colorMap(norm);
      const off = x * 4;
      row.data[off] = r; row.data[off + 1] = g; row.data[off + 2] = b;
      row.data[off + 3] = 255;
    }
    wfCtx.putImageData(row, 0, 0);
  }

  // ---- spectrum -----------------------------------------------------
  function drawSpectrum(psd) {
    const w = specCanvas.width, h = specCanvas.height;
    specCtx.clearRect(0, 0, w, h);
    const n = psd.length;
    const vmin = -110, vmax = -10;
    if (!peakHold || peakHold.length !== n) peakHold = psd.slice();
    for (let i = 0; i < n; i++) peakHold[i] = Math.max(peakHold[i] * 0.999, psd[i]);

    const toY = (db) => h - ((db - vmin) / (vmax - vmin)) * h;

    // grid
    specCtx.strokeStyle = "#1b2733";
    specCtx.lineWidth = 1;
    for (let g = 0; g <= 4; g++) {
      const y = (h / 4) * g;
      specCtx.beginPath(); specCtx.moveTo(0, y); specCtx.lineTo(w, y); specCtx.stroke();
    }

    // peak hold
    specCtx.strokeStyle = "#f59e0b";
    specCtx.beginPath();
    for (let x = 0; x < w; x++) {
      const i = Math.floor((x / w) * n);
      const y = toY(peakHold[i]);
      x === 0 ? specCtx.moveTo(x, y) : specCtx.lineTo(x, y);
    }
    specCtx.stroke();

    // live
    specCtx.strokeStyle = "#22d3ee";
    specCtx.beginPath();
    for (let x = 0; x < w; x++) {
      const i = Math.floor((x / w) * n);
      const y = toY(psd[i]);
      x === 0 ? specCtx.moveTo(x, y) : specCtx.lineTo(x, y);
    }
    specCtx.stroke();
  }

  // ---- signal list --------------------------------------------------
  function addSignal(kind, data) {
    signals.unshift({ kind, data, t: Date.now() });
    if (signals.length > MAX_SIGNALS) signals.pop();
    renderSignals();
  }

  function renderSignals() {
    signalList.innerHTML = "";
    for (const s of signals) {
      const d = s.data;
      const div = document.createElement("div");
      let cls = "sig-item";
      if (s.kind === "unknown") cls += " unknown";
      if (s.kind === "drone") cls += " drone";
      div.className = cls;
      const mhz = ((d.freq_hz || 0) / 1e6).toFixed(4);
      const bw = ((d.bandwidth_hz || 0) / 1e3).toFixed(1);
      let title = d.signal_db_match ? d.signal_db_match.name : (s.kind === "drone" ? (d.match_name || "Suspected drone") : "Unknown");
      div.innerHTML =
        '<div class="freq">' + mhz + " MHz</div>" +
        '<div class="meta">' + title + " · BW " + bw + " kHz · " +
        (d.modulation_hint || d.role || "") +
        " · " + (d.power_db ? d.power_db.toFixed(1) + " dB" : "") + "</div>";
      signalList.appendChild(div);
    }
  }

  // ---- WebSockets ---------------------------------------------------
  function wsURL(path) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return proto + "://" + location.host + path;
  }

  function connectSpectrum() {
    const ws = new WebSocket(wsURL("/ws/spectrum"));
    ws.onopen = () => ws.send("hello");
    ws.onmessage = (ev) => {
      const frame = JSON.parse(ev.data);
      if (frame && frame.psd_db) {
        pushWaterfall(frame.psd_db);
        drawSpectrum(frame.psd_db);
      }
    };
    ws.onclose = () => setTimeout(connectSpectrum, 1500);
  }

  function connectEvents() {
    const ws = new WebSocket(wsURL("/ws/events"));
    ws.onopen = () => ws.send("hello");
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (!msg) return;
      addSignal(msg.kind, msg.data || {});
    };
    ws.onclose = () => setTimeout(connectEvents, 1500);
  }

  // ---- REST controls ------------------------------------------------
  async function loadDevices() {
    try {
      const r = await fetch("/api/devices");
      const j = await r.json();
      const sel = document.getElementById("deviceSel");
      sel.innerHTML = "";
      (j.devices || []).forEach((d) => {
        const o = document.createElement("option");
        o.value = d.driver + "|" + d.serial;
        o.textContent = d.label + (j.soapy_available ? "" : " [mock]");
        sel.appendChild(o);
      });
    } catch (e) { console.error(e); }
  }

  async function startScan() {
    const fs = parseFloat(document.getElementById("freqStart").value) * 1e6;
    const fe = parseFloat(document.getElementById("freqEnd").value) * 1e6;
    await fetch("/api/scan/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ freq_start: fs, freq_end: fe }),
    });
    setStatus(true);
  }

  async function stopScan() {
    await fetch("/api/scan/stop", { method: "POST" });
    setStatus(false);
  }

  function setStatus(on) {
    statusDot.classList.toggle("on", on);
    statusText.textContent = on ? "scanning" : "idle";
  }

  document.getElementById("startBtn").addEventListener("click", startScan);
  document.getElementById("stopBtn").addEventListener("click", stopScan);

  // ---- init ---------------------------------------------------------
  resizeCanvases();
  loadDevices();
  connectSpectrum();
  connectEvents();
})();
