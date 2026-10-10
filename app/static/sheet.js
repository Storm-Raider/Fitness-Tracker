/*
 * Sheet: turns an element that already lives in the page into a modal bottom
 * sheet, and back. The element is restyled in place and NEVER moved in the DOM,
 * so a focused <textarea> inside it keeps focus and its text (iOS drops the
 * keyboard if a focused input is reparented).
 *
 *   var handle = Sheet.open(root, {label, initialFocus, returnFocus, onClose});
 *   handle.close();
 *
 * What it does (DESIGN.md "Sheet"):
 *  - adds .is-sheet (+ a .sheet-dim layer): 62% of the visual viewport, or the
 *    whole visual viewport while the on-screen keyboard is open;
 *  - role="dialog" aria-modal="true"; the rest of the page is made `inert`;
 *  - focus moves to initialFocus synchronously (iOS only raises the keyboard
 *    for a focus() inside the tap handler), Tab is trapped, focus returns on close;
 *  - Escape, a tap on the dim layer, or handle.close() dismiss it;
 *  - the page is scroll-locked with position:fixed (overflow:hidden does not
 *    lock iOS) and the scroll position is restored on close;
 *  - visualViewport resize/scroll keep the sheet on the visible area; the
 *    listeners are removed on close;
 *  - a grab handle (.sheet-handle, aria-hidden: Close and Escape remain) is
 *    added at the top: dragging it down past a quarter of the sheet's height,
 *    or flicking it, dismisses (reason "drag"); a shorter drag snaps back.
 *    Only the handle starts a drag, so the sheet's content scrolls normally.
 *
 * Overlays that open on top (the confirm sheet, toasts) stay usable: inert is
 * applied only to what exists at open time, never to .undo-toast, the
 * achievement rack or [data-sheet-keep], and the sheet ignores Escape and the
 * Tab trap while #confirm-sheet is open.
 *
 * SAFETY: no HTML-string APIs here (tests/test_chat_js_safety.py).
 */
