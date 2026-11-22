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
  the remaining laps (>= 10 laps), and his median must also be clearly below the field's.

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


def detect_driver(dn: int, mate: int, dev: dict, skip=frozenset()) -> PowerCut | None:
    # Clean air only: a car in a tow is faster in a straight line, so a pair where one car follows
    # and the other does not would show a deficit that is only the tow. Too few clean laps = no verdict.
    laps = sorted(l for (d, l) in dev if d == dn and (mate, l) in dev
                  and (dn, l) not in skip and (mate, l) not in skip)
    if len(laps) < MIN_LAPS:
        return None
    mine = np.array([dev[(dn, l)] for l in laps])
    diff = mine - np.array([dev[(mate, l)] for l in laps])
    low = diff <= -MIN_DROP_KMH
    for i in range(0, len(laps) - MIN_LAPS + 1):
        if not low[i]:
            continue                        # the spell starts on a lap that shows the deficit
        if low[i:].mean() >= MIN_SHARE and float(np.median(mine[i:])) <= -MIN_FIELD_DROP_KMH:
            return PowerCut(dn, mate, int(laps[i]), len(laps) - i, float(low[i:].mean()),
                            float(np.median(diff[i:])), float(np.median(mine[i:])))
    return None


def detect(dev: dict, mates: dict[int, int | None], skip=frozenset()) -> dict[int, PowerCut]:
    out = {}
    for dn in sorted({d for d, _ in dev}):
        mate = mates.get(dn)
        if mate is None:
            continue
        pc = detect_driver(dn, mate, dev, skip)
        if pc:
            out[dn] = pc
    return out


def apply(rd, findings: list, ctx: dict, traffic: dict | None = None) -> dict[int, PowerCut]:
    """Mark battery_low findings that fall inside a sustained power loss (finding.detail["power_cut"])."""
    if not ctx.get("feats"):
        return {}
    dev = lap_deviation(ctx["feats"], ctx["straights"])
    skip = {k for k, v in (traffic or {}).items() if v >= 0.5}
    mates = {int(d): rd.teammate(int(d)) for d in rd.drivers.index}
    cuts = detect(dev, mates, skip)
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
