/**
 * @vitest-environment happy-dom
 *
 * public/boot-watchdog.js (2026-09-21 audit, 12.3 review leftover): when the
 * entry chunk never loads, the page used to keep the aria-hidden shell
 * skeleton forever with no visible error. This executes the real script the
 * way themeBoot.test.ts executes theme-boot.js, with fake timers, and pins
 * its ?v= content hash in index.html and the ready signal main.tsx sets.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads repo files under Vitest only.
import { createHash } from 'node:crypto';
// @ts-expect-error see node:crypto note above.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:crypto note above.
import { join } from 'node:path';
import { SHELL_SKELETON_HTML } from './shellSkeleton';

declare const process: { cwd(): string };

const watchdogSource = readFileSync(join(process.cwd(), 'public', 'boot-watchdog.js'), 'utf8');
const indexHtml = readFileSync(join(process.cwd(), 'index.html'), 'utf8');
const mainSource = readFileSync(join(process.cwd(), 'src', 'main.tsx'), 'utf8');

const BOUND_MS = 20_000;
const listeners: Array<{ type: string; listener: EventListenerOrEventListenerObject; capture: boolean }> = [];

/** Runs the script once against a page with (or without) the skeleton, tracking its window listeners. */
function runWatchdog({ skeleton = true }: { skeleton?: boolean } = {}): void {
  document.body.innerHTML = `<div id="root">${skeleton ? SHELL_SKELETON_HTML : '<div class="app-shell"></div>'}</div>`;
  const add = window.addEventListener.bind(window);
  const spy = vi.spyOn(window, 'addEventListener').mockImplementation((type, listener, options) => {
    if (listener) listeners.push({ type, listener, capture: options === true || (typeof options === 'object' && options?.capture === true) });
    add(type, listener, options);
  });
  try {
    new Function('window', 'document', watchdogSource)(window, document);
  } finally {
    spy.mockRestore();
  }
}

/** A load error on a <script> element, as the browser reports it (no bubbling; seen in capture). */
function scriptLoadError(attrs: { src: string; type?: string; tag?: 'script' | 'link' }): void {
  const node = document.createElement(attrs.tag ?? 'script');
  if (attrs.tag === 'link') node.setAttribute('href', attrs.src);
  else node.setAttribute('src', attrs.src);
  if (attrs.type) node.setAttribute('type', attrs.type);
  document.head.appendChild(node);
  node.dispatchEvent(new Event('error', { bubbles: false }));
}

const alert = () => document.querySelector<HTMLElement>('[data-boot-watchdog]');

/** happy-dom's own disabled-loading path: a 'load' instead of a logged error, so this file dispatches the only error. */
const happyDomSettings = (window as unknown as { happyDOM?: { settings: { handleDisabledFileLoadingAsSuccess: boolean } } }).happyDOM?.settings;
const loadingAsSuccess = happyDomSettings?.handleDisabledFileLoadingAsSuccess ?? false;

