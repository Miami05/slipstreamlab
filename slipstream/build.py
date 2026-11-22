"""Build the JSON files for one race (see docs/DATA_CONTRACT.md)."""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import compare as cmp
from . import conditions, energy, model, openf1, powercut, story, tyres, validate, whatif

ROOT = Path(__file__).resolve().parent.parent
WEB_DATA = ROOT / "web" / "data"
OUT_DIR = ROOT / "out"

MAX_MOMENTS = 6
KIND_CAP = {"battery_low": 3, "energy_saving": 2, "tyre_wear": 3, "power_cut": 1}
PER_DRIVER_CAP = 2
REF_SCORE = {"battery_low": 25.0, "energy_saving": 1.5, "tyre_wear": 1.5, "power_cut": 25.0}
CONF_RANK = {"high": 2, "medium": 1, "low": 0}


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serialisable: {type(o)}")


def write_json(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, default=_json_default, allow_nan=False, separators=(",", ":"))
    path.write_text(text)
    return len(text.encode())


# ----------------------------------------------------------------------------
# Moments
# ----------------------------------------------------------------------------

def _pick_comparison(rd, ctx, f, measure=None, mine=None) -> tuple[int | None, bool]:
    """Teammate first, then nearest in the standings, who had a clean lap there.

    A car that did the same thing (>= half the driver's loss) is no useful
    comparison: skip it. If the teammate did the same, say so (shared cause).

    measure(driver) -> the comparison car's number, measured the same way as `mine`
    (for battery moments: the speed drop inside the shaded zone of the plotted chart,
    so the caption can never contradict the picture). Without it the detection
    features are used.
    """
    feats, traces = ctx["feats"], ctx["traces"]
    key, fld = ("clip", "fall") if f.kind == "battery_low" else ("lift", "lift_s")
    if mine is None:
        mine = feats[(f.driver, f.lap, f.straight)][key][fld]
    mate = rd.teammate(f.driver)
    pos = {d: p for d, p in model.results_table(rd)}
    order = ([mate] if mate is not None else []) + sorted(
        [d for d in rd.drivers.index if d != f.driver and d != mate],
        key=lambda d: abs(pos.get(d, 99) - pos.get(f.driver, 99)))
    mate_similar = False
    for d in order:
        if (d, f.lap) not in traces:
            continue
        if measure is not None:
            o = measure(d)
            if o is None:               # no usable chart line for this car
                continue
        else:
            oo = feats.get((d, f.lap, f.straight), {}).get(key)
            o = oo[fld] if oo else None
        if o is not None and o >= 0.5 * mine:
            mate_similar = mate_similar or d == mate
            continue
        return int(d), mate_similar
    return None, mate_similar


def zone_drop(points, zone) -> float | None:
    """Speed lost inside the shaded zone of a speed chart: highest speed in the zone minus the
    lowest speed after it (km/h). Measured on the exact points that are drawn."""
    if not points or not zone:
        return None
    ys = [y for x, y in points if zone[0] <= x <= zone[1]]
    if len(ys) < 3:
        return None
    pk = max(range(len(ys)), key=lambda i: ys[i])
    return float(ys[pk] - min(ys[pk:]))


def _speed_series(rd, ctx, dn, lap, s):
    tr = ctx["traces"].get((dn, lap))
    if tr is None:
        return None
    span = max(s.fb - s.fs, 1e-6)
    m = (tr["frac"] >= s.fs) & (tr["frac"] <= s.fb)
    if m.sum() < 8:
        return None
    x = (tr["frac"][m] - s.fs) / span
    grid = np.linspace(x.min(), x.max(), 100)
    y = np.interp(grid, x, tr["speed"][m])
    return [[round(float(a), 4), round(float(b), 1)] for a, b in zip(grid, y)]


