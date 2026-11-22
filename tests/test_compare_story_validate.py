import copy

import numpy as np
import pytest

from slipstream import compare, openf1, model, story, validate


def ellipse(speed_scale=1.0, n=400, lap=90.0):
    """A 2:1 ellipse driven at constant speed. raw y grows 'north'."""
    t = np.linspace(0, lap / speed_scale, n)
    a = np.linspace(0, 2 * np.pi, n)
    return t, 4000 * np.cos(a) + 5000, 2000 * np.sin(a) + 1000


def test_scaling_keeps_aspect_ratio_and_flips_y():
    p = compare.analyse(ellipse(), ellipse(1.05), 90.0, 85.7, "Aaa", "Bbb", "AAA", "BBB")
    xs = [q[0] for q in p["track"]]
    ys = [q[1] for q in p["track"]]
    assert min(xs) == pytest.approx(0, abs=1e-3) and max(xs) == pytest.approx(1, abs=1e-3)
    assert (max(ys) - min(ys)) == pytest.approx(0.5, abs=0.01)        # 2:1 shape is not distorted
    assert all(0 <= v <= 1 for v in xs + ys)
    # raw y largest (north) must be drawn at the SMALLEST y (SVG y points down)
    raw_t, raw_x, raw_y = ellipse()
    top_idx = int(np.argmax(raw_y))
    assert p["track"][top_idx][1] == pytest.approx(min(ys), abs=1e-3)


def test_segments_cover_the_track_and_name_the_faster_car():
    p = compare.analyse(ellipse(), ellipse(1.05), 90.0, 85.7, "Aaa", "Bbb", "AAA", "BBB")
    segs = p["segments"]
    assert len(segs) == compare.N_SEGMENTS
    assert segs[0]["from"] == 0 and segs[-1]["to"] == len(p["track"]) - 1
    assert all(s["from"] <= s["to"] < len(p["track"]) for s in segs)
    assert all(a["to"] == b["from"] for a, b in zip(segs, segs[1:]))
    assert {s["faster"] for s in segs} == {"BBB"}          # B is 5 % quicker everywhere
    assert len(p["a_path"]) <= 400 and len(p["track"]) <= 400
    assert p["a_path"][0][0] == 0
    assert p["key_moment_frac"] is None or 0 <= p["key_moment_frac"] <= 1
    assert story.banned_hits(p["summary"]) == []


def test_path_limit_for_long_samples():
    a = ellipse(n=1500)
    b = ellipse(1.02, n=1500)
    p = compare.analyse(a, b, 90.0, 88.2, "Aaa", "Bbb", "AAA", "BBB")
    assert len(p["track"]) <= 400 and len(p["a_path"]) <= 400 and len(p["b_path"]) <= 400


# --- story -----------------------------------------------------------------

def test_banned_words_are_detected():
    for bad in ["super-clipping", "He is P5", "an undercut", "with DRS", "the delta", "second stint"]:
        assert story.banned_hits(bad), bad
    assert story.banned_hits("Norris slowed down. The battery probably ran low.") == []


def test_generated_text_has_no_jargon_or_codes():
    texts = []
    for kind in ("battery_low", "energy_saving"):
        d = story.speed_moment(kind, "Leclerc", 34, 28.0, 14.0, "Norris", 3.0)
        texts += [d["chart_title"], d["zone_label"], d["caption"], d["social_title"], d["social_sentence"]]
    w = story.wear_moment("Hamilton", 31, 1.4, 20, 40)
    texts += [w["chart_title"], w["zone_label"], w["caption"], w["social_title"], w["social_sentence"]]
    h = story.headline("Verstappen", 0.639, [19, 20, 21], 3)
    texts += [h["title"], h["text"]]
    texts += list(story.KIND_LABEL.values())
    for t in texts:
        assert story.banned_hits(t) == [], t
        assert story.bare_codes(t, ["NOR", "LEC", "HAM", "VER"]) == [], t
    assert h["title"] == "Verstappen won by 0.6 seconds."


def test_vsc_headline_is_neutral():
    h = story.headline("Norris", 2.0, [], 1, vsc_laps=[56, 57])
    assert "laps 56 and 57" in h["text"] and "changed the race" not in h["text"]
    assert story.banned_hits(h["text"]) == []


