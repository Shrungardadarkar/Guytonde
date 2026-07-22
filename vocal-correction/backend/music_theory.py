"""Pitch-class / scale / chord helpers shared by chord_detection, pitch_analysis,
target_computation, and correction.

Pitch classes are represented as floats in [0, 12) — 0 = C, continuous so that
detected (non-quantized) fundamental frequencies can be compared directly
against integer scale/chord tones without rounding first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

A4_HZ = 440.0
A4_MIDI = 69

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

_NOTE_TO_PC = {
    "C": 0, "B#": 0,
    "C#": 1, "DB": 1,
    "D": 2,
    "D#": 3, "EB": 3,
    "E": 4, "FB": 4,
    "F": 5, "E#": 5,
    "F#": 6, "GB": 6,
    "G": 7,
    "G#": 8, "AB": 8,
    "A": 9,
    "A#": 10, "BB": 10,
    "B": 11, "CB": 11,
}

SCALE_INTERVALS = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "ionian": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "natural minor": [0, 2, 3, 5, 7, 8, 10],
    "aeolian": [0, 2, 3, 5, 7, 8, 10],
    "harmonic minor": [0, 2, 3, 5, 7, 8, 11],
    "melodic minor": [0, 2, 3, 5, 7, 9, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "locrian": [0, 1, 3, 5, 6, 8, 10],
    "major pentatonic": [0, 2, 4, 7, 9],
    "minor pentatonic": [0, 3, 5, 7, 10],
    "blues": [0, 3, 5, 6, 7, 10],
    "chromatic": list(range(12)),
}

# Chord quality -> semitone offsets from root, used both for template-matching
# chroma vectors (§4.1) and for chord-tone target lookup (§4.3).
CHORD_QUALITIES = {
    "maj": [0, 4, 7],
    "min": [0, 3, 7],
    "dim": [0, 3, 6],
    "aug": [0, 4, 8],
    "maj7": [0, 4, 7, 11],
    "min7": [0, 3, 7, 10],
    "dom7": [0, 4, 7, 10],
}


def note_name_to_pitch_class(name: str) -> int:
    """'G' -> 7, 'Eb' -> 3, 'F#' -> 6 (case-insensitive)."""
    key = name.strip().upper()
    if key not in _NOTE_TO_PC:
        raise ValueError(f"Unrecognized note name: {name!r}")
    return _NOTE_TO_PC[key]


def hz_to_midi(hz: float) -> float:
    return A4_MIDI + 12.0 * __import__("math").log2(hz / A4_HZ)


def midi_to_hz(midi: float) -> float:
    return A4_HZ * 2.0 ** ((midi - A4_MIDI) / 12.0)


def hz_to_pitch_class(hz: float) -> float:
    """Continuous pitch class in [0, 12), e.g. 440 Hz -> 9.0 (A)."""
    midi = hz_to_midi(hz)
    return midi % 12.0


def hz_to_cents(hz_a: float, hz_b: float) -> float:
    """Cents from hz_a to hz_b (positive = b is sharper)."""
    import math
    return 1200.0 * math.log2(hz_b / hz_a)


def cents_to_ratio(cents: float) -> float:
    return 2.0 ** (cents / 1200.0)


@dataclass(frozen=True)
class ParentScale:
    root_pc: int
    intervals: tuple  # semitone offsets from root, ascending, within one octave
    name: str

    def pitch_classes(self) -> list:
        return sorted((self.root_pc + i) % 12 for i in self.intervals)


def parse_scale(scale_str: str) -> ParentScale:
    """Parse a free-text parent-scale description, e.g. 'G major',
    'E minor pentatonic', 'Bb dorian'. Falls back to 'major' if the
    quality text isn't recognized (still gives a usable diatonic fallback
    rather than failing the whole request).
    """
    text = scale_str.strip()
    match = re.match(r"^\s*([A-Ga-g][#b]?)\s*(.*)$", text)
    if not match:
        raise ValueError(f"Could not parse root note from scale string: {scale_str!r}")
    root_name, quality_raw = match.groups()
    root_pc = note_name_to_pitch_class(root_name)
    quality = quality_raw.strip().lower() or "major"
    intervals = SCALE_INTERVALS.get(quality, SCALE_INTERVALS["major"])
    return ParentScale(root_pc=root_pc, intervals=tuple(intervals), name=f"{root_name} {quality}")


def chord_tone_pitch_classes(root_pc: int, quality: str) -> list:
    offsets = CHORD_QUALITIES.get(quality, CHORD_QUALITIES["maj"])
    return sorted((root_pc + o) % 12 for o in offsets)


def nearest_tone_cents(pitch_class: float, tone_pcs: list) -> tuple:
    """Given a continuous pitch class and a set of integer target pitch
    classes, return (nearest_tone_pc, signed_distance_cents) using circular
    (mod-12-octave) distance, i.e. the smallest wrap-around distance.
    Positive distance means the target is sharper than the sung pitch class.
    """
    best_pc = None
    best_delta = None
    for pc in tone_pcs:
        # circular difference in semitones, in (-6, 6]
        diff = (pc - pitch_class + 6) % 12 - 6
        if best_delta is None or abs(diff) < abs(best_delta):
            best_delta = diff
            best_pc = pc
    return best_pc, best_delta * 100.0
