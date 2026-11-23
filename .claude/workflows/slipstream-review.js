export const meta = {
  name: 'slipstream-review',
  description: 'Fable + Neon (Gemini) review of the SlipstreamLab repo, then verify findings',
  phases: [
    { title: 'Review', detail: 'Fable and Neon review independently' },
    { title: 'Verify', detail: 'check each finding against the code' },
  ],
}

const REPO = '/Users/lediodurmishaj/Projects/slipstreamlab'
const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'integer' },
          severity: { type: 'string', enum: ['high', 'medium', 'low'] },
          title: { type: 'string' },
          scenario: { type: 'string' },
        },
        required: ['file', 'severity', 'title', 'scenario'],
      },
    },
  },
  required: ['findings'],
}

const SCOPE = `Repository: ${REPO} (git, review the entire repo, or the diff if one is passed in args).
What it is: free open-source fan site explaining F1 races after they happen. Python pipeline slipstream/ pulls OpenF1 data (openf1.py client+cache, model.py, energy.py battery/lift-coast detection, tyres.py fuel-corrected degradation, whatif.py pit-stop what-if, compare.py two-driver lap compare, story.py plain-English text, validate.py, build.py CLI, social.py 1080x1920 images) and writes web/data/*.json per docs/DATA_CONTRACT.md. Static site web/ (index.html, whatif.html, compare.html, assets/common.js, home.js, whatif.js, compare.js, app.css) reads that JSON. Tests: tests/ (pytest) and tests/web/*.mjs.
Also: conditions.py (rain/traffic/blue flags), powercut.py, evaluate.py (checks findings against tests/validation_cases.json, real-world verdicts from docs/VALIDATION.md), cleancache.py.
Generated real data exists in web/data/ (2026 races) — use it and \`python -m slipstream evaluate\` to check plausibility.
Known weaknesses already documented (report only if worse than described): no battery channel (causes inferred, capped at medium); an engine power cut like Gasly Las Vegas is not visible in public data; the power_cut detector (slipstream/powercut.py) is deliberately SWITCHED OFF (ENABLED = False) after 12 review rounds — only report powercut.py issues that affect the pipeline while it is off; what-if only moves green-flag stops (Las Vegas has none); some races have degraded data.
Look for: real bugs (crashes, wrong maths, wrong sign conventions, off-by-one laps, contract mismatches between pipeline and site, XSS/innerHTML with data, broken paths on GitHub Pages), misleading user-facing claims, legal guardrails from docs/LEGAL.md (no headshots/logos, disclaimer present). Skip style nitpicks. READ-ONLY: do not modify files. Return at most 12 findings, most severe first, each with a concrete failure scenario.`

// Optional: Workflow args {base: "<commit>"} reviews only `git diff <base>...HEAD` (plus the context it needs).
const DIFF_NOTE = (args && args.base)
  ? `\nREVIEW SCOPE: only the changes in \`git -C ${REPO} diff ${args.base}...HEAD\` (read surrounding code as needed). Earlier code was already reviewed.`
  : ''

phase('Review')
const reviews = await parallel([
  () => agent(SCOPE + DIFF_NOTE, { label: 'Fable review', phase: 'Review', agentType: 'fable-reviewer', schema: FINDINGS }),
  () => agent(SCOPE + DIFF_NOTE + `\nFollow your agent instructions: put the code INTO the prompt (diff mode if a REVIEW SCOPE is given, else whole-repo mode), Gemini must not need tools. Then return Gemini's findings in the schema (translate faithfully, do not add your own).`, { label: 'Neon review', phase: 'Review', agentType: 'gemini-reviewer', schema: FINDINGS }),
])
const tagged = []
reviews.forEach((r, i) => { if (r) r.findings.forEach(f => tagged.push({ ...f, by: i === 0 ? 'Fable' : 'Neon' })) })
log(`Fable: ${reviews[0] ? reviews[0].findings.length : 'failed'} findings, Neon: ${reviews[1] ? reviews[1].findings.length : 'failed'} findings`)
if (!tagged.length) return { confirmed: [], rejected: [], raw: reviews }

phase('Verify')
const VERDICTS = {
  type: 'object',
  properties: {
    results: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          index: { type: 'integer' },
          real: { type: 'boolean' },
          severity: { type: 'string', enum: ['high', 'medium', 'low'] },
          evidence: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['index', 'real', 'severity', 'evidence', 'fix'],
      },
    },
  },
  required: ['results'],
}
const list = tagged.map((f, i) => `[${i}] (${f.by}) ${f.severity} ${f.file}:${f.line ?? '?'} — ${f.title}. Scenario: ${f.scenario}`).join('\n')
const v = await agent(`You verify code-review findings for the repo at ${REPO}. READ-ONLY. For each finding below, open the code (and web/data/*.json real outputs where relevant), decide if it is a REAL defect (reproduce mentally or by running read-only commands like the tests or a python one-liner), merge duplicates (mark the duplicate real=false with evidence "duplicate of [n]"), set your own severity, and give a concrete minimal fix. Default to real=false if you cannot confirm it.\n\n${list}`, { label: 'Verify (Sol)', phase: 'Verify', model: 'opus', schema: VERDICTS })
const confirmed = [], rejected = []
;(v ? v.results : []).forEach(r => { const f = tagged[r.index]; if (!f) return; (r.real ? confirmed : rejected).push({ ...f, severity: r.severity, evidence: r.evidence, fix: r.fix }) })
return { confirmed, rejected }