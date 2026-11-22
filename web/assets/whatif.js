// Screen 2: What if?
import {
  bootPage, el, svgEl, clear, fetchJSON, fetchOptional, EmptyError, driverMap, lastName, colorFor,
  lightenForText, finishKind, finishText, pitLabel, trafficText, rangeText, gapLabel, fitPoints,
  makeOutline, ovalTrack, validTrack, isNum,
} from './common.js';

const MW = 600;
const MH = 340;
const FONT = 'Inter, system-ui, sans-serif';
export const SECONDS_TO_FRACTION = 0.045; // 1 second ~ 4.5% of the outline (approximate, on purpose)
const MIN_SEPARATION = 56;

/** Pure-ish: work out where each car sits on the outline (pixel coords). */
export function placeCars(outline, driverCode, nearby) {
  const base = outline.total * 0.3;
  const placed = [{ code: driverCode, gap: 0, me: true, ...outline.at(base) }];
  for (const n of nearby) {
    const g = Number(n.gap_s);
    if (!Number.isFinite(g)) continue;
    const sign = g < 0 ? 1 : -1; // negative gap = ahead = further along the lap
    const dist = Math.max(MIN_SEPARATION, Math.abs(g) * SECONDS_TO_FRACTION * outline.total);
    placed.push({ code: n.code, gap: g, me: false, ...outline.at(base + sign * dist) });
  }
  return placed;
}

