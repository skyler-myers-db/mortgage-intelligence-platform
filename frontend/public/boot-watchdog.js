/*
 * Boot watchdog for the Mortgage Intelligence Platform shell (2026-09-21
 * audit, 12.3 review leftover: "if the entry chunk never loads, the page
 * keeps an aria-hidden skeleton with no visible error").
 *
 * index.html paints a static, aria-hidden skeleton inside #root
 * (src/lib/shellSkeleton.ts) until the entry chunk renders. If that chunk
 * never arrives (a failed or hung download), nothing ever replaced it: a
 * sighted user saw a frozen shell and a screen-reader user heard nothing.
 * This classic script (CSP is script-src 'self', so no inline code) swaps
 * the skeleton for a recovery surface when:
 *   - a module script other than the boot chunk (/assets/boot-*.js) fails to
 *     load (a capture-phase window 'error'; a failed import inside the entry
 *     graph is reported on the entry's own script element), or
 *   - BOUND_MS pass and main.tsx has not signalled data-mip-boot="ready" on
 *     <html>.
 * It never swaps a signalled page or a #root the app already owns, reads no
 * error message, filename, URL or stack, sends nothing and stores nothing.
 * The surface uses existing classes only (.error-surface, as the root
 * ErrorBoundary draws it), so it adds no CSS. If the entry still commits
 * after a swap, createRoot clears #root and the app renders.
 * src/lib/bootWatchdog.test.ts executes this file; index.html references it
 * with a content-hash query (?v=...) that the same test pins.
 * Declared departure from design_files/Module 0 Prototype.html:974 (an
 * empty #root with no pre-JS state) (deviation:boot-load-error).
 *
 * ES5 on purpose: it must run in whatever parses index.html first.
 */
(function () {
  'use strict';
  var BOUND_MS = 20000;
  var SIGNAL = 'data-mip-boot';
  var BOOT_CHUNK = /\/assets\/boot-[^/]*\.js(\?|$)/;
  var done = false;

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function fail() {
    if (done) return;
    if (document.documentElement.getAttribute(SIGNAL) === 'ready') return;
    var root = document.getElementById('root');
    if (!root || !document.querySelector('#root > .app-shell[aria-hidden="true"]')) return;
    done = true;
    var surface = element('section', 'surface error-surface error-surface--page');
    surface.setAttribute('role', 'alert');
    surface.setAttribute('data-boot-watchdog', '');
    var body = element('div', 'surface__body error-surface__body');
    var copy = element('div', 'error-surface__copy');
    copy.appendChild(element('h1', 'h-3', 'The workspace did not finish loading'));
    copy.appendChild(element('p', 'body error-surface__sub',
      'Part of the app could not be downloaded. Check your connection, then reload the page.'));
    var actions = element('div', 'error-surface__actions');
    var reload = element('button', 'btn btn--primary', 'Reload');
    reload.type = 'button';
    reload.addEventListener('click', function () {
      window.location.reload();
    });
    actions.appendChild(reload);
    body.appendChild(copy);
    body.appendChild(actions);
    surface.appendChild(body);
    while (root.firstChild) root.removeChild(root.firstChild);
    root.appendChild(surface);
    reload.focus();
  }

  try {
    window.setTimeout(fail, BOUND_MS);
    window.addEventListener('error', function (event) {
      var target = event.target;
      if (!target || target.tagName !== 'SCRIPT' || target.type !== 'module') return;
      if (BOOT_CHUNK.test(target.getAttribute('src') || '')) return;
      fail();
    }, true);
  } catch (error) {
    /* Never block the shell. */
  }
})();
