"""Regression tests for the code-review findings (no network, synthetic data only)."""
import math

import numpy as np
import pandas as pd
import pytest

from slipstream import build, compare, energy, model, openf1, story, tyres, whatif
from tests.test_energy import feats_for

T0 = 1_800_000_000.0     # an arbitrary "race start" (epoch seconds)
LAP = 90.0


def iso(epoch: float) -> str:
    return pd.Timestamp(epoch, unit="s", tz="UTC").isoformat()


def make_rd(n_laps=30, drivers=(1, 2), rc_rows=(), pit_rows=(), stint_rows=None, result_rows=None):
    rows = []
    for d in drivers:
        for lap in range(1, n_laps + 1):
            t0 = T0 + (lap - 1) * LAP
            rows.append({"driver_number": d, "lap_number": lap, "t0": t0, "t1": t0 + LAP, "dur": LAP,
                         "is_pit_out_lap": False, "date_start": iso(t0), "lap_duration": LAP})
    laps = pd.DataFrame(rows)
    drv = pd.DataFrame({"code": [f"D{d}" for d in drivers], "last_name": [f"Name{d}" for d in drivers],
                        "team": [f"T{d}" for d in drivers], "color": "#112233"},
                       index=pd.Index(list(drivers), name="driver_number"))
    rc = pd.DataFrame(list(rc_rows) or [{"date": iso(T0), "message": "SESSION STARTED", "category": "SessionStatus"}])
    for col in ("flag", "scope", "sector", "category", "message"):
        if col not in rc:
            rc[col] = None
    rc["epoch"] = [model.to_epoch(v) for v in rc.date]
    stints = pd.DataFrame(stint_rows if stint_rows is not None else [
        {"driver_number": d, "stint_number": 1, "lap_start": 1, "lap_end": 10, "compound": "MEDIUM",
         "tyre_age_at_start": 0} for d in drivers] + [
        {"driver_number": d, "stint_number": 2, "lap_start": 11, "lap_end": n_laps, "compound": "HARD",
         "tyre_age_at_start": 0} for d in drivers])
    pits = model._normalize_pits(pd.DataFrame(list(pit_rows)))
    result = model._normalize_result(pd.DataFrame(result_rows or [
        {"position": i + 1, "driver_number": d, "number_of_laps": n_laps, "dns": False, "dnf": False,
         "duration": 3000.0 + i, "gap_to_leader": 0.0 if i == 0 else float(i)} for i, d in enumerate(drivers)]))
    stints = model.normalize_stints(stints, laps)
    rd = model.RaceData(session={"session_key": 1}, drivers=drv, laps=laps, stints=stints, pits=pits,
                        result=result, rc=rc, total_laps=n_laps, race_start=T0)
    rd._lapkey = {(int(r.driver_number), int(r.lap_number)): i for i, r in laps.iterrows()}
    return rd


def finish_flags(rd):
    model._build_windows(rd)
    model._flag_laps(rd)
    model._build_stops(rd)
    return rd


def sc_msg(lap, text, category="SafetyCar", flag=None):
    return {"date": iso(T0 + (lap - 1) * LAP + 10), "message": text, "category": category, "flag": flag,
            "scope": "Track"}


# --- Fable 3 / Neon 1: VSC -> SC escalation ----------------------------------------------------

def test_vsc_escalating_to_safety_car_does_not_stay_open_until_the_end():
    rd = make_rd(rc_rows=[
        {"date": iso(T0), "message": "SESSION STARTED", "category": "SessionStatus"},
        sc_msg(5, "VIRTUAL SAFETY CAR DEPLOYED"),
        sc_msg(6, "SAFETY CAR DEPLOYED"),                 # escalation: no "VSC ENDING" ever comes
        sc_msg(8, "SAFETY CAR IN THIS LAP"),
    ])
    finish_flags(rd)
    kinds = {k: (a, b) for k, a, b in rd.windows}
    assert set(kinds) == {"VSC", "SC"}
    assert kinds["VSC"][1] == pytest.approx(T0 + 5 * LAP + 10)            # closed when the SC arrived
    neutral = list(rd.laps[(rd.laps.driver_number == 1) & rd.laps.neutral].lap_number)
    assert max(neutral) <= 8                                                # laps after the SC are racing laps again
    assert not rd.laps[(rd.laps.driver_number == 1) & (rd.laps.lap_number == 20)].neutral.iloc[0]


