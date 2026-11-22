# Validation: are our explanations right?

**Status: not yet validated. Don't share findings widely until this is done.**

## The big weakness

We don't know yet whether the explanations are right. "Battery ran low" is an
educated guess from speed and throttle, recorded only about 4 times per
second. There is no battery data to check it against. If the tool says
"battery" and the team later says "engine problem", people lose trust fast.

## The fix: compare with what was said after the race

After every race we build, compare each finding with what drivers and teams
said afterwards (driver interviews, team race reports, reputable motorsport
press). Record the result in the table below.

| Verdict | Meaning |
|---|---|
| ✅ Confirmed | A driver/team/press source names the same cause on the same part of the race |
| 🟡 Plausible | Nothing contradicts it, and the context fits (e.g. "we were struggling with energy") |
| ❌ Wrong | A source gives a different cause (engine problem, damage, traffic, lifting for a yellow flag…) |
| ⬜ Unknown | Nobody talked about it |

**Goal:** over 3–4 races, most checkable findings are ✅/🟡 and very few ❌.
Every ❌ becomes a test case and a detection fix. If ❌ is common, lower the
confidence levels or stop showing that kind of finding.

## Results

### 2026 Las Vegas Grand Prix (round 21)

Checked 2026-11-22 against post-race statements (Formula1.com "What the teams said",
Wolff interview, PlanetF1, The Race/Pitpass via Gemini Hydrogen research).

| Finding | Our confidence | Verdict | Source / note |
|---|---|---|---|
| Russell, lap 37, full throttle but −49 km/h → battery | high | 🟡 Plausible | Russell: his energy mode "left me very vulnerable in the middle sector". No lap-specific statement. ([PlanetF1](https://www.planetf1.com/news/george-russell-f1-2026-las-vegas-grand-prix-crash)) |
| Verstappen, lap 40, full throttle but −41 km/h → battery | high | 🟡 Plausible | Red Bull: "The whole race was about managing the deployment, timing everything correctly." ([F1.com teams](https://www.formula1.com/en/latest/article/what-the-teams-said-race-day-in-las-vegas-2026.2UjFcltnN1WABnZawPqeyP)). Verstappen: "just trying to press the battery whenever it was correct or defend" (pitdebrief.com, via Hydrogen) — deliberate energy management, not a failure. |
| Bortoleto, lap 14, lifted 3 s early → saving energy | medium | ⬜ Unknown | Only "issues on my car throughout the weekend". |
| Gasly, lap 6, full throttle but −27 km/h → battery | medium | ❌ **Wrong cause** | Gasly: "we had to turn the power down throughout the race to manage our engine temperatures" (≈45 of 50 laps). The power loss was real; the cause was engine heat, not the battery. ([F1.com teams](https://www.formula1.com/en/latest/article/what-the-teams-said-race-day-in-las-vegas-2026.2UjFcltnN1WABnZawPqeyP)) |

**Lesson from Las Vegas:** the detector finds *real power losses*, but "battery"
is only one possible cause. Engine-temperature power reduction looks the same
in speed/throttle data. Changes made:
- Wording: "lost power — possibly the battery" instead of "battery ran low".
- A loss that repeats on most laps of the race (like Gasly's) is more likely a
  car problem than tactical battery use → lower confidence and say so.

### 2026 United States GP (round 18) — checked 2026-11-22

| Finding | Conf. | Verdict | Source / note |
|---|---|---|---|
| Perez lap 28 → saving energy | high | ❌ Wrong | Being lapped: "we basically went straight into the blue flags" (pitdebrief.com) |
| Albon from lap 28 → tyres | high | 🟡 Plausible | "I've got no rears!" — but he also had damage (alexalbon.com) |
| Russell from lap 32 → tyres | high | ❌ Wrong | Traffic: "I caught up to Oscar, and I just couldn't overtake" (gpblog.com) |
| Alonso lap 55 → saving energy | medium | ⬜ Unknown | Only lap-1 floor damage mentioned |
| Antonelli from lap 11 → tyres | medium | 🟡 Plausible | Pirelli: first-stint degradation as predicted |

### 2026 Mexico City GP (round 19)

| Finding | Conf. | Verdict | Source / note |
|---|---|---|---|
| Hülkenberg lap 67 → saving energy | medium | ❌ Wrong cause | "We struggled with temperatures throughout" — lifting to cool, not to save energy (pitpass.com) |
| Leclerc lap 40 → saving energy | medium | 🟡 Plausible | Ferrari managing; no lap-specific quote |
| Colapinto from lap 19 → tyres | medium | 🟡 Plausible (weak) | Fits pit timing; Pirelli: degradation "within expected levels" |
| Leclerc from lap 11 → tyres | medium | 🟡 Plausible (weak) | same |
| Hamilton from lap 35 → tyres | medium | 🟡 Plausible (weak) | same |

### 2026 São Paulo GP (round 20)

| Finding | Conf. | Verdict | Source / note |
|---|---|---|---|
| Gasly from lap 44 → tyres | high | ❌ Wrong | Rain returned; fresh softs fitted lap 37 (pitdebrief.com) |
| Albon from lap 52 → tyres | medium | ❌ Wrong | Rain; fresh softs fitted lap 43 (f1network.net, williamsf1.com) |
| Norris lap 55 → lost power (maybe battery) | medium | ⬜ Unknown | No statement about power |
| Bortoleto lap 30 → lost power (maybe battery) | medium | 🟡 Plausible (strong) | "I got stuck… we are limited by 250 kW in the straight" (pitdebrief.com) |
| Hadjar lap 46 → lost power (maybe battery) | medium | ⬜ Unknown | No statement about power |

## Summary after 4 races (19 findings)

| ✅ Confirmed | 🟡 Plausible | ⬜ Unknown | ❌ Wrong |
|---|---|---|---|
| 0 | 9 | 4 | **6** (40 % of checkable findings) |

**Verdict: not ready to share widely.** The errors fall into four fixable patterns:

| Pattern | Cases | Fix |
|---|---|---|
| Rain read as tyre wear | Gasly, Albon (Brazil) | Use OpenF1 `weather` rainfall; exclude wet laps |
| Traffic read as tyres/energy | Russell (USA), Perez (USA, blue flags) | Gap to car ahead (`intervals`), blue-flag messages |
| Cooling read as energy saving | Hülkenberg (Mexico) | Label "Lifted off early (saving energy or cooling the car)" |
| Engine power cut read as battery | Gasly (Las Vegas) | Sustained top-speed deficit vs teammate → "probably a car problem" |

By type: "Lost power (maybe battery)" was the most reliable (1 wrong of 7);
tyre wear the least (3 wrong of 9). These 19 cases are stored in
`tests/validation_cases.json` so every change is checked against them.

**Note on AI research:** Gemini Hydrogen labelled Russell "CONFIRMED" although its
quote never mentions the battery. Always check the verdict label against the
quote itself; the table above uses our own verdicts.

## Smaller weaknesses (known)

- **What-if is still rough.** In Las Vegas it only works for 1 driver, because
  almost everyone stopped under the safety car and the what-if only moves
  green-flag stops. Tyre wear there came out flat, so gains are 0 with wide ranges.
- **Depends on OpenF1.** FastF1 already needs an F1 account login in 2026; OpenF1
  could follow. The client is isolated in `slipstream/openf1.py` so a different
  source can be swapped in.
- **Some races have poor data.** Budapest 2026 has heavily repeated speed and
  position samples, so the pipeline skips battery/energy and compare there rather
  than invent findings. Expect fewer stories for such races.
- **Niche audience.** The audience is fans who care about the "why". That is a
  niche — but the right one for content.
