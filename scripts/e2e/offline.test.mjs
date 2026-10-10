// Browser checks for offline-safe logging (Chromium via Playwright).
//   scripts/e2e/serve.sh &            # throwaway server on :8765
//   node scripts/e2e/offline.test.mjs
// Needs Node and Playwright; set PLAYWRIGHT_DIR to the node_modules that holds
// it (default: gstack's copy). The last section stops the server on purpose.
import { createRequire } from 'module';
const require = createRequire((process.env.PLAYWRIGHT_DIR ||
  `${process.env.HOME}/.claude/skills/gstack/node_modules`) + '/');
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8765';
const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ hasTouch: true, viewport: { width: 390, height: 844 } });
const page = await ctx.newPage();
page.on('dialog', d => d.dismiss());

// Login (also registers the service worker)
await page.goto(BASE + '/login');
await page.fill('input[name=username]', 'e2eadmin');
await page.fill('input[name=password]', 'e2e-admin-password-123');
await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);

async function api(method, path, body) {
  return page.evaluate(async ([m, p, b]) => {
    const r = await fetch(p, { method: m, headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                               body: b ? JSON.stringify(b) : undefined });
    return r.json();
  }, [method, path, body]);
}
const exercises = (await api('GET', '/api/exercises')).exercises;
const bench = exercises.find(e => e.name === 'Bench Press') || exercises.find(e => e.category !== 'Cardio');

async function openWorkout() {
  const w = await api('POST', '/workouts', { notes: null });
  await page.goto(`${BASE}/workouts/${w.id}`, { waitUntil: 'networkidle' });
  return w.id;
}
async function chooseBench() {
  await page.evaluate(id => {
    document.getElementById('exercise-input').value = 'Bench Press';
    document.getElementById('exercise-id').value = id;
  }, bench.id);
}
const busy = () => page.evaluate(() => !!document.querySelector('.log-btn').dataset.busy);
const text = sel => page.evaluate(s => { const el = document.querySelector(s); return el && getComputedStyle(el).display !== 'none' ? el.textContent : ''; }, sel);

// ── 1. Log Set while offline ─────────────────────────────────────────────
await openWorkout();
await chooseBench();
await ctx.setOffline(true);
await page.click('.log-btn');
await page.waitForTimeout(400);
const err1 = await text('#log-error');
check('Log Set offline shows a connection error', /connection/i.test(err1), JSON.stringify(err1));
check('Log Set offline re-enables the button', !(await busy()));
await ctx.setOffline(false);
await page.click('.log-btn');
await page.waitForSelector('.set-row[id^="set-"]', { timeout: 3000 }).catch(() => {});
check('Log Set works again once back online', (await page.locator('.set-row[id^="set-"]').count()) === 1);

// ── 2. Session notes save while offline ──────────────────────────────────
await page.fill('#session-notes-input', 'felt strong');
await ctx.setOffline(true);
await page.evaluate(() => saveNotes().catch(() => {}));
await page.waitForTimeout(200);
const ind = await text('#notes-save-indicator');
check('Notes offline do not claim "Saved"', ind.trim() !== 'Saved' && /not saved/i.test(ind), JSON.stringify(ind));

// ── 3. Finish while offline ──────────────────────────────────────────────
await page.evaluate(() => finishWorkout().catch(() => {}));
await page.waitForTimeout(400);
const err3 = await text('#log-error');
const modal = await page.evaluate(() => getComputedStyle(document.getElementById('finish-modal')).display);
check('Finish offline shows a connection error', /connection/i.test(err3) && modal === 'none', JSON.stringify(err3));
await ctx.setOffline(false);

// ── 4. Log Cardio while offline ──────────────────────────────────────────
const cardioOk = await page.evaluate(() => {
  const sel = document.getElementById('cardio-exercise-select');
  const opt = [...sel.options].find(o => o.value);
  if (opt) sel.value = opt.value;
  return !!opt;
});
check('Cardio select has options', cardioOk);
await ctx.setOffline(true);
await page.evaluate(() => logCardio().catch(() => {}));
await page.waitForTimeout(300);
const err4 = await text('#cardio-error');
check('Log Cardio offline shows a connection error', /connection/i.test(err4), JSON.stringify(err4));
await ctx.setOffline(false);

// ── 5. Swipe-to-delete then Cancel keeps the set visible ─────────────────
const wS = await api('POST', '/workouts', { notes: null });
await api('POST', `/workouts/${wS.id}/sets`, { exercise_id: bench.id, reps: 5, weight_kg: 60 });
await page.goto(`${BASE}/workouts/${wS.id}`, { waitUntil: 'networkidle' });
const row = page.locator('.set-row[id^="set-"]').first();
const box = await row.boundingBox();
await page.evaluate(([x, y]) => {
  const el = document.querySelector('.set-row[id^="set-"]');
  const t = (cx) => new Touch({ identifier: 1, target: el, clientX: cx, clientY: y });
  el.dispatchEvent(new TouchEvent('touchstart', { touches: [t(x)], bubbles: true, cancelable: true }));
  for (let i = 1; i <= 10; i++) el.dispatchEvent(new TouchEvent('touchmove', { touches: [t(x - i * 12)], bubbles: true, cancelable: true }));
  el.dispatchEvent(new TouchEvent('touchend', { touches: [], bubbles: true, cancelable: true }));
}, [box.x + box.width - 20, box.y + box.height / 2]);
await page.waitForSelector('.confirm-sheet-cancel', { timeout: 2000 });
await page.click('.confirm-sheet-cancel');
await page.waitForTimeout(500);
const rowStyle = await page.evaluate(() => {
  const el = document.querySelector('.set-row[id^="set-"]');
  return { opacity: getComputedStyle(el).opacity, transform: el.style.transform };
});
check('Cancelled swipe leaves the set visible', rowStyle.opacity === '1' && !rowStyle.transform.includes('-100%'), JSON.stringify(rowStyle));