def test_red_flag_window_is_neutral_and_next_lap_is_a_restart():
    rd = make_rd(rc_rows=[
        {"date": iso(T0), "message": "SESSION STARTED", "category": "SessionStatus"},
        sc_msg(10, "RED FLAG", category="Flag", flag="RED"),
        sc_msg(13, "SESSION RESUMED", category="SessionStatus"),
    ])
    finish_flags(rd)
    assert [k for k, _, _ in rd.windows] == ["RED"]
    g = rd.laps[rd.laps.driver_number == 1].set_index("lap_number")
    assert g.loc[11, "neutral"] and g.loc[12, "neutral"]
    assert not g.loc[16, "neutral"]
    assert g.loc[14, "restart"] or g.loc[13, "restart"]


# --- Fable 4 / Neon: pit off-by-one --------------------------------------------------------------

def test_pit_marks_in_lap_and_out_lap_only():
    # stop on lap 10 (in-lap); OpenF1 stamps the pit row 20 s into the OUT-lap (lap 11)
    rd = make_rd(pit_rows=[{"driver_number": 1, "lap_number": 10, "date": iso(T0 + 10 * LAP + 20),
                            "lane_duration": 22.0}])
    finish_flags(rd)
    g = rd.laps[rd.laps.driver_number == 1].set_index("lap_number")
    assert list(g[g.pit_in].index) == [10]
    assert list(g[g.pit_out].index) == [11]
    assert not model.usable_lap(rd.lap_row(1, 10)) and not model.usable_lap(rd.lap_row(1, 11))
    assert model.usable_lap(rd.lap_row(1, 12))            # the first flying lap on new tyres is kept


def test_pit_without_lap_number_uses_the_lap_before_the_timestamp():
    rd = make_rd(pit_rows=[{"driver_number": 2, "date": iso(T0 + 10 * LAP + 20), "lane_duration": 22.0}])
    rd.pits["lap_number"] = np.nan
    finish_flags(rd)
    g = rd.laps[rd.laps.driver_number == 2].set_index("lap_number")
    assert g.loc[10, "pit_in"] and g.loc[11, "pit_out"] and not g.loc[12, "pit_out"]


def test_restart_lap_stop_is_not_a_green_stop():
    rd = make_rd(rc_rows=[
        {"date": iso(T0), "message": "SESSION STARTED", "category": "SessionStatus"},
        sc_msg(3, "SAFETY CAR DEPLOYED"), sc_msg(5, "SAFETY CAR IN THIS LAP")],
        stint_rows=[
            {"driver_number": 1, "stint_number": 1, "lap_start": 1, "lap_end": 4, "compound": "S", "tyre_age_at_start": 0},
            {"driver_number": 1, "stint_number": 2, "lap_start": 5, "lap_end": 12, "compound": "M", "tyre_age_at_start": 0},
            {"driver_number": 1, "stint_number": 3, "lap_start": 13, "lap_end": 30, "compound": "H", "tyre_age_at_start": 0}],
        pit_rows=[{"driver_number": 1, "lap_number": 4, "date": iso(T0 + 4 * LAP + 20), "lane_duration": 22.0},
                  {"driver_number": 1, "lap_number": 12, "date": iso(T0 + 12 * LAP + 20), "lane_duration": 22.0}])
    finish_flags(rd)
    restart = rd.laps[(rd.laps.driver_number == 1) & rd.laps.restart].lap_number.tolist()
    stops = {s.in_lap: s.green for s in rd.stops}
    assert stops[4] is False                                  # under the safety car
    assert stops[12] is True                                  # a normal stop later is green
    # same stop, but put on the restart lap itself: no longer green
    first_restart = restart[0]
    rd2 = make_rd(rc_rows=list(rd.rc.drop(columns=["epoch"]).to_dict("records")),
                  stint_rows=[
                      {"driver_number": 1, "stint_number": 1, "lap_start": 1, "lap_end": first_restart, "compound": "S", "tyre_age_at_start": 0},
                      {"driver_number": 1, "stint_number": 2, "lap_start": first_restart + 1, "lap_end": 30, "compound": "M", "tyre_age_at_start": 0}],
                  pit_rows=[{"driver_number": 1, "lap_number": first_restart,
                             "date": iso(T0 + first_restart * LAP + 20), "lane_duration": 22.0}])
    finish_flags(rd2)
    assert [s.green for s in rd2.stops] == [False]


