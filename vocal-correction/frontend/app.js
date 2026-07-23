// Vocal Tuning Correction -- thin frontend (§7).
//
// The pull-strength dial is expensive: it re-runs the backend pipeline
// (debounced). The blend/"preserve original" dial is cheap: it's a pure
// client-side GainNode crossfade between the original upload and whatever
// the last /correct response was, per the two-dial split in spec §5.

const BACKEND_URL = window.BACKEND_URL || "http://localhost:8000";
const PULL_STRENGTH_DEBOUNCE_MS = 600;
const CHORD_PALETTE = ["#6f9fe0", "#e0a039", "#7fd1a8", "#e08fc0", "#b39ddb", "#f2c14e"];

const els = {
  vocalsFile: document.getElementById("vocalsFile"),
  vocalsFilename: document.getElementById("vocalsFilename"),
  mixFile: document.getElementById("mixFile"),
  mixFilename: document.getElementById("mixFilename"),
  scaleInput: document.getElementById("scaleInput"),
  processBtn: document.getElementById("processBtn"),
  statusDot: document.getElementById("statusDot"),
  statusText: document.getElementById("statusText"),
  dialsSection: document.getElementById("dialsSection"),
  waveformSection: document.getElementById("waveformSection"),
  waveformWrap: document.getElementById("waveformWrap"),
  waveformCanvas: document.getElementById("waveformCanvas"),
  playheadCanvas: document.getElementById("playheadCanvas"),
  viewOriginalBtn: document.getElementById("viewOriginalBtn"),
  viewCorrectedBtn: document.getElementById("viewCorrectedBtn"),
  viewBothBtn: document.getElementById("viewBothBtn"),
  timelineSection: document.getElementById("timelineSection"),
  pullStrength: document.getElementById("pullStrength"),
  pullStrengthValue: document.getElementById("pullStrengthValue"),
  preserveOriginal: document.getElementById("preserveOriginal"),
  preserveOriginalValue: document.getElementById("preserveOriginalValue"),
  playPauseBtn: document.getElementById("playPauseBtn"),
  jumpOriginalBtn: document.getElementById("jumpOriginalBtn"),
  jumpCorrectedBtn: document.getElementById("jumpCorrectedBtn"),
  timeReadout: document.getElementById("timeReadout"),
  downloadBtn: document.getElementById("downloadBtn"),
  strip: document.getElementById("strip"),
};

const state = {
  audioCtx: null,
  jobId: null,
  originalBuffer: null,
  correctedBuffer: null,
  chordTimeline: [],
  notesSummary: [],
  durationSeconds: 0,
  sourceOriginal: null,
  sourceCorrected: null,
  gainOriginal: null,
  gainCorrected: null,
  isPlaying: false,
  playbackStartedAt: 0, // AudioContext time when playback (re)started
  playbackOffset: 0,    // seconds into the buffer at that start
  pullDebounceTimer: null,
  rafId: null,
  waveformMode: "both",
  isScrubbing: false,
  scrubWasPlaying: false,
};

function getAudioCtx() {
  if (!state.audioCtx) {
    state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  return state.audioCtx;
}

function setStatus(text, mode = "ok") {
  els.statusText.textContent = text;
  els.statusDot.className = `dot${mode === "ok" ? "" : ` ${mode}`}`;
}

function formatTime(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function updateProcessEnabled() {
  els.processBtn.disabled = !(els.vocalsFile.files[0] && els.mixFile.files[0] && els.scaleInput.value.trim());
}

function bindFileSlot(input, filenameEl) {
  input.addEventListener("change", () => {
    const file = input.files[0];
    filenameEl.textContent = file ? file.name : "click to choose…";
    filenameEl.classList.toggle("placeholder", !file);
    updateProcessEnabled();
  });
}

bindFileSlot(els.vocalsFile, els.vocalsFilename);
bindFileSlot(els.mixFile, els.mixFilename);
els.scaleInput.addEventListener("input", updateProcessEnabled);

els.pullStrength.addEventListener("input", () => {
  els.pullStrength.style.setProperty("--fill", `${els.pullStrength.value}%`);
  els.pullStrengthValue.textContent = `${els.pullStrength.value}%`;
  clearTimeout(state.pullDebounceTimer);
  setStatus("recompute pending…", "busy");
  state.pullDebounceTimer = setTimeout(runCorrect, PULL_STRENGTH_DEBOUNCE_MS);
});

els.preserveOriginal.addEventListener("input", () => {
  els.preserveOriginal.style.setProperty("--fill", `${els.preserveOriginal.value}%`);
  els.preserveOriginalValue.textContent = `${els.preserveOriginal.value}%`;
  applyBlendGains();
});

els.processBtn.addEventListener("click", runAnalyze);
els.playPauseBtn.addEventListener("click", togglePlayback);
els.jumpOriginalBtn.addEventListener("click", () => setBlendSlider(100));
els.jumpCorrectedBtn.addEventListener("click", () => setBlendSlider(0));
els.downloadBtn.addEventListener("click", downloadCorrected);

els.viewOriginalBtn.addEventListener("click", () => setWaveformMode("original"));
els.viewCorrectedBtn.addEventListener("click", () => setWaveformMode("corrected"));
els.viewBothBtn.addEventListener("click", () => setWaveformMode("both"));

function setWaveformMode(mode) {
  state.waveformMode = mode;
  [els.viewOriginalBtn, els.viewCorrectedBtn, els.viewBothBtn].forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === mode);
  });
  renderWaveform();
}