describe('boot watchdog (public/boot-watchdog.js)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    document.documentElement.removeAttribute('data-mip-boot');
    if (happyDomSettings) happyDomSettings.handleDisabledFileLoadingAsSuccess = true;
  });

  afterEach(() => {
    for (const { type, listener, capture } of listeners.splice(0)) window.removeEventListener(type, listener, capture);
    vi.clearAllTimers();
    vi.useRealTimers();
    document.head.innerHTML = '';
    document.body.innerHTML = '';
    document.documentElement.removeAttribute('data-mip-boot');
    if (happyDomSettings) happyDomSettings.handleDisabledFileLoadingAsSuccess = loadingAsSuccess;
  });

  it('keeps the skeleton until the bound, then swaps in a focused, message-free alert', () => {
    runWatchdog();
    vi.advanceTimersByTime(BOUND_MS - 1);
    expect(alert()).toBeNull();
    expect(document.querySelector('#root > .app-shell[aria-hidden="true"]')).not.toBeNull();

    vi.advanceTimersByTime(1);
    const surface = alert();
    expect(surface).not.toBeNull();
    expect(surface?.getAttribute('role')).toBe('alert');
    expect(surface?.className).toBe('surface error-surface error-surface--page');
    expect(document.querySelector('#root > .app-shell')).toBeNull();
    expect(document.getElementById('root')?.children).toHaveLength(1);
    expect(surface?.querySelector('h1.h-3')?.textContent).toBe('The workspace did not finish loading');
    expect(surface?.querySelector('p.body.error-surface__sub')?.textContent).toBe(
      'Part of the app could not be downloaded. Check your connection, then reload the page.',
    );
    const reload = surface?.querySelector<HTMLButtonElement>('.error-surface__actions > button.btn.btn--primary');
    expect(reload?.textContent).toBe('Reload');
    expect(reload?.type).toBe('button');
    expect(document.activeElement).toBe(reload);
    // Message-free: only the fixed copy, no URL, file name or stack.
    expect(surface?.textContent).toBe(
      'The workspace did not finish loadingPart of the app could not be downloaded. Check your connection, then reload the page.Reload',
    );
  });

  it('never swaps a page the entry signalled, or a #root without the skeleton', () => {
    runWatchdog();
    document.documentElement.setAttribute('data-mip-boot', 'ready');
    vi.advanceTimersByTime(BOUND_MS * 2);
    scriptLoadError({ src: '/assets/index-ABC.js', type: 'module' });
    expect(alert()).toBeNull();

    document.documentElement.removeAttribute('data-mip-boot');
    runWatchdog({ skeleton: false });
    vi.advanceTimersByTime(BOUND_MS);
    expect(alert()).toBeNull();
    expect(document.querySelector('#root > .app-shell')).not.toBeNull();
  });

  it('swaps at once when a module script fails, but not for the boot chunk or a link', () => {
    runWatchdog();
    scriptLoadError({ src: '/assets/boot-XyZ12.js', type: 'module' });
    scriptLoadError({ src: '/assets/vendor-react-1.js', tag: 'link' });
    scriptLoadError({ src: '/theme-boot.js?v=00000000' });
    expect(alert()).toBeNull();

    scriptLoadError({ src: 'https://example.invalid/assets/index-B9.js?borrower=B-0TESTBORROWER', type: 'module' });
    expect(alert()).not.toBeNull();
    expect(alert()?.textContent).not.toMatch(/index-B9|example|B-0TESTBORROWER|assets/);
  });

  it('reloads the page from its button', () => {
    const reloadSpy = vi.fn();
    const location = window.location;
    Object.defineProperty(window, 'location', { configurable: true, value: { ...location, reload: reloadSpy } });
    try {
      runWatchdog();
      vi.advanceTimersByTime(BOUND_MS);
      alert()?.querySelector('button')?.click();
      expect(reloadSpy).toHaveBeenCalledTimes(1);
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: location });
    }
  });
});

describe('the watchdog is wired', () => {
  it('index.html loads it as a classic script after theme-boot, by its current content hash', () => {
    const head = indexHtml.slice(0, indexHtml.indexOf('</head>'));
    const hash = createHash('sha256').update(watchdogSource).digest('hex').slice(0, 8);
    expect(head, `public/boot-watchdog.js changed: update the ?v= query in index.html to ${hash}`).toContain(
      `<script src="/boot-watchdog.js?v=${hash}"></script>`,
    );
    expect(head.indexOf('/boot-watchdog.js')).toBeGreaterThan(head.indexOf('/theme-boot.js'));
    expect(head).not.toMatch(/<script[^>]*boot-watchdog[^>]*(async|defer|type="module")/);
  });

  it('main.tsx sets the same ready signal, after render', () => {
    expect(watchdogSource).toContain("var SIGNAL = 'data-mip-boot';");
    const signal = mainSource.indexOf('document.documentElement.setAttribute("data-mip-boot", "ready");');
    expect(signal).toBeGreaterThan(mainSource.indexOf('.render('));
  });
});
