"""Plain-English text for the website and social images.

Rules: last names only (never three-letter codes), no jargon, short
sentences, "probably/likely" for anything inferred rather than measured.
"""
from __future__ import annotations

import re

# Causes are inferred, never measured: every label is hedged (docs/LEGAL.md). We can see that a
# car lost power on a straight; the battery is only one possible reason (an engine-temperature
# limit, for example, looks the same), so the battery label says "maybe". Lifting off early can be
# saving energy or cooling the car (a driver said so in Mexico), so the label names both.
KIND_LABEL = {
    "battery_low": "Lost power (maybe battery)",
    "energy_saving": "Lifted off early (probably saving energy or cooling the car)",
    "tyre_wear": "Tyres probably wore out",
    "power_cut": "Had less power for most of the race (probably a car problem)",
}
HEDGE_RE = re.compile(r"\b(probably|likely|maybe|possibly|may be|might)\b", re.I)


def is_hedged(text: str) -> bool:
    return bool(HEDGE_RE.search(text or ""))

# Words/patterns that must never appear in user-facing text.
BANNED_PATTERNS = [
    r"super-?clipp\w*", r"\bclipp\w*", r"\bP\d{1,2}\b", r"\bundercut\w*", r"\bovercut\w*",
    r"\bDRS\b", r"\bdelta\b", r"\bstints?\b", r"lift[- ]and[- ]coast", r"\bERS\b",
    r"\bharvest\w*", r"\bdegradation\b", r"\bcompound\w*", r"\btelemetry\b",
]
_BANNED = [re.compile(p, re.I if p != r"\bDRS\b" and p != r"\bP\d{1,2}\b" and p != r"\bERS\b" else 0)
           for p in BANNED_PATTERNS]


def banned_hits(text: str) -> list[str]:
    return [m.group(0) for rx in _BANNED for m in rx.finditer(text or "")]


def bare_codes(text: str, codes) -> list[str]:
    """Three-letter driver codes used as words (e.g. 'NOR')."""
    return [c for c in codes if re.search(rf"\b{re.escape(c)}\b", text or "")]


def fmt_seconds(x: float) -> str:
    return f"{x:.1f}" if x < 10 else f"{x:.0f}"


def lap_runs(laps) -> list[tuple[int, int]]:
    """[19, 20, 21, 40] -> [(19, 21), (40, 40)]: consecutive laps grouped into spells."""
    runs: list[list[int]] = []
    for lap in sorted(set(int(x) for x in laps)):
        if runs and lap == runs[-1][1] + 1:
            runs[-1][1] = lap
        else:
            runs.append([lap, lap])
    return [(a, b) for a, b in runs]


def _when(a: int, b: int) -> str:
    return f"lap {a}" if a == b else f"laps {a} and {b}" if b == a + 1 else f"laps {a} to {b}"


def _when_all(runs: list[tuple[int, int]]) -> str:
    return " and again on ".join(_when(a, b) for a, b in runs)


def rain_sentence(rain_laps: list[int], total_laps: int) -> str:
    """One plain sentence about the rain (laps are the winner's laps that were wet)."""
    runs = lap_runs(rain_laps)
    if not runs:
        return ""
    first, last = runs[0][0], runs[-1][1]
    if len(runs) == 1 and first <= 2 and last >= total_laps - 1:
        return "It rained for the whole race, so we left tyre and power readings out."
    if len(runs) == 1 and last >= total_laps - 1:
        return f"Rain came on lap {first} and stayed until the end, so we left those laps out of the tyre and power readings."
    if len(runs) == 1 and first <= 2:
        return f"The track was wet at the start and dried out by lap {last + 1}, so we left the wet laps out of the tyre and power readings."
    return (f"Rain came and went, on {_when_all(runs)}, so we left those laps out of the tyre and power readings.")


def red_sentence(red_laps: list[int], abandoned_lap: int | None = None) -> str:
    """abandoned_lap: set only when race control never restarted the race (known from the data)."""
    if abandoned_lap:
        return f"The race was stopped with a red flag on lap {abandoned_lap} and was not restarted."
    runs = lap_runs(red_laps or [])
    if not runs:
        return ""
    if len(runs) == 1:
        return f"The race was stopped with a red flag on lap {runs[0][0]} and started again later."
    return f"The race was stopped with a red flag more than once, on {_when_all(runs)}."