function timeFromClientX(clientX) {
  const rect = els.waveformWrap.getBoundingClientRect();
  const ratio = rect.width ? Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)) : 0;
  return ratio * state.durationSeconds;
}

function seekTo(seconds) {
  state.playbackOffset = Math.max(0, Math.min(seconds, state.durationSeconds));
  updateTimeReadout();
  drawPlayhead();
}

function beginScrub(clientX) {
  if (!state.originalBuffer) return;
  state.isScrubbing = true;
  state.scrubWasPlaying = state.isPlaying;
  if (state.isPlaying) {
    state.playbackOffset = currentPlaybackTime();
    stopSources();
    state.isPlaying = false;
    stopTimeReadoutTicker();
  }
  seekTo(timeFromClientX(clientX));
}

function updateScrub(clientX) {
  if (!state.isScrubbing) return;
  seekTo(timeFromClientX(clientX));
}

function endScrub() {
  if (!state.isScrubbing) return;
  state.isScrubbing = false;
  if (state.scrubWasPlaying) {
    restartPlayback(state.playbackOffset);
  }
}

els.waveformWrap.addEventListener("mousedown", (e) => beginScrub(e.clientX));
window.addEventListener("mousemove", (e) => updateScrub(e.clientX));
window.addEventListener("mouseup", endScrub);

els.waveformWrap.addEventListener("touchstart", (e) => beginScrub(e.touches[0].clientX), { passive: true });
window.addEventListener("touchmove", (e) => {
  if (state.isScrubbing) updateScrub(e.touches[0].clientX);
}, { passive: true });
window.addEventListener("touchend", endScrub);

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    renderWaveform();
    drawPlayhead();
  }, 150);
});

function setBlendSlider(value) {
  els.preserveOriginal.value = String(value);
  els.preserveOriginal.style.setProperty("--fill", `${value}%`);
  els.preserveOriginalValue.textContent = `${value}%`;
  applyBlendGains();
}

function blendRatio() {
  return Number(els.preserveOriginal.value) / 100; // fraction of ORIGINAL retained
}

function applyBlendGains() {
  if (!state.gainOriginal || !state.gainCorrected) return;
  const ratio = blendRatio();
  const now = getAudioCtx().currentTime;
  state.gainOriginal.gain.setTargetAtTime(ratio, now, 0.01);
  state.gainCorrected.gain.setTargetAtTime(1 - ratio, now, 0.01);
}

async function decodeFileToBuffer(file) {
  const arrayBuffer = await file.arrayBuffer();
  return await getAudioCtx().decodeAudioData(arrayBuffer);
}

