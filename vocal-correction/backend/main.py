"""FastAPI app exposing the vocal tuning correction pipeline (§7 API surface).

POST /analyze  -- runs chord detection (§4.1) + pitch analysis (§4.2) once,
                  caches results under a job_id, returns the chord timeline
                  for the optional visualization.
POST /correct  -- runs target computation + correction curve + WORLD
                  resynthesis (§4.3-4.5) for a given pull_strength, reusing
                  the cached analysis. This is the debounced call from the
                  frontend's pull-strength slider.

The blend/preserve-original dial never hits this backend -- it's an instant
client-side crossfade between the original upload and the last /correct
response (see frontend/app.js), per §5.
"""

from __future__ import annotations

import io
import json
import uuid

import librosa
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from chord_detection import detect_chord_timeline
from correction import build_corrected_f0
from music_theory import parse_scale
from pitch_analysis import extract_f0
from resynth import resynthesize
from target_computation import compute_note_target

app = FastAPI(title="Vocal Tuning Correction")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store -- this is a personal single-user local tool (spec §1),
# not a multi-tenant service, so a process-lifetime dict is sufficient.
_JOBS: dict = {}


def _load_mono_audio(file_bytes: bytes):
    data, sr = sf.read(io.BytesIO(file_bytes), dtype="float64", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data, sr


@app.post("/analyze")
async def analyze(
    vocals: UploadFile = File(...),
    mix: UploadFile = File(...),
    scale: str = Form(...),
):
    vocals_audio, vocals_sr = _load_mono_audio(await vocals.read())
    mix_audio, mix_sr = _load_mono_audio(await mix.read())

    if mix_sr != vocals_sr:
        mix_audio = librosa.resample(mix_audio, orig_sr=mix_sr, target_sr=vocals_sr)
        mix_sr = vocals_sr

    try:
        parent_scale = parse_scale(scale)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    chord_timeline = detect_chord_timeline(mix_audio, mix_sr)
    analysis = extract_f0(vocals_audio, vocals_sr)

    job_id = str(uuid.uuid4())
    _JOBS[job_id] = {
        "sr": vocals_sr,
        "vocals_audio": vocals_audio,
        "chord_timeline": chord_timeline,
        "analysis": analysis,
        "parent_scale": parent_scale,
    }

    return {
        "job_id": job_id,
        "sample_rate": vocals_sr,
        "duration_seconds": len(vocals_audio) / vocals_sr,
        "note_count": len(analysis.notes),
        "chord_timeline": [
            {"start": seg.start, "end": seg.end, "label": seg.label} for seg in chord_timeline
        ],
    }


class CorrectRequest(BaseModel):
    job_id: str
    pull_strength: float


@app.post("/correct")
async def correct(req: CorrectRequest):
    job = _JOBS.get(req.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job_id -- re-run /analyze")

    pull_strength = max(0.0, min(1.0, req.pull_strength))

    analysis = job["analysis"]
    targets = [
        compute_note_target(note, job["chord_timeline"], job["parent_scale"])
        for note in analysis.notes
    ]
    correction_result = build_corrected_f0(analysis, targets, pull_strength)
    corrected_audio = resynthesize(job["vocals_audio"], job["sr"], correction_result)

    buf = io.BytesIO()
    sf.write(buf, corrected_audio, job["sr"], format="WAV", subtype="FLOAT")
    buf.seek(0)

    # Per-note correction summary for the optional chord-timeline/cents
    # visualization (§6) -- kept as a response header so the audio itself
    # stays a plain streamable WAV body.
    notes_summary = [
        {
            "start": note.start_time,
            "end": note.end_time,
            "source": target.source,
            "chord": target.chord_label,
            "delta_cents": round(target.delta_cents * pull_strength, 1),
        }
        for note, target in zip(analysis.notes, targets)
    ]

    return StreamingResponse(
        buf,
        media_type="audio/wav",
        headers={"X-Notes": json.dumps(notes_summary)},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
