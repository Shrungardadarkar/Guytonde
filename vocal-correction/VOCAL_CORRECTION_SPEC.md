# Vocal Tuning Correction Tool — Build Specification

**Status:** Design locked. Ready to implement.
**Author context:** Personal-use tool for one singer (untrained, non-classical), correcting wavering/drift in their own recorded vocal takes. Not a DAW, not auto-tune, not a mixing tool.

---

## 1. Problem Statement

The user records vocals over their own instrumental tracks. They are not classically trained, so during sustained notes their pitch **wavers/drifts** around the note they meant to sing — sometimes flat, sometimes sharp, always by a small margin. They do **not** hit outright wrong notes (if that happens, they re-record — this tool never needs to invent a melody).

**Goal:** Given a take, gently pull each sustained note's *center pitch* back toward the harmonically correct target, while:
- Preserving vibrato (the fast oscillation within a note) — do not flatten it.
- Preserving timbre/formants — must not sound pitch-shifted, chipmunked, or robotic.
- Preserving timing, consonants, breaths, dynamics — untouched.
- Keeping 80–90% of the original signal in the final output by default (user-adjustable).
- Never inventing melody — this is a *tuning stabilizer*, not a note-fixer.

**Explicitly not building:**
- Auto-tune-style hard quantization to a chromatic grid.
- Wrong-note correction / melody generation.
- Multi-take learning or "voice profile" persistence (descoped).
- Leveling, compression, EQ, breath/de-ess cleanup, reverb, or any other mixing feature. This tool does **one job**: tuning correction.

---

## 2. Inputs

The user provides, per song, three things:

1. **Isolated vocals** — an audio file (WAV preferred) containing only their voice, dry, from a specific take. This is the file that gets corrected.
2. **Rough mix** — an audio file of the same take with vocals + instrumental tracks blended together, time-aligned 1:1 with the isolated vocals (same recording session, same timeline — no time-stretching or alignment needed between these two files). Used **only** to detect chord/harmony content over time. Never itself modified or output.
3. **Parent scale** — a simple text description of the song's key/scale, e.g. `"G major"` or `"E minor pentatonic"`. Used as the fallback tone set for notes that fall outside the currently-sounding chord (passing tones, suspensions, etc.), so the tool doesn't miscorrect deliberate melodic movement.

No chord timestamps, no BPM, no lyric charts are required — chords are detected automatically from the mix audio.

---

## 3. Output

A single corrected vocal audio file (same sample rate / bit depth as the input vocals file), representing a blend of:
- The original dry vocals (weighted by the **blend dial**, default 80–90%).
- The pitch-corrected resynthesis (weighted by the remainder).

No other processing is applied. No mix/master, no effects, no loudness normalization beyond preventing clipping on output.

---

## 4. Core Algorithm Pipeline

```
┌─────────────────┐     ┌──────────────────┐
│  Rough mix       │     │  Isolated vocals │
│  (vox+instr)     │     │  (dry)           │
└────────┬─────────┘     └─────────┬────────┘
         │                          │
         ▼                          ▼
┌─────────────────────┐   ┌───────────────────────┐
│ 1. Chord/key         │   │ 2. Pitch analysis      │
│    detection          │   │    (F0 extraction,     │
│    (chroma + vocal    │   │    note segmentation,  │
│    de-emphasis)        │   │    vibrato separation) │
└────────┬─────────────┘   └───────────┬───────────┘
         │  chord-per-frame timeline    │  per-note pitch curves
         └──────────────┬───────────────┘
                         ▼
              ┌─────────────────────────┐
              │ 3. Target computation    │
              │  nearest chord tone,     │
              │  else nearest scale tone │
              └────────────┬────────────┘
                            ▼
              ┌─────────────────────────┐
              │ 4. Correction curve      │
              │  pull toward target by   │
              │  pull-strength %,        │
              │  vibrato re-added,       │
              │  onsets/consonants       │
              │  left untouched          │
              └────────────┬────────────┘
                            ▼
              ┌─────────────────────────┐
              │ 5. WORLD vocoder         │
              │  resynthesis             │
              │  (F0 changed only;       │
              │  spectral envelope +     │
              │  aperiodicity untouched) │
              └────────────┬────────────┘
                            ▼
              ┌─────────────────────────┐
              │ 6. Blend with dry        │
              │  original (blend dial)   │
              └────────────┬────────────┘
                            ▼
                   Corrected vocal output
```

### 4.1 Chord/key detection (from the mix)

