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
`date` is the **local calendar date at the circuit** (race start in UTC shifted
by the session's `gmt_offset`), so a Saturday-night race in Las Vegas is
`2026-11-21`, not the UTC date.

## `web/data/<slug>/race.json` — screen 1 "What happened"

```json
{
  "slug": "2026-21-las-vegas",
  "name": "Las Vegas Grand Prix",
  "location": "Las Vegas",
  "date": "2026-11-21",
  "total_laps": 50,
  "drivers": [
    {"number": 1, "code": "NOR", "last_name": "Norris", "team": "McLaren", "color": "#F47600"}
  ],
  "results": [{"code": "VER", "position": 1}],
  "safety_car_laps": [19, 20, 21, 22, 23],
  "headline": {
    "title": "Verstappen won by 0.6 seconds.",
    "text": "One to three plain-English sentences. No jargon. Separate safety-car spells are named separately (lap 5 to 7 and again on lap 40 to 42). If it rained, one sentence says so (\"Rain came and went, on laps 1 to 16 and again on laps 58 to 71, so we left those laps out of the tyre and power readings.\")."
  },
  "moments": [
    {
      "id": "m1",
      "driver": "LEC",
      "lap": 34,
      "lap_label": "Lap 34",
      "kind": "battery_low",
      "label": "Lost power (maybe battery)",
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
      "caption": "One plain-English sentence explaining the chart. Its numbers are measured on the plotted lines."
    }
  ]
}
```

- `kind` is one of `battery_low`, `energy_saving`, `tyre_wear`, `power_cut`.
  Website labels: `battery_low` → "Lost power (maybe battery)",
  `energy_saving` → "Lifted off early (probably saving energy or cooling the car)",
  `tyre_wear` → "Tyres probably wore out",
  `power_cut` → "Had less power for most of the race (probably a car problem)".
  The pipeline also writes `label` (the website's pill uses its own mapping in
  `web/assets/common.js` for these four kinds, so both must say the same).
  **Every inferred cause is hedged** ("probably", "likely", "maybe", "may be")
  in labels, zone labels, captions and social titles (docs/LEGAL.md); `build`
  refuses to write a moment whose label or caption is not hedged.
  For `battery_low` the data only shows that the car lost speed at full
  throttle; the battery is one possible reason (an engine-temperature power cut
  looks the same), so `battery_low` confidence is capped at `medium`, never
  `high`. If the same loss shows up on at least half of the driver's clean laps
  at that place, `why` and `caption` add that it happened on most laps, which
  can also mean a car problem (information only: it does not lower the confidence).
  `power_cut` (new, 2026-11-22) is a `battery_low` finding of a driver who, from
  some lap on, was at least 8 km/h slower than his teammate on at least 70 % of the
  remaining laps (and below the field's median): the speed loss at that moment is
  real but the cause is probably the car, not the battery. It is drawn like
  `battery_low` (`chart.type` = `speed`), its confidence is `low` or `medium`, and
  the per-kind limit (1 per race) only applies to `medium` ones; `low` ones compete on rank like any other low-confidence moment. A page that does not know a kind shows
  the pipeline's `label` on a grey pill.
  `energy_saving` can be lowered one confidence step when the driver was less than
  1.5 s behind the car in front (traffic); `high` of any kind needs at least 5 clean
  laps of evidence.
- `confidence` is one of `high`, `medium`, `low`.
- `why` is a sentence fragment shown after "Why <confidence> confidence: ".
- `chart.type`:
  - `speed`: x is the fraction of the straight/segment (0–1), y is speed km/h.
    For `battery_low` the numbers in the caption ("slowed by about 41 km/h",
    "<other> lost 5 km/h on the same stretch") are the speed drop inside
    `chart.zone` measured on the plotted `series` points themselves: highest
    speed in the zone minus the lowest speed after it.
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
- Optional `unavailable` (string): present with `drivers: {}` when no what-if could be made (race
  control missing); the page shows it instead of the generic empty message.
- Only drivers who made at least one stop under green flag appear. The
  what-if moves their **first green-flag stop**: the first stop after lap 2 and
  before the last lap whose in-lap and out-lap are not under a safety car, VSC
  or red flag, that is not made on a restart lap and has no safety-car window
  at the pit timestamp. A driver whose only stops were under a safety car
  (e.g. most of the field in Las Vegas 2026) does not appear; `drivers` can be
  `{}` and the page then says there is nothing to try. Later stops stay as
  they really were. The page says "first pit stop made under green flags".
- `options` covers every lap from `real_pit_lap - 8` to `real_pit_lap + 8`
  that is a valid racing lap (≥ 2, < total_laps), including the real lap
  (gain 0).
- `gain_s` > 0 means the alternative is faster than reality.
  `low_s`/`high_s` are the honest range. `gain_s` is held at what the measured
  tyre wear can plausibly deliver (stop moved by k laps → at most k × the
  largest tyre penalty measured in this race); when the straight-line model
  wanted more, the range is widened until it reaches the model's own number.
  The same `gain_s` drives `finish_estimate` and `nearby`. `low_s <= gain_s <= high_s`.
  `finish_estimate` is a *probable* finish; the website says "would probably finish".
- `nearby[].gap_s` < 0 means that car is **ahead** of the driver after the
  stop, > 0 means behind. Max 2 entries (the nearest).
- `traffic` is true when a car is less than 1.5 s ahead after the stop (same
  lap count, end of the out-lap). That is all it says: the other car's pace is
  not compared and no time is deducted for it. The website says "He'd come out
  right behind another car, which could cost him time." / "No car within 1.5
  seconds in front of him when he comes out."

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
  scaled coordinates. Each path is cut and interpolated to **exactly
  `[0, lap_time]`**: the first row has `t = 0` and the last row `t = lap_time`
  (rounded to 0.01 s), so the end of the replay equals the printed lap time.
- This is a **ghost-lap comparison**: both laps start together at `t = 0`
  (each from its own start of lap), so the gap is not the real gap on track.
  The page says: "Both laps start together, like a ghost car — this isn't
  their real gap on track." The gap readout follows each car's share of its
  own lap distance, with hysteresis (a leader is only named at 0.10 s and kept
  until the gap falls below 0.05 s), and once both laps are over it equals
  `lap_time_b - lap_time_a`. The key-moment marker is always "Key moment"
  (never "Overtake").
- `segments` index into `track` points (`from`/`to` inclusive) and say who
  was faster through that part.
- `key_moment_frac` is 0–1 of the lap time, or `null`.

## Social images

`python -m slipstream social --race <slug>` writes `out/<slug>/` with
`moment-<id>.png` (1080×1920). Not read by the website.
