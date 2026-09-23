/**
 * @vitest-environment happy-dom
 *
 * The data router that main.tsx mounts (audit states-05) must not change who
 * owns a shell failure. A data router wraps its root route in React Router's
 * own error boundary, which would render its developer page ("Unexpected
 * Application Error!") instead of the product's root recovery surface; the
 * catch-all route's RethrowToRootBoundary hands the error on. Rendered here
 * through the same route objects main.tsx uses, on a memory history.
 */
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { RouterProvider, createMemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { appRouteObjects } from './appRouter';
import { ErrorBoundary } from './components/ErrorBoundary';
import { rootErrorOptions } from './lib/clientErrorLog';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function ShellThrow(): ReactNode {
  throw new Error('shell render failed for B-0TESTBORROWER');
}

function Where() {
  const { pathname, search, hash } = useLocation();
  return <output data-testid="where">{`${pathname}${search}${hash}`}</output>;
}

describe('appRouter', () => {
  let root: Root;
  let container: HTMLElement;
  let consoleError: MockInstance<typeof console.error>;

  beforeEach(() => {
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container, rootErrorOptions());
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  async function mount(element: ReactNode, path: string): Promise<void> {
    const router = createMemoryRouter(appRouteObjects(element), { initialEntries: [path] });
    await act(async () => {
      root.render(
        <ErrorBoundary boundary="root" variant="page">
          <RouterProvider router={router} />
        </ErrorBoundary>,
      );
    });
  }

  it('hands every URL, with its query and hash, to the one catch-all element', async () => {
    await mount(<Where />, '/borrower-360/B-0TESTBORROWER?tab=offer#evidence');
    expect(container.querySelector('[data-testid="where"]')?.textContent).toBe(
      '/borrower-360/B-0TESTBORROWER?tab=offer#evidence',
    );
  });

  it('lets a shell render throw reach the root ErrorBoundary, not React Router\'s page', async () => {
    await mount(<ShellThrow />, '/');
    const surface = container.querySelector('[data-testid="error-surface"]');
    expect(surface?.getAttribute('data-error-boundary')).toBe('root');
    expect(container.textContent).not.toContain('Unexpected Application Error');
    // One report, from the root boundary, through the message-free client log.
    const reports = consoleError.mock.calls.filter((call) => call[0] === '[mip] client error');
    expect(reports).toHaveLength(1);
    expect(reports[0][1]).toMatchObject({ source: 'caught', boundary: 'root' });
    expect(consoleError.mock.calls.some((call) => String(call[0]).includes('React Router caught'))).toBe(false);
  });
});
