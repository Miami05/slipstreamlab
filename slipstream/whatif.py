"""What if a driver had stopped for tyres earlier or later?

Assumptions (also in docs/METHODS.md)
-------------------------------------
* Only the driver's FIRST stop under green flags is moved; everything else
  (second stops, compounds, other drivers) stays as it really happened.
* Time gain comes only from tyre age: laps on fresher rubber are faster by the
  measured degradation slope (seconds per lap of age, fuel-corrected, per
  compound, blended with the driver's own slope when reliable).
* The pit-lane time loss is the same for the real and the alternative stop
  (median measured on green-flag stops; fallback: median pit-lane time).
* Position after the stop = race-time order at the end of the out-lap
  (cumulative lap times of everybody, real data). Cars that have not stopped
  yet are still counted ahead/behind where they really were: this is exactly
  the "who would I rejoin behind" question, but it is not a race simulation.
* The range comes from lap-to-lap noise, slope uncertainty and a flat
  allowance when the new tyre compound is not the old one; never narrower
  than +-1.5 s. Laps under safety car get a much wider upper bound because a
  stop then costs far less than our fixed pit loss.
"""
from __future__ import annotations

import numpy as np

from . import model, tyres

MIN_HALF_WIDTH = 1.5
COMPOUND_UNCERTAINTY_S = 0.5     # per lap on a different compound (their base pace gap is not modelled)
TRAFFIC_GAP_S = 1.5
SPAN = 8


def estimate_pit_loss(rd: model.RaceData) -> tuple[float, str]:
    """Seconds lost to a stop. Returns (value, how it was measured)."""
    losses = []
    for s in rd.stops:
        if not s.green or s.in_lap < 4:
            continue
        a, b = rd.lap_row(s.driver, s.in_lap), rd.lap_row(s.driver, s.in_lap + 1)
        if a is None or b is None or not (np.isfinite(a.dur) and np.isfinite(b.dur)):
            continue
        ref = []
        for l in list(range(s.in_lap - 3, s.in_lap)) + list(range(s.in_lap + 2, s.in_lap + 5)):
            r = rd.lap_row(s.driver, l)
            if r is not None and model.usable_lap(r) and np.isfinite(r.dur):
                ref.append(r.dur)
        if len(ref) >= 3:
            loss = a.dur + b.dur - 2 * float(np.median(ref))
            if 10 <= loss <= 40:
                losses.append(loss)
    if len(losses) >= 3:
        return float(np.median(losses)), "in/out-lap loss on green-flag stops"
    lanes = [s.lane_s for s in rd.stops if s.lane_s and 10 <= s.lane_s <= 40]
    if lanes:
        return float(np.median(lanes)), "median time in the pit lane (few green-flag stops to measure)"
    return 21.0, "default"


def _age_comp(stints_info, l, which, shift_lap=None):
    """compound and tyre age at lap l for the real plan (shift_lap=None) or a plan that stops at shift_lap."""
    (c1, s1, a1), (c2, a2, L) = stints_info
    stop = L if shift_lap is None else shift_lap
    if l <= stop:
        return c1, a1 + (l - s1)
    return c2, a2 + (l - (stop + 1))


def option_gain(rd, fits, comp, driver, info, P) -> tuple[float, float, int]:
    """(gain_s, uncertainty_sd, laps_changed) of stopping at lap P instead of L.

    Only laps where the real and the alternative tyre differ (compound or age) count.
    """
    (c1, s1, a1), (c2, a2, L), e2 = info
    lo, hi = min(P, L) + 1, e2
    gain, n, sum_dage, ncomp = 0.0, 0, 0.0, 0
    se_acc = 0.0
    for l in range(lo, hi + 1):
        cr, ar = _age_comp(((c1, s1, a1), (c2, a2, L)), l, "real")
        cn, an = _age_comp(((c1, s1, a1), (c2, a2, L)), l, "new", P)
        if (cr, ar) == (cn, an):
            continue
        sr, ser = tyres.blended_slope(fits, comp, driver, cr)
        sn, sen = tyres.blended_slope(fits, comp, driver, cn)
        gain += sr * ar - sn * an
        n += 1
        sum_dage += abs(ar - an)
        se_acc = max(se_acc, ser, sen)
        if cr != cn:
            ncomp += 1
    resid = float(np.median([f.resid_sd for f in fits])) if fits else 0.4
    var = resid ** 2 * n + (se_acc * sum_dage) ** 2 + (COMPOUND_UNCERTAINTY_S * ncomp) ** 2
    return float(gain), float(np.sqrt(var)), n


def value_range(gain: float, sd: float, under_safety_car: bool, pit_loss: float) -> tuple[float, float]:
    """Honest low/high around the gain: ~90 % band, never narrower than +-1.5 s."""
    half = max(MIN_HALF_WIDTH, 1.645 * sd)
    low, high = gain - half, gain + half
    if under_safety_car:
        high += 0.4 * pit_loss      # a stop under safety car is much cheaper than the fixed loss we assume
    return low, high


