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

  var HEADER_STYLE = "font-family:'Syne',sans-serif;font-size:1.15rem;font-weight:700;letter-spacing:-0.02em;";
  var SUMMARY_STYLE = 'color:var(--muted);font-size:0.85rem;margin-top:0.35rem;line-height:1.5;';
  var DROPPED_STYLE = 'font-size:0.72rem;color:var(--muted);margin-bottom:0.75rem;';

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function fmt(v) { return v == null ? '' : String(v); }

  /* ── PlanView ─────────────────────────────────────── */

  function ensure(container) {
    var pv = container.__pv;
    if (pv && pv.header.parentNode === container) return pv;
    // First render, or something cleared the container behind our back.
    while (container.firstChild) container.removeChild(container.firstChild);
    pv = {
      header: el('div', 'card card-accent'),
      title: el('div'),
      summary: el('div'),
      dropped: el('div'),
      daysEl: el('div'),
      days: [],
    };
    pv.header.style.marginBottom = '1rem';
    pv.title.style.cssText = HEADER_STYLE;
    pv.summary.style.cssText = SUMMARY_STYLE;
    pv.dropped.style.cssText = DROPPED_STYLE;
    pv.header.appendChild(pv.title);
    pv.header.appendChild(pv.summary);
    container.appendChild(pv.header);
    container.appendChild(pv.dropped);
    container.appendChild(pv.daysEl);
    container.__pv = pv;
    return pv;
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
      row.appendChild(name);
      row.appendChild(el('span', 'ex-scheme', fmt(ex.sets) + ' × ' + fmt(ex.reps)));
      if (opts.onRowAction && !opts.readonly) {
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
     *                   when given (and not readonly) each row gets a 44x44
     *                   overflow button
     *
     * Re-rendering patches in place: only days whose content or flags changed
     * are rebuilt, so UI living inside an unchanged day (an open swap list)
     * survives. Nothing outside the header and the days is touched.
     */
    render: function (container, plan, opts) {
      opts = opts || {};
      plan = plan || {};
      var pv = ensure(container);
      var changed = {};
      (opts.changedDays || []).forEach(function (n) { changed[n] = true; });

      pv.title.textContent = fmt(plan.title);
      pv.summary.textContent = fmt(plan.summary);
      pv.summary.style.display = plan.summary ? '' : 'none';

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

    clear: function (container) {
      while (container.firstChild) container.removeChild(container.firstChild);
      container.__pv = null;
    },

    dayNode: function (container, n) {
      return container.querySelector('.day-card[data-day="' + Number(n) + '"]');
    },
  };

  /* ── PlanState ────────────────────────────────────── */

  var DEFAULTS = { plan: null, draftId: null, rev: null, readonly: false, busy: false };
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
