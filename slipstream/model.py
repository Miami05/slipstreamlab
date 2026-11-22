"""Load one race from OpenF1 and prepare it for analysis.

Everything the other modules need lives here: drivers, laps with absolute
timings, which laps are "neutralised" (safety car / virtual safety car /
yellow flags / pit in- and out-laps), cleaned telemetry per lap, and the list
of pit stops.

Times are epoch seconds (float) internally.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import openf1


# A lap is dropped if the telemetry has a hole longer than this. Normal spacing
# is ~0.27 s (about 4 holes of 0.5-1 s per lap are normal in OpenF1 data).
MAX_SAMPLE_GAP_S = 1.2


def to_epoch(value) -> float:
    """ISO timestamp string -> epoch seconds (NaN if missing)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return float("nan")
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.timestamp()


def as_int(value, default: int = 0) -> int:
    """int(value), but None / NaN / garbage give `default` (a pandas NaN is truthy, so `x or 0` is not enough)."""
    try:
        if value is None or pd.isna(value):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def truthy(value) -> bool:
    """bool(value) with None / NaN counting as False."""
    try:
        if value is None or pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return bool(value)


def epoch_seconds(dates) -> np.ndarray:
    """ISO timestamps (strings or datetimes) -> epoch seconds, whatever the datetime resolution.

    `.astype("int64")` of a datetime column returns nanoseconds under pandas 2 but
    microseconds/milliseconds when pandas infers a coarser unit (always so in pandas 3),
    which made every telemetry timestamp 1000x too small. Subtracting the epoch and
    asking for total_seconds() does not depend on the unit.
    """
    ts = pd.to_datetime(pd.Series(dates), format="ISO8601", utc=True)
    return (ts - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy(float)


def parse_gmt_offset(value) -> float:
    """OpenF1 gmt_offset ('-08:00:00', '02:00:00', '+05:30:00') -> seconds (0 if missing/odd)."""
    m = re.fullmatch(r"\s*([+-]?)(\d{1,2}):(\d{2})(?::(\d{2}))?\s*", str(value or ""))
    if not m:
        return 0.0
    secs = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4) or 0)
    return -float(secs) if m.group(1) == "-" else float(secs)


def local_date(session: dict) -> str:
    """Calendar date of the session start in the circuit's local time (YYYY-MM-DD).

    date_start is UTC: a night race in the Americas starts on the next UTC day.
    """
    import datetime as _dt
    start = _dt.datetime.fromisoformat(str(session["date_start"]).replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=_dt.timezone.utc)
    local = start + _dt.timedelta(seconds=parse_gmt_offset(session.get("gmt_offset")))
    return local.date().isoformat()


