"""Check generated JSON against docs/DATA_CONTRACT.md before it is written."""
from __future__ import annotations

import math
import re

KINDS = {"battery_low", "energy_saving", "tyre_wear", "power_cut"}
CONFIDENCE = {"high", "medium", "low"}
CHART_TYPES = {"speed", "laptime"}
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ValidationError(ValueError):
    def __init__(self, what: str, problems: list[str]):
        self.problems = problems
        super().__init__(f"{what} is invalid:\n  - " + "\n  - ".join(problems[:25]))


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _str(x) -> bool:
    return isinstance(x, str) and x != ""


class _P(list):
    def req(self, cond, msg):
        if not cond:
            self.append(msg)
        return cond


def validate_index(obj) -> None:
    p = _P()
    if p.req(isinstance(obj, dict), "index must be an object"):
        p.req(_str(obj.get("generated_at")), "generated_at must be a string")
        races = obj.get("races")
        if p.req(isinstance(races, list), "races must be a list"):
            dates = []
            for i, r in enumerate(races):
                w = f"races[{i}]"
                if not p.req(isinstance(r, dict), f"{w} must be an object"):
                    continue
                for k in ("slug", "name", "location"):
                    p.req(_str(r.get(k)), f"{w}.{k} must be a non-empty string")
                for k in ("session_key", "year", "round"):
                    p.req(_int(r.get(k)), f"{w}.{k} must be an integer")
                p.req(isinstance(r.get("date"), str) and DATE_RE.match(r["date"] or ""),
                      f"{w}.date must be YYYY-MM-DD")
                p.req(isinstance(r.get("has_compare"), bool), f"{w}.has_compare must be a boolean")
                dates.append(r.get("date") or "")
            p.req(dates == sorted(dates, reverse=True), "races must be sorted newest first")
    if p:
        raise ValidationError("index.json", p)


def _chart(p: _P, w: str, c, codes) -> None:
    if not p.req(isinstance(c, dict), f"{w} must be an object"):
        return
    p.req(c.get("type") in CHART_TYPES, f"{w}.type must be one of {sorted(CHART_TYPES)}")
    for k in ("title", "x_label", "y_label"):
        p.req(_str(c.get(k)), f"{w}.{k} must be a non-empty string")
    z = c.get("zone")
    if z is not None:
        p.req(isinstance(z, list) and len(z) == 2 and all(_num(v) for v in z) and z[0] <= z[1],
              f"{w}.zone must be null or [start, end]")
        p.req(_str(c.get("zone_label")), f"{w}.zone_label required when zone is set")
    series = c.get("series")
    if p.req(isinstance(series, list) and 1 <= len(series) <= 2, f"{w}.series must have 1-2 entries"):
        for j, s in enumerate(series):
            ws = f"{w}.series[{j}]"
            p.req(isinstance(s, dict) and s.get("driver") in codes, f"{ws}.driver must be a code from drivers")
            pts = s.get("points") if isinstance(s, dict) else None
            if p.req(isinstance(pts, list) and 2 <= len(pts) <= 200, f"{ws}.points must have 2-200 points"):
                p.req(all(isinstance(q, list) and len(q) == 2 and all(_num(v) for v in q) for q in pts),
                      f"{ws}.points must be [x, y] pairs of finite numbers")