async function runAnalyze() {
  const vocalsFile = els.vocalsFile.files[0];
  const mixFile = els.mixFile.files[0];
  const scale = els.scaleInput.value.trim();
  if (!vocalsFile || !mixFile || !scale) return;

  els.processBtn.disabled = true;
  setStatus("decoding original vocals…", "busy");

  try {
    state.originalBuffer = await decodeFileToBuffer(vocalsFile);
    state.durationSeconds = state.originalBuffer.duration;
    els.waveformSection.hidden = false;
    renderWaveform();

    setStatus("uploading + analyzing (chords, pitch)…", "busy");
    const form = new FormData();
    form.append("vocals", vocalsFile);
    form.append("mix", mixFile);
    form.append("scale", scale);

    const res = await fetch(`${BACKEND_URL}/analyze`, { method: "POST", body: form });
    if (!res.ok) throw new Error(`/analyze failed: ${res.status} ${await res.text()}`);
    const data = await res.json();

    state.jobId = data.job_id;
    state.chordTimeline = data.chord_timeline;

    els.dialsSection.hidden = false;
    els.timelineSection.hidden = false;

    await runCorrect();

    setStatus(`analyzed — ${data.note_count} notes, ${data.chord_timeline.length} chord segments`, "ok");
  } catch (err) {
    console.error(err);
    setStatus(`error: ${err.message}`, "error");
  } finally {
    updateProcessEnabled();
  }
}

async function runCorrect() {
  if (!state.jobId) return;
  const pullStrength = Number(els.pullStrength.value) / 100;

  setStatus("recomputing…", "busy");
  try {
    const res = await fetch(`${BACKEND_URL}/correct`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: state.jobId, pull_strength: pullStrength }),
    });
    if (!res.ok) throw new Error(`/correct failed: ${res.status} ${await res.text()}`);

    const notesHeader = res.headers.get("X-Notes");
    state.notesSummary = notesHeader ? JSON.parse(notesHeader) : [];

    const arrayBuffer = await res.arrayBuffer();
    state.correctedBuffer = await getAudioCtx().decodeAudioData(arrayBuffer);

    renderStrip();
    renderWaveform();

    if (state.isPlaying) {
      restartPlayback(currentPlaybackTime());
    }

    setStatus("up to date", "ok");
  } catch (err) {
    console.error(err);
    setStatus(`error: ${err.message}`, "error");
  }
}

function currentPlaybackTime() {
  if (!state.isPlaying) return state.playbackOffset;
  return state.playbackOffset + (getAudioCtx().currentTime - state.playbackStartedAt);
}

function stopSources() {
  [state.sourceOriginal, state.sourceCorrected].forEach((src) => {
    if (src) {
      try { src.stop(); } catch (_) { /* already stopped */ }
    }
  });
  state.sourceOriginal = null;
  state.sourceCorrected = null;
}

function restartPlayback(offsetSeconds) {
  if (!state.originalBuffer) return;
  stopSources();

  const ctx = getAudioCtx();
  const duration = state.originalBuffer.duration;
  const offset = Math.max(0, Math.min(offsetSeconds, duration - 0.001));

  state.gainOriginal = ctx.createGain();
  state.gainCorrected = ctx.createGain();
  state.gainOriginal.connect(ctx.destination);
  state.gainCorrected.connect(ctx.destination);
  applyBlendGains();

  state.sourceOriginal = ctx.createBufferSource();
  state.sourceOriginal.buffer = state.originalBuffer;
  state.sourceOriginal.connect(state.gainOriginal);

  state.sourceCorrected = ctx.createBufferSource();
  state.sourceCorrected.buffer = state.correctedBuffer || state.originalBuffer;
  state.sourceCorrected.connect(state.gainCorrected);

  const onEnded = () => {
    state.isPlaying = false;
    state.playbackOffset = 0;
    els.playPauseBtn.textContent = "▶ play";
    els.playPauseBtn.classList.remove("playing");
    stopTimeReadoutTicker();
    updateTimeReadout();
    drawPlayhead();
  };
  state.sourceOriginal.onended = onEnded;

  state.sourceOriginal.start(0, offset);
  state.sourceCorrected.start(0, offset);

  state.playbackStartedAt = ctx.currentTime;
  state.playbackOffset = offset;
  state.isPlaying = true;
  els.playPauseBtn.textContent = "⏸ pause";
  els.playPauseBtn.classList.add("playing");
  startTimeReadoutTicker();
}

function togglePlayback() {
  if (!state.originalBuffer) return;
  const ctx = getAudioCtx();
  if (ctx.state === "suspended") ctx.resume();

  if (state.isPlaying) {
    state.playbackOffset = currentPlaybackTime();
    stopSources();
    state.isPlaying = false;
    els.playPauseBtn.textContent = "▶ play";
    els.playPauseBtn.classList.remove("playing");
    stopTimeReadoutTicker();
    updateTimeReadout();
    drawPlayhead();
  } else {
    restartPlayback(state.playbackOffset);
  }
}

