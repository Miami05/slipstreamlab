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
        sc_msg(13, "SESSION STARTED", category="SessionStatus"),
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
                                "energy_saving": "Lifted off early (probably saving energy or cooling the car)",
                                "tyre_wear": "Tyres probably wore out",
                                "power_cut": "Had less power for most of the race (probably a car problem)"}
    for label in story.KIND_LABEL.values():
        assert story.is_hedged(label)
    texts = []
    for kind in ("battery_low", "energy_saving", "power_cut"):
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


# --- Final review round: real red-flag shapes (Mexico City 2026), what-if, power cut, evaluate -----------------

def other_msg(lap, text, category="Other"):
    """A race-control row as OpenF1 really sends most of them: no flag, no scope."""
    return {"date": iso(T0 + (lap - 1) * LAP + 10), "message": text, "category": category, "flag": None,
            "scope": None}


def mexico_rows():
    return [
        {"date": iso(T0), "message": "SESSION STARTED", "category": "SessionStatus"},
        sc_msg(5, "SAFETY CAR DEPLOYED"),
        other_msg(7, "RED FLAG - RACE SUSPENDED"),
        other_msg(7, "SESSION ABORTED", category="SessionStatus"),
        other_msg(9, "RACE WILL RESUME AT 15:45"),            # advance notice, not the restart
        other_msg(12, "SESSION STARTED", category="SessionStatus"),
        other_msg(12, "STANDING START"),
        sc_msg(20, "SAFETY CAR DEPLOYED"),                     # a second, real safety car after the restart
        sc_msg(22, "SAFETY CAR IN THIS LAP"),
    ]


def test_mexico_red_flag_shapes_close_the_safety_car_and_end_only_at_session_started():
    rd = make_rd(n_laps=30, rc_rows=mexico_rows())
    finish_flags(rd)
    w = {k: [] for k in ("SC", "RED")}
    for k, a, b in rd.windows:
        w[k].append((a, b))
    assert len(w["RED"]) == 1 and len(w["SC"]) == 2
    red_a, red_b = w["RED"][0]
    assert red_a == pytest.approx(T0 + 6 * LAP + 10)                       # "RED FLAG - RACE SUSPENDED"
    assert red_b == pytest.approx(T0 + 11 * LAP + 10)                      # SessionStatus SESSION STARTED, not lap 9
    assert w["SC"][0][1] == pytest.approx(red_a)                           # first safety car closed by the red flag
    assert w["SC"][1][0] == pytest.approx(T0 + 19 * LAP + 10)              # not merged into one long window
    g = rd.laps[rd.laps.driver_number == 1].set_index("lap_number")
    assert g.loc[7:11, "neutral"].all()                                    # laps 14-19: racing again
    assert not g.loc[14:19, "neutral"].any()
    assert rd.safety_car_laps("SC") == [5, 6, 20, 21, 22]
    assert rd.safety_car_laps("RED") == [7, 8, 9, 10, 11]


def test_race_that_is_never_restarted_stays_red_until_the_end():
    rd = make_rd(n_laps=30, rc_rows=[
        {"date": iso(T0), "message": "SESSION STARTED", "category": "SessionStatus"},
        other_msg(7, "SESSION ABORTED", category="SessionStatus"),
        other_msg(8, "RACE WILL NOT BE RESUMED")])
    finish_flags(rd)
    assert [(k, b) for k, _, b in rd.windows] == [("RED", pytest.approx(T0 + 30 * LAP))]


def test_headline_says_there_was_a_red_flag():
    h = story.headline("Norris", 2.0, [45, 59, 60], 2, red_laps=[45, 46], n_low=0)
    assert "red flag on lap 45" in h["text"] and story.banned_hits(h["text"]) == []
    assert "red flag" not in story.headline("Norris", 2.0, [45], 2)["text"]


def test_headline_does_not_say_low_confidence_moments_likely_explain_anything():
    h = story.headline("A", 1.0, [], 6, n_low=4)["text"]
    assert "2 moments that likely explain big changes in speed, plus 4 less certain ones." in h
    only_low = story.headline("A", 1.0, [], 3, n_low=3)["text"]
    assert "likely" not in only_low and "not very sure" in only_low
    assert "plus one less certain one" in story.headline("A", 1.0, [], 3, n_low=1)["text"]
    assert story.headline("A", 1.0, [], 2)["text"].endswith("We found 2 moments that likely explain big changes in speed.")


