"""WORLD vocoder resynthesis (§4.5): decompose the original vocal into
F0 / spectral envelope / aperiodicity, replace only the F0 stream, and
resynthesize -- so timbre never changes and only the pitch contour does.

The correction delta (cents) is computed on librosa's pyin frame grid
(pitch_analysis.py), but WORLD's cheaptrick/d4c analysis needs its own F0
track on its own frame grid. Rather than replacing WORLD's F0 outright
(which would require the two pitch trackers to agree exactly, frame for
frame), we interpolate the *delta* onto WORLD's grid and apply it relative
to WORLD's own F0. This keeps the "only F0 changes" property while being
robust to the two analyses landing on slightly different frames.
"""

from __future__ import annotations

import numpy as np
import pyworld as pw

from correction import CorrectionResult

FRAME_PERIOD_MS = 5.0


def resynthesize(vocals_audio: np.ndarray, sr: int, correction: CorrectionResult) -> np.ndarray:
    x = vocals_audio.astype(np.float64)

    f0_world, t_world = pw.dio(x, sr, frame_period=FRAME_PERIOD_MS)
    f0_world = pw.stonemask(x, f0_world, t_world, sr)
    sp = pw.cheaptrick(x, f0_world, t_world, sr)
    ap = pw.d4c(x, f0_world, t_world, sr)

    delta_interp = np.interp(t_world, correction.frame_times, correction.delta_cents, left=0.0, right=0.0)

    voiced = f0_world > 0.0
    corrected_f0 = f0_world.copy()
    corrected_f0[voiced] = f0_world[voiced] * (2.0 ** (delta_interp[voiced] / 1200.0))

    y = pw.synthesize(corrected_f0, sp, ap, sr, frame_period=FRAME_PERIOD_MS)
    return y.astype(np.float32)
