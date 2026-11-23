// SlipstreamLab shared helpers.
// Pure helpers (no DOM access at import time) are unit-tested in tests/web/.

export const CARD_BG = '#161B22';
export const FALLBACK_COLOR = '#9AA4B2';
export const GITHUB_URL = 'https://github.com/Miami05/slipstreamlab';
const STORAGE_KEY = 'slipstreamlab:last-race';

/* ------------------------------------------------------------------ */
/* Colours                                                             */
/* ------------------------------------------------------------------ */

export function hexToRgb(hex) {
  if (typeof hex !== 'string') return null;
  let h = hex.trim().replace(/^#/, '');
  if (/^[0-9a-fA-F]{3}$/.test(h)) h = h.split('').map((c) => c + c).join('');
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return null;
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

export function rgbToHex(rgb) {
  return '#' + rgb.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('').toUpperCase();
}

function channel(v) {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

export function luminance(rgb) {
  return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
}

export function contrastRatio(hexA, hexB) {
  const a = hexToRgb(hexA);
  const b = hexToRgb(hexB);
  if (!a || !b) return 1;
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

export function mixWithWhite(hex, amount) {
  const rgb = hexToRgb(hex);
  if (!rgb) return hex;
  return rgbToHex(rgb.map((v) => v + (255 - v) * amount));
}

/** Lighten `hex` (towards white) until it has at least `min`:1 contrast on `bg`. */
export function lightenForText(hex, bg = CARD_BG, min = 4.5) {
  const rgb = hexToRgb(hex);
  if (!rgb) return '#E6EAF0';
  const base = rgbToHex(rgb);
  if (contrastRatio(base, bg) >= min) return base;
  for (let t = 0.02; t <= 1.0001; t += 0.02) {
    const c = mixWithWhite(base, t);
    if (contrastRatio(c, bg) >= min) return c;
  }
  return '#FFFFFF';
}

export function colorsClose(a, b, threshold = 60) {
  const x = hexToRgb(a);
  const y = hexToRgb(b);
  if (!x || !y) return false;
  return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]) < threshold;
}

/* ------------------------------------------------------------------ */
/* Drivers / text formatting                                           */
/* ------------------------------------------------------------------ */

export function driverMap(drivers) {
  const map = new Map();
  if (Array.isArray(drivers)) {
    for (const d of drivers) if (d && d.code) map.set(d.code, d);
  }
  return map;
}

export function lastName(map, code, fallback = 'Another driver') {
  const d = map && map.get ? map.get(code) : null;
  return d && d.last_name ? String(d.last_name) : fallback;
}

export function colorFor(map, code, fallback = FALLBACK_COLOR) {
  const d = map && map.get ? map.get(code) : null;
  return d && hexToRgb(d.color) ? rgbToHex(hexToRgb(d.color)) : fallback;
}

export function ordinal(n) {
  const v = Math.abs(Math.round(Number(n)));
  if (!Number.isFinite(v)) return '';
  const mod100 = v % 100;
  if (mod100 >= 11 && mod100 <= 13) return v + 'th';
  switch (v % 10) {
    case 1: return v + 'st';
    case 2: return v + 'nd';
    case 3: return v + 'rd';
    default: return v + 'th';
  }
}

export const KIND_INFO = {
  battery_low: { label: 'Lost power (maybe battery)', bg: '#2E2547', fg: '#C9B6FF' },
  energy_saving: { label: 'Lifted off early (probably saving energy or cooling the car)', bg: '#13283F', fg: '#9CC8FF' },
  tyre_wear: { label: 'Tyres probably wore out', bg: '#2F2A12', fg: '#EBD57A' },
  power_cut: { label: 'Had less power for most of the race (probably a car problem)', bg: '#3A1A22', fg: '#FFB3C0' },
};

export function kindInfo(kind, fallbackLabel) {
  const k = KIND_INFO[kind];
  if (k) return k;
  return { label: fallbackLabel || 'Something happened', bg: '#262C36', fg: '#E6EAF0' };
}

export function confidenceLevel(c) {
  return c === 'high' ? 3 : c === 'medium' ? 2 : 1;
}

export function confidenceWord(c) {
  return c === 'high' ? 'High' : c === 'medium' ? 'Medium' : 'Low';
}

/** "1.4 seconds faster" / "2.1 seconds slower" (sign of v: + = faster). */
export function secondsPhrase(v) {
  const n = Math.abs(Number(v) || 0);
  const num = n.toFixed(1);
  return num + (num === '1.0' ? ' second ' : ' seconds ') + (Number(v) >= 0 ? 'faster' : 'slower');
}

export function rangeText(low, high) {
  let lo = Number(low);
  let hi = Number(high);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return 'This is a guess, so treat it with care.';
  if (lo > hi) [lo, hi] = [hi, lo];
  return 'This is a guess: it could be anywhere from ' + secondsPhrase(lo) + ' to ' + secondsPhrase(hi) + '.';
}

/** Only says what the pipeline computed: is a car within 1.5 s ahead when he comes out? */
export function trafficText(traffic) {
  return traffic
    ? 'He’d come out right behind another car, which could cost him time.'
    : 'No car within 1.5 seconds in front of him when he comes out.';
}

export function finishKind(real, est) {
  if (!Number.isFinite(real) || !Number.isFinite(est)) return 'unknown';
  return est < real ? 'better' : est > real ? 'worse' : 'same';
}

export function finishText(real, est) {
  const kind = finishKind(real, est);
  if (kind === 'unknown') return 'We can’t estimate a finish for this one.';
  if (kind === 'same') return 'He would probably still finish ' + ordinal(real);
  return 'He would probably finish ' + ordinal(est) + ' instead of ' + ordinal(real);
}

export function pitLabel(lap, realLap) {
  const diff = realLap - lap;
  if (diff === 0) return 'Stop on lap ' + lap + ' — the same as in the real race';
  const n = Math.abs(diff);
  return 'Stop ' + n + ' lap' + (n === 1 ? '' : 's') + (diff > 0 ? ' earlier' : ' later') + ' (lap ' + lap + ')';
}

export function gapLabel(gap) {
  const g = Number(gap);
  if (!Number.isFinite(g)) return '';
  if (Math.abs(g) < 0.05) return 'alongside';
  return Math.abs(g).toFixed(1) + 's ' + (g < 0 ? 'ahead' : 'behind');
}

/** 95.123 -> "1:35.123" */
export function formatLapTime(s) {
  const v = Number(s);
  if (!Number.isFinite(v) || v <= 0) return '';
  const m = Math.floor(v / 60);
  const rest = v - m * 60;
  const sec = rest.toFixed(3).padStart(6, '0');
  return m + ':' + sec;
}

export function formatDate(iso) {
  if (typeof iso !== 'string') return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const mo = months[Number(m[2]) - 1];
  return mo ? Number(m[3]) + ' ' + mo + ' ' + m[1] : iso;
}

export function safeFileName(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'chart';
}

/* ------------------------------------------------------------------ */
/* Geometry: paths, interpolation, track fitting                       */
/* ------------------------------------------------------------------ */

export function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}

export function isNum(v) {
  return typeof v === 'number' && Number.isFinite(v);
}

/** path rows are [t, x, y]. Returns {x, y} at time t (clamped to the ends). */
export function interpolatePath(path, t) {
  if (!Array.isArray(path) || path.length === 0) return { x: 0, y: 0 };
  if (path.length === 1 || t <= path[0][0]) return { x: path[0][1], y: path[0][2] };
  const last = path[path.length - 1];
  if (t >= last[0]) return { x: last[1], y: last[2] };
  let lo = 0;
  let hi = path.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (path[mid][0] <= t) lo = mid; else hi = mid;
  }
  const a = path[lo];
  const b = path[hi];
  const span = b[0] - a[0];
  const f = span > 0 ? (t - a[0]) / span : 0;
  return { x: a[1] + (b[1] - a[1]) * f, y: a[2] + (b[2] - a[2]) * f };
}

