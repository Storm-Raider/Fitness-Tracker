/*
 * PlanView + PlanState for the Plan page's AI section.
 *
 * PlanView.render(container, plan, opts) draws a coach plan (summary header +
 * day cards) and is the only code that does so: generation, saved/draft plans
 * and, later, chat edits, undo and swap all go through it.
 *
 * PlanState is the one place that knows which plan is on screen, so the page
 * script and the chat panel cannot disagree about it.
 *
 * SAFETY: everything here comes from a model or the database, so the DOM is
 * built with createElement + textContent only. Never use HTML-string APIs in
 * this file; tests/test_chat_js_safety.py fails the build if one appears.
 *
 * Markup contract (the chat relies on it):
 *   .day-card[data-day="N"]   N is 1-based and matches the server's day index
 *     .ex-row[data-idx="I"]   I is the 0-based position in the day's exercises
 */
(function (global) {
  'use strict';

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function fmt(v) { return v == null ? '' : String(v); }

  var defaultRowAction = null;

  /* ── PlanView ─────────────────────────────────────── */

  function ensure(container) {
    var pv = container.__pv;
    if (pv && pv.header.parentNode === container) return pv;
    // First render, or something cleared the container behind our back.
    while (container.firstChild) container.removeChild(container.firstChild);
    pv = {
      // Styles live in plan.html (.plan-out-*), not inline.
      header: el('div', 'card card-accent plan-out-head'),
      title: el('div', 'plan-out-title'),
      summary: el('div', 'plan-out-summary'),
      volume: el('div', 'plan-out-volume'),
      dropped: el('div', 'plan-out-dropped'),
      daysEl: el('div'),
      days: [],
    };
    pv.header.appendChild(pv.title);
    pv.header.appendChild(pv.summary);
    pv.header.appendChild(pv.volume);
    container.appendChild(pv.header);
    container.appendChild(pv.dropped);
    container.appendChild(pv.daysEl);
    container.__pv = pv;
    return pv;
  }

  function fmtRest(sec) {
    if (!sec) return '';
    return sec < 120 ? sec + ' s rest' : (Math.round(sec / 30) / 2) + ' min rest';
  }

  // "80 kg · RIR 2 · 3 min rest" for plans the coach programmed; null for older plans.
  // The load sits in a .weight-display[data-kg] span so the app's unit toggle re-renders it.
  function buildTarget(ex) {
    var parts = [];
    if (ex.rir != null) parts.push('RIR ' + ex.rir);
    if (ex.rest_sec) parts.push(fmtRest(ex.rest_sec));
    if (!parts.length && !ex.load_kg) return null;
    var line = el('span', 'ex-target');
    if (ex.load_kg) {
      var w = el('span', 'weight-display num');
      w.setAttribute('data-kg', String(ex.load_kg));
      w.textContent = global.fmtWeight ? global.fmtWeight(ex.load_kg, global.getAppUnit()) : ex.load_kg + ' kg';
      line.appendChild(w);
      if (parts.length) line.appendChild(document.createTextNode(' · '));
    }
    line.appendChild(document.createTextNode(parts.join(' · ')));
    return line;
  }

  function buildDay(day, n, opts, isChanged) {
    var card = el('div', 'day-card');
    card.setAttribute('data-day', String(n));
    var h3 = el('h3');
    h3.appendChild(el('span', 'day-num', 'Day ' + n));
    h3.appendChild(document.createTextNode(' ' + fmt(day.focus)));
    if (isChanged) h3.appendChild(el('span', 'edited-tag', 'Edited'));
    card.appendChild(h3);

    (day.exercises || []).forEach(function (ex, idx) {
      var row = el('div', isChanged ? 'ex-row changed' : 'ex-row');
      row.setAttribute('data-idx', String(idx));
      var name = el('span', 'ex-name', fmt(ex.name));
      if (ex.note) name.appendChild(el('span', 'ex-note', fmt(ex.note)));
      var target = buildTarget(ex);
      if (target) name.appendChild(target);
      if (ex.progression) name.appendChild(el('span', 'ex-prog', fmt(ex.progression)));
      row.appendChild(name);
      row.appendChild(el('span', 'ex-scheme num', fmt(ex.sets) + ' × ' + fmt(ex.reps)));
      if (opts.onRowAction) {
        var more = el('button', 'ex-more', '⋯');
        more.type = 'button';
        more.setAttribute('aria-label', 'Actions for ' + fmt(ex.name));
        more.setAttribute('aria-haspopup', 'true');
        more.addEventListener('click', function () { opts.onRowAction(n, idx, more); });
        row.appendChild(more);
      }
      card.appendChild(row);
    });
    return card;
  }

  var PlanView = {
    /*
     * opts.readonly     true hides the per-row action button
     * opts.changedDays  1-based day numbers of the latest edit: they get the
     *                   EDITED label and the changed-row tint
     * opts.dropped      names the model suggested that were not recognised
     * opts.onRowAction  (dayNumber, exerciseIndex, buttonElement) => void;
     *                   when given each row gets a 44x44 overflow button (the
     *                   callback decides what a read-only plan allows). Defaults
     *                   to the page-wide hook set with PlanView.setRowAction;
     *                   pass null to render without buttons
     *
     * Re-rendering patches in place: only days whose content or flags changed
     * are rebuilt, so UI living inside an unchanged day (an open swap list)
     * survives. Nothing outside the header and the days is touched.
     */
    render: function (container, plan, opts) {
      opts = Object.assign({}, opts || {});
      if (opts.onRowAction === undefined) opts.onRowAction = defaultRowAction;
      plan = plan || {};
      var pv = ensure(container);
      var changed = {};
      (opts.changedDays || []).forEach(function (n) { changed[n] = true; });

      pv.title.textContent = fmt(plan.title);
      pv.summary.textContent = fmt(plan.summary);
      pv.summary.style.display = plan.summary ? '' : 'none';
      // Why accessory volume moved for recovery (coach_program.volume_note); older plans have none.
      pv.volume.textContent = fmt(plan.volume_note);
      pv.volume.style.display = plan.volume_note ? '' : 'none';

      var dropped = opts.dropped || [];
      pv.dropped.textContent = dropped.length
        ? 'Skipped unrecognised suggestions: ' + dropped.join(', ')
        : '';
      pv.dropped.style.display = dropped.length ? '' : 'none';

      var days = plan.days || [];
      days.forEach(function (day, i) {
        var n = i + 1;
        var isChanged = !!changed[n];
        var key = JSON.stringify([day.focus, day.exercises, !!opts.readonly, !!opts.onRowAction, isChanged]);
        var old = pv.days[i];
        if (old && old.key === key) return;
        var node = buildDay(day, n, opts, isChanged);
        if (old) pv.daysEl.replaceChild(node, old.node);
        else pv.daysEl.appendChild(node);
        pv.days[i] = { key: key, node: node };
      });
      while (pv.days.length > days.length) {
        pv.daysEl.removeChild(pv.days.pop().node);
      }
    },

    /* The coach chat registers its row menu here, so every render of the plan
       (generation, drafts, saved plans, edits, undo) gets the same buttons. */
    setRowAction: function (fn) { defaultRowAction = fn || null; },

    clear: function (container) {
      while (container.firstChild) container.removeChild(container.firstChild);
      container.__pv = null;
    },

    dayNode: function (container, n) {
      return container.querySelector('.day-card[data-day="' + Number(n) + '"]');
    },
  };

  /* ── PlanState ────────────────────────────────────── */

  // planId: the coach_plans row on screen (a draft or a saved plan); draftId only for drafts.
  var DEFAULTS = { plan: null, planId: null, draftId: null, rev: null, readonly: false, busy: false };
  var state = Object.assign({}, DEFAULTS);

  function snapshot() { return Object.assign({}, state); }

  function emit(name, detail) {
    document.dispatchEvent(new CustomEvent(name, { detail: detail }));
  }

  var PlanState = {
    get: snapshot,

    /* Merge known keys and announce `plan:changed` with {state, prev}. */
    set: function (patch) {
      var prev = snapshot();
      Object.keys(DEFAULTS).forEach(function (k) {
        if (k !== 'busy' && Object.prototype.hasOwnProperty.call(patch, k)) state[k] = patch[k];
      });
      emit('plan:changed', { state: snapshot(), prev: prev });
    },

    /* No plan on screen any more: back to defaults and announce `plan:cleared`. */
    clear: function () {
      var prev = snapshot();
      state = Object.assign({}, DEFAULTS);
      emit('plan:cleared', { prev: prev });
    },

    /* A write is in flight (generate, save, undo, a chat turn): mutators disable. */
    setBusy: function (busy) {
      busy = !!busy;
      if (state.busy === busy) return;
      state.busy = busy;
      emit('plan:busy', { busy: busy });
    },
  };

  global.PlanView = PlanView;
  global.PlanState = PlanState;
})(window);