def _energy_moment(rd, ctx, f):
    s = ctx["straights"][f.straight]
    feats = ctx["feats"]
    me = feats[(f.driver, f.lap, f.straight)]
    last = rd.drivers.loc[f.driver, "last_name"]
    my_pts = _speed_series(rd, ctx, f.driver, f.lap, s)
    if not my_pts:
        return None
    if f.kind == "battery_low":
        c = me["clip"]
        zone = [round(min(c["peak_x"], c["end_x"]), 3), round(max(c["peak_x"], c["end_x"]), 3)]
        zone[1] = max(zone[1], zone[0] + 0.01)
        # Caption numbers are measured on the plotted lines inside the shaded zone.
        value = zone_drop(my_pts, zone)
        if value is None:
            value = c["fall"]
        other, mate_similar = _pick_comparison(
            rd, ctx, f, mine=value,
            measure=lambda d: zone_drop(_speed_series(rd, ctx, d, f.lap, s), zone))
        other_pts = _speed_series(rd, ctx, other, f.lap, s) if other else None
        other_value = zone_drop(other_pts, zone) if other_pts else None
        if other is not None and other_value is None:
            other = None
    else:
        l = me["lift"]
        zone = [round(min(l["coast_x"], l["brake_x"]), 3), round(max(l["coast_x"], l["brake_x"]), 3)]
        zone[1] = max(zone[1], zone[0] + 0.01)
        value, other_value = l["lift_s"], None
        other, mate_similar = _pick_comparison(rd, ctx, f)
        other_pts = _speed_series(rd, ctx, other, f.lap, s) if other else None
        if other is not None and not other_pts:
            other = None
    y_label = "Speed (km/h)"
    conf, why = f.confidence, f.why
    if mate_similar:
        conf = "medium" if conf == "high" else conf
        why += ("; his teammate lost speed in the same place, so the cause may be shared" if f.kind == "battery_low"
                else "; his teammate coasted there too, so the cause may be shared")
    if f.kind == "battery_low":
        # `why` quotes the detector's own number; show the same (chart-measured) number everywhere
        why = why.replace(f"fell {f.detail['value']:.0f} km/h", f"fell {value:.0f} km/h", 1)
    kind = "power_cut" if f.detail.get("power_cut") else f.kind     # a sustained loss is a car problem, not a battery
    texts = story.speed_moment(kind, last, f.lap, value, f.score,
                               rd.drivers.loc[other, "last_name"] if other else None, other_value,
                               persistent=bool(f.detail.get("persistent")))
    series = [{"driver": rd.code(f.driver), "points": my_pts}]
    if other:
        series.append({"driver": rd.code(other), "points": other_pts})
    chart = {"type": "speed", "title": texts["chart_title"], "x_label": "Along the straight →",
             "y_label": y_label, "zone": zone, "zone_label": texts["zone_label"], "series": series}
    return {"driver": f.driver, "lap": f.lap, "kind": kind, "confidence": conf, "why": why,
            "chart": chart, "caption": texts["caption"], "other": other, "texts": texts,
            "norm": f.score / REF_SCORE[kind]}


def _wear_moment(rd, df, f):
    a, b = f.detail["stint_laps"]
    rival, mate_slowed = tyres.pick_rival(rd, df, f.driver, a, b, from_lap=f.lap, rise=f.score)
    conf, why = f.confidence, f.why
    if mate_slowed:
        conf = "low" if conf == "low" else "medium"
        why += "; his teammate slowed down in the same way, so the cause may be shared"
    series = [{"driver": rd.code(d), "points": pts[:200]} for d, pts in tyres.wear_chart(rd, df, f, rival)]
    if not series or len(series[0]["points"]) < 2:
        return None
    last = rd.drivers.loc[f.driver, "last_name"]
    texts = story.wear_moment(last, f.lap, f.score, a, b)
    chart = {"type": "laptime", "title": texts["chart_title"], "x_label": "Lap",
             "y_label": "Lap time, fuel-adjusted (s) · higher = slower",
             "zone": [float(f.lap), float(b)], "zone_label": texts["zone_label"], "series": series}
    return {"driver": f.driver, "lap": f.lap, "kind": "tyre_wear", "confidence": conf, "why": why,
            "chart": chart, "caption": texts["caption"], "other": rival, "texts": texts,
            "norm": f.score / REF_SCORE["tyre_wear"]}


