// Issue #28: a plan generation started before the Plan page loaded is picked
// back up instead of the page showing its empty state.
//   scripts/e2e/serve.sh &
//   node scripts/e2e/coach_resume.test.mjs
// A real generation needs Gemini, so the browser is handed the two things the
// server would send: the Plan page with an in-flight job id, and that job's
// event stream (queued -> phase -> done). Everything else is the real app.
import { createRequire } from 'module';
const require = createRequire((process.env.PLAYWRIGHT_DIR ||
  `${process.env.HOME}/.claude/skills/gstack/node_modules`) + '/');
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8765';
const JOB = 'feedface';
const PLAN = {
  title: 'Resumed Plan', summary: 'From a job started on another page', goal: 'strength', days_per_week: 1,
  days: [{ focus: 'Full body', exercises: [{ name: 'Bench Press', sets: 3, reps: '5', note: '' }] }],
};
let failures = 0;
const check = (name, ok, detail = '') => { if (!ok) failures++; console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`); };

const browser = await chromium.launch();
// The offline service worker would fetch pages itself, out of reach of page.route().
const page = await (await browser.newContext({ serviceWorkers: 'block' })).newPage();
await page.goto(BASE + '/login');
await page.fill('input[name=username]', 'e2eadmin');
await page.fill('input[name=password]', 'e2e-admin-password-123');
await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);

// The page as the server renders it while this user has a job running.
await page.route(BASE + '/plan', async route => {
  const res = await route.fetch();
  const html = (await res.text()).replace('const _activeJobId = null;', `const _activeJobId = "${JOB}";`);
  await route.fulfill({ response: res, body: html });
});
let streamOpened = 0;
await page.route(`${BASE}/coach/stream/${JOB}`, async route => {
  streamOpened++;
  const ev = (type, data) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
  await new Promise(r => setTimeout(r, 300));   // let the progress state render first
  await route.fulfill({
    status: 200, headers: { 'Content-Type': 'text/event-stream' },
    body: ev('queued', { position: 1, ahead: 0 }) + ev('phase', { type: 'phase', message: 'Building your training profile…' })
        + ev('done', { type: 'done', plan: PLAN, dropped: [], model: 'test', draft_id: null }),
  });
});

await page.goto(BASE + '/plan');
const busyText = await page.textContent('#ai-generate-btn');
check('Returning to /plan shows the running generation', /Coaching|queue|Next up|profile/i.test(busyText), JSON.stringify(busyText.trim()));
await page.waitForSelector('#ai-plan-output :text("Bench Press")', { timeout: 5000 }).catch(() => {});
const out = await page.textContent('#ai-plan-output');
check('The finished plan appears without pressing Generate', /Bench Press/.test(out || ''), (out || '').slice(0, 60));
check('It re-attached to that job, once', streamOpened === 1, `stream opened ${streamOpened}×`);
check('The empty state is hidden', await page.evaluate(() => getComputedStyle(document.getElementById('ai-result-empty')).display === 'none'));

// With no job running, nothing is resumed.
await page.unroute(BASE + '/plan');
streamOpened = 0;
await page.goto(BASE + '/plan', { waitUntil: 'networkidle' });
check('No job: no stream is opened', streamOpened === 0);

await browser.close();
console.log(failures ? `${failures} failed` : 'all passed');
process.exit(failures ? 1 : 0);
