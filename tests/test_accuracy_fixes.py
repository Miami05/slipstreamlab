"""Tests for the accuracy fixes made after the real-world validation (docs/VALIDATION.md).

All synthetic, no network: rain, traffic, blue flags, chequered flag, lift label, power cut,
"high" needs 5 clean laps, OpenF1 retries / optional endpoints, evaluate.
"""
import json
import urllib.error
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from slipstream import build, conditions, energy, evaluate, model, openf1, powercut, story, tyres
from tests.test_energy import feats_for
from tests.test_review_fixes import LAP, T0, finish_flags, iso, make_rd


# --- 7. OpenF1: transient errors are retried, optional endpoints are optional ---------------------

@pytest.fixture
def net(monkeypatch):
    """Scripted network: net.script = list of results (an int = HTTP error code, bytes = a good answer)."""
    class Resp:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.payload

    class Net:
        script: list = []
        calls = 0
        sleeps: list = []

    n = Net()

    def fake_urlopen(req, timeout=0):
        n.calls += 1
        item = n.script[min(n.calls - 1, len(n.script) - 1)]
        if isinstance(item, int):
            raise urllib.error.HTTPError(req.full_url, item, "err", {}, None)
        return Resp(item)

    monkeypatch.setattr(openf1.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(openf1.time, "sleep", lambda s: n.sleeps.append(s))
    monkeypatch.setattr(openf1, "MIN_INTERVAL", 0.0)
    return n


def test_422_is_retried_with_backoff_and_then_succeeds(net, tmp_path):
    net.script = [422, 422, b'[{"v": 1}]']
    assert openf1.get("weather", {"session_key": 1}, cache_dir=tmp_path) == [{"v": 1}]
    assert net.calls == 3
    assert net.sleeps == [4.0, 8.0]                 # back-off doubles


def test_422_gives_up_after_the_retries_and_fails_loudly_for_required_data(net, tmp_path):
    net.script = [422]
    with pytest.raises(openf1.OpenF1Error):
        openf1.get("laps", {"session_key": 1}, cache_dir=tmp_path)
    assert net.calls == 1 + openf1.HTTP_RETRIES


def test_other_client_errors_still_raise_at_once(net, tmp_path):
    net.script = [403]
    with pytest.raises(urllib.error.HTTPError):
        openf1.get("laps", {"session_key": 1}, cache_dir=tmp_path)
    assert net.calls == 1


def test_optional_endpoint_failure_prints_a_note_and_returns_nothing(net, tmp_path, capsys):
    net.script = [422]
    notes = []
    assert openf1.get_optional("intervals", {"session_key": 1}, what="intervals", notes=notes, cache_dir=tmp_path) == []
    assert "NOTE" in capsys.readouterr().out and len(notes) == 1


def _fake_get(fail):
    """openf1.get replacement for a tiny 2-driver race; endpoints in `fail` raise."""
    rows = {
        "drivers": [{"driver_number": d, "name_acronym": f"D{d}", "last_name": f"Name{d}", "team_name": f"T{d}",
                     "team_colour": "112233"} for d in (1, 2)],
        "laps": [{"driver_number": d, "lap_number": l, "date_start": iso(T0 + (l - 1) * LAP),
                  "lap_duration": LAP, "is_pit_out_lap": False} for d in (1, 2) for l in range(1, 6)],
        "stints": [{"driver_number": d, "stint_number": 1, "lap_start": 1, "lap_end": 5, "compound": "MEDIUM",
                    "tyre_age_at_start": 0} for d in (1, 2)],
        "pit": [],
        "session_result": [{"position": i + 1, "driver_number": d, "number_of_laps": 5, "dns": False, "dnf": False,
                            "duration": 450.0, "gap_to_leader": 0.0} for i, d in enumerate((1, 2))],
        "race_control": [{"date": iso(T0), "message": "SESSION STARTED", "category": "SessionStatus"}],
        "weather": [{"date": iso(T0), "rainfall": 0}],
        "intervals": [{"date": iso(T0), "driver_number": 2, "interval": 3.0, "gap_to_leader": 3.0}],
    }

    def fake(endpoint, params=None, **kw):
        if endpoint in fail:
            raise openf1.OpenF1Error(f"{endpoint} failed")
        return rows[endpoint]
    return fake


def test_load_race_goes_on_without_optional_endpoints(monkeypatch, capsys):
    monkeypatch.setattr(openf1, "get", _fake_get({"weather", "intervals", "race_control"}))
    rd = model.load_race({"session_key": 7})
    assert rd.weather is None and rd.intervals is None and rd.traffic is None
    assert rd.rc_failed is True and len(rd.notes) == 4          # three failures + the "flags unknown" warning
    assert capsys.readouterr().out.count("NOTE") == 3
    assert len(rd.laps) == 10 and not rd.laps.wet.any()


def test_load_race_still_fails_loudly_without_laps(monkeypatch):
    monkeypatch.setattr(openf1, "get", _fake_get({"laps"}))
    with pytest.raises(openf1.OpenF1Error):
        model.load_race({"session_key": 7})


# --- 1. Rain --------------------------------------------------------------------------------------

def weather(*pairs):
    """pairs of (minutes after T0, rainfall flag)"""
    return pd.DataFrame([{"epoch": T0 + m * 60.0, "rainfall": r} for m, r in pairs])


def test_rain_windows_follow_the_rainfall_flag_and_stay_inside_the_race():
    w = weather((-30, 1), (0, 0), (10, 1), (11, 1), (12, 0))
    win = conditions.rain_windows(w, T0, T0 + 3600)
    assert win == [(T0 + 600.0, T0 + 720.0)]               # two samples in a row merge; pre-race rain is ignored
    assert conditions.rain_windows(None, T0, T0 + 3600) == []
    assert conditions.rain_windows(weather((5, 0)), T0, T0 + 3600) == []


def test_laps_in_rain_and_two_laps_after_are_wet_and_not_used():
    rd = make_rd(n_laps=20, drivers=(1, 2))
    rd.weather = weather((13 * LAP / 60 + 0.1, 1), (13 * LAP / 60 + 1.0, 0))   # inside lap 14 (a lap is 90 s)
    finish_flags(rd)
    rain = sorted(rd.laps[(rd.laps.driver_number == 1) & rd.laps.rain].lap_number)
    wet = sorted(rd.laps[(rd.laps.driver_number == 1) & rd.laps.wet].lap_number)
    assert rain == [14]
    assert wet == [14, 15, 16]                               # the rain lap and 2 laps after
    assert not model.analysis_lap(rd.lap_row(1, 15))
    assert model.usable_lap(rd.lap_row(1, 15))               # what-if / compare still see the lap
    assert model.analysis_lap(rd.lap_row(1, 17))
    assert 14 in conditions.rain_laps_of(rd, 1) and 15 not in conditions.rain_laps_of(rd, 1)


def test_cars_on_rain_tyres_make_those_laps_wet_for_everybody():
    st = pd.DataFrame([
        {"driver_number": d, "stint_number": 1, "lap_start": 1, "lap_end": 30,
         "compound": "INTERMEDIATE" if d <= 2 else "MEDIUM"} for d in (1, 2, 3, 4, 5, 6)])
    st.loc[st.driver_number <= 2, "lap_end"] = 10
    st.loc[st.driver_number <= 2, "compound"] = "MEDIUM"
    st.loc[st.driver_number <= 2, "lap_start"] = 1
    extra = pd.DataFrame([{"driver_number": d, "stint_number": 2, "lap_start": 11, "lap_end": 30,
                           "compound": "INTERMEDIATE"} for d in (1, 2)])
    st = pd.concat([st, extra], ignore_index=True)
    # laps 11..30: 2 of 6 cars (33 %) on intermediates >= 25 %
    assert conditions.fleet_wet_laps(st, 30) == set(range(11, 31))
    assert conditions.fleet_wet_laps(st[st.compound != "INTERMEDIATE"], 30) == set()


def test_runup_finds_the_laps_before_the_switch_when_the_field_was_already_slower():
    pace = {l: 90.0 for l in range(2, 41)}
    for l in (37, 38, 39):                 # the track got wet; nobody had rain tyres yet
        pace[l] = 92.5
    pace[36] = 90.4                        # a small wobble is not rain
    assert conditions.runup_laps({40, 41, 42}, pace) == {37, 38, 39}
    assert conditions.runup_laps({2, 3}, pace) == set()     # a race that starts wet has no run-up
    assert conditions.runup_laps({40}, {l: 90.0 for l in range(2, 41)}) == set()


def test_wet_spells_get_a_headline_sentence_and_dry_races_do_not():
    assert story.rain_sentence([], 50) == ""
    assert "Rain came on lap 57 and stayed until the end" in story.rain_sentence(list(range(57, 72)), 71)
    assert "wet at the start" in story.rain_sentence(list(range(1, 17)), 71)
    assert story.rain_sentence(list(range(1, 17)) + list(range(58, 72)), 71).startswith("Rain came and went, on laps 1 to 16 and again on laps 58 to 71")
    assert "whole race" in story.rain_sentence(list(range(1, 72)), 71)
    h = story.headline("Norris", 15.0, [], 2, rain_laps=list(range(1, 17)) + list(range(58, 72)), total_laps=71)
    assert "Rain came and went" in h["text"]
    # at most 3 sentences: the safety car, the rain and the findings beat a short slow spell
    h = story.headline("A", 1.0, [10, 11], 2, vsc_laps=[30], rain_laps=[40, 41], total_laps=50)
    assert h["text"].count(".") == 3 and "slowed down for a short time" not in h["text"]
    assert story.headline("A", 1.0, [], 0)["text"] == "Nothing in the data stood out clearly enough to explain here."


def wear_setup(n_laps=40, stint_rows=None):
    return make_rd(n_laps=n_laps, drivers=(1, 2), stint_rows=stint_rows or [
        {"driver_number": d, "stint_number": 1, "lap_start": 1, "lap_end": n_laps, "compound": "SOFT",
         "tyre_age_at_start": 0} for d in (1, 2)])


def wear_df(rise_from, last=30, rise=1.6, first=2):
    rows = []
    for d in (1, 2, 3, 4, 5):        # the field (drivers 2-5) stays flat
        for lap in range(first, last + 1):
            y = 90.0 + (rise if d == 1 and lap >= rise_from else 0.0)
            rows.append({"driver": d, "lap": lap, "stint": 1, "compound": "SOFT", "age": lap - 1, "dur": y,
                         "corr": y})
    return pd.DataFrame(rows)


def test_a_clear_rise_is_found_and_long_evidence_can_be_high():
    rd = wear_setup()
    df = wear_df(rise_from=12)
    f = tyres.detect_wear(rd, df, tyres.stint_fits(df))
    assert len(f) == 1 and f[0].driver == 1 and f[0].lap == 12 and f[0].confidence == "high"


def test_high_confidence_needs_five_clean_laps_of_evidence():
    rd = wear_setup()
    df = wear_df(rise_from=8, last=11, rise=2.0)              # only 4 slow laps (8..11)
    f = tyres.detect_wear(rd, df, tyres.stint_fits(df))
    assert len(f) == 1 and f[0].detail["n_clean"] == 4
    assert f[0].confidence == "medium"                          # would be "high" on points alone


def test_stints_next_to_rain_tyres_never_give_tyre_wear():
    # the slick stint ends with a change to intermediates: its late slow-down is the weather
    rd = wear_setup(stint_rows=[
        {"driver_number": 1, "stint_number": 1, "lap_start": 1, "lap_end": 30, "compound": "SOFT", "tyre_age_at_start": 0},
        {"driver_number": 1, "stint_number": 2, "lap_start": 31, "lap_end": 40, "compound": "INTERMEDIATE", "tyre_age_at_start": 0},
        {"driver_number": 2, "stint_number": 1, "lap_start": 1, "lap_end": 40, "compound": "SOFT", "tyre_age_at_start": 0}])
    assert tyres.wet_adjacent_stints(rd.stints) == {(1, 1), (1, 2)}
    df = wear_df(rise_from=12)
    assert tyres.detect_wear(rd, df, tyres.stint_fits(df)) == []
    # the same rise on a stint that has nothing to do with rain is still reported
    rd2 = wear_setup()
    assert len(tyres.detect_wear(rd2, df, tyres.stint_fits(df))) == 1


# --- 2. Traffic -------------------------------------------------------------------------------------

def test_a_long_spell_behind_another_car_is_traffic_not_tyre_wear():
    rd = wear_setup()
    df = wear_df(rise_from=12)
    rd.traffic = {(1, l): 1.0 for l in range(12, 31)}          # glued to the car in front for all 19 slow laps
    assert tyres.detect_wear(rd, df, tyres.stint_fits(df)) == []
    rd.traffic = {(1, l): 1.0 for l in range(12, 17)}           # 5 of 19 slow laps: not most of them
    f = tyres.detect_wear(rd, df, tyres.stint_fits(df))
    assert len(f) == 1 and f[0].confidence == "high"


def test_a_short_spell_behind_another_car_is_kept_but_low():
    rd = wear_setup()
    df = wear_df(rise_from=8, last=11, rise=2.0)               # 4 slow laps, all in traffic
    rd.traffic = {(1, l): 1.0 for l in range(8, 12)}
    f = tyres.detect_wear(rd, df, tyres.stint_fits(df))
    assert len(f) == 1 and f[0].confidence == "low" and "traffic" in f[0].why


def test_traffic_share_reads_intervals_as_a_step_function_with_leader_and_stale_rules():
    laps = pd.DataFrame([{"driver_number": d, "lap_number": 1, "t0": 0.0, "t1": 100.0} for d in (1, 2, 3)])
    raw = pd.DataFrame([
        {"date": iso(T0 + 0), "driver_number": 1, "interval": 0.0, "gap_to_leader": 0.0},      # the leader
        {"date": iso(T0 + 0), "driver_number": 2, "interval": 0.8, "gap_to_leader": 0.8},
        {"date": iso(T0 + 0), "driver_number": 3, "interval": "+1 LAP", "gap_to_leader": "+1 LAP"},
    ])
    iv = conditions.prepare_intervals(raw)
    laps[["t0", "t1"]] += T0
    share = conditions.traffic_share(iv, laps, max_age=1e9)
    assert share[(1, 1)] == 0.0            # interval 0 of the leader is not traffic
    assert share[(2, 1)] == 1.0
    assert share[(3, 1)] == 0.0            # "+1 LAP" is unknown, not close
    stale = conditions.traffic_share(iv, laps, max_age=10.0)      # the value is 50+ s old for most of the lap
    assert stale[(2, 1)] < 0.2
    assert conditions.traffic_share(None, laps) is None
    assert conditions.in_traffic(0.5) and not conditions.in_traffic(0.49) and not conditions.in_traffic(None)


def test_a_lift_in_traffic_is_one_step_lower_not_a_cap():
    lift = {(5, 7): 2.0}
    straights, feats = feats_for({}, lift=lift, n_laps=20)
    base = [f for f in energy.detect_from_features(None, feats, straights) if f.kind == "energy_saving"]
    traf = [f for f in energy.detect_from_features(None, feats, straights, traffic={(5, 7): 1.0})
            if f.kind == "energy_saving"]
    free = [f for f in energy.detect_from_features(None, feats, straights, traffic={(5, 7): 0.0})
            if f.kind == "energy_saving"]
    assert base[0].confidence == "medium" and free[0].confidence == "medium"
    assert traf[0].confidence == "low" and "close behind another car" in traf[0].why
    assert traf[0].detail["in_traffic"] is True


def test_battery_findings_do_not_use_the_traffic_step():
    straights, feats = feats_for({(5, 7): 40.0}, n_laps=20)
    f = [x for x in energy.detect_from_features(None, feats, straights, traffic={(5, 7): 1.0}) if x.kind == "battery_low"]
    g = [x for x in energy.detect_from_features(None, feats, straights) if x.kind == "battery_low"]
    assert f[0].confidence == g[0].confidence


# --- 3. Blue flags and 8. the chequered flag -------------------------------------------------------

def rc_frame(rows):
    rc = pd.DataFrame(rows)
    rc["epoch"] = [model.to_epoch(v) for v in rc.date]
    return rc


def test_blue_flags_are_matched_to_the_drivers_own_lap_by_time_stamp_and_spread_one_lap():
    rd = make_rd(n_laps=20, drivers=(1, 2), rc_rows=[
        {"date": iso(T0), "message": "SESSION STARTED", "category": "SessionStatus"},
        # lap_number says 3 (the leader's lap) but the time stamp is in lap 9 of everybody here
        {"date": iso(T0 + 8 * LAP + 10), "message": "WAVED BLUE FLAG FOR CAR 2 (D2) TIMED AT 1", "category": "Flag",
         "flag": "BLUE", "scope": "Driver", "driver_number": 2.0, "lap_number": 3},
        {"date": iso(T0 + 3 * LAP), "message": "WAVED BLUE FLAG FOR CAR (no driver)", "category": "Flag",
         "flag": "BLUE", "driver_number": np.nan}])
    finish_flags(rd)
    assert sorted(l for (d, l) in conditions.blue_flag_laps(rd.rc, rd.laps) if d == 2) == [8, 9, 10]
    assert not [1 for (d, l) in conditions.blue_flag_laps(rd.rc, rd.laps) if d == 1]
    assert rd.laps[(rd.laps.driver_number == 2) & rd.laps.blue].lap_number.tolist() == [8, 9, 10]
    assert not model.analysis_lap(rd.lap_row(2, 9)) and model.analysis_lap(rd.lap_row(1, 9))
    assert model.analysis_lap(rd.lap_row(2, 9), blue=False)    # energy detection keeps the trace


def test_blue_flag_laps_lose_their_lift_but_keep_the_full_throttle_speed_fall():
    straights, feats = feats_for({(5, 7): 35.0}, lift={(5, 7): 2.0, (5, 8): 2.0}, n_laps=12)
    energy.drop_blue_lifts(feats, {(5, 7), (5, 8)})
    assert feats[(5, 7, 0)]["lift"] is None and feats[(5, 7, 0)]["clip"] is not None
    kinds = {f.kind for f in energy.detect_from_features(None, feats, straights) if f.driver == 5}
    assert kinds == {"battery_low"}                              # the lift finding is gone, the power loss stays


def test_the_lap_with_the_chequered_flag_is_not_analysed():
    rd = make_rd(n_laps=14, drivers=(1, 2), rc_rows=[
        {"date": iso(T0), "message": "SESSION STARTED", "category": "SessionStatus"},
        {"date": iso(T0 + 13.5 * LAP), "message": "CHEQUERED FLAG", "category": "Flag", "flag": "CHEQUERED",
         "scope": "Track"}])
    finish_flags(rd)
    assert rd.finish_time == pytest.approx(T0 + 13.5 * LAP, abs=1e-3)
    assert rd.laps[rd.laps.finish].lap_number.unique().tolist() == [14]
    assert not model.analysis_lap(rd.lap_row(1, 14)) and model.usable_lap(rd.lap_row(1, 14))
    assert model.analysis_lap(rd.lap_row(1, 13))


def test_a_lift_under_a_virtual_safety_car_or_yellow_flag_is_never_analysed():
    rd = make_rd(n_laps=12, drivers=(1, 2), rc_rows=[
        {"date": iso(T0), "message": "SESSION STARTED", "category": "SessionStatus"},
        {"date": iso(T0 + 4 * LAP + 10), "message": "VSC DEPLOYED", "category": "SafetyCar", "scope": "Track"},
        {"date": iso(T0 + 5 * LAP + 10), "message": "VSC ENDING", "category": "SafetyCar", "scope": "Track"},
        {"date": iso(T0 + 8 * LAP + 5), "message": "YELLOW IN TRACK SECTOR 4", "category": "Flag", "flag": "YELLOW",
         "scope": "Sector", "sector": 4},
        {"date": iso(T0 + 8 * LAP + 20), "message": "CLEAR IN TRACK SECTOR 4", "category": "Flag", "flag": "CLEAR",
         "scope": "Sector", "sector": 4}])
    finish_flags(rd)
    ok = {l for l in range(1, 13) if model.analysis_lap(rd.lap_row(1, l))}
    assert not ({5, 6, 9} & ok)          # VSC laps, the restart lap and the yellow lap
    assert {2, 3, 4, 8, 12} <= ok


# --- 6. high confidence needs 5 clean laps ---------------------------------------------------------------

def test_energy_saving_high_needs_five_flagged_laps():
    four = {(5, l): 2.5 for l in (6, 7, 8, 9)}
    five = {(5, l): 2.5 for l in (6, 7, 8, 9, 10)}
    st, ft = feats_for({}, lift=four, n_laps=24)
    f4 = [f for f in energy.detect_from_features(None, ft, st) if f.kind == "energy_saving"][0]
    st, ft = feats_for({}, lift=five, n_laps=24)
    f5 = [f for f in energy.detect_from_features(None, ft, st) if f.kind == "energy_saving"][0]
    assert f4.detail["n_laps"] == 4 and f4.confidence == "medium"
    assert f5.detail["n_laps"] == 5 and f5.confidence == "high"


# --- 4. the lift label -------------------------------------------------------------------------------------

def test_lift_label_names_both_causes_and_is_hedged():
    label = story.KIND_LABEL["energy_saving"]
    assert label == "Lifted off early (probably saving energy or cooling the car)"
    assert story.is_hedged(label) and "cooling" in label
    t = story.speed_moment("energy_saving", "Hulkenberg", 67, 1.9, 1.5, None, None)
    for text in (t["caption"], t["social_title"]):
        assert story.is_hedged(text) and "cool" in text
        assert not story.banned_hits(text)


# --- 5. power cut -----------------------------------------------------------------------------------------------

def fleet_feats(cut=None, both=False, laps=range(2, 42), noise=None):
    """8 cars (teams of two), 2 straights. cut = (driver, from_lap, km/h): that car is slower from then on."""
    rng = np.random.default_rng(1)
    feats = {}
    for lap in laps:
        for d in range(1, 9):
            for si in (0, 1):
                v = 300.0 + (rng.normal(0, 1.5) if noise is None else noise(d, lap))
                if cut and (d == cut[0] or (both and d == cut[0] + 1)) and lap >= cut[1]:
                    v -= cut[2]
                feats[(d, lap, si)] = {"clip": {"peak_speed": v}, "lift": None}
    return feats


def teams_rd():
    rd = make_rd(n_laps=5, drivers=tuple(range(1, 9)))
    rd.drivers["team"] = [f"T{(d + 1) // 2}" for d in range(1, 9)]       # 1+2, 3+4, 5+6, 7+8
    return rd


def test_a_sustained_deficit_against_the_teammate_is_found_and_dated():
    dev = powercut.lap_deviation(fleet_feats(cut=(3, 10, 15.0)), None)
    cuts = powercut.detect(dev, {3: 4, 4: 3, 1: 2, 2: 1})
    assert set(cuts) == {3}
    pc = cuts[3]
    assert pc.start_lap == 10 and pc.share >= 0.9 and pc.vs_mate == pytest.approx(-15.0, abs=2.0)
    assert pc.confidence in ("low", "medium")


def test_no_power_cut_for_noise_for_battery_dips_or_a_team_wide_setting():
    assert powercut.detect(powercut.lap_deviation(fleet_feats(), None), {3: 4, 4: 3}) == {}
    # a few bad laps (a battery running low) are not "most of the race"
    dips = fleet_feats()
    for lap in (12, 13, 14, 25, 26):
        for si in (0, 1):
            dips[(3, lap, si)]["clip"]["peak_speed"] -= 25
    assert powercut.detect(powercut.lap_deviation(dips, None), {3: 4, 4: 3}) == {}
    # both cars of the team lose speed: the teammate comparison cannot see it, so nothing is claimed
    both = fleet_feats(cut=(3, 10, 15.0), both=True)
    assert powercut.detect(powercut.lap_deviation(both, None), {3: 4, 4: 3}) == {}
    # a car that is simply slower than the field, but also than its teammate only half the time, is not a cut
    assert powercut.detect({}, {3: 4}) == {}


def test_traffic_laps_are_skipped_and_too_few_clean_laps_mean_no_verdict():
    dev = powercut.lap_deviation(fleet_feats(cut=(3, 10, 15.0)), None)
    skip = {(3, l) for l in range(2, 42, 2)}                      # half of his laps in traffic: 20 clean ones remain
    assert 3 in powercut.detect(dev, {3: 4, 4: 3}, skip)
    # only a tow-inflated comparison is left: say nothing rather than guess (Las Vegas: 67 % of laps in traffic)
    skip_most = {(3, l) for l in range(2, 42) if l % 5}
    assert powercut.detect(dev, {3: 4, 4: 3}, skip_most) == {}
    # and the teammate's traffic laps are skipped too: a tow on one car only is not a deficit
    tow = fleet_feats()
    for lap in range(10, 42):
        for si in (0, 1):
            tow[(4, lap, si)]["clip"]["peak_speed"] += 14          # teammate in a tow: 14 km/h faster
    skip_tow = {(4, l) for l in range(10, 42)}
    assert powercut.detect(powercut.lap_deviation(tow, None), {3: 4, 4: 3}, skip_tow) == {}


def test_a_battery_finding_after_the_power_loss_started_is_relabelled_but_not_one_before_it(monkeypatch):
    from slipstream import powercut as _pc
    monkeypatch.setattr(_pc, "ENABLED", True)   # the detector is off in the pipeline; test it directly
    rd = teams_rd()
    ctx = {"feats": fleet_feats(cut=(3, 10, 15.0)), "straights": []}
    mk = lambda lap, conf="medium": energy.Finding("battery_low", 3, lap, [lap], 0, 25.0, conf, "speed fell",
                                                    {"value": 30.0})
    before, after = mk(6), mk(20)
    other = energy.Finding("battery_low", 1, 20, [20], 0, 25.0, "medium", "speed fell", {"value": 30.0})
    cuts = powercut.apply(rd, [before, after, other], ctx, {})
    assert set(cuts) == {3}
    assert "power_cut" not in before.detail and "power_cut" not in other.detail
    assert after.detail["power_cut"]["start_lap"] == 10
    assert after.confidence in ("low", "medium") and "car problem" in after.why


def test_power_cut_is_a_known_kind_with_a_hedged_label_text_and_slot():
    assert story.is_hedged(story.KIND_LABEL["power_cut"]) and "car problem" in story.KIND_LABEL["power_cut"]
    t = story.speed_moment("power_cut", "Gasly", 6, 26.0, 20.0, "Colapinto", 3.0)
    assert story.is_hedged(t["caption"]) and "car problem" in t["caption"] and not story.banned_hits(t["caption"])
    assert "Gasly" in t["social_title"] and "less power" in t["social_title"]
    assert build.KIND_CAP["power_cut"] >= 1 and build.REF_SCORE["power_cut"] > 0
    from slipstream import validate
    assert "power_cut" in validate.KINDS


def test_the_final_selection_applies_the_kind_cap_to_every_moment():
    mk = lambda d, kind, conf, norm: {"driver": d, "kind": kind, "confidence": conf, "norm": norm}
    cands = [mk(1, "tyre_wear", "medium", 2.0), mk(2, "tyre_wear", "medium", 1.9), mk(3, "tyre_wear", "medium", 1.8),
             mk(4, "tyre_wear", "low", 1.0)]
    assert [m["driver"] for m in build.select_moments(cands)] == [1, 2, 3]        # >= 3 confident ones: lows are hidden
    cands = [mk(1, "tyre_wear", "medium", 2.0), mk(2, "tyre_wear", "low", 1.2), mk(3, "tyre_wear", "low", 1.1),
             mk(4, "tyre_wear", "low", 1.0), mk(5, "tyre_wear", "low", 0.9)]
    # few confident ones: lows fill in, but at most LOW_KIND_CAP (2) of one kind, so never 5 tyre_wear moments
    assert [m["driver"] for m in build.select_moments(cands)] == [1, 2, 3]
    cands += [{"driver": 6, "kind": "battery_low", "confidence": "low", "norm": 0.5}]
    assert [m["driver"] for m in build.select_moments(cands)] == [1, 2, 3, 6]


# --- evaluate ------------------------------------------------------------------------------------------------------

def moment(driver, kind, lap, label=None, conf="medium"):
    return {"driver": driver, "kind": kind, "lap": lap, "confidence": conf, "caption": "",
            "label": label or story.KIND_LABEL[kind]}


def test_validation_cases_file_matches_the_documented_counts():
    cases = evaluate.load_cases()
    assert len(cases) == 19
    by = {v: sum(c["verdict"] == v for c in cases) for v in ("plausible", "unknown", "wrong")}
    assert by == {"plausible": 9, "unknown": 4, "wrong": 6}
    for c in cases:
        assert c["kind"] in story.KIND_LABEL and c["race"].startswith("2026-")
        if c["verdict"] == "wrong":
            assert c["true_cause"] in evaluate.CAUSE_WORDS


def test_evaluate_classifies_present_relabelled_and_gone():
    case = {"race": "r", "driver": "GAS", "kind": "battery_low", "lap": 6, "verdict": "wrong",
            "true_cause": "engine_power_cut"}
    assert evaluate.classify(case, [moment("GAS", "battery_low", 8)])[0] == "present"          # +-3 laps
    assert evaluate.classify(case, [moment("GAS", "battery_low", 10)])[0] == "gone"            # too far
    assert evaluate.classify(case, [moment("VER", "battery_low", 6)])[0] == "gone"             # another driver
    assert evaluate.classify(case, [moment("GAS", "tyre_wear", 6)])[0] == "gone"               # unrelated new finding
    st, m = evaluate.classify(case, [moment("GAS", "power_cut", 7)])
    assert st == "relabelled" and evaluate.judge(case, st, m) is True
    st, m = evaluate.classify(case, [moment("GAS", "battery_low", 6)])
    assert evaluate.judge(case, st, m) is False                                                # still wrong
    assert evaluate.judge(case, *evaluate.classify(case, [])) is True                          # gone = fixed


def test_evaluate_counts_a_lost_plausible_case_as_a_regression_and_checks_the_cooling_label():
    plaus = {"race": "r", "driver": "LEC", "kind": "tyre_wear", "lap": 11, "verdict": "plausible"}
    rows = evaluate.evaluate([plaus], {"r": []})
    assert rows[0]["status"] == "gone" and rows[0]["ok"] is False
    cool = {"race": "r", "driver": "HUL", "kind": "energy_saving", "lap": 67, "verdict": "wrong", "true_cause": "cooling"}
    st, m = evaluate.classify(cool, [moment("HUL", "energy_saving", 67)])
    assert evaluate.judge(cool, st, m) is True                       # the new label names cooling
    st, m = evaluate.classify(cool, [moment("HUL", "energy_saving", 67, label="Probably saving energy")])
    assert evaluate.judge(cool, st, m) is False
    assert evaluate.evaluate([plaus], {})[0]["status"] == "no data"