# --- Fable 5 / Neon: NaN stints, dns, laps, speed -----------------------------------------------

def test_missing_stint_values_do_not_crash():
    rd = make_rd(stint_rows=[
        {"driver_number": 1, "stint_number": 1, "lap_start": 1, "lap_end": 10, "compound": "M", "tyre_age_at_start": np.nan},
        {"driver_number": 1, "stint_number": 2, "lap_start": 11, "lap_end": np.nan, "compound": "H", "tyre_age_at_start": 3.0},
        {"driver_number": 2, "stint_number": 1, "lap_start": 1, "lap_end": 30, "compound": None, "tyre_age_at_start": 0}])
    assert rd.stints.tyre_age_at_start.tolist() == [0, 3, 0]
    assert rd.stints.lap_end.tolist() == [10, 30, 30]          # unfinished stint ends with his last lap
    assert (rd.stints.dtypes[["lap_start", "lap_end", "tyre_age_at_start"]] == "int64").all()
    finish_flags(rd)
    df = tyres.clean_laps(rd)                                   # used to raise "cannot convert float NaN to integer"
    assert len(df) > 0
    # the helper alone, as used in tyres / whatif / model
    assert model.as_int(float("nan")) == 0 and model.as_int(None, 7) == 7 and model.as_int("4") == 4
    raw = rd.stints.copy().astype(object)
    raw.loc[0, "tyre_age_at_start"] = float("nan")
    rd.stints = raw
    assert len(tyres.clean_laps(rd)) > 0


def test_null_dns_laps_and_duration_in_the_result_do_not_crash_whatif():
    result = pd.DataFrame([
        {"position": 1, "driver_number": 1, "number_of_laps": 30, "dns": None, "duration": "3000.5"},
        {"position": 2, "driver_number": 2, "number_of_laps": 30, "dns": float("nan"), "duration": float("nan")},
        {"position": None, "driver_number": 3, "number_of_laps": float("nan"), "dns": False, "duration": None},
        {"position": None, "driver_number": 4, "number_of_laps": 0, "dns": True, "duration": None}])
    rd = make_rd(drivers=(1, 2))
    rd.result = result                                              # raw, not normalised: the function must cope
    final = whatif._final_times(rd, {2: {30: 3010.0}})
    assert set(final) == {1, 2}                                     # null dns is not "did not start"; NaN laps skipped
    assert final[1] == (30, 3000.5) and final[2] == (30, 3010.0)
    normalised = model._normalize_result(result)
    assert normalised.dns.tolist() == [False, False, False, True]
    assert normalised.number_of_laps.tolist() == [30, 30, 0, 0]
    assert [dn for dn, _ in model.results_table(type("R", (), {"result": normalised})())] == [1, 2, 3]