/** Cumulative distance travelled along a [t,x,y] path (same length as path). */
export function cumulativeDistances(path) {
  const cum = [0];
  for (let i = 1; i < path.length; i++) {
    cum.push(cum[i - 1] + Math.hypot(path[i][1] - path[i - 1][1], path[i][2] - path[i - 1][2]));
  }
  return cum;
}

export function distanceAt(path, cum, t) {
  if (!path.length) return 0;
  if (t <= path[0][0]) return 0;
  const n = path.length;
  if (t >= path[n - 1][0]) return cum[n - 1];
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (path[mid][0] <= t) lo = mid; else hi = mid;
  }
  const span = path[hi][0] - path[lo][0];
  const f = span > 0 ? (t - path[lo][0]) / span : 0;
  return cum[lo] + (cum[hi] - cum[lo]) * f;
}

/** Time at which the path had covered distance d (clamped to the path's ends). */
export function timeAtDistance(path, cum, d) {
  const n = path.length;
  if (!n) return 0;
  if (d <= 0) return path[0][0];
  if (d >= cum[n - 1]) return path[n - 1][0];
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (cum[mid] <= d) lo = mid; else hi = mid;
  }
  const span = cum[hi] - cum[lo];
  const f = span > 0 ? (d - cum[lo]) / span : 0;
  return path[lo][0] + (path[hi][0] - path[lo][0]) * f;
}

