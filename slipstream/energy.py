"""Energy findings: "battery ran low" and "saved energy".

What we can and cannot see
--------------------------
OpenF1 gives speed, throttle, brake, gear and RPM at about 3.7 Hz. There is
NO battery channel. So we look for the *footprint* of an empty battery:

* battery_low ("clipping"): on a straight the driver holds the accelerator at
  ~100 % yet the car stops accelerating and even slows down before the braking
  point.
* energy_saving ("lift and coast"): the driver comes off the accelerator well
  before the braking point without touching the brake, to harvest energy.

Both are compared with two baselines, so we never flag normal behaviour:
  1. the circuit baseline: what the median driver does on that straight/zone
     (some tracks make everybody lose speed on every lap);
  2. the driver's own baseline: his median over his clean laps.
A finding must beat BOTH by a clear margin.

Laps under safety car / VSC, yellow flags, the restart lap, lap 1 and pit
in/out laps are never used; neither are wet laps (and 2 after), laps under a blue flag
(being lapped) and the lap of the chequered flag (model.analysis_lap; for blue-flag laps only the lift features are dropped).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import conditions, model

NBINS = 1000
FULL = 98.0                 # throttle % counted as "flat out"
MIN_STRAIGHT_M = 350.0      # shortest flat-out stretch we call a straight
MIN_FALL_KMH = 8.0          # speed lost at full throttle that we care about
MIN_EXCESS_KMH = 6.0        # ... and how much more than both baselines
MIN_LIFT_S = 1.0            # coasting time we care about
MIN_LIFT_EXCESS_S = 0.55    # ... and how much more than both baselines
MIN_BASELINE_LAPS = 6
MAX_STEP_DROP = 15.0        # km/h lost in one sample = braking/glitch, not clipping
PERSISTENT_SHARE = 0.5      # loss on at least this share of his clean laps there: mentioned in `why` (information only)
MIN_HIGH_LAPS = 5           # "high" confidence needs at least this many flagged laps of evidence
MIN_POST_SAMPLES = 3        # samples after the peak (~0.8 s) needed to judge a fall


@dataclass
class Straight:
    idx: int
    fs: float               # start (fraction of lap distance)
    fe: float               # end of flat-out (median)
    fb: float               # braking point (median)
    length_m: float


@dataclass
class Finding:
    kind: str               # battery_low | energy_saving
    driver: int
    lap: int                # strongest lap
    laps: list[int]         # all flagged laps (this driver, this place)
    straight: int
    score: float            # km/h (battery_low) or seconds (energy_saving) beyond baselines
    confidence: str
    why: str
    detail: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------
# Straights
# ----------------------------------------------------------------------------

def _rising(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.int8)
    return np.flatnonzero(np.diff(m, prepend=0) == 1)


def telemetry_quality(rd: model.RaceData) -> float:
    """Share of consecutive speed samples that are exactly equal during the race.

    ~0.15-0.30 is normal. Above ~0.5 the speed channel was updated far too
    rarely (seen in the Hungary 2026 feed), so clipping/coasting cannot be
    measured honestly.
    """
    vals = []
    end = float(np.nanmax(rd.laps.t1))
    for dn in list(rd.drivers.index)[:6]:
        tele = rd.telemetry(int(dn))
        if not tele:
            continue
        m = (tele["t"] >= rd.race_start) & (tele["t"] <= end)
        if m.sum() > 100:
            vals.append(float((np.diff(tele["speed"][m]) == 0).mean()))
    return float(np.median(vals)) if vals else 1.0


MAX_REPEATED_SPEED = 0.5


def collect_traces(rd: model.RaceData) -> dict:
    """{(driver, lap): trace} for every usable lap."""
    traces = {}
    for r in rd.laps.itertuples():
        if not model.analysis_lap(r, blue=False):
            continue
        tr = rd.lap_trace(int(r.driver_number), int(r.lap_number))
        if tr is not None:
            traces[(int(r.driver_number), int(r.lap_number))] = tr
    return traces


def find_straights(traces: dict, min_length_m: float = MIN_STRAIGHT_M) -> list[Straight]:
    """Long flat-out stretches shared by most laps, found from the data itself."""
    if not traces:
        return []
    grid = (np.arange(NBINS) + 0.5) / NBINS
    full = np.zeros(NBINS)
    dists = []
    for tr in traces.values():
        full += np.interp(grid, tr["frac"], tr["throttle"]) >= FULL
        dists.append(tr["dist"][-1])
    full /= len(traces)
    lap_m = float(np.median(dists))
    on = full >= 0.5
    # fill small holes (< 1 % of the lap)
    runs = _runs(on)
    merged = []
    for a, b in runs:
        if merged and a - merged[-1][1] <= 10:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    # a run that wraps around the start/finish line
    if len(merged) > 1 and merged[0][0] == 0 and merged[-1][1] == NBINS:
        a0, b0 = merged.pop(0)
        merged[-1] = (merged[-1][0], NBINS)
        merged.insert(0, (a0, b0))      # keep both halves; the wrap is rare and handled as two
    out = []
    for a, b in merged:
        length = (b - a) / NBINS * lap_m
        if length < min_length_m:
            continue
        fs, fe = a / NBINS, b / NBINS
        out.append(Straight(len(out), fs, fe, fe, length))
    # braking point per straight: median first brake onset after the flat-out end
    for s in out:
        onsets = []
        for tr in traces.values():
            idx = _rising(tr["brake"])
            hit = [tr["frac"][i] for i in idx if s.fe - 0.01 <= tr["frac"][i] <= s.fe + 0.08]
            if hit:
                onsets.append(hit[0])
        if len(onsets) >= 0.3 * len(traces):
            s.fb = float(np.median(onsets))
    return out


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    m = np.concatenate([[0], mask.astype(np.int8), [0]])
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


# ----------------------------------------------------------------------------
# Per-lap features
# ----------------------------------------------------------------------------

def clip_feature(tr: dict, s: Straight) -> dict | None:
    """Speed lost at full throttle on one straight, for one lap."""
    f, thr, sp = tr["frac"], tr["throttle"], tr["speed"]
    lo, hi = s.fs - 0.01, s.fe + 0.02
    sel = np.flatnonzero((f >= lo) & (f <= hi))
    if len(sel) < 8:
        return None
    flat = thr[sel] >= FULL
    runs = _runs(flat)
    if not runs:
        return None
    a, b = max(runs, key=lambda r: r[1] - r[0])
    i0, i1 = sel[a], sel[b - 1]
    # stop before the braking point and at any single-step crash in speed
    # (>15 km/h in one sample is braking or a stale-data jump, not clipping)
    stop = np.flatnonzero(f[i0:i1 + 1] > s.fb - 0.004)
    if len(stop):
        i1 = i0 + max(int(stop[0]) - 1, 0)
    drops = np.flatnonzero(np.diff(sp[i0:i1 + 1]) < -MAX_STEP_DROP)
    if len(drops):
        i1 = i0 + int(drops[0])
    if i1 - i0 < 6 or np.max(np.diff(tr["t"][i0:i1 + 1])) > 1.0:
        return None
    seg = sp[i0:i1 + 1]
    pk = int(np.argmax(seg))
    post = len(seg) - 1 - pk
    fall = float(seg[pk] - seg[pk:].min()) if post >= MIN_POST_SAMPLES else 0.0
    span = max(s.fb - s.fs, 1e-6)
    return {
        "fall": fall,
        "peak_speed": float(seg[pk]),
        "end_speed": float(seg[-1]),
        "peak_x": float((f[i0 + pk] - s.fs) / span),
        "end_x": float((f[i1] - s.fs) / span),
        "run_frac": float(f[i1] - f[i0]),
    }


def lift_feature(tr: dict, s: Straight) -> dict | None:
    """Time coasting (throttle ~0, no brake) just before the braking point."""
    f, thr, brk, t, sp = tr["frac"], tr["throttle"], tr["brake"], tr["t"], tr["speed"]
    idx = _rising(brk)
    cand = [i for i in idx if abs(f[i] - s.fb) <= 0.025]
    if not cand:
        return None
    e = min(cand, key=lambda i: abs(f[i] - s.fb))
    j = e - 1
    while j > 0 and thr[j] < 10 and not brk[j]:
        j -= 1
    cs = j + 1                                   # first coasting sample
    dur = float(t[e] - t[cs])
    # only count if the driver was really on the power just before
    before = (t >= t[cs] - 1.0) & (t <= t[cs])
    if dur > 0 and thr[before].max() < 90:
        dur = 0.0
    span = max(s.fb - s.fs, 1e-6)
    return {"lift_s": dur, "coast_x": float((f[cs] - s.fs) / span),
            "brake_x": float((f[e] - s.fs) / span),
            "speed_lost": float(sp[cs] - sp[e])}


# ----------------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------------

def detect(rd: model.RaceData, traces: dict | None = None, straights: list[Straight] | None = None,
           gaps: dict | None = None, traffic: dict | None = None) -> tuple[list[Finding], dict]:
    """Return (findings, context). `context` carries traces/straights for charts."""
    quality = telemetry_quality(rd)
    if quality > MAX_REPEATED_SPEED:
        return [], {"traces": {}, "straights": [], "feats": {}, "skipped": f"speed channel too coarse ({quality:.0%} repeated samples)"}
    traces = traces if traces is not None else collect_traces(rd)
    straights = straights if straights is not None else find_straights(traces)
    ctx = {"traces": traces, "straights": straights}
    feats = {}   # (dn, lap, straight) -> {"clip":.., "lift":..}
    for (dn, lap), tr in traces.items():
        for s in straights:
            c = clip_feature(tr, s)
            l = lift_feature(tr, s)
            if c or l:
                feats[(dn, lap, s.idx)] = {"clip": c, "lift": l}
    # A driver being lapped (blue flag) eases off to let the leader by: no lift finding from those laps.
    blue = ({(int(d), int(l)) for d, l, b in zip(rd.laps.driver_number, rd.laps.lap_number, rd.laps.blue) if b}
            if "blue" in rd.laps else set())
    drop_blue_lifts(feats, blue)
    ctx["feats"] = feats
    findings = detect_from_features(rd, feats, straights, gaps=gaps, traffic=traffic)
    return findings, ctx


def drop_blue_lifts(feats: dict, blue: set) -> None:
    """A driver under a blue flag (being lapped) eases off to let the leader by: remove the lift
    feature of those laps (his full-throttle speed fall is kept, that is not a driver action)."""
    for (dn, lap, si), v in feats.items():
        if v["lift"] and (dn, lap) in blue:
            v["lift"] = None


def detect_from_features(rd, feats: dict, straights: list[Straight], gaps: dict | None = None,
                         traffic: dict | None = None) -> list[Finding]:
    """`traffic` = {(driver, lap): share of the lap < 1.5 s behind a car} (OpenF1 intervals) or None.
    With it, a lift in traffic is one confidence step lower; without it the lap-timestamp proxy `gaps` is used."""
    findings: list[Finding] = []
    drivers = sorted({k[0] for k in feats})
    for s in straights:
        # ---- circuit baselines -------------------------------------------
        falls = [v["clip"]["fall"] for k, v in feats.items() if k[2] == s.idx and v["clip"]]
        lifts = [v["lift"]["lift_s"] for k, v in feats.items() if k[2] == s.idx and v["lift"]]
        field_fall = float(np.median(falls)) if falls else 0.0
        field_lift = float(np.median(lifts)) if lifts else 0.0
        for dn in drivers:
            mine = {k[1]: v for k, v in feats.items() if k[0] == dn and k[2] == s.idx}
            # ---------- battery_low ---------------------------------------
            f_laps = {lap: v["clip"]["fall"] for lap, v in mine.items() if v["clip"]}
            if len(f_laps) >= MIN_BASELINE_LAPS:
                own = float(np.median(list(f_laps.values())))
                base = max(field_fall, own)
                flagged = {lap: fall - base for lap, fall in f_laps.items()
                           if fall >= MIN_FALL_KMH and fall - base >= MIN_EXCESS_KMH}
                share = float(np.mean([fall >= MIN_FALL_KMH and fall - field_fall >= MIN_EXCESS_KMH
                                       for fall in f_laps.values()]))
                findings += _group(rd, "battery_low", dn, s, flagged, mine, f_laps, base, field_fall, own, gaps,
                                   persistent_share=share)
            # ---------- energy_saving -------------------------------------
            l_laps = {lap: v["lift"]["lift_s"] for lap, v in mine.items() if v["lift"]}
            if len(l_laps) >= MIN_BASELINE_LAPS:
                own = float(np.median(list(l_laps.values())))
                base = max(field_lift, own)
                flagged = {lap: x - base for lap, x in l_laps.items()
                           if x >= MIN_LIFT_S and x - base >= MIN_LIFT_EXCESS_S}
                findings += _group(rd, "energy_saving", dn, s, flagged, mine, l_laps, base, field_lift, own, gaps,
                                   traffic=traffic)
    return findings


def _group(rd, kind, dn, s, flagged: dict, mine: dict, raw: dict, base, field_base, own, gaps,
           persistent_share: float = 0.0, traffic: dict | None = None) -> list[Finding]:
    """Turn flagged laps into findings; neighbouring laps become one finding."""
    if not flagged:
        return []
    laps = sorted(flagged)
    groups, cur = [], [laps[0]]
    for lap in laps[1:]:
        if lap - cur[-1] <= 2:       # a lap in between may simply be unusable
            cur.append(lap)
        else:
            groups.append(cur)
            cur = [lap]
    groups.append(cur)
    out = []
    unit = "km/h" if kind == "battery_low" else "s"
    for g in groups:
        best = max(g, key=lambda l: flagged[l])
        score = float(flagged[best])
        n = len(g)
        if kind == "battery_low":
            pts = 2 if score >= 25 else (1 if score >= 15 else 0)
        else:
            pts = 2 if score >= 1.5 else (1 if score >= 1.0 else 0)
        pts += (1 if n >= 2 else 0) + (1 if n >= 4 else 0)
        conf = "high" if pts >= 3 else ("medium" if pts == 2 else "low")
        notes = []
        gap = None
        in_traffic = None
        if kind == "energy_saving" and traffic is not None:
            # Intervals say whether he was right behind a car on this lap: lifting then is no
            # surprise. One step lower (not a hard cap); this replaces the lap-timestamp proxy.
            share = traffic.get((dn, best))
            in_traffic = conditions.in_traffic(share)
            if in_traffic:
                notes.append("he was close behind another car, which can explain the lift")
                conf = _LOWER[conf]
        elif gaps is not None:
            gap = gaps.get((dn, best))
            if gap is not None and gap < 1.0:
                notes.append("another car was close ahead, which can explain it")
                conf = _cap(conf, "medium" if gap >= 0.5 else "low")
        if conf == "high" and n < MIN_HIGH_LAPS:
            conf = "medium"            # too few flagged laps of evidence for "high"
        if len(raw) < 10:
            conf = _cap(conf, "medium")
        persistent = kind == "battery_low" and persistent_share >= PERSISTENT_SHARE
        if kind == "battery_low":
            # The power loss is measured, but its cause cannot be told apart from e.g. an
            # engine-temperature power cut: never "high".
            conf = _cap(conf, "medium")
        if kind == "battery_low":
            val = raw[best]
            why = (f"accelerator flat out, yet speed fell {val:.0f} km/h before the braking point "
                   f"({score:.0f} km/h more than usual for him and for the field)")
        else:
            val = raw[best]
            why = (f"coasted for {val:.1f} s before braking "
                   f"({score:.1f} s more than usual for him and for the field)")
        if n >= 2:
            why += f", repeated on {n} laps in a short spell"
        if persistent:
            # Informational only: repeated loss can be normal on an energy-starved track, or a car problem.
            why += (f"; it happened on most laps ({persistent_share:.0%} of his clean laps there), "
                    "which can also mean a car problem")
        if notes:
            why += "; " + notes[0]
        out.append(Finding(kind, dn, best, g, s.idx, score, conf, why,
                           {"value": float(val), "base": float(base), "field": float(field_base),
                            "own": float(own), "unit": unit, "n_laps": n, "gap_ahead": gap, "in_traffic": in_traffic,
                            "persistent": persistent, "persistent_share": float(persistent_share)}))
    return out


_ORDER = {"low": 0, "medium": 1, "high": 2}
_LOWER = {"high": "medium", "medium": "low", "low": "low"}


def _cap(conf: str, cap: str) -> str:
    return conf if _ORDER[conf] <= _ORDER[cap] else cap


def gap_ahead_table(rd: model.RaceData) -> dict:
    """{(driver, lap): seconds to the car in front when this lap started/ended (min)}.

    Cheap proxy from lap timestamps: cars crossing the line in order.
    """
    out = {}
    laps = rd.laps
    for lap, grp in laps.groupby("lap_number"):
        g = grp[np.isfinite(grp.t0)].sort_values("t0")
        t = g.t0.to_numpy()
        dn = g.driver_number.to_numpy()
        for i in range(1, len(t)):
            out[(int(dn[i]), int(lap))] = float(t[i] - t[i - 1])
    # also use the gap at the end of the lap (= start of the next one)
    res = {}
    for (dn, lap), v in out.items():
        nxt = out.get((dn, lap + 1))
        res[(dn, lap)] = min(v, nxt) if nxt is not None else v
    return res
