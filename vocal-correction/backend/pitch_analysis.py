"""F0 extraction, note segmentation, and vibrato (slow/fast component)
separation on the isolated vocal track (§4.2).

F0 extraction uses torchcrepe (a neural pitch tracker) rather than
librosa.pyin -- pyin's classical autocorrelation approach was visibly
unreliable on real vocals (frequent octave errors, low-confidence voicing
right on vibrato swings, which was the root cause of a note
over-segmentation bug). torchcrepe is far more robust on real singing and
needed no hand-rolled octave-jump correction to get clean results.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import torch
import torchcrepe
from scipy.ndimage import median_filter

from music_theory import hz_to_cents

HOP_LENGTH = 512      # ~11.6ms @ 44.1kHz
FMIN_HZ = 65.0         # ~C2
FMAX_HZ = 1050.0       # ~C6

CREPE_MODEL = "tiny"  # 'full' is ~2-2.3x realtime on CPU (6-9+ min for a real song, no GPU
                      # on a typical Mac) vs 'tiny' at ~0.17x realtime, with no measured
                      # accuracy loss on our test cases -- see commit history for benchmarks
CREPE_BATCH_SIZE = 2048
CREPE_PERIODICITY_MEDIAN_WIN = 3   # smooths confidence noise, per torchcrepe's own recommended usage
CREPE_PITCH_MEAN_WIN = 3           # smooths pitch quantization artifacts
CREPE_PERIODICITY_THRESHOLD = 0.21  # torchcrepe's recommended voiced/unvoiced cutoff
CREPE_SILENCE_DB = -60.0            # below this loudness, treat as silence regardless of periodicity

REF_HZ = 16.3516  # C0, arbitrary fixed reference so cents values are comparable across notes

NOTE_SPLIT_JUMP_CENTS = 150.0
NOTE_SPLIT_SUSTAIN_FRAMES = 2  # jump must persist this many frames to count as a new note, not a blip
MIN_NOTE_FRAMES = 4  # shorter voiced blips are discarded as noise, not notes
SEGMENTATION_SMOOTH_SECONDS = 0.26  # >= a full vibrato period even at a slow 4Hz (250ms), so segmentation ignores vibrato swings
JUMP_LOOKBACK_SECONDS = 0.09  # torchcrepe's own temporal smoothing spreads a genuine note change over ~60-70ms,
                              # so a single-adjacent-frame comparison (fine for pyin) misses it entirely

SLOW_COMPONENT_WINDOW_SECONDS = 0.2  # 150-250ms range from spec, preserves 4-8Hz vibrato as "fast"
ONSET_SECONDS = 0.045


@dataclass
class NoteSegment:
    start_frame: int
    end_frame: int  # exclusive
    start_time: float
    end_time: float
    raw_cents: np.ndarray       # per-frame F0 in cents-from-REF_HZ, this note's span
    slow_cents: np.ndarray      # smoothed center-pitch trajectory
    fast_cents: np.ndarray      # vibrato/residual, raw - slow
    onset_frames: int = 0       # frames at the start treated as onset (uncorrected/light)


@dataclass
class PitchAnalysisResult:
    sr: int
    hop_length: int
    frame_times: np.ndarray
    f0_hz: np.ndarray          # full-length, nan where unvoiced
    voiced: np.ndarray         # bool mask, full-length
    notes: list = field(default_factory=list)


def _crepe_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def extract_f0(vocals_audio: np.ndarray, sr: int) -> PitchAnalysisResult:
    audio = torch.tensor(vocals_audio, dtype=torch.float32).unsqueeze(0)
    device = _crepe_device()

    pitch, periodicity = torchcrepe.predict(
        audio,
        sr,
        HOP_LENGTH,
        fmin=FMIN_HZ,
        fmax=FMAX_HZ,
        model=CREPE_MODEL,
        batch_size=CREPE_BATCH_SIZE,
        device=device,
        return_periodicity=True,
    )

    periodicity = torchcrepe.filter.median(periodicity, CREPE_PERIODICITY_MEDIAN_WIN)
    periodicity = torchcrepe.threshold.Silence(CREPE_SILENCE_DB)(periodicity, audio, sr, HOP_LENGTH)
    pitch = torchcrepe.filter.mean(pitch, CREPE_PITCH_MEAN_WIN)

    voiced = (periodicity >= CREPE_PERIODICITY_THRESHOLD).squeeze(0).numpy()
    f0_hz = pitch.squeeze(0).to(torch.float64).numpy()
    f0_hz = np.where(voiced, f0_hz, np.nan)

    frame_times = np.arange(len(f0_hz)) * (HOP_LENGTH / sr)

    result = PitchAnalysisResult(
        sr=sr, hop_length=HOP_LENGTH, frame_times=frame_times, f0_hz=f0_hz, voiced=voiced,
    )
    result.notes = _segment_notes(result)
    return result


def _segment_notes(result: PitchAnalysisResult) -> list:
    voiced = result.voiced
    f0_hz = result.f0_hz
    hop_seconds = result.hop_length / result.sr

    cents_full = np.full_like(f0_hz, np.nan)
    valid = ~np.isnan(f0_hz)
    cents_full[valid] = np.array([hz_to_cents(REF_HZ, v) for v in f0_hz[valid]])

    # Vibrato (4-8Hz, i.e. a 125-250ms period) swings well past
    # NOTE_SPLIT_JUMP_CENTS within a fraction of a cycle, so a jump has to
    # stay away from the pre-jump baseline for a full vibrato period, not
    # just a couple of frames, to count as a genuine new note rather than an
    # ordinary oscillation. A real vibrato swing reverses and dips back
    # below half the threshold within that window; a real note change
    # doesn't. (A median-filtered "trend" signal was tried first as a
    # cleaner-looking fix, but median filters don't suppress continuous
    # oscillations -- they're built to reject one-off spikes -- and left
    # wide/fast vibrato essentially untouched even at very wide windows.)
    sustain_frames = max(NOTE_SPLIT_SUSTAIN_FRAMES, int(round(SEGMENTATION_SMOOTH_SECONDS / hop_seconds)))

    # torchcrepe's own temporal smoothing spreads a genuine note change over
    # several frames (a G3->B3 step measured ~60-70ms edge-to-edge in
    # testing), so comparing only adjacent frames -- which worked fine
    # against pyin's less-smoothed output -- missed real note changes
    # entirely. Comparing each frame against one a short lookback behind
    # catches the full step; the sustained-check above still rejects
    # vibrato, since a real oscillation reverses back within that window
    # while a genuine step doesn't.
    lookback_frames = max(1, int(round(JUMP_LOOKBACK_SECONDS / hop_seconds)))

    segments = []
    n = len(voiced)
    i = 0
    while i < n:
        if not voiced[i]:
            i += 1
            continue
        start = i
        j = i + 1
        while j < n and voiced[j]:
            lookback = max(start, j - lookback_frames)
            if lookback < j and abs(cents_full[j] - cents_full[lookback]) > NOTE_SPLIT_JUMP_CENTS:
                sustained = all(
                    k < n and voiced[k] and abs(cents_full[k] - cents_full[lookback]) > NOTE_SPLIT_JUMP_CENTS / 2
                    for k in range(j, min(j + sustain_frames, n))
                )
                if sustained:
                    break
            j += 1
        end = j
        if end - start >= MIN_NOTE_FRAMES:
            segments.append(_build_note_segment(result, cents_full, start, end, hop_seconds))
        i = end

    return segments


def _build_note_segment(result: PitchAnalysisResult, cents_full: np.ndarray, start: int, end: int, hop_seconds: float) -> NoteSegment:
    raw = cents_full[start:end]

    window_frames = max(3, int(round(SLOW_COMPONENT_WINDOW_SECONDS / hop_seconds)))
    if window_frames % 2 == 0:
        window_frames += 1
    window_frames = min(window_frames, len(raw) if len(raw) % 2 == 1 else len(raw) - 1)
    window_frames = max(window_frames, 1)

    slow = median_filter(raw, size=window_frames, mode="nearest")
    fast = raw - slow

    onset_frames = min(len(raw), max(1, int(round(ONSET_SECONDS / hop_seconds))))

    return NoteSegment(
        start_frame=start,
        end_frame=end,
        start_time=float(result.frame_times[start]),
        end_time=float(result.frame_times[end - 1]),
        raw_cents=raw,
        slow_cents=slow,
        fast_cents=fast,
        onset_frames=onset_frames,
    )
