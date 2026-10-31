// UI functionality and ease-of-use checks, written against what a person does,
// not against markup, so they keep holding through a redesign (the Test rig
// Home, 2026-10-30). Each check prints PASS/FAIL with the measured number.
//
// Functionality
//   F1 Login: a wrong password says so (role="alert"); the right one lands on Home.
//   F2 Every main page loads (200) and its nav link is marked current.
//   F3 Home -> live session, then log one set by tapping, then finish: the set
//      shows on the finished session with the weight and reps typed.
//   F4 The kg/lbs toggle changes every weight on the page and survives a reload.
// Ease of use
//   E1 Tap budget: Home to a live session in 1 tap; one set logged in
//      at most 5 (choose exercise, pick it, type weight, type reps, tick).
//   E2 The way into a session is visible without scrolling on a 360x640 phone.
//   E3 Touch targets: every visible button/link/input at least 44x44 px on a
//      phone; primary actions (.btn-primary, the log tick) at least 56 px tall.
//   E4 Readable text: no visible text under 13px (DESIGN.md).
//   E5 Contrast: visible text at least 4.5:1 against its background (3:1 at 24px+).
//   E6 Keyboard: Tab reaches the way into a session within 15 presses, and every
//      focused element shows a visible outline.
//   E7 Every input, select and textarea has a real label (a placeholder alone
//      doesn't count: it disappears as soon as you type).
//   E8 Reflow: at 320px wide (400% zoom of a 1280 screen) nothing scrolls sideways.
//   E9 Reduced motion: with prefers-reduced-motion no animation runs longer than 0.1s.
//   E10 Speed: each main page is ready (DOMContentLoaded) in under 1.5 s.
//   E11 Type scale: every main page and the sign-in page use only the five sizes in
//      DESIGN.md (13 / 15 / 18 / 22 / 30 px; 40 for the desktop headline; 16 for
//      text typed into a field, which iOS needs to avoid zooming).
//
//   scripts/e2e/serve.sh &
//   node scripts/e2e/ui_usability.test.mjs        # exit 1 if anything fails
// Needs Playwright (PLAYWRIGHT_DIR, default gstack's copy; falls back to a global install).
import { createRequire } from 'module';
let chromium;
try {
  const require = createRequire((process.env.PLAYWRIGHT_DIR ||
    `${process.env.HOME}/.claude/skills/gstack/node_modules`) + '/');
  ({ chromium } = require('playwright'));
} catch {
  ({ chromium } = createRequire(import.meta.url)('playwright'));
}

const BASE = process.env.E2E_BASE || 'http://127.0.0.1:8765';
const USER = process.env.E2E_USER || 'e2eadmin';
const PASS = process.env.E2E_PASS || 'e2e-admin-password-123';
const SCALE = ['13px', '15px', '18px', '22px', '30px'];
const PAGES = ['/', '/workouts', '/analytics', '/plan', '/exercises', '/journal', '/metrics', '/settings'];
const PHONE = { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true };