- Compute a chromagram of the mix audio (12-bin pitch class energy over time), using a hop size around 100–200ms — chords don't change faster than that in normal music.
- **Vocal de-emphasis before chroma:** apply a broad attenuation in the vocal formant range (roughly 200 Hz – 4 kHz, tapered, not a hard cut) before computing chroma, OR compute chroma from a harmonic-percussive-source-separated / low-passed version of the mix that favors sustained chordal/bass content over the more melodically-moving vocal line. Practical approach: use `librosa.effects.hpss` to get the harmonic component, and additionally weight lower octaves more heavily (bass notes define chord roots and move less than vocal melody).
- Match each chroma frame against major/minor triad templates (and optionally 7th-chord templates) via cosine similarity or simple template correlation. Take the best-matching chord label per frame.
- Smooth the chord sequence over time (median filter over ~4–8 frames) to avoid chord flicker from transient noise.
- Output: a **chord timeline** — list of (start_time, end_time, chord_label) segments covering the whole song.

This does not need to be perfect. Small chord misreads are absorbed later by the scale fallback and by the partial pull-strength — an occasional wrong chord for one note will nudge that note slightly, not derail it.

### 4.2 Pitch analysis (on isolated vocals)

- Extract F0 using `librosa.pyin` (probabilistic YIN) — returns F0 per frame, a voiced/unvoiced flag, and voiced-probability confidence per frame. Use a frame length appropriate for vocal fundamentals (roughly 46ms window / ~11ms hop at 44.1kHz is a reasonable start).
- **Note segmentation:** group contiguous voiced frames into note segments. Split segments at:
  - Unvoiced gaps (breaths, consonants, silence).
  - Sudden large F0 jumps that indicate a new note (e.g., > 150 cents change within 1–2 frames, sustained rather than transient).
- For each note segment, separate the pitch curve into two components:
  - **Slow component** — the note's underlying center pitch trajectory. Get this via a strong smoothing pass (e.g. median filter with a window on the order of 150–250ms, wide enough to average out vibrato which is typically 4–8 Hz).
  - **Fast component (vibrato/residual)** — the difference between the raw F0 and the slow component. This is preserved unchanged.
- Also identify the note's **onset region** (first ~30–60ms) and treat it more gently or leave it uncorrected — singers often scoop or slide into a note deliberately, and hard-correcting the attack sounds artificial.

### 4.3 Target computation

For each note segment:
- Take the note's slow-component center pitch (e.g., median or mode of the slow component across the sustained middle of the note, excluding onset/release).
- Convert to a pitch-class (mod 12 in semitone space).
- Look up the chord active at that note's time range from the chord timeline (§4.1).
- Compute distance (in semitones/cents) from the note's pitch class to every tone in that chord. If the nearest chord tone is within a reasonable margin (e.g. ≤ 150 cents), use it as the target.
- **Else**, fall back to the parent scale (§2 input): find the nearest tone in the full scale associated with the given key. This handles passing tones and suspensions that are correctly outside the current chord.
- The target's **octave** should be chosen to be the closest octave to the sung note (i.e., don't jump the note to a different octave — just correct pitch class in the nearest octave to where the singer actually sang).
- Store, per note, the delta in cents between sung center pitch and computed target.

### 4.4 Correction curve construction

For each note segment:
- Compute the corrected slow-component trajectory as:
  `corrected_slow = original_slow + (target_delta_cents * pull_strength)`
  where `pull_strength` is the user's dial (0.0–1.0, see §6). This is a **partial** pull — at 100% pull the note center lands exactly on target; at 50% it lands halfway there; at 0% no correction happens at all.
- Re-add the preserved fast component (vibrato) unchanged: `corrected_f0 = corrected_slow + fast_component`.
- Smooth the transition at note boundaries (a short cross-fade, e.g. 20–40ms) so corrected pitch curves don't click or jump between adjacent notes.
- Leave onset region (§4.2) uncorrected or only lightly corrected (e.g. correction fades in over the onset rather than snapping instantly) to preserve natural attack/scoop.
- Cap the maximum correction per note (e.g. clip total pull to ±150 cents) as a safety rail — if a note is more than a semitone-and-a-half off, something is likely wrong with detection (or the singer genuinely sang a different note on purpose, i.e. a "wrong note" per §1) and it should not be aggressively force-corrected.

Output of this stage: a full-length corrected F0 curve, same length/timing as the original, differing from the original only in the ways described above.

### 4.5 Resynthesis (WORLD vocoder)

