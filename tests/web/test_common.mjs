// Run with: node tests/web/test_common.mjs
import assert from 'node:assert/strict';
import {
  hexToRgb, contrastRatio, lightenForText, ordinal, secondsPhrase, rangeText, trafficText,
  finishText, finishKind, pitLabel, gapLabel, formatLapTime, formatDate, safeFileName, interpolatePath,
  cumulativeDistances, distanceAt, niceTicks, timeAtDistance, gapSeconds, makeGapReader, aheadText, fitPoints, makeOutline,
  chooseRace, pageUrl, dataRoot, kindInfo, confidenceLevel, ovalTrack, validTrack, colorsClose,
} from '../../web/assets/common.js';
import { placeCars, SECONDS_TO_FRACTION } from '../../web/assets/whatif.js';
import { GHOST_NOTE } from '../../web/assets/compare.js';
import fs from 'node:fs';

let n = 0;
function test(name, fn) { fn(); n++; console.log('ok -', name); }
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, a + ' !~ ' + b);

test('hexToRgb', () => {
  assert.deepEqual(hexToRgb('#F47600'), [244, 118, 0]);
  assert.deepEqual(hexToRgb('#fff'), [255, 255, 255]);
  assert.equal(hexToRgb('nope'), null);
  assert.equal(hexToRgb(null), null);
});

test('contrast ratio basics', () => {
  near(contrastRatio('#000000', '#FFFFFF'), 21, 1e-9);
  near(contrastRatio('#123456', '#123456'), 1, 1e-9);
});

test('lightenForText reaches 4.5:1 on the card colour for every team colour', () => {
  const colours = ['#E80020', '#3671C6', '#229971', '#27F4D2', '#FF8000', '#0000FF', '#000000', '#64C4FF', '#FF87BC', '#112233'];
  for (const c of colours) {
    const out = lightenForText(c, '#161B22', 4.5);
    assert.ok(contrastRatio(out, '#161B22') >= 4.5, c + ' -> ' + out);
  }
});

test('lightenForText leaves already-readable colours alone and survives bad input', () => {
  assert.equal(lightenForText('#27F4D2'), '#27F4D2');
  assert.equal(lightenForText('garbage'), '#E6EAF0');
  assert.notEqual(lightenForText('#E80020'), '#E80020'); // red is too dark on the card
});

test('colorsClose detects teammates', () => {
  assert.ok(colorsClose('#E80020', '#E80020'));
  assert.ok(!colorsClose('#E80020', '#27F4D2'));
});

test('ordinal', () => {
  assert.deepEqual([1, 2, 3, 4, 11, 12, 13, 21, 22, 101, 111].map(ordinal),
    ['1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd', '101st', '111th']);
});

test('secondsPhrase and rangeText', () => {
  assert.equal(secondsPhrase(1.44), '1.4 seconds faster');
  assert.equal(secondsPhrase(-2.14), '2.1 seconds slower');
  assert.equal(secondsPhrase(-1), '1.0 second slower');
  assert.equal(rangeText(-2.1, 4.8), 'This is a guess: it could be anywhere from 2.1 seconds slower to 4.8 seconds faster.');
  assert.equal(rangeText(4.8, -2.1), rangeText(-2.1, 4.8)); // swapped input is fixed
  assert.match(rangeText(null, undefined), /guess/);
});

test('finish sentences', () => {
  assert.equal(finishText(5, 4), 'He would probably finish 4th instead of 5th');
  assert.equal(finishText(5, 6), 'He would probably finish 6th instead of 5th');
  assert.equal(finishText(5, 5), 'He would probably still finish 5th');
  assert.equal(finishKind(5, 4), 'better');
  assert.equal(finishKind(5, 6), 'worse');
  assert.equal(finishKind(5, 5), 'same');
  assert.equal(finishKind(5, undefined), 'unknown');
});

test('pit label wording', () => {
  assert.equal(pitLabel(28, 31), 'Stop 3 laps earlier (lap 28)');
  assert.equal(pitLabel(30, 31), 'Stop 1 lap earlier (lap 30)');
  assert.equal(pitLabel(33, 31), 'Stop 2 laps later (lap 33)');
  assert.equal(pitLabel(31, 31), 'Stop on lap 31 — the same as in the real race');
});

