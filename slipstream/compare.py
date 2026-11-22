"""Compare two drivers over one lap using GPS-like position data (OpenF1 `location`).

Output coordinates are ready to draw with y pointing DOWN (SVG convention):
y_svg = 1 - y_scaled, x and y share one scale so the track is not distorted
and its longer side spans 0..1.
"""
from __future__ import annotations

import numpy as np

from . import model, openf1

N_SEGMENTS = 20
MAX_POINTS = 400
TIE_S = 0.02
MAX_REPEATED_POSITION = 0.3   # share of identical consecutive samples we tolerate
LAP_MARGIN_S = 0.5            # samples fetched before the lap start / after the lap end, for interpolation
MAX_EXTRAPOLATE_S = 0.6       # a path that misses the line by more than this is refused


def fetch_lap_path(rd: model.RaceData, dn: int, lap: int):
    """(t, x, y) arrays for one driver's lap, or None."""
    r = rd.lap_row(dn, lap)
    if r is None or not (np.isfinite(r.t0) and np.isfinite(r.t1)):
        return None
    rows = openf1.get("location", {"session_key": rd.key, "driver_number": int(dn),
                                   "date>": openf1.iso_utc(r.t0 - LAP_MARGIN_S),
                                   "date<": openf1.iso_utc(r.t1 + LAP_MARGIN_S)})
    if len(rows) < 60:
        return None
    t = np.array([model.to_epoch(x["date"]) for x in rows]) - r.t0
    x = np.array([x["x"] for x in rows], float)
    y = np.array([x["y"] for x in rows], float)
    order = np.argsort(t)
    t, x, y = t[order], x[order], y[order]
    keep = ~((x == 0) & (y == 0))
    t, x, y = t[keep], x[keep], y[keep]
    if len(t) < 60:
        return None
    # Some feeds (e.g. Hungary 2026) update the position only every few seconds:
    # the "track" would be a rough polygon. Refuse rather than draw a misleading map.
    if ((np.diff(x) == 0) & (np.diff(y) == 0)).mean() > MAX_REPEATED_POSITION:
        return None
    return t, x, y


def trim_path(path, lap_time: float):
    """Cut a (t, x, y) path to exactly [0, lap_time] seconds after the lap start.

    Samples outside are dropped; the position at t=0 and at t=lap_time is interpolated
    (a few tenths of a second of missing coverage at either end are extrapolated from the
    two nearest samples). Raises ValueError if the path misses the line by more than
    MAX_EXTRAPOLATE_S: better no comparison than a misleading one.
    """
    t, x, y = (np.asarray(a, float) for a in path)
    if len(t) < 2 or not np.isfinite(lap_time) or lap_time <= 0:
        raise ValueError("path too short")
    if t[0] > MAX_EXTRAPOLATE_S or lap_time - t[-1] > MAX_EXTRAPOLATE_S:
        raise ValueError("path does not cover the lap")
    if t[0] > 0:        # extend backwards to t=0 along the first segment
        k = (t[0] - 0.0) / max(t[1] - t[0], 1e-9)
        t, x, y = (np.concatenate([[0.0], t]), np.concatenate([[x[0] - (x[1] - x[0]) * k], x]),
                   np.concatenate([[y[0] - (y[1] - y[0]) * k], y]))
    if t[-1] < lap_time:
        k = (lap_time - t[-1]) / max(t[-1] - t[-2], 1e-9)
        t, x, y = (np.concatenate([t, [lap_time]]), np.concatenate([x, [x[-1] + (x[-1] - x[-2]) * k]]),
                   np.concatenate([y, [y[-1] + (y[-1] - y[-2]) * k]]))
    inner = (t > 0.0) & (t < lap_time)
    tt = np.concatenate([[0.0], t[inner], [lap_time]])
    return tt, np.interp(tt, t, x), np.interp(tt, t, y)


def _downsample(n: int, k: int) -> np.ndarray:
    if n <= k:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, k).round().astype(int))


def scale_paths(paths: list[tuple[np.ndarray, np.ndarray]]):
    """Return a function mapping raw (x, y) -> scaled (x, y_down) using one shared scale."""
    allx = np.concatenate([p[0] for p in paths])
    ally = np.concatenate([p[1] for p in paths])
    minx, miny = allx.min(), ally.min()
    scale = max(allx.max() - minx, ally.max() - miny)
    if scale <= 0:
        scale = 1.0

    def f(x, y):
        return (np.asarray(x) - minx) / scale, 1.0 - (np.asarray(y) - miny) / scale
    return f


def _progress(x, y):
    d = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    return d / d[-1] if d[-1] > 0 else d


