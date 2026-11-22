"""Race conditions that make a lap unfit for tyre and energy analysis, or explain a slow lap.

* rain      - OpenF1 `weather` (rainfall flag), cars on intermediate/wet tyres, and the laps
              just before a mass switch to rain tyres, when the track was already getting wet.
              A wet lap and the 2 laps after it are left out ("wet").
* blue flag - a blue flag shown to a driver (he is being lapped): his laps +-1 are left out.
* finish    - the lap on which the chequered flag came out (cars slow down at the end).
* traffic   - share of a lap spent less than 1.5 s behind the car in front (OpenF1 `intervals`).
              Traffic does not remove a lap; it is evidence that explains slow laps.

None of this changes `model.usable_lap` (the what-if and compare screens use that); the
analysis modules ask `model.analysis_lap`, which also respects wet / blue / finish.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRAFFIC_GAP_S = 1.5          # closer than this to the car in front = "in traffic"
TRAFFIC_LAP_SHARE = 0.5      # share of a lap spent that close = the lap counts as "in traffic"
INTERVAL_MAX_AGE_S = 60.0    # an interval older than this is unknown, not "still true"
TRAFFIC_SAMPLES = 12         # points per lap where we read the interval

WET_COMPOUNDS = ("INTERMEDIATE", "WET")
WET_FLEET_SHARE = 0.25       # this share of the running cars on rain tyres = wet for everybody
RAIN_AFTER_LAPS = 2          # laps still left out after the rain
RUNUP_MAX_LAPS = 12          # how far before a mass switch to rain tyres we look for the first wet laps
RUNUP_RISE_S = 1.0           # field pace this much slower than before = the track was already wet
RAIN_SAMPLE_S = 180.0        # a weather sample is valid for at most this long


def is_wet_compound(c) -> bool:
    return str(c).upper() in WET_COMPOUNDS


def _runs(laps) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for lap in sorted(set(int(x) for x in laps)):
        if out and lap == out[-1][1] + 1:
            out[-1][1] = lap
        else:
            out.append([lap, lap])
    return [(a, b) for a, b in out]


# ----------------------------------------------------------------------------
# Rain
# ----------------------------------------------------------------------------

def rain_windows(weather: pd.DataFrame | None, t_lo: float, t_hi: float) -> list[tuple[float, float]]:
    """(start, end) epoch windows inside [t_lo, t_hi] during which the rainfall flag was on.

    A weather sample (about one a minute) holds until the next sample, at most RAIN_SAMPLE_S.
    """
    if weather is None or len(weather) == 0 or "rainfall" not in weather or "epoch" not in weather:
        return []
    w = weather.sort_values("epoch")
    t = w.epoch.to_numpy(float)
    rain = pd.to_numeric(w.rainfall, errors="coerce").fillna(0).to_numpy(float) > 0
    out: list[tuple[float, float]] = []
    for i in np.flatnonzero(rain):
        a = t[i]
        b = min(t[i + 1] if i + 1 < len(t) else a + RAIN_SAMPLE_S, a + RAIN_SAMPLE_S)
        a, b = max(a, t_lo), min(b, t_hi)
        if b > a:
            if out and a <= out[-1][1] + 1e-6:
                out[-1] = (out[-1][0], max(out[-1][1], b))
            else:
                out.append((a, b))
    return out


def fleet_wet_laps(stints: pd.DataFrame, total_laps: int, share: float = WET_FLEET_SHARE) -> set[int]:
    """Lap numbers on which at least `share` of the cars that were running were on rain tyres."""
    if stints is None or len(stints) == 0:
        return set()
    out = set()
    st = stints.to_dict("records")
    for lap in range(1, int(total_laps) + 1):
        running = [s for s in st if s["lap_start"] <= lap <= s["lap_end"]]
        if running and sum(is_wet_compound(s["compound"]) for s in running) / len(running) >= share:
            out.add(lap)
    return out


def field_pace(laps: pd.DataFrame) -> dict[int, float]:
    """Median lap time of the green, non-pit laps of each lap number (raw seconds)."""
    ok = laps[~laps.neutral & ~laps.pit_in & ~laps.pit_out & (laps.lap_number > 1) & np.isfinite(laps.dur)]
    return {int(k): float(v) for k, v in ok.groupby("lap_number").dur.median().items()}


def runup_laps(wet: set[int], pace: dict[int, float]) -> set[int]:
    """Laps just before each wet spell in which the field already slowed down (rain, no rain tyres yet).

    For every wet spell that starts after the first laps of the race we walk back from the lap
    before it while the field's median lap time is at least RUNUP_RISE_S above its earlier level
    (the 25th percentile of the RUNUP_MAX_LAPS laps before the spell).
    """
    out: set[int] = set()
    for a, _ in _runs(wet):
        if a <= 3:
            continue                    # the race simply started wet
        ref_laps = [pace[l] for l in range(max(2, a - RUNUP_MAX_LAPS - 3), a) if l in pace]
        if len(ref_laps) < 5:
            continue
        ref = float(np.percentile(ref_laps, 25))
        lap = a - 1
        while lap >= max(2, a - RUNUP_MAX_LAPS) and lap in pace and pace[lap] >= ref + RUNUP_RISE_S:
            out.add(lap)
            lap -= 1
    return out


def mark_rain(laps: pd.DataFrame, stints: pd.DataFrame, total_laps: int, windows) -> set[int]:
    """Set laps["rain"] (the lap was wet) and laps["wet"] (wet, or within RAIN_AFTER_LAPS after a wet lap).

    Returns the fleet-wide wet lap numbers (rain tyres or run-up).
    """
    fleet = fleet_wet_laps(stints, total_laps)
    fleet |= runup_laps(fleet, field_pace(laps))
    own = set()
    for r in stints.itertuples():
        if is_wet_compound(r.compound):
            own |= {(int(r.driver_number), l) for l in range(int(r.lap_start), int(r.lap_end) + 1)}
    rain = []
    for r in laps.itertuples():
        w = (int(r.lap_number) in fleet or (int(r.driver_number), int(r.lap_number)) in own
             or (np.isfinite(r.t0) and np.isfinite(r.t1) and any(min(r.t1, b) > max(r.t0, a) for a, b in windows)))
        rain.append(bool(w))
    laps["rain"] = rain
    wet = laps.rain.to_numpy().copy()
    for dn, grp in laps.groupby("driver_number"):
        idx = grp.sort_values("lap_number").index.to_numpy()
        flags = laps.loc[idx, "rain"].to_numpy()
        ext = flags.copy()
        for k in range(1, RAIN_AFTER_LAPS + 1):
            ext[k:] |= flags[:-k]
        wet[laps.index.get_indexer(idx)] = ext
    laps["wet"] = wet
    return fleet


# ----------------------------------------------------------------------------
# Blue flags and the chequered flag
# ----------------------------------------------------------------------------

def blue_flag_laps(rc: pd.DataFrame, laps: pd.DataFrame, spread: int = 1) -> set[tuple[int, int]]:
    """{(driver, lap)}: laps (+- `spread`) in which that driver was shown a blue flag.

    The message's lap_number is the race leader's lap, so the time stamp is matched to the
    flagged driver's own lap instead.
    """
    if rc is None or len(rc) == 0 or "driver_number" not in rc:
        return set()
    flag = rc["flag"].astype(str).str.upper() if "flag" in rc else pd.Series("", index=rc.index)
    msg = rc["message"].astype(str).str.upper() if "message" in rc else pd.Series("", index=rc.index)
    sel = rc[(flag.str.contains("BLUE") | msg.str.contains("BLUE FLAG")) & rc.driver_number.notna()]
    out: set[tuple[int, int]] = set()
    by_driver = {int(dn): g for dn, g in laps.groupby("driver_number")}
    for r in sel.itertuples():
        g = by_driver.get(int(r.driver_number))
        if g is None or not np.isfinite(r.epoch):
            continue
        hit = g[(g.t0 <= r.epoch) & (g.t1 > r.epoch)]
        if not len(hit):
            continue
        lap = int(hit.lap_number.iloc[0])
        out |= {(int(r.driver_number), l) for l in range(lap - spread, lap + spread + 1)}
    return out


def chequered_time(rc: pd.DataFrame) -> float | None:
    if rc is None or len(rc) == 0 or "flag" not in rc:
        return None
    hit = rc[rc["flag"].astype(str).str.upper() == "CHEQUERED"]
    return float(hit.epoch.min()) if len(hit) else None


# ----------------------------------------------------------------------------
# Traffic
# ----------------------------------------------------------------------------

def prepare_intervals(raw: pd.DataFrame | None) -> pd.DataFrame | None:
    """OpenF1 intervals -> columns driver_number, t (epoch), gap (s, NaN if unknown), leader (bool)."""
    if raw is None or len(raw) == 0 or "interval" not in raw or "date" not in raw:
        return None
    from .model import epoch_seconds
    df = pd.DataFrame({
        "driver_number": pd.to_numeric(raw.driver_number, errors="coerce"),
        "t": epoch_seconds(raw["date"]),
        # "+1 LAP" and None are not numbers: unknown, which is not the same as close
        "gap": pd.to_numeric(raw["interval"], errors="coerce"),
        "leader": (pd.to_numeric(raw["gap_to_leader"], errors="coerce") == 0) if "gap_to_leader" in raw else False,
    })
    df = df[df.driver_number.notna() & np.isfinite(df.t)].copy()
    df["driver_number"] = df.driver_number.astype(int)
    return df.sort_values(["driver_number", "t"]).reset_index(drop=True)


def traffic_share(intervals: pd.DataFrame | None, laps: pd.DataFrame,
                  gap_s: float = TRAFFIC_GAP_S, max_age: float = INTERVAL_MAX_AGE_S,
                  samples: int = TRAFFIC_SAMPLES) -> dict[tuple[int, int], float] | None:
    """{(driver, lap): share of the lap spent less than `gap_s` behind the car in front}.

    OpenF1 only publishes an interval when it changes, so the value holds until the next row,
    but no longer than `max_age` seconds (older = unknown). The leader is never in traffic.
    """
    if intervals is None:
        return None
    out: dict[tuple[int, int], float] = {}
    for dn, iv in intervals.groupby("driver_number"):
        t, g, lead = iv.t.to_numpy(float), iv.gap.to_numpy(float), iv.leader.to_numpy(bool)
        for r in laps[laps.driver_number == dn].itertuples():
            if not (np.isfinite(r.t0) and np.isfinite(r.t1)) or r.t1 <= r.t0:
                continue
            ts = r.t0 + (np.arange(samples) + 0.5) / samples * (r.t1 - r.t0)
            i = np.searchsorted(t, ts, "right") - 1
            ok = i >= 0
            ic = np.clip(i, 0, None)
            valid = ok & ((ts - t[ic]) <= max_age) & ~lead[ic] & np.isfinite(g[ic])
            close = valid & (g[ic] < gap_s)
            out[(int(dn), int(r.lap_number))] = float(close.mean())
    return out


def in_traffic(share: float | None) -> bool:
    return share is not None and share >= TRAFFIC_LAP_SHARE


# ----------------------------------------------------------------------------
# Putting it on the race
# ----------------------------------------------------------------------------

def mark_laps(rd) -> None:
    """Add rain / wet / blue / finish / traffic to rd.laps (and rd.wet_fleet, rd.rain_windows, rd.traffic)."""
    laps = rd.laps
    t_hi = float(np.nanmax(laps.t1)) if len(laps) else float("nan")
    rd.rain_windows = rain_windows(rd.weather, rd.race_start, t_hi)
    rd.wet_fleet = mark_rain(laps, rd.stints, rd.total_laps, rd.rain_windows)
    blue = blue_flag_laps(rd.rc, laps)
    laps["blue"] = [(int(d), int(l)) in blue for d, l in zip(laps.driver_number, laps.lap_number)]
    ft = chequered_time(rd.rc)
    rd.finish_time = ft
    laps["finish"] = ([bool(ft is not None and np.isfinite(a) and np.isfinite(b) and a < ft and b >= ft - 1.0)
                       for a, b in zip(laps.t0, laps.t1)])
    rd.traffic = traffic_share(prepare_intervals(rd.intervals), laps)
    laps["traffic"] = ([rd.traffic.get((int(d), int(l)), np.nan) for d, l in zip(laps.driver_number, laps.lap_number)]
                       if rd.traffic is not None else np.nan)


def rain_laps_of(rd, driver: int) -> list[int]:
    """Lap numbers of `driver` that were wet (without the laps after the rain)."""
    g = rd.laps[(rd.laps.driver_number == driver) & rd.laps.rain]
    return sorted(int(x) for x in g.lap_number)
