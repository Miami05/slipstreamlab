"""Tiny OpenF1 client with an on-disk cache and polite rate limiting.

Free tier limits are roughly 3 requests/second and 30 requests/minute, so we
wait at least MIN_INTERVAL seconds between *uncached* requests. Cached answers
are free, so re-running the pipeline never hits the network again.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api.openf1.org/v1/"
MIN_INTERVAL = 2.1
TIMEOUT = 60
MAX_RETRIES = 6
USER_AGENT = "SlipstreamLab/0.1 (open-source fan project)"

CACHE_DIR = Path(os.environ.get("SLIPSTREAM_CACHE") or Path(__file__).resolve().parent.parent / ".cache" / "openf1")

_last_request = 0.0

# Cache freshness -----------------------------------------------------------
# * REFRESH: when True every request bypasses the disk cache once per URL
#   (set by `build --refresh`; see refreshing()).
# * Results of a race that ended recently are provisional (penalties, late
#   corrections), so those files only live RECENT_TTL_S in the cache.
RECENT_WINDOW_S = 48 * 3600
RECENT_TTL_S = 3600
REFRESH = False
_refetched: set = set()


class refreshing:
    """Context manager: `with openf1.refreshing(True): ...` bypasses the cache (each URL is fetched once)."""

    def __init__(self, on: bool = True):
        self.on = bool(on)

    def __enter__(self):
        global REFRESH
        self._old = REFRESH
        REFRESH = self.on
        _refetched.clear()
        return self

    def __exit__(self, *exc):
        global REFRESH
        REFRESH = self._old
        _refetched.clear()
        return False


def recent_ttl(session: dict, now_epoch: float | None = None) -> float | None:
    """Cache lifetime (seconds) for provisional endpoints of this session, or None = cache forever.

    A session that ended less than 48 h ago (or has not ended yet) is "recent".
    """
    import datetime as _dt
    now = now_epoch if now_epoch is not None else time.time()
    raw = session.get("date_end") or session.get("date_start")
    if not raw:
        return None
    try:
        end = _dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None
    return RECENT_TTL_S if now - end.timestamp() < RECENT_WINDOW_S else None


def build_url(endpoint: str, params: dict | None = None) -> str:
    """Build a URL. Keys may end in '>' or '<' to mean >= / < filters
    (e.g. {"date>": iso} -> date>=iso)."""
    parts = []
    for key, value in (params or {}).items():
        if key.endswith(">"):
            parts.append(f"{key}={_q(value)}")  # key already 'date>' -> 'date>='
        elif key.endswith("<"):
            parts.append(f"{key}{_q(value)}")
        else:
            parts.append(f"{key}={_q(value)}")
    q = "&".join(parts)
    return BASE + endpoint + ("?" + q if q else "")


def _q(value) -> str:
    # '+' must be escaped (a raw '+' in a query string decodes to a space).
    # Callers should still prefer naive-UTC timestamps (see iso_utc).
    return urllib.parse.quote(str(value), safe=":-.")


def iso_utc(epoch_seconds: float) -> str:
    """Epoch seconds -> naive UTC ISO string that OpenF1 date filters accept."""
    import datetime as _dt
    d = _dt.datetime.fromtimestamp(epoch_seconds, _dt.timezone.utc).replace(tzinfo=None)
    return d.isoformat(timespec="milliseconds")


def cache_key(url: str) -> str:
    """Stable cache file name for a URL."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def cache_path(url: str, cache_dir: Path | None = None) -> Path:
    return (cache_dir or CACHE_DIR) / (cache_key(url) + ".json")


def get(endpoint: str, params: dict | None = None, *, cache_dir: Path | None = None,
        verbose: bool = False, max_age: float | None = None, refresh: bool | None = None):
    """GET an endpoint, return parsed JSON (a list of dicts). Cached on disk.

    max_age: cached files older than this many seconds are fetched again (if that
    fails, the stale file is used rather than failing the build).
    refresh: True = ignore the cache (once per URL per refreshing() block).
    """
    global _last_request
    url = build_url(endpoint, params)
    path = cache_path(url, cache_dir)
    force = (REFRESH if refresh is None else refresh) and url not in _refetched
    stale = None
    if path.exists() and not force:
        try:
            cached = json.loads(path.read_text())
        except json.JSONDecodeError:
            path.unlink(missing_ok=True)
        else:
            if max_age is None or time.time() - path.stat().st_mtime <= max_age:
                return cached
            stale = cached           # expired: refetch, but keep this as a fallback
    elif path.exists():
        try:
            stale = json.loads(path.read_text())
        except json.JSONDecodeError:
            stale = None
    if force:
        _refetched.add(url)

    delay = 4.0
    for attempt in range(MAX_RETRIES):
        wait = MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        if verbose:
            print(f"  GET {url}", flush=True)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:  # OpenF1 answers 404 for "no data"
                data = []
                break
            if e.code == 429 or e.code >= 500:
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
    else:
        if stale is not None:
            print(f"  NOTE: could not refresh {endpoint}; using the older cached copy", flush=True)
            return stale
        raise RuntimeError(f"OpenF1 request failed after retries: {url}")

    if not data:  # never cache empty answers: a bad filter must not stick forever
        return stale if stale is not None else data
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)
    return data