// ── 6. Rest timer survives the page being frozen (phone locked) ──────────
const page2 = await ctx.newPage();
await page2.clock.install();
const w2 = (await api('POST', '/workouts', { notes: null })).id;
await page2.goto(`${BASE}/workouts/${w2}`, { waitUntil: 'networkidle' });
await page2.evaluate(() => localStorage.setItem('zenkai_rest_s', '120'));
await page2.evaluate(() => startTimer());
const t0 = await page2.clock.runFor(1000).then(() => page2.textContent('#timer-display'));
// Jump the wall clock 60 s without firing timers: what a locked iPhone does.
await page2.evaluate(() => {});
await page2.clock.setSystemTime(Date.now() + 0); // no-op guard for API presence
const before = await page2.evaluate(() => Date.now());
await page2.clock.setSystemTime(before + 60_000);
await page2.clock.runFor(1000);
const t1 = await page2.textContent('#timer-display');
check('Rest timer counts real time after a freeze', t1 === '0:58' || t1 === '0:59', `${t0} -> ${t1}`);
await page2.close();

// ── 7. Numeric keypads ───────────────────────────────────────────────────
const modes = await page.evaluate(() => ['weight-input', 'reps-input', 'cardio-duration', 'cardio-distance']
  .map(id => document.getElementById(id)?.getAttribute('inputmode')));
check('Numeric fields set inputmode', modes[0] === 'decimal' && modes[1] === 'numeric' && modes[2] === 'decimal' && modes[3] === 'decimal', JSON.stringify(modes));

// ── 8. Start Session while offline ───────────────────────────────────────
await page.goto(BASE + '/workouts', { waitUntil: 'networkidle' });
await ctx.setOffline(true);
const startBtn = await page.evaluate(async () => {
  const b = document.createElement('button'); b.textContent = 'Start Session'; document.body.appendChild(b);
  await _doStartSession(b).catch(() => {});
  return { disabled: b.disabled, text: b.textContent.trim() };
});
check('Start Session offline recovers the button', !startBtn.disabled && startBtn.text === 'Start Session', JSON.stringify(startBtn));
await ctx.setOffline(false);

// ── 9a. The login page clears saved pages (next person can't see them) ───
await page.goto(BASE + '/workouts', { waitUntil: 'networkidle' });
await page.evaluate(() => navigator.serviceWorker.ready);
await page.reload({ waitUntil: 'networkidle' });
const savedBefore = await page.evaluate(async () => (await (await caches.open('zenkai-pages-v1')).keys()).length);
await page.goto(BASE + '/login', { waitUntil: 'networkidle' });
const savedAfter = await page.evaluate(async () => (await caches.keys()).includes('zenkai-pages-v1')
  ? (await (await caches.open('zenkai-pages-v1')).keys()).length : 0);
check('Showing the login page clears saved pages', savedBefore > 0 && savedAfter === 0, `${savedBefore} -> ${savedAfter}`);
await page.fill('input[name=username]', 'e2eadmin');
await page.fill('input[name=password]', 'e2e-admin-password-123');
await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);

// ── 9. Service worker: visited pages open offline, others get a fallback ─
await page.goto(BASE + '/workouts', { waitUntil: 'networkidle' });
await page.evaluate(() => navigator.serviceWorker.ready);
await page.reload({ waitUntil: 'networkidle' });          // now controlled + cached
// Stop the server: Playwright's offline switch doesn't reliably reach the
// service worker's own fetches, and "Pi unreachable" is the real case anyway.
const { execSync } = require('child_process');
execSync('fuser -k 8765/tcp || true');
await new Promise(r => setTimeout(r, 500));
let visited = '', fallback = '';
try { await page.reload(); visited = await page.title(); } catch (e) { visited = 'ERR ' + e.message.split('\n')[0]; }
check('Visited page opens offline', /Zenkai/i.test(visited), visited);
try { await page.goto(BASE + '/achievements'); fallback = await page.textContent('body'); } catch (e) { fallback = 'ERR ' + e.message.split('\n')[0]; }
check('Unvisited page offline shows the offline fallback', /offline/i.test(fallback) && !/ERR/.test(fallback), fallback.slice(0, 80));
await ctx.setOffline(false);

await browser.close();
const failed = results.filter(r => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