def validate_race(obj) -> None:
    p = _P()
    if not p.req(isinstance(obj, dict), "race must be an object"):
        raise ValidationError("race.json", p)
    for k in ("slug", "name", "location"):
        p.req(_str(obj.get(k)), f"{k} must be a non-empty string")
    p.req(isinstance(obj.get("date"), str) and DATE_RE.match(obj["date"] or ""), "date must be YYYY-MM-DD")
    p.req(_int(obj.get("total_laps")) and obj["total_laps"] > 0, "total_laps must be a positive integer")
    codes = set()
    drivers = obj.get("drivers")
    if p.req(isinstance(drivers, list) and drivers, "drivers must be a non-empty list"):
        for i, d in enumerate(drivers):
            w = f"drivers[{i}]"
            if not p.req(isinstance(d, dict), f"{w} must be an object"):
                continue
            p.req(_int(d.get("number")), f"{w}.number must be an integer")
            p.req(_str(d.get("code")) and len(d["code"]) == 3, f"{w}.code must be 3 letters")
            for k in ("last_name", "team"):
                p.req(_str(d.get(k)), f"{w}.{k} must be a non-empty string")
            p.req(isinstance(d.get("color"), str) and COLOR_RE.match(d["color"]), f"{w}.color must be #RRGGBB")
            codes.add(d.get("code"))
    res = obj.get("results")
    if p.req(isinstance(res, list), "results must be a list"):
        for i, r in enumerate(res):
            p.req(isinstance(r, dict) and r.get("code") in codes and _int(r.get("position")),
                  f"results[{i}] needs a known code and integer position")
    sc = obj.get("safety_car_laps")
    p.req(isinstance(sc, list) and all(_int(x) for x in sc), "safety_car_laps must be a list of integers")
    h = obj.get("headline")
    p.req(isinstance(h, dict) and _str(h.get("title")) and _str(h.get("text")),
          "headline needs non-empty title and text")
    moments = obj.get("moments")
    if p.req(isinstance(moments, list) and len(moments) <= 8, "moments must be a list of at most 8"):
        for i, m in enumerate(moments):
            w = f"moments[{i}]"
            if not p.req(isinstance(m, dict), f"{w} must be an object"):
                continue
            p.req(_str(m.get("id")), f"{w}.id must be a string")
            p.req(m.get("driver") in codes, f"{w}.driver must be a code from drivers")
            p.req(_int(m.get("lap")), f"{w}.lap must be an integer")
            p.req(_str(m.get("lap_label")), f"{w}.lap_label must be a string")
            p.req(m.get("kind") in KINDS, f"{w}.kind must be one of {sorted(KINDS)}")
            p.req(_str(m.get("label")), f"{w}.label must be a string")
            p.req(m.get("confidence") in CONFIDENCE, f"{w}.confidence must be high/medium/low")
            p.req(_str(m.get("why")), f"{w}.why must be a string")
            p.req(_str(m.get("caption")), f"{w}.caption must be a string")
            _chart(p, f"{w}.chart", m.get("chart"), codes)
    if p:
        raise ValidationError("race.json", p)


def validate_whatif(obj) -> None:
    p = _P()
    if not p.req(isinstance(obj, dict), "whatif must be an object"):
        raise ValidationError("whatif.json", p)
    p.req(_num(obj.get("pit_loss_s")), "pit_loss_s must be a finite number")
    p.req(obj.get("unavailable") is None or isinstance(obj.get("unavailable"), str),
          "unavailable must be a string when present")
    drivers = obj.get("drivers")
    if p.req(isinstance(drivers, dict), "drivers must be an object"):
        for code, d in drivers.items():
            w = f"drivers.{code}"
            if not p.req(isinstance(d, dict), f"{w} must be an object"):
                continue
            p.req(_int(d.get("real_pit_lap")), f"{w}.real_pit_lap must be an integer")
            p.req(_int(d.get("real_finish")), f"{w}.real_finish must be an integer")
            opts = d.get("options")
            if not p.req(isinstance(opts, list) and opts, f"{w}.options must be a non-empty list"):
                continue
            laps = [o.get("pit_lap") for o in opts if isinstance(o, dict)]
            p.req(laps == sorted(set(laps)), f"{w}.options must be sorted by pit_lap with no repeats")
            p.req(d.get("real_pit_lap") in laps, f"{w}.options must include the real pit lap")
            for j, o in enumerate(opts):
                wo = f"{w}.options[{j}]"
                if not p.req(isinstance(o, dict), f"{wo} must be an object"):
                    continue
                p.req(_int(o.get("pit_lap")), f"{wo}.pit_lap must be an integer")
                p.req(_int(o.get("finish_estimate")) and o["finish_estimate"] >= 1,
                      f"{wo}.finish_estimate must be a positive integer")
                nums = [o.get(k) for k in ("gain_s", "low_s", "high_s")]
                if p.req(all(_num(x) for x in nums), f"{wo} gain_s/low_s/high_s must be finite numbers"):
                    p.req(o["low_s"] <= o["gain_s"] <= o["high_s"], f"{wo} needs low_s <= gain_s <= high_s")
                p.req(isinstance(o.get("traffic"), bool), f"{wo}.traffic must be a boolean")
                nb = o.get("nearby")
                if p.req(isinstance(nb, list) and len(nb) <= 2, f"{wo}.nearby must be a list of at most 2"):
                    p.req(all(isinstance(n, dict) and _str(n.get("code")) and _num(n.get("gap_s")) for n in nb),
                          f"{wo}.nearby entries need code and gap_s")
    if p:
        raise ValidationError("whatif.json", p)


