"""Tyre wear: fuel-corrected clean lap times, per-stint slopes, "tyres wore out" moments.

Method in one paragraph
-----------------------
A car starts with ~70 kg of fuel and burns it evenly, so lap times get faster
by ~0.03 s per kg even if the tyres do nothing. We remove that (fuel
correction), keep only "clean" laps (no safety car, no pit in/out laps, no
lap 1, no yellow-flag laps, nothing slower than 107 % of the stint's median)
and fit a straight line of corrected lap time against tyre age for each
stint. A "tyre wear moment" is a clear, sustained rise: the lap time climbs
well above the early-stint level and stays there until the stint ends.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import model

FUEL_START_KG = 70.0
FUEL_S_PER_KG = 0.03
SLOW_FACTOR = 1.07
MIN_FIT_LAPS = 6
MIN_WEAR_LAPS = 10
WEAR_RISE_S = 0.7          # sustained rise (s) before we call it tyre wear


def fuel_kg(lap: int, total_laps: int) -> float:
    """Fuel left at the START of this lap (burned evenly)."""
    return FUEL_START_KG * (1.0 - (lap - 1) / total_laps)


def fuel_correct(lap, dur, total_laps: int):
    """Lap time as if the tank were empty. Works on scalars or numpy arrays.

    A lighter car is faster, so earlier (heavier) laps get a bigger deduction;
    corrected times therefore rise by ~0.03 * 70 / total_laps per lap compared
    with the raw ones.
    """
    lap = np.asarray(lap, dtype=float)
    fuel = FUEL_START_KG * (1.0 - (lap - 1.0) / total_laps)
    return np.asarray(dur, dtype=float) - FUEL_S_PER_KG * fuel


def clean_laps(rd: model.RaceData) -> pd.DataFrame:
    """One row per clean lap: driver, lap, stint, compound, tyre_age, dur, corr."""
    rows = []
    st = rd.stints.sort_values(["driver_number", "stint_number"])
    for dn, grp in st.groupby("driver_number"):
        for s in grp.to_dict("records"):
            for lap in range(model.as_int(s["lap_start"]), model.as_int(s["lap_end"], model.as_int(s["lap_start"])) + 1):
                r = rd.lap_row(int(dn), lap)
                if r is None or not model.usable_lap(r) or not np.isfinite(r.dur):
                    continue
                rows.append({"driver": int(dn), "lap": lap, "stint": model.as_int(s["stint_number"]),
                             "compound": str(s["compound"]),
                             "age": model.as_int(s.get("tyre_age_at_start")) + lap - model.as_int(s["lap_start"]),
                             "dur": float(r.dur)})
    df = pd.DataFrame(rows, columns=["driver", "lap", "stint", "compound", "age", "dur"])
    if df.empty:
        df["corr"] = []
        return df
    med = df.groupby(["driver", "stint"]).dur.transform("median")
    df = df[df.dur <= SLOW_FACTOR * med].copy()
    df["corr"] = fuel_correct(df.lap.to_numpy(), df.dur.to_numpy(), rd.total_laps)
    return df.reset_index(drop=True)


@dataclass
class Fit:
    driver: int
    stint: int
    compound: str
    n: int
    slope: float          # s per lap of tyre age, fuel-corrected
    raw_slope: float      # same, without fuel correction
    se: float             # standard error of the slope
    resid_sd: float
    r2: float
    age_span: float = 0.0   # tyre ages covered by the clean laps of this stint (laps)


def stint_fits(df: pd.DataFrame, min_laps: int = MIN_FIT_LAPS) -> list[Fit]:
    out = []
    for (dn, stint), g in df.groupby(["driver", "stint"]):
        if len(g) < min_laps or g.age.nunique() < 3:
            continue
        x, y, yr = g.age.to_numpy(float), g["corr"].to_numpy(float), g.dur.to_numpy(float)
        slope, icpt = np.polyfit(x, y, 1)
        raw_slope = np.polyfit(x, yr, 1)[0]
        res = y - (slope * x + icpt)
        n = len(x)
        sd = float(res.std(ddof=2)) if n > 2 else 0.0
        sxx = float(((x - x.mean()) ** 2).sum())
        se = sd / np.sqrt(sxx) if sxx > 0 else float("inf")
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 1 - float((res ** 2).sum()) / ss_tot if ss_tot > 0 else 0.0
        out.append(Fit(int(dn), int(stint), str(g.compound.iloc[0]), n, float(slope),
                       float(raw_slope), float(se), sd, r2, float(x.max() - x.min())))
    return out


MIN_PENALTY_S = 1.0


def max_measured_penalty(fits: list[Fit]) -> float:
    """Largest tyre penalty actually measured in this race: slope x laps covered, per stint (>= 1 s).

    It bounds how much one lap can plausibly cost on worn rubber compared with fresh rubber.
    """
    vals = [max(f.slope, 0.0) * f.age_span for f in fits]
    return max([MIN_PENALTY_S] + vals)


def compound_slopes(fits: list[Fit]) -> dict:
    """{compound: (slope, se)} = median over stints of that compound, clamped >= 0."""
    by: dict = {}
    for f in fits:
        by.setdefault(f.compound, []).append(f)
    out = {}
    for c, lst in by.items():
        good = [f for f in lst if f.n >= 8] or lst
        sl = float(np.median([f.slope for f in good]))
        spread = float(np.std([f.slope for f in good])) if len(good) > 1 else 0.05
        out[c] = (max(sl, 0.0), max(spread / np.sqrt(len(good)), 0.01))
    return out


def blended_slope(fits: list[Fit], comp: dict, driver: int, compound: str) -> tuple[float, float]:
    """Field slope for this compound, blended with the driver's own if it is reliable."""
    base, se = comp.get(compound, (0.05, 0.05))
    own = [f for f in fits if f.driver == driver and f.compound == compound and f.n >= 8]
    if own:
        f = max(own, key=lambda f: f.n)
        disagree = abs(f.slope - base) / 2          # own and field slope disagree: that is uncertainty too
        return max(0.5 * base + 0.5 * f.slope, 0.0), float(np.sqrt((se / 2) ** 2 + (f.se / 2) ** 2 + disagree ** 2))
    return base, se