def test_nan_speed_sample_does_not_poison_the_trace():
    n = 400
    t = T0 + np.arange(n) * 0.27
    sp = np.full(n, 250.0)
    tele = {"t": t, "speed": sp.copy(), "throttle": np.full(n, 100.0), "brake": np.zeros(n, bool)}
    tele["speed"][100] = np.nan
    rd = make_rd(n_laps=2, drivers=(1,))
    rd.laps.loc[:, "t0"] = [T0, T0 + 50.0]
    rd.laps.loc[:, "t1"] = [T0 + 50.0, T0 + 100.0]
    rd._tele[1] = tele
    assert rd.lap_trace(1, 1) is None                              # rejected instead of returning NaN arrays
    tele["speed"][100] = 250.0
    tr = rd.lap_trace(1, 1)
    assert tr is not None and np.isfinite(tr["dist"]).all() and np.isfinite(tr["frac"]).all()


def test_telemetry_drops_rows_with_missing_values(monkeypatch):
    n = 120
    rows = [{"date": iso(T0 + i * 0.27), "speed": 200 + i % 5, "throttle": 100, "brake": 0} for i in range(n)]
    rows[10]["speed"] = None
    rows[20]["throttle"] = None
    monkeypatch.setattr(openf1, "get", lambda *a, **k: rows)
    rd = make_rd(n_laps=2, drivers=(1,))
    tele = rd.telemetry(1)
    assert len(tele["t"]) == n - 2 and np.isfinite(tele["speed"]).all()


# --- Neon 3: gap_to_leader -----------------------------------------------------------------------

@pytest.mark.parametrize("gap,expected", [("+1 LAP", None), (None, None), (float("nan"), None), ("", None),
                                          (0.639, 0.639), ("0.639", 0.639), ("+0.639", 0.639), (0, 0.0)])
def test_winning_margin_survives_strings_and_none(gap, expected):
    res = pd.DataFrame([{"position": 1, "gap_to_leader": 0}, {"position": 2, "gap_to_leader": gap}])
    assert build.winning_margin(res) == expected
    assert build.winning_margin(pd.DataFrame([{"position": 1}])) is None            # no P2 / no column


# --- Neon 5: datetime resolution --------------------------------------------------------------------

@pytest.mark.parametrize("unit", ["ns", "us", "ms", "s"])
def test_epoch_seconds_ignores_datetime_resolution(unit):
    stamps = pd.Series(["2026-11-22T04:35:10.500000+00:00", "2026-11-22T04:35:11.000000+00:00"])
    parsed = pd.to_datetime(stamps, format="ISO8601", utc=True)
    want = parsed.astype("datetime64[ns, UTC]").astype("int64").to_numpy() / 1e9
    coarse = parsed.dt.as_unit(unit)
    got = model.epoch_seconds(coarse)
    assert got[0] > 1.7e9                                           # the buggy version gave ~1.7e6 for "us"
    expect = want if unit in ("ns", "us", "ms") else np.floor(want)
    assert got == pytest.approx(expect)
    assert model.epoch_seconds(stamps) == pytest.approx(want)       # plain strings too


# --- Fable 7 / Neon 10: safety-car headline -----------------------------------------------------------

def test_two_safety_car_periods_are_not_one_long_spell():
    h = story.headline("Verstappen", 0.6, [5, 6, 7, 40, 41, 42], 2)
    assert "lap 5 and stayed until lap 42" not in h["text"]
    assert "laps 5 to 7 and again on laps 40 to 42" in h["text"]
    assert story.banned_hits(h["text"]) == []


def test_single_lap_safety_car_and_runs():
    assert "came out on lap 19, which" in story.headline("A", 1.0, [19], 0)["text"]
    assert story.lap_runs([3, 4, 5, 9, 11, 12]) == [(3, 5), (9, 9), (11, 12)]
    one = story.headline("A", 1.0, [19, 20, 21, 22, 23], 1)["text"]
    assert "came out on lap 19 and stayed until lap 23" in one


def test_vsc_is_mentioned_next_to_a_safety_car_and_split_into_runs():
    h = story.headline("A", 1.0, [10, 11], 1, vsc_laps=[30, 31, 50])
    assert "also slowed down" in h["text"] and "laps 30 and 31 and again on lap 50" in h["text"]


