/**
 * @vitest-environment happy-dom
 */

import { act, type ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

/**
 * Boot wiring of main.tsx, asserted on the real `#root` it renders into:
 * the root ErrorBoundary, the createRoot error callbacks and the
 * `vite:preloadError` listener. Only `./app` is replaced, so the test can
 * make the whole shell throw.
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
});
