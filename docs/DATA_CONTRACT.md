# Data contract (pipeline → website)

The Python pipeline (`slipstream/`) writes JSON files into `web/data/`.
The static website (`web/`) only reads these files. Neither side may change
a field name without updating this document.

All times are seconds (float). All speeds are km/h. Colours are `#RRGGBB`.
Driver codes are the 3-letter acronyms (`NOR`), but the website shows
**last names** (`Norris`) to users.

## `web/data/index.json`

```json
{
  "generated_at": "2026-11-22T20:00:00Z",
  "races": [
    {
      "slug": "2026-21-las-vegas",
      "session_key": 11420,
      "year": 2026,
      "round": 21,
      "name": "Las Vegas Grand Prix",
      "location": "Las Vegas",
      "date": "2026-11-22",
      "has_compare": true
    }
  ]
}
```
`races` is sorted newest first. The website's race picker uses this list.

## `web/data/<slug>/race.json` — screen 1 "What happened"

```json
{
  "slug": "2026-21-las-vegas",
  "name": "Las Vegas Grand Prix",
  "location": "Las Vegas",
  "date": "2026-11-22",
  "total_laps": 50,
  "drivers": [
    {"number": 1, "code": "NOR", "last_name": "Norris", "team": "McLaren", "color": "#F47600"}
  ],
  "results": [{"code": "VER", "position": 1}],
  "safety_car_laps": [19, 20, 21, 22, 23],
  "headline": {
    "title": "Verstappen won by 0.6 seconds.",
    "text": "One or two plain-English sentences. No jargon."
  },
  "moments": [
    {
      "id": "m1",
      "driver": "LEC",
      "lap": 34,
      "lap_label": "Lap 34",
      "kind": "battery_low",
      "label": "Battery ran low",
      "confidence": "high",
      "why": "accelerator at 100%, yet 28 km/h slower before the braking point.",
      "chart": {
        "type": "speed",
        "title": "Where Leclerc slowed down · lap 34",
        "x_label": "Along the straight →",
        "y_label": "Speed",
        "zone": [0.55, 0.8],
        "zone_label": "Leclerc slows here",
        "series": [
          {"driver": "NOR", "points": [[0.0, 212.0], [0.01, 215.0]]},
          {"driver": "LEC", "points": [[0.0, 210.0], [0.01, 213.0]]}
        ]
      },
      "caption": "One plain-English sentence explaining the chart."
    }
  ]
}
```

- `kind` is one of `battery_low`, `energy_saving`, `tyre_wear`.
  Website labels: `battery_low` → "Battery ran low",
  `energy_saving` → "Saved energy to attack later",
  `tyre_wear` → "Tyres wore out". The pipeline also writes `label`.
- `confidence` is one of `high`, `medium`, `low`.
- `why` is a sentence fragment shown after "Why <confidence> confidence: ".
- `chart.type`:
  - `speed`: x is the fraction of the straight/segment (0–1), y is speed km/h.
  - `laptime`: x is the lap number, y is lap time in seconds (higher = slower).
- `chart.zone` is `[x_start, x_end]` in the same x units, or `null`.
- `series[].driver` is a code; the website looks up colour and last name in
  `drivers`. Max 2 series per chart. Max ~200 points per series.
- `moments` is sorted best first; `moments[0]` is what the chart shows on load.
  0–8 moments. An empty list is valid (the page says nothing stood out).

## `web/data/<slug>/whatif.json` — screen 2 "What if?"

```json
{
  "pit_loss_s": 21.4,
  "drivers": {
    "HAM": {
      "real_pit_lap": 31,
      "real_finish": 5,
      "options": [
        {
          "pit_lap": 28,
          "finish_estimate": 4,
          "gain_s": 1.4,
          "low_s": -2.1,
          "high_s": 4.8,
          "traffic": false,
          "nearby": [{"code": "ALB", "gap_s": -1.4}, {"code": "GAS", "gap_s": 2.0}]
        }
      ]
    }
  }
}
```
- Only drivers who made at least one stop under green flag appear. The
  what-if moves their **first** stop.
- `options` covers every lap from `real_pit_lap - 8` to `real_pit_lap + 8`
  that is a valid racing lap (≥ 2, < total_laps), including the real lap
  (gain 0).
- `gain_s` > 0 means the alternative is faster than reality.
  `low_s`/`high_s` are the honest range.
- `nearby[].gap_s` < 0 means that car is **ahead** of the driver after the
  stop, > 0 means behind. Max 2 entries (the nearest).
- `traffic` is true when a car is less than 1.5 s ahead after the stop.

## `web/data/<slug>/compare.json` — screen 3 "Compare two drivers"

```json
{
  "pairs": [
    {
      "a": "LEC",
      "b": "NOR",
      "lap": 34,
      "track": [[0.12, 0.40], [0.13, 0.41]],
      "a_path": [[0.0, 0.12, 0.40], [0.27, 0.13, 0.41]],
      "b_path": [[0.0, 0.12, 0.40], [0.27, 0.13, 0.41]],
      "segments": [{"from": 0, "to": 12, "faster": "LEC"}],
      "lap_time_a": 95.123,
      "lap_time_b": 94.912,
      "key_moment_frac": 0.58,
      "summary": "Leclerc was faster in the corners. Norris pulled away on the long straight."
    }
  ]
}
```
- `track` is the circuit outline from one lap, x/y scaled to 0–1 (keep the
  aspect ratio; the longer side is 0–1). **y points down** (SVG convention,
  0 = top), so the pipeline flips OpenF1's upward y.
- Every driver code in `whatif.json` / `compare.json` must also appear in
  `race.json` → `drivers`; the website hides codes it can't name.
- `a_path`/`b_path` rows are `[t_seconds_since_lap_start, x, y]` in the same
  scaled coordinates.
- `segments` index into `track` points (`from`/`to` inclusive) and say who
  was faster through that part.
- `key_moment_frac` is 0–1 of the lap time, or `null`.

## Social images

`python -m slipstream social --race <slug>` writes `out/<slug>/` with
`moment-<id>.png` (1080×1920). Not read by the website.
