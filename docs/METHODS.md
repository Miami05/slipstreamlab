# How SlipstreamLab works (methods, assumptions, limits)

SlipstreamLab is a free, open-source, **unofficial** fan project. It explains
races in plain English *after* they happen. Everything below is an
**estimate made from public timing data, not a fact from the teams**. Wherever
we infer a cause, the website says "probably" or "likely" and shows a
confidence level.

## Data source and licence

* All data comes from [OpenF1](https://openf1.org) (`api.openf1.org/v1`), free
  and without login, for **non-commercial** use. We respect its limits
  (about 3 requests/s and 30/min): the client waits at least 2.1 s between
  uncached requests, retries with back-off on 429/5xx (and twice on a transient
  422/408/425), and caches every answer
  on disk (`.cache/openf1/<sha1 of the URL>.json`, git-ignored). Results of a
  race that ended less than 48 hours ago are provisional (penalties, late
  corrections), so its `session_result` and `laps` files expire after one hour
  (if the refresh fails, the older copy is used and a note is printed).
  `build --refresh` ignores the cache for that whole race and downloads it
  again (slow: minutes, car data included).
* **Required** endpoints (`laps`, `drivers`, `stints`, `pit`, `session_result`) fail the
  build loudly. **Optional** ones (`weather`, `intervals`, `race_control`, `location`) only print
  a `NOTE` and the build goes on without them: no weather = rain is read from rain tyres and
  field pace only; no intervals = traffic is not checked (the old lap-timestamp proxy stays);
  no race control = safety-car, yellow and blue-flag laps cannot be left out, so every
  finding is shown with low confidence (and says so) and **no what-if is offered** (every
  safety-car stop would look like a green-flag stop); no location = no track comparison.
* We do **not** redistribute raw OpenF1 data. `web/data/` holds only derived
  results (a few hundred points per chart) and is git-ignored; raw telemetry
  never leaves the local cache.
* We never use driver photos (`headshot_url`), team logos or any official
  F1 artwork. Driver colours are the plain hex team colours from the feed.
* FastF1 is not used (it now needs an F1 account for laps and telemetry).
* "F1", "Formula 1" and team names are trademarks of their owners; this site is
  not affiliated with or endorsed by them.

## What the data can and cannot show

* Car data is sampled about 3.7 times per second (every ~0.27 s) and there are
  a few gaps of 0.5-1 s per lap. At 300 km/h the car covers ~22 m per sample.
* **There is no battery channel.** We cannot see how full the battery is. We
  only see its *footprint*: what speed, throttle and brake did.
* Position data (`location`) has the same rate and is used only for the
  "compare" track map.
* Some feeds are bad. In the Hungary 2026 race the speed channel repeated the
  same value ~73 % of the time (updated about once a second or less) and the
  position channel ~93 % of the time. Rather than invent findings from that,
  the pipeline measures this (`energy.telemetry_quality`,
  `compare.MAX_REPEATED_POSITION`) and **skips** battery/energy findings and the
  track comparison for such races. Tyre moments and the what-if still work
  because they only use lap times.

## Which laps count

A lap is used only if it is a normal racing lap: not lap 1, not under a safety
car or virtual safety car, not the restart lap after one, not a pit in-lap or
out-lap, and no yellow flag active anywhere on track during the lap (we do not
know which marshal sector is where, so we are strict). Safety-car windows come
from the race-control messages ("deployed" to "in this lap", extended to the end
of the leader's lap), matched to each driver's own lap timestamps. A virtual
safety car that is upgraded to a full safety car is closed when the safety car
is deployed (it is never announced as "ending"). A red flag opens a window that
closes at the message saying the session resumed (or runs to the end if none
comes); its laps are neutral and the first green lap after it is a restart lap.
The real message shapes come from Mexico City 2026 (the only cached race with a red flag):
"RED FLAG - RACE SUSPENDED" (category Other, no flag) and SessionStatus "SESSION ABORTED" open
the window and close any open safety car (the red flag replaces it, so it is never announced as
ending); only SessionStatus "SESSION STARTED" ends it. "RACE WILL RESUME AT 15:45" is an
advance notice sent 13 minutes before the restart and does not end it. A race that is never
restarted stays red until its end. The headline says there was a red flag.
**Laps left out of tyre and energy analysis as well** (`model.analysis_lap`; the what-if and
compare screens still use every green lap):

* **Rain.** A lap is wet if the OpenF1 `weather` rainfall flag was on during it, if the driver
  was on intermediate or wet tyres, or if at least 25 % of the cars still running were on rain
  tyres. For each wet spell that starts after the first laps we also walk back from its first
  lap while the field's median lap time is at least 1 s above its earlier level, because the
  track gets wet before anyone changes tyres (Brazil 2026: the rainfall flag only came on at
  lap 63, but the field slowed from lap 58 and rain tyres appeared on lap 61). A wet lap and the
  2 laps after it are left out. We do not use track temperature: the OpenF1 values jump by 10 °C
  and drop to 0 before the race, which makes it a worse rain signal than the rainfall flag.
  If it rained the headline says so.
* **Blue flags.** A blue flag message for a driver (he is being lapped) is matched by time stamp
  to his own lap (the message's `lap_number` is the leader's lap). His laps +-1 are left out of
  tyre analysis; in energy analysis only his *lift* features are dropped (a lapped driver eases
  off to let the leader by), not full-throttle speed falls.
* **Chequered flag.** The lap during which the flag came out (the last lap of everybody) is
  left out: cars ease off and nothing there is racing data. In the United States 2026 race
  Alonso's flagged "energy saving" lift on lap 55 was this lap (no safety car or yellow flag was
  active; he was a lap down and took the flag at the end of it).

**Traffic** (OpenF1 `intervals`) does not remove laps, it explains them. OpenF1 only publishes an
interval when it changes, so the value is held until the next row, but not longer than 60 s
(older = unknown). A lap is "in traffic" when the driver spent at least half of it less than
1.5 s behind the car in front. The leader and cars a lap down ("+1 LAP") are never in traffic.

A pit stop marks the in-lap (OpenF1's own `lap_number` of the stop) and the
following out-lap; the first flying lap on the new tyres is a normal lap. Laps whose
telemetry has a hole longer than 1.2 s are dropped. Braking readings that
contradict the throttle (brake stuck at 100 while flat out) are ignored, and
single-sample blips are removed.

## Lost power, maybe the battery (`battery_low`)

2026 cars get roughly half their power from the battery. When it runs empty
on a straight, the car stops accelerating and can even slow down while the
driver still has the accelerator flat down (engineers call it "clipping";
we don't use that word on the site). We can only see the **lost power**; the
battery is one likely reason, but an engine-temperature limit or another car
problem looks the same, so the label is "Lost power (maybe battery)".

1. **Straights are found from the data**: places where most laps are at >= 98 %
   throttle for at least 350 m (gaps under 1 % of the lap are bridged). The
   braking point is the median first brake application after each straight.
2. For every driver and lap we take the longest flat-out run on each straight,
   cut it at the braking point (or at any one-sample drop above 15 km/h, which
   is braking or a data jump), and measure **how far the speed fell from its
   peak** before that point.
3. Two baselines must both be beaten: the **circuit baseline** (median fall of
   all drivers on that straight - some circuits make everyone slow down) and
   **the driver's own baseline** (his median over his clean laps). A lap is
   flagged when the fall is at least 8 km/h *and* at least 6 km/h more than the
   larger of the two. A driver who always loses speed there is not flagged.
4. Neighbouring flagged laps (within 2 laps) are merged into one moment; the
   strongest lap is shown.
5. **Confidence** comes from points: excess >= 25 km/h (2) or >= 15 (1), plus 1
   if it repeated on >= 2 laps and 1 more for >= 4 laps. 3+ points = high, 2 =
   medium, otherwise low. It is capped at medium when we have fewer than 10
   laps to define his normal, and lowered when another car was within 1 s
   ahead at the start/finish line (lap timestamps are the only gap data we
   use) because following closely changes speed too.
6. The chart compares the driver's speed on that straight with his teammate's
   on the same lap (or the nearest car in the standings if the teammate has no
   clean lap). The grey zone is where the speed fell. **Caption and chart use
   the same number:** the speed drop (highest speed in the zone minus the lowest
   after it) is measured on the plotted points of each car inside the grey zone,
   and the comparison car is picked by that same number (a car that lost half as
   much or more is skipped; if none qualifies the comparison sentence is left out).
7. **Never "high".** The power loss is measured, but its cause cannot be told apart
   from e.g. an engine-temperature power cut, so `battery_low` confidence is capped
   at *medium*. Ranking of moments still goes by the size of the loss beyond the
   circuit and driver baselines.
8. **Persistent loss (information only).** If a driver loses at least 8 km/h *and*
   at least 6 km/h more than the field median on 50 % or more of his clean laps
   at that straight, `why` and the caption say it happened on most laps, which
   can also mean a car problem. It does not change the confidence: on an
   energy-starved track repeated losses are normal.

9. **Sustained power loss (`power_cut`).** See the next section: a battery finding of a driver
   who was slower than his teammate for most of the race after it is relabelled.

*Limits:* a fall at full throttle can also come from wind, tow loss, a car
ahead, a different set-up, or from the sparse sampling near the braking point.
We call it "probably", never "certainly". Short falls (< 8 km/h) are ignored.

## Had less power for most of the race (`power_cut`)

A battery that runs low costs speed for a few laps. An engine that is turned down (heat, a
fault) costs speed on *most* laps. Las Vegas 2026 showed why we need to tell them apart: Gasly
lost power on "most of the 50 laps" because Alpine turned the engine down to manage temperatures,
while our detector said "battery".

1. For each lap and straight we take each car's **peak speed** and subtract the median peak speed
   of the whole field on that lap and straight; averaged over the straights this is the lap's
   deviation (needs at least 6 cars and 2 straights).
2. For each driver and his teammate we look only at laps both have and where **neither was in
   traffic** (a tow adds speed: when one car follows and the other does not, the pair shows a
   "deficit" that is only the tow; we saw exactly that for Hulkenberg against Bortoleto in Las
   Vegas before adding this rule). With fewer than 10 such laps there is no verdict (Las Vegas:
   67 % of laps were in traffic, so most drivers cannot be judged there).
3. From some lap onward he must be **at least 8 km/h below his teammate on at least 70 % of the
   remaining laps (>= 10 laps)**, the spell must start on such a lap, and his median deviation
   from the field must be at least 4 km/h negative (so a teammate who is just very fast does not
   count). The spell must also cover **at least half of his shared clean laps**, and from its
   first to its last **deficit** lap at least **half of the laps he actually raced** (taken from the
   lap table, so a car that retires with the problem counts and a teammate who finishes doesn't
   stretch it). The deficit laps are split into segments wherever more than 5 laps (or 10 % of
   the race) are missing; shorter holes (traffic, a pit stop, one healthy lap) are normal.
   The spell may not start on a segment of fewer than 3 laps, and short segments never bridge a
   hole (their laps count as missing). Walking from the start, the spell must reach half of the
   laps the driver raced while no more than 35 % of the full race distance (and never more than
   half of his own laps) is missing in total — every missing lap counts, inside segments too; the
   first point where that holds decides, so more deficit laps later can't undo it, and only the
   clean laps up to that point count towards the minimum of 10. **Principle: when in doubt, don't claim a car problem** — a
   missed power cut leaves the finding as "Lost power (maybe battery)", a false one states
   something wrong. Every edge case raised in review is a row in `POWER_CUT_SPAN_CASES`. So a deficit that only shows in the
   last 10 laps, or a few early laps plus a late spell, is not "most of the race". Without intervals
   data there is no verdict at all (tow laps cannot be left out).
4. A `battery_low` finding of that driver from that lap on becomes `power_cut` with the label
   "Had less power for most of the race (probably a car problem)"; confidence is `medium` if the
   deficit was >= 12 km/h on >= 85 % of the laps, otherwise `low`.

*Limits, honestly:* it cannot see a loss that hits **both** cars of a team (a team-wide setting),
and set-up differences between teams are too large for a field-only comparison. **It does not
fire for Gasly in Las Vegas:** he and Colapinto were never both in clean air for 10 laps, and
on all laps together Gasly was within a few km/h of his teammate and above the field's median on
every straight (speed trap, peak speed and average speed along the straight all checked), so the
Alpine power reduction is not visible in the public speed data. That validation case
stays "wrong" in `evaluate`. The detector fires for none of the 5 cached races; its behaviour is
covered by synthetic tests.

## Lifted off early (`energy_saving`)

To recharge the battery a driver can lift off the accelerator early and coast
into the braking zone. He can also lift to cool the car (Hulkenberg in Mexico: "we struggled with
temperatures throughout"), and the data cannot tell the two apart, so the label is "Lifted off
early (probably saving energy or cooling the car)". We look at each braking zone: how long was the throttle
~0 (< 10 %) with no brake, after being flat out, just before the brake was
pressed? A lap is flagged when that is >= 1.0 s and at least 0.55 s more than
both the circuit median and the driver's own median. The same confidence rules
apply. If the driver was in traffic on that lap (intervals, see above) the confidence is one
step lower (without intervals the old lap-timestamp proxy caps it). "High" needs at least
5 flagged laps in the spell. With 0.27 s sampling, durations are only good to about
+-0.3 s. Lifting can also be caused by traffic, blue flags (those laps are left out), yellow
flags we cannot see, or a mistake; we cannot tell these apart from the numbers.

## Tyres wore out (`tyre_wear`)

1. **Fuel correction.** We assume 70 kg of fuel at the start, burned evenly over
   the race distance, and 0.03 s of lap time per kg. We subtract that from each
   lap, so a car does not look "faster" just because it got lighter. Raw lap
   times can fall during a stint while the corrected ones rise.
2. **Clean laps** (see above) and no lap slower than 107 % of the stint's
   median.
3. A straight line of corrected lap time against tyre age is fitted per stint
   (>= 6 clean laps) to measure the **degradation slope** in seconds per lap.
4. A **tyre-wear moment** needs >= 10 clean laps in the stint, a baseline from
   the early laps (laps 2-5 of the stint), and then a smoothed (3-lap median) lap
   time at least 0.7 s above that baseline which **stays there until the stint
   ends**. We report the lap where the climb began. Confidence comes from the
   size of the rise, how many laps it lasted and how well a straight line fits.
   *New (validation fixes):* a stint that is on rain tyres, or directly before or after a
   stint on rain tyres, never gives a tyre-wear moment (the weather changed; the slow-down at the
   end of the slick stint that led to the switch is the rain, not the rubber). If the driver was
   in traffic on most of the slow laps and on at least 5 of them, the finding is dropped (it is
   traffic); with fewer than 5 such laps it stays but with `low` confidence and says so. "High"
   needs at least 5 clean (not-in-traffic) slow laps.
   If the field's median lap time rose by half as much or more over the same
   laps (weather, track state), or if two or more other drivers show a rise
   starting on the same lap (traffic, a restart), the confidence is forced to
   *low* and the text says so.

*Limits:* track evolution (rubber laid down) makes lap times fall during a
race. In Las Vegas this was stronger than the fuel and tyre effects, so most
measured slopes were negative; we clamp slopes at zero, which means the
what-if sees "no measurable tyre wear" there.

## Checking against the real world (`evaluate`)

`tests/validation_cases.json` holds the 19 findings that were checked against driver and team
statements (docs/VALIDATION.md): race, driver, kind, lap, verdict and, for the wrong ones, the
true cause. `python -m slipstream evaluate` reads `web/data/*/race.json` and reports for each case
whether it is *present* (same driver and kind, lap within +-3, same label), *relabelled* (another
kind that names the true cause) or *gone*. A "wrong" case passes when it is gone or its label names
the true cause; a "plausible" case passes only when it is still present (losing one is a regression,
not a win); "unknown" cases are information. `--strict` exits with 1 on any failure.

| | before the fixes | after |
|---|---|---|
| wrong cases fixed | 0 / 6 | 5 / 6 (Gasly Las Vegas stays: see power cut) |
| plausible cases kept | 9 / 9 | 9 / 9 |

Caveats: Hulkenberg (Mexico) is "gone" only because his lift is now in traffic, so its
confidence fell to low, and low-confidence findings are hidden when at least three confident ones
exist (low-confidence fill-ins have their own cap of at most 2 per kind, 1 for power cut, counted apart
from the confident ones, and the headline counts them apart as "less
certain"). Albon (USA) survives only as a low-confidence finding. Removing the confident findings
lets other low-confidence ones appear that nobody has checked yet.

## What if? (`whatif.json`)

For each driver who made a stop under green-flag conditions, we move his
**first green-flag stop** to every lap within +-8 laps of the real one that
is a valid lap (2 to total-1, inside his tyre set's laps). "Green-flag stop"
means: after lap 2, before the last two laps, no safety car / VSC / red-flag
window at the pit time or over the in-lap and out-lap, and not on a restart lap.
(Yellow flags are *not* checked for stops.) Earlier stops under a safety car
and later stops stay as they were; a driver whose only stops were under a
safety car is not offered (in Las Vegas 2026 that is everybody, so the page
says there is nothing to try). A driver whose first green stop is between a rain tyre
(intermediate / wet) and a slick is not offered either: only tyre age is modelled, not the grip
of a drying or wetting track (Brazil 2026: the first stop of everybody was inters to slicks).
If race control could not be loaded the what-if is skipped (`unavailable` in `whatif.json`).

* **Time gain** comes only from tyre age. For each lap we compare the real tyre
  (compound, age) with the alternative one and multiply the age by the measured
  slope for that compound (field median, blended 50/50 with the driver's own
  slope when he has a stint of >= 8 clean laps). Positive = faster than reality.
  Everything else (second stops, other cars, weather) stays real.
* **Plausibility bound.** A straight line fitted to a few stints can promise
  absurd numbers when it is extrapolated over many laps. Moving a stop by k laps
  swaps k laps of worn rubber for k laps of fresh rubber, so the gain cannot be
  more than k times the largest tyre penalty measured in this race (slope x laps
  covered of the worst measured stint, at least 1 s). If the model's number is
  larger, `gain_s` is held at that bound, the range is widened so it still
  reaches the model's number, and the same held value feeds the finish estimate
  and the nearby cars. This bound is deliberately loose: it only removes
  impossible values. Gains beyond a few seconds still deserve scepticism.
* **Pit loss** (`pit_loss_s`) is measured as the median time lost over the in-lap
  and out-lap on green-flag stops compared with the driver's nearby clean laps,
  if at least three such stops exist; otherwise the median pit-lane time of all
  stops is used (e.g. Las Vegas, where almost everyone stopped under the safety
  car). It cancels out of the gain because both versions make one stop, but it
  matters for position.
* **Nearby cars and traffic**: we take everybody's real cumulative race time at
  the end of the out-lap, shift the driver's by the modelled gain and (when the
  alternative stop happens before the real one) the pit loss, and list the two
  nearest cars. When the alternative stop is exactly one lap earlier, lap Q is
  the real in-lap, whose time already contains part of the real stop; we then
  start from the driver's last lap plus a normal clean lap instead, so the pit
  loss is not paid twice. (A second stop's in-lap needs no such correction: both
  versions contain it.) `gap_s` is their time minus his: **negative means the car is
  ahead**. `traffic` is true when a car is less than 1.5 s ahead. That is all it
  claims: the other car's pace is not compared and the finish estimate does not
  deduct time for it. Cars that have not stopped yet are placed as they really were.
* **Finish estimate** = official finishing position moved by how many cars'
  final times lie between his real and his alternative time. Time penalties in
  the official result are therefore respected. It is a probable finish ("would
  probably finish 4th").
* **Range** (`low_s`/`high_s`) uses a ~90 % band built from lap-to-lap noise,
  the slope's uncertainty and 0.5 s per lap spent on a different compound
  (base pace gap not modelled), and is **never narrower than +-1.5 s** (also for the
  real lap, whose gain is exactly 0). For laps under a safety car the upper
  bound is raised by 40 % of the pit loss, since a stop then costs much less
  than we assume. Options stay contiguous; SC laps are not hidden.

*Limits:* tyre wear is not really a straight line (there are "cliffs"), a fresh
set of a different compound is not equally fast, drivers stuck in traffic
change their own wear, and other teams would have reacted. Large gains
(> 10 s) deserve scepticism, they come from extrapolating one slope over many
laps. This is an explainer for fans, **not a strategy simulator**.

## Compare two drivers (`compare.json`)

We take one lap where two drivers appear in the top moment (or the winner and
runner-up on the winner's fastest clean lap if no moment applies; both are
written when they differ). Position samples for that lap come from OpenF1
`location`. The circuit outline is that lap's path of the first driver. Both
paths and the outline use one shared scale (aspect ratio kept, longer side
0..1) and are written ready for screens where **y points down** (y is flipped,
`y_svg = 1 - y_scaled`). The track is cut into 20 equal-distance segments; for
each one we compare how long each car needed to cover it and mark the quicker
one. `key_moment_frac` is the point in the lap where the gap grew the most (or
null if no segment differed by > 0.05 s). The summary compares the slowest and
fastest 40 % of segments. Each path has at most 400 points.

Each path is first cut to exactly `[0, lap_time]`: the position at the start line
(t = 0) and at the flag (t = lap time) is interpolated from the neighbouring
samples (the feed is fetched half a second beyond both ends), and a path that
misses the line by more than 0.6 s is refused. So both paths start together and
end at their official lap times, and the 20 segment times add up to
`lap_time_b - lap_time_a`.

The page is a **ghost-lap comparison**: both laps are replayed from t = 0, as if
two ghost cars started side by side, so the readout is not their real gap on
track and the page says so. The readout takes the leader's share of his own lap
distance, asks how long each car needed to reach that point, and calls a car the
leader only after a 0.10 s lead (kept until the gap is under 0.05 s) so noise does
not flip it. It is computed on a fixed time grid, so it depends only on the
playhead position. After both laps it equals the lap-time difference exactly.
The marker is always "Key moment".

*Limits:* distance is measured along each car's own path, so a different racing
line shifts the segment boundaries slightly; differences under ~0.05 s per
segment are within noise. When two cars are within ~0.3 s over the lap the
mid-lap leader still changes a few times (on Las Vegas lap 50: 27 raw flips, 5
with hysteresis); that is real closeness plus noise, not something a better
projection removes (projecting onto the track outline gave the same count).

## Text

Every inferred cause is hedged ("probably", "likely", "maybe", "may be") on the
site and on the social images: pills, zone labels, captions, titles, finish
estimate. `build` refuses to write a moment whose label or caption is not
hedged. Race dates are the local date at the circuit (UTC start + `gmt_offset`).

All user-facing text uses last names, short sentences, and no insider words
("clipping", position codes like "P5", "undercut", "DRS", "delta", "stint"...).
`build` checks every headline, label, caption, chart title and summary against
a banned-word list and against bare three-letter driver codes, and refuses to
write the files if one slips through.

## Reproducing

```
python -m slipstream build --year 2026 --round 21     # or --latest
python -m slipstream social --race 2026-21-las-vegas [--video]
python -m pytest -q
```
Rounds count non-cancelled races in date order (so cancelled Bahrain and
Jeddah in 2026 do not shift the numbering: Las Vegas is round 21, Budapest 11).
