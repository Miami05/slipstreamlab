"""Sustained loss of straight-line speed: "had less power for most of the race (probably a car problem)".

A battery that runs low costs speed for a few laps; an engine turned down (heat, a fault) costs
speed on most laps. So for every driver we compare his top speed on the straights with his
TEAMMATE's (same lap, so wind and track state cancel) and with the field's median:

* per lap and straight: peak speed minus the median peak speed of all drivers on that lap;
  averaged over the straights = the lap's deviation (km/h);
* only laps where neither teammate was in traffic count (a tow adds speed, and a pair where one
  car follows and the other does not would show a deficit that is only the tow); fewer than 10
  such laps = no verdict (in Las Vegas, where 67 % of laps were in traffic, most drivers cannot
  be judged);
* from some lap onward the driver must be at least 8 km/h below his teammate on at least 70 % of
  the remaining laps (>= 10 laps), and his median must also be clearly below the field's;
* that spell must cover at least half of the driver's shared clean laps AND, in race laps, at least
  half of the distance the pair actually raced (to the last lap that shows the deficit; a car that
  retires still counts), with no hole between clean laps longer than a quarter of that distance:
  a deficit that only shows in the last 10 laps, or one early lap plus a late spell, is not
  "most of the race";
* without intervals data traffic cannot be filtered, so there is no verdict at all.

Any `battery_low` finding of that driver from that lap on is then relabelled `power_cut`.

Limits: if both cars of a team lose power (a team-wide setting) the teammate comparison cannot see
it; the field comparison alone is too noisy (set-up differences are bigger than the loss).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MIN_DROP_KMH = 8.0        # below the teammate by at least this much ...
MIN_SHARE = 0.7           # ... on at least this share of the remaining laps
MIN_LAPS = 10             # remaining laps needed
MIN_COVERAGE = 0.5        # the spell must cover at least this share of the shared clean laps
MIN_FIELD_DROP_KMH = 4.0  # and the median deviation from the field at least this negative
MIN_FIELD_CARS = 6        # cars needed to define the field's median on a lap and straight
MIN_STRAIGHTS = 2         # straights needed to average a lap's deviation
STRONG_SHARE = 0.85       # share and drop for "medium" instead of "low" confidence
STRONG_DROP_KMH = 12.0


@dataclass
class PowerCut:
    driver: int
    mate: int
    start_lap: int
    n_laps: int
    share: float             # share of those laps at least MIN_DROP_KMH below the teammate
    vs_mate: float           # median deviation from the teammate (km/h, negative = slower)
    vs_field: float          # median deviation from the field (km/h, negative = slower)

    @property
    def confidence(self) -> str:
        return "medium" if self.share >= STRONG_SHARE and self.vs_mate <= -STRONG_DROP_KMH else "low"


def lap_deviation(feats: dict, straights) -> dict[tuple[int, int], float]:
    """{(driver, lap): mean over straights of (peak speed - median peak speed of the field)}."""
    peaks: dict[tuple[int, int], dict[int, float]] = {}      # (lap, straight) -> {driver: peak speed}
    for (dn, lap, si), v in feats.items():
        c = v.get("clip")
        if c:
            peaks.setdefault((lap, si), {})[dn] = c["peak_speed"]
    per: dict[tuple[int, int], list[float]] = {}
    for (lap, si), by in peaks.items():
        if len(by) < MIN_FIELD_CARS:
            continue
        med = float(np.median(list(by.values())))
        for dn, pk in by.items():
            per.setdefault((dn, lap), []).append(pk - med)
    return {k: float(np.mean(v)) for k, v in per.items() if len(v) >= MIN_STRAIGHTS}


MAX_GAP_SHARE = 0.25   # clean laps inside the spell may not leave a hole longer than this share of the race


def _spans_most_of_race(laps: list[int], low: np.ndarray, race_len: int) -> bool:
    """From laps[0] to the last lap that shows the deficit: covers >= MIN_COVERAGE of the laps raced,
    and the clean laps in between are spread out (no gap > MAX_GAP_SHARE of the race)."""
    if not low.any() or not race_len:
        return False
    last = int(np.flatnonzero(low)[-1])
    span_laps = laps[: last + 1]
    if span_laps[-1] - span_laps[0] + 1 < MIN_COVERAGE * race_len:
        return False
    gaps = np.diff(span_laps) if len(span_laps) > 1 else np.array([0])
    return int(gaps.max()) <= MAX_GAP_SHARE * race_len


def detect_driver(dn: int, mate: int, dev: dict, skip=frozenset(), total_laps: int | None = None) -> PowerCut | None:
    # Clean air only: a car in a tow is faster in a straight line, so a pair where one car follows
    # and the other does not would show a deficit that is only the tow. Too few clean laps = no verdict.
    laps = sorted(l for (d, l) in dev if d == dn and (mate, l) in dev
                  and (dn, l) not in skip and (mate, l) not in skip)
    if len(laps) < MIN_LAPS:
        return None
    # The distance this pair actually raced (a car that retires with an engine problem must still count).
    raced = [l for (d, l) in dev if d in (dn, mate)]
    race_len = min(total_laps, max(raced)) if (total_laps and raced) else total_laps
    mine = np.array([dev[(dn, l)] for l in laps])
    diff = mine - np.array([dev[(mate, l)] for l in laps])
    low = diff <= -MIN_DROP_KMH
    need = max(MIN_LAPS, int(np.ceil(MIN_COVERAGE * len(laps))))
    for i in range(0, len(laps) - need + 1):
        if not low[i]:
            continue                        # the spell starts on a lap that shows the deficit
        if total_laps and not _spans_most_of_race(laps[i:], low[i:], race_len):
            continue                        # "most of the race" must hold in race laps, not just clean laps
        if low[i:].mean() >= MIN_SHARE and float(np.median(mine[i:])) <= -MIN_FIELD_DROP_KMH:
            return PowerCut(dn, mate, int(laps[i]), len(laps) - i, float(low[i:].mean()),
                            float(np.median(diff[i:])), float(np.median(mine[i:])))
    return None


def detect(dev: dict, mates: dict[int, int | None], skip=frozenset(), total_laps: int | None = None) -> dict[int, PowerCut]:
    out = {}
    for dn in sorted({d for d, _ in dev}):
        mate = mates.get(dn)
        if mate is None:
            continue
        pc = detect_driver(dn, mate, dev, skip, total_laps)
        if pc:
            out[dn] = pc
    return out


def apply(rd, findings: list, ctx: dict, traffic: dict | None = None) -> dict[int, PowerCut]:
    """Mark battery_low findings that fall inside a sustained power loss (finding.detail["power_cut"])."""
    if not ctx.get("feats"):
        return {}
    if traffic is None:
        # No intervals: tow laps cannot be left out, and a tow looks exactly like a power deficit.
        ctx["power_cuts"] = {}
        return {}
    dev = lap_deviation(ctx["feats"], ctx["straights"])
    skip = {k for k, v in traffic.items() if v >= 0.5}
    mates = {int(d): rd.teammate(int(d)) for d in rd.drivers.index}
    cuts = detect(dev, mates, skip, getattr(rd, "total_laps", None))
    ctx["power_cuts"] = cuts
    order = {"low": 0, "medium": 1, "high": 2}
    for f in findings:
        pc = cuts.get(f.driver)
        if f.kind == "battery_low" and pc and f.lap >= pc.start_lap:
            f.detail["power_cut"] = {"start_lap": pc.start_lap, "share": pc.share, "vs_mate": pc.vs_mate,
                                     "vs_field": pc.vs_field, "n_laps": pc.n_laps, "mate": pc.mate}
            if order[pc.confidence] < order[f.confidence]:
                f.confidence = pc.confidence
            f.why += (f"; he was also slower than his teammate by about {abs(pc.vs_mate):.0f} km/h on "
                      f"{pc.share:.0%} of the clean laps from lap {pc.start_lap}, which probably points to a car problem")
    return cuts
