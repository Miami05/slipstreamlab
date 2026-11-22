// Screen 1: What happened
import {
  bootPage, el, svgEl, clear, fetchJSON, driverMap, lastName, colorFor, lightenForText,
  mixWithWhite, colorsClose, kindInfo, confidenceLevel, confidenceWord, safeFileName, isNum,
} from './common.js';

const W = 600;
const H = 270;
const PLOT = { x0: 40, x1: 590, yAxis: 220, yTop: 46, yBot: 206 };
const FONT = 'Inter, system-ui, sans-serif';

/** Draw one moment's chart into `svg` (cleared first). */
export function drawChart(svg, chart, drivers, ariaText) {
  clear(svg);
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', ariaText);

  const isLaps = chart.type === 'laptime';
  const series = (Array.isArray(chart.series) ? chart.series : [])
    .map((s) => ({
      driver: s && s.driver,
      pts: (s && Array.isArray(s.points) ? s.points : []).filter((p) => Array.isArray(p) && isNum(p[0]) && isNum(p[1])),
    }))
    .filter((s) => s.pts.length >= 2)
    .slice(0, 2);

  let xmin = Infinity; let xmax = -Infinity; let ymin = Infinity; let ymax = -Infinity;
  for (const s of series) {
    for (const p of s.pts) {
      xmin = Math.min(xmin, p[0]); xmax = Math.max(xmax, p[0]);
      ymin = Math.min(ymin, p[1]); ymax = Math.max(ymax, p[1]);
    }
  }
  if (!series.length) { xmin = 0; xmax = 1; ymin = 0; ymax = 1; }
  const xScale = (x) => PLOT.x0 + ((x - xmin) / (xmax - xmin || 1)) * (PLOT.x1 - PLOT.x0 - 6);
  const yScale = (y) => PLOT.yBot - ((y - ymin) / (ymax - ymin || 1)) * (PLOT.yBot - PLOT.yTop);

  // Dashed grey zone
  const zone = Array.isArray(chart.zone) && chart.zone.length === 2 && isNum(chart.zone[0]) && isNum(chart.zone[1]) ? chart.zone : null;
  if (zone) {
    const zx0 = Math.max(PLOT.x0, Math.min(xScale(zone[0]), xScale(zone[1])));
    const zx1 = Math.min(PLOT.x1, Math.max(xScale(zone[0]), xScale(zone[1])));
    svg.appendChild(svgEl('rect', {
      x: zx0.toFixed(1), y: 10, width: Math.max(2, zx1 - zx0).toFixed(1), height: PLOT.yAxis - 10,
      fill: '#9AA4B2', 'fill-opacity': 0.08, stroke: '#9AA4B2', 'stroke-opacity': 0.5, 'stroke-dasharray': '6 6',
    }));
    if (chart.zone_label) {
      const label = String(chart.zone_label);
      const fits = zx0 + 8 + label.length * 7.6 <= PLOT.x1;
      svg.appendChild(svgEl('text', {
        x: (fits ? zx0 + 8 : Math.min(PLOT.x1, zx1) - 8).toFixed(1), y: 30, fill: '#C9D1DB', 'font-size': 14,
        'font-family': FONT, 'text-anchor': fits ? 'start' : 'end', text: label,
      }));
    }
  }

  // Axes + labels
  svg.appendChild(svgEl('line', { x1: PLOT.x0, y1: PLOT.yAxis, x2: PLOT.x1, y2: PLOT.yAxis, stroke: '#333B47' }));
  svg.appendChild(svgEl('text', { x: 4, y: 16, fill: '#9AA4B2', 'font-size': 12, 'font-family': FONT, text: chart.y_label || (isLaps ? 'Lap time (higher = slower)' : 'Speed') }));
  svg.appendChild(svgEl('text', { x: PLOT.x0, y: 250, fill: '#9AA4B2', 'font-size': 13, 'font-family': FONT, text: chart.x_label || (isLaps ? 'Laps →' : 'Along the straight →') }));
  if (isLaps && series.length) {
    svg.appendChild(svgEl('text', { x: PLOT.x0, y: 236, fill: '#9AA4B2', 'font-size': 11, 'font-family': FONT, text: 'Lap ' + Math.round(xmin) }));
    svg.appendChild(svgEl('text', { x: PLOT.x1, y: 236, fill: '#9AA4B2', 'font-size': 11, 'font-family': FONT, 'text-anchor': 'end', text: 'Lap ' + Math.round(xmax) }));
  }

  // Lines and direct labels (team colour; teammates get a lighter dashed line)
  const ends = [];
  series.forEach((s, i) => {
    const base = colorFor(drivers, s.driver);
    let stroke = base;
    let dash = null;
    if (i === 1 && colorsClose(base, colorFor(drivers, series[0].driver))) {
      stroke = mixWithWhite(base, 0.45);
      dash = '9 5';
    }
    svg.appendChild(svgEl('polyline', {
      fill: 'none', stroke, 'stroke-width': 3, 'stroke-linejoin': 'round', 'stroke-linecap': 'round',
      'stroke-dasharray': dash, points: s.pts.map((p) => xScale(p[0]).toFixed(1) + ',' + yScale(p[1]).toFixed(1)).join(' '),
    }));
    const last = s.pts[s.pts.length - 1];
    ends.push({ s, color: stroke, y: yScale(last[1]) });
  });
  // keep end labels apart
  const placed = ends.map((e) => ({ ...e, ly: Math.max(24, Math.min(214, e.y - 10)) }));
  if (placed.length === 2 && Math.abs(placed[0].ly - placed[1].ly) < 20) {
    const lower = placed[0].y > placed[1].y ? placed[0] : placed[1];
    const upper = lower === placed[0] ? placed[1] : placed[0];
    upper.ly = Math.max(24, Math.min(upper.y - 10, lower.y - 30));
    lower.ly = Math.min(214, Math.max(lower.y + 20, upper.ly + 22));
  }
  placed.forEach((e) => {
    svg.appendChild(svgEl('text', {
      x: PLOT.x1 - 6, y: e.ly.toFixed(1), fill: lightenForText(e.color), 'font-size': 14, 'font-weight': 600,
      'font-family': FONT, 'text-anchor': 'end', text: lastName(drivers, e.s.driver),
    }));
  });

  svg.appendChild(svgEl('text', { x: PLOT.x1, y: 264, fill: '#6B7380', 'font-size': 12, 'font-family': 'Space Grotesk, sans-serif', 'text-anchor': 'end', text: 'SlipstreamLab' }));
}

