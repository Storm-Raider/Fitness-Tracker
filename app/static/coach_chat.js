/*
 * Coach chat panel on the Plan page (docs/designs/coach-chat.md, DS-1..DS-10, ER-9..ER-16).
 *
 * Talks to /coach/plans/{id}/chat, /undo, /coach/notes and /coach/chat/ack, and keeps the
 * plan on screen in step through PlanState (plan_view.js): it follows plan:changed /
 * plan:cleared / plan:busy and publishes every edit back through PlanState.set + PlanView.
 *
 * SAFETY: replies, notes, change summaries and exercise names come from a model or the
 * database, so the DOM is built with createElement + textContent only. Never use
 * HTML-string APIs in this file; tests/test_chat_js_safety.py fails the build if one appears.
 *
 * Layout: >= 768px the panel sits in the left column behind a Generate | Coach toggle;
 * below that it is a fixed one-row composer that opens as a bottom sheet (sheet.js). The
 * panel element is never moved, so the textarea keeps focus and text.
 */
(function (global) {
  'use strict';

  var MOBILE = global.matchMedia('(max-width: 767px)');
  var COARSE = global.matchMedia('(pointer: coarse)');
  var REDUCED = global.matchMedia('(prefers-reduced-motion: reduce)');
  var MAX_CHARS = 500;
  var HISTORY_LIMIT = 100;
  var STILL_WORKING_AFTER = 15;   // seconds
  var COMPOSER_H = '72px';        // mobile collapsed composer row incl. padding

  var panel, log, input, sendBtn, workingRow, workingText, errorBox, banner, notesToggle, notesList,
      notesCount, privacy, ackBtn, tabs, tabGenerate, tabCoach, genPanel;

  var st = {
    planId: null, canEdit: false, acked: false, enabled: true, atCap: false,
    messages: [], notes: [], noteCap: 20, hasUndo: false, undoLabel: null,
    notesOpen: false, loading: false, loadFailed: false, sending: false,
    sheet: null, tab: 'generate', generating: false, lastChanged: [], bannerKind: null,
    chips: [],          // pending note proposals shown after the last reply: [{text}]
  };

  /* ── tiny DOM helpers (text only) ─────────────────────────────── */

  function el(tag, className, text) {
    var n = document.createElement(tag);
    if (className) n.className = className;
    if (text != null) n.textContent = text;
    return n;
  }
  function button(text, className, onClick) {
    var b = el('button', className || 'btn btn-ghost', text);
    b.type = 'button';
    if (onClick) b.addEventListener('click', onClick);
    return b;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  async function api(method, url, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    var r;
    try {
      r = await fetch(url, opts);
    } catch (e) {
      return { ok: false, status: 0, data: { kind: 'network', detail: 'No connection. Your message is back in the box.' } };
    }
    var data = {};
    if (r.status !== 204) {
      try { data = await r.json(); } catch (e) { data = {}; }
    }
    return { ok: r.ok, status: r.status, data: data };
  }

  /* ── visibility: tabs, composer, sheet ────────────────────────── */

  function planOnScreen() {
    var s = PlanState.get();
    return !!(s.plan && s.planId);
  }

  function setComposerHeight() {
    // Toasts, the achievement rack and the page's bottom padding clear the fixed composer.
    var shown = MOBILE.matches && !panel.hidden;
    var h = shown && !st.sheet ? Math.ceil(panel.getBoundingClientRect().height) : 0;
    document.documentElement.style.setProperty('--composer-h', shown ? (h ? h + 'px' : COMPOSER_H) : '0px');
  }

  function applyVisibility() {
    var has = planOnScreen();
    if (tabs) tabs.hidden = !has;
    if (MOBILE.matches) {
      panel.hidden = !has;                 // the collapsed composer exists only with a plan on screen
      if (genPanel) genPanel.hidden = false;
    } else {
      var coach = has && st.tab === 'coach';
      panel.hidden = !coach;
      if (genPanel) genPanel.hidden = coach;
    }
    if (tabGenerate) tabGenerate.setAttribute('aria-pressed', String(st.tab !== 'coach'));
    if (tabCoach) {
      tabCoach.setAttribute('aria-pressed', String(st.tab === 'coach'));
      tabCoach.disabled = st.generating;
      tabCoach.title = st.generating ? 'Generating…' : '';
    }
    if (panel.hidden && st.sheet) st.sheet.close();
    setComposerHeight();
  }

  function showTab(name) {
    st.tab = name === 'coach' && !st.generating ? 'coach' : 'generate';
    applyVisibility();
  }

  function openSheet() {
    if (!MOBILE.matches || st.sheet || panel.hidden) return;
    st.sheet = Sheet.open(panel, {
      label: 'Coach chat',
      initialFocus: st.acked ? input : ackBtn,
      returnFocus: input,
      onClose: function () { st.sheet = null; },
    });
    scrollLogToEnd();
  }

  function scrollLogToEnd() { log.scrollTop = log.scrollHeight; }

  /* ── rendering ────────────────────────────────────────────────── */

  function localStamp() {
    var n = new Date();
    var p = function (v) { return String(v).padStart(2, '0'); };
    return n.getFullYear() + '-' + p(n.getMonth() + 1) + '-' + p(n.getDate()) + ' ' +
      p(n.getHours()) + ':' + p(n.getMinutes()) + ':' + p(n.getSeconds());
  }

  function dayLabel(iso) {
    var d = String(iso || '').slice(0, 10);
    if (d === localStamp().slice(0, 10)) return 'Today';
    var parsed = new Date(d + 'T00:00:00');
    if (isNaN(parsed)) return d;
    return parsed.toLocaleDateString('en-US', { weekday: 'short', day: 'numeric', month: 'short' });
  }

  function changesBox(changes, undone) {
    var box = el('div', 'cp-changes' + (undone ? ' undone' : ''));
    (changes || []).forEach(function (c) {
      box.appendChild(el('div', null, 'Day ' + c.day + ' · ' + c.text));
    });
    return box;
  }

  function entry(m) {
    var isModel = m.role === 'model';
    var wrap = el('div', 'cp-entry ' + (isModel ? 'coach' : 'you') + (m.pending ? ' pending' : ''));
    wrap.appendChild(el('span', 'cp-who cp-label', isModel ? 'Coach' : 'You'));
    wrap.appendChild(el('div', 'cp-body', m.content));
    if (isModel && m.changes && m.changes.length) wrap.appendChild(changesBox(m.changes, m.undone));
    return wrap;
  }

  function renderLog() {
    clear(log);
    if (!st.acked) { log.hidden = true; return; }
    log.hidden = false;
    if (st.loading) {
      var sk = el('div');
      for (var i = 0; i < 3; i++) sk.appendChild(el('div', 'cp-skel'));
      sk.style.cssText = 'display:flex;flex-direction:column;gap:.6rem;';
      log.appendChild(sk);
      return;
    }
    if (st.loadFailed) {
      var f = el('div', 'flash flash-error', "Couldn't load the conversation. ");
      f.appendChild(button('Retry', 'btn btn-ghost', function () { load(st.planId); }));
      log.appendChild(f);
      return;
    }
    if (st.messages.length >= HISTORY_LIMIT) log.appendChild(el('div', 'cp-partial', 'Showing the latest 100 messages.'));
    if (!st.messages.length) {
      log.appendChild(el('p', 'cp-empty', 'Ask for a change or a reason. I use your training log.'));
    }
    var lastDay = null;
    st.messages.forEach(function (m) {
      var day = String(m.created_at || '').slice(0, 10) || 'pending';
      if (day !== lastDay) {
        if (!m.pending || lastDay === null) log.appendChild(el('div', 'cp-divider cp-label', m.pending ? 'Today' : dayLabel(m.created_at)));
        lastDay = day;
      }
      log.appendChild(entry(m));
    });
    st.chips.forEach(function (chip) { log.appendChild(noteChip(chip)); });
    if (st.hasUndo && st.canEdit) {
      var row = el('div', 'cp-undo-row');
      var u = button('Undo', 'btn btn-ghost', function () { undo(false); });
      u.disabled = st.sending || PlanState.get().busy;
      row.appendChild(u);
      if (st.undoLabel) row.appendChild(el('span', 'cp-label', st.undoLabel));
      log.appendChild(row);
    }
    scrollLogToEnd();
  }

  function noteChip(chip) {
    var box = el('div', 'cp-chip' + (chip.saved ? ' saved' : ''));
    if (chip.saved) {
      box.appendChild(el('span', null, 'Saved'));
      return box;
    }
    if (chip.full) {
      box.appendChild(el('span', null, 'Notes are full (' + st.noteCap + '). Delete one in Coach notes to save this.'));
      return box;
    }
    box.appendChild(document.createTextNode('Remember: '));
    box.appendChild(el('q', null, chip.text));
    box.appendChild(document.createTextNode('?'));
    var actions = el('div', 'cp-chip-actions');
    var yes = button(chip.saving ? 'Saving…' : 'Yes, remember', 'btn btn-ghost', function () { confirmNote(chip); });
    yes.disabled = !!chip.saving;
    var no = button('No', 'btn btn-ghost', function () {
      st.chips = st.chips.filter(function (c) { return c !== chip; });
      renderLog();
    });
    actions.appendChild(yes);
    actions.appendChild(no);
    if (chip.error) box.appendChild(el('div', 'cp-label', "Couldn't save. Try again."));
    box.appendChild(actions);
    return box;
  }

  function renderNotes() {
    notesCount.textContent = String(st.notes.length);
    notesToggle.setAttribute('aria-expanded', String(st.notesOpen));
    notesList.hidden = !st.notesOpen;
    clear(notesList);
    if (!st.notes.length) {
      notesList.appendChild(el('div', 'cp-notes-empty',
        "Nothing remembered yet. When you tell me about an injury or a preference, I'll offer to remember it."));
      return;
    }
    if (st.notes.length >= st.noteCap) {
      notesList.appendChild(el('div', 'cp-notes-empty', 'Notes are full (' + st.noteCap + '). Delete one to save more.'));
    }
    st.notes.slice().reverse().forEach(function (n) {
      var row = el('div', 'cp-note');
      row.appendChild(el('span', 'cp-note-text', n.text));
      var del = button('×', 'cp-note-del', function () { deleteNote(n, del); });
      del.setAttribute('aria-label', 'Forget: ' + n.text);
      row.appendChild(del);
      notesList.appendChild(row);
    });
  }

  function renderBanner() {
    clear(banner);
    var kind = st.bannerKind;
    if (!kind && !st.canEdit && st.planId) kind = 'saved';
    if (!kind && st.atCap) kind = 'cap';
    banner.hidden = !kind;
    if (!kind) return;
    if (kind === 'saved') {
      banner.appendChild(el('span', null, 'This plan is saved. I can answer questions; edits go to drafts.'));
      banner.appendChild(button('Regenerate from this chat', 'btn btn-ghost', regenerateFromChat));
    } else if (kind === 'stale') {
      banner.appendChild(el('span', null, 'This plan changed in another tab.'));
      banner.appendChild(button('Reload', 'btn btn-ghost', function () { st.bannerKind = null; load(st.planId, true); }));
    } else if (kind === 'replaced') {
      banner.appendChild(el('span', null, 'This plan was replaced. Reload to see the new one.'));
      banner.appendChild(button('Reload', 'btn btn-ghost', function () { global.location.reload(); }));
    } else if (kind === 'cap') {
      banner.appendChild(el('span', null, 'Coach is resting until tomorrow. Undo and swaps still work.'));
    }
  }

  function renderComposer() {
    var busy = PlanState.get().busy;
    var blocked = st.atCap || !st.enabled || st.bannerKind === 'replaced';
    // readOnly, never disabled: a disabled control takes no taps (the mobile sheet with the
    // privacy card could not be opened) and disabling a focused textarea blurs it, which
    // drops the iOS keyboard in the middle of a conversation.
    input.readOnly = !st.acked || blocked || st.sending;
    sendBtn.disabled = st.acked && (blocked || st.sending || busy);   // before the ack, Send opens the card
    input.placeholder = !st.acked ? 'Acknowledge to start'
      : st.atCap ? 'Coach is resting until tomorrow'
      : 'Ask or change something';
    privacy.hidden = st.acked;
  }

  function renderAll() {
    renderBanner();
    renderNotes();
    renderLog();
    renderComposer();
  }

  function showError(text) {
    errorBox.textContent = text;
    errorBox.hidden = !text;
  }

  /* ── loading a plan's conversation ────────────────────────────── */

  function adopt(d) {
    st.planId = d.plan_id;
    st.canEdit = !!d.can_edit;
    st.acked = !!d.acked;
    st.enabled = d.enabled !== false;
    st.atCap = !!d.at_cap;
    st.messages = d.messages || [];
    st.notes = d.notes || [];
    st.noteCap = d.note_cap || 20;
    st.hasUndo = !!d.has_undo;
    st.undoLabel = d.undo_label || null;
    st.loadFailed = false;
  }

  async function load(planId, refreshPlan) {
    if (!planId) return;
    st.planId = planId;
    st.loading = true;
    st.chips = [];
    showError('');
    renderAll();
    var r = await api('GET', '/coach/plans/' + planId + '/chat');
    if (st.planId !== planId) return;                  // another plan took over meanwhile
    st.loading = false;
    if (!r.ok) {
      st.loadFailed = true;
      renderAll();
      return;
    }
    adopt(r.data);
    if (refreshPlan) {
      // In-place reload (stale tab): take the server's plan and rev; the typed text stays.
      PlanState.set({ plan: r.data.plan, rev: r.data.rev });
      PlanView.render(document.getElementById('ai-plan-output'), r.data.plan,
        { readonly: !st.canEdit });
    }
    renderAll();
  }

  /* ── sending a message ────────────────────────────────────────── */

  var workingTimer = null;
  function startWorking() {
    var t0 = Date.now();
    workingRow.hidden = false;
    workingText.textContent = 'working… 0s';
    workingTimer = setInterval(function () {
      var s = Math.floor((Date.now() - t0) / 1000);
      workingText.textContent = s > STILL_WORKING_AFTER ? 'still working… ' + s + 's' : 'working… ' + s + 's';
    }, 1000);
  }
  function stopWorking() {
    clearInterval(workingTimer);
    workingTimer = null;
    workingRow.hidden = true;
  }

  function editLabel(days) {
    return 'Edited ' + days.map(function (d) { return 'Day ' + d; }).join(', ');
  }

  async function send() {
    var text = input.value.trim();
    if (!text || st.sending || PlanState.get().busy || !st.acked || st.atCap) return;
    if (text.length > MAX_CHARS) { showError('Keep it under ' + MAX_CHARS + ' characters.'); return; }
    var s = PlanState.get();
    var planId = st.planId;
    showError('');
    st.sending = true;
    st.chips = [];
    var pending = { role: 'user', content: text, pending: true };
    st.messages.push(pending);
    input.value = '';
    autosize();
    PlanState.setBusy(true);
    renderLog();
    renderComposer();
    startWorking();

    var r = await api('POST', '/coach/plans/' + planId + '/chat',
      st.canEdit ? { message: text, base_rev: s.rev } : { message: text });

    stopWorking();
    st.sending = false;
    PlanState.setBusy(false);
    if (st.planId !== planId) return;                  // the plan on screen changed under us

    if (!r.ok) {
      st.messages = st.messages.filter(function (m) { return m !== pending; });
      if (!input.value) input.value = text;            // the message goes back in the box
      autosize();
      var kind = r.data.kind;
      if (kind === 'stale' || kind === 'replaced') st.bannerKind = kind;
      else if (kind === 'quota') st.atCap = true;
      else if (kind === 'ack_required') st.acked = false;
      else if (kind === 'disabled') { st.enabled = false; }
      showError(kind === 'stale' || kind === 'replaced' ? '' : (r.data.detail || 'The coach had a problem. Try again.'));
      renderAll();
      return;
    }

    var d = r.data;
    var now = localStamp();
    st.messages = st.messages.filter(function (m) { return m !== pending; });
    st.messages.push({ id: d.message_ids.user, role: 'user', content: text, created_at: now });
    st.messages.push({ id: d.message_ids.model, role: 'model', content: d.reply, created_at: now,
      changed_days: d.changed_days, changes: d.changes, undone: false });
    st.hasUndo = !!d.has_undo;
    st.undoLabel = d.undo_label || null;
    if (d.propose_note) st.chips.push(d.notes_full ? { text: d.propose_note, full: true } : { text: d.propose_note });

    if (d.changed_days && d.changed_days.length) {
      st.lastChanged = d.changed_days;
      PlanState.set({ plan: d.plan, rev: d.rev });
      var out = document.getElementById('ai-plan-output');
      PlanView.render(out, d.plan, { changedDays: d.changed_days });
      if (!MOBILE.matches) {
        var first = PlanView.dayNode(out, d.changed_days[0]);
        if (first) first.scrollIntoView({ behavior: REDUCED.matches ? 'auto' : 'smooth', block: 'nearest' });
      } else if (!st.sheet) {
        global.showActionToast(editLabel(d.changed_days), function () { return undo(true); });
      }
    } else if (typeof d.rev === 'number') {
      PlanState.set({ rev: d.rev });
    }
    renderAll();
  }

  /* ── undo ─────────────────────────────────────────────────────── */

  async function undo(fromToast) {
    var s = PlanState.get();
    if (!st.planId || s.busy || st.sending) return false;
    PlanState.setBusy(true);
    var r = await api('POST', '/coach/plans/' + st.planId + '/undo', { base_rev: s.rev });
    PlanState.setBusy(false);
    if (!r.ok) {
      var conflict = r.data.kind === 'stale' || r.data.kind === 'replaced';
      if (conflict) st.bannerKind = r.data.kind;           // the banner says it; no second message
      else if (!fromToast) showError(r.data.kind === 'corrupt' ? "Can't restore this edit." : (r.data.detail || 'Undo failed.'));
      renderAll();
      return false;
    }
    var d = r.data;
    st.hasUndo = !!d.has_undo;
    st.undoLabel = d.undo_label || null;
    st.messages.forEach(function (m) { if (m.id === d.undone_message_id) m.undone = true; });
    st.lastChanged = [];
    PlanState.set({ plan: d.plan, rev: d.rev });
    PlanView.render(document.getElementById('ai-plan-output'), d.plan, {});
    showError(d.notice || '');
    renderAll();
    return true;
  }

  /* ── notes ────────────────────────────────────────────────────── */

  async function confirmNote(chip) {
    chip.saving = true;
    chip.error = false;
    renderLog();
    var r = await api('POST', '/coach/notes', { text: chip.text, source_plan_id: st.planId });
    chip.saving = false;
    if (!r.ok) {
      if (r.data.kind === 'notes_full') chip.full = true;
      else chip.error = true;
      renderLog();
      return;
    }
    if (!st.notes.some(function (n) { return n.id === r.data.id; })) st.notes.push(r.data);
    chip.saved = true;
    st.notesOpen = true;                 // show what was just remembered (DS-10)
    renderNotes();
    renderLog();
    setTimeout(function () {
      st.chips = st.chips.filter(function (c) { return c !== chip; });
      renderLog();
    }, 3000);
  }

  function deleteNote(note, btn) {
    var go = async function () {
      btn.disabled = true;
      var r = await api('DELETE', '/coach/notes/' + note.id);
      if (!r.ok && r.status !== 404) {
        btn.disabled = false;
        showError("Couldn't delete the note. Try again.");
        return;
      }
      st.notes = st.notes.filter(function (n) { return n.id !== note.id; });
      renderNotes();
    };
    if (global.showConfirm) global.showConfirm('Forget this note?', go, { confirmLabel: 'Forget' });
    else go();
  }

  /* ── privacy acknowledgement ──────────────────────────────────── */

  async function acknowledge() {
    ackBtn.disabled = true;
    var r = await api('POST', '/coach/chat/ack');
    ackBtn.disabled = false;
    if (!r.ok) { showError('Could not save that. Try again.'); return; }
    st.acked = true;
    renderAll();
    input.focus();
  }

  /* ── saved plans and regenerating ─────────────────────────────── */

  async function openSaved(planId) {
    var r = await api('GET', '/coach/plans/' + planId + '/chat');
    if (!r.ok) { global.alert && global.alert("Couldn't open that plan's conversation."); return; }
    st.planId = planId;                   // set first, so the plan:changed handler does not reload
    adopt(r.data);
    st.bannerKind = null;
    st.chips = [];
    PlanState.set({ plan: r.data.plan, planId: planId, draftId: null, rev: r.data.rev, readonly: true });
    var out = document.getElementById('ai-plan-output');
    PlanView.render(out, r.data.plan, { readonly: true });
    ['ai-save-row', 'ai-result-empty', 'ai-result-error'].forEach(function (id) {
      var n = document.getElementById(id);
      if (n) n.style.display = 'none';
    });
    showTab('coach');
    renderAll();
    var section = document.getElementById('plan-ai');
    if (section) section.scrollIntoView({ behavior: REDUCED.matches ? 'auto' : 'smooth', block: 'start' });
  }

  function regenerateFromChat() {
    // The athlete's own recent requests become the generator's focus note (<= 300 chars).
    var asks = st.messages.filter(function (m) { return m.role === 'user' && !m.pending; })
      .map(function (m) { return m.content; }).reverse();
    var note = '';
    for (var i = 0; i < asks.length; i++) {
      var next = note ? note + '; ' + asks[i] : asks[i];
      if (next.length > 300) break;
      note = next;
    }
    var focus = document.getElementById('ai-focus-note');
    if (focus) focus.value = (note || asks[0] || '').slice(0, 300);
    if (st.sheet) st.sheet.close();
    showTab('generate');
    var btn = document.getElementById('ai-generate-btn');
    if (btn) {
      btn.scrollIntoView({ behavior: REDUCED.matches ? 'auto' : 'smooth', block: 'center' });
      btn.focus({ preventScroll: true });
    }
  }

  /* ── wiring ───────────────────────────────────────────────────── */

  function autosize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
  }

  function onPlanChanged(e) {
    var s = (e && e.detail && e.detail.state) || PlanState.get();
    var prev = e && e.detail && e.detail.prev;
    if (s.planId && s.planId !== st.planId) {
      st.bannerKind = null;
      st.tab = 'coach';                   // a plan just appeared: the conversation is the next step (DS-6)
      load(s.planId);
    } else if (prev && prev.planId !== s.planId && !s.planId) {
      st.planId = null;
    }
    applyVisibility();
    renderComposer();
  }

  function onPlanCleared() {
    st.planId = null;
    st.messages = [];
    st.chips = [];
    st.bannerKind = null;
    st.hasUndo = false;
    if (st.sheet) st.sheet.close();
    var toast = document.getElementById('action-toast');
    if (toast) toast.remove();
    showError('');
    st.tab = 'generate';
    applyVisibility();
  }

  function init() {
    panel = document.getElementById('coach-panel');
    if (!panel || !global.PlanState) return;          // chat switched off: nothing to wire
    log = document.getElementById('cp-log');
    input = document.getElementById('cp-input');
    sendBtn = document.getElementById('cp-send');
    workingRow = document.getElementById('cp-working');
    workingText = document.getElementById('cp-working-text');
    errorBox = document.getElementById('cp-error');
    banner = document.getElementById('cp-banner');
    notesToggle = document.getElementById('cp-notes-toggle');
    notesList = document.getElementById('cp-notes-list');
    notesCount = document.getElementById('cp-notes-count');
    privacy = document.getElementById('cp-privacy');
    ackBtn = document.getElementById('cp-ack');
    tabs = document.getElementById('coach-tabs');
    tabGenerate = document.getElementById('tab-generate');
    tabCoach = document.getElementById('tab-coach');
    genPanel = document.getElementById('ai-generate-panel');

    document.getElementById('cp-form').addEventListener('submit', function (e) {
      e.preventDefault();
      if (MOBILE.matches && !st.sheet) openSheet();
      send();
    });
    input.addEventListener('keydown', function (e) {
      // Desktop: Enter sends, Shift+Enter is a newline. Touch: Enter is a newline; Send sends.
      if (e.key === 'Enter' && !e.shiftKey && !COARSE.matches && !e.isComposing) {
        e.preventDefault();
        send();
      }
    });
    input.addEventListener('input', autosize);
    // Opening the sheet inside the tap keeps iOS's keyboard up: the focus is already there.
    input.addEventListener('focus', function () { if (MOBILE.matches) openSheet(); });
    document.getElementById('cp-close').addEventListener('click', function () { if (st.sheet) st.sheet.close(); });
    ackBtn.addEventListener('click', acknowledge);
    notesToggle.addEventListener('click', function () { st.notesOpen = !st.notesOpen; renderNotes(); });
    if (tabGenerate) tabGenerate.addEventListener('click', function () { showTab('generate'); });
    if (tabCoach) tabCoach.addEventListener('click', function () { showTab('coach'); });

    document.addEventListener('plan:changed', onPlanChanged);
    document.addEventListener('plan:cleared', onPlanCleared);
    document.addEventListener('plan:busy', function () { renderComposer(); if (st.hasUndo) renderLog(); });
    var onResize = function () { if (!MOBILE.matches && st.sheet) st.sheet.close(); applyVisibility(); };
    if (MOBILE.addEventListener) MOBILE.addEventListener('change', onResize);
    else if (MOBILE.addListener) MOBILE.addListener(onResize);

    if (planOnScreen()) onPlanChanged(null);           // a draft rendered before this script ran
    else applyVisibility();
  }

  global.CoachChat = {
    init: init,
    showTab: showTab,
    openSaved: openSaved,
    reload: function () { return load(st.planId, true); },   // re-read the plan and the conversation
    /* ER-9: a generation shows its progress on the Generate side, and chatting about a
       plan that is being replaced makes no sense, so Coach is unavailable meanwhile. */
    generating: function (on) {
      st.generating = !!on;
      if (on) { if (st.sheet) st.sheet.close(); st.tab = 'generate'; }
      if (panel) applyVisibility();
    },
    _state: st,                           // for tests
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})(window);
