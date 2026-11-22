"""Build the JSON files for one race (see docs/DATA_CONTRACT.md)."""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import numpy as np

from . import compare as cmp
from . import energy, model, story, tyres, validate, whatif

ROOT = Path(__file__).resolve().parent.parent
WEB_DATA = ROOT / "web" / "data"
OUT_DIR = ROOT / "out"

MAX_MOMENTS = 6
KIND_CAP = {"battery_low": 3, "energy_saving": 2, "tyre_wear": 3}
PER_DRIVER_CAP = 2
REF_SCORE = {"battery_low": 25.0, "energy_saving": 1.5, "tyre_wear": 1.5}
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

def _pick_comparison(rd, ctx, f) -> tuple[int | None, bool]:
    """Teammate first, then nearest in the standings, who had a clean lap there.

    A car that did the same thing (>= half the driver's loss) is no useful
    comparison: skip it. If the teammate did the same, say so (shared cause).
    """
    feats, traces = ctx["feats"], ctx["traces"]
    key, fld = ("clip", "fall") if f.kind == "battery_low" else ("lift", "lift_s")
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
        o = feats.get((d, f.lap, f.straight), {}).get(key)
        if o and o[fld] >= 0.5 * mine:
            mate_similar = mate_similar or d == mate
            continue
        return int(d), mate_similar
    return None, mate_similar


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
    other, mate_similar = _pick_comparison(rd, ctx, f)
    last = rd.drivers.loc[f.driver, "last_name"]
    if f.kind == "battery_low":
        c = me["clip"]
        zone = [round(min(c["peak_x"], c["end_x"]), 3), round(max(c["peak_x"], c["end_x"]), 3)]
        value = c["fall"]
        ov = feats.get((other, f.lap, f.straight), {}).get("clip") if other else None
        other_value = ov["fall"] if ov else None
        y_label = "Speed (km/h)"
    else:
        l = me["lift"]
        zone = [round(min(l["coast_x"], l["brake_x"]), 3), round(max(l["coast_x"], l["brake_x"]), 3)]
        value, other_value = l["lift_s"], None
        y_label = "Speed (km/h)"
    zone[1] = max(zone[1], zone[0] + 0.01)
    conf, why = f.confidence, f.why
    if mate_similar:
        conf = "medium" if conf == "high" else conf
        why += ("; his teammate lost speed in the same place, so the cause may be shared" if f.kind == "battery_low"
                else "; his teammate coasted there too, so the cause may be shared")
    texts = story.speed_moment(f.kind, last, f.lap, value, f.score,
                               rd.drivers.loc[other, "last_name"] if other else None, other_value)
    series = []
    for dn in [f.driver] + ([other] if other else []):
        pts = _speed_series(rd, ctx, dn, f.lap, s)
        if pts:
            series.append({"driver": rd.code(dn), "points": pts})
    if not series or series[0]["driver"] != rd.code(f.driver):
        return None
    chart = {"type": "speed", "title": texts["chart_title"], "x_label": "Along the straight →",
             "y_label": y_label, "zone": zone, "zone_label": texts["zone_label"], "series": series}
    return {"driver": f.driver, "lap": f.lap, "kind": f.kind, "confidence": conf, "why": why,
            "chart": chart, "caption": texts["caption"], "other": other, "texts": texts,
            "norm": f.score / REF_SCORE[f.kind]}


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
        if per_kind.get(m["kind"], 0) >= KIND_CAP[m["kind"]] or per_driver.get(m["driver"], 0) >= PER_DRIVER_CAP:
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

def build(year: int, rnd: int | None = None, latest: bool = False, out_root: Path | None = None,
          log=print) -> dict:
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
    e_findings, ctx = energy.detect(rd, gaps=gaps)
    if ctx.get("skipped"):
        notes.append(f"energy detection skipped: {ctx['skipped']}")
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
    p2 = rd.result[rd.result.position == 2]
    gap = float(p2.gap_to_leader.iloc[0]) if len(p2) and np.isfinite(p2.gap_to_leader.iloc[0]) else None
    head = story.headline(rd.drivers.loc[win_dn, "last_name"], gap, sc_laps, len(moments),
                          vsc_laps=rd.safety_car_laps("VSC") if not sc_laps else None)
    race = {"slug": slug, "name": name, "location": location, "date": session["date_start"][:10],
            "total_laps": rd.total_laps, "drivers": drivers,
            "results": [{"code": rd.code(dn), "position": pos} for dn, pos in results],
            "safety_car_laps": sc_laps, "headline": head, "moments": moments}

    # --- whatif.json --------------------------------------------------------
    wi = whatif.run(rd, df, fits)
    pit_method = wi.pop("_pit_loss_method")
    wi["drivers"] = {c: d for c, d in wi["drivers"].items() if c in {x["code"] for x in drivers}}

    # --- compare.json -------------------------------------------------------
    pairs = []
    want = []
    for m in chosen:
        if m.get("other") is not None:
            want.append((m["driver"], m["other"], m["lap"]))
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
                   "date": session["date_start"][:10], "has_compare": bool(pairs)}
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
    bad = []
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