def _whatif_rd(compounds, rc_rows=()):
    rd = make_rd(n_laps=30, rc_rows=rc_rows, stint_rows=[
        {"driver_number": d, "stint_number": 1, "lap_start": 1, "lap_end": 12, "compound": compounds[0],
         "tyre_age_at_start": 0} for d in (1, 2)] + [
        {"driver_number": d, "stint_number": 2, "lap_start": 13, "lap_end": 30, "compound": compounds[1],
         "tyre_age_at_start": 0} for d in (1, 2)],
        pit_rows=[{"driver_number": d, "lap_number": 12, "date": iso(T0 + 12 * LAP + 20), "lane_duration": 22.0}
                  for d in (1, 2)])
    return finish_flags(rd)


def test_whatif_moves_a_normal_stop_but_never_a_rain_tyre_to_slick_stop():
    dry = whatif.run(_whatif_rd(("MEDIUM", "HARD")))
    assert set(dry["drivers"]) == {"D1", "D2"}
    for pair in (("INTERMEDIATE", "SOFT"), ("SOFT", "INTERMEDIATE"), ("WET", "MEDIUM")):
        res = whatif.run(_whatif_rd(pair))
        assert res["drivers"] == {} and "rain tyres" in res["unavailable"], pair
    assert "unavailable" not in dry


def test_power_cut_needs_the_spell_to_cover_half_the_shared_clean_laps():
    from slipstream import powercut
    from tests.test_accuracy_fixes import fleet_feats
    # a 15 km/h deficit on the last 10 of 50 laps only: 20 % of the race
    dev = powercut.lap_deviation(fleet_feats(cut=(3, 42, 15.0), laps=range(2, 52)), None)
    assert powercut.detect(dev, {3: 4, 4: 3}) == {}
    # the same deficit over the last 30 of 50 laps is "most of the race"
    dev = powercut.lap_deviation(fleet_feats(cut=(3, 22, 15.0), laps=range(2, 52)), None)
    assert set(powercut.detect(dev, {3: 4, 4: 3})) == {3}


def test_power_cut_gives_no_verdict_without_intervals():
    from slipstream import powercut
    from tests.test_accuracy_fixes import fleet_feats, teams_rd
    rd = teams_rd()
    ctx = {"feats": fleet_feats(cut=(3, 10, 15.0)), "straights": []}
    f = energy.Finding("battery_low", 3, 20, [20], 0, 25.0, "medium", "speed fell", {"value": 30.0})
    assert powercut.apply(rd, [f], ctx, None) == {} and "power_cut" not in f.detail
    assert set(powercut.apply(rd, [f], ctx, {})) == {3}            # intervals present (nobody in traffic)


def test_evaluate_leaves_unbuilt_races_out_of_the_totals_and_never_calls_them_regressions():
    from slipstream import evaluate
    cases = [{"race": "built", "driver": "GAS", "kind": "battery_low", "lap": 6, "verdict": "wrong",
              "true_cause": "engine_power_cut"},
             {"race": "gone", "driver": "GAS", "kind": "battery_low", "lap": 6, "verdict": "wrong",
              "true_cause": "engine_power_cut"},
             {"race": "gone", "driver": "LEC", "kind": "tyre_wear", "lap": 11, "verdict": "plausible"}]
    rows = evaluate.evaluate(cases, {"built": []})
    s = evaluate.summary(rows)
    assert (s["wrong_total"], s["wrong_fixed"], s["plausible_total"], s["plausible_kept"]) == (1, 1, 0, 0)
    assert s["missing_races"] == ["gone"]
    rows_only_missing = evaluate.evaluate(cases[1:], {})
    lost = [r for r in rows_only_missing if r["verdict"] == "plausible" and r["ok"] is False]
    assert lost == []                                                 # "no data" (ok None) is not a lost case


def test_energy_traffic_step_down_is_not_swallowed_by_the_short_spell_cap():
    # a strong but short (2 lap) lift spell: capped to medium in clean air, one step lower in traffic
    from tests.test_energy import feats_for  # noqa: F401  (import check only)
    rd = make_rd()
    flagged = {10: 2.5, 11: 2.5}
    raw = {l: 1.0 for l in range(2, 30)}
    raw.update({10: 3.5, 11: 3.5})
    clean = energy._group(rd, "energy_saving", 1, type("S", (), {"idx": 0})(), flagged, {}, raw, 1.0, 1.0, 1.0,
                          None, traffic={(1, 10): 0.0})
    busy = energy._group(rd, "energy_saving", 1, type("S", (), {"idx": 0})(), flagged, {}, raw, 1.0, 1.0, 1.0,
                         None, traffic={(1, 10): 1.0})
    assert clean[0].confidence == "medium" and busy[0].confidence == "low"