def select_moments(cands: list[dict]) -> list[dict]:
    ranked = sorted(cands, key=lambda m: (-CONF_RANK[m["confidence"]], -m["norm"]))
    # prefer fewer honest moments: low-confidence ones only fill in when there is little else
    if sum(1 for m in ranked if m["confidence"] != "low") >= 3:
        ranked = [m for m in ranked if m["confidence"] != "low"]
    chosen, per_kind, per_driver = [], {}, {}
    for m in ranked:
        if len(chosen) >= MAX_MOMENTS:
            break
        # the per-kind cap keeps the confident moments varied; low-confidence fill-ins only compete on rank
        kind_full = m["confidence"] != "low" and per_kind.get(m["kind"], 0) >= KIND_CAP[m["kind"]]
        if kind_full or per_driver.get(m["driver"], 0) >= PER_DRIVER_CAP:
            continue
        if any(c["driver"] == m["driver"] and c["kind"] == m["kind"] for c in chosen):
            continue
        chosen.append(m)
        per_kind[m["kind"]] = per_kind.get(m["kind"], 0) + 1
        per_driver[m["driver"]] = per_driver.get(m["driver"], 0) + 1
    return chosen


# ----------------------------------------------------------------------------
# Main build
# ----------------------------------------------------------------------------

def winning_margin(result: pd.DataFrame) -> float | None:
    """Seconds between P1 and P2, or None when it is not a plain number.

    gap_to_leader holds strings such as "+1 LAP" for a lapped runner-up and None for
    missing values, so it is coerced instead of passed to np.isfinite.
    """
    p2 = result[result.position == 2]
    if not len(p2) or "gap_to_leader" not in p2:
        return None
    g = pd.to_numeric(pd.Series([p2.gap_to_leader.iloc[0]]), errors="coerce").iloc[0]
    return float(g) if pd.notna(g) and np.isfinite(g) else None


def pick_compare_lap(rd, a: int, b: int, start: int, last: int) -> int | None:
    """First lap in [start, last] on which both drivers have a clean racing lap, else None.

    (A tyre-wear moment names a rival who was clean over the stint, but he may have been
    in the pits on that very lap: a racing lap must never be compared with a pit lap.)
    """
    for lap in range(int(start), int(last) + 1):
        ra, rb = rd.lap_row(a, lap), rd.lap_row(b, lap)
        if ra is None or rb is None:
            continue
        if model.usable_lap(ra) and model.usable_lap(rb) and np.isfinite(ra.dur) and np.isfinite(rb.dur):
            return lap
    return None


def build(year: int, rnd: int | None = None, latest: bool = False, out_root: Path | None = None,
          log=print, refresh: bool = False) -> dict:
    """Build one race. refresh=True ignores the OpenF1 disk cache for this race (and refills it)."""
    with openf1.refreshing(refresh):
        return _build(year, rnd, latest, out_root, log)


