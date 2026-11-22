# SlipstreamLab

**Plain-English explanations of motor races — after they happen.**

In the 2026 season the hybrid battery delivers roughly half of a car's power,
but the TV broadcast no longer shows how much charge is left. So fans see a car
suddenly lose speed on a straight and nobody explains why.

SlipstreamLab takes the free, public timing data after each race and answers
three simple questions:

| Page | Question it answers |
|---|---|
| **What happened** | Why did that driver slow down? Lost power (maybe the battery), probably saving energy on purpose, or worn-out tyres? |
| **What if?** | What if a driver had stopped for new tyres a few laps earlier or later? |
| **Compare two drivers** | Where on the track was each driver faster? (replay on a track map) |

It also makes vertical images/videos (9:16) for social media.

> Everything is an **estimate** from public data (about 4 samples per second,
> no battery data). Every finding shows how confident we are.

## Quick start

```bash
git clone https://github.com/Miami05/slipstreamlab.git
cd slipstreamlab
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 1. Build the data for a race (downloads from OpenF1, cached in .cache/)
.venv/bin/python -m slipstream build --year 2026 --round 21
#    or: --latest  (most recent finished race)
#    add --refresh to ignore the local cache and download that race again

# 2. Look at the website
python3 -m http.server -d web 8000
#    open http://localhost:8000
#    no data yet? open http://localhost:8000/?data=sample for a demo

# 3. Make social media images (and --video for an animated version)
.venv/bin/python -m slipstream social --race 2026-21-las-vegas

# 4. Free disk space: raw downloads are ~150 MB per race; the website only
#    needs web/data (~70 KB per race). Deletes downloads of races already built.
.venv/bin/python -m slipstream clean-cache            # add --dry-run to preview, --all for everything
```

The first build of a race takes a few minutes because the free data API is
rate-limited. After that everything comes from the local cache.

## Tests

```bash
.venv/bin/python -m pytest -q
for f in tests/web/*.mjs; do node "$f"; done
```

## How it works

```
OpenF1 API ──► slipstream/ (Python) ──► web/data/*.json ──► web/ (static site)
                     └──► out/<race>/*.png  (social images)
```

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — folders, data flow, design choices
- [`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md) — the JSON files between pipeline and website
- [`docs/METHODS.md`](docs/METHODS.md) — how battery / tyre / pit-stop estimates work, and their limits
- [`docs/LEGAL.md`](docs/LEGAL.md) — what we may and may not use (names, logos, data)
- [`HANDOFF.md`](HANDOFF.md) — current status and next steps

## Data and licence

- Code: [MIT](LICENSE). The licence covers **code only**, not any data.
- Data comes from [OpenF1](https://openf1.org), a free community API for
  non-commercial use. This project is free and non-commercial. Generated race
  data is **not** committed to this repository.
- No driver photos, team logos or broadcast footage are used — drivers are
  shown by name and team colour only.

---

This website is unofficial and is not associated in any way with the Formula 1
companies. F1, FORMULA ONE, FORMULA 1, FIA FORMULA ONE WORLD CHAMPIONSHIP,
GRAND PRIX and related marks are trade marks of Formula One Licensing B.V.
