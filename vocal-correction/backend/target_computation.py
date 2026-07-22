"""Per-note target pitch computation: nearest chord tone, falling back to the
parent scale (§4.3).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from chord_detection import chord_at_time
from music_theory import ParentScale, nearest_tone_cents
from pitch_analysis import NoteSegment

CHORD_TONE_MAX_CENTS = 150.0


@dataclass
class NoteTarget:
    center_cents: float       # sung center pitch, cents-from-REF_HZ (see pitch_analysis.REF_HZ)
    target_cents: float       # target pitch, same reference, nearest octave to center_cents
    delta_cents: float        # target_cents - center_cents (signed)
    source: str               # "chord" or "scale"
    chord_label: str | None


def _center_cents(note: NoteSegment) -> float:
    onset = note.onset_frames
    n = len(note.slow_cents)
    # Exclude onset and a symmetric release region from the center-pitch estimate;
    # if the note's too short for that, fall back to the full slow component.
    lo, hi = onset, n - onset
    if hi - lo < max(2, n // 3):
        lo, hi = 0, n
    return float(np.median(note.slow_cents[lo:hi]))


def _nearest_octave_target_cents(center_cents: float, target_pc: int) -> float:
    """Place target_pc in whichever octave lands closest to center_cents,
    never jumping the note to a different octave (§4.3 / summary table).
    """
    base_octave_cents = round(center_cents / 1200.0) * 1200.0
    candidates = [base_octave_cents + target_pc * 100.0 + k * 1200.0 for k in (-1, 0, 1)]
    return min(candidates, key=lambda c: abs(c - center_cents))


def compute_note_target(
    note: NoteSegment,
    chord_timeline: list,
    parent_scale: ParentScale,
) -> NoteTarget:
    center_cents = _center_cents(note)
    pitch_class = (center_cents / 100.0) % 12.0

    chord = chord_at_time(chord_timeline, (note.start_time + note.end_time) / 2.0)

    source = "scale"
    chord_label = None
    target_pc = None
    best_delta = None

    if chord is not None:
        chord_tones = chord.tone_pitch_classes()
        nearest_pc, delta = nearest_tone_cents(pitch_class, chord_tones)
        if abs(delta) <= CHORD_TONE_MAX_CENTS:
            target_pc, best_delta, source, chord_label = nearest_pc, delta, "chord", chord.label

    if target_pc is None:
        scale_tones = parent_scale.pitch_classes()
        target_pc, best_delta = nearest_tone_cents(pitch_class, scale_tones)
        source = "scale"

    target_cents = _nearest_octave_target_cents(center_cents, target_pc)
    delta_cents = target_cents - center_cents

    return NoteTarget(
        center_cents=center_cents,
        target_cents=target_cents,
        delta_cents=delta_cents,
        source=source,
        chord_label=chord_label,
    )
