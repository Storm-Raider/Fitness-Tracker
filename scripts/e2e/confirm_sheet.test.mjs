// TODO-CC-8: the delete-confirm sheet is a real modal dialog for keyboard and
// screen-reader users, and every delete flow through it still works.
//   scripts/e2e/serve.sh &
//   node scripts/e2e/confirm_sheet.test.mjs
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
const api = (m, p, b) => page.evaluate(async ([m, p, b]) => {
  const r = await fetch(p, { method: m, headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: b ? JSON.stringify(b) : undefined });
  try { return await r.json(); } catch { return null; }
}, [m, p, b]);

const { exercises } = await api('GET', '/api/exercises');
const bench = exercises.find(e => e.name === 'Bench Press').id;
const w = await api('POST', '/workouts', {});
await api('POST', `/workouts/${w.id}/sets`, { exercise_id: bench, reps: 5, weight_kg: 60 });
await api('POST', `/workouts/${w.id}/sets`, { exercise_id: bench, reps: 5, weight_kg: 62.5 });
await page.goto(`${BASE}/workouts/${w.id}`, { waitUntil: 'networkidle' });

// Count keydown listeners added/removed on document, to catch the Escape leak.
await page.evaluate(() => {
  window.__kd = 0;
  const add = document.addEventListener.bind(document), rem = document.removeEventListener.bind(document);
  document.addEventListener = (t, f, o) => { if (t === 'keydown') window.__kd++; return add(t, f, o); };
  document.removeEventListener = (t, f, o) => { if (t === 'keydown') window.__kd--; return rem(t, f, o); };
});

// ── Open from a set's delete button ──────────────────────────────────────
const del = page.locator('.set-del').first();
await del.focus();
await del.press('Enter');
await page.waitForSelector('#confirm-sheet');
const dlg = await page.evaluate(() => {
  const s = document.getElementById('confirm-sheet');
  const lbl = s.getAttribute('aria-labelledby');
  return { role: s.getAttribute('role'), modal: s.getAttribute('aria-modal'),
           label: lbl && document.getElementById(lbl)?.textContent,
           focus: document.activeElement?.textContent?.trim(),
           mainInert: document.querySelector('main')?.inert === true };
});
check('Announced as a modal alert dialog', dlg.role === 'alertdialog' && dlg.modal === 'true', JSON.stringify(dlg));
check('Labelled by its question', /Remove this set/.test(dlg.label || ''), JSON.stringify(dlg.label));
check('Focus starts on Cancel (the safe choice)', dlg.focus === 'Cancel', JSON.stringify(dlg.focus));
check('The page behind is inert', dlg.mainInert);

for (let i = 0; i < 3; i++) await page.keyboard.press('Tab');
const inside = await page.evaluate(() => document.getElementById('confirm-sheet').contains(document.activeElement));
check('Tab stays inside the dialog', inside);

await page.keyboard.press('Escape');
await page.waitForTimeout(350);
const after = await page.evaluate(() => ({
  open: !!document.getElementById('confirm-sheet'),
  focusIsDelete: document.activeElement?.classList.contains('set-del'),
  mainInert: document.querySelector('main')?.inert === true,
  sets: document.querySelectorAll('.set-row[id^="set-"]').length,
  listeners: window.__kd,
}));
check('Escape closes it and nothing is deleted', !after.open && after.sets === 2, JSON.stringify(after));
check('Focus returns to the delete button', after.focusIsDelete);
check('The page is usable again', !after.mainInert);
check('No keydown listener left behind', after.listeners === 0, `net listeners ${after.listeners}`);

// Closing with Cancel (not Escape) used to leave its Escape listener behind.
await page.locator('.set-del').first().click();
await page.waitForSelector('#confirm-sheet');
await page.click('.confirm-sheet-cancel');
await page.waitForTimeout(350);
const leak = await page.evaluate(() => window.__kd);
check('Cancel leaves no keydown listener behind', leak === 0, `net listeners ${leak}`);

// ── Confirm still deletes ────────────────────────────────────────────────
await page.locator('.set-del').first().click();
await page.click('.confirm-sheet-confirm');
await page.waitForTimeout(600);
const left = await page.evaluate(() => document.querySelectorAll('.set-row[id^="set-"]').length);
check('Confirm deletes the set', left === 1, `${left} left`);

// ── hx-confirm (HTMX) path: deleting a workout template ──────────────────
const tpl = await api('POST', '/templates', { name: 'A11y Template', workout_id: w.id });
await page.goto(BASE + '/templates', { waitUntil: 'networkidle' });
const before = await page.locator('text=A11y Template').count();
await page.locator('[hx-confirm]').first().click();
await page.waitForSelector('#confirm-sheet');
await page.click('.confirm-sheet-confirm');
await page.waitForTimeout(800);
const gone = await page.locator('text=A11y Template').count();
check('hx-confirm deletes through the sheet', before >= 1 && gone === 0, `${before} → ${gone}`);

// ── On top of a sheet.js sheet (the coach chat's "Forget this note?") ─────
await page.goto(BASE + '/plan', { waitUntil: 'networkidle' });
// sheet.js is only included with the chat (needs a Gemini key); load the same file.
if (!(await page.evaluate(() => !!window.Sheet))) await page.addScriptTag({ url: BASE + '/static/sheet.js' });
const stack = await page.evaluate(async () => {
  const host = document.createElement('div');
  host.id = 'test-sheet';
  const btn = document.createElement('button'); btn.textContent = 'Forget'; host.appendChild(btn);
  document.querySelector('main').appendChild(host);
  let closedBy = null;
  Sheet.open(host, { label: 'Test sheet', onClose: r => { closedBy = r; } });
  btn.focus();
  showConfirm('Forget this note?', () => {}, { confirmLabel: 'Forget' });
  const esc = () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  esc();
  await new Promise(r => setTimeout(r, 300));
  const afterFirst = { confirm: !!document.getElementById('confirm-sheet'), sheet: Sheet.isOpen(), focusInSheet: host.contains(document.activeElement) };
  esc();
  await new Promise(r => setTimeout(r, 300));
  return { afterFirst, sheetAfterSecond: Sheet.isOpen(), closedBy };
});
check('Over a sheet: Escape closes only the confirm', !stack.afterFirst.confirm && stack.afterFirst.sheet, JSON.stringify(stack.afterFirst));
check('Over a sheet: focus returns into the sheet', stack.afterFirst.focusInSheet);
check('Over a sheet: a second Escape closes the sheet', !stack.sheetAfterSecond && stack.closedBy === 'escape', JSON.stringify(stack));

await browser.close();
console.log(failures ? `${failures} failed` : 'all passed');
process.exit(failures ? 1 : 0);