(function (global) {
  'use strict';

  var KEEP = '.undo-toast, #ach-toast-rack, [data-sheet-keep]';
  var FOCUSABLE = 'a[href], button, input, select, textarea, [tabindex]';
  var KEYBOARD_PX = 150; // innerHeight - visualViewport.height above this = keyboard open
  var DRAG_CLOSE_FRACTION = 0.25; // drag past this share of the sheet's height to dismiss
  var FLICK_PX_PER_MS = 0.4;      // or release this fast (and at least 20px down); vaul uses 0.4

  var active = null;
  var finishPending = null; // completes a close animation that is still running

  function reducedMotion() {
    return !!(global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  function confirmOpen() { return !!document.getElementById('confirm-sheet'); }

  function focusables(root) {
    return Array.prototype.filter.call(root.querySelectorAll(FOCUSABLE), function (n) {
      return !n.disabled && n.tabIndex >= 0 && n.getClientRects().length > 0;
    });
  }

  // Make everything outside `root` (and its ancestors) inert; return what we changed.
  function inertOthers(root) {
    var changed = [];
    var node = root;
    while (node && node !== document.body) {
      var parent = node.parentNode;
      Array.prototype.forEach.call(parent.children, function (sib) {
        if (sib === node || sib.matches(KEEP) || sib.hasAttribute('inert')) return;
        if (sib.tagName === 'SCRIPT' || sib.tagName === 'STYLE') return;
        sib.setAttribute('inert', '');
        changed.push(sib);
      });
      node = parent;
    }
    return changed;
  }

  function layout(root) {
    var vv = global.visualViewport;
    var vh = vv ? vv.height : global.innerHeight;
    var offsetTop = vv ? vv.offsetTop : 0;
    var keyboard = vv ? global.innerHeight - vv.height > KEYBOARD_PX : false;
    var h = keyboard ? vh : Math.round(vh * 0.62);
    root.style.top = (offsetTop + vh - h) + 'px';
    root.style.height = h + 'px';
  }

  function open(root, opts) {
    if (finishPending) finishPending();
    if (active) return active.handle;
    opts = opts || {};

    var returnTo = opts.returnFocus || document.activeElement;
    var prevRole = root.getAttribute('role');
    var prevModal = root.getAttribute('aria-modal');
    var prevLabel = root.getAttribute('aria-label');
    var prevTop = root.style.top;
    var prevHeight = root.style.height;
    var inerted = inertOthers(root);

    var body = document.body;
    var scrollY = global.pageYOffset || 0;
    var prevBody = {
      position: body.style.position, top: body.style.top, left: body.style.left,
      right: body.style.right, width: body.style.width,
    };
    body.style.position = 'fixed';
    body.style.top = (-scrollY) + 'px';
    body.style.left = '0';
    body.style.right = '0';
    body.style.width = '100%';

    var dim = document.createElement('div');
    dim.className = 'sheet-dim';
    document.body.appendChild(dim);

    var grab = document.createElement('div');
    grab.className = 'sheet-handle';
    grab.setAttribute('aria-hidden', 'true');
    var bar = document.createElement('span');
    bar.className = 'sheet-handle-bar';
    grab.appendChild(bar);
    root.insertBefore(grab, root.firstChild);

    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-modal', 'true');
    if (opts.label) root.setAttribute('aria-label', opts.label);
    root.classList.add('is-sheet');
    layout(root);
    void root.offsetHeight; // flush so the slide-in transition runs
    root.classList.add('show');
    dim.classList.add('show');

    // Synchronous on purpose: iOS raises the keyboard only for focus() called
    // inside the user's tap.
    var first = opts.initialFocus || focusables(root)[0];
    if (first) first.focus({ preventScroll: true });

    var raf = 0;
    function onViewport() {
      if (raf) return;
      raf = global.requestAnimationFrame(function () { raf = 0; layout(root); });
    }
    var vv = global.visualViewport;
    if (vv) {
      vv.addEventListener('resize', onViewport);
      vv.addEventListener('scroll', onViewport);
    }

    function onKey(e) {
      if (confirmOpen()) return; // the confirm sheet on top owns the keyboard
      if (e.key === 'Escape') {
        e.preventDefault();
        close('escape');
      } else if (e.key === 'Tab') {
        var items = focusables(root);
        if (!items.length) { e.preventDefault(); return; }
        var firstEl = items[0];
        var lastEl = items[items.length - 1];
        if (!root.contains(document.activeElement)) {
          e.preventDefault();
          firstEl.focus();
        } else if (e.shiftKey && document.activeElement === firstEl) {
          e.preventDefault();
          lastEl.focus();
        } else if (!e.shiftKey && document.activeElement === lastEl) {
          e.preventDefault();
          firstEl.focus();
        }
      }
    }
    document.addEventListener('keydown', onKey, true);
    dim.addEventListener('click', function () { close('dim'); });

    // Drag to dismiss, from the handle only.
    var drag = null;
    function onGrabDown(e) {
      if (e.button > 0 || confirmOpen()) return;
      drag = { startY: e.clientY, dy: 0, samples: [{ y: e.clientY, t: e.timeStamp }] };
      root.style.transition = 'none';
      if (grab.setPointerCapture) grab.setPointerCapture(e.pointerId);
      e.preventDefault();
    }
    function onGrabMove(e) {
      if (!drag) return;
      drag.samples.push({ y: e.clientY, t: e.timeStamp });
      if (drag.samples.length > 20) drag.samples.shift();
      drag.dy = Math.max(0, e.clientY - drag.startY);
      root.style.transform = 'translateY(' + drag.dy + 'px)';
      dim.style.opacity = String(Math.max(0, 1 - drag.dy / root.offsetHeight));
    }
    // Release speed over the last 100 ms: one move-to-move step is too noisy
    // (browsers batch pointer moves per frame).
    function releaseSpeed(samples) {
      var last = samples[samples.length - 1];
      for (var i = 0; i < samples.length; i++) {
        if (last.t - samples[i].t <= 100) {
          var dt = last.t - samples[i].t;
          return dt > 0 ? (last.y - samples[i].y) / dt : 0;
        }
      }
      return 0;
    }
    function onGrabUp() {
      if (!drag) return;
      var d = drag;
      d.v = releaseSpeed(d.samples);
      drag = null;
      root.style.transition = '';
      dim.style.opacity = '';
      if (d.dy > root.offsetHeight * DRAG_CLOSE_FRACTION || (d.v > FLICK_PX_PER_MS && d.dy > 20)) {
        close('drag');
      } else {
        root.style.transform = '';   // snap back
      }
    }
    grab.addEventListener('pointerdown', onGrabDown);
    grab.addEventListener('pointermove', onGrabMove);
    grab.addEventListener('pointerup', onGrabUp);
    grab.addEventListener('pointercancel', onGrabUp);

    function restoreAttr(name, value) {
      if (value == null) root.removeAttribute(name); else root.setAttribute(name, value);
    }

    function close(reason) {
      if (!active || active.root !== root) return;
      active = null;
      document.removeEventListener('keydown', onKey, true);
      if (vv) {
        vv.removeEventListener('resize', onViewport);
        vv.removeEventListener('scroll', onViewport);
      }
      if (raf) { global.cancelAnimationFrame(raf); raf = 0; }

      inerted.forEach(function (n) { n.removeAttribute('inert'); });
      body.style.position = prevBody.position;
      body.style.top = prevBody.top;
      body.style.left = prevBody.left;
      body.style.right = prevBody.right;
      body.style.width = prevBody.width;
      global.scrollTo(0, scrollY);
      var restoredY = global.pageYOffset; // may be clamped while the root is still out of flow
      restoreAttr('role', prevRole);
      restoreAttr('aria-modal', prevModal);
      restoreAttr('aria-label', prevLabel);

      drag = null;
      root.style.transition = '';
      root.style.transform = '';   // a drag's offset: let the slide-out continue from it
      root.classList.remove('show');
      dim.classList.remove('show');
      var finished = false;
      function finish() {
        if (finished) return;
        finished = true;
        finishPending = null;
        root.classList.remove('is-sheet');
        grab.remove();
        root.style.top = prevTop;
        root.style.height = prevHeight;
        dim.remove();
        // The root may have rejoined the document flow (taller page): if the
        // earlier restore was clamped and the user has not scrolled since, redo it.
        if (restoredY !== scrollY && global.pageYOffset === restoredY) global.scrollTo(0, scrollY);
      }
      if (reducedMotion()) finish();
      else { finishPending = finish; setTimeout(finish, 240); }

      if (returnTo && document.contains(returnTo) && returnTo.focus) {
        returnTo.focus({ preventScroll: true });
      }
      if (opts.onClose) opts.onClose(reason || 'api');
    }

    var handle = {
      close: function () { close('api'); },
      relayout: function () { layout(root); },
    };
    active = { root: root, handle: handle };
    return handle;
  }

  global.Sheet = {
    open: open,
    isOpen: function () { return !!active; },
  };
})(window);