/** Serialise the chart SVG, draw it on a 2x canvas with a title, and download a PNG. */
export async function saveChartImage(svg, title, fileName) {
  const clone = svg.cloneNode(true);
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  clone.setAttribute('width', String(W * 2));
  clone.setAttribute('height', String(H * 2));
  const xml = new XMLSerializer().serializeToString(clone);
  const url = URL.createObjectURL(new Blob([xml], { type: 'image/svg+xml;charset=utf-8' }));
  try {
    const img = new Image();
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = () => reject(new Error('image failed to load'));
      img.src = url;
    });
    const pad = 24;
    const titleH = 56;
    const scale = 2;
    const canvas = document.createElement('canvas');
    canvas.width = (W + pad * 2) * scale;
    canvas.height = (titleH + H + pad) * scale;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#161B22';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.scale(scale, scale);
    let size = 22;
    ctx.fillStyle = '#E6EAF0';
    ctx.textBaseline = 'alphabetic';
    do {
      ctx.font = '700 ' + size + 'px "Space Grotesk", Inter, system-ui, sans-serif';
      size -= 1;
    } while (ctx.measureText(title).width > W && size > 12);
    ctx.fillText(title, pad, 38);
    ctx.drawImage(img, pad, titleH, W, H);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
    if (!blob) throw new Error('could not create PNG');
    const a = document.createElement('a');
    const dl = URL.createObjectURL(blob);
    a.href = dl;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(dl), 2000);
  } finally {
    URL.revokeObjectURL(url);
  }
}