def _build(year, rnd, latest, out_root, log) -> dict:
    t_start = time.time()
    out_root = out_root or WEB_DATA
    session = model.latest_session(year) if latest else model.session_for_round(year, rnd)
    rnd = session["round"]
    location = session["location"]
    slug = model.make_slug(year, rnd, location)
    name = model.meeting_name(session["meeting_key"], f"{location} Grand Prix")
    log(f"Building {slug} ({name}), session {session['session_key']}")

    rd = model.load_race(session)
    results = model.results_table(rd)
    starters = [dn for dn in rd.drivers.index
                if dn in {d for d, _ in results}]
    notes = []

    # --- detection ----------------------------------------------------------
    gaps = energy.gap_ahead_table(rd)
    e_findings, ctx = energy.detect(rd, gaps=gaps, traffic=rd.traffic)
    if ctx.get("skipped"):
        notes.append(f"energy detection skipped: {ctx['skipped']}")
    cuts = powercut.apply(rd, e_findings, ctx, rd.traffic)
    for pc in cuts.values():
        notes.append(f"{rd.code(pc.driver)}: slower than his teammate by {abs(pc.vs_mate):.0f} km/h on "
                     f"{pc.share:.0%} of clean laps from lap {pc.start_lap} (power loss)")
    if not cuts:
        notes.append("no sustained power loss (against the teammate) found for any driver")
    if rd.weather is None:
        notes.append("no weather data: rain is read from rain tyres and field pace only")
    if rd.traffic is None:
        notes.append("no intervals data: traffic is not checked (old lap-timestamp proxy only)")
    notes.extend(n for n in rd.notes if n not in notes)
    df = tyres.clean_laps(rd)
    fits = tyres.stint_fits(df)
    w_findings = tyres.detect_wear(rd, df, fits)

    cands = []
    for f in e_findings:
        m = _energy_moment(rd, ctx, f)
        if m:
            cands.append(m)
    for f in w_findings:
        m = _wear_moment(rd, df, f)
        if m:
            cands.append(m)
    if rd.rc_failed:
        for m in cands:
            m["confidence"] = "low"        # flags unknown: safety-car / yellow / blue-flag laps were not left out
    chosen = select_moments(cands)

    # --- race.json ----------------------------------------------------------
    drivers = [{"number": int(dn), "code": rd.drivers.loc[dn, "code"],
                "last_name": rd.drivers.loc[dn, "last_name"], "team": rd.drivers.loc[dn, "team"],
                "color": rd.drivers.loc[dn, "color"]} for dn in sorted(starters)]
    moments = []
    for i, m in enumerate(chosen, 1):
        caption = m["caption"] + (" We are not very sure about this one." if m["confidence"] == "low" else "")
        moments.append({"id": f"m{i}", "driver": rd.code(m["driver"]), "lap": int(m["lap"]),
                        "lap_label": f"Lap {m['lap']}", "kind": m["kind"], "label": story.KIND_LABEL[m["kind"]],
                        "confidence": m["confidence"], "why": m["why"], "chart": m["chart"],
                        "caption": caption})
    sc_laps = rd.safety_car_laps()
    win_dn = results[0][0]
    gap = winning_margin(rd.result)
    win_rain = conditions.rain_laps_of(rd, win_dn)
    head = story.headline(rd.drivers.loc[win_dn, "last_name"], gap, sc_laps, len(moments),
                          vsc_laps=rd.safety_car_laps("VSC"), rain_laps=win_rain, total_laps=rd.total_laps)
    date = model.local_date(session)          # the circuit's local date, not the UTC one
    race = {"slug": slug, "name": name, "location": location, "date": date,
            "total_laps": rd.total_laps, "drivers": drivers,
            "results": [{"code": rd.code(dn), "position": pos} for dn, pos in results],
            "safety_car_laps": sc_laps, "headline": head, "moments": moments}

    # --- whatif.json --------------------------------------------------------
    # the what-if uses every clean lap as before (rain / blue-flag laps are only left out of the findings)
    df_wi = tyres.clean_laps(rd, analysis=False)
    wi = whatif.run(rd, df_wi, tyres.stint_fits(df_wi))
    pit_method = wi.pop("_pit_loss_method")
    wi["drivers"] = {c: d for c, d in wi["drivers"].items() if c in {x["code"] for x in drivers}}

    # --- compare.json -------------------------------------------------------
    pairs = []
    want = []
    for m in chosen:
        if m.get("other") is None:
            continue
        if m["kind"] == "tyre_wear":
            last_lap = m["chart"]["zone"][1]
            lap = pick_compare_lap(rd, m["driver"], m["other"], m["lap"], last_lap)
        else:
            lap = m["lap"]       # energy moments only pick rivals who have a clean trace on this lap
        if lap:
            want.append((m["driver"], m["other"], lap))
            break
    if len(results) >= 2:
        a, b = results[0][0], results[1][0]
        lap = cmp.best_common_lap(rd, a, b)
        if lap:
            want.append((a, b, lap))
    seen = set()
    for a, b, lap in want:
        key = (frozenset((a, b)), lap)
        if key in seen:
            continue
        seen.add(key)
        pair = cmp.build_pair(rd, a, b, lap)
        if pair:
            pair.pop("_advantage", None)
            pairs.append(pair)
    compare_obj = {"pairs": pairs}

    # --- validate, then write ----------------------------------------------
    validate.validate_race(race)
    validate.validate_whatif(wi)
    validate.validate_compare(compare_obj)
    validate.validate_cross(race, wi, compare_obj)
    _text_checks(race, compare_obj)

    folder = out_root / slug
    sizes = {
        "race.json": write_json(folder / "race.json", race),
        "whatif.json": write_json(folder / "whatif.json", wi),
        "compare.json": write_json(folder / "compare.json", compare_obj),
    }
    _write_social_extras(slug, race, chosen, rd)
    index_entry = {"slug": slug, "session_key": int(session["session_key"]), "year": int(year),
                   "round": int(rnd), "name": name, "location": location,
                   "date": date, "has_compare": bool(pairs)}
    _merge_index(out_root, index_entry)

    secs = time.time() - t_start
    log(f"  drivers {len(drivers)}, laps {rd.total_laps}, safety-car laps {sc_laps or 'none'}")
    log(f"  pit loss {wi['pit_loss_s']} s ({pit_method}); what-if drivers: {', '.join(wi['drivers']) or 'none'}")
    for m in moments:
        log(f"  {m['id']}: {m['driver']} lap {m['lap']} {m['kind']} [{m['confidence']}] - {m['why']}")
    if not moments:
        log("  no moments found")
    log(f"  compare pairs: {[(p['a'], p['b'], p['lap']) for p in pairs]}")
    for n in notes:
        log(f"  NOTE: {n}")
    log("  files: " + ", ".join(f"{k} {v/1024:.0f} KB" for k, v in sizes.items()) + f"  ({secs:.0f} s)")
    return {"slug": slug, "race": race, "whatif": wi, "compare": compare_obj, "sizes": sizes,
            "seconds": secs, "notes": notes}