function render(data, ctx) {
  const { race, whatif, compare } = data;
  const drivers = driverMap(race.drivers);
  const pit = whatif.drivers || {};
  const codes = Object.keys(pit)
    .filter((c) => drivers.has(c) && pit[c] && Array.isArray(pit[c].options) && pit[c].options.length)
    .sort((a, b) => (pit[a].real_finish || 99) - (pit[b].real_finish || 99));
  if (!codes.length && typeof whatif.unavailable === 'string' && whatif.unavailable) throw new EmptyError(whatif.unavailable);
  if (!codes.length) throw new EmptyError('No driver made a normal pit stop under green flags in this race (stops under a safety car are not moved), so there is nothing to try.');

  const trackPoints = compare && Array.isArray(compare.pairs) && compare.pairs[0] && validTrack(compare.pairs[0].track)
    ? compare.pairs[0].track : ovalTrack();
  const fit = fitPoints(trackPoints, MW, MH, 64);
  const px = trackPoints.map((p) => [fit.x(p[0]), fit.y(p[1])]);
  const outline = makeOutline(px, true);
  const centre = fit.center;

  let code = codes[0];
  let options = [];
  let idx = 0;

  // Controls
  const h1 = el('h1');
  const select = el('select', { id: 'driver-select', class: 'select' },
    codes.map((c) => el('option', { value: c, text: lastName(drivers, c) })));
  const slider = el('input', { id: 'pitlap', type: 'range', min: 0, max: 0, step: 1, value: 0 });
  const sliderLabel = el('label', { class: 'slider-label', for: 'pitlap' });
  const endMid = el('span');
  const finishEl = el('div', { class: 'result-big' });
  const trafficEl = el('div', { class: 'result-line' });
  const rangeEl = el('div', { class: 'result-small' });

  ctx.content.appendChild(el('div', { style: 'display:flex;flex-direction:column;gap:16px' },
    h1,
    el('div', { class: 'controls' },
      el('label', { class: 'field', for: 'driver-select' }, el('span', { class: 'field-label', text: 'Driver' }), select))));

  ctx.content.appendChild(el('section', { class: 'card', style: 'gap:16px' },
    sliderLabel, slider,
    el('div', { class: 'slider-ends' }, el('span', { text: '← earlier' }), endMid, el('span', { text: 'later →' })),
    el('div', { class: 'result', 'aria-live': 'polite', 'aria-atomic': 'true' }, finishEl, trafficEl, rangeEl),
    el('p', { class: 'hint', text: 'We move his first pit stop made under green flags. His other stops stay as they really were.' })));

  // Map card
  const mapTitle = el('h2');
  const svg = svgEl('svg', { class: 'map-svg', viewBox: '0 0 ' + MW + ' ' + MH, role: 'img' });
  const carList = el('ul', { class: 'car-list' });
  ctx.content.appendChild(el('section', { class: 'card' },
    mapTitle, svg, carList,
    el('p', { class: 'hint', text: 'Spacing on the map is approximate. The numbers next to each name are the real gaps.' })));

  function drawMap(opt) {
    clear(svg);
    clear(carList);
    const me = lastName(drivers, code);
    svg.appendChild(svgEl('polygon', {
      points: px.map((p) => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' '),
      fill: 'none', stroke: '#333B47', 'stroke-width': 20, 'stroke-linejoin': 'round',
    }));
    const nearby = Array.isArray(opt.nearby) ? opt.nearby.slice(0, 2) : [];
    const cars = placeCars(outline, code, nearby);
    const meCar = cars[0];
    svg.appendChild(svgEl('circle', { cx: meCar.x.toFixed(1), cy: meCar.y.toFixed(1), r: 36, fill: opt.traffic ? '#FF8A3D' : '#4EA8FF', 'fill-opacity': 0.18 }));
    // draw other cars first so the driver is on top
    const order = cars.slice(1).concat([meCar]);
    const parts = [];
    for (const c of order) {
      const color = colorFor(drivers, c.code);
      const name = lastName(drivers, c.code);
      svg.appendChild(svgEl('circle', {
        cx: c.x.toFixed(1), cy: c.y.toFixed(1), r: c.me ? 12 : 9, fill: color,
        stroke: c.me ? '#E6EAF0' : '#0E1116', 'stroke-width': c.me ? 3 : 2,
      }));
      // label sits just outside the track, away from the middle of the map
      let nx = c.x - centre[0];
      let ny = c.y - centre[1];
      const len = Math.hypot(nx, ny) || 1;
      nx /= len; ny /= len;
      const lx = Math.max(48, Math.min(MW - 48, c.x + nx * 40));
      const ly = Math.max(20, Math.min(MH - 30, c.y + ny * 36));
      const common = {
        x: lx.toFixed(1), 'text-anchor': 'middle', 'font-family': FONT, 'font-weight': 600,
        stroke: '#161B22', 'stroke-width': 4, 'paint-order': 'stroke',
      };
      svg.appendChild(svgEl('text', { ...common, y: ly.toFixed(1), fill: c.me ? '#E6EAF0' : lightenForText(color), 'font-size': c.me ? 18 : 16, text: name }));
      if (!c.me) {
        svg.appendChild(svgEl('text', { ...common, y: (ly + 17).toFixed(1), fill: '#C9D1DB', 'font-size': 14, 'font-weight': 500, text: gapLabel(c.gap) }));
      }
      if (!c.me) {
        parts.push(name + ' is ' + gapLabel(c.gap) + ' of ' + me);
        carList.appendChild(el('li', null,
          el('span', { class: 'dot', style: 'background:' + color, 'aria-hidden': 'true' }),
          el('span', null, el('strong', { text: name }), ' — ' + gapLabel(c.gap))));
      }
    }
    if (!nearby.length) carList.appendChild(el('li', null, 'No other cars close by when ' + me + ' rejoins.'));
    svg.setAttribute('aria-label', 'Track map showing where ' + me + ' would rejoin. ' + (parts.length ? parts.join('. ') + '.' : 'No other cars are close.'));
  }

  function update() {
    const d = pit[code];
    const opt = options[idx];
    const me = lastName(drivers, code);
    const lap = opt.pit_lap;
    const label = pitLabel(lap, d.real_pit_lap);
    sliderLabel.textContent = label;
    slider.setAttribute('aria-valuetext', label);
    const kind = finishKind(d.real_finish, opt.finish_estimate);
    finishEl.textContent = finishText(d.real_finish, opt.finish_estimate);
    finishEl.className = 'result-big' + (kind === 'better' ? ' better' : kind === 'worse' ? ' worse' : '');
    trafficEl.textContent = trafficText(!!opt.traffic);
    rangeEl.textContent = rangeText(opt.low_s, opt.high_s);
    drawMap(opt);
  }

  function setDriver(c) {
    code = c;
    const d = pit[c];
    options = d.options.filter((o) => o && isNum(o.pit_lap)).slice().sort((a, b) => a.pit_lap - b.pit_lap);
    if (!options.length) options = d.options.slice();
    let best = 0;
    options.forEach((o, i) => {
      if (Math.abs(o.pit_lap - d.real_pit_lap) < Math.abs(options[best].pit_lap - d.real_pit_lap)) best = i;
    });
    idx = best;
    const me = lastName(drivers, c);
    h1.textContent = 'What if ' + me + ' had stopped for new tyres at a different time?';
    mapTitle.textContent = 'Where ' + me + ' comes back onto the track';
    slider.max = String(options.length - 1);
    slider.value = String(idx);
    endMid.textContent = 'He really stopped on lap ' + d.real_pit_lap;
    update();
  }

  select.addEventListener('change', () => setDriver(select.value));
  slider.addEventListener('input', () => {
    idx = Number(slider.value) || 0;
    update();
  });

  setDriver(code);
  return null;
}

if (typeof document !== 'undefined') {
  bootPage({
    page: 'whatif',
    load: async (ctx) => {
      const base = ctx.root + ctx.slug + '/';
      const [race, whatif, compare] = await Promise.all([
        fetchJSON(base + 'race.json'),
        fetchOptional(base + 'whatif.json', 'No “what if?” for this race yet.'),
        fetchJSON(base + 'compare.json').catch(() => null), // only used for the circuit outline
      ]);
      return { race, whatif, compare };
    },
    render,
  });
}