# --- Fable 8 / Neon 7: hedged wording ---------------------------------------------------------------

def test_every_inferred_cause_is_hedged():
    assert story.KIND_LABEL == {"battery_low": "Lost power (maybe battery)",
                                "energy_saving": "Probably saving energy",
                                "tyre_wear": "Tyres probably wore out"}
    for label in story.KIND_LABEL.values():
        assert story.is_hedged(label)
    texts = []
    for kind in ("battery_low", "energy_saving"):
        for persistent in (False, True):
            d = story.speed_moment(kind, "Leclerc", 34, 28.0, 14.0, "Norris", 3.0, persistent=persistent)
            texts += [d["caption"], d["social_title"]]
    w = story.wear_moment("Hamilton", 31, 1.4, 20, 40)
    assert w["zone_label"] == "Tyres probably worn from here"
    assert w["social_title"].startswith("Hamilton's tyres probably wore out")
    texts += [w["caption"], w["social_title"], w["zone_label"]]
    for t in texts:
        assert story.is_hedged(t), t
    assert not story.is_hedged("Tyres wore out")


def test_text_check_refuses_an_unhedged_cause():
    race = {"drivers": [{"code": "AAA"}], "headline": {"title": "t.", "text": "t."},
            "moments": [{"label": "Battery ran low", "why": "w", "caption": "The battery ran low.",
                         "chart": {"title": "c", "zone_label": "z", "x_label": "x", "y_label": "y"}}]}
    with pytest.raises(Exception):
        build._text_checks(race, {"pairs": []})


# --- battery_low: never "high"; persistence is information only ----------------------------------------

def test_battery_low_is_never_high_and_persistence_is_only_a_note():
    # field loses 3 km/h; driver 1 loses 30 km/h on every lap (45 on three), driver 2 only on laps 5-7
    falls = {"base": 3.0}
    for lap in range(2, 12):
        falls[(1, lap)] = 30.0
    for lap in (5, 6, 7):
        falls[(2, lap)] = 40.0
    straights, feats = feats_for(falls)
    for lap in (4, 5, 6):
        feats[(1, lap, 0)]["clip"]["fall"] = 45.0
    rd = type("R", (), {})()
    found = [f for f in energy.detect_from_features(rd, feats, straights) if f.kind == "battery_low"]
    mine = [f for f in found if f.driver == 1]
    other = [f for f in found if f.driver == 2]
    assert mine and mine[0].detail["persistent"]
    assert mine[0].confidence == "medium"                       # not demoted to low ...
    assert "most laps" in mine[0].why and "car problem" in mine[0].why and "%" in mine[0].why
    assert other and not other[0].detail["persistent"] and "car problem" not in other[0].why
    assert all(f.confidence in ("low", "medium") for f in found)   # ... and never high
    # a huge, repeated loss would have been "high" before: it is capped
    big = {"base": 3.0}
    for lap in range(2, 12):
        big[(2, lap)] = 60.0 if lap in (4, 5, 6, 7, 8) else 3.0
    st2, ft2 = feats_for(big)
    strong = [f for f in energy.detect_from_features(rd, ft2, st2) if f.kind == "battery_low" and f.driver == 2]
    assert strong and strong[0].score >= 25 and strong[0].confidence == "medium"
    t = story.speed_moment("battery_low", "Gasly", 6, 26.0, 20.0, None, None, persistent=True)
    assert "most of his laps" in t["caption"] and "can also mean a problem with the car" in t["caption"]
    assert "The battery is one likely reason." in t["caption"]
    assert story.is_hedged(t["caption"]) and story.is_hedged(t["social_title"])
    t2 = story.speed_moment("battery_low", "Gasly", 6, 26.0, 20.0, None, None)
    assert "most of his laps" not in t2["caption"] and "lost power" in t2["caption"]


