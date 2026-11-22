// Screen 3: Compare two drivers, one lap
import {
  bootPage, el, svgEl, clear, fetchJSON, fetchOptional, EmptyError, driverMap, lastName, colorFor,
  lightenForText, colorsClose, mixWithWhite, fitPoints, interpolatePath, cumulativeDistances, distanceAt,
  gapSeconds, aheadText, formatLapTime, validTrack, validPath, clamp, isNum,
} from './common.js';

const MW = 600;
const MH = 340;
const FONT = 'Inter, system-ui, sans-serif';
const NORMAL_SECONDS = 20; // a whole lap plays in about 20 s at normal speed
const SLOW_FACTOR = 0.25;

/** Is the key moment an overtake (the lead changes around it)? */
export function isOvertake(a, b, duration, frac) {
  if (!isNum(frac) || !duration) return false;
  const eps = Math.max(0.5, duration * 0.03);
  const t = frac * duration;
  const before = distanceAt(a.path, a.cum, t - eps) - distanceAt(b.path, b.cum, t - eps);
  const after = distanceAt(a.path, a.cum, t + eps) - distanceAt(b.path, b.cum, t + eps);
  return before * after < 0;
}

function render(data, ctx) {
  const { race, compare } = data;
  const drivers = driverMap(race.drivers);
  const pairs = (Array.isArray(compare && compare.pairs) ? compare.pairs : [])
    .filter((p) => p && validTrack(p.track) && validPath(p.a_path) && validPath(p.b_path));
  if (!pairs.length) throw new EmptyError('No driver comparison for this race yet.');

  let pairIdx = 0;
  let raf = 0;
  let playing = false;
  let slow = false;
  let last = 0;
  let playhead = 0;
  let cur = null; // prepared pair

  const h1 = el('h1');
  const sub = el('div', { class: 'race-meta' });
  const aheadEl = el('div', { class: 'big-line' });
  const svg = svgEl('svg', { class: 'map-svg', viewBox: '0 0 ' + MW + ' ' + MH, role: 'img' });
  const playBtn = el('button', { type: 'button', class: 'btn btn-primary', text: 'Play' });
  const slowBtn = el('button', { type: 'button', class: 'btn', 'aria-pressed': 'false', text: 'Slow motion' });
  const scrubber = el('input', { type: 'range', min: 0, max: 1000, step: 1, value: 0, 'aria-label': 'Position in the lap' });
  const markerLabel = el('span', { class: 'scrub-marker-label', hidden: true });
  const markerTick = el('span', { class: 'scrub-tick', 'aria-hidden': 'true', hidden: true });
  const summaryEl = el('p', { class: 'caption' });
  const legendEl = el('div', { class: 'legend' });
  const lapTimesEl = el('p', { class: 'laptimes' });
  const pairSlot = el('div', { class: 'pair-picker' });

  ctx.content.appendChild(el('div', { class: 'toolbar' },
    el('div', null, sub, h1), pairSlot));
  ctx.content.appendChild(aheadEl);
  ctx.content.appendChild(el('section', { class: 'card', style: 'gap:16px' },
    svg,
    el('div', { class: 'player' },
      playBtn, slowBtn,
      el('div', { class: 'scrub' },
        el('span', { class: 'scrub-marker-row' }, markerLabel),
        el('span', { class: 'scrub-track' }, scrubber, markerTick))),
    summaryEl, legendEl, lapTimesEl));

  if (pairs.length > 1) {
    const sel = el('select', { id: 'pair-select', class: 'select' },
      pairs.map((p, i) => el('option', { value: String(i), text: lastName(drivers, p.a) + ' vs ' + lastName(drivers, p.b) + (p.lap ? ' · lap ' + p.lap : '') })));
    sel.addEventListener('change', () => setPair(Number(sel.value) || 0));
    pairSlot.appendChild(el('label', { class: 'field', for: 'pair-select' }, el('span', { class: 'field-label', text: 'Pick two drivers' }), sel));
  }

  function prepare(p) {
    const a = { path: p.a_path, cum: cumulativeDistances(p.a_path), code: p.a };
    const b = { path: p.b_path, cum: cumulativeDistances(p.b_path), code: p.b };
    const endA = a.path[a.path.length - 1][0];
    const endB = b.path[b.path.length - 1][0];
    const lapA = isNum(p.lap_time_a) && p.lap_time_a > 0 ? p.lap_time_a : endA;
    const lapB = isNum(p.lap_time_b) && p.lap_time_b > 0 ? p.lap_time_b : endB;
    const duration = Math.max(lapA, lapB, endA, endB) || 1;
    const fit = fitPoints(p.track, MW, MH, 36);
    const nameA = lastName(drivers, p.a);
    const nameB = lastName(drivers, p.b);
    let colorA = colorFor(drivers, p.a, '#FF8000');
    let colorB = colorFor(drivers, p.b, '#4EA8FF');
    let altB = false;
    if (colorsClose(colorA, colorB)) { colorB = mixWithWhite(colorB, 0.5); altB = true; }
    return { p, a, b, duration, fit, nameA, nameB, colorA, colorB, altB, lapA, lapB };
  }

  function drawStatic() {
    clear(svg);
    const { p, fit, nameA, nameB, colorA, colorB } = cur;
    const pts = p.track.map((q) => fit.x(q[0]).toFixed(1) + ',' + fit.y(q[1]).toFixed(1));
    svg.appendChild(svgEl('polyline', { points: pts.join(' '), fill: 'none', stroke: '#333B47', 'stroke-width': 20, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    (Array.isArray(p.segments) ? p.segments : []).forEach((s) => {
      if (!s || !isNum(s.from) || !isNum(s.to)) return;
      const from = clamp(Math.round(s.from), 0, pts.length - 1);
      const to = clamp(Math.round(s.to), 0, pts.length - 1);
      if (to <= from) return;
      const color = s.faster === p.a ? colorA : s.faster === p.b ? colorB : null;
      if (!color) return;
      svg.appendChild(svgEl('polyline', { points: pts.slice(from, to + 1).join(' '), fill: 'none', stroke: color, 'stroke-width': 6, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    });
    cur.dotA = svgEl('circle', { r: 12, fill: colorA, stroke: '#0E1116', 'stroke-width': 2 });
    cur.dotB = svgEl('circle', { r: 12, fill: colorB, stroke: '#0E1116', 'stroke-width': 2 });
    cur.txtA = svgEl('text', { fill: lightenForText(colorA), 'font-size': 16, 'font-weight': 600, 'font-family': FONT, 'text-anchor': 'middle', stroke: '#161B22', 'stroke-width': 4, 'paint-order': 'stroke', text: nameA });
    cur.txtB = svgEl('text', { fill: lightenForText(colorB), 'font-size': 16, 'font-weight': 600, 'font-family': FONT, 'text-anchor': 'middle', stroke: '#161B22', 'stroke-width': 4, 'paint-order': 'stroke', text: nameB });
    [cur.dotB, cur.dotA, cur.txtB, cur.txtA].forEach((n) => svg.appendChild(n));
  }

  function drawLegend() {
    clear(legendEl);
    const { nameA, nameB, colorA, colorB } = cur;
    [[nameA, colorA], [nameB, colorB]].forEach(([n, c]) => {
      legendEl.appendChild(el('span', null,
        el('span', { class: 'swatch', style: 'background:' + c, 'aria-hidden': 'true' }),
        el('span', { style: 'color:' + lightenForText(c), text: n + ' faster here' })));
    });
  }

  function paint() {
    const { a, b, duration, fit, nameA, nameB } = cur;
    const t = clamp(playhead, 0, duration);
    const pa = interpolatePath(a.path, t);
    const pb = interpolatePath(b.path, t);
    const ax = fit.x(pa.x); const ay = fit.y(pa.y);
    const bx = fit.x(pb.x); const by = fit.y(pb.y);
    cur.dotA.setAttribute('cx', ax.toFixed(1)); cur.dotA.setAttribute('cy', ay.toFixed(1));
    cur.dotB.setAttribute('cx', bx.toFixed(1)); cur.dotB.setAttribute('cy', by.toFixed(1));
    // labels: keep them apart (one above, one below the dots) and inside the picture
    const aAbove = ay <= by;
    const place = (txt, x, y, above) => {
      txt.setAttribute('x', clamp(x, 40, MW - 40).toFixed(1));
      txt.setAttribute('y', clamp(above ? y - 20 : y + 32, 18, MH - 8).toFixed(1));
    };
    place(cur.txtA, ax, ay, aAbove);
    place(cur.txtB, bx, by, !aAbove);
    const result = gapSeconds(a, b, t);
    const text = aheadText(nameA, nameB, result);
    if (aheadEl.textContent !== text) aheadEl.textContent = text;
    const v = Math.round((t / duration) * 1000);
    if (Number(scrubber.value) !== v) scrubber.value = String(v);
    scrubber.setAttribute('aria-valuetext', text + ', ' + Math.round((t / duration) * 100) + '% through the lap');
    svg.setAttribute('aria-label', 'Track map: ' + text + '. ' + nameA + ' and ' + nameB + ' shown as moving dots.');
  }

  function setPlaying(on) {
    playing = on;
    playBtn.textContent = on ? 'Pause' : 'Play';
    playBtn.setAttribute('aria-label', on ? 'Pause the replay' : 'Play the replay');
    if (on) {
      if (playhead >= cur.duration - 0.01) playhead = 0;
      last = 0;
      raf = requestAnimationFrame(tick);
    } else if (raf) {
      cancelAnimationFrame(raf);
      raf = 0;
    }
  }

  function tick(now) {
    if (!playing) return;
    if (last) {
      const rate = (cur.duration / NORMAL_SECONDS) * (slow ? SLOW_FACTOR : 1);
      playhead += ((now - last) / 1000) * rate;
    }
    last = now;
    if (playhead >= cur.duration) {
      playhead = cur.duration;
      paint();
      setPlaying(false);
      return;
    }
    paint();
    raf = requestAnimationFrame(tick);
  }

  function setPair(i) {
    setPlaying(false);
    pairIdx = i;
    cur = prepare(pairs[i]);
    const p = cur.p;
    h1.textContent = cur.nameA + ' vs ' + cur.nameB + ', one lap';
    sub.textContent = (race.name || '') + (p.lap ? ' · lap ' + p.lap : '');
    summaryEl.textContent = p.summary || '';
    const tA = formatLapTime(p.lap_time_a);
    const tB = formatLapTime(p.lap_time_b);
    lapTimesEl.textContent = tA && tB ? 'Lap times: ' + cur.nameA + ' ' + tA + ' · ' + cur.nameB + ' ' + tB : '';
    const frac = isNum(p.key_moment_frac) ? clamp(p.key_moment_frac, 0, 1) : null;
    if (frac !== null) {
      const overtake = isOvertake(cur.a, cur.b, cur.duration, frac);
      markerLabel.textContent = overtake ? 'Overtake' : 'Key moment';
      markerLabel.style.left = (frac * 100).toFixed(1) + '%';
      markerTick.style.left = (frac * 100).toFixed(1) + '%';
      markerLabel.hidden = false;
      markerTick.hidden = false;
    } else {
      markerLabel.hidden = true;
      markerTick.hidden = true;
    }
    playhead = (frac !== null ? frac : 0.5) * cur.duration;
    drawStatic();
    drawLegend();
    paint();
  }

  playBtn.addEventListener('click', () => setPlaying(!playing));
  slowBtn.addEventListener('click', () => {
    slow = !slow;
    slowBtn.setAttribute('aria-pressed', slow ? 'true' : 'false');
  });
  scrubber.addEventListener('input', () => {
    playhead = (Number(scrubber.value) / 1000) * cur.duration;
    last = 0;
    paint();
  });

  setPair(pairIdx);
  return () => {
    playing = false;
    if (raf) cancelAnimationFrame(raf);
  };
}

if (typeof document !== 'undefined') {
  bootPage({
    page: 'compare',
    load: async (ctx) => {
      const base = ctx.root + ctx.slug + '/';
      if (ctx.race && ctx.race.has_compare === false) throw new EmptyError('No driver comparison for this race yet.');
      const [race, compare] = await Promise.all([
        fetchJSON(base + 'race.json'),
        fetchOptional(base + 'compare.json', 'No driver comparison for this race yet.'),
      ]);
      return { race, compare };
    },
    render,
  });
}