/** Progress (0..1) along the path's own length at time t. */
export function progressAt(path, cum, t) {
  const total = cum.length ? cum[cum.length - 1] : 0;
  return total > 0 ? distanceAt(path, cum, t) / total : 0;
}

/** Time at which the path had covered the fraction f (0..1) of its own length. */
export function timeAtProgress(path, cum, f) {
  const total = cum.length ? cum[cum.length - 1] : 0;
  return timeAtDistance(path, cum, f * total);
}

/** Lap time of a prepared car: the official lap time when known, else the end of its path. */
function lapEnd(c) {
  return isNum(c.lap) && c.lap > 0 ? c.lap : c.path[c.path.length - 1][0];
}

/**
 * Each car's clock runs from its own first sample (= the line, t = 0) to its lap time.
 * For paths cut by the pipeline this changes nothing; for older paths that start a few
 * tenths after the line or run past the flag it removes the sampling offset.
 */
function clockOf(c) {
  const t0 = c.path[0][0];
  const span = c.path[c.path.length - 1][0] - t0;
  const lap = lapEnd(c);
  return { t0, k: span > 0 && lap > 0 ? span / lap : 1 };
}
const progressOf = (c, t) => progressAt(c.path, c.cum, clockOf(c).t0 + Math.max(0, t) * clockOf(c).k);
const timeOf = (c, f) => (timeAtProgress(c.path, c.cum, f) - clockOf(c).t0) / clockOf(c).k;

/**
 * Who is ahead at time t, and by how many seconds (a "ghost lap" comparison: both
 * laps start together at t = 0 and are cut at their lap time).
 * Progress is the share of each path's own length (0 = line, 1 = line again), so it
 * does not matter where each path's first sample happened to fall. The gap is "how long
 * after the leader did the other driver reach the point where the leader is right now".
 * Once both laps are over the readout is exactly the lap-time difference (final: true).
 * leader is 'a', 'b' or null (side by side).
 */
export function gapSeconds(a, b, t) {
  if (!a.path.length || !b.path.length) return { leader: null, gap: 0, final: false };
  const endA = lapEnd(a);
  const endB = lapEnd(b);
  if (t >= Math.max(endA, endB)) {
    const diff = endB - endA;
    return { leader: Math.abs(diff) < 1e-9 ? null : diff > 0 ? 'a' : 'b', gap: Math.abs(diff), final: true };
  }
  const f = Math.max(progressOf(a, t), progressOf(b, t));
  const g = timeOf(b, f) - timeOf(a, f); // > 0: a is ahead
  if (Math.abs(g) < 1e-9) return { leader: null, gap: 0, final: false };
  return { leader: g > 0 ? 'a' : 'b', gap: Math.abs(g), final: false };
}

export const LEAD_ENTER_S = 0.1;  // a car must be this far ahead to be called the leader
export const LEAD_EXIT_S = 0.05;  // ... and stays the leader until the gap shrinks below this

/**
 * Readout with hysteresis, so the leader does not flip on noise. The readings are
 * computed once, in order, on a fixed time grid, so the answer depends only on the
 * playhead position (never on how the visitor scrubbed). The end is exact.
 */