test('traffic, gap and time formatting', () => {
  assert.equal(trafficText(true), 'He’d come out right behind another car, which could cost him time.');
  assert.equal(trafficText(false), 'No car within 1.5 seconds in front of him when he comes out.');
  assert.ok(!/slower|stuck/.test(trafficText(true)));
  assert.equal(gapLabel(-1.4), '1.4s ahead');
  assert.equal(gapLabel(2), '2.0s behind');
  assert.equal(gapLabel(0.01), 'alongside');
  assert.equal(formatLapTime(95.123), '1:35.123');
  assert.equal(formatLapTime(59.5), '0:59.500');
  assert.equal(formatLapTime(null), '');
  assert.equal(formatDate('2026-11-22'), '22 Nov 2026');
  assert.equal(safeFileName('2026-21-Las Vegas/../x'), '2026-21-las-vegas-x');
});

test('moment kinds and confidence', () => {
  assert.equal(kindInfo('battery_low').bg, '#2E2547');
  assert.equal(kindInfo('energy_saving').fg, '#9CC8FF');
  assert.equal(kindInfo('tyre_wear').label, 'Tyres probably wore out');
  assert.equal(kindInfo('battery_low').label, 'Lost power (maybe battery)');
  assert.equal(kindInfo('energy_saving').label, 'Lifted off early (probably saving energy or cooling the car)');
  assert.equal(kindInfo('power_cut').label, 'Had less power for most of the race (probably a car problem)');
  for (const k of ['battery_low', 'energy_saving', 'tyre_wear', 'power_cut']) {
    assert.match(kindInfo(k).label, /probably|maybe/i); // inferred causes are always hedged
    assert.ok(contrastRatio(kindInfo(k).fg, kindInfo(k).bg) >= 4.5, k + ' pill contrast'); // readable pills
  }
  assert.equal(kindInfo('weird', 'Something').label, 'Something');
  assert.deepEqual(['high', 'medium', 'low', undefined].map(confidenceLevel), [3, 2, 1, 1]);
});

const pathA = [[0, 0, 0], [10, 10, 0], [20, 10, 10]]; // 10 units per 10 s, then up
test('interpolatePath clamps and interpolates', () => {
  assert.deepEqual(interpolatePath(pathA, -5), { x: 0, y: 0 });
  assert.deepEqual(interpolatePath(pathA, 5), { x: 5, y: 0 });
  assert.deepEqual(interpolatePath(pathA, 15), { x: 10, y: 5 });
  assert.deepEqual(interpolatePath(pathA, 99), { x: 10, y: 10 });
  assert.deepEqual(interpolatePath([], 1), { x: 0, y: 0 });
});

test('distance helpers', () => {
  const cum = cumulativeDistances(pathA);
  assert.deepEqual(cum, [0, 10, 20]);
  near(distanceAt(pathA, cum, 5), 5);
  near(distanceAt(pathA, cum, 15), 15);
  near(timeAtDistance(pathA, cum, 15), 15);
  near(timeAtDistance(pathA, cum, 99), 20);
});

test('gapSeconds: leader and time gap', () => {
  // A drives at 1 unit/s; B drives at 0.5 unit/s over the same line
  const mk = (rate) => {
    // both cars cover the same 40 units; A takes 40 s, B takes 80 s
    const total = 40 / rate;
    const path = [[0, 0, 0], [total / 2, 20, 0], [total, 40, 0]];
    return { path, cum: cumulativeDistances(path) };
  };
  const a = mk(1);
  const b = mk(0.5);
  const r = gapSeconds(a, b, 10); // A at 10; B reaches 10 at t=20 -> gap 10 s
  assert.equal(r.leader, 'a');
  near(r.gap, 10);
  const r2 = gapSeconds(b, a, 10);
  assert.equal(r2.leader, 'b');
  near(r2.gap, 10);
  assert.equal(gapSeconds(a, a, 7).leader, null);
  assert.equal(gapSeconds({ path: [], cum: [] }, a, 3).leader, null);
  // after A is finished, the gap stays equal to the finishing gap
  near(gapSeconds(a, b, 40).gap, 40);
  // at the very end both have covered the whole lap: the gap is the lap-time difference
  const end = gapSeconds(a, b, 999);
  assert.equal(end.leader, 'a');
  near(end.gap, 40);
  assert.equal(gapSeconds(a, b, 0).leader, null);
});