Use `pyworld` (Python bindings for the WORLD vocoder) to preserve authenticity:
- Decompose the original vocal audio into three streams: F0, spectral envelope (formants/timbre), and aperiodicity (breathiness/noise component).
- Replace **only** the F0 stream with the corrected F0 curve from §4.4. Leave spectral envelope and aperiodicity **completely untouched**.
- Resynthesize audio from (corrected F0, original spectral envelope, original aperiodicity).
- This is what prevents the "chipmunk"/robotic artifact of naive pitch-shifting — because timbre (spectral envelope) never changes, only the fundamental frequency contour does.

### 4.6 Blending

- Time-align the resynthesized (fully-corrected) audio with the original dry vocal (they should already be the same length/timing since only F0 changed).
- Mix: `output = blend_ratio * original + (1 - blend_ratio) * corrected`, where `blend_ratio` defaults to 0.8–0.9 (user dial, see §6).
- Normalize final output only enough to prevent clipping (peak-limit to e.g. -0.5 dBFS headroom) — no loudness/compression processing.

---

## 5. Two independent dials — how they differ

This is important to get right; they are not the same control:

| Dial | What it controls | Where it acts | Live behavior |
|---|---|---|---|
| **Pull strength** (0–100%) | How far each note's center pitch is corrected toward its target (§4.4 formula) | Inside the DSP pipeline, before/during resynthesis | **Debounced recompute** — changing this triggers re-running the correction-curve + resynthesis steps (§4.4–4.5). Not instant; settles ~1–2 seconds after the user stops moving the slider. |
| **Blend / preserve original** (0–100%, default 80–90% original) | How much of the dry original vs. the fully-resynthesized corrected audio appears in the final mix (§4.6) | Simple linear crossfade of two already-computed audio buffers | **Instant** — implement as a client-side (or otherwise trivially cheap) crossfade between two pre-rendered buffers (original audio, corrected audio). No recomputation needed; this can update live while dragging. |

Implementation implication: the backend should expose an endpoint that runs the expensive pipeline (§4.1–4.5) once per pull-strength value and returns the fully-corrected buffer; the frontend then does the cheap original/corrected crossfade locally for the blend dial, without hitting the backend again. Debounce the pull-strength slider (e.g. 400–800ms after the user stops moving it) before firing the recompute request.

---

## 6. User-facing controls (UI)

Minimal, single-purpose interface. No tabs for auto-tune, no leveling/EQ tab — those are explicitly out of scope.

```
┌────────────────────────────────────────────┐
│  VOCAL TUNING CORRECTION                    │
├────────────────────────────────────────────┤
│  Upload isolated vocals (WAV)               │
│  Upload rough mix (WAV)                     │
│  Parent scale: [ text input, e.g. "G major"]│
│  [Process]                                  │
├────────────────────────────────────────────┤
│  Pull strength         ▬▬▬●▬▬▬  50%         │
│  (debounced recompute on change)            │
│                                              │
│  Preserve original     ▬▬▬▬▬●▬  85%         │
│  (instant crossfade)                        │
├────────────────────────────────────────────┤
│  [▶ Original]  [▶ Corrected]  [A/B]         │
├────────────────────────────────────────────┤
│  [Download corrected vocal]                 │
└────────────────────────────────────────────┘
```

Optional (nice-to-have, not required for v1): a simple visualization showing the detected chord timeline and, per note, how many cents of correction were applied — helps the user sanity-check whether a section was over/under corrected without needing to inspect logs.

---

## 7. Suggested tech stack

**Backend (does the heavy DSP):**
- Python
- `librosa` — audio loading, `pyin` pitch detection, chroma computation, HPSS
- `pyworld` — WORLD vocoder decomposition/resynthesis (pip package: `pyworld`; may need `numpy`, a C compiler available at install time, or a prebuilt wheel — verify at setup)
- `numpy`, `scipy` — array math, filtering (median filter, smoothing)
- `soundfile` — WAV I/O
- A lightweight web framework to expose endpoints, e.g. FastAPI

**Frontend (thin, single-purpose):**
- Simple upload UI (two file inputs + scale text field)
- Audio playback with Web Audio API `GainNode`s for the instant blend crossfade
- Debounced request trigger for the pull-strength slider
- Any lightweight framework is fine (React, or plain HTML/JS) — this does not need to be elaborate