export function makeGapReader(a, b, step = 0.1) {
  const end = Math.max(a.path.length ? lapEnd(a) : 0, b.path.length ? lapEnd(b) : 0);
  const steps = Math.max(1, Math.ceil(end / step));
  const table = [];
  let lead = null;
  for (let i = 0; i < steps; i++) {
    const r = gapSeconds(a, b, i * step);
    const sg = r.leader === 'a' ? r.gap : r.leader === 'b' ? -r.gap : 0;
    if (lead === null) {
      if (Math.abs(sg) >= LEAD_ENTER_S) lead = sg > 0 ? 'a' : 'b';
    } else if (lead === 'a') {
      if (sg <= -LEAD_ENTER_S) lead = 'b';
      else if (sg < LEAD_EXIT_S) lead = null;
    } else if (sg >= LEAD_ENTER_S) lead = 'a';
    else if (sg > -LEAD_EXIT_S) lead = null;
    table.push({ leader: lead, gap: Math.abs(sg), final: false });
  }
  const finalResult = gapSeconds(a, b, end + 1);
  return (t) => {
    if (t >= end) return finalResult;
    return table[Math.min(steps - 1, Math.max(0, Math.floor(t / step)))];
  };
}

/** Sentence for the big "who is ahead" line. */
export function aheadText(nameA, nameB, result) {
  const tiny = result && result.final ? 0.005 : 0.05;
  if (!result || !result.leader || result.gap < tiny) return nameA + ' and ' + nameB + ' are side by side';
  const name = result.leader === 'a' ? nameA : nameB;
  return name + ' is ' + result.gap.toFixed(result.final ? 2 : 1) + ' seconds ahead';
}

/** Fit points (any scale) into a w x h box with padding, keeping aspect ratio. */
export function fitPoints(points, w, h, pad) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const p of points) {
    if (!p || !isNum(p[0]) || !isNum(p[1])) continue;
    minX = Math.min(minX, p[0]); maxX = Math.max(maxX, p[0]);
    minY = Math.min(minY, p[1]); maxY = Math.max(maxY, p[1]);
  }
  if (!Number.isFinite(minX)) { minX = 0; minY = 0; maxX = 1; maxY = 1; }
  const dx = maxX - minX || 1;
  const dy = maxY - minY || 1;
  const scale = Math.min((w - 2 * pad) / dx, (h - 2 * pad) / dy);
  const offX = (w - dx * scale) / 2;
  const offY = (h - dy * scale) / 2;
  return {
    scale,
    x: (v) => offX + (v - minX) * scale,
    y: (v) => offY + (v - minY) * scale,
    center: [offX + (dx * scale) / 2, offY + (dy * scale) / 2],
  };
}

/** Closed outline helper: position at a distance along a polyline of pixel points. */
export function makeOutline(pts, closed = true) {
  const seg = [];
  let total = 0;
  const n = pts.length;
  const count = closed ? n : n - 1;
  for (let i = 0; i < count; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % n];
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    seg.push(len);
    total += len;
  }
  function at(distance) {
    if (!total) return pts[0] ? { x: pts[0][0], y: pts[0][1] } : { x: 0, y: 0 };
    let d = closed ? ((distance % total) + total) % total : clamp(distance, 0, total);
    for (let i = 0; i < seg.length; i++) {
      if (d <= seg[i] || i === seg.length - 1) {
        const a = pts[i];
        const b = pts[(i + 1) % n];
        const f = seg[i] > 0 ? Math.min(1, d / seg[i]) : 0;
        return { x: a[0] + (b[0] - a[0]) * f, y: a[1] + (b[1] - a[1]) * f };
      }
      d -= seg[i];
    }
    return { x: pts[0][0], y: pts[0][1] };
  }
  return { total, at };
}

/** A simple oval used when the data has no circuit outline (coordinates 0..1). */
export function ovalTrack(count = 120) {
  const pts = [];
  for (let i = 0; i < count; i++) {
    const a = (i / count) * Math.PI * 2;
    pts.push([0.5 + 0.5 * Math.cos(a), 0.3 + 0.3 * Math.sin(a)]);
  }
  return pts;
}

export function validTrack(track) {
  return Array.isArray(track) && track.length >= 3 && track.every((p) => Array.isArray(p) && isNum(p[0]) && isNum(p[1]));
}

