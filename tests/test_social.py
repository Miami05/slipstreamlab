import numpy as np
from PIL import Image

from slipstream import social


def race_and_moment():
    pts = [[x / 99, 200 + 100 * np.sin(x / 20) - (x > 60) * (x - 60) * 0.7] for x in range(100)]
    pts2 = [[x / 99, 200 + 100 * np.sin(x / 20)] for x in range(100)]
    race = {"name": "Test Grand Prix",
            "drivers": [{"code": "AAA", "last_name": "Leclerc", "color": "#E8002D"},
                        {"code": "BBB", "last_name": "Hamilton", "color": "#E8002D"}]}
    m = {"id": "m1", "driver": "AAA", "lap": 34, "lap_label": "Lap 34", "label": "Lost power (maybe battery)",
         "caption": "Leclerc lost power on the straight. The battery is one likely reason.",
         "chart": {"type": "speed", "title": "Where Leclerc slowed down · lap 34", "x_label": "Along the straight →",
                   "y_label": "Speed (km/h)", "zone": [0.6, 1.0], "zone_label": "Leclerc slows here",
                   "series": [{"driver": "AAA", "points": pts}, {"driver": "BBB", "points": pts2}]}}
    return race, m


def test_png_size_and_safe_zone(tmp_path):
    race, m = race_and_moment()
    path = tmp_path / "m.png"
    social.render_png(race, m, {"m1": {"social_title": "Leclerc lost power. A low battery is one likely reason.",
                                         "social_sentence": "Flat out, yet 28 km/h slower before braking.",
                                         "big": "-28 km/h"}}, path)
    im = np.array(Image.open(path).convert("RGB"))
    assert im.shape == (1920, 1080, 3)
    x0, y0, x1, y1 = social.SAFE
    bg = np.array([0x0E, 0x11, 0x16])
    outside = np.ones(im.shape[:2], bool)
    outside[y0:y1, x0:x1] = False
    assert (np.abs(im.astype(int) - bg).sum(axis=2)[outside] > 0).sum() == 0
    inside = np.abs(im.astype(int) - bg).sum(axis=2)[y0:y1, x0:x1]
    assert (inside > 0).mean() > 0.1            # something is actually drawn in the safe area


def test_long_headline_still_fits(tmp_path):
    race, m = race_and_moment()
    path = tmp_path / "m.png"
    social.render_png(race, m, {"m1": {"social_title": "Leclerc slowed down a very great deal on a long straight. " * 2,
                                         "social_sentence": "x " * 80, "big": "-28 km/h"}}, path)
    im = np.array(Image.open(path).convert("RGB"))
    x0, y0, x1, y1 = social.SAFE
    bg = np.array([0x0E, 0x11, 0x16])
    diff = np.abs(im.astype(int) - bg).sum(axis=2)
    assert diff[:, x1:].sum() == 0 and diff[y1:, :].sum() == 0