# --- Fable 1: caption measures what the chart shows -----------------------------------------------

def test_zone_drop_measures_the_plotted_points():
    pts = [[x / 10, 300 - (x > 4) * (x - 4) * 10] for x in range(11)]     # 300 until x=0.4, then falls to 240
    assert build.zone_drop(pts, [0.3, 1.0]) == pytest.approx(60.0)
    assert build.zone_drop(pts, [0.9, 1.0]) is None                        # fewer than 3 points in the zone
    assert build.zone_drop([], [0, 1]) is None


def test_comparison_car_is_chosen_by_the_chart_number():
    class RD:
        drivers = pd.DataFrame({"team": ["a", "b", "c"]}, index=[1, 2, 3])
        def teammate(self, dn):
            return 2 if dn == 1 else None
    class F:
        kind, driver, lap, straight = "battery_low", 1, 5, 0
    ctx = {"feats": {}, "traces": {(2, 5): 1, (3, 5): 1}}
    rd = RD()
    rd.result = pd.DataFrame({"position": [1, 2, 3], "driver_number": [1, 2, 3], "dns": [False] * 3,
                              "number_of_laps": [5] * 3})
    drops = {2: 69.0, 3: 5.0}          # the teammate lost MORE inside the zone than the driver (38)
    other, similar = build._pick_comparison(rd, ctx, F(), mine=38.0, measure=lambda d: drops[d])
    assert other == 3 and similar is True
    none, _ = build._pick_comparison(rd, ctx, F(), mine=38.0, measure=lambda d: None)
    assert none is None                # no usable chart line: the comparison sentence is dropped
    assert "same stretch" not in story.speed_moment("battery_low", "A", 1, 38.0, 1.0, None, None)["caption"]
    assert "did not slow down" in story.speed_moment("battery_low", "A", 1, 38.0, 1.0, "B", 0.4)["caption"]


# --- Neon 11: wear compare lap must be clean for both --------------------------------------------------

def test_wear_compare_never_uses_a_pit_lap():
    rd = make_rd(pit_rows=[{"driver_number": 2, "lap_number": 20, "date": iso(T0 + 20 * LAP + 20), "lane_duration": 22.0}],
                 stint_rows=[
                     {"driver_number": 1, "stint_number": 1, "lap_start": 1, "lap_end": 30, "compound": "M", "tyre_age_at_start": 0},
                     {"driver_number": 2, "stint_number": 1, "lap_start": 1, "lap_end": 20, "compound": "M", "tyre_age_at_start": 0},
                     {"driver_number": 2, "stint_number": 2, "lap_start": 21, "lap_end": 30, "compound": "H", "tyre_age_at_start": 0}])
    finish_flags(rd)
    assert build.pick_compare_lap(rd, 1, 2, 20, 28) == 22                  # laps 20 (in) and 21 (out) are skipped
    assert build.pick_compare_lap(rd, 1, 2, 20, 21) is None


# --- Fable 9 / what-if ------------------------------------------------------------------------------

class StubRD:
    def code(self, dn):
        return {1: "AAA", 2: "BBB", 3: "CCC"}[dn]


def test_stop_one_lap_earlier_does_not_pay_the_pit_loss_twice():
    # real stop on lap L=10: lap 10 (the in-lap) already contains ~10 s of the real stop
    L, loss, lap_s = 10, 22.0, 90.0
    cum = {1: {l: l * lap_s + (10.0 if l == L else 0.0) + (loss if l > L else 0.0) for l in range(1, 20)},
           2: {l: l * lap_s + 3.0 for l in range(1, 20)}}
    # P = L-1 -> Q = L. Alternative: normal laps until L-1, then stop (loss) by the end of lap L.
    near, _ = whatif._nearby(StubRD(), cum, 1, P=L - 1, L=L, gain_upto={}, pit_loss=loss, N=50, typical_lap_s=lap_s)
    expected_me = (L - 1) * lap_s + lap_s + loss
    assert near[0]["gap_s"] == pytest.approx(cum[2][L] - expected_me)
    # the old maths (raw cum + loss) would have been 10 s worse
    old, _ = whatif._nearby(StubRD(), cum, 1, P=L - 1, L=L, gain_upto={}, pit_loss=loss, N=50)
    assert old[0]["gap_s"] == pytest.approx(cum[2][L] - (cum[1][L] + loss))
    assert old[0]["gap_s"] < near[0]["gap_s"] - 9.0