def test_chart_measured_drop_and_excess_are_consistent():
    f = energy.Finding("battery_low", 1, 10, [10], 0, 23.0, "medium",
                       "accelerator flat out, yet speed fell 45 km/h before the braking point "
                       "(23 km/h more than usual for him and for the field)", {"value": 45.0})
    why = build.chart_why(f.why, f, 44.0)               # chart line shows 44, usual drop was 45 - 23 = 22
    assert "fell 44 km/h" in why and "(22 km/h more than usual" in why


def test_missing_race_control_gives_no_whatif_and_says_so():
    from slipstream import validate
    wi = build.rc_failed_whatif()
    validate.validate_whatif(wi)
    assert wi["drivers"] == {} and "not available" in wi["unavailable"]


def test_a_blue_flag_lap_is_never_the_reference_car_for_a_lift():
    rd = make_rd(n_laps=10, drivers=(1, 2, 3))
    rd.laps["blue"] = False
    rd.laps.loc[(rd.laps.driver_number == 2) & (rd.laps.lap_number == 5), "blue"] = True
    lift = {"lift_s": 2.0}
    feats = {(d, 5, 0): {"lift": lift if d == 1 else {"lift_s": 0.1}, "clip": None} for d in (1, 2, 3)}
    traces = {(d, 5): object() for d in (1, 2, 3)}
    f = energy.Finding("energy_saving", 1, 5, [5], 0, 1.9, "medium", "coasted", {"value": 2.0})
    f.straight = 0
    other, _ = build._pick_comparison(rd, {"feats": feats, "traces": traces}, f)
    assert other == 3 and other != 2


# --- third review round -------------------------------------------------------

def test_red_sentence_uses_the_restart_signal_not_lap_numbers():
    from slipstream import story
    # a late red flag that WAS restarted must not read "not restarted"
    assert "started again later" in story.red_sentence([28, 29])
    assert "was not restarted" in story.red_sentence([], abandoned_lap=30)
    assert "was not restarted" in story.red_sentence([29, 30], abandoned_lap=29)
    two = story.red_sentence([12, 13, 40, 41], abandoned_lap=40)
    assert "more than once" in two and "12" in two and "lap 40" in two and "not restarted" in two


def test_model_marks_only_a_never_restarted_red_flag_as_abandoned():
    late_restart = make_rd(n_laps=30, rc_rows=[
        {"date": iso(T0), "message": "SESSION STARTED", "category": "SessionStatus"},
        other_msg(28, "RED FLAG - RACE SUSPENDED"),
        other_msg(29, "SESSION STARTED", category="SessionStatus")])
    finish_flags(late_restart)
    assert late_restart.red_abandoned_epoch is None
    abandoned = make_rd(n_laps=30, rc_rows=[
        {"date": iso(T0), "message": "SESSION STARTED", "category": "SessionStatus"},
        other_msg(29, "RED FLAG - RACE SUSPENDED")])
    finish_flags(abandoned)
    assert abandoned.red_abandoned_epoch is not None


def _pc_dev(deficit_laps, healthy_laps=()):
    dev = {}
    for lap in deficit_laps:
        dev[(1, lap)], dev[(2, lap)] = -20.0, 0.0
    for lap in healthy_laps:
        dev[(1, lap)], dev[(2, lap)] = 0.0, 0.0
    return dev


def test_power_cut_still_found_when_the_car_retires_and_teammate_finishes():
    from slipstream import powercut
    dev = _pc_dev(range(3, 29))                       # driver 1 retires after lap 28 ...
    for lap in range(29, 61):
        dev[(2, lap)] = 0.0                           # ... teammate 2 races on to lap 60
    pc = powercut.detect_driver(1, 2, dev, total_laps=60, last_lap=28)
    assert pc is not None and pc.start_lap == 3


def test_power_cut_race_length_comes_from_laps_raced_not_feature_laps():
    from slipstream import powercut
    # healthy 3-13, deficit 14-29, features stop after lap 29 but the car raced all 60 laps
    dev = _pc_dev(range(14, 30), healthy_laps=range(3, 14))
    assert powercut.detect_driver(1, 2, dev, total_laps=60, last_lap=60) is None