def headline(winner: str, gap_s: float | None, sc_laps: list[int], n_moments: int,
             vsc_laps: list[int] | None = None, rain_laps: list[int] | None = None,
             total_laps: int = 0, red_laps: list[int] | None = None, n_low: int = 0,
             red_abandoned_lap: int | None = None) -> dict:
    """n_moments = all moments shown, n_low = how many of them are low confidence (hedged apart)."""
    if gap_s is None or gap_s <= 0:
        title = f"{winner} won the race."
    else:
        unit = "second" if round(gap_s, 1) == 1.0 else "seconds"
        title = f"{winner} won by {fmt_seconds(gap_s)} {unit}."
    runs = lap_runs(sc_laps or [])
    sc = ""
    if len(runs) == 1:
        a, b = runs[0]
        sc = (f"A safety car came out on lap {a}, which bunched the field up." if a == b else
              f"A safety car came out on lap {a} and stayed until lap {b}, which bunched the field up.")
    elif runs:
        sc = f"A safety car came out more than once, on {_when_all(runs)}, which bunched the field up."
    vruns = lap_runs(vsc_laps or [])
    vsc = ""
    if vruns:
        lead = "The whole field was slowed down for a short time" if not runs else \
            "The whole field was also slowed down for a short time"
        vsc = f"{lead} on {_when_all(vruns)}."
    n_low = max(0, min(n_low, n_moments))
    n_sure = n_moments - n_low
    if n_moments == 0:
        found = "Nothing in the data stood out clearly enough to explain here."
    elif n_sure == 0:
        found = ("We found one moment that may explain a change in speed, but we are not very sure about it."
                 if n_low == 1 else
                 f"We found {n_low} moments that may explain changes in speed, but we are not very sure about them.")
    else:
        found = ("We found one moment that likely explains a big change in speed"
                 if n_sure == 1 else f"We found {n_sure} moments that likely explain big changes in speed")
        if n_low:
            found += (", plus one less certain one." if n_low == 1 else f", plus {n_low} less certain ones.")
        else:
            found += "."
    red = red_sentence(red_laps or [], red_abandoned_lap)
    parts = [x for x in (sc, red, rain_sentence(rain_laps or [], total_laps), vsc, found) if x]
    if len(parts) > (4 if red else 3) and vsc:
        parts.remove(vsc)            # at most 3 sentences (4 with a red flag): the short slow spell matters least
    return {"title": title, "text": " ".join(parts)}


def speed_moment(kind: str, last: str, lap: int, value: float, score: float, other: str | None,
                 other_value: float | None, persistent: bool = False) -> dict:
    if kind == "battery_low":
        cmp_txt = ""
        if other and other_value is not None:
            cmp_txt = (f" {other} lost {other_value:.0f} km/h on the same stretch." if other_value >= 1
                       else f" {other} did not slow down on the same stretch.")
        reason = "The battery is one likely reason."
        if persistent:
            reason += " This happened on most of his laps, which can also mean a problem with the car."
        return {
            "chart_title": f"Where {last} slowed down · lap {lap}",
            "zone_label": f"{last} slows here",
            "caption": (f"{last} kept the accelerator flat out, but the car lost power on the straight and slowed by "
                        f"about {value:.0f} km/h before braking.{cmp_txt} {reason}"),
            "social_title": f"{last} lost power. A low battery is one likely reason.",
            "social_sentence": f"Flat out, yet {value:.0f} km/h slower before braking.",
            "big": f"-{value:.0f} km/h",
        }
    if kind == "power_cut":
        cmp_txt = ""
        if other and other_value is not None and other_value >= 1:
            cmp_txt = f" {other} lost {other_value:.0f} km/h on the same stretch."
        return {
            "chart_title": f"Where {last} slowed down · lap {lap}",
            "zone_label": f"{last} slows here",
            "caption": (f"{last} kept the accelerator flat out, but the car slowed by about {value:.0f} km/h before "
                        f"braking.{cmp_txt} He was slower than his teammate in a straight line for most of the race, "
                        "which probably points to a car problem rather than a low battery."),
            "social_title": f"{last} had less power for most of the race. A car problem is the likely reason.",
            "social_sentence": f"Flat out, yet {value:.0f} km/h slower before braking.",
            "big": f"-{value:.0f} km/h",
        }
    return {
        "chart_title": f"Where {last} came off the accelerator · lap {lap}",
        "zone_label": f"{last} coasts here",
        "caption": (f"{last} came off the accelerator for about {value:.1f} seconds before braking. "
                    "That was probably to save battery energy for later, or to cool the car."),
        "social_title": f"{last} eased off early, probably to save energy or cool the car.",
        "social_sentence": f"{value:.1f} seconds of coasting before the braking point.",
        "big": f"{value:.1f} s",
    }


def wear_moment(last: str, start_lap: int, rise: float, first_lap: int, last_lap: int) -> dict:
    return {
        "chart_title": f"{last}'s lap times · laps {first_lap} to {last_lap}",
        "zone_label": "Tyres probably worn from here",
        "caption": (f"{last}'s lap times, adjusted for the fuel burning off, climbed by about "
                    f"{rise:.1f} seconds from lap {start_lap}. The tyres had probably worn out."),
        "social_title": f"{last}'s tyres probably wore out. Lap times fell off from lap {start_lap}.",
        "social_sentence": f"About {rise:.1f} seconds slower per lap, and it stayed that way.",
        "big": f"+{rise:.1f} s",
    }
