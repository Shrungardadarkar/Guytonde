"""Blend dial (§4.6): linear crossfade of dry original vs. fully-corrected
resynthesis, then peak-limit only enough to prevent clipping.

The live UI does this crossfade client-side for instant response (§5); this
module is used server-side only when baking the final downloadable file at
whatever blend_ratio was last selected.
"""

from __future__ import annotations

import numpy as np

PEAK_HEADROOM_DB = -0.5


def blend_and_limit(original: np.ndarray, corrected: np.ndarray, blend_ratio: float) -> np.ndarray:
    """blend_ratio is the fraction of ORIGINAL retained (spec §4.6 / §6:
    'Preserve original' dial, default 0.8-0.9)."""
    n = min(len(original), len(corrected))
    mixed = blend_ratio * original[:n] + (1.0 - blend_ratio) * corrected[:n]

    peak = np.max(np.abs(mixed))
    ceiling = 10.0 ** (PEAK_HEADROOM_DB / 20.0)
    if peak > ceiling:
        mixed = mixed * (ceiling / peak)

    return mixed.astype(np.float32)