# ----------------------------------------------------------------------------
# Tyre wear moments
# ----------------------------------------------------------------------------

@dataclass
class WearFinding:
    kind: str
    driver: int
    lap: int                # first lap of the sustained rise
    laps: list[int]
    score: float            # rise in seconds
    confidence: str
    why: str
    detail: dict = field(default_factory=dict)


def _rolling_median3(a: np.ndarray) -> np.ndarray:
    out = np.copy(a)
    for i in range(len(a)):
        out[i] = np.median(a[max(0, i - 1): i + 2])
    return out


def detect_wear(rd: model.RaceData, df: pd.DataFrame, fits: list[Fit]) -> list[WearFinding]:
    findings = []
    field_med = df.groupby("lap")["corr"].median()
    for (dn, stint), g in df.groupby(["driver", "stint"]):
        g = g.sort_values("lap")
        if len(g) < MIN_WEAR_LAPS:
            continue
        laps = g.lap.to_numpy()
        y = g["corr"].to_numpy(float)
        base = float(np.median(y[1:5]))
        sm = _rolling_median3(y)
        hit = None
        for i in range(5, len(y) - 2):
            if all(sm[j] - base >= WEAR_RISE_S for j in range(i, len(y))):
                hit = i
                break
        if hit is None:
            continue
        # walk back to where the climb began (lap the smoothed time first left the base level)
        j = hit
        while j > 5 and sm[j - 1] - base >= 0.5 * WEAR_RISE_S:
            j -= 1
        rise = float(np.median(y[j:]) - base)      # median: one slow lap must not create a 'wear' moment
        n_after = len(y) - j
        fit = next((f for f in fits if f.driver == dn and f.stint == stint), None)
        # did everybody slow down on those laps (weather / track)? then it is not tyres
        fl = [float(field_med.get(int(l), np.nan)) for l in laps]
        fb = np.nanmedian(fl[1:5])
        f_rise = float(np.nanmean(fl[j:]) - fb)
        pts = (2 if rise >= 1.5 else 1 if rise >= 1.0 else 0) + (1 if n_after >= 8 else 0) + \
              (1 if fit and fit.r2 >= 0.5 else 0)
        conf = "high" if pts >= 3 else ("medium" if pts == 2 else "low")
        why = (f"lap times rose by about {rise:.1f} s from lap {int(laps[j])} and stayed slower "
               f"for {n_after} clean laps, after taking fuel burn into account")
        if np.isfinite(f_rise) and f_rise >= 0.5 * rise:
            conf = "low"
            why += "; other drivers also slowed down then, so it may not be only the tyres"
        findings.append(WearFinding("tyre_wear", int(dn), int(laps[j]), [int(l) for l in laps[j:]],
                                    rise, conf, why,
                                    {"stint": int(stint), "compound": str(g.compound.iloc[0]),
                                     "stint_laps": [int(laps[0]), int(laps[-1])],
                                     "base": base, "field_rise": f_rise,
                                     "slope": fit.slope if fit else None}))
    # Several cars "wearing out" on the same lap points to a common cause
    # (traffic, a restart, weather), not to tyres: be honest and lower confidence.
    for f in findings:
        same = [g for g in findings if g is not f and abs(g.lap - f.lap) <= 1]
        if len(same) >= 2 and f.confidence != "low":
            f.confidence = "low"
            f.why += "; several drivers slowed down at the same time, so it may not be only the tyres"
    return findings


