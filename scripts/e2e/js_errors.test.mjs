// No page may throw. An uncaught error in a page's inline script silently skips
// everything after it in that script (2026-10-12: an unsaved coach draft made
// the Plan page throw before the Mesocycle planner was set up, and nothing
// showed). Visits every page at phone and desktop width, first on an empty
// account and then with data (a finished and a live session, a routine, a
// challenge, a daily log, cardio, body metrics), presses each in-page toggle,
// and fails on any uncaught error or console error.
//   scripts/e2e/serve.sh &
//   node scripts/e2e/js_errors.test.mjs
// E2E_DB=<the server's e2e.db> also seeds an unsaved coach draft (the state
// that broke the Plan page). Needs Playwright (PLAYWRIGHT_DIR, default gstack's copy).
import { createRequire } from 'module';
const require = createRequire((process.env.PLAYWRIGHT_DIR ||
  `${process.env.HOME}/.claude/skills/gstack/node_modules`) + '/');
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8765';
const browser = await chromium.launch();
const errors = [];

async function session(viewport, mobile) {
  const ctx = await browser.newContext({ viewport, isMobile: mobile, hasTouch: mobile });
  const page = await ctx.newPage();
  page.on('pageerror', e => errors.push({ where: page.__where, msg: 'uncaught: ' + e.message }));
  page.on('console', m => {
    // A resource the browser couldn't fetch (e.g. no network for a CDN) is not a script error.
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) {
      errors.push({ where: page.__where, msg: 'console: ' + m.text() });
    }
  });
  page.__where = 'login';
  await page.goto(BASE + '/login');
  await page.fill('input[name=username]', 'e2eadmin');
  await page.fill('input[name=password]', 'e2e-admin-password-123');
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
  return { ctx, page };
}

const PHONE = await session({ width: 390, height: 844 }, true);
const DESK = await session({ width: 1280, height: 900 }, false);

const api = (method, path, body) => PHONE.page.evaluate(async ([m, p, b]) => {
  const r = await fetch(p, { method: m, headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                             body: b ? JSON.stringify(b) : undefined });
  try { return await r.json(); } catch { return null; }
}, [method, path, body]);

const PAGES = ['/', '/workouts', '/exercises', '/analytics', '/metrics', '/plan', '/routines/manage',
  '/cardio', '/journal', '/settings', '/challenges', '/achievements', '/templates', '/prs', '/stats'];

// Press each segmented toggle and tab, so their handlers run too. Navigation
// links are left alone; only buttons inside toggle groups are pressed.
async function pressToggles(page) {
  const buttons = page.locator('.seg-toggle button:visible, [role=tablist] [role=tab]:visible');
  const n = await buttons.count();
  for (let i = 0; i < n; i++) {
    await buttons.nth(i).click({ timeout: 2000 }).catch(() => {});
    await page.waitForTimeout(60);
  }
}

async function sweep(label, paths) {
  for (const { page } of [PHONE, DESK]) {
    for (const path of paths) {
      page.__where = `${label} ${page.viewportSize().width}px ${path}`;
      await page.goto(BASE + path, { waitUntil: 'networkidle' });
      await page.waitForTimeout(150);
      await pressToggles(page);
    }
  }
}

// 1. A brand-new account: every page with nothing in it.
await sweep('empty', PAGES);

// 2. With data.
const { exercises } = await api('GET', '/api/exercises');
const bench = exercises.find(e => e.name === 'Bench Press');
const squat = exercises.find(e => e.name === 'Squat') || exercises[1];
const done = await api('POST', '/workouts', { notes: 'Finished session' });
for (const id of [bench.id, squat.id]) {
  await api('POST', `/workouts/${done.id}/sets`, { exercise_id: id, reps: 8, weight_kg: 60 });
}
await api('POST', `/workouts/${done.id}/finish`);
const routine = await api('POST', '/routines', { name: 'Upper', exercise_ids: [bench.id, squat.id] });
await api('POST', '/metrics', { weight_kg: 80 });
const today = new Date().toLocaleDateString('en-CA');
await api('POST', '/journal', { log_date: today, energy: 'medium', sleep_hrs: 7.5, notes: 'Fine' });
const cardio = exercises.find(e => e.category === 'Cardio');
if (cardio) {
  await PHONE.page.evaluate(id => fetch('/cardio', { method: 'POST',
    body: new URLSearchParams({ exercise_id: id, duration_minutes: '30', distance_km: '5' }) }), cardio.id);
}
const attempt = await api('POST', '/challenges', { template_key: '75_hard' });
const live = await api('POST', '/workouts', { notes: 'Live session' });
await api('POST', `/workouts/${live.id}/sets`, { exercise_id: bench.id, reps: 5, weight_kg: 70 });

// An unsaved coach draft, when the test can reach the server's database.
if (process.env.E2E_DB) {
  const { DatabaseSync } = await import('node:sqlite');
  const db = new DatabaseSync(process.env.E2E_DB);
  const days = [{ focus: 'Upper', exercises: [bench, squat].map(e => ({
    exercise_id: e.id, name: e.name, sets: 3, reps: '8-10', rir: 2, rest_sec: 120, note: '', progression: '' })) }];
  db.prepare("INSERT INTO coach_plans(user_id, title, goal, days_per_week, plan_json, status) VALUES (1, 'Draft', 'general', 1, ?, 'draft')")
    .run(JSON.stringify({ title: 'Draft', summary: 'A draft.', goal: 'general', days_per_week: 1, days,
                          volume_note: 'One set fewer on accessory work for recovery, because of x.' }));
  db.close();
}

const detail = [`/workouts/${done.id}`, `/workouts/${live.id}`, `/exercises/${bench.id}`];
if (attempt?.id) detail.push(`/challenges/${attempt.id}`);
if (!routine?.id || !attempt?.id) console.log('note: routine or challenge seeding failed', routine, attempt);
await sweep('data', [...PAGES, ...detail]);

await browser.close();
const seen = new Set();
for (const e of errors) {
  const line = `FAIL  ${e.where}: ${e.msg}`;
  if (!seen.has(line)) { seen.add(line); console.log(line); }
}
console.log(errors.length ? `\n${seen.size} distinct page errors` : '\nno page errors');
process.exit(errors.length ? 1 : 0);
