/**
 * @vitest-environment happy-dom
 */

import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { rootErrorOptions } from '../lib/clientErrorLog';
import { ROUTE_IDS, ROUTES, type RouteDefinition } from '../lib/routeMeta';
import { designCss } from '../test/designCss';
import { ErrorBoundary } from './ErrorBoundary';
import { routeLabelForPath } from './ErrorBoundaryFallback';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const BORROWER_BEARING_MESSAGE = 'Cannot read score of borrower B-0TESTBORROWER';

function Thrower({ error }: { error: unknown }): ReactNode {
  throw error;
}

function buttons(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('button')).map((b) => b.textContent ?? '');
}

describe('ErrorBoundary', () => {
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

  it('replaces a render throw with a recovery surface and keeps siblings mounted', async () => {
    await act(async () => {
      root.render(
        <div>
          <nav data-testid="shell">shell stays</nav>
          <ErrorBoundary boundary="route" routeLabel="Lead Queue">
            <Thrower error={new Error(BORROWER_BEARING_MESSAGE)} />
          </ErrorBoundary>
        </div>,
      );
    });

    const surface = container.querySelector('[data-testid="error-surface"]');
    expect(surface).not.toBeNull();
    expect(surface?.getAttribute('role')).toBe('alert');
    expect(surface?.getAttribute('data-error-kind')).toBe('render');
    expect(surface?.getAttribute('data-error-boundary')).toBe('route');
    expect(surface?.className).toContain('error-surface--route');
    expect(surface?.textContent).toContain('Lead Queue hit an unexpected error');
    expect(surface?.textContent).toContain('Route · Lead Queue');
    expect(container.querySelector('[data-testid="shell"]')?.textContent).toBe('shell stays');
    expect(buttons(container)).toEqual(['Try again', 'Reload']);
  });

  it('never renders the raw error message, which can carry borrower data', async () => {
    await act(async () => {
      root.render(
        <ErrorBoundary boundary="route" routeLabel="Borrower 360">
          <Thrower error={new Error(BORROWER_BEARING_MESSAGE)} />
        </ErrorBoundary>,
      );
    });

    expect(container.querySelector('[data-testid="error-surface"]')).not.toBeNull();
    expect(container.innerHTML).not.toContain('B-0TESTBORROWER');
    expect(container.innerHTML).not.toContain('Cannot read score');
  });

  it('offers Reload only for a failed lazy chunk, because React.lazy caches the rejection', async () => {
    const reload = vi.fn();
    await act(async () => {
      root.render(
        <ErrorBoundary boundary="route" routeLabel="Analytics" onReload={reload}>
          <Thrower
            error={new TypeError('Failed to fetch dynamically imported module: /assets/analytics-0ld.js')}
          />
        </ErrorBoundary>,
      );
    });

    const surface = container.querySelector('[data-testid="error-surface"]');
    expect(surface?.getAttribute('data-error-kind')).toBe('chunk');
    expect(surface?.textContent).toContain('A new version is available');
    expect(container.innerHTML).not.toContain('/assets/analytics-0ld.js');
    expect(buttons(container)).toEqual(['Reload']);

    await act(async () => {
      container.querySelector('button')?.click();
    });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('"Try again" clears the boundary and re-renders the children', async () => {
    let shouldThrow = true;
    function Flaky() {
      if (shouldThrow) throw new Error('first render fails');
      return <div data-testid="recovered">recovered</div>;
    }
    await act(async () => {
      root.render(
        <ErrorBoundary boundary="route">
          <Flaky />
        </ErrorBoundary>,
      );
    });
    expect(container.querySelector('[data-testid="error-surface"]')).not.toBeNull();

    shouldThrow = false;
    await act(async () => {
      Array.from(container.querySelectorAll('button'))
        .find((b) => b.textContent === 'Try again')
        ?.click();
    });

    expect(container.querySelector('[data-testid="error-surface"]')).toBeNull();
    expect(container.querySelector('[data-testid="recovered"]')?.textContent).toBe('recovered');
  });

  it('"Try again" runs onRetry before the children re-render', async () => {
    let shouldThrow = true;
    const onRetry = vi.fn(() => {
      shouldThrow = false;
    });
    function Flaky() {
      if (shouldThrow) throw new Error('first render fails');
      return <div data-testid="recovered">recovered</div>;
    }
    await act(async () => {
      root.render(
        <ErrorBoundary boundary="route" onRetry={onRetry}>
          <Flaky />
        </ErrorBoundary>,
      );
    });
    expect(onRetry).not.toHaveBeenCalled();

    await act(async () => {
      Array.from(container.querySelectorAll('button'))
        .find((b) => b.textContent === 'Try again')
        ?.click();
    });

    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="error-surface"]')).toBeNull();
    expect(container.querySelector('[data-testid="recovered"]')?.textContent).toBe('recovered');
  });

  it('clears a caught error when resetKey changes (navigating away from a broken route)', async () => {
    const tree = (pathname: string, child: ReactNode) => (
      <ErrorBoundary boundary="route" resetKey={pathname}>
        {child}
      </ErrorBoundary>
    );
    await act(async () => {
      root.render(tree('/lead-queue', <Thrower error={new Error('boom')} />));
    });
    expect(container.querySelector('[data-testid="error-surface"]')).not.toBeNull();

    await act(async () => {
      root.render(tree('/glossary', <div data-testid="next-route">glossary</div>));
    });

    expect(container.querySelector('[data-testid="error-surface"]')).toBeNull();
    expect(container.querySelector('[data-testid="next-route"]')?.textContent).toBe('glossary');
  });

  it('stays on the recovery surface while resetKey is unchanged', async () => {
    const tree = (tick: number) => (
      <div data-tick={tick}>
        <ErrorBoundary boundary="route" resetKey="/lead-queue">
          <Thrower error={new Error('boom')} />
        </ErrorBoundary>
      </div>
    );
    await act(async () => {
      root.render(tree(0));
    });
    await act(async () => {
      root.render(tree(1));
    });

    expect(container.querySelectorAll('[data-testid="error-surface"]')).toHaveLength(1);
  });

  it('renders the page variant for the root boundary', async () => {
    await act(async () => {
      root.render(
        <ErrorBoundary boundary="root" variant="page">
          <Thrower error={new Error('shell crashed')} />
        </ErrorBoundary>,
      );
    });

    const surface = container.querySelector('[data-testid="error-surface"]');
    expect(surface?.className).toContain('error-surface--page');
    expect(surface?.getAttribute('data-error-boundary')).toBe('root');
    expect(surface?.textContent).toContain('The workspace hit an unexpected error');
  });

  it('only emits error-surface classes that the design system defines', async () => {
    await act(async () => {
      root.render(
        <ErrorBoundary boundary="root" variant="page" routeLabel="Lead Queue">
          <Thrower error={new Error('boom')} />
        </ErrorBoundary>,
      );
    });

    const emitted = new Set(
      Array.from(container.querySelectorAll('[class*="error-surface"]')).flatMap((el) =>
        Array.from(el.classList).filter((name) => name.startsWith('error-surface')),
      ),
    );
    const css = designCss();
    expect(emitted.size).toBeGreaterThanOrEqual(7);
    // (`--route` is a marker modifier with no rule of its own; the page
    // variant rendered here is the one that needs CSS.)
    for (const name of emitted) {
      expect(css, `.${name} is not defined in design-system/components`).toContain(`.${name} {`);
    }
  });

  it('reports the caught error once, message-free, with the boundary id', async () => {
    await act(async () => {
      root.render(
        <ErrorBoundary boundary="route">
          <Thrower error={new RangeError(BORROWER_BEARING_MESSAGE)} />
        </ErrorBoundary>,
      );
    });

    const reports = consoleError.mock.calls.filter((call) => call[0] === '[mip] client error');
    expect(reports).toHaveLength(1);
    expect(reports[0][1]).toEqual({
      source: 'caught',
      kind: 'render',
      errorName: 'RangeError',
      boundary: 'route',
      route: '/',
    });
    expect(JSON.stringify(reports[0][1])).not.toContain('B-0TESTBORROWER');
  });
});

describe('routeLabelForPath', () => {
  it('names the route and never echoes an id-bearing path', () => {
    expect(routeLabelForPath('/')).toBe('Home');
    expect(routeLabelForPath('/lead-queue?state=TX')).toBe('Lead Queue');
    expect(routeLabelForPath('/borrower-360/B-0TESTBORROWER')).toBe('Borrower 360');
    expect(routeLabelForPath('/offer-orchestrator/B-0TESTBORROWER#approval')).toBe('Offer Orchestrator');
    expect(routeLabelForPath('/data-estate/assets/lead_population')).toBe('Governed asset');
    expect(routeLabelForPath('/lead-queue-typo')).toBeNull();
    expect(routeLabelForPath('/nope')).toBeNull();
  });

  // Audit shell-08: this was a sixth hand-kept route-name table and had
  // drifted ("Segments", "Offer & Outreach", "Administration"); every served
  // route now reads its name from the registry.
  it('names every served route exactly as the route registry does', () => {
    for (const id of ROUTE_IDS) {
      const route: RouteDefinition = ROUTES[id];
      if (!route.chunk) continue;
      const concrete = route.pattern.replace(/:[A-Za-z]+/g, 'B-0123456789ABC');
      expect(routeLabelForPath(concrete), concrete).toBe(route.name);
    }
    expect(routeLabelForPath('/segment-intelligence')).toBe('Segment Intelligence');
    expect(routeLabelForPath('/admin-config')).toBe('Admin');
  });
});
