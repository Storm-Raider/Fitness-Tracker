// Accessibility checks: axe-core (WCAG 2.1 A/AA) on the main pages, plus the
// logging screen's touch targets against DESIGN.md.
//   scripts/e2e/serve.sh &
//   node scripts/e2e/a11y.mjs            # exit 1 if anything fails
// Needs Playwright (PLAYWRIGHT_DIR, default gstack's copy) and axe-core
// (AXE_PATH: the path to axe.min.js).
import { createRequire } from 'module';
import { readFileSync } from 'fs';
const require = createRequire((process.env.PLAYWRIGHT_DIR ||
  `${process.env.HOME}/.claude/skills/gstack/node_modules`) + '/');
const { chromium } = require('playwright');
const AXE = readFileSync(process.env.AXE_PATH ||
  `${process.env.HOME}/Desktop/Git/Tamuru/node_modules/axe-core/axe.min.js`, 'utf8');

const BASE = 'http://127.0.0.1:8765';
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true });
const page = await ctx.newPage();

let failures = 0;
const totals = {};
async function audit(pg, path) {
  await pg.goto(BASE + path, { waitUntil: 'networkidle' });
  await pg.addScriptTag({ content: AXE });
  const res = await pg.evaluate(async () => (await axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  })).violations.map(v => ({ id: v.id, n: v.nodes.length })));
  const nodes = res.reduce((a, v) => a + v.n, 0);
  failures += nodes;
  for (const v of res) totals[v.id] = (totals[v.id] || 0) + v.n;
  console.log(`${nodes ? 'FAIL' : 'PASS'}  ${path}  ${res.map(v => `${v.id}×${v.n}`).join(', ')}`);
}

// Signed-out pages are standalone templates (they don't extend base.html).
await audit(page, '/login');
await audit(page, '/forgot-password');

await page.goto(BASE + '/login');
await page.fill('input[name=username]', 'e2eadmin');
await page.fill('input[name=password]', 'e2e-admin-password-123');
await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);

const api = (method, path, body) => page.evaluate(async ([m, p, b]) => {
  const r = await fetch(p, { method: m, headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                             body: b ? JSON.stringify(b) : undefined });
  try { return await r.json(); } catch { return null; }
}, [method, path, body]);

// Seed enough data that icon buttons, rows and charts actually render.
const { exercises } = await api('GET', '/api/exercises');
const bench = exercises.find(e => e.name === 'Bench Press');
const done = await api('POST', '/workouts', {});
await api('POST', `/workouts/${done.id}/sets`, { exercise_id: bench.id, reps: 5, weight_kg: 60 });
await api('POST', `/workouts/${done.id}/finish`);
const live = await api('POST', '/workouts', {});
await api('POST', `/workouts/${live.id}/sets`, { exercise_id: bench.id, reps: 5, weight_kg: 62.5 });
const cardioId = await page.evaluate(async () => {
  const r = await fetch('/cardio'); const html = await r.text();
  const m = html.match(/<option value="(\d+)"/); return m && +m[1];
});
if (cardioId) await api('POST', `/workouts/${live.id}/cardio`, { exercise_id: cardioId, duration_minutes: 20 });
await api('POST', '/metrics', { weight_kg: 80 });
await api('POST', '/routines', { name: 'A11y Routine', exercise_ids: [bench.id] });

const PAGES = ['/', '/workouts', `/workouts/${live.id}`, `/workouts/${done.id}`, '/exercises',
  `/exercises/${bench.id}`, '/analytics', '/metrics', '/plan', '/routines/manage', '/cardio', '/settings'];

for (const path of PAGES) await audit(page, path);

// The invite page, seen by someone who isn't signed in.
const invite = await page.evaluate(async () => {
  const r = await fetch('/invite', { method: 'POST', body: new URLSearchParams({ max_uses: '1' }),
                                     headers: { Accept: 'application/json' } });
  return (await r.json()).invite_url;
});
const guest = await (await browser.newContext({ viewport: { width: 390, height: 844 } })).newPage();
await audit(guest, new URL(invite).pathname);

// Touch targets on the logging screen (DESIGN.md "Touch Targets").
await page.goto(`${BASE}/workouts/${live.id}`, { waitUntil: 'networkidle' });
const targets = await page.evaluate(() => {
  const size = el => { if (!el) return null; const r = el.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; };
  const hit = el => {  // the tappable box includes ::before/::after hit-area expansion
    if (!el) return null;
    const r = el.getBoundingClientRect();
    let [w, h] = [r.width, r.height];
    for (const p of ['::before', '::after']) {
      const s = getComputedStyle(el, p);
      if (s.content !== 'none' && s.position === 'absolute') {
        const pw = parseFloat(s.width) || 0, ph = parseFloat(s.height) || 0;
        w = Math.max(w, pw); h = Math.max(h, ph);
      }
    }
    return [Math.round(w), Math.round(h)];
  };
  const finish = [...document.querySelectorAll('button')].find(b => /finish/i.test(b.textContent));
  return {
    'Log Set height ≥56': [size(document.querySelector('.log-btn'))?.[1], 56],
    'Finish height ≥56': [size(finish)?.[1], 56],
    'Set delete ≥44×44': [Math.min(...(hit(document.querySelector('.set-del')) || [0])), 44],
    'Set edit ≥44×44': [Math.min(...(hit(document.querySelector('.set-edit-btn')) || [0])), 44],
    'Stepper ≥40': [Math.min(...(size(document.querySelector('.sx-btn, .stepper-btn')) || [0])), 40],
  };
});
for (const [name, [got, need]] of Object.entries(targets)) {
  const ok = got >= need;
  if (!ok) failures++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}  (got ${got})`);
}

// A logged set is announced to screen readers.
await page.evaluate(id => {
  document.getElementById('exercise-input').value = 'Bench Press';
  document.getElementById('exercise-id').value = id;
}, bench.id);
await page.click('.log-btn');
await page.waitForTimeout(400);
const said = await page.textContent('#sr-announcer');
const announced = /Set logged: Bench Press/.test(said || '');
if (!announced) failures++;
console.log(`${announced ? 'PASS' : 'FAIL'}  Logged set is announced  (${JSON.stringify(said)})`);

console.log('\naxe rule totals:', JSON.stringify(totals));
console.log(failures ? `${failures} problems` : 'all clear');
await browser.close();
process.exit(failures ? 1 : 0);