function updateTimeReadout() {
  els.timeReadout.textContent = `${formatTime(currentPlaybackTime())} / ${formatTime(state.durationSeconds)}`;
}

function playheadTick() {
  updateTimeReadout();
  drawPlayhead();
  state.rafId = requestAnimationFrame(playheadTick);
}

function startTimeReadoutTicker() {
  stopTimeReadoutTicker();
  playheadTick();
}

function stopTimeReadoutTicker() {
  if (state.rafId) {
    cancelAnimationFrame(state.rafId);
    state.rafId = null;
  }
}

// --- Waveform: static original/corrected traces plus a live playhead,
// drawn on two stacked canvases so the playhead can redraw every frame
// without re-computing the (relatively expensive) min/max downsample. ---

function sizeCanvasToWrap(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = els.waveformWrap.clientWidth;
  const cssHeight = els.waveformWrap.clientHeight;
  canvas.width = Math.max(1, Math.round(cssWidth * dpr));
  canvas.height = Math.max(1, Math.round(cssHeight * dpr));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, cssWidth, cssHeight };
}

function drawWaveformLayer(ctx, buffer, width, height, color, alpha) {
  if (!buffer) return;
  const data = buffer.getChannelData(0);
  const step = Math.max(1, Math.ceil(data.length / width));
  const amp = height / 2;

  ctx.globalAlpha = alpha;
  ctx.fillStyle = color;
  for (let x = 0; x < width; x++) {
    let min = 1.0;
    let max = -1.0;
    const start = x * step;
    const end = Math.min(start + step, data.length);
    for (let i = start; i < end; i++) {
      const v = data[i];
      if (v < min) min = v;
      if (v > max) max = v;
    }
    if (min > max) {
      min = 0;
      max = 0;
    }
    const y1 = amp - max * amp;
    const y2 = amp - min * amp;
    ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
  }
  ctx.globalAlpha = 1;
}

function renderWaveform() {
  if (!els.waveformWrap.clientWidth) return;
  const { ctx, cssWidth, cssHeight } = sizeCanvasToWrap(els.waveformCanvas);
  ctx.clearRect(0, 0, cssWidth, cssHeight);

  const rootStyles = getComputedStyle(document.documentElement);
  const mutedColor = rootStyles.getPropertyValue("--muted").trim() || "#8b8d98";
  const accentColor = rootStyles.getPropertyValue("--accent").trim() || "#e0a039";

  const mode = state.waveformMode;
  if (mode === "original" || mode === "both") {
    drawWaveformLayer(ctx, state.originalBuffer, cssWidth, cssHeight, mutedColor, mode === "both" ? 0.55 : 0.9);
  }
  if (mode === "corrected" || mode === "both") {
    drawWaveformLayer(ctx, state.correctedBuffer, cssWidth, cssHeight, accentColor, mode === "both" ? 0.6 : 0.9);
  }

  drawPlayhead();
}

function drawPlayhead() {
  if (!els.waveformWrap.clientWidth) return;
  const { ctx, cssWidth, cssHeight } = sizeCanvasToWrap(els.playheadCanvas);
  ctx.clearRect(0, 0, cssWidth, cssHeight);
  if (!state.durationSeconds) return;

  const x = (currentPlaybackTime() / state.durationSeconds) * cssWidth;
  const accentColor = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#e0a039";
  ctx.strokeStyle = accentColor;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x, 0);
  ctx.lineTo(x, cssHeight);
  ctx.stroke();
}