def test_power_cut_survives_one_long_hole_with_deficit_on_both_sides():
    from slipstream import powercut
    dev = _pc_dev([*range(3, 22), *range(38, 61)])    # long SC/red flag/traffic gap 22-37
    assert powercut.detect_driver(1, 2, dev, total_laps=60, last_lap=60) is not None


def test_power_cut_healthy_laps_cannot_bridge_an_isolated_early_lap():
    from slipstream import powercut
    dev = _pc_dev([10, *range(49, 61)], healthy_laps=range(20, 49, 4))
    assert powercut.detect_driver(1, 2, dev, total_laps=60, last_lap=60) is None


def test_power_cut_rejects_one_isolated_early_lap_plus_late_spell():
    from slipstream import powercut
    assert powercut.detect_driver(1, 2, _pc_dev([10, *range(49, 61)]), total_laps=60) is None


def test_power_cut_span_ends_at_last_deficit_lap():
    from slipstream import powercut
    # deficit laps 10-19, healthy clean laps at the end: only ~10 laps of deficit, not "most of the race"
    assert powercut.detect_driver(1, 2, _pc_dev(range(10, 20), healthy_laps=(58, 59, 60)), total_laps=60) is None


def test_power_cut_needs_race_lap_span_not_only_clean_laps():
    from slipstream import powercut
    dn, mate = 1, 2
    dev = {}
    # only 12 shared clean laps, all at the very end of a 60-lap race, with a big deficit
    for lap in range(49, 61):
        dev[(dn, lap)] = -20.0
        dev[(mate, lap)] = 0.0
    assert powercut.detect_driver(dn, mate, dev, total_laps=60) is None


def test_evaluate_strict_fails_when_races_missing(tmp_path, monkeypatch):
    from slipstream import evaluate
    monkeypatch.setattr(evaluate, "load_moments", lambda *a, **k: {})   # nothing built
    out = []
    code = evaluate.run(strict=True, log=out.append)
    assert code == 1
    assert any("NOT VALIDATED" in line for line in out)


# Every power-cut span case raised in review rounds 3-6, in one table, so no patch can reopen one.
POWER_CUT_SPAN_CASES = [
    # (deficit laps, race_len, expected)
    (list(range(3, 58)) + [59, 60], 60, True),          # one missing lap near the end is normal
    ([3] + list(range(5, 61)), 60, True),               # one missing lap near the start is normal
    (list(range(3, 22)) + list(range(38, 61)), 60, True),  # long SC/red-flag hole, deficit both sides
    (list(range(3, 29)), 28, True),                     # car retired on lap 28 with the problem
    ([5, 6, 7] + list(range(49, 61)), 60, False),       # 3 early laps + late spell: hole too long
    ([10] + list(range(49, 61)), 60, False),            # one isolated early lap + late spell
    ([5] + [20, 21, 22] + list(range(40, 61)), 60, False),  # isolated lap 5 behind a real hole
    (list(range(14, 30)), 60, False),                   # 16 laps of a 60-lap race
    (list(range(10, 20)), 60, False),                   # deficit only laps 10-19
    (list(range(49, 61)), 60, False),                   # only the last 12 laps
    (list(range(3, 51)) + [58, 59], 60, True),          # 2 straggler laps after a late hole: noise
    ([5, 6, 7, 28] + list(range(49, 61)), 60, False),   # one noisy lap can't bridge a huge hole
    (list(range(20, 44)) + [49, 50], 50, True),         # exactly 5 missing laps doesn't split (span flips if it did)
    ([1, 2] + list(range(8, 35)), 50, True),            # 5-lap hole after a 2-lap start isn't a split either
    ([*range(3, 11), 18, 19, 27, 28, *range(36, 61)], 60, False),  # short pairs don't bridge (safe miss)
    ([5, 6, 7, 18, *range(30, 61)], 60, False),         # one stray lap can't bridge a 22-lap hole
    ([3, 4, 5, 13, 20, *range(35, 40)], 60, False),     # sparse short segment hides 27 missing laps
    ([*range(1, 21), *range(32, 43), *range(54, 61)], 60, True),  # more deficit laps can't undo a verdict
    ([*range(3, 12), *range(20, 28), *range(35, 43), *range(50, 61)], 60, True),  # SC + 2 traffic holes
    ([3, 4, 5, 13, 19, 25, *range(35, 40)], 60, False),  # 3-lap sparse segment: its inside gaps count
    ([3, 4, 5, 13, 20, 27, *range(35, 40)], 60, False),
    ([1, 8, 15, 22, 29, 36, 43, 50, 57, 60], 60, False),  # one very sparse segment: 50 laps missing
    ([5, 6, 7, 29, 30, 31, 53, 54, 55, 56], 60, False),  # 42 of 60 laps missing in total
]