def _text_checks(race, compare_obj) -> None:
    """User-facing text must be jargon-free and never use bare driver codes."""
    codes = [d["code"] for d in race["drivers"]]
    texts = [race["headline"]["title"], race["headline"]["text"]]
    for m in race["moments"]:
        texts += [m["label"], m["why"], m["caption"], m["chart"]["title"], m["chart"]["zone_label"],
                  m["chart"]["x_label"], m["chart"]["y_label"]]
    texts += [p["summary"] for p in compare_obj["pairs"]]
    bad = [f"cause not hedged (needs 'probably'/'likely'): {m['label']!r}" for m in race["moments"]
           if not story.is_hedged(m["label"])]
    bad += [f"cause not hedged: {m['caption']!r}" for m in race["moments"]
            if not story.is_hedged(m["caption"])]
    for t in texts:
        bad += [f"{h!r} in {t!r}" for h in story.banned_hits(t)]
        bad += [f"code {c} in {t!r}" for c in story.bare_codes(t, codes)]
    if bad:
        raise validate.ValidationError("user-facing text", bad)


def _write_social_extras(slug, race, chosen, rd) -> None:
    extras = {}
    for i, m in enumerate(chosen, 1):
        extras[f"m{i}"] = m["texts"]
    write_json(OUT_DIR / slug / "social.json", extras)


def _merge_index(out_root: Path, entry: dict) -> None:
    path = out_root / "index.json"
    races = []
    if path.exists():
        try:
            races = json.loads(path.read_text()).get("races", [])
        except (json.JSONDecodeError, AttributeError):
            races = []
    races = [r for r in races if r.get("slug") != entry["slug"]] + [entry]
    races.sort(key=lambda r: (r["date"], r["round"]), reverse=True)
    obj = {"generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "races": races}
    validate.validate_index(obj)
    write_json(path, obj)