function renderStrip() {
  const duration = state.durationSeconds;
  els.strip.innerHTML = "";
  if (!duration) return;

  if (state.chordTimeline.length === 0 && state.notesSummary.length === 0) {
    const empty = document.createElement("div");
    empty.className = "strip-empty";
    empty.textContent = "no chord/note data";
    els.strip.appendChild(empty);
    return;
  }

  const chordRow = document.createElement("div");
  chordRow.className = "strip-row";
  const labelColor = {};
  let nextColor = 0;
  for (const seg of state.chordTimeline) {
    if (!(seg.label in labelColor)) {
      labelColor[seg.label] = CHORD_PALETTE[nextColor % CHORD_PALETTE.length];
      nextColor += 1;
    }
    const el = document.createElement("div");
    el.className = "chord-seg";
    el.style.width = `${((seg.end - seg.start) / duration) * 100}%`;
    el.style.background = labelColor[seg.label];
    el.textContent = seg.label;
    chordRow.appendChild(el);
  }
  els.strip.appendChild(chordRow);

  const centsRow = document.createElement("div");
  centsRow.className = "strip-row cents-row";
  const maxCents = 150;
  for (const note of state.notesSummary) {
    const wrap = document.createElement("div");
    wrap.className = "cents-bar-wrap";
    wrap.style.width = `${((note.end - note.start) / duration) * 100}%`;
    wrap.style.alignItems = note.delta_cents >= 0 ? "flex-end" : "flex-start";
    const bar = document.createElement("div");
    bar.className = "cents-bar";
    bar.style.height = `${Math.min(17, (Math.abs(note.delta_cents) / maxCents) * 17)}px`;
    bar.style.background = note.delta_cents >= 0 ? "var(--good)" : "var(--warn)";
    wrap.appendChild(bar);
    centsRow.appendChild(wrap);
  }
  els.strip.appendChild(centsRow);
}

// --- Download: bake the current pull-strength + blend into a single WAV,
// entirely client-side (no extra backend call -- blend never hits the
// server, per §5/§7). ---

async function downloadCorrected() {
  if (!state.originalBuffer || !state.correctedBuffer) {
    setStatus("process a take first", "error");
    return;
  }

  const ratio = blendRatio();
  const length = state.originalBuffer.length;
  const offlineCtx = new OfflineAudioContext(
    state.originalBuffer.numberOfChannels,
    length,
    state.originalBuffer.sampleRate
  );

  const srcOriginal = offlineCtx.createBufferSource();
  srcOriginal.buffer = state.originalBuffer;
  const gainOriginal = offlineCtx.createGain();
  gainOriginal.gain.value = ratio;
  srcOriginal.connect(gainOriginal).connect(offlineCtx.destination);

  const srcCorrected = offlineCtx.createBufferSource();
  srcCorrected.buffer = state.correctedBuffer;
  const gainCorrected = offlineCtx.createGain();
  gainCorrected.gain.value = 1 - ratio;
  srcCorrected.connect(gainCorrected).connect(offlineCtx.destination);

  srcOriginal.start(0);
  srcCorrected.start(0);

  const rendered = await offlineCtx.startRendering();
  const wavBlob = encodeWav(rendered);

  const url = URL.createObjectURL(wavBlob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "corrected_vocal.wav";
  a.click();
  URL.revokeObjectURL(url);
}

function encodeWav(audioBuffer) {
  const numChannels = audioBuffer.numberOfChannels;
  const sampleRate = audioBuffer.sampleRate;
  const numFrames = audioBuffer.length;
  const bytesPerSample = 2; // 16-bit PCM output
  const blockAlign = numChannels * bytesPerSample;
  const dataSize = numFrames * blockAlign;

  // Peak-safe scale-down (never boost) instead of a hard per-sample clamp --
  // clamping crushes only the loudest samples and sounds like crackly
  // clipping; scaling the whole buffer down preserves the waveform shape.
  let peak = 0;
  for (let ch = 0; ch < numChannels; ch++) {
    const data = audioBuffer.getChannelData(ch);
    for (let i = 0; i < numFrames; i++) {
      const abs = Math.abs(data[i]);
      if (abs > peak) peak = abs;
    }
  }
  const ceiling = 10 ** (-0.5 / 20); // -0.5 dBFS headroom, matches backend
  const scale = peak > ceiling ? ceiling / peak : 1;

  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  const writeString = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };

  writeString(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bytesPerSample * 8, true);
  writeString(36, "data");
  view.setUint32(40, dataSize, true);

  const channelData = [];
  for (let ch = 0; ch < numChannels; ch++) channelData.push(audioBuffer.getChannelData(ch));

  let offset = 44;
  for (let i = 0; i < numFrames; i++) {
    for (let ch = 0; ch < numChannels; ch++) {
      // Still clamp as a last-resort safety net -- should be a no-op now
      // that `scale` already keeps everything under the ceiling.
      const sample = Math.max(-1, Math.min(1, channelData[ch][i] * scale));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
  }

  return new Blob([buffer], { type: "audio/wav" });
}
