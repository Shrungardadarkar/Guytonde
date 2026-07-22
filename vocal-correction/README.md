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

### Hosted on GitHub Pages

The frontend is also deployed to GitHub Pages on every push to `main`
(`.github/workflows/deploy-pages.yml`), so it's reachable at a stable URL
without cloning the repo. The backend still runs locally on your own
machine -- your audio is never uploaded anywhere; the hosted page just
talks to `http://localhost:8000` from your browser, same as running it
locally. Two one-time repo settings are needed for this to work (not
scriptable from here, done once in the GitHub UI):

1. **Settings > Pages > Build and deployment > Source**: set to
   "GitHub Actions".
2. The repo must be public (or on a paid plan) for Pages to be free --
   see **Settings > General > Danger Zone > Change visibility**.

Once Pages is live, browsers treat `localhost` as an exception to the
mixed-content block, so an `https://…github.io` page can fetch
`http://localhost:8000` without extra configuration -- just make sure the
backend is running locally before using the hosted page.

## Workflow

1. Upload the isolated dry vocals and the rough mix (same take, same
   timeline), and type the song's parent scale (e.g. `"G major"`).
2. Click **Process** -- this runs chord detection and pitch analysis once.
3. Drag **Pull strength** to taste; it debounces and re-renders via the
   backend. Drag **Preserve original** to taste; it crossfades instantly,
   client-side, with no server round-trip.
4. **Download corrected vocal** bakes the current pull-strength +
   blend into a single WAV, entirely client-side.