def analyse(a_path, b_path, lap_time_a, lap_time_b, a_name, b_name, a_code, b_code):
    """Core maths on already-fetched paths (pure function, unit-testable).

    a_path / b_path: (t, x, y) raw arrays. Returns the pair dict (contract format).
    """
    ta, xa, ya = trim_path(a_path, lap_time_a)
    tb, xb, yb = trim_path(b_path, lap_time_b)
    f = scale_paths([(xa, ya), (xb, yb)])
    sxa, sya = f(xa, ya)
    sxb, syb = f(xb, yb)

    idx = _downsample(len(ta), MAX_POINTS)
    track = [[round(float(sxa[i]), 4), round(float(sya[i]), 4)] for i in idx]
    ia, ib = _downsample(len(ta), MAX_POINTS), _downsample(len(tb), MAX_POINTS)
    a_out = [[round(float(ta[i]), 2), round(float(sxa[i]), 4), round(float(sya[i]), 4)] for i in ia]
    b_out = [[round(float(tb[i]), 2), round(float(sxb[i]), 4), round(float(syb[i]), 4)] for i in ib]

    # Segments: equal slices of the track (by distance), indices into `track`
    pt = _progress(np.array([p[0] for p in track]), np.array([p[1] for p in track]))
    bounds_p = np.linspace(0, 1, N_SEGMENTS + 1)
    bidx = [int(np.searchsorted(pt, p)) for p in bounds_p]
    bidx[-1] = len(track) - 1
    # elapsed time at each boundary for both drivers, using distance progress of their own path
    pa, pb = _progress(sxa, sya), _progress(sxb, syb)
    Ta = np.interp(bounds_p, pa, ta)
    Tb = np.interp(bounds_p, pb, tb)
    seg_a, seg_b = np.diff(Ta), np.diff(Tb)
    segments, adv = [], []
    for k in range(N_SEGMENTS):
        frm, to = bidx[k], max(bidx[k + 1], bidx[k])
        d = float(seg_b[k] - seg_a[k])           # >0: a was quicker
        adv.append(d)
        segments.append({"from": frm, "to": to, "faster": a_code if d >= 0 else b_code})
    adv = np.array(adv)

    # key moment: where the cumulative gap changed most
    key = None
    if np.max(np.abs(adv)) >= 0.05:
        k = int(np.argmax(np.abs(adv)))
        mid = (Ta[k] + Ta[k + 1]) / 2
        key = float(np.clip(mid / max(ta[-1], 1e-6), 0, 1))

    # summary: slow vs fast parts of the track (by average speed of both cars)
    rate = (1.0 / N_SEGMENTS) / np.maximum((seg_a + seg_b) / 2, 1e-3)
    order = np.argsort(rate)
    n3 = max(int(N_SEGMENTS * 0.4), 1)
    slow, fast = order[:n3], order[-n3:]
    summary = _summary(adv[slow].sum(), adv[fast].sum(), lap_time_a, lap_time_b, a_name, b_name)

    return {
        "a": a_code, "b": b_code, "lap": None,
        "track": track, "a_path": a_out, "b_path": b_out, "segments": segments,
        "lap_time_a": round(float(lap_time_a), 3), "lap_time_b": round(float(lap_time_b), 3),
        "key_moment_frac": None if key is None else round(key, 3),
        "summary": summary,
        "_advantage": adv.tolist(),
    }


def _summary(slow_adv, fast_adv, lt_a, lt_b, a, b) -> str:
    """slow_adv / fast_adv > 0 means `a` gained time there."""
    parts = []
    def who(v):
        return (a, b) if v >= 0 else (b, a)
    if abs(slow_adv) >= 0.05:
        w, _ = who(slow_adv)
        parts.append(f"{w} was faster through the slow corners.")
    if abs(fast_adv) >= 0.05:
        w, _ = who(fast_adv)
        parts.append(f"{w} pulled away on the fast stretches." if len(parts) else
                     f"{w} was quicker on the fast stretches.")
    diff = lt_b - lt_a
    if not parts:
        parts.append("The two cars were almost level all the way round.")
    if np.isfinite(diff) and abs(diff) >= 0.01:
        w = a if diff > 0 else b
        parts.append(f"Over the whole lap {w} was {abs(diff):.2f} seconds quicker.")
    return " ".join(parts)


def build_pair(rd: model.RaceData, a_dn: int, b_dn: int, lap: int) -> dict | None:
    pa = fetch_lap_path(rd, a_dn, lap)
    pb = fetch_lap_path(rd, b_dn, lap)
    if pa is None or pb is None:
        return None
    ra, rb = rd.lap_row(a_dn, lap), rd.lap_row(b_dn, lap)
    la = float(ra.dur) if np.isfinite(ra.dur) else float(ra.t1 - ra.t0)
    lb = float(rb.dur) if np.isfinite(rb.dur) else float(rb.t1 - rb.t0)
    try:
        pair = analyse(pa, pb, la, lb, rd.drivers.loc[a_dn, "last_name"], rd.drivers.loc[b_dn, "last_name"],
                       rd.code(a_dn), rd.code(b_dn))
    except ValueError:
        return None
    pair["lap"] = int(lap)
    return pair


def best_common_lap(rd: model.RaceData, a_dn: int, b_dn: int) -> int | None:
    """Fastest clean lap of `a` on which `b` also has a clean lap."""
    best, best_t = None, 1e9
    for r in rd.laps[rd.laps.driver_number == a_dn].itertuples():
        if not model.usable_lap(r) or not np.isfinite(r.dur):
            continue
        o = rd.lap_row(b_dn, int(r.lap_number))
        if o is None or not model.usable_lap(o):
            continue
        if r.dur < best_t:
            best, best_t = int(r.lap_number), r.dur
    return best