def validate_compare(obj) -> None:
    p = _P()
    if not p.req(isinstance(obj, dict), "compare must be an object"):
        raise ValidationError("compare.json", p)
    pairs = obj.get("pairs")
    if p.req(isinstance(pairs, list), "pairs must be a list"):
        for i, pr in enumerate(pairs):
            w = f"pairs[{i}]"
            if not p.req(isinstance(pr, dict), f"{w} must be an object"):
                continue
            p.req(_str(pr.get("a")) and _str(pr.get("b")) and pr["a"] != pr["b"], f"{w}.a/b must be two different codes")
            p.req(_int(pr.get("lap")), f"{w}.lap must be an integer")
            track = pr.get("track")
            ntrack = 0
            def pts(arr, n):
                return isinstance(arr, list) and len(arr) >= 2 and len(arr) <= 400 and all(
                    isinstance(q, list) and len(q) == n and all(_num(v) for v in q) for q in arr)
            if p.req(pts(track, 2), f"{w}.track must be 2-400 [x, y] points"):
                ntrack = len(track)
                p.req(all(0 <= v <= 1 for q in track for v in q), f"{w}.track values must be within 0..1")
            for k in ("a_path", "b_path"):
                if p.req(pts(pr.get(k), 3), f"{w}.{k} must be 2-400 [t, x, y] points"):
                    p.req(all(0 <= q[1] <= 1 and 0 <= q[2] <= 1 for q in pr[k]), f"{w}.{k} x/y must be within 0..1")
            segs = pr.get("segments")
            if p.req(isinstance(segs, list) and segs, f"{w}.segments must be a non-empty list"):
                for j, s in enumerate(segs):
                    ok = (isinstance(s, dict) and _int(s.get("from")) and _int(s.get("to"))
                          and 0 <= s["from"] <= s["to"] < max(ntrack, 1) and s.get("faster") in (pr.get("a"), pr.get("b")))
                    p.req(ok, f"{w}.segments[{j}] needs from<=to inside track and faster = a or b")
            p.req(_num(pr.get("lap_time_a")) and _num(pr.get("lap_time_b")), f"{w}.lap_time_a/b must be numbers")
            k = pr.get("key_moment_frac")
            p.req(k is None or (_num(k) and 0 <= k <= 1), f"{w}.key_moment_frac must be null or 0..1")
            p.req(_str(pr.get("summary")), f"{w}.summary must be a string")
    if p:
        raise ValidationError("compare.json", p)


def validate_cross(race, whatif, compare) -> None:
    """Every driver code used in whatif/compare must exist in race.drivers (the site hides unknown ones)."""
    codes = {d["code"] for d in race["drivers"]}
    p = _P()
    for c in whatif["drivers"]:
        p.req(c in codes, f"whatif driver {c} missing from race.drivers")
        for d in whatif["drivers"][c]["options"]:
            for n in d["nearby"]:
                p.req(n["code"] in codes, f"whatif nearby code {n['code']} missing from race.drivers")
    for pr in compare["pairs"]:
        p.req(pr["a"] in codes and pr["b"] in codes, f"compare pair {pr['a']}/{pr['b']} missing from race.drivers")
    if p:
        raise ValidationError("cross-file references", p)