function render(race, ctx) {
  const content = ctx.content;
  const drivers = driverMap(race.drivers);
  const moments = Array.isArray(race.moments) ? race.moments.filter((m) => m && m.chart) : [];
  const headline = race.headline || {};

  content.appendChild(el('section', { class: 'headline', style: 'display:flex;flex-direction:column;gap:12px' },
    el('h1', { text: headline.title || race.name || 'Race report' }),
    headline.text ? el('p', { class: 'lede', text: headline.text }) : null));

  if (!moments.length) {
    content.appendChild(el('section', { class: 'card' },
      el('h2', { text: 'Nothing stood out in this race' }),
      el('p', { class: 'caption', text: 'We didn’t spot any clear moments where a driver lost speed or time. Try another race, or have a look at the “What if?” page.' })));
    return null;
  }

  // Chart card
  const titleEl = el('h2', { id: 'chart-title' });
  const svg = svgEl('svg', { class: 'chart-svg' });
  const captionEl = el('p', { class: 'caption', 'aria-live': 'polite' });
  const saveNote = el('span', { class: 'save-note', role: 'status' });
  const saveBtn = el('button', { type: 'button', class: 'btn', 'aria-label': 'Save this chart as an image' },
    svgEl('svg', { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' },
      svgEl('path', { d: 'M12 3v12' }), svgEl('path', { d: 'M7 10l5 5 5-5' }), svgEl('path', { d: 'M4 19h16' })),
    'Save image');
  content.appendChild(el('section', { class: 'card', 'aria-labelledby': 'chart-title' },
    el('div', { class: 'card-head' }, titleEl, el('div', { style: 'display:flex;gap:12px;align-items:center;flex-wrap:wrap' }, saveNote, saveBtn)),
    svg, captionEl));

  // Moments card
  const list = el('ul', { class: 'moments' });
  const whyEl = el('p', { class: 'why', 'aria-live': 'polite' });
  content.appendChild(el('section', { class: 'card', 'aria-labelledby': 'moments-title' },
    el('div', null,
      el('h2', { id: 'moments-title', text: 'Key moments' }),
      el('p', { class: 'hint', text: 'Tap a moment to see it on the chart.' })),
    list, whyEl));

  let selected = 0;
  const buttons = moments.map((m, i) => {
    const info = kindInfo(m.kind, m.label);
    const level = confidenceLevel(m.confidence);
    const btn = el('button', { type: 'button', class: 'moment', 'aria-pressed': 'false', onclick: () => select(i) },
      el('span', { class: 'moment-name' },
        el('span', { class: 'moment-bar', style: 'background:' + colorFor(drivers, m.driver), 'aria-hidden': 'true' }),
        lastName(drivers, m.driver)),
      el('span', { class: 'moment-when', text: m.lap_label || (m.lap ? 'Lap ' + m.lap : '') }),
      el('span', { class: 'pill', style: 'background:' + info.bg + ';color:' + info.fg, text: info.label }),
      el('span', { class: 'moment-conf' },
        el('span', { class: 'meter', 'aria-hidden': 'true' },
          [1, 2, 3].map((n) => el('span', { class: n <= level ? 'on' : null }))),
        el('span', { text: confidenceWord(m.confidence) + ' confidence' }),
        el('span', { class: 'badge', hidden: true, text: 'Viewing' })));
    list.appendChild(el('li', null, btn));
    return btn;
  });

  function select(i) {
    selected = i;
    const m = moments[i];
    const chart = m.chart;
    titleEl.textContent = chart.title || 'Chart';
    captionEl.textContent = m.caption || '';
    drawChart(svg, chart, drivers, (chart.title || 'Chart') + '. ' + (m.caption || ''));
    buttons.forEach((b, j) => {
      b.setAttribute('aria-pressed', j === i ? 'true' : 'false');
      b.querySelector('.badge').hidden = j !== i;
    });
    const level = String(m.confidence || 'low');
    whyEl.textContent = m.why ? 'Why ' + level + ' confidence: ' + m.why : '';
    saveNote.textContent = '';
  }

  saveBtn.addEventListener('click', async () => {
    const m = moments[selected];
    saveBtn.disabled = true;
    saveNote.textContent = 'Saving…';
    try {
      await saveChartImage(svg, (m.chart && m.chart.title) || 'SlipstreamLab chart', safeFileName(ctx.slug + '-' + (m.id || selected + 1)) + '.png');
      saveNote.textContent = 'Saved';
    } catch (e) {
      console.error(e);
      saveNote.textContent = 'Couldn’t save the image';
    } finally {
      saveBtn.disabled = false;
    }
  });

  select(0);
  return null;
}

if (typeof document !== 'undefined') {
  bootPage({
    page: 'home',
    load: (ctx) => fetchJSON(ctx.root + ctx.slug + '/race.json'),
    render,
  });
}