@pytest.mark.parametrize("deficit, race_len, expected", POWER_CUT_SPAN_CASES)
def test_power_cut_span_cases(deficit, race_len, expected):
    from slipstream import powercut
    assert powercut._spans_most_of_race(deficit, race_len) is expected


def test_power_cut_retirement_with_a_safety_car_hole():
    from slipstream import powercut
    # retired on lap 28 of 60; a 10-lap SC hole is normal relative to the FULL race distance
    assert powercut._spans_most_of_race([*range(3, 11), *range(21, 29)], 28, total_laps=60) is True
    # ...but two islands 20 laps apart in a 28-lap race are not "most of his race"
    assert powercut._spans_most_of_race([3, 4, 5, 26, 27, 28], 28, total_laps=60) is False


def test_power_cut_start_lap_skips_an_isolated_early_lap():
    from slipstream import powercut
    pc = powercut.detect_driver(1, 2, _pc_dev([5, 20, 21, 22, *range(40, 61)]), total_laps=60, last_lap=60)
    assert pc is not None and pc.start_lap == 20


def test_red_sentence_keeps_a_merged_earlier_stoppage():
    two = story.red_sentence([39, 40, 41], abandoned_lap=40)
    assert "more than once" in two and "39" in two and "lap 40" in two


def test_power_cut_start_is_not_pulled_forward_by_a_stray_lap():
    from slipstream import powercut
    dev = _pc_dev([5, 6, 7, 18, *range(30, 61)], healthy_laps=(12, 24))
    pc = powercut.detect_driver(1, 2, dev, total_laps=60, last_lap=60)
    assert pc is not None and pc.start_lap == 30


def test_power_cut_more_evidence_keeps_the_early_start():
    from slipstream import powercut
    pc = powercut.detect_driver(1, 2, _pc_dev([*range(1, 16), *range(27, 42), *range(53, 61)]),
                                total_laps=60, last_lap=60)
    assert pc is not None and pc.start_lap == 1


def test_power_cut_laps_after_an_over_cap_hole_dont_make_up_the_lap_count():
    from slipstream import powercut
    dev = _pc_dev([5, 6, 7, *range(29, 35), 58, 59, 60])
    assert powercut.detect_driver(1, 2, dev, total_laps=60, last_lap=60) is None


def test_power_cut_sparse_early_retirement_is_decided_by_the_70_percent_rule():
    from slipstream import powercut
    # Absent (traffic) laps are not evidence against him: 7 of 10 clean laps down = exactly 70 %.
    at_threshold = _pc_dev([1, 8, 9, 10, 11, 12, 19], healthy_laps=(2, 3, 4))
    assert powercut.detect_driver(1, 2, at_threshold, total_laps=60, last_lap=20) is not None
    # One more healthy clean lap (7 of 11 = 64 %) and there is no verdict.
    below = _pc_dev([1, 8, 9, 10, 11, 12, 19], healthy_laps=(2, 3, 4, 13))
    assert powercut.detect_driver(1, 2, below, total_laps=60, last_lap=20) is None


def test_power_cut_not_claimed_when_the_car_recovers():
    from slipstream import powercut
    for deficit in (range(1, 31), range(1, 36), range(10, 41)):
        healthy = [l for l in range(1, 61) if l not in deficit]
        dev = _pc_dev(deficit, healthy_laps=healthy)
        assert powercut.detect_driver(1, 2, dev, total_laps=60, last_lap=60) is None, deficit


def test_power_cut_traffic_gaps_do_not_slide_the_start_lap():
    from slipstream import powercut
    dev = _pc_dev(range(1, 61, 2))                    # only odd laps clean (even laps in traffic)
    pc = powercut.detect_driver(1, 2, dev, total_laps=60, last_lap=60)
    assert pc is not None and pc.start_lap == 1


def test_span_helper_rejects_trivial_input():
    from slipstream import powercut
    assert powercut._spans_most_of_race([5], 60) is False
    assert powercut._spans_most_of_race([], 60) is False
    assert powercut._spans_most_of_race([5, 6, 7], 0) is False
