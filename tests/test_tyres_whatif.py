import numpy as np
import pandas as pd
import pytest

from slipstream import tyres, whatif


def test_fuel_correction_sign():
    N = 50
    laps = np.arange(2, 30)
    true_deg = 0.02                       # real tyre wear, s per lap
    fuel_gain = -0.03 * 70 / N            # lighter car: about -0.042 s per lap
    raw = 95.0 + (true_deg + fuel_gain) * laps
    assert np.polyfit(laps, raw, 1)[0] < 0                      # raw slope is negative
    corrected = tyres.fuel_correct(laps, raw, N)
    assert np.polyfit(laps, corrected, 1)[0] == pytest.approx(true_deg, abs=1e-6)   # corrected positive


def test_stint_fit_uses_corrected_times():
    N = 50
    rows = [{"driver": 1, "lap": l, "stint": 1, "compound": "SOFT", "age": l - 2,
             "dur": 95.0 - 0.022 * l} for l in range(2, 22)]
    df = pd.DataFrame(rows)
    df["corr"] = tyres.fuel_correct(df.lap, df.dur, N)
    fit = tyres.stint_fits(df)[0]
    assert fit.raw_slope < 0 < fit.slope


# --- what-if ----------------------------------------------------------------

SOFT = ("C", 1, 0)     # compound, stint start lap, tyre age at start


def run_gain(P, L=20, e2=40):
    info = ((SOFT[0], 1, 0), ("C", 0, L), e2)
    comp = {"C": (0.1, 0.01)}
    return whatif.option_gain(None, [], comp, 1, info, P)


def test_real_lap_has_zero_gain():
    gain, sd, n = run_gain(20)
    assert gain == pytest.approx(0.0) and n == 0


def test_moving_stop_away_from_real_lap_costs_time_when_tyres_wear_linearly():
    earlier, _, _ = run_gain(15)
    later, _, _ = run_gain(25)
    assert earlier == pytest.approx(-2.5)
    assert later == pytest.approx(-2.5)


def test_fresher_second_set_gives_positive_gain():
    # first compound wears fast (0.2), second slowly (0.05): staying out longer on the bad tyre loses time,
    # pitting earlier gains.
    comp = {"A": (0.2, 0.01), "B": (0.05, 0.01)}
    info = (("A", 1, 0), ("B", 0, 20), 40)
    gain, _, _ = whatif.option_gain(None, [], comp, 1, info, 15)
    assert gain > 0


def test_range_never_narrower_than_1_5_seconds():
    low, high = whatif.value_range(2.0, 0.01, False, 22.0)
    assert high - low >= 3.0 and low <= 2.0 <= high
    low, high = whatif.value_range(0.0, 5.0, False, 22.0)
    assert (high - low) / 2 == pytest.approx(1.645 * 5.0)


def test_safety_car_lap_widens_upper_bound():
    plain = whatif.value_range(0.0, 0.5, False, 22.0)
    sc = whatif.value_range(0.0, 0.5, True, 22.0)
    assert sc[1] > plain[1] and sc[0] == plain[0]


class StubRD:
    def code(self, dn):
        return {1: "AAA", 2: "BBB", 3: "CCC", 4: "DDD"}[dn]


def test_nearby_sign_convention_and_traffic():
    # at the end of lap 5: car 2 is 1.0 s EARLIER than us (= ahead), car 3 is 1.5 s later (= behind)
    cum = {1: {5: 100.0}, 2: {5: 99.0}, 3: {5: 101.5}, 4: {5: 140.0}}
    near, traffic = whatif._nearby(StubRD(), cum, 1, P=4, L=1, gain_upto={}, pit_loss=22.0, N=50)
    by = {n["code"]: n["gap_s"] for n in near}
    assert by == {"BBB": -1.0, "CCC": 1.5}
    assert len(near) <= 2
    assert traffic is True            # a car is less than 1.5 s ahead


def test_no_traffic_when_car_ahead_is_far():
    cum = {1: {5: 100.0}, 2: {5: 95.0}, 3: {5: 101.5}}
    _, traffic = whatif._nearby(StubRD(), cum, 1, P=4, L=1, gain_upto={}, pit_loss=22.0, N=50)
    assert traffic is False


def test_earlier_stop_adds_pit_loss_before_the_real_one():
    # we pit at lap 4 (out-lap = 5) but really pit later (L=10): the alternative has paid 22 s by lap 5
    cum = {1: {5: 100.0}, 2: {5: 115.0}}
    near, _ = whatif._nearby(StubRD(), cum, 1, P=4, L=10, gain_upto={}, pit_loss=22.0, N=50)
    assert near[0]["gap_s"] == pytest.approx(115.0 - 122.0)      # car 2 is now ahead by 7 s


def test_nearby_uses_gain_through_that_lap_not_the_maximum():
    cum = {1: {5: 100.0}, 2: {5: 98.0}}
    # cumulative gain grows to 3.0 by lap 4, then falls back to 1.0 at lap 5 (laps after 5 are irrelevant)
    gain_upto = {3: 2.0, 4: 3.0, 5: 1.0, 6: 9.0}
    near, _ = whatif._nearby(StubRD(), cum, 1, P=4, L=1, gain_upto=gain_upto, pit_loss=22.0, N=50)
    assert near[0]["gap_s"] == pytest.approx(98.0 - (100.0 - 1.0))        # gain at lap 5, not 3.0 or 9.0


def test_only_laps_that_differ_widen_the_range():
    info = (("C", 1, 0), ("C", 0, 20), 40)
    comp = {"C": (0.1, 0.01)}
    _, sd_real, n_real = whatif.option_gain(None, [], comp, 1, info, 20)
    _, sd_alt, n_alt = whatif.option_gain(None, [], comp, 1, info, 17)
    assert n_real == 0 and sd_real == 0.0
    assert n_alt > 0 and sd_alt > 0