test('aheadText', () => {
  assert.equal(aheadText('Norris', 'Leclerc', { leader: 'a', gap: 0.24 }), 'Norris is 0.2 seconds ahead');
  assert.equal(aheadText('Norris', 'Leclerc', { leader: 'b', gap: 1.26 }), 'Leclerc is 1.3 seconds ahead');
  assert.equal(aheadText('Norris', 'Leclerc', { leader: 'a', gap: 0.01 }), 'Norris and Leclerc are side by side');
  assert.equal(aheadText('Norris', 'Leclerc', { leader: null, gap: 0 }), 'Norris and Leclerc are side by side');
});

test('there is no Overtake label any more and the page explains the ghost comparison', () => {
  assert.equal(GHOST_NOTE, 'Both laps start together, like a ghost car — this isn\'t their real gap on track.');
  const src = fs.readFileSync(new URL('../../web/assets/compare.js', import.meta.url), 'utf8');
  assert.ok(!/Overtake/.test(src));
  assert.match(src, /'Key moment'/);
});

// A path that does NOT start exactly on the line and runs past its lap time (like old data):
// the final readout must still be lap_time_b - lap_time_a.
const ghost = (lapA, lapB, offA = 0.2, offB = 0.03, over = 0.3) => {
  const mk = (lap, off) => {
    const rows = [];
    for (let t = off; t <= lap + over; t += 0.27) rows.push([Number(t.toFixed(2)), Math.cos(t / lap * 6.2832), Math.sin(t / lap * 6.2832)]);
    return { path: rows, cum: cumulativeDistances(rows), lap };
  };
  return [mk(lapA, offA), mk(lapB, offB)];
};

test('gapSeconds: the final readout is exactly the lap time difference', () => {
  const [a, b] = ghost(96.45, 96.182);
  const end = gapSeconds(a, b, 500);
  assert.equal(end.final, true);
  assert.equal(end.leader, 'b'); // the faster lap wins even though a has a trailing sample past the line
  near(end.gap, 96.45 - 96.182, 1e-9);
  const [c, d] = ghost(97.08, 97.159, 0.11, 0.23);
  const e2 = gapSeconds(c, d, 500);
  assert.equal(e2.leader, 'a');
  near(e2.gap, 0.079, 1e-9);
  assert.equal(aheadText('Russell', 'Antonelli', e2), 'Russell is 0.08 seconds ahead'); // not "side by side"
  assert.notEqual(aheadText('A', 'B', gapSeconds(...ghost(96.45, 96.182), 500)), 'A and B are side by side');
});

test('gapSeconds is anchored at t=0 and does not depend on where the first sample falls', () => {
  const [a, b] = ghost(90, 90, 0.2, 0.03);
  assert.equal(gapSeconds(a, b, 0).leader, null);
  assert.ok(gapSeconds(a, b, 45).gap < 0.2); // same pace: only the sampling offset (<0.2 s) remains
});

test('makeGapReader: hysteresis keeps the leader from flipping on noise; depends only on the playhead', () => {
  // two cars of identical pace with a tiny wobble in B's speed (+-0.04 s)
  const mk = (wobble) => {
    const rows = [];
    for (let i = 0; i <= 200; i++) { const t = i * 0.5; rows.push([t, t + wobble * Math.sin(i), 0]); }
    return { path: rows, cum: cumulativeDistances(rows), lap: 100 };
  };
  const a = mk(0);
  const b = mk(0.04);
  const reader = makeGapReader(a, b);
  const seen = new Set();
  for (let t = 0; t < 100; t += 0.1) seen.add(reader(t).leader);
  assert.deepEqual([...seen], [null]); // never calls a leader inside the dead band
  // scrubbing order does not matter
  const x1 = reader(42.3);
  reader(5); reader(99);
  assert.deepEqual(reader(42.3), x1);
  // a real, growing lead is reported, and does not flip back on a small wobble
  const fast = mk(0);
  const slow = { path: fast.path.map(([t, x, y]) => [t * 1.01, x, y]), lap: 101 };
  slow.cum = cumulativeDistances(slow.path);
  const r2 = makeGapReader(fast, slow);
  assert.equal(r2(60).leader, 'a');
  assert.equal(r2(1000).leader, 'a');
  near(r2(1000).gap, 1, 1e-9);
  // swapping the cars swaps the leader
  assert.equal(makeGapReader(slow, fast)(60).leader, 'b');
});

