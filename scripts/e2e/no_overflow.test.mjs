// No page may be wider than a phone screen. A page that overflows sideways
// makes iOS lay it out wider and zoom out, then zoom back in whenever a field
// is focused ("the app zooms when I type"). Seeds long, unbroken text (session
// notes, exercise and routine names) and checks every main page at 390px with
// mobile viewport rules on.
//   scripts/e2e/serve.sh &
//   node scripts/e2e/no_overflow.test.mjs
// Needs Playwright (PLAYWRIGHT_DIR, default gstack's copy).
import { createRequire } from 'module';
const require = createRequire((process.env.PLAYWRIGHT_DIR ||
  `${process.env.HOME}/.claude/skills/gstack/node_modules`) + '/');
const { chromium } = require('playwright');

const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8765';
const WIDTH = 390;
const browser = await chromium.launch();
// isMobile: the layout viewport follows <meta name=viewport> and grows with
// overflowing content, the way a phone's does.
const ctx = await browser.newContext({ viewport: { width: WIDTH, height: 844 }, isMobile: true, hasTouch: true });
const page = await ctx.newPage();

await page.goto(BASE + '/login');
await page.fill('input[name=username]', 'e2eadmin');
await page.fill('input[name=password]', 'e2e-admin-password-123');
await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);

const api = (method, path, body) => page.evaluate(async ([m, p, b]) => {
  const r = await fetch(p, { method: m, headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                             body: b ? JSON.stringify(b) : undefined });
  try { return await r.json(); } catch { return null; }
}, [method, path, body]);

const LONG = 'Felt strong today, long note to see how text wraps on the card '.repeat(3);
const { exercises } = await api('GET', '/api/exercises');
const bench = exercises.find(e => e.name === 'Bench Press');
const longEx = await api('POST', '/exercises', { name: 'Single Arm Half Kneeling Landmine Press With Pause', muscle_primary: 'Shoulders' });
const done = await api('POST', '/workouts', { notes: LONG });
for (const id of [bench.id, longEx.id]) {
  await api('POST', `/workouts/${done.id}/sets`, { exercise_id: id, reps: 8, weight_kg: 60, notes: LONG.slice(0, 120) });
}
await api('POST', `/workouts/${done.id}/finish`);
const live = await api('POST', '/workouts', { notes: LONG });
await api('POST', `/workouts/${live.id}/sets`, { exercise_id: longEx.id, reps: 8, weight_kg: 40 });
await api('POST', '/routines', { name: 'A Very Long Routine Name For Upper Body Strength Days', exercise_ids: [bench.id, longEx.id] });
await api('POST', '/metrics', { weight_kg: 80 });

const PAGES = ['/', '/workouts', `/workouts/${live.id}`, `/workouts/${done.id}`, '/exercises',
  `/exercises/${longEx.id}`, '/analytics', '/metrics', '/plan', '/routines/manage', '/cardio',
  '/journal', '/settings', '/challenges', '/achievements', '/templates'];

let failures = 0;
for (const path of PAGES) {
  await page.goto(BASE + path, { waitUntil: 'networkidle' });
  await page.waitForTimeout(300);
  const r = await page.evaluate(() => ({ inner: innerWidth, scroll: document.documentElement.scrollWidth }));
  const ok = r.inner === WIDTH && r.scroll <= WIDTH;
  if (!ok) failures++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${path}  (layout ${r.inner}px, content ${r.scroll}px)`);
}

await browser.close();
console.log(failures ? `\n${failures} pages wider than the screen` : '\nall pages fit');
process.exit(failures ? 1 : 0);
