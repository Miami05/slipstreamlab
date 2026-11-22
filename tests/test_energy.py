import numpy as np
import pandas as pd
import pytest

from slipstream import energy, model


def make_trace(clip_fall=3.0, lift_s=0.0, n=900):
    """One synthetic lap. Straight = frac 0.30..0.60 flat out, braking at 0.62."""
    frac = np.linspace(0, 1, n)
    t = frac * 90.0
    speed = np.full(n, 120.0)
    thr = np.zeros(n)
    brk = np.zeros(n, bool)
    s = (frac >= 0.30) & (frac < 0.60)
    thr[s] = 100.0
    # speed rises to a peak at 0.45 then falls by `clip_fall` until 0.60
    up = (frac >= 0.30) & (frac < 0.45)
    speed[up] = 120 + (frac[up] - 0.30) / 0.15 * 180
    dn = (frac >= 0.45) & (frac < 0.60)
    speed[dn] = 300 - (frac[dn] - 0.45) / 0.15 * clip_fall
    if lift_s > 0:
        # coast (throttle 0, no brake) for lift_s seconds before braking at 0.62
        k = (frac >= 0.62 - lift_s / 90.0) & (frac < 0.62)
        thr[k] = 0.0
        thr[(frac >= 0.60) & (frac < 0.62 - lift_s / 90.0)] = 100.0
        speed[(frac >= 0.60) & (frac < 0.62)] = 300 - clip_fall
    else:
        thr[(frac >= 0.60) & (frac < 0.62)] = 100.0
        speed[(frac >= 0.60) & (frac < 0.62)] = 300 - clip_fall
    brk[(frac >= 0.62) & (frac < 0.66)] = True
    speed[(frac >= 0.62) & (frac < 0.66)] = 300 - clip_fall - np.linspace(0, 150, ((frac >= 0.62) & (frac < 0.66)).sum())
    dist = frac * 5000.0
    return {"t": t, "speed": speed, "throttle": thr, "brake": brk, "dist": dist, "frac": frac}


def feats_for(lap_falls, lift=None, drivers=range(1, 9), n_laps=10):
    """{(driver, lap, 0): features} - everybody falls `base` km/h; overrides in lap_falls[(d, lap)]."""
    traces = {}
    straights = None
    for d in drivers:
        for lap in range(2, 2 + n_laps):
            fall = lap_falls.get((d, lap), lap_falls.get("base", 3.0))
            traces[(d, lap)] = make_trace(fall, (lift or {}).get((d, lap), 0.0))
    straights = energy.find_straights(traces, min_length_m=300)
    feats = {}
    for (d, lap), tr in traces.items():
        feats[(d, lap, 0)] = {"clip": energy.clip_feature(tr, straights[0]),
                              "lift": energy.lift_feature(tr, straights[0])}
    return straights, feats


def test_finds_the_straight_from_data():
    straights, _ = feats_for({})
    assert len(straights) == 1
    assert straights[0].fs == pytest.approx(0.30, abs=0.01)
    assert straights[0].fb == pytest.approx(0.62, abs=0.01)


def test_clipping_lap_is_found():
    straights, feats = feats_for({(5, 7): 35.0})
    found = energy.detect_from_features(None, feats, straights)
    bl = [f for f in found if f.kind == "battery_low"]
    assert len(bl) == 1
    assert bl[0].driver == 5 and bl[0].lap == 7
    assert bl[0].score > 20


def test_uniform_circuit_wide_drop_is_not_flagged():
    straights, feats = feats_for({"base": 25.0})
    found = energy.detect_from_features(None, feats, straights)
    assert [f for f in found if f.kind == "battery_low"] == []


def test_drivers_own_habit_is_not_flagged():
    # driver 3 always loses 25, everyone else 3: his normal behaviour is not a "moment"
    falls = {(3, lap): 25.0 for lap in range(2, 12)}
    straights, feats = feats_for(falls)
    found = energy.detect_from_features(None, feats, straights)
    assert [f for f in found if f.kind == "battery_low"] == []


def test_lift_and_coast_found_only_when_unusual():
    straights, feats = feats_for({}, lift={(2, 6): 2.0})
    found = energy.detect_from_features(None, feats, straights)
    es = [f for f in found if f.kind == "energy_saving"]
    assert len(es) == 1 and es[0].driver == 2 and es[0].lap == 6


def test_safety_car_and_special_laps_are_excluded():
    base = dict(lap_number=10, neutral=False, restart=False, pit_in=False, pit_out=False,
                yellow=False, t0=0.0, t1=90.0)
    mk = lambda **kw: pd.DataFrame([{**base, **kw}]).itertuples().__next__()
    assert model.usable_lap(mk())
    assert not model.usable_lap(mk(neutral=True))        # safety car / VSC
    assert not model.usable_lap(mk(restart=True))
    assert not model.usable_lap(mk(pit_in=True))
    assert not model.usable_lap(mk(pit_out=True))
    assert not model.usable_lap(mk(yellow=True))
    assert not model.usable_lap(mk(lap_number=1))


def test_collect_traces_skips_safety_car_laps():
    rows = [dict(driver_number=1, lap_number=l, neutral=(l == 5), restart=False, pit_in=False,
                 pit_out=False, yellow=False, t0=0.0, t1=90.0) for l in range(2, 9)]

    class Fake:
        laps = pd.DataFrame(rows)

        def lap_trace(self, dn, lap):
            return make_trace(30.0)

    traces = energy.collect_traces(Fake())
    assert (1, 5) not in traces and (1, 4) in traces


def test_coarse_telemetry_gate():
    # a speed channel that repeats values most of the time cannot be analysed
    sp = np.repeat(np.arange(100.0), 4)
    class Fake:
        drivers = pd.DataFrame(index=[1])
        laps = pd.DataFrame({"t1": [len(sp) * 0.25]})
        race_start = 0.0
        def telemetry(self, dn):
            return {"t": np.arange(len(sp)) * 0.25, "speed": sp}
    assert energy.telemetry_quality(Fake()) > energy.MAX_REPEATED_SPEED