export function validPath(path) {
  return Array.isArray(path) && path.length >= 2 && path.every((r) => Array.isArray(r) && isNum(r[0]) && isNum(r[1]) && isNum(r[2]));
}

/* ------------------------------------------------------------------ */
/* URLs and race selection                                             */
/* ------------------------------------------------------------------ */

export function dataMode(search) {
  return new URLSearchParams(search || '').get('data') === 'sample' ? 'sample' : '';
}

export function dataRoot(search) {
  return dataMode(search) ? 'sample-data/' : 'data/';
}

/** Build a relative URL like "whatif.html?race=abc&data=sample". */
export function pageUrl(page, { race, data } = {}) {
  const p = new URLSearchParams();
  if (race) p.set('race', race);
  if (data) p.set('data', data);
  const q = p.toString();
  return page + (q ? '?' + q : '');
}

/**
 * Which race to show: ?race= (if it exists) > the race the visitor last picked
 * (unless a newer race has appeared since) > the newest race.
 */
export function chooseRace(races, requested, stored) {
  if (!Array.isArray(races) || races.length === 0) return null;
  const slugs = races.map((r) => r && r.slug).filter(Boolean);
  if (!slugs.length) return null;
  if (requested && slugs.includes(requested)) return requested;
  const newest = slugs[0];
  if (stored && stored.slug && slugs.includes(stored.slug) && stored.newest === newest) return stored.slug;
  return newest;
}

function readStored() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function writeStored(slug, newest) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ slug, newest }));
  } catch (e) {
    /* storage can be blocked; the site works without it */
  }
}

export class EmptyError extends Error {}

export async function fetchJSON(url) {
  const res = await fetch(url, { cache: 'no-cache' });
  if (!res.ok) {
    const err = new Error('HTTP ' + res.status + ' for ' + url);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

/** fetchJSON, but a 404 means "this race has no such file" (shown as an empty state). */
export async function fetchOptional(url, emptyMessage) {
  try {
    return await fetchJSON(url);
  } catch (e) {
    if (e && e.status === 404) throw new EmptyError(emptyMessage);
    throw e;
  }
}

/* ------------------------------------------------------------------ */
/* DOM helpers (only touched when called in a browser)                 */
/* ------------------------------------------------------------------ */

const SVG_NS = 'http://www.w3.org/2000/svg';

function build(node, attrs, kids) {
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') node.setAttribute('class', v);
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : String(v));
    }
  }
  const add = (child) => {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) child.forEach(add);
    else if (typeof child === 'object' && child.nodeType) node.appendChild(child);
    else node.appendChild(document.createTextNode(String(child)));
  };
  kids.forEach(add);
  return node;
}

/** el('div', {class: 'x', text: 'hi'}, child, 'text') — never uses innerHTML. */
export function el(tag, attrs, ...kids) {
  return build(document.createElement(tag), attrs, kids);
}