def run(rd: model.RaceData, df=None, fits=None) -> dict:
    df = df if df is not None else tyres.clean_laps(rd)
    fits = fits if fits is not None else tyres.stint_fits(df)
    comp = tyres.compound_slopes(fits)
    pit_loss, how = estimate_pit_loss(rd)
    cum = model.cumulative_times(rd)
    N = rd.total_laps
    sc_laps = _neutral_laps_leader(rd)
    results = dict(model.results_table(rd))
    final = _final_times(rd, cum)

    out = {}
    for dn in sorted(rd.drivers.index):
        gstops = [s for s in rd.stops if s.driver == dn and s.green and 3 <= s.in_lap < N - 1]
        if not gstops or dn not in results:
            continue
        S = min(gstops, key=lambda s: s.in_lap)
        stints = rd.stints[rd.stints.driver_number == dn].sort_values("stint_number").to_dict("records")
        k = next((i for i, st in enumerate(stints) if int(st["lap_end"]) == S.in_lap), None)
        if k is None or k + 1 >= len(stints):
            continue
        before, after = stints[k], stints[k + 1]
        info = ((str(before["compound"]), int(before["lap_start"]), int(before.get("tyre_age_at_start") or 0)),
                (str(after["compound"]), int(after.get("tyre_age_at_start") or 0), S.in_lap),
                int(after["lap_end"]))
        L, s1, e2 = S.in_lap, int(before["lap_start"]), int(after["lap_end"])
        options = []
        for P in range(L - SPAN, L + SPAN + 1):
            if P < 2 or P >= N or P < s1 or P >= e2:
                continue
            gain, sd, n = option_gain(rd, fits, comp, dn, info, P)
            low, high = value_range(gain, sd, P in sc_laps, pit_loss)
            nearby, traffic = _nearby(rd, cum, dn, P, L, gain_upto=_gain_upto(rd, fits, comp, dn, info, P),
                                      pit_loss=pit_loss, N=N)
            fin = _finish(rd, results, final, dn, gain)
            options.append({"pit_lap": int(P), "finish_estimate": int(fin), "gain_s": round(gain, 2),
                            "low_s": round(low, 2), "high_s": round(high, 2),
                            "traffic": bool(traffic), "nearby": nearby})
        if options:
            out[rd.code(dn)] = {"real_pit_lap": int(L), "real_finish": int(results[dn]), "options": options}
    return {"pit_loss_s": round(float(pit_loss), 1), "drivers": out, "_pit_loss_method": how}


def _neutral_laps_leader(rd) -> set:
    win = int(rd.result.driver_number.iloc[0])
    g = rd.laps[(rd.laps.driver_number == win) & rd.laps.neutral]
    return set(int(x) for x in g.lap_number)


def _gain_upto(rd, fits, comp, dn, info, P):
    """cumulative gain per lap (dict lap -> gain through that lap)."""
    (c1, s1, a1), (c2, a2, L), e2 = info
    acc, out = 0.0, {}
    for l in range(min(P, L) + 1, e2 + 1):
        cr, ar = _age_comp(((c1, s1, a1), (c2, a2, L)), l, "real")
        cn, an = _age_comp(((c1, s1, a1), (c2, a2, L)), l, "new", P)
        sr, _ = tyres.blended_slope(fits, comp, dn, cr)
        sn, _ = tyres.blended_slope(fits, comp, dn, cn)
        acc += sr * ar - sn * an
        out[l] = acc
    return out


def _nearby(rd, cum, dn, P, L, gain_upto, pit_loss, N):
    """Two nearest cars (signed gap, negative = ahead) after the stop, and traffic flag."""
    Q = min(P + 1, N)
    mine = cum.get(dn, {}).get(Q)
    if mine is None:
        return [], False
    upto = [l for l in gain_upto if l <= Q]
    g = gain_upto[max(upto)] if upto else 0.0      # cumulative gain through lap Q (not the maximum)
    shift = pit_loss if L >= Q else 0.0
    t_new = mine - g + shift
    gaps = []
    for o, d in cum.items():
        if o == dn or Q not in d:
            continue
        gaps.append((d[Q] - t_new, o))
    gaps.sort(key=lambda x: abs(x[0]))
    near = [{"code": rd.code(o), "gap_s": round(float(gp), 2)} for gp, o in gaps[:2]]
    traffic = any(-TRAFFIC_GAP_S < gp < 0 for gp, _ in gaps)
    return near, traffic


def _final_times(rd, cum):
    """Final classified time per driver (laps, seconds). Uses the official duration when available."""
    res = {}
    for r in rd.result.itertuples():
        if getattr(r, "dns", False):
            continue
        dn = int(r.driver_number)
        laps = int(r.number_of_laps)
        t = float(r.duration) if r.duration is not None and np.isfinite(r.duration) else None
        if t is None:
            t = cum.get(dn, {}).get(laps)
        res[dn] = (laps, t)
    return res


def _calc_pos(final, dn, gain) -> int | None:
    laps, t = final.get(dn, (None, None))
    if t is None:
        return None
    new = t - gain
    better = 0
    for o, (ol, ot) in final.items():
        if o == dn:
            continue
        if ol > laps or (ol == laps and ot is not None and ot < new):
            better += 1
    return better + 1


def _finish(rd, results, final, dn, gain) -> int:
    """Official finishing position moved by the number of places the time gain changes.

    Computed as a difference against the same calculation with gain = 0, so
    penalties and classification quirks in the official result do not leak in.
    """
    base = _calc_pos(final, dn, 0.0)
    new = _calc_pos(final, dn, gain)
    if base is None or new is None:
        return results[dn]
    return int(max(1, min(len(final), results[dn] + (new - base))))
