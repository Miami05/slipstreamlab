"""Plain-English text for the website and social images.

Rules: last names only (never three-letter codes), no jargon, short
sentences, "probably/likely" for anything inferred rather than measured.
"""
from __future__ import annotations

import re

KIND_LABEL = {
    "battery_low": "Battery ran low",
    "energy_saving": "Saved energy to attack later",
    "tyre_wear": "Tyres wore out",
}

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


def headline(winner: str, gap_s: float | None, sc_laps: list[int], n_moments: int,
             vsc_laps: list[int] | None = None) -> dict:
    if gap_s is None or gap_s <= 0:
        title = f"{winner} won the race."
    else:
        unit = "second" if round(gap_s, 1) == 1.0 else "seconds"
        title = f"{winner} won by {fmt_seconds(gap_s)} {unit}."
    parts = []
    if sc_laps:
        a, b = min(sc_laps), max(sc_laps)
        parts.append(f"A safety car came out on lap {a} and stayed until lap {b}, which bunched the field up.")
    elif vsc_laps:
        a, b = min(vsc_laps), max(vsc_laps)
        when = f"lap {a}" if a == b else f"laps {a} and {b}" if b == a + 1 else f"laps {a} to {b}"
        parts.append(f"The whole field was slowed down for a short time on {when}.")
    if n_moments == 0:
        parts.append("Nothing in the data stood out clearly enough to explain here.")
    elif n_moments == 1:
        parts.append("We found one moment that likely explains a big change in speed.")
    else:
        parts.append(f"We found {n_moments} moments that likely explain big changes in speed.")
    return {"title": title, "text": " ".join(parts[:2])}


def speed_moment(kind: str, last: str, lap: int, value: float, score: float, other: str | None,
                 other_value: float | None) -> dict:
    if kind == "battery_low":
        cmp_txt = ""
        if other and other_value is not None:
            cmp_txt = f" {other} lost {other_value:.0f} km/h on the same stretch."
        return {
            "chart_title": f"Where {last} slowed down · lap {lap}",
            "zone_label": f"{last} slows here",
            "caption": (f"{last} kept the accelerator flat out, but the car still slowed by about "
                        f"{value:.0f} km/h before braking.{cmp_txt} The battery probably ran low."),
            "social_title": f"{last} slowed down. A low battery is the likely reason.",
            "social_sentence": f"Flat out, yet {value:.0f} km/h slower before braking.",
            "big": f"-{value:.0f} km/h",
        }
    return {
        "chart_title": f"Where {last} came off the accelerator · lap {lap}",
        "zone_label": f"{last} coasts here",
        "caption": (f"{last} came off the accelerator for about {value:.1f} seconds before braking. "
                    "That probably saved battery energy for later."),
        "social_title": f"{last} eased off early. Saving energy is the likely reason.",
        "social_sentence": f"{value:.1f} seconds of coasting before the braking point.",
        "big": f"{value:.1f} s",
    }


def wear_moment(last: str, start_lap: int, rise: float, first_lap: int, last_lap: int) -> dict:
    return {
        "chart_title": f"{last}'s lap times · laps {first_lap} to {last_lap}",
        "zone_label": "Tyres worn from here",
        "caption": (f"{last}'s lap times, adjusted for the fuel burning off, climbed by about "
                    f"{rise:.1f} seconds from lap {start_lap}. The tyres had probably worn out."),
        "social_title": f"{last}'s tyres wore out. Lap times fell off from lap {start_lap}.",
        "social_sentence": f"About {rise:.1f} seconds slower per lap, and it stayed that way.",
        "big": f"+{rise:.1f} s",
    }