export function svgEl(tag, attrs, ...kids) {
  return build(document.createElementNS(SVG_NS, tag), attrs, kids);
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/* ------------------------------------------------------------------ */
/* Page bootstrap: race picker, loading / error / empty states         */
/* ------------------------------------------------------------------ */

/**
 * bootPage({ page, load, render })
 *   load(ctx)  -> Promise<data>   fetches what the page needs (throw EmptyError for "nothing here")
 *   render(data, ctx) -> optional cleanup function; builds into ctx.content
 * ctx = { slug, race (index entry), root, data (mode), content }
 */
export async function bootPage({ page, load, render }) {
  const search = window.location.search;
  const params = new URLSearchParams(search);
  const mode = dataMode(search);
  const root = dataRoot(search);
  const statusEl = document.getElementById('status');
  const contentEl = document.getElementById('content');
  const pickerEl = document.getElementById('picker');
  const metaEl = document.getElementById('race-meta');
  const bannerEl = document.getElementById('demo-banner');
  let seq = 0;
  let cleanup = null;
  let races = [];
  let currentSlug = null;

  if (mode && bannerEl) {
    bannerEl.hidden = false;
    bannerEl.textContent = 'Demo data: everything on this page is made up so the site can be tried out.';
  }

  function showStatus(kind, title, detail, retry) {
    clear(statusEl);
    statusEl.hidden = false;
    contentEl.hidden = true;
    const box = el('div', { class: 'state state-' + kind },
      kind === 'loading' ? el('span', { class: 'spinner', 'aria-hidden': 'true' }) : null,
      el('div', { class: 'state-title', text: title }),
      detail ? el('div', { class: 'state-detail', text: detail }) : null,
      retry ? el('button', { type: 'button', class: 'btn', onclick: retry, text: 'Try again' }) : null);
    statusEl.appendChild(box);
  }

  function syncLinks(slug) {
    const opts = { race: slug, data: mode };
    document.querySelectorAll('[data-nav]').forEach((a) => {
      a.setAttribute('href', pageUrl(a.getAttribute('data-nav') + '.html', opts));
    });
    const brand = document.querySelector('[data-brand]');
    if (brand) brand.setAttribute('href', pageUrl('index.html', opts));
  }

  function mountPicker() {
    clear(pickerEl);
    if (races.length === 0) return;
    const select = el('select', { id: 'race-select', class: 'select' },
      races.map((r) => {
        const text = (r.name || r.slug) + (r.year ? ' ' + r.year : '');
        return el('option', { value: r.slug, text, selected: r.slug === currentSlug });
      }));
    select.addEventListener('change', () => {
      go(select.value, true);
    });
    pickerEl.appendChild(el('label', { class: 'field', for: 'race-select' }, el('span', { class: 'field-label', text: 'Pick a race' }), select));
  }

  async function go(slug, push) {
    currentSlug = slug;
    const my = ++seq;
    const url = new URL(window.location.href);
    url.searchParams.set('race', slug);
    if (push) window.history.pushState({}, '', url);
    else window.history.replaceState({}, '', url);
    syncLinks(slug);
    const entry = races.find((r) => r.slug === slug) || { slug };
    writeStored(slug, races[0] && races[0].slug);
    const select = document.getElementById('race-select');
    if (select && select.value !== slug) select.value = slug;
    if (metaEl) {
      const bits = [entry.location, formatDate(entry.date)].filter(Boolean);
      metaEl.textContent = bits.join(' · ');
    }
    if (cleanup) { try { cleanup(); } catch (e) { /* ignore */ } cleanup = null; }
    showStatus('loading', 'Loading the race…');
    const ctx = { slug, race: entry, root, data: mode, content: contentEl };
    try {
      const data = await load(ctx);
      if (my !== seq) return;
      clear(contentEl);
      statusEl.hidden = true;
      contentEl.hidden = false;
      cleanup = render(data, ctx) || null;
    } catch (err) {
      if (my !== seq) return;
      if (err instanceof EmptyError) {
        showStatus('empty', err.message || 'Nothing to show for this race yet.', 'Try another race from the list above.');
      } else {
        console.error(err);
        showStatus('error', 'Couldn’t load this race', 'Check your connection, or pick another race, then try again.', () => go(slug, false));
      }
    }
  }

  showStatus('loading', 'Loading the races…');
  try {
    const index = await fetchJSON(root + 'index.json');
    races = Array.isArray(index && index.races) ? index.races.filter((r) => r && r.slug) : [];
  } catch (err) {
    console.error(err);
    showStatus('error', 'Couldn’t load the race list', 'Check your connection, then try again.', () => window.location.reload());
    return;
  }
  if (races.length === 0) {
    showStatus('empty', 'No races yet', 'The first one appears after the next Grand Prix.');
    return;
  }
  const slug = chooseRace(races, params.get('race'), readStored());
  currentSlug = slug;
  mountPicker();
  window.addEventListener('popstate', () => {
    const want = chooseRace(races, new URLSearchParams(window.location.search).get('race'), null);
    if (want && want !== currentSlug) go(want, false);
  });
  await go(slug, false);
}

/** About `count` round tick values between lo and hi (e.g. 260, 280, 300, 320). */
export function niceTicks(lo, hi, count) {
  if (!isFinite(lo) || !isFinite(hi) || hi <= lo) return [];
  const raw = (hi - lo) / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map((m) => m * mag)
    .reduce((best, st) => (Math.abs(st - raw) < Math.abs(best - raw) ? st : best));
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}