def test_at_the_real_lap_the_nearby_gaps_are_the_real_gaps():
    cum = {1: {l: l * 90.0 + (22.0 if l > 10 else 0.0) for l in range(1, 20)},
           2: {l: l * 90.0 + 30.0 for l in range(1, 20)},
           3: {l: l * 90.0 + 5.0 for l in range(1, 20)}}
    near, traffic = whatif._nearby(StubRD(), cum, 1, P=10, L=10, gain_upto={}, pit_loss=22.0, N=50, typical_lap_s=90.0)
    real = {n["code"]: n["gap_s"] for n in near}
    assert real == {"CCC": pytest.approx(cum[3][11] - cum[1][11]), "BBB": pytest.approx(cum[2][11] - cum[1][11])}


def test_absurd_gain_is_held_back_and_the_range_reaches_the_model_value():
    fits = [tyres.Fit(1, 1, "H", 20, 0.05, 0.05, 0.01, 0.2, 0.9, age_span=20.0)]
    assert tyres.max_measured_penalty(fits) == pytest.approx(1.0)          # 0.05 x 20 = 1.0 s measured
    assert whatif.plausible_gain(20, 12, fits) == pytest.approx(8.0)
    assert whatif.plausible_gain(20, 20, fits) == 0.0
    raw, cap = 27.4, whatif.plausible_gain(29, 21, fits)
    gain = float(np.clip(raw, -cap, cap))
    low, high = whatif.value_range(gain, 0.5, False, 22.0, reach=abs(raw - gain))
    assert gain == cap and low <= gain <= high
    assert high >= raw or low <= -raw                                      # the model's own number is inside the range
    assert tyres.max_measured_penalty([]) == tyres.MIN_PENALTY_S


# --- Fable 11 / Neon: date ------------------------------------------------------------------------------

@pytest.mark.parametrize("start,offset,expected", [
    ("2026-11-22T04:00:00+00:00", "-08:00:00", "2026-11-21"),     # Las Vegas night race
    ("2026-07-26T13:00:00+00:00", "02:00:00", "2026-07-26"),      # unsigned positive offset
    ("2026-03-15T23:30:00+00:00", "+05:30:00", "2026-03-16"),
    ("2026-03-15T23:30:00+00:00", None, "2026-03-15"),
    ("2026-03-15T01:00:00", "-03:00", "2026-03-14"),
])
def test_race_date_is_local(start, offset, expected):
    assert model.local_date({"date_start": start, "gmt_offset": offset}) == expected


# --- Fable 10: cache ----------------------------------------------------------------------------------------

