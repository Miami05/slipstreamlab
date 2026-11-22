"""Vertical (1080x1920) social images and short animations for the top moments.

Safe zones: TikTok/Reels cover roughly the bottom 20 % and right 15 % of the
screen, so everything (card included) stays inside x 64..918, y 80..1536.
Outside that box the picture is plain background. No logos, no photos.
"""
from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parent.parent
WEB_DATA = ROOT / "web" / "data"
OUT_DIR = ROOT / "out"

W, H, DPI = 1080, 1920, 100
SAFE = (64, 80, 918, 1536)       # x0, y0, x1, y1 in pixels
BG, CARD, TEXT, MUTED = "#0E1116", "#161B22", "#E6EAF0", "#9AA4B2"
GRID = "#2A313C"
FPS = 10
DRAW_S, HOLD_S = 4.0, 1.6


def _lighten(hex_color: str, amount: float) -> str:
    c = np.array([int(hex_color[i:i + 2], 16) for i in (1, 3, 5)], float)
    c = c + (255 - c) * amount
    return "#" + "".join(f"{int(round(v)):02X}" for v in c)


def _luma(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _visible(hex_color: str) -> str:
    return _lighten(hex_color, 0.5) if _luma(hex_color) < 0.25 else hex_color


def _fit_text(fig, ax, x, y, text, max_w, max_lines, size, min_size, **kw):
    """Draw wrapped text whose left edge is x (px) and top is y (px); shrink until it fits."""
    renderer = fig.canvas.get_renderer()
    for s in np.arange(size, min_size - 1, -2):
        per_line = max(int(max_w / (s * 0.58)), 8)
        lines = textwrap.wrap(text, per_line) or [""]
        if len(lines) > max_lines:
            continue
        t = ax.text(x, y, "\n".join(lines), fontsize=s * 72 / DPI, va="top", ha="left",
                    linespacing=1.18, **kw)
        bb = t.get_window_extent(renderer)
        if bb.width <= max_w:
            return t
        t.remove()
    return ax.text(x, y, text[: 40], fontsize=min_size * 72 / DPI, va="top", ha="left", **kw)


class Scene:
    """One moment as a Matplotlib figure. `update(p)` sets the animation progress 0..1."""

    def __init__(self, race: dict, moment: dict, extras: dict | None):
        self.moment = moment
        colors = {d["code"]: d["color"] for d in race["drivers"]}
        names = {d["code"]: d["last_name"] for d in race["drivers"]}
        extras = extras or {}
        title = extras.get("social_title") or f"{names[moment['driver']]}: {moment['label']}."
        sentence = extras.get("social_sentence") or moment["caption"].split(". ")[0].rstrip(".") + "."
        big = extras.get("big", "")

        fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI, facecolor=BG)
        self.fig = fig
        bg = fig.add_axes([0, 0, 1, 1])
        bg.set_xlim(0, W)
        bg.set_ylim(H, 0)
        bg.axis("off")
        self.bg = bg

        x0, y0, x1, y1 = SAFE
        tx = x0 + 8          # text sits a few px inside the safe box (anti-aliasing spill)
        # header
        bg.text(x1 - 8, y0 + 8, "SlipstreamLab", fontsize=34 * 72 / DPI, color=TEXT, ha="right", va="top",
                fontweight="bold")
        bg.text(tx, y0 + 14, f"{race['name']} · {moment['lap_label']}", fontsize=28 * 72 / DPI,
                color=MUTED, ha="left", va="top")
        # headline (answers the question)
        _fit_text(fig, bg, tx, 190, title, x1 - x0 - 16, 4, 64, 40, color=TEXT, fontweight="bold")

        # card
        card_top, card_bot = 470, 1070
        bg.add_patch(FancyBboxPatch((x0, card_top), x1 - x0, card_bot - card_top,
                                    boxstyle="round,pad=0,rounding_size=28", fc=CARD, ec="none"))
        chart = moment["chart"]
        bg.text(x0 + 36, card_top + 28, chart["title"], fontsize=27 * 72 / DPI, color=MUTED,
                ha="left", va="top")
        # legend (dots + last names)
        lx = x0 + 36
        for s in chart["series"]:
            col = self._series_color(s["driver"], chart, colors)
            bg.plot([lx], [card_top + 100], "o", color=col, ms=16 * 72 / DPI, clip_on=False)
            t = bg.text(lx + 22, card_top + 100, names[s["driver"]], fontsize=30 * 72 / DPI, color=TEXT,
                        ha="left", va="center")
            bb = t.get_window_extent(fig.canvas.get_renderer())
            lx += 22 + bb.width + 50

        ax = fig.add_axes([(x0 + 100) / W, 1 - (card_bot - 50) / H, (x1 - x0 - 140) / W,
                           (card_bot - 50 - (card_top + 160)) / H], facecolor=CARD)
        self.ax = ax
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(GRID)
        ax.tick_params(colors=MUTED, labelsize=22 * 72 / DPI, length=0)
        ax.grid(True, color=GRID, lw=1.0)
        ax.set_axisbelow(True)
        ax.set_xlabel(chart["x_label"], color=MUTED, fontsize=24 * 72 / DPI, labelpad=10)
        ax.set_ylabel(chart["y_label"].split("·")[0].strip(), color=MUTED, fontsize=24 * 72 / DPI, labelpad=10)
        allx = [p[0] for s in chart["series"] for p in s["points"]]
        ally = [p[1] for s in chart["series"] for p in s["points"]]
        pad = (max(ally) - min(ally)) * 0.08 or 1
        ax.set_xlim(min(allx), max(allx))
        ax.set_ylim(min(ally) - pad, max(ally) + pad * 2.5)
        if chart["type"] == "speed":
            ax.set_xticks([])
        self.zone = None
        if chart.get("zone"):
            a, b = chart["zone"]
            self.zone = ax.axvspan(a, b, facecolor="#9AA4B2", alpha=0.0, edgecolor=MUTED, linestyle="--",
                                   linewidth=2)
            self.zone_label = ax.text((a + b) / 2, ax.get_ylim()[1] - pad * 0.4, chart["zone_label"],
                                      color=MUTED, fontsize=24 * 72 / DPI, ha="center", va="top", alpha=0.0)
            self.zone_ab = (a, b)
        self.full = []
        self.lines = []
        for s in chart["series"]:
            pts = np.array(s["points"], float)
            col = self._series_color(s["driver"], chart, colors)
            dashed = s is not chart["series"][0] and colors.get(s["driver"]) == colors.get(chart["series"][0]["driver"])
            (ln,) = ax.plot([], [], color=col, lw=5.0, solid_capstyle="round",
                            linestyle="-" if not dashed else (0, (4, 2)))
            self.lines.append(ln)
            self.full.append(pts)

        # big number + plain sentence + footer
        lead = _visible(colors[moment["driver"]])
        self.big = bg.text(tx, 1100, big, fontsize=130 * 72 / DPI, color=lead, fontweight="bold",
                           ha="left", va="top")
        self.big_size = 130
        _fit_text(fig, bg, tx, 1290, sentence, x1 - x0 - 16, 3, 46, 32, color=TEXT)
        bg.text(tx, y1 - 6, "Full story: link in bio", fontsize=32 * 72 / DPI, color=MUTED, ha="left", va="bottom")
        self.update(1.0)

    @staticmethod
    def _series_color(code, chart, colors):
        first = chart["series"][0]["driver"]
        col = _visible(colors[code])
        if code != first and colors[code] == colors[first]:
            col = _lighten(colors[code], 0.65)
        return col

    def update(self, p: float, pop: float = 1.0) -> None:
        """p: 0..1 line drawing progress (lines are revealed left to right)."""
        for ln, pts in zip(self.lines, self.full):
            x0 = pts[0, 0]
            xr = x0 + (pts[-1, 0] - x0) * p
            m = pts[:, 0] <= xr
            if p >= 1.0:
                ln.set_data(pts[:, 0], pts[:, 1])
            elif m.sum() >= 1:
                xs, ys = pts[m, 0], pts[m, 1]
                ln.set_data(xs, ys)
        if self.zone is not None:
            alpha = float(np.clip((p - 0.7) / 0.3, 0, 1))
            self.zone.set_alpha(0.18 * alpha)
            self.zone.set_edgecolor(MUTED)
            self.zone_label.set_alpha(alpha)
        self.big.set_fontsize(self.big_size * pop * 72 / DPI)
        self.big.set_alpha(min(1.0, 0.0 + p * 4) if pop != 1.0 else 1.0)

    def save_png(self, path: Path) -> None:
        self.update(1.0)
        self.fig.savefig(path, dpi=DPI, facecolor=BG)

    def close(self):
        plt.close(self.fig)


