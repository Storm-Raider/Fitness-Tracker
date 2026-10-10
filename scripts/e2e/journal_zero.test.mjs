// TODO-EL-6: a past Daily Log entry with 0 steps must load as 0 and survive Save.
//   scripts/e2e/serve.sh &
//   node scripts/e2e/journal_zero.test.mjs
import { createRequire } from 'module';
const require = createRequire((process.env.PLAYWRIGHT_DIR ||
  `${process.env.HOME}/.claude/skills/gstack/node_modules`) + '/');
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8765';
let failures = 0;
const check = (name, ok, detail = '') => { if (!ok) failures++; console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`); };

const browser = await chromium.launch();
const page = await (await browser.newContext({ serviceWorkers: 'block' })).newPage();
await page.goto(BASE + '/login');
await page.fill('input[name=username]', 'e2eadmin');
await page.fill('input[name=password]', 'e2e-admin-password-123');
await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);

const day = new Date(Date.now() - 3 * 86400000).toISOString().slice(0, 10);
await page.evaluate(async d => {
  await fetch('/journal', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ log_date: d, steps: 0, water_l: 0, sleep_hrs: 7, workout: 'Rest day' }) });
}, day);

await page.goto(BASE + '/journal', { waitUntil: 'networkidle' });
await page.fill('#j-date', day);
await page.dispatchEvent('#j-date', 'change');
await page.waitForFunction(() => document.getElementById('j-workout').value === 'Rest day', null, { timeout: 5000 }).catch(() => {});

const steps = await page.inputValue('#j-steps');
const water = await page.inputValue('#j-water');
check('0 steps loads as 0', steps === '0', JSON.stringify(steps));
check('0 water loads as 0', water === '0', JSON.stringify(water));

await page.click('#save-btn');
await page.waitForTimeout(500);
const saved = await page.evaluate(async d => (await (await fetch('/journal/entry?date=' + d)).json()).entry, day);
check('Saving again keeps 0 steps', saved && saved.steps === 0, JSON.stringify(saved && saved.steps));
check('Saving again keeps 0 water', saved && saved.water_l === 0, JSON.stringify(saved && saved.water_l));

await browser.close();
console.log(failures ? `${failures} failed` : 'all passed');
process.exit(failures ? 1 : 0);
