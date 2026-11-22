"""Delete raw OpenF1 downloads that are no longer needed.

The cache (.cache/openf1/) holds raw telemetry: ~150 MB per race. Once a race
is built, the website only needs the small files in web/data/, so the raw
downloads for that race can be deleted. Rebuilding later just downloads again.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import openf1

_SESSION_KEY = re.compile(rb'"session_key"\s*:\s*(\d+)')
# Season-wide lists (calendar, meetings) are tiny and shared by every race: always keep them.
_SEASON_MARKERS = (b'"session_type"', b'"meeting_name"', b'"session_name"')


def file_session_key(path: Path) -> int | None:
    """The race (session_key) a cached file belongs to, or None for shared season-wide files."""
    with open(path, "rb") as fh:
        head = fh.read(600)
    if any(m in head for m in _SEASON_MARKERS):
        return None
    m = _SESSION_KEY.search(head)
    return int(m.group(1)) if m else None


def built_session_keys(index_path: Path) -> set[int]:
    try:
        races = json.loads(index_path.read_text()).get("races", [])
    except (OSError, ValueError):
        return set()
    return {int(r["session_key"]) for r in races if "session_key" in r}


def clean(*, everything: bool = False, dry_run: bool = False,
          cache_dir: Path | None = None, index_path: Path | None = None) -> tuple[int, int]:
    """Delete cached files of built races (or all files with everything=True).

    Returns (files_removed, bytes_freed). With dry_run nothing is deleted.
    """
    cache_dir = cache_dir or openf1.CACHE_DIR
    index_path = index_path or Path(__file__).resolve().parent.parent / "web" / "data" / "index.json"
    if not cache_dir.exists():
        return 0, 0
    built = built_session_keys(index_path)
    n = freed = 0
    for path in cache_dir.glob("*.json"):
        if not everything:
            key = file_session_key(path)
            if key is None or key not in built:
                continue
        size = path.stat().st_size
        if not dry_run:
            path.unlink()
        n += 1
        freed += size
    return n, freed


def run(everything: bool = False, dry_run: bool = False) -> None:
    n, freed = clean(everything=everything, dry_run=dry_run)
    verb = "Would delete" if dry_run else "Deleted"
    what = "all cached downloads" if everything else "cached downloads of races already built"
    print(f"{verb} {n} files ({freed / 1e6:.0f} MB): {what}.")
    if not everything and not dry_run:
        print("Races that are not built yet keep their downloads. Rebuilding a race downloads it again.")
