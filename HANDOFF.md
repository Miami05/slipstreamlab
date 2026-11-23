# Handoff — SlipstreamLab

_Last updated: 2026-11-22_

## What this is
A free, open-source, unofficial fan website that explains motor races in plain
English after they happen (2026 rules: battery state is no longer shown on TV).
Python pipeline (`slipstream/`) → JSON (`web/data/`) → static site (`web/`).
Start with [README.md](README.md), then [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Status
| Area | State |
|---|---|
| Pipeline (OpenF1 → JSON) | ✅ works on 5 real 2026 races (Budapest, USA, Mexico, Brazil, Las Vegas) |
| Website (3 pages) | ✅ works on real data and on the demo (`?data=sample`) |
| Social images/videos (1080×1920, safe zones) | ✅ PNG + MP4 (H.264, bundled ffmpeg via imageio-ffmpeg; GIF only as fallback) |
| Tests | ✅ pytest (no network) + Node tests; CI in `.github/workflows/tests.yml` |
| Real-world validation | ⚠️ 4 races checked: after fixes 5 of 6 wrong causes removed, 9 of 9 plausible kept. See [docs/VALIDATION.md](docs/VALIDATION.md) |
| Code review | Fable + Neon (Gemini) + Sol verify — workflow `.claude/workflows/slipstream-review.js`. Three rounds done; the third reviewed everything after `fccb271` (incl. MP4 export) and its 5 findings are fixed. Next review: `{"base": "17abbce"}`. |
| Hosting | ❌ not deployed yet (local only) |

## Key decisions (and why)
- **OpenF1, not FastF1** — FastF1 needs an F1 account login for laps/telemetry in 2026.
- **Generated data is not committed** — we don't redistribute F1 data; anyone can rebuild.
- **Causes are hedged and capped** — "Lost power (maybe battery)" is never above
  medium confidence: the public data has no battery channel and an engine power
  cut looks the same (Gasly, Las Vegas).
- **No "F1"/team names in the project name, no photos/logos** — see [docs/LEGAL.md](docs/LEGAL.md).

## How to work on it
```bash
.venv/bin/python -m slipstream build --year 2026 --round 21   # build a race (~10 min first time, cached after)
.venv/bin/python -m slipstream evaluate                       # check findings against the validated cases
.venv/bin/python -m pytest -q && for f in tests/web/*.mjs; do node "$f"; done
python3 -m http.server -d web 8000                            # view the site
.venv/bin/python -m slipstream clean-cache                    # free ~150 MB per built race
```
**After any detection change: rebuild the 5 races and run `evaluate --strict`.**
A fix that removes a "plausible" case is a regression.

Before pushing: run the review workflow in Claude Code
(`Workflow scriptPath=.claude/workflows/slipstream-review.js`, args `{"base": "<last reviewed commit>"}`).
It needs the `fable-reviewer` and `gemini-reviewer` agents in `~/.claude/agents/`
and the Antigravity CLI (`agy`) logged in.

## Known limitations
- Engine power cuts (Gasly, Las Vegas) can't be told apart from battery limits in public data.
- What-if only moves **green-flag** stops and skips rain-tyre changes (Las Vegas and São Paulo: none qualify → the page explains why).
- Red flags are detected from real Mexico 2026 message formats; other formats may differ.
- Some feeds are degraded (Budapest: repeated samples) → fewer findings there.
- 0 findings are "confirmed" yet — most are "plausible". Post-race quotes rarely name a lap.
- Depends on OpenF1 staying free; the client is isolated in `slipstream/openf1.py`.

## Next steps (suggested order)
1. Validate 2–3 more races (Qatar 29 Nov, Abu Dhabi 6 Dec) with the Gemini Hydrogen researcher; add cases to `tests/validation_cases.json`.
2. Only share publicly once wrong causes stay rare across those races.
3. Deploy the static site (GitHub Pages: build data in an Action, don't commit it).
4. First social posts from the strongest "plausible" findings (r/F1Technical, r/F1DataAnalysis — post the image, not a link).