def wear_chart(rd: model.RaceData, df: pd.DataFrame, f: WearFinding, rival: int | None):
    """Series for a laptime chart: driver's stint vs a rival over the same laps."""
    a, b = f.detail["stint_laps"]
    def pts(dn):
        g = df[(df.driver == dn) & (df.lap >= a) & (df.lap <= b)].sort_values("lap")
        return [[int(l), round(float(c), 3)] for l, c in zip(g.lap, g["corr"])]
    series = [(f.driver, pts(f.driver))]
    if rival is not None:
        rp = pts(rival)
        if len(rp) >= 4:
            series.append((rival, rp))
    return series


def rise_of(df: pd.DataFrame, dn: int, a: int, b: int, from_lap: int) -> float | None:
    """How much slower (s) a driver's clean, fuel-corrected laps are from `from_lap` than just before it."""
    g = df[(df.driver == dn) & (df.lap >= a) & (df.lap <= b)].sort_values("lap")
    before = g[g.lap < from_lap]["corr"].to_numpy()[1:5]
    after = g[g.lap >= from_lap]["corr"].to_numpy()
    if len(before) < 2 or len(after) < 3:
        return None
    return float(np.mean(after) - np.median(before))


def pick_rival(rd: model.RaceData, df: pd.DataFrame, dn: int, a: int, b: int,
               from_lap: int | None = None, rise: float | None = None) -> tuple[int | None, bool]:
    """Pick a comparison car. Returns (rival, teammate_slowed_too).

    Teammate first, then nearest in the standings, needing >= 6 clean laps in the window.
    A rival who slowed down just as much (>= half the rise) does not make a useful
    comparison, so he is skipped; if that rival was the teammate, we report it
    because it points to a shared cause (traffic, track, car) rather than tyres.
    """
    def overlap(d):
        return int(((df.driver == d) & (df.lap >= a) & (df.lap <= b)).sum())
    mate = rd.teammate(dn)
    pos = {d: p for d, p in model.results_table(rd)}
    order = ([mate] if mate is not None else []) + sorted(
        [d for d in rd.drivers.index if d != dn and d != mate], key=lambda d: abs(pos.get(d, 99) - pos.get(dn, 99)))
    mate_slowed = False
    for d in order:
        if overlap(d) < 6:
            continue
        if from_lap is not None and rise:
            r = rise_of(df, d, a, b, from_lap)
            if r is not None and r >= 0.5 * rise:
                mate_slowed = mate_slowed or d == mate
                continue
        return d, mate_slowed
    return None, mate_slowed