test('real/sample compare data: final readout equals the printed lap times', () => {
  const file = new URL('../../web/sample-data/2026-00-demo-grand-prix/compare.json', import.meta.url);
  const data = JSON.parse(fs.readFileSync(file, 'utf8'));
  for (const p of data.pairs) {
    const a = { path: p.a_path, cum: cumulativeDistances(p.a_path), lap: p.lap_time_a };
    const b = { path: p.b_path, cum: cumulativeDistances(p.b_path), lap: p.lap_time_b };
    const fin = makeGapReader(a, b)(1e6);
    near(fin.leader === 'a' ? fin.gap : -fin.gap, p.lap_time_b - p.lap_time_a, 1e-9);
  }
});

test('fitPoints keeps aspect ratio inside the padded box', () => {
  const fit = fitPoints([[0, 0], [1, 0], [1, 0.5]], 600, 340, 30);
  near(fit.x(0), 30); near(fit.x(1), 570);
  near(fit.y(0.5) - fit.y(0), 270); // 0.5 * (540/1)
  assert.ok(fit.y(0) >= 30 && fit.y(0.5) <= 310);
  // degenerate input does not throw / NaN
  const f2 = fitPoints([[0.5, 0.5]], 600, 340, 30);
  assert.ok(Number.isFinite(f2.x(0.5)));
  assert.ok(Number.isFinite(fitPoints([], 600, 340, 30).x(0)));
});

test('makeOutline walks a closed square and wraps', () => {
  const o = makeOutline([[0, 0], [10, 0], [10, 10], [0, 10]], true);
  assert.equal(o.total, 40);
  assert.deepEqual(o.at(5), { x: 5, y: 0 });
  assert.deepEqual(o.at(15), { x: 10, y: 5 });
  assert.deepEqual(o.at(45), { x: 5, y: 0 });
  assert.deepEqual(o.at(-5), { x: 0, y: 5 });
});

test('placeCars puts cars ahead further along the outline than the driver', () => {
  const pts = ovalTrack(200).map((p) => [p[0] * 500, p[1] * 500]);
  const outline = makeOutline(pts, true);
  const cars = placeCars(outline, 'HAM', [{ code: 'ALB', gap_s: -1.4 }, { code: 'GAS', gap_s: 2.0 }]);
  assert.equal(cars.length, 3);
  assert.ok(cars[0].me);
  assert.equal(cars[1].code, 'ALB');
  assert.equal(cars[2].code, 'GAS');
  const base = outline.at(outline.total * 0.3);
  near(cars[0].x, base.x); near(cars[0].y, base.y);
  const aheadPoint = outline.at(outline.total * 0.3 + 1.4 * SECONDS_TO_FRACTION * outline.total);
  near(cars[1].x, aheadPoint.x, 1e-6);
  assert.deepEqual(placeCars(outline, 'HAM', []).length, 1);
});

test('track validation and oval', () => {
  assert.ok(validTrack(ovalTrack()));
  assert.ok(!validTrack([[0, 0]]));
  assert.ok(!validTrack(null));
});

test('chooseRace: ?race > remembered (unless newer exists) > newest', () => {
  const races = [{ slug: 'c' }, { slug: 'b' }, { slug: 'a' }];
  assert.equal(chooseRace(races, 'b', null), 'b');
  assert.equal(chooseRace(races, 'zzz', null), 'c');
  assert.equal(chooseRace(races, null, { slug: 'a', newest: 'c' }), 'a');
  assert.equal(chooseRace(races, null, { slug: 'a', newest: 'b' }), 'c'); // a newer race appeared
  assert.equal(chooseRace(races, null, { slug: 'gone', newest: 'c' }), 'c');
  assert.equal(chooseRace([], 'a', null), null);
  assert.equal(chooseRace(undefined, 'a', null), null);
});

test('urls keep ?data= and pick the right root', () => {
  assert.equal(pageUrl('whatif.html', { race: 'x', data: 'sample' }), 'whatif.html?race=x&data=sample');
  assert.equal(pageUrl('index.html', {}), 'index.html');
  assert.equal(pageUrl('index.html', { race: 'x', data: '' }), 'index.html?race=x');
  assert.equal(dataRoot('?data=sample&race=a'), 'sample-data/');
  assert.equal(dataRoot('?race=a'), 'data/');
  assert.equal(dataRoot(''), 'data/');
});

test('niceTicks gives round speed and lap-time ticks', () => {
  assert.deepEqual(niceTicks(262, 331, 4), [280, 300, 320]);
  assert.deepEqual(niceTicks(94.1, 96.3, 4), [94.5, 95, 95.5, 96]);
  assert.deepEqual(niceTicks(5, 5, 4), []);
  assert.deepEqual(niceTicks(NaN, 3, 4), []);
});

console.log('\n' + n + ' tests passed');