def test_headline_with_no_moments_is_honest():
    h = story.headline("Norris", 12.3, [], 0)
    assert "Nothing" in h["text"]


# --- validators --------------------------------------------------------------

def good_race():
    series = [{"driver": "AAA", "points": [[0.0, 200.0], [1.0, 250.0]]}]
    return {
        "slug": "2026-01-x", "name": "X Grand Prix", "location": "X", "date": "2026-03-08", "total_laps": 50,
        "drivers": [{"number": 1, "code": "AAA", "last_name": "Aaa", "team": "T", "color": "#F47600"}],
        "results": [{"code": "AAA", "position": 1}], "safety_car_laps": [3, 4],
        "headline": {"title": "Aaa won.", "text": "Some text."},
        "moments": [{"id": "m1", "driver": "AAA", "lap": 3, "lap_label": "Lap 3", "kind": "battery_low",
                     "label": "Lost power (maybe battery)", "confidence": "high", "why": "because.",
                     "chart": {"type": "speed", "title": "t", "x_label": "x", "y_label": "y",
                               "zone": [0.5, 0.8], "zone_label": "z", "series": series},
                     "caption": "c"}]}


def good_whatif():
    return {"pit_loss_s": 21.4, "drivers": {"AAA": {"real_pit_lap": 10, "real_finish": 3, "options": [
        {"pit_lap": 9, "finish_estimate": 3, "gain_s": 0.5, "low_s": -1.0, "high_s": 2.0, "traffic": False,
         "nearby": [{"code": "AAA", "gap_s": -1.4}]},
        {"pit_lap": 10, "finish_estimate": 3, "gain_s": 0.0, "low_s": -1.5, "high_s": 1.5, "traffic": True,
         "nearby": []}]}}}


