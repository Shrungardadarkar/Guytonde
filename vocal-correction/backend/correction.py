"""Correction curve construction (§4.4): scale each note's target delta by the
pull-strength dial, re-add vibrato unchanged, fade correction in over the
onset, cap the per-note correction, and smooth note-to-note boundaries.

Because the slow/fast split is additive (raw = slow + fast) and the
correction only ever adjusts the slow component, `corrected = raw + delta`
holds directly -- there's no need to separately re-add the fast/vibrato
component, it falls out of the algebra and is preserved bit-for-bit wherever
delta is smooth.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pitch_analysis import PitchAnalysisResult, REF_HZ
from target_computation import NoteTarget

MAX_PULL_CENTS = 150.0
BOUNDARY_CROSSFADE_SECONDS = 0.03  # 20-40ms range from spec


@dataclass
class CorrectionResult:
    corrected_hz: np.ndarray    # full-length, 0.0 where unvoiced, on the pitch-analysis frame grid
    delta_cents: np.ndarray     # full-length additive correction actually applied, same grid
    frame_times: np.ndarray


def _onset_gain(note_len: int, onset_frames: int) -> np.ndarray:
    gain = np.ones(note_len)
    n = min(onset_frames, note_len)
    if n > 0:
        ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / max(n - 1, 1))
        gain[:n] = ramp
    return gain


def build_corrected_f0(
    analysis: PitchAnalysisResult,
    targets: list,
    pull_strength: float,
) -> CorrectionResult:
    """Returns the full-length additive correction curve (delta_cents) plus a
    convenience corrected_hz curve on the pitch-analysis frame grid. The
    delta curve is what resynth.py actually applies (interpolated onto
    WORLD's own frame grid), since it composes cleanly with WORLD's own F0
    track regardless of grid alignment.
    """
    n = len(analysis.f0_hz)
    delta_full = np.zeros(n)

    for note, target in zip(analysis.notes, targets):
        capped_delta = float(np.clip(target.delta_cents, -MAX_PULL_CENTS, MAX_PULL_CENTS))
        applied = capped_delta * pull_strength
        gain = _onset_gain(len(note.raw_cents), note.onset_frames)
        delta_full[note.start_frame:note.end_frame] = applied * gain

    crossfade_frames = max(1, int(round(BOUNDARY_CROSSFADE_SECONDS / (analysis.hop_length / analysis.sr))))
    if crossfade_frames > 1:
        kernel = np.hanning(crossfade_frames * 2 + 1)
        kernel /= kernel.sum()
        delta_full = np.convolve(delta_full, kernel, mode="same")

    raw_cents = np.full(n, np.nan)
    valid = ~np.isnan(analysis.f0_hz)
    raw_cents[valid] = 1200.0 * np.log2(analysis.f0_hz[valid] / REF_HZ)

    corrected_cents = raw_cents + delta_full
    corrected_hz = np.zeros(n)
    corrected_hz[valid] = REF_HZ * (2.0 ** (corrected_cents[valid] / 1200.0))

    return CorrectionResult(corrected_hz=corrected_hz, delta_cents=delta_full, frame_times=analysis.frame_times)