def kebab(text: str) -> str:
    """'São Paulo' -> 'sao-paulo' (ASCII, lower case, dashes)."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def make_slug(year: int, rnd: int, location: str) -> str:
    return f"{year}-{rnd:02d}-{kebab(location)}"


# ----------------------------------------------------------------------------
# Calendar helpers
# ----------------------------------------------------------------------------

def race_sessions(year: int) -> list[dict]:
    """Non-cancelled race sessions of a year, oldest first. Index + 1 = round."""
    rows = openf1.get("sessions", {"year": year, "session_name": "Race"})
    rows = [r for r in rows if not r.get("is_cancelled")]
    return sorted(rows, key=lambda r: r["date_start"])


def session_for_round(year: int, rnd: int) -> dict:
    sessions = race_sessions(year)
    if not 1 <= rnd <= len(sessions):
        raise SystemExit(f"Round {rnd} not found for {year} (only {len(sessions)} races).")
    return {**sessions[rnd - 1], "round": rnd}


def latest_session(year: int, now_epoch: float | None = None) -> dict:
    """Most recent non-cancelled race whose start is in the past."""
    import time
    now = now_epoch if now_epoch is not None else time.time()
    sessions = race_sessions(year)
    def finished(s):   # a race still running (date_end in the future) is not finished
        end = to_epoch(s.get("date_end")) if s.get("date_end") else to_epoch(s["date_start"]) + 3 * 3600
        return end < now
    done = [(i + 1, s) for i, s in enumerate(sessions) if finished(s)]
    if not done:
        raise SystemExit(f"No finished race found for {year}.")
    rnd, s = done[-1]
    return {**s, "round": rnd}


def meeting_name(meeting_key: int, fallback: str) -> str:
    rows = openf1.get("meetings", {"meeting_key": meeting_key})
    return rows[0]["meeting_name"] if rows else fallback


# ----------------------------------------------------------------------------
# Race container
# ----------------------------------------------------------------------------

@dataclass
class Stop:
    driver: int
    in_lap: int
    compound_before: str
    compound_after: str
    new_age: int
    lane_s: float | None
    green: bool


@dataclass
class RaceData:
    session: dict
    drivers: pd.DataFrame          # index driver_number
    laps: pd.DataFrame             # one row per driver/lap
    stints: pd.DataFrame
    pits: pd.DataFrame
    result: pd.DataFrame
    rc: pd.DataFrame
    total_laps: int
    race_start: float
    windows: list = field(default_factory=list)       # (kind, start, end) neutral windows
    yellow_windows: list = field(default_factory=list)
    stops: list = field(default_factory=list)
    _tele: dict = field(default_factory=dict)
    _lapkey: dict = field(default_factory=dict)

    # -- convenience --------------------------------------------------------
    @property
    def key(self) -> int:
        return self.session["session_key"]

    def code(self, dn: int) -> str:
        return self.drivers.loc[dn, "code"]

    def dn_of(self, code: str) -> int:
        return int(self.drivers.index[self.drivers.code == code][0])

    def last_name(self, code: str) -> str:
        return self.drivers.loc[self.dn_of(code), "last_name"]

    def team(self, dn: int) -> str:
        return self.drivers.loc[dn, "team"]

    def teammate(self, dn: int) -> int | None:
        t = self.team(dn)
        mates = [d for d in self.drivers.index if d != dn and self.drivers.loc[d, "team"] == t]
        return mates[0] if mates else None

    def lap_row(self, dn: int, lap: int):
        i = self._lapkey.get((int(dn), int(lap)))
        return None if i is None else self.laps.loc[i]

    def safety_car_laps(self, kind: str = "SC") -> list[int]:
        """Lap numbers (by the winner's timing) under a real safety car (kind="SC") or VSC (>= 20% of the lap)."""
        win = int(self.result.driver_number.iloc[0])
        out = []
        for _, r in self.laps[self.laps.driver_number == win].iterrows():
            for k, a, b in self.windows:
                if k != kind:
                    continue
                ov = min(r.t1, b) - max(r.t0, a)
                if ov > 0.2 * (r.t1 - r.t0):
                    out.append(int(r.lap_number))
                    break
        return sorted(set(out))

    # -- telemetry ----------------------------------------------------------
    def telemetry(self, dn: int) -> dict:
        """Cleaned car_data arrays for one driver (cached in memory)."""
        if dn in self._tele:
            return self._tele[dn]
        rows = openf1.get("car_data", {"session_key": self.key, "driver_number": int(dn)})
        df = pd.DataFrame(rows)
        if df.empty:
            self._tele[dn] = {}
            return {}
        t = epoch_seconds(df["date"])
        speed = pd.to_numeric(df["speed"], errors="coerce").to_numpy(float)
        thr = pd.to_numeric(df["throttle"], errors="coerce").to_numpy(float)
        brk = pd.to_numeric(df["brake"], errors="coerce").to_numpy(float)
        # a sample with a missing channel is dropped (the lap check then rejects laps with a real hole)
        good = np.isfinite(t) & np.isfinite(speed) & np.isfinite(thr) & np.isfinite(brk)
        t, speed, thr, brk = t[good], speed[good], thr[good], brk[good]
        order = np.argsort(t)
        t, speed, brk = t[order], speed[order], brk[order]
        thr = np.clip(thr[order], 0, 100)
        if len(t) == 0:
            self._tele[dn] = {}
            return {}
        # Median-filter(3) removes one-sample blips.
        thr = _med3(thr)
        brk = _med3(brk)
        # A brake channel stuck at 100 while the throttle is pinned is a glitch.
        brake_on = (brk >= 50) & (thr <= 20)
        self._tele[dn] = {"t": t, "speed": speed, "throttle": thr, "brake": brake_on}
        return self._tele[dn]

    def lap_trace(self, dn: int, lap: int) -> dict | None:
        """Telemetry of one lap: t (s from lap start), speed, throttle, brake, dist (m), frac."""
        row = self.lap_row(dn, lap)
        if row is None or not np.isfinite(row.t0) or not np.isfinite(row.t1):
            return None
        tele = self.telemetry(dn)
        if not tele:
            return None
        a = np.searchsorted(tele["t"], row.t0, "left")
        b = np.searchsorted(tele["t"], row.t1, "left")
        if b - a < 40:
            return None
        t = tele["t"][a:b] - row.t0
        if np.max(np.diff(t)) > MAX_SAMPLE_GAP_S + 1e-3 or t[-1] < 0.9 * (row.t1 - row.t0):
            return None
        sp = tele["speed"][a:b]
        if not (np.all(np.isfinite(sp)) and np.all(np.isfinite(tele["throttle"][a:b]))):
            return None
        dist = np.concatenate([[0.0], np.cumsum((sp[1:] + sp[:-1]) / 2 / 3.6 * np.diff(t))])
        if not np.isfinite(dist[-1]) or dist[-1] < 1000:
            return None
        return {"t": t, "speed": sp, "throttle": tele["throttle"][a:b],
                "brake": tele["brake"][a:b], "dist": dist, "frac": dist / dist[-1]}


def _med3(x: np.ndarray) -> np.ndarray:
    if len(x) < 3:
        return x
    y = x.copy()
    y[1:-1] = np.median(np.stack([x[:-2], x[1:-1], x[2:]]), axis=0)
    return y


# ----------------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------------

def load_race(session: dict) -> RaceData:
    key = session["session_key"]
    ttl = openf1.recent_ttl(session)       # results of a race that just ended are provisional
    g = lambda ep: openf1.get(ep, {"session_key": key},
                              max_age=ttl if ep in ("laps", "session_result") else None)

    drv = pd.DataFrame(g("drivers")).drop_duplicates("driver_number")
    last = drv.last_name.fillna("").astype(str)
    drv = pd.DataFrame({
        "driver_number": drv.driver_number.astype(int),
        "code": drv.name_acronym,
        "last_name": [x.title() if x.isupper() else x for x in last],
        "team": drv.team_name,
        "color": "#" + drv.team_colour.fillna("888888").str.upper(),
    }).set_index("driver_number")

    laps = pd.DataFrame(g("laps"))
    stints = pd.DataFrame(g("stints"))
    pits = pd.DataFrame(g("pit"))
    result = pd.DataFrame(g("session_result"))
    rc = pd.DataFrame(g("race_control"))
    for col in ("flag", "scope", "sector"):
        if col not in rc:
            rc[col] = None

    result = _normalize_result(result)
    pits = _normalize_pits(pits)
    result = result.sort_values("position", na_position="last").reset_index(drop=True)
    total_laps = int(result.number_of_laps.max())

    # --- lap timings: absolute start/end per lap ---------------------------
    laps = laps.sort_values(["driver_number", "lap_number"]).reset_index(drop=True)
    laps["lap_number"] = laps.lap_number.astype(int)
    laps["driver_number"] = laps.driver_number.astype(int)
    laps["t0"] = [to_epoch(v) for v in laps.date_start]
    laps["dur"] = laps.lap_duration.astype(float)
    rc["epoch"] = [to_epoch(v) for v in rc.date]
    started = rc[rc.message.str.contains("SESSION STARTED", na=False)]
    race_start = float(started.epoch.iloc[0]) if len(started) else float(laps.t0[laps.lap_number == 1].min())

    laps["t1"] = np.nan
    for dn, grp in laps.groupby("driver_number", sort=False):
        idx = grp.index.to_numpy()
        t0 = grp.t0.to_numpy(float).copy()
        dur = grp.dur.to_numpy(float)
        ln = grp.lap_number.to_numpy(int)
        if ln[0] == 1 and np.isnan(t0[0]):
            t0[0] = race_start
        for i in range(1, len(t0)):
            if np.isnan(t0[i]) and np.isfinite(t0[i - 1]) and np.isfinite(dur[i - 1]):
                t0[i] = t0[i - 1] + dur[i - 1]
        end = np.full(len(t0), np.nan)
        for i in range(len(t0)):
            nxt = t0[i + 1] if i + 1 < len(t0) and ln[i + 1] == ln[i] + 1 else np.nan
            if np.isfinite(nxt):
                end[i] = nxt
            elif np.isfinite(t0[i]) and np.isfinite(dur[i]):
                end[i] = t0[i] + dur[i]
        laps.loc[idx, "t0"] = t0
        laps.loc[idx, "t1"] = end

    stints = normalize_stints(stints, laps)
    rd = RaceData(session=session, drivers=drv, laps=laps, stints=stints, pits=pits,
                  result=result, rc=rc, total_laps=total_laps, race_start=race_start)
    rd._lapkey = {(int(r.driver_number), int(r.lap_number)): i for i, r in laps.iterrows()}

    _build_windows(rd)
    _flag_laps(rd)
    _build_stops(rd)
    return rd


def _normalize_result(result: pd.DataFrame) -> pd.DataFrame:
    """Clean types of session_result: null dns/dnf -> False, missing laps -> 0, duration numeric."""
    result = result.copy()
    for col in ("dns", "dnf", "dsq"):
        result[col] = [truthy(v) for v in result[col]] if col in result else False
    result["number_of_laps"] = [as_int(v) for v in result["number_of_laps"]] if "number_of_laps" in result else 0
    result["duration"] = (pd.to_numeric(result["duration"], errors="coerce")
                          if "duration" in result else float("nan"))
    if "position" not in result:
        result["position"] = float("nan")
    return result


def _normalize_pits(pits: pd.DataFrame) -> pd.DataFrame:
    pits = pits.copy()
    for col in ("driver_number", "date", "lap_number", "lane_duration"):
        if col not in pits:
            pits[col] = pd.Series(dtype=float)
    return pits


def normalize_stints(stints: pd.DataFrame, laps: pd.DataFrame) -> pd.DataFrame:
    """Integer stints without holes.

    A null in a pandas column turns it into floats with NaN (which is truthy and
    cannot be int()-ed). Rows without a start lap are dropped, a missing lap_end
    (an unfinished stint) becomes the driver's last lap, a missing tyre age is 0.
    """
    cols = ["driver_number", "stint_number", "lap_start", "lap_end", "compound", "tyre_age_at_start"]
    st = stints.copy()
    for c in cols:
        if c not in st:
            st[c] = pd.Series(dtype=object if c == "compound" else float)
    for c in ("driver_number", "stint_number", "lap_start", "lap_end", "tyre_age_at_start"):
        st[c] = pd.to_numeric(st[c], errors="coerce")
    st = st[st.driver_number.notna() & st.lap_start.notna()].copy()
    last_lap = laps.groupby("driver_number").lap_number.max().to_dict() if len(laps) else {}
    st["lap_end"] = [le if pd.notna(le) else last_lap.get(int(dn), ls)
                     for le, dn, ls in zip(st.lap_end, st.driver_number, st.lap_start)]
    st["tyre_age_at_start"] = st.tyre_age_at_start.fillna(0)
    st["stint_number"] = [sn if pd.notna(sn) else i + 1 for i, sn in enumerate(st.stint_number)]
    for c in ("driver_number", "stint_number", "lap_start", "lap_end", "tyre_age_at_start"):
        st[c] = st[c].astype(int)
    st["compound"] = st.compound.fillna("UNKNOWN").astype(str)
    return st.reset_index(drop=True)


def _build_windows(rd: RaceData) -> None:
    """Safety-car / VSC windows (from race-control messages) and yellow-flag windows."""
    rc = rd.rc.sort_values("epoch")
    win_driver = int(rd.result.driver_number.iloc[0])
    wl = rd.laps[rd.laps.driver_number == win_driver]

    def end_of_leader_lap(ts: float) -> float:
        hit = wl[(wl.t0 <= ts) & (wl.t1 > ts)]
        return float(hit.t1.iloc[0]) if len(hit) else ts

    start = {"SC": None, "VSC": None}
    for _, r in rc[rc.category == "SafetyCar"].iterrows():
        msg = str(r.message).upper()
        kind = "VSC" if ("VSC" in msg or "VIRTUAL" in msg) else "SC"
        if "DEPLOYED" in msg:
            if kind == "SC" and start["VSC"] is not None:
                # A VSC that becomes a full safety car is never announced as "ending":
                # close it here, or it would stay open until the end of the race.
                rd.windows.append(("VSC", start["VSC"], r.epoch))
                start["VSC"] = None
            if start[kind] is None:
                start[kind] = r.epoch
        elif ("IN THIS LAP" in msg or "ENDING" in msg or "ENDED" in msg) and start[kind] is not None:
            rd.windows.append((kind, start[kind], end_of_leader_lap(r.epoch)))
            start[kind] = None
    race_end = float(np.nanmax(rd.laps.t1)) if len(rd.laps) else float("nan")
    for kind, s in start.items():          # never ended (rare): until the end
        if s is not None:
            rd.windows.append((kind, s, race_end))

    # Red flag: from the red flag to the message that says the race resumes (never resumed: until the end).
    # Laps in that window are neutral (nobody races), and the first green lap after it is a restart lap.
    red = None
    for _, r in rc.iterrows():
        msg = str(r.message).upper()
        if red is None:
            if str(r.flag).upper() == "RED" and str(r.get("scope")) in ("Track", "None", "nan"):
                red = r.epoch
        elif "RESUME" in msg or "SESSION STARTED" in msg or "RACE RESTART" in msg:
            rd.windows.append(("RED", red, r.epoch))
            red = None
    if red is not None:
        rd.windows.append(("RED", red, race_end))

    open_y: dict = {}
    for _, r in rc[rc.category == "Flag"].iterrows():
        f = str(r.flag).upper()
        sector = r.get("sector")
        if f in ("YELLOW", "DOUBLE YELLOW"):
            open_y.setdefault(sector, r.epoch)
        elif f in ("CLEAR", "GREEN"):
            if f == "GREEN" or str(r.get("scope")) == "Track":
                for s in open_y.values():
                    rd.yellow_windows.append((s, r.epoch))
                open_y = {}
            elif sector in open_y:
                rd.yellow_windows.append((open_y.pop(sector), r.epoch))
    for s in open_y.values():
        rd.yellow_windows.append((s, s + 120.0))


def _overlaps(t0: float, t1: float, windows) -> bool:
    return any(min(t1, b) > max(t0, a) for a, b in windows)


def _flag_laps(rd: RaceData) -> None:
    laps = rd.laps
    neutral_w = [(a, b) for _, a, b in rd.windows]
    ok = np.isfinite(laps.t0) & np.isfinite(laps.t1)
    laps["neutral"] = [bool(o and _overlaps(a, b, neutral_w)) for o, a, b in zip(ok, laps.t0, laps.t1)]
    laps["yellow"] = [bool(o and _overlaps(a, b, rd.yellow_windows)) for o, a, b in zip(ok, laps.t0, laps.t1)]
    laps["restart"] = False
    for dn, grp in laps.groupby("driver_number"):
        prev = False
        for i, r in grp.iterrows():
            if prev and not r.neutral:
                laps.loc[i, "restart"] = True
            prev = r.neutral

    laps["pit_in"] = False
    laps["pit_out"] = ([truthy(v) for v in laps.is_pit_out_lap]
                       if "is_pit_out_lap" in laps else False)
    st = rd.stints.sort_values(["driver_number", "stint_number"])
    for dn, grp in st.groupby("driver_number"):
        rows = grp.to_dict("records")
        for a, b in zip(rows[:-1], rows[1:]):
            _mark(rd, dn, as_int(a["lap_end"], -1), "pit_in")
            _mark(rd, dn, as_int(b["lap_start"], -1), "pit_out")
    for r in rd.pits.itertuples():
        dn = as_int(r.driver_number, -1)
        in_lap = as_int(r.lap_number, -1)      # OpenF1's own lap number of the stop = the in-lap
        if in_lap < 0:
            # No lap number: the pit timestamp falls inside the OUT-lap (it is stamped when the car
            # leaves the pit lane), so the in-lap is the lap before the one that contains it.
            ts = to_epoch(r.date)
            hit = laps[(laps.driver_number == dn) & (laps.t0 <= ts) & (laps.t1 > ts)]
            if not len(hit):
                continue
            in_lap = int(hit.lap_number.iloc[0]) - 1
        _mark(rd, dn, in_lap, "pit_in")
        _mark(rd, dn, in_lap + 1, "pit_out")


def _mark(rd: RaceData, dn: int, lap: int, col: str) -> None:
    i = rd._lapkey.get((int(dn), lap))
    if i is not None:
        rd.laps.loc[i, col] = True


def usable_lap(r, *, allow_yellow: bool = False) -> bool:
    """Lap usable for pace/energy analysis: green, not lap 1, not pit in/out, not restart."""
    if r.lap_number <= 1 or r.neutral or r.restart or r.pit_in or r.pit_out:
        return False
    if r.yellow and not allow_yellow:
        return False
    return bool(np.isfinite(r.t0) and np.isfinite(r.t1))


def _build_stops(rd: RaceData) -> None:
    st = rd.stints.sort_values(["driver_number", "stint_number"])
    pits = rd.pits.copy()
    pits["epoch"] = [to_epoch(v) for v in pits.date]
    sc_w = [(x, y) for _, x, y in rd.windows]
    for dn, grp in st.groupby("driver_number"):
        rows = grp.to_dict("records")
        for a, b in zip(rows[:-1], rows[1:]):
            in_lap = as_int(a["lap_end"], -1)
            lr = rd.lap_row(int(dn), in_lap)
            lane, green = None, False
            if lr is not None and np.isfinite(lr.t0):
                nx = rd.lap_row(int(dn), in_lap + 1)
                end = nx.t1 if (nx is not None and np.isfinite(nx.t1)) else lr.t1
                hit = pits[(pits.driver_number == dn) & (pits.epoch >= lr.t0) & (pits.epoch < end)]
                if len(hit):
                    v = hit.lane_duration.iloc[0]
                    lane = float(v) if pd.notna(v) else None
                    ts = float(hit.epoch.iloc[0])
                    green = not _overlaps(ts - 1, ts + 1, sc_w)
                    green = (green and not bool(lr.neutral) and not bool(nx is not None and nx.neutral)
                             and not bool(lr.restart))      # a restart-lap stop is not a normal green stop
            rd.stops.append(Stop(int(dn), in_lap, str(a["compound"]), str(b["compound"]),
                                 as_int(b.get("tyre_age_at_start")), lane, green))


def results_table(rd: RaceData) -> list[tuple[int, int]]:
    """[(driver_number, position)] — classified first, then others by laps done."""
    res = rd.result[[not truthy(v) for v in rd.result.dns]].copy()
    cls = res[res.position.notna()].sort_values("position")
    rest = res[res.position.isna()].sort_values("number_of_laps", ascending=False)
    out, pos = [], 0
    for dn in list(cls.driver_number) + list(rest.driver_number):
        pos += 1
        out.append((int(dn), pos))
    return out


def cumulative_times(rd: RaceData) -> dict[int, dict[int, float]]:
    """cum[driver][lap] = seconds since race start at the end of that lap."""
    cum: dict[int, dict[int, float]] = {}
    for dn, grp in rd.laps.groupby("driver_number"):
        d, prev = {}, 0.0
        for r in grp.itertuples():
            if np.isfinite(r.t1):
                v = r.t1 - rd.race_start
            elif np.isfinite(r.dur):
                v = prev + r.dur
            else:
                break
            d[int(r.lap_number)] = v
            prev = v
        cum[int(dn)] = d
    return cum