**Suggested project layout:**
```
vocal-correction/
├── backend/
│   ├── main.py                 # FastAPI app, endpoints
│   ├── chord_detection.py      # §4.1
│   ├── pitch_analysis.py       # §4.2
│   ├── target_computation.py   # §4.3 (chord/scale tone logic)
│   ├── correction.py           # §4.4
│   ├── resynth.py              # §4.5 (pyworld wrapper)
│   ├── blend.py                # §4.6
│   ├── music_theory.py         # scale/chord tone tables, note name <-> Hz helpers
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── app.js                  # upload, slider handling, debounce, playback crossfade
│   └── styles.css
└── VOCAL_CORRECTION_SPEC.md     # this document
```

**Suggested API surface:**
- `POST /analyze` — accepts vocals file + mix file + scale string. Runs §4.1 and §4.2 once, caches results server-side (session/job id), returns job id + detected chord timeline (for optional UI display).
- `POST /correct` — accepts job id + pull_strength. Runs §4.3–§4.5 (reuses cached analysis from `/analyze`), returns the fully-corrected audio buffer (or a URL to fetch it). This is the debounced call.
- Blend dial never calls the backend — handled entirely client-side once both original and corrected buffers are available in the browser.

---

## 8. Key implementation risks / things to watch

- **`pyworld` installation** can be finicky depending on OS (needs a C++ compiler if no wheel is available). Verify install early, before building the rest of the pipeline around it.
- **Chord detection accuracy** off a rough mix with vocals bleeding into the harmonic content is inherently imperfect — this is expected and acceptable (see §4.1); don't over-invest trying to make it perfect. The scale fallback and partial pull-strength are the safety nets, not chord-detection perfection.
- **Note segmentation edge cases:** long sustained notes with a scoop at the start, or notes with a glide (portamento) into the next note, may get mis-segmented. Onset handling (§4.2, §4.4) mitigates but won't eliminate all artifacts — expect to iterate here with real recordings.
- **Octave errors in pitch detection** are the classic pYIN failure mode (jumping to a harmonic). Keep confidence-based filtering and consider a light post-hoc octave-jump smoother on the raw F0 before segmentation.
- **Latency of the debounced recompute:** the full pipeline (chroma + pitch analysis + WORLD resynthesis) should be profiled on a real ~3–4 minute vocal file early on to confirm the "1–2 seconds" target in §5 is realistic; WORLD resynthesis in particular can be a bottleneck depending on hop size settings.

---

## 9. Explicit non-goals (do not build these)

- Auto-tune / hard chromatic quantization mode.
- Wrong-note or melody correction/generation.
- Multi-take learning, persistent "voice profiles," or expression modeling across recordings.
- Any leveling, compression, EQ, de-essing, breath removal, reverb, or other mixing/mastering feature.
- Multi-track / DAW-style editing, timeline editing, or effect chains.
- Support for non-vocal instrument correction.

If a future version wants any of the above, treat it as a distinct project — keep this tool doing exactly one job well.

---

## 10. Summary of all locked decisions (quick reference)

| Decision point | Locked answer |
|---|---|
| What triggers correction | Wavering/drift only; wrong notes are re-recorded by the user, not fixed by the tool |
| Input files | Isolated dry vocals + rough mix (same take, same timeline) + parent scale string |
| Chord source | Auto-detected from the mix via chroma analysis; no manual chord chart or timestamps needed |
| Vocal bleed in mix | Handled via harmonic/percussive separation + low-frequency weighting before chroma, not by requiring an instrumental-only mix |
| Target pitch logic | Nearest chord tone; falls back to nearest parent-scale tone if no close chord tone |
| Octave handling | Correct pitch class only, snapped to nearest octave to the sung note — never jump octaves |
| Vibrato | Explicitly separated (slow vs. fast F0 component) and preserved unchanged |
| Onsets/attacks | Left uncorrected or only lightly/fading-in corrected |
| Formants/timbre | Preserved exactly via WORLD vocoder (only F0 stream is modified) |
| Correction amount | Partial pull toward target, scaled by pull-strength dial, capped at ±150 cents |
| Final authenticity control | Blend dial, default 80–90% original signal in final output |
| Live tweaking | Blend dial = instant client-side crossfade; pull-strength dial = debounced backend recompute (~1–2s) |
| Multi-take learning | Descoped — single-upload flow only |
| Output | Single corrected WAV, same sample rate/bit depth as input vocals, no other processing applied |

---

This document is intended to be handed directly to Claude Code as the full build context. All open design questions from the planning conversation have been resolved and are reflected above; implementation should not need to guess at scope or approach.
