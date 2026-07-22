# Vocal Tuning Correction

Personal-use tool that gently pulls a sung take's center pitch back toward
the harmonically correct target, while preserving vibrato, timbre, and
timing. Not auto-tune, not a mixing tool. Full design in
[`VOCAL_CORRECTION_SPEC.md`](./VOCAL_CORRECTION_SPEC.md).

## Run the backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

`pyworld` needs a C compiler at install time if no prebuilt wheel matches
your platform -- see spec §8 if `pip install` fails there.

## Open the frontend

`frontend/index.html` is a static page with no build step -- open it
directly in a browser, or serve it with any static file server. It talks to
the backend at `http://localhost:8000` by default (override by setting
`window.BACKEND_URL` before `app.js` loads).

## Workflow

1. Upload the isolated dry vocals and the rough mix (same take, same
   timeline), and type the song's parent scale (e.g. `"G major"`).
2. Click **Process** -- this runs chord detection and pitch analysis once.
3. Drag **Pull strength** to taste; it debounces and re-renders via the
   backend. Drag **Preserve original** to taste; it crossfades instantly,
   client-side, with no server round-trip.
4. **Download corrected vocal** bakes the current pull-strength +
   blend into a single WAV, entirely client-side.