def _load(slug: str):
    folder = WEB_DATA / slug
    race = json.loads((folder / "race.json").read_text())
    extras_path = OUT_DIR / slug / "social.json"
    extras = json.loads(extras_path.read_text()) if extras_path.exists() else {}
    return race, extras


def render_png(race, moment, extras, path: Path) -> None:
    sc = Scene(race, moment, extras.get(moment["id"]))
    sc.save_png(path)
    sc.close()


def find_ffmpeg() -> str | None:
    """ffmpeg for MP4 export: a system install, else the copy bundled with imageio-ffmpeg."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # not installed or no binary for this platform -> GIF fallback
        return None


def render_video(race, moment, extras, path_base: Path) -> Path:
    """Lines draw left to right (~4 s), the zone fades in, the big number pops."""
    from matplotlib import animation
    sc = Scene(race, moment, extras.get(moment["id"]))
    n_draw, n_hold = int(DRAW_S * FPS), int(HOLD_S * FPS)
    total = n_draw + n_hold

    def frame(i):
        p = min(i / max(n_draw - 1, 1), 1.0)
        pop = 1.0
        if i >= n_draw - 3:               # little "pop" as the lines finish
            k = (i - (n_draw - 3)) / 8
            pop = 1.0 + 0.16 * np.sin(np.clip(k, 0, 1) * np.pi)
        sc.update(p, pop if i < total else 1.0)
        return []

    ffmpeg = find_ffmpeg()
    anim = animation.FuncAnimation(sc.fig, frame, frames=total, blit=False)
    if ffmpeg:
        import matplotlib
        matplotlib.rcParams["animation.ffmpeg_path"] = ffmpeg
        out = path_base.with_suffix(".mp4")
        anim.save(out, writer=animation.FFMpegWriter(fps=FPS, codec="libx264",
                                                     extra_args=["-pix_fmt", "yuv420p"]), dpi=DPI)
    else:
        out = path_base.with_suffix(".gif")
        anim.save(out, writer=animation.PillowWriter(fps=FPS), dpi=60)   # smaller GIF: 648x1152
    sc.close()
    return out


def render_race(slug: str, video: bool = False, top: int = 3, log=print) -> list[Path]:
    race, extras = _load(slug)
    out_dir = OUT_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for m in race["moments"][:top]:
        p = out_dir / f"moment-{m['id']}.png"
        render_png(race, m, extras, p)
        paths.append(p)
        log(f"  wrote {p}")
        if video:
            v = render_video(race, m, extras, out_dir / f"moment-{m['id']}")
            paths.append(v)
            log(f"  wrote {v}")
    if not race["moments"]:
        log("  no moments for this race: nothing to render")
    return paths
