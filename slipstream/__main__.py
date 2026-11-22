"""Command line: python -m slipstream build|social|evaluate ..."""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="slipstream", description="SlipstreamLab data pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="fetch a race and write web/data/<slug>/*.json")
    b.add_argument("--year", type=int, required=True)
    g = b.add_mutually_exclusive_group(required=True)
    g.add_argument("--round", type=int, help="round number (non-cancelled races, in date order)")
    g.add_argument("--latest", action="store_true", help="most recent finished race")

    b.add_argument("--refresh", action="store_true",
                   help="ignore the local OpenF1 cache for this race and download it again (slow: minutes)")

    s = sub.add_parser("social", help="render 1080x1920 images for the top 3 moments")
    s.add_argument("--race", required=True, help="slug, e.g. 2026-21-las-vegas")
    s.add_argument("--video", action="store_true", help="also write an animated MP4 (or GIF without ffmpeg)")

    c = sub.add_parser("clean-cache", help="delete raw downloads (~150 MB/race) of races already built")
    c.add_argument("--all", dest="everything", action="store_true",
                   help="delete ALL cached downloads, also for races not built yet")
    c.add_argument("--dry-run", action="store_true", help="only show what would be deleted")

    e = sub.add_parser("evaluate", help="compare web/data findings with the checked real-world cases")
    e.add_argument("--strict", action="store_true", help="exit 1 if a wrong case is still wrong or a plausible case was lost")

    args = ap.parse_args(argv)
    if args.cmd == "build":
        from .build import build
        build(args.year, rnd=args.round, latest=args.latest, refresh=args.refresh)
    elif args.cmd == "evaluate":
        from .evaluate import run as evaluate_run
        return evaluate_run(strict=args.strict)
    elif args.cmd == "clean-cache":
        from .cleancache import run
        run(everything=args.everything, dry_run=args.dry_run)
    else:
        from .social import render_race
        render_race(args.race, video=args.video)
    return 0


if __name__ == "__main__":
    sys.exit(main())