@pytest.fixture
def fake_network(monkeypatch, tmp_path):
    calls = []
    class Resp:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.payload
    def fake_urlopen(req, timeout=0):
        calls.append(req.full_url)
        return Resp(b'[{"v": %d}]' % len(calls))
    monkeypatch.setattr(openf1.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(openf1.time, "sleep", lambda s: None)
    monkeypatch.setattr(openf1, "MIN_INTERVAL", 0.0)
    return calls


def test_refresh_bypasses_the_cache_once_per_url(fake_network, tmp_path):
    assert openf1.get("laps", {"session_key": 1}, cache_dir=tmp_path) == [{"v": 1}]
    assert openf1.get("laps", {"session_key": 1}, cache_dir=tmp_path) == [{"v": 1}]      # cached
    with openf1.refreshing(True):
        assert openf1.get("laps", {"session_key": 1}, cache_dir=tmp_path) == [{"v": 2}]   # refetched
        assert openf1.get("laps", {"session_key": 1}, cache_dir=tmp_path) == [{"v": 2}]   # not twice in one run
    assert openf1.REFRESH is False
    assert openf1.get("laps", {"session_key": 1}, cache_dir=tmp_path) == [{"v": 2}]       # cache was refilled


def test_expired_entries_are_refetched_and_stale_copy_is_the_fallback(fake_network, tmp_path, monkeypatch):
    import os
    openf1.get("session_result", {"session_key": 1}, cache_dir=tmp_path)
    path = openf1.cache_path(openf1.build_url("session_result", {"session_key": 1}), tmp_path)
    old = path.stat().st_mtime - 7200
    os.utime(path, (old, old))
    assert openf1.get("session_result", {"session_key": 1}, cache_dir=tmp_path, max_age=3600) == [{"v": 2}]
    # expired again, and the network is down: the stale file is used instead of failing the build
    os.utime(path, (old, old))
    def down(req, timeout=0):
        raise openf1.urllib.error.URLError("no network")
    monkeypatch.setattr(openf1.urllib.request, "urlopen", down)
    assert openf1.get("session_result", {"session_key": 1}, cache_dir=tmp_path, max_age=3600, ) == [{"v": 2}]


def test_recent_sessions_get_a_short_ttl_old_ones_none():
    now = model.to_epoch("2026-11-22T21:00:00+00:00")
    recent = {"date_end": "2026-11-22T06:00:00+00:00"}
    old = {"date_end": "2026-11-18T06:00:00+00:00"}
    assert openf1.recent_ttl(recent, now) == openf1.RECENT_TTL_S
    assert openf1.recent_ttl(old, now) is None
    assert openf1.recent_ttl({}, now) is None


# --- Compare paths trimmed to the lap ------------------------------------------------------------------------

def _lap_path(lap, first=0.2, over=0.3, n=400):
    t = np.arange(first, lap + over, 0.27)
    a = t / lap * 2 * np.pi
    return t, 4000 * np.cos(a) + 5000, 2000 * np.sin(a) + 1000


def test_paths_are_cut_to_exactly_the_lap():
    t, x, y = compare.trim_path(_lap_path(96.45), 96.45)
    assert t[0] == 0.0 and t[-1] == pytest.approx(96.45) and np.all(np.diff(t) > 0)
    with pytest.raises(ValueError):
        compare.trim_path((np.array([5.0, 6.0]), np.array([0.0, 1.0]), np.array([0.0, 1.0])), 90.0)


def test_pair_summary_and_paths_agree_with_lap_times():
    pa, pb = _lap_path(96.45, 0.21, 0.3), _lap_path(96.182, 0.03, 0.3)
    p = compare.analyse(pa, pb, 96.45, 96.182, "Verstappen", "Norris", "VER", "NOR")
    assert p["a_path"][0][0] == 0 and p["b_path"][0][0] == 0
    assert p["a_path"][-1][0] == pytest.approx(96.45, abs=0.006) and p["b_path"][-1][0] == pytest.approx(96.18, abs=0.006)
    assert sum(p["_advantage"]) == pytest.approx(96.182 - 96.45, abs=1e-6)       # segments add up to the lap time gap
    assert "Norris was 0.27 seconds quicker" in p["summary"]


# --- sample data follows the same rules -----------------------------------------------------------------

def test_sample_data_is_valid_and_hedged():
    import json
    from pathlib import Path
    from slipstream import validate
    base = Path(__file__).resolve().parent.parent / "web" / "sample-data" / "2026-00-demo-grand-prix"
    race = json.loads((base / "race.json").read_text())
    validate.validate_race(race)
    for m in race["moments"]:
        assert m["label"] == story.KIND_LABEL[m["kind"]]
        assert story.is_hedged(m["label"]) and story.is_hedged(m["caption"])
