# Architecture

SlipstreamLab has two halves that only talk through JSON files:

```
                 ┌──────────────── slipstream/ (Python) ────────────────┐
OpenF1 API ────► │ openf1.py  → model.py → energy.py  ┐                 │
(free, cached     │                       → tyres.py   ├→ story.py ──►  │──► web/data/<race>/race.json
 in .cache/)      │                       → whatif.py  ┘                 │──► web/data/<race>/whatif.json
                 │                       → compare.py ─────────────►    │──► web/data/<race>/compare.json
                 │ validate.py checks everything before build.py writes │──► web/data/index.json
                 │ social.py ───────────────────────────────────────►   │──► out/<race>/moment-*.png/.gif
                 └──────────────────────────────────────────────────────┘
                                                   │
                         web/ (static HTML + vanilla JS) reads web/data/
```

## Why it is built this way

| Decision | Why |
|---|---|
| **Pre-compute after the race, no live server** | Free hosting can't hold raw telemetry in memory; static files never crash on race day. |
| **OpenF1, not FastF1** | In 2026 FastF1 needs an F1 account login for laps and telemetry. OpenF1 is free and needs no login. |
| **Disk cache for every API call** | The free API allows ~30 requests/minute. The first build of a race takes ~2 minutes, later builds ~2 seconds. |
| **Vanilla JS, no framework, no build step** | Simple for a beginner to read and change; works on GitHub Pages as-is. |
| **One JSON contract** ([DATA_CONTRACT.md](DATA_CONTRACT.md)) | The pipeline and website can be changed independently as long as the contract holds; `validate.py` enforces it. |
| **Generated data is git-ignored** | We don't redistribute F1 data. Anyone can regenerate it with one command. |
| **Social images rendered locally** | Cloud video rendering is expensive; a local script is free. |

## Folders

| Path | What's inside |
|---|---|
| `slipstream/` | The Python pipeline. Entry point: `python -m slipstream build|social`. |
| `slipstream/openf1.py` | HTTP client: cache, rate limiting, retries. |
| `slipstream/model.py` | Loads a race into tidy tables; marks safety-car / pit / lap-1 laps to exclude. |
| `slipstream/energy.py` | Finds "battery ran low" and "saving energy" moments from speed/throttle/brake. |
| `slipstream/conditions.py` | Rain, blue-flag, chequered-flag and traffic marks per lap (what to leave out of tyre / energy analysis). |
| `slipstream/powercut.py` | Sustained speed deficit against the teammate = "probably a car problem". |
| `slipstream/evaluate.py` | `python -m slipstream evaluate`: compares findings with the checked cases in `tests/validation_cases.json`. |
| `slipstream/tyres.py` | Fuel-corrected lap times and tyre wear per stint. |
| `slipstream/whatif.py` | "What if the stop happened on another lap?" estimates with honest ranges. |
| `slipstream/compare.py` | Two drivers' laps on a track map, who was faster where. |
| `slipstream/story.py` | Turns findings into short plain-English sentences (bans jargon). |
| `slipstream/validate.py` | Checks every JSON file against the contract. |
| `slipstream/social.py` | 1080×1920 PNG (and GIF/MP4) for TikTok/Reels/Shorts, inside safe zones. |
| `web/` | The website: `index.html` (What happened), `whatif.html`, `compare.html`, `assets/`. |
| `web/sample-data/` | Fake demo race so the site works without running the pipeline (`?data=sample`). |
| `tests/` | `pytest` tests (no network) and `tests/web/*.mjs` Node tests. |
| `.claude/workflows/` | The Fable + Neon review workflow for Claude Code. |

## Reviews

Before pushing, the code is reviewed by two independent AI reviewers and the
findings are verified:

1. **Fable** (Claude Fable, read-only review)
2. **Neon** (Gemini 4 Neon via the Antigravity CLI `agy`, read-only)
3. **Sol** (Claude Opus) checks every finding against the code and drops false alarms.

Run it in Claude Code by asking to run the `slipstream-review` workflow.
