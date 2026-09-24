/**
 * @vitest-environment happy-dom
 */

import { act, type ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

/**
 * Boot wiring of main.tsx, asserted on the real `#root` it renders into:
 * the root ErrorBoundary, the createRoot error callbacks, the
 * `vite:preloadError` listener, the window `error` / `unhandledrejection`
 * listeners and the router source RUM reads route changes from. Only `./app`
 * is replaced, so the test can make the whole shell throw.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const shell = vi.hoisted(() => ({
  render: (): ReactNode => {
    throw new Error('shell crashed while rendering borrower B-0TESTBORROWER');
  },
}));

vi.mock('./app', () => ({
  default: function App() {
    return shell.render();
  },
}));

describe('main.tsx boot', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders a page-level recovery surface instead of an empty #root when the shell throws', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const addEventListener = vi.spyOn(window, 'addEventListener');
    document.body.innerHTML = '<div id="root"></div>';

    await act(async () => {
      await import('./main');
    });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    const root = document.getElementById('root') as HTMLElement;
    const surface = root.querySelector('[data-testid="error-surface"]');
    expect(root.childElementCount).toBeGreaterThan(0);
    expect(surface?.getAttribute('data-error-boundary')).toBe('root');
    expect(surface?.className).toContain('error-surface--page');
    expect(root.innerHTML).not.toContain('B-0TESTBORROWER');

    // createRoot error callbacks are wired: the caught error reaches the log
    // as a message-free report naming the root boundary.
    const reports = consoleError.mock.calls
      .filter((call) => call[0] === '[mip] client error')
      .map((call) => call[1] as { source: string; boundary: string | null });
    expect(reports).toContainEqual(
      expect.objectContaining({ source: 'caught', boundary: 'root', kind: 'render' }),
    );

    // Stale-chunk recovery is installed at boot.
    expect(addEventListener).toHaveBeenCalledWith('vite:preloadError', expect.any(Function));

    // "Try again" re-renders the real provider tree once the shell stops throwing.
    shell.render = () => <div data-testid="shell-ok">workspace</div>;
    await act(async () => {
      Array.from(root.querySelectorAll('button'))
        .find((b) => b.textContent === 'Try again')
        ?.click();
    });
    expect(root.querySelector('[data-testid="error-surface"]')).toBeNull();
    expect(root.querySelector('[data-testid="shell-ok"]')?.textContent).toBe('workspace');
    // Importing main.tsx transforms the whole boot graph; on a loaded CI box
    // that alone can pass the 5 s default.
  }, 60_000);

  it('installs the error listeners and the router source; a borrower-bearing ErrorEvent leaves one message-free report and no request', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const addEventListener = vi.spyOn(window, 'addEventListener');
    const fetchSpy = vi.fn();
    const beacon = vi.fn(() => true);
    vi.stubGlobal('fetch', fetchSpy);
    Object.defineProperty(navigator, 'sendBeacon', { configurable: true, value: beacon });
    shell.render = () => <div data-testid="shell-ok">workspace</div>;
    document.body.innerHTML = '<div id="root"></div>';

    vi.resetModules();
    await act(async () => {
      await import('./main');
    });
    // The same module instance main.tsx registered with (no reset in between).
    const bridge = await import('./lib/rumBridge');

    const events = addEventListener.mock.calls.map((call) => call[0]);
    expect(events).toContain('error');
    expect(events).toContain('unhandledrejection');
    const source = bridge.getRumRouteSource();
    expect(source, 'main.tsx registered the data router as the RUM route source').not.toBeNull();
    const unsubscribe = source?.(() => undefined);
    expect(typeof unsubscribe).toBe('function');
    unsubscribe?.();

    const message = 'Cannot read score of borrower B-0TESTBORROWER at /borrower-360/B-0TESTBORROWER?x=1';
    window.dispatchEvent(new ErrorEvent('error', { error: new Error(message), message }));

    const sent: unknown[] = [];
    bridge.attachClientErrorSink((report) => sent.push(report));
    expect(sent).toEqual([
      { source: 'window', kind: 'render', errorName: 'Error', boundary: null, route: '/' },
    ]);
    expect(JSON.stringify(sent)).not.toContain('B-0TESTBORROWER');
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(beacon).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  }, 60_000);
});