const browser = await chromium.launch();
const results = [];
function check(id, name, ok, detail = '') {
  results.push({ id, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${id} ${name}${detail ? '  ' + detail : ''}`);
}

// Log in once and reuse the cookie: the app rate-limits logins (5 per 15 min),
// so a fresh login per check would lock the suite out.
let STATE = null;
async function login(ctx) {
  const page = await ctx.newPage();
  if (STATE) { await page.goto(BASE + '/'); return page; }
  await page.goto(BASE + '/login');
  await page.fill('input[name=username]', USER);
  await page.fill('input[name=password]', PASS);
  await Promise.all([page.waitForURL(u => !u.pathname.startsWith('/login')), page.click('button[type=submit]')]);
  return page;
}
const _newContext = browser.newContext.bind(browser);
browser.newContext = opts => _newContext({ ...opts, ...(STATE ? { storageState: STATE } : {}) });

const api = (page, method, path, body) => page.evaluate(async ([m, p, b]) => {
  const r = await fetch(p, { method: m, headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                             body: b ? JSON.stringify(b) : undefined });
  try { return await r.json(); } catch { return null; }
}, [method, path, body]);

// Visible, interactive elements (what a person can actually tap).
const VISIBLE_INTERACTIVE = () => [...document.querySelectorAll(
    'a[href], button, input:not([type=hidden]), select, textarea, [role=button]')]
  .filter(el => {
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' &&
           !el.closest('[aria-hidden=true], [inert], .sr-only, .visually-hidden');
  });

// ── F1 login ────────────────────────────────────────────────────────────────
{
  const ctx = await browser.newContext(PHONE);
  const page = await ctx.newPage();
  await page.goto(BASE + '/login');
  await page.fill('input[name=username]', USER);
  await page.fill('input[name=password]', 'definitely-wrong');
  await page.click('button[type=submit]');
  const alert = await page.locator('[role=alert]').first().textContent({ timeout: 3000 }).catch(() => '');
  await page.fill('input[name=username]', USER);
  await page.fill('input[name=password]', PASS);
  await Promise.all([page.waitForLoadState('networkidle'), page.click('button[type=submit]')]);
  const landed = new URL(page.url()).pathname;
  check('F1', 'login: wrong password explained, right one lands on Home',
        !!alert.trim() && landed === '/', `alert="${alert.trim().slice(0, 40)}" landed=${landed}`);
  if (landed === '/') STATE = await ctx.storageState();
  await ctx.close();
}
if (!STATE) {   // F1 failed: log in once anyway so the other checks can run
  const ctx = await browser.newContext(PHONE);
  await login(ctx);
  STATE = await ctx.storageState();
  await ctx.close();
}

// Seed: a finished session with a set, so pages have weights and rows to show.
const seedCtx = await browser.newContext(PHONE);
const seedPage = await login(seedCtx);
const { exercises } = await api(seedPage, 'GET', '/api/exercises');
const bench = exercises.find(e => e.name === 'Bench Press') || exercises[0];
const done = await api(seedPage, 'POST', '/workouts', {});
await api(seedPage, 'POST', `/workouts/${done.id}/sets`, { exercise_id: bench.id, reps: 5, weight_kg: 60 });
await api(seedPage, 'POST', `/workouts/${done.id}/finish`);
await seedCtx.close();

// ── F2 pages load, nav marks the current page ───────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await login(ctx);
  const bad = [];
  for (const p of PAGES) {
    const r = await page.goto(BASE + p, { waitUntil: 'domcontentloaded' });
    if (!r || r.status() !== 200) { bad.push(`${p}:${r && r.status()}`); continue; }
    if (['/', '/workouts', '/analytics', '/plan', '/exercises'].includes(p)) {
      const marked = await page.evaluate(path => [...document.querySelectorAll('nav a[href]')].some(a =>
        new URL(a.href).pathname === path &&
        (a.getAttribute('aria-current') || a.classList.contains('active') || a.classList.contains('on'))), p);
      if (!marked) bad.push(`${p}:nav-not-marked`);
    }
  }
  check('F2', 'main pages load and nav marks the current one', bad.length === 0, bad.join(' '));
  await ctx.close();
}

// ── F3 + E1 the core loop by tapping, counting taps ─────────────────────────
{
  const ctx = await browser.newContext(PHONE);
  const page = await login(ctx);
  let taps = 0;
  const tap = async loc => { taps++; await loc.click(); };
  await page.goto(BASE + '/');
  // Into a session: follow whatever the page offers ("Start …"), up to 3 taps.
  for (let i = 0; i < 3 && !/\/workouts\/\d+/.test(page.url()); i++) {
    const go = page.locator('a, button').filter({ hasText: /^\s*(Start|Resume)\b/i }).first();
    await Promise.all([page.waitForLoadState('networkidle'), tap(go)]);
    await page.waitForTimeout(300);
  }
  const tapsToSession = taps;
  const inSession = /\/workouts\/\d+/.test(page.url());

  let tapsToLog = 0, logged = false, shown = false;
  if (inSession) {
    taps = 0;
    await tap(page.getByRole('button', { name: /choose exercise/i }).first());
    await page.locator('#ex-picker').waitFor({ state: 'visible' });
    await tap(page.locator('#ex-picker [role=option]')
      .filter({ hasText: bench.name }).first());
    const w = page.getByLabel(/weight/i).first(), r = page.getByLabel(/^reps/i).first();
    await tap(w); await w.fill('62.5');
    await tap(r); await r.fill('5');
    await tap(page.getByRole('button', { name: /log set|complete set/i }).first());
    tapsToLog = taps;
    await page.waitForTimeout(600);
    // SESSION_SETS is a top-level `let`, so it is a global binding but not on window.
    logged = await page.evaluate(() => (typeof SESSION_SETS !== 'undefined' ? SESSION_SETS : []).length > 0);
    const sessionUrl = page.url();
    await page.getByRole('button', { name: /^finish$/i }).first().click();
    await page.waitForTimeout(800);
    // Either a confirm sheet (finish anyway) or the summary modal (Done).
    const confirm = page.locator('.sheet [role=dialog] button, [role=dialog] button').filter({ hasText: /^\s*finish/i }).last();
    if (await confirm.isVisible().catch(() => false)) { await confirm.click(); await page.waitForTimeout(800); }
    const doneBtn = page.locator('#finish-modal button').filter({ hasText: /done/i });
    if (await doneBtn.isVisible().catch(() => false)) await doneBtn.click();
    await page.goto(sessionUrl, { waitUntil: 'networkidle' });
    const text = await page.locator('body').innerText();
    shown = /62[.,]5/.test(text) && /\b5\b/.test(text);
  }
  check('F3', 'start a session, log a set, finish: the set is saved and shown',
        inSession && logged && shown, `inSession=${inSession} logged=${logged} shownAfterFinish=${shown}`);
  check('E1', 'tap budget: Home→session 1 tap, one set ≤5',
        inSession && tapsToSession <= 1 && tapsToLog <= 5, `toSession=${tapsToSession} toLog=${tapsToLog}`);
  await ctx.close();
}

// ── F4 unit toggle ──────────────────────────────────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await login(ctx);
  await page.goto(BASE + '/workouts/' + done.id, { waitUntil: 'networkidle' });
  const before = await page.locator('body').innerText();
  await page.evaluate(() => window.toggleUnit && window.toggleUnit());
  await page.waitForTimeout(300);
  const after = await page.locator('body').innerText();
  await page.reload({ waitUntil: 'networkidle' });
  const reloaded = await page.locator('body').innerText();
  const ok = /\bkg\b/.test(before) && /\blbs?\b/.test(after) && /\blbs?\b/.test(reloaded);
  check('F4', 'kg/lbs toggle changes weights and survives a reload', ok,
        `after=${/\blbs?\b/.test(after)} reloaded=${/\blbs?\b/.test(reloaded)}`);
  await page.evaluate(() => window.toggleUnit && window.toggleUnit());   // back to kg
  await ctx.close();
}

// ── E2 above the fold on a small phone ──────────────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 360, height: 640 }, isMobile: true, hasTouch: true });
  const page = await login(ctx);
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  const box = await page.locator('a, button').filter({ hasText: /^\s*(Start|Resume)\b/i }).first().boundingBox();
  const tabbar = await page.evaluate(() => {
    // The fold is the top of a bottom-pinned tab bar, if there is one.
    const bars = [...document.querySelectorAll('nav, [role=navigation]')].filter(n => {
      const cs = getComputedStyle(n), r = n.getBoundingClientRect();
      return (cs.position === 'fixed' || cs.position === 'sticky') && r.height > 0 && r.top > innerHeight / 2;
    });
    return bars.length ? Math.min(...bars.map(n => n.getBoundingClientRect().top)) : innerHeight;
  });
  check('E2', 'the way into a session is visible without scrolling at 360x640',
        !!box && box.y + box.height <= tabbar, box ? `bottom=${Math.round(box.y + box.height)} fold=${Math.round(tabbar)}` : 'not found');
  await ctx.close();
}

// ── E3–E7, E9 per page on a phone ───────────────────────────────────────────
{
  const ctx = await browser.newContext(PHONE);
  const page = await login(ctx);
  const live = await api(page, 'POST', '/workouts', {});   // the session screen, live
  const pages = [...PAGES, `/workouts/${live.id}`];
  const fails = { E3: [], E4: [], E5: [], E7: [] };
  for (const p of pages) {
    await page.goto(BASE + p, { waitUntil: 'networkidle' });
    const r = await page.evaluate(VIS => {
      const visible = eval(VIS)();
      // E3: an inline link inside a sentence is exempt (WCAG 2.5.8 inline exception).
      const small = visible.filter(el => {
        const b = el.getBoundingClientRect();
        if (el.tagName === 'A' && getComputedStyle(el).display === 'inline') return false;
        return b.width < 44 || b.height < 44;
      }).map(el => `${el.tagName.toLowerCase()}"${(el.getAttribute('aria-label') || el.textContent).trim().slice(0, 16)}"${Math.round(el.getBoundingClientRect().width)}x${Math.round(el.getBoundingClientRect().height)}`);
      const primary = visible.filter(el => el.matches('.btn-primary, .log-btn, .start-btn') && el.getBoundingClientRect().height < 56)
        .map(el => `primary"${el.textContent.trim().slice(0, 16)}"h${Math.round(el.getBoundingClientRect().height)}`);
      // Text nodes: size and contrast.
      const lum = c => { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
      const rgb = s => (s.match(/[\d.]+/g) || []).map(Number);
      const L = ([r, g, b]) => 0.2126 * lum(r) + 0.7152 * lum(g) + 0.0722 * lum(b);
      function bgOf(el) {
        for (let e = el; e; e = e.parentElement) {
          const cs = getComputedStyle(e);
          if (cs.backgroundImage !== 'none' && !/gradient/.test(cs.backgroundImage)) return null;   // image: can't judge
          const c = rgb(cs.backgroundColor);
          if (c.length >= 3 && (c[3] === undefined || c[3] > 0.9)) return c;
        }
        return [255, 255, 255];
      }
      const tiny = [], lowc = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let n; (n = walker.nextNode());) {
        const el = n.parentElement;
        if (!n.textContent.trim() || !el || el.closest('script, style, svg, [aria-hidden=true], .sr-only, .visually-hidden')) continue;
        const b = el.getBoundingClientRect(), cs = getComputedStyle(el);
        if (!b.width || !b.height || cs.visibility === 'hidden' || +cs.opacity === 0) continue;
        const fs = parseFloat(cs.fontSize);
        if (fs < 13) tiny.push(`"${n.textContent.trim().slice(0, 16)}"${fs}px`);
        const bg = bgOf(el); if (!bg) continue;
        const fg = rgb(cs.color); if ((fg[3] ?? 1) < 1) continue;
        const [hi, lo] = [L(fg), L(bg)].sort((a, b) => b - a);
        const ratio = (hi + 0.05) / (lo + 0.05);
        const need = fs >= 24 || (fs >= 18.66 && +cs.fontWeight >= 700) ? 3 : 4.5;
        if (ratio < need && !el.closest('button:disabled, [disabled]')) lowc.push(`"${n.textContent.trim().slice(0, 16)}"${ratio.toFixed(2)}`);
      }
      const unnamed = [...document.querySelectorAll('input:not([type=hidden]), select, textarea')]
        .filter(el => el.offsetParent !== null || el.getClientRects().length)
        .filter(el => !(el.labels && el.labels.length) && !el.getAttribute('aria-label') &&
                      !el.getAttribute('aria-labelledby') && !el.getAttribute('title'))
        .map(el => el.id || el.name || el.type);
      return { small: [...new Set(small)], primary, tiny: [...new Set(tiny)], lowc: [...new Set(lowc)], unnamed };
    }, `(${VISIBLE_INTERACTIVE})`);
    if (r.small.length || r.primary.length) fails.E3.push(`${p}: ${[...r.primary, ...r.small].slice(0, 4).join(', ')}`);
    if (r.tiny.length) fails.E4.push(`${p}: ${r.tiny.slice(0, 3).join(', ')}`);
    if (r.lowc.length) fails.E5.push(`${p}: ${r.lowc.slice(0, 3).join(', ')}`);
    if (r.unnamed.length) fails.E7.push(`${p}: ${r.unnamed.slice(0, 3).join(', ')}`);
  }
  check('E3', 'touch targets ≥44px, primary actions ≥56px tall', !fails.E3.length, fails.E3.join(' | '));
  check('E4', 'no text under 13px', !fails.E4.length, fails.E4.join(' | '));
  check('E5', 'text contrast ≥4.5:1 (3:1 large)', !fails.E5.length, fails.E5.join(' | '));
  check('E7', 'every field has an accessible name', !fails.E7.length, fails.E7.join(' | '));
  await api(page, 'DELETE', `/workouts/${live.id}`);
  await ctx.close();
}

// ── E6 keyboard ─────────────────────────────────────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await login(ctx);
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  let reached = 0; const noOutline = [];
  for (let i = 1; i <= 40; i++) {
    await page.keyboard.press('Tab');
    const f = await page.evaluate(() => {
      const el = document.activeElement; if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const visible = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2) ||
                      (cs.boxShadow && cs.boxShadow !== 'none');
      return { text: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30), visible };
    });
    if (!f) continue;
    if (!f.visible) noOutline.push(f.text || '?');
    if (!reached && /^(Start|Resume)\b/i.test(f.text)) reached = i;
  }
  check('E6', 'keyboard: session start within 15 Tabs, focus always visible',
        reached > 0 && reached <= 15 && noOutline.length === 0,
        `startAtTab=${reached || 'never'} noVisibleFocus=${[...new Set(noOutline)].slice(0, 4).join(', ')}`);
  await ctx.close();
}

// ── E8 reflow at 320px ──────────────────────────────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 320, height: 640 }, isMobile: true, hasTouch: true });
  const page = await login(ctx);
  const wide = [];
  for (const p of PAGES) {
    await page.goto(BASE + p, { waitUntil: 'networkidle' });
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    if (over > 1) wide.push(`${p}+${over}px`);
  }
  check('E8', 'reflow: nothing scrolls sideways at 320px', !wide.length, wide.join(' '));
  await ctx.close();
}

// ── E9 reduced motion ───────────────────────────────────────────────────────
{
  const ctx = await browser.newContext({ ...PHONE, reducedMotion: 'reduce' });
  const page = await login(ctx);
  const long = [];
  for (const p of PAGES) {
    await page.goto(BASE + p, { waitUntil: 'networkidle' });
    const n = await page.evaluate(() => document.getAnimations()
      .filter(a => a.playState === 'running' && (a.effect?.getComputedTiming().duration || 0) > 100).length);
    if (n) long.push(`${p}:${n}`);
  }
  check('E9', 'reduced motion: no animation over 0.1s', !long.length, long.join(' '));
  await ctx.close();
}

// ── E10 speed ───────────────────────────────────────────────────────────────
{
  const ctx = await browser.newContext(PHONE);
  const page = await login(ctx);
  const slow = []; let worst = 0;
  for (const p of PAGES) {
    await page.goto(BASE + p, { waitUntil: 'domcontentloaded' });
    const ms = await page.evaluate(() => performance.getEntriesByType('navigation')[0].domContentLoadedEventEnd);
    worst = Math.max(worst, ms);
    if (ms > 1500) slow.push(`${p}:${Math.round(ms)}ms`);
  }
  check('E10', 'each main page ready in under 1.5 s', !slow.length, `worst=${Math.round(worst)}ms ${slow.join(' ')}`);
  await ctx.close();
}

// ── E11 type scale on the redesigned screens ────────────────────────────────
{
  const off = [];
  for (const [vp, extra] of [[PHONE, []], [{ viewport: { width: 1440, height: 900 } }, ['40px']]]) {
    const ctx = await browser.newContext(vp);
    const page = await login(ctx);
    const live = await api(page, 'POST', '/workouts', {});
    await api(page, 'POST', `/workouts/${live.id}/sets`, { exercise_id: bench.id, reps: 5, weight_kg: 60 });
    for (const p of ['/', `/workouts/${live.id}`, ...PAGES.slice(1), '/achievements', '/challenges', '/routines',
                     '/templates', '/cardio', '/export', '/feedback', `/exercises/${bench.id}`]) {
      await page.goto(BASE + p, { waitUntil: 'networkidle' });
      const bad = await page.evaluate(([ok]) => {
        const out = new Set();
        for (const el of document.querySelectorAll('body *')) {
          if (!el.getClientRects().length || el.closest('svg, [aria-hidden=true], .sr-only')) continue;
          if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
          const fs = getComputedStyle(el).fontSize;
          const typed = el.matches('input, select, textarea') && fs === '16px';
          if (!ok.includes(fs) && !typed) out.add(`${fs} "${el.textContent.trim().slice(0, 14)}"`);
        }
        return [...out];
      }, [[...SCALE, ...extra]]);
      if (bad.length) off.push(`${p}@${vp.viewport.width}: ${bad.slice(0, 3).join(', ')}`);
    }
    await api(page, 'DELETE', `/workouts/${live.id}`);
    await ctx.close();
  }
  // The sign-in page is standalone (its own stylesheet): check it signed out.
  const out = await _newContext(PHONE);
  const lp = await out.newPage();
  await lp.goto(BASE + '/login', { waitUntil: 'networkidle' });
  const lbad = await lp.evaluate(ok => [...document.querySelectorAll('body *')]
    .filter(el => el.getClientRects().length && [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()))
    .map(el => [getComputedStyle(el).fontSize, el])
    .filter(([fs, el]) => !ok.includes(fs) && !(el.matches('input, select, textarea') && fs === '16px'))
    .map(([fs, el]) => `${fs} "${el.textContent.trim().slice(0, 14)}"`), SCALE);
  if (lbad.length) off.push(`/login: ${[...new Set(lbad)].slice(0, 3).join(', ')}`);
  await out.close();
  check('E11', 'type scale: every main page uses only the five sizes', !off.length, off.join(' | '));
}

await browser.close();
const failed = results.filter(r => !r.ok).map(r => r.id);
console.log(`\n${failed.length ? 'FAIL' : 'PASS'} ${results.length - failed.length}/${results.length}${failed.length ? ': ' + failed.join(' ') : ''}`);
process.exit(failed.length ? 1 : 0);