def good_compare():
    return {"pairs": [{"a": "AAA", "b": "BBB", "lap": 5,
                       "track": [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
                       "a_path": [[0.0, 0.1, 0.2], [1.0, 0.3, 0.4]],
                       "b_path": [[0.0, 0.1, 0.2], [1.0, 0.3, 0.4]],
                       "segments": [{"from": 0, "to": 2, "faster": "AAA"}],
                       "lap_time_a": 90.1, "lap_time_b": 90.3, "key_moment_frac": None, "summary": "s"}]}


def test_good_objects_pass():
    validate.validate_race(good_race())
    validate.validate_whatif(good_whatif())
    validate.validate_compare(good_compare())
    validate.validate_index({"generated_at": "2026-11-22T20:00:00Z", "races": [
        {"slug": "b", "session_key": 2, "year": 2026, "round": 21, "name": "n", "location": "l",
         "date": "2026-11-22", "has_compare": True},
        {"slug": "a", "session_key": 1, "year": 2026, "round": 11, "name": "n", "location": "l",
         "date": "2026-07-26", "has_compare": False}]})


def bad(fn, mutate, obj):
    o = copy.deepcopy(obj)
    mutate(o)
    with pytest.raises(validate.ValidationError):
        fn(o)


def test_bad_race_objects_fail():
    r = good_race()
    bad(validate.validate_race, lambda o: o["moments"][0].__setitem__("kind", "overtake"), r)
    bad(validate.validate_race, lambda o: o["moments"][0].__setitem__("confidence", "sure"), r)
    bad(validate.validate_race, lambda o: o["moments"][0]["chart"].__setitem__("type", "pie"), r)
    bad(validate.validate_race, lambda o: o["moments"][0].__setitem__("driver", "ZZZ"), r)
    bad(validate.validate_race, lambda o: o["drivers"][0].__setitem__("color", "orange"), r)
    bad(validate.validate_race, lambda o: o.pop("headline"), r)
    bad(validate.validate_race, lambda o: o["moments"][0]["chart"]["series"][0]["points"].append([1.0, float("nan")]), r)
    bad(validate.validate_race, lambda o: o["moments"].extend([o["moments"][0]] * 8), r)


def test_bad_whatif_and_compare_fail():
    w = good_whatif()
    bad(validate.validate_whatif, lambda o: o["drivers"]["AAA"]["options"][0].__setitem__("low_s", 5.0), w)
    bad(validate.validate_whatif, lambda o: o["drivers"]["AAA"]["options"][0].__setitem__("traffic", "no"), w)
    bad(validate.validate_whatif, lambda o: o["drivers"]["AAA"]["options"][0]["nearby"].extend([{"code": "X", "gap_s": 1}] * 2), w)
    bad(validate.validate_whatif, lambda o: o["drivers"]["AAA"].__setitem__("real_pit_lap", 99), w)
    c = good_compare()
    bad(validate.validate_compare, lambda o: o["pairs"][0]["segments"][0].__setitem__("to", 50), c)
    bad(validate.validate_compare, lambda o: o["pairs"][0]["segments"][0].__setitem__("faster", "ZZZ"), c)
    bad(validate.validate_compare, lambda o: o["pairs"][0]["track"].append([1.5, 0.2]), c)
    bad(validate.validate_compare, lambda o: o["pairs"][0].__setitem__("key_moment_frac", 2.0), c)


def test_bad_index_fails():
    ok = {"slug": "b", "session_key": 2, "year": 2026, "round": 21, "name": "n", "location": "l",
          "date": "2026-11-22", "has_compare": True}
    bad(validate.validate_index, lambda o: o["races"][0].__setitem__("date", "22/11/2026"), {"generated_at": "t", "races": [ok]})
    old = dict(ok, date="2026-01-01")
    with pytest.raises(validate.ValidationError):    # oldest first is wrong
        validate.validate_index({"generated_at": "t", "races": [old, ok]})


def test_cross_file_check():
    r, w, c = good_race(), good_whatif(), good_compare()
    with pytest.raises(validate.ValidationError):    # compare uses BBB who is not in drivers
        validate.validate_cross(r, w, c)
    c["pairs"][0]["b"] = "AAA"
    c["pairs"][0]["segments"][0]["faster"] = "AAA"
    validate.validate_cross(r, w, c)


# --- cache / calendar ---------------------------------------------------------

def test_cache_key_is_stable():
    url = "https://api.openf1.org/v1/laps?session_key=11420"
    assert openf1.cache_key(url) == openf1.cache_key(url)
    assert len(openf1.cache_key(url)) == 40
    import hashlib
    assert openf1.cache_key(url) == "512ecbbe0700e4fc0757335a9e52848b209a6853"    # pinned: changing it would orphan every cache file
    assert openf1.cache_key(url) == hashlib.sha1(url.encode()).hexdigest()
    assert openf1.cache_key(url) != openf1.cache_key(url + "&driver_number=1")


def test_url_building_and_date_filters():
    assert openf1.build_url("laps", {"session_key": 5}) == "https://api.openf1.org/v1/laps?session_key=5"
    u = openf1.build_url("location", {"session_key": 5, "date>": "2026-11-22T04:00:00.000", "date<": "2026-11-22T04:01:00.000"})
    assert "date>=2026-11-22T04:00:00.000" in u and "&date<2026-11-22T04:01:00.000" in u
    assert "+" not in openf1.build_url("x", {"date>": "2026-01-01T00:00:00+00:00"})


def test_get_uses_disk_cache_and_skips_empty(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(openf1.urllib.request, "urlopen", boom)
    url = openf1.build_url("laps", {"session_key": 1})
    p = openf1.cache_path(url, tmp_path)
    p.write_text('[{"a": 1}]')
    assert openf1.get("laps", {"session_key": 1}, cache_dir=tmp_path) == [{"a": 1}]


def test_rounds_skip_cancelled_races(monkeypatch):
    sessions = [{"session_key": i, "date_start": f"2026-{m:02d}-01T00:00:00+00:00", "is_cancelled": c,
                 "location": f"L{i}", "meeting_key": i}
                for i, (m, c) in enumerate([(3, False), (4, True), (5, False), (6, False)], 1)]
    monkeypatch.setattr(openf1, "get", lambda ep, params=None, **k: list(reversed(sessions)))
    assert model.session_for_round(2026, 2)["session_key"] == 3      # the cancelled round is not counted
    assert model.session_for_round(2026, 3)["session_key"] == 4
    assert model.latest_session(2026, now_epoch=model.to_epoch("2026-05-15T00:00:00+00:00"))["round"] == 2


def test_slug_strips_accents():
    assert model.make_slug(2026, 21, "Las Vegas") == "2026-21-las-vegas"
    assert model.make_slug(2026, 7, "Montréal") == "2026-07-montreal"
    assert model.make_slug(2026, 22, "São Paulo") == "2026-22-sao-paulo"
