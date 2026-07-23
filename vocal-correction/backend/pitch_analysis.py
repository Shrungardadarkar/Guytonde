"""F0 extraction, octave-jump smoothing, note segmentation, and vibrato
(slow/fast component) separation on the isolated vocal track (§4.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import librosa
import numpy as np
from scipy.ndimage import median_filter

from music_theory import hz_to_cents

FRAME_LENGTH = 2048   # ~46ms @ 44.1kHz
HOP_LENGTH = 512      # ~11.6ms @ 44.1kHz
FMIN_HZ = 65.0         # ~C2
FMAX_HZ = 1050.0       # ~C6
VOICED_PROB_THRESHOLD = 0.5

REF_HZ = 16.3516  # C0, arbitrary fixed reference so cents values are comparable across notes

NOTE_SPLIT_JUMP_CENTS = 150.0
NOTE_SPLIT_SUSTAIN_FRAMES = 2  # jump must persist this many frames to count as a new note, not a blip
MIN_NOTE_FRAMES = 4  # shorter voiced blips are discarded as noise, not notes
SEGMENTATION_SMOOTH_SECONDS = 0.26  # >= a full vibrato period even at a slow 4Hz (250ms), so segmentation ignores vibrato swings

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


def _smooth_octave_jumps(f0_hz: np.ndarray, voiced_prob: np.ndarray) -> np.ndarray:
    """pYIN's classic failure mode is snapping to a harmonic (usually an
    octave up/down). Where a voiced frame sits far from its local median in
    cents but an octave-shifted version would sit close, assume it's an
    octave error and correct it (§8 risk item).
    """
    f0 = f0_hz.copy()
    voiced_idx = np.where(~np.isnan(f0))[0]
    if len(voiced_idx) < 5:
        return f0

    cents = np.full_like(f0, np.nan)
    cents[voiced_idx] = np.array([hz_to_cents(REF_HZ, v) for v in f0[voiced_idx]])

    window = 7
    for i in voiced_idx:
        lo, hi = max(0, i - window), min(len(f0), i + window + 1)
        local = cents[lo:hi]
        local = local[~np.isnan(local)]
        if len(local) < 3:
            continue
        local_median = np.median(local)
        diff = cents[i] - local_median
        if abs(diff) > 600.0:  # more than half an octave off the local trend
            for shift in (-1200.0, 1200.0):
                if abs(diff + shift) < 100.0:
                    cents[i] += shift
                    f0[i] = REF_HZ * (2.0 ** (cents[i] / 1200.0))
                    break
    return f0


def extract_f0(vocals_audio: np.ndarray, sr: int) -> PitchAnalysisResult:
    f0_hz, voiced_flag, voiced_prob = librosa.pyin(
        vocals_audio,
        fmin=FMIN_HZ,
        fmax=FMAX_HZ,
        sr=sr,
        frame_length=FRAME_LENGTH,
        hop_length=HOP_LENGTH,
        fill_na=np.nan,
    )
    voiced = voiced_flag & (voiced_prob >= VOICED_PROB_THRESHOLD) & ~np.isnan(f0_hz)
    f0_hz = np.where(voiced, f0_hz, np.nan)
    f0_hz = _smooth_octave_jumps(f0_hz, voiced_prob)

    frame_times = librosa.frames_to_time(np.arange(len(f0_hz)), sr=sr, hop_length=HOP_LENGTH)

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
            if abs(cents_full[j] - cents_full[j - 1]) > NOTE_SPLIT_JUMP_CENTS:
                sustained = all(
                    k < n and voiced[k] and abs(cents_full[k] - cents_full[j - 1]) > NOTE_SPLIT_JUMP_CENTS / 2
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
