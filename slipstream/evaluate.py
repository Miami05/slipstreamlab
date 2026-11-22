"""Check the generated findings against the real-world validation cases.

`python -m slipstream evaluate` reads tests/validation_cases.json (the checked findings from
docs/VALIDATION.md) and web/data/*/race.json, and reports for each case:

* present    - same driver, same kind, same label, lap within +-3 of the case
* relabelled - same driver within +-3 laps, but another kind or another label
* gone       - nothing like it any more

Pass criteria:
* "wrong" case: gone, or relabelled / labelled so that it matches the true cause
* "plausible" case: still present (a plausible case that disappeared is a regression)
* "unknown" case: information only
"""
from __future__ import annotations

import json
from pathlib import Path

from . import story

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "validation_cases.json"
WEB_DATA = ROOT / "web" / "data"
LAP_TOLERANCE = 3

# Words in a moment's label/caption that show the finding now names the true cause.
CAUSE_WORDS = {
    "rain": ("rain", "wet"),
    "traffic": ("traffic", "stuck behind"),
    "blue_flags": ("blue flag", "lapped", "traffic"),
    "cooling": ("cooling",),
    "engine_power_cut": ("car problem", "less power for most of the race"),
}
CAUSE_KINDS = {"engine_power_cut": {"power_cut"}}


def load_cases(path: Path = CASES) -> list[dict]:
    return json.loads(Path(path).read_text())["cases"]


def load_moments(web_data: Path = WEB_DATA) -> dict[str, list[dict]]:
    out = {}
    for f in sorted(Path(web_data).glob("*/race.json")):
        out[f.parent.name] = json.loads(f.read_text()).get("moments", [])
    return out


def matches_cause(moment: dict | None, cause: str | None) -> bool:
    """Does this moment's kind/label name the true cause?"""
    if not moment or not cause:
        return False
    if moment["kind"] in CAUSE_KINDS.get(cause, ()):
        return True
    return any(w in moment.get("label", "").lower() for w in CAUSE_WORDS.get(cause, ()))


def classify(case: dict, moments: list[dict]) -> tuple[str, dict | None]:
    """('present'|'relabelled'|'gone', matching moment or None)."""
    near = [m for m in moments if m["driver"] == case["driver"]
            and abs(int(m["lap"]) - int(case["lap"])) <= LAP_TOLERANCE]
    same_kind = [m for m in near if m["kind"] == case["kind"]]
    if same_kind:
        m = min(same_kind, key=lambda m: abs(int(m["lap"]) - int(case["lap"])))
        if m["label"] == story.KIND_LABEL.get(m["kind"]):
            return "present", m
        return "relabelled", m
    # another kind close by counts as the same finding relabelled only when it names the true cause;
    # otherwise it is an unrelated new finding and the case itself is gone
    named = [m for m in near if matches_cause(m, case.get("true_cause"))]
    if named:
        return "relabelled", min(named, key=lambda m: abs(int(m["lap"]) - int(case["lap"])))
    return "gone", None


def judge(case: dict, status: str, moment: dict | None) -> bool | None:
    """True = pass, False = fail, None = information only."""
    v = case["verdict"]
    if v == "wrong":
        return status == "gone" or matches_cause(moment, case.get("true_cause"))
    if v == "plausible":
        return status == "present"
    return None


def evaluate(cases: list[dict] | None = None, moments_by_race: dict | None = None) -> list[dict]:
    cases = cases if cases is not None else load_cases()
    moments_by_race = moments_by_race if moments_by_race is not None else load_moments()
    rows = []
    for c in cases:
        ms = moments_by_race.get(c["race"])
        if ms is None:
            rows.append({**c, "status": "no data", "moment": None, "ok": None})
            continue
        status, m = classify(c, ms)
        rows.append({**c, "status": status, "moment": m, "ok": judge(c, status, m)})
    return rows


def _fmt(rows: list[dict]) -> str:
    lines = [f"{'race':<19}{'who':<5}{'kind':<14}{'lap':>4}  {'verdict':<10}{'true cause':<17}{'now':<11}{'result':<6} detail"]
    for r in rows:
        m = r["moment"]
        detail = ""
        if m:
            detail = f"{m['kind']} lap {m['lap']} [{m['confidence']}] {m['label']}"
        res = {True: "ok", False: "FAIL", None: "-"}[r["ok"]]
        if r["verdict"] == "plausible" and r["ok"] is False:
            res = "LOST"
        lines.append(f"{r['race'][5:]:<19}{r['driver']:<5}{r['kind']:<14}{r['lap']:>4}  {r['verdict']:<10}"
                     f"{r.get('true_cause', ''):<17}{r['status']:<11}{res:<6} {detail}")
    return "\n".join(lines)


def summary(rows: list[dict]) -> dict:
    out = {"present": 0, "relabelled": 0, "gone": 0, "no data": 0}
    for r in rows:
        out[r["status"]] += 1
    # a race that is not built is "not built", not a regression: leave it out of the totals
    built = [r for r in rows if r["status"] != "no data"]
    wrong = [r for r in built if r["verdict"] == "wrong"]
    plaus = [r for r in built if r["verdict"] == "plausible"]
    out["missing_races"] = sorted({r["race"] for r in rows if r["status"] == "no data"})
    out["wrong_total"] = len(wrong)
    out["wrong_fixed"] = sum(1 for r in wrong if r["ok"])
    out["plausible_total"] = len(plaus)
    out["plausible_kept"] = sum(1 for r in plaus if r["ok"])
    return out


def run(strict: bool = False, log=print) -> int:
    rows = evaluate()
    log(_fmt(rows))
    s = summary(rows)
    log("")
    log(f"present {s['present']}  relabelled {s['relabelled']}  gone {s['gone']}"
        + (f"  (not built, left out: {', '.join(s['missing_races'])})" if s["missing_races"] else ""))
    log(f"wrong cases fixed (gone or relabelled to the true cause): {s['wrong_fixed']} / {s['wrong_total']}")
    log(f"plausible cases still present: {s['plausible_kept']} / {s['plausible_total']}")
    lost = [r for r in rows if r["verdict"] == "plausible" and r["ok"] is False]
    for r in lost:
        log(f"  REGRESSION: plausible case lost: {r['race']} {r['driver']} {r['kind']} lap {r['lap']}")
    bad = s["wrong_fixed"] < s["wrong_total"] or lost
    return 1 if (strict and bad) else 0
