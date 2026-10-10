// TODO-CC-7: drag the chat sheet down by its grab handle to dismiss it.
//   scripts/e2e/serve.sh &
//   node scripts/e2e/sheet_drag.test.mjs
// Touch is emulated with pointer events from a touchscreen-like context.
import { createRequire } from 'module';
const require = createRequire((process.env.PLAYWRIGHT_DIR ||
  `${process.env.HOME}/.claude/skills/gstack/node_modules`) + '/');
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:8765';
let failures = 0;
const check = (name, ok, detail = '') => { if (!ok) failures++; console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`); };

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, serviceWorkers: 'block' })).newPage();
await page.goto(BASE + '/login');
await page.fill('input[name=username]', 'e2eadmin');
await page.fill('input[name=password]', 'e2e-admin-password-123');
await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
await page.goto(BASE + '/plan', { waitUntil: 'networkidle' });
// sheet.js is only included with the chat (needs a Gemini key); load the same file.
if (!(await page.evaluate(() => !!window.Sheet))) await page.addScriptTag({ url: BASE + '/static/sheet.js' });

// A stand-in for the chat panel: a scrollable transcript with a Close button.
async function openSheet() {
  await page.evaluate(() => {
    document.getElementById('test-sheet')?.remove();
    const host = document.createElement('div');
    host.id = 'test-sheet';
    host.style.display = 'flex'; host.style.flexDirection = 'column';
    const log = document.createElement('div');
    log.id = 'test-log'; log.style.overflowY = 'auto'; log.style.flex = '1';
    for (let i = 0; i < 60; i++) { const p = document.createElement('p'); p.textContent = 'message ' + i; log.appendChild(p); }
    const close = document.createElement('button'); close.textContent = 'Close';
    host.appendChild(log); host.appendChild(close);
    document.querySelector('main').appendChild(host);
    window.__closedBy = null;
    window.__h = Sheet.open(host, { label: 'Test sheet', onClose: r => { window.__closedBy = r; } });
  });
  await page.waitForTimeout(300);
}

async function drag(selector, dy, ms = 300, fromMiddle = false, steps = 10) {
  const box = await page.locator(selector).boundingBox();
  const x = box.x + box.width / 2, y = box.y + (fromMiddle ? box.height / 2 : Math.min(box.height / 2, 10));
  await page.mouse.move(x, y);
  await page.mouse.down();
  for (let i = 1; i <= steps; i++) { await page.mouse.move(x, y + (dy * i) / steps); await page.waitForTimeout(ms / steps); }
  await page.mouse.up();
  await page.waitForTimeout(350);
}

await openSheet();
const handle = await page.evaluate(() => {
  const h = document.querySelector('#test-sheet .sheet-handle');
  if (!h) return null;
  const r = h.getBoundingClientRect();
  const hit = Math.max(r.height, parseFloat(getComputedStyle(h, '::after').height) || 0);   // the ::after extends the touch area
  return { height: Math.round(hit), hidden: h.getAttribute('aria-hidden'), first: h === document.getElementById('test-sheet').firstElementChild };
});
check('An open sheet has a grab handle at the top', handle && handle.first, JSON.stringify(handle));
check('The handle is a 44px touch target', handle && handle.height >= 44, JSON.stringify(handle));
check('The handle is hidden from screen readers (Close/Escape remain)', handle && handle.hidden === 'true');

const sheetH = await page.evaluate(() => document.getElementById('test-sheet').getBoundingClientRect().height);
await drag('#test-sheet .sheet-handle', 30);
let st = await page.evaluate(() => ({ open: Sheet.isOpen(), transform: document.getElementById('test-sheet').style.transform }));
check('A short drag snaps back', st.open && (!st.transform || st.transform === 'translateY(0px)' || st.transform === 'none'), JSON.stringify(st));

await drag('#test-log', sheetH * 0.3, 300, true);   // a scroll gesture in the conversation
st = await page.evaluate(() => ({ open: Sheet.isOpen(), by: window.__closedBy }));
check('Dragging the transcript scrolls, it never dismisses', st.open, JSON.stringify(st));

await drag('#test-sheet .sheet-handle', sheetH * 0.4);
st = await page.evaluate(() => ({ open: Sheet.isOpen(), by: window.__closedBy, handleLeft: !!document.querySelector('#test-sheet .sheet-handle') }));
check('Dragging the handle past a quarter of the height dismisses', !st.open && st.by === 'drag', JSON.stringify(st));
await page.waitForTimeout(300);
st = await page.evaluate(() => ({ handleLeft: !!document.querySelector('#test-sheet .sheet-handle'), transform: document.getElementById('test-sheet').style.transform }));
check('Closing removes the handle and the drag offset', !st.handleLeft && !st.transform, JSON.stringify(st));

await openSheet();
// Playwright moves the pointer at most every ~16 ms, so a flick is a few big steps
// (about 70 px in 50 ms, ~1.4 px/ms; a thumb flick is ~1–3 px/ms).
await drag('#test-sheet .sheet-handle', 70, 0, false, 3);
st = await page.evaluate(() => ({ open: Sheet.isOpen(), by: window.__closedBy }));
check('A quick flick down dismisses', !st.open && st.by === 'drag', JSON.stringify(st));

await openSheet();
await page.keyboard.press('Escape');
await page.waitForTimeout(300);
check('Escape still closes it', !(await page.evaluate(() => Sheet.isOpen())));

await browser.close();
console.log(failures ? `${failures} failed` : 'all passed');
process.exit(failures ? 1 : 0);
