"""Chord/key timeline detection from the rough mix (§4.1).

Vocals bleed into the mix, so before computing chroma we:
  1. Run harmonic/percussive source separation (HPSS) and keep the harmonic part.
  2. Apply a tapered attenuation over the vocal formant range (~200 Hz-4 kHz) in
     the frequency domain, so the mix's chordal/bass content dominates the
     chroma vector rather than the more melodically-moving vocal line.
  3. Additionally compute a bass-weighted chroma from a low-passed copy and mix
     it in, since bass notes define chord roots and move less than the melody.

This does not need to be perfect (see spec §8) — the scale fallback and
partial pull-strength in later stages absorb occasional chord misreads.
"""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import butter, sosfiltfilt

from music_theory import CHORD_QUALITIES, NOTE_NAMES

HOP_SECONDS = 0.15  # within the 100-200ms range suggested by the spec
VOCAL_DEEMPHASIS_LOW_HZ = 200.0
VOCAL_DEEMPHASIS_HIGH_HZ = 4000.0
VOCAL_DEEMPHASIS_ATTEN_DB = -12.0
BASS_LOWPASS_HZ = 250.0
BASS_CHROMA_WEIGHT = 0.6
CHORD_MEDIAN_FILTER_FRAMES = 6


@dataclass(frozen=True)
class ChordSegment:
    start: float
    end: float
    root_pc: int
    quality: str

    @property
    def label(self) -> str:
        return f"{NOTE_NAMES[self.root_pc]}{'' if self.quality == 'maj' else self.quality}"

    def tone_pitch_classes(self) -> list:
        offsets = CHORD_QUALITIES[self.quality]
        return sorted((self.root_pc + o) % 12 for o in offsets)


def _tapered_band_attenuation(y: np.ndarray, sr: int) -> np.ndarray:
    """Smoothly attenuate VOCAL_DEEMPHASIS_LOW_HZ-HIGH_HZ via an STFT magnitude
    mask with raised-cosine tapers at the band edges (no hard cutoff, per §4.1).
    """
    n_fft = 4096
    hop = n_fft // 4
    stft = librosa.stft(y, n_fft=n_fft, hop_length=hop)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

    taper_width_hz = 150.0
    gain_db = np.zeros_like(freqs)
    in_band = (freqs >= VOCAL_DEEMPHASIS_LOW_HZ) & (freqs <= VOCAL_DEEMPHASIS_HIGH_HZ)
    gain_db[in_band] = VOCAL_DEEMPHASIS_ATTEN_DB

    low_taper = (freqs >= VOCAL_DEEMPHASIS_LOW_HZ - taper_width_hz) & (freqs < VOCAL_DEEMPHASIS_LOW_HZ)
    frac = (freqs[low_taper] - (VOCAL_DEEMPHASIS_LOW_HZ - taper_width_hz)) / taper_width_hz
    gain_db[low_taper] = VOCAL_DEEMPHASIS_ATTEN_DB * (0.5 - 0.5 * np.cos(np.pi * frac))

    high_taper = (freqs > VOCAL_DEEMPHASIS_HIGH_HZ) & (freqs <= VOCAL_DEEMPHASIS_HIGH_HZ + taper_width_hz)
    frac = (freqs[high_taper] - VOCAL_DEEMPHASIS_HIGH_HZ) / taper_width_hz
    gain_db[high_taper] = VOCAL_DEEMPHASIS_ATTEN_DB * (0.5 + 0.5 * np.cos(np.pi * frac))

    gain_lin = 10.0 ** (gain_db / 20.0)
    stft *= gain_lin[:, None]
    return librosa.istft(stft, hop_length=hop, length=len(y))


def _bass_lowpass(y: np.ndarray, sr: int) -> np.ndarray:
    sos = butter(4, BASS_LOWPASS_HZ, btype="low", fs=sr, output="sos")
    return sosfiltfilt(sos, y)


def _chord_templates() -> dict:
    """12 roots x {maj, min} -> normalized 12-bin chroma template."""
    templates = {}
    for root_pc in range(12):
        for quality in ("maj", "min"):
            offsets = CHORD_QUALITIES[quality]
            vec = np.zeros(12)
            for o in offsets:
                vec[(root_pc + o) % 12] = 1.0
            templates[(root_pc, quality)] = vec / np.linalg.norm(vec)
    return templates


def detect_chord_timeline(mix_audio: np.ndarray, sr: int) -> list:
    """Returns a list of ChordSegment covering [0, duration), sorted by start."""
    y_harm, _ = librosa.effects.hpss(mix_audio)
    y_deemph = _tapered_band_attenuation(y_harm, sr)
    y_bass = _bass_lowpass(y_harm, sr)

    hop_length = int(round(HOP_SECONDS * sr))

    chroma_main = librosa.feature.chroma_cqt(y=y_deemph, sr=sr, hop_length=hop_length)
    chroma_bass = librosa.feature.chroma_cqt(y=y_bass, sr=sr, hop_length=hop_length, fmin=librosa.note_to_hz("C1"))

    n_frames = min(chroma_main.shape[1], chroma_bass.shape[1])
    chroma_main = chroma_main[:, :n_frames]
    chroma_bass = chroma_bass[:, :n_frames]

    chroma = chroma_main + BASS_CHROMA_WEIGHT * chroma_bass
    norms = np.linalg.norm(chroma, axis=0, keepdims=True)
    norms[norms == 0] = 1.0
    chroma = chroma / norms

    templates = _chord_templates()
    keys = list(templates.keys())
    template_matrix = np.stack([templates[k] for k in keys], axis=1)  # (12, n_templates)

    similarity = template_matrix.T @ chroma  # (n_templates, n_frames)
    best_idx = np.argmax(similarity, axis=0)
    best_idx = median_filter(best_idx, size=CHORD_MEDIAN_FILTER_FRAMES, mode="nearest")

    frame_times = librosa.frames_to_time(np.arange(n_frames), sr=sr, hop_length=hop_length)
    duration = len(mix_audio) / sr

    segments = []
    seg_start_idx = 0
    for i in range(1, n_frames + 1):
        if i == n_frames or best_idx[i] != best_idx[seg_start_idx]:
            root_pc, quality = keys[best_idx[seg_start_idx]]
            start = float(frame_times[seg_start_idx])
            end = float(frame_times[i]) if i < n_frames else duration
            segments.append(ChordSegment(start=start, end=end, root_pc=int(root_pc), quality=quality))
            seg_start_idx = i

    return segments


def chord_at_time(timeline: list, t: float) -> ChordSegment | None:
    for seg in timeline:
        if seg.start <= t < seg.end:
            return seg
    return timeline[-1] if timeline else None
