/**
 * @vitest-environment happy-dom
 *
 * useRoutePending (shell-05 remainder): true while a data-router navigation
 * is held on a route whose chunk is still loading, false once it paints, and
 * always false without a data router.
 */
import { act, lazy, Suspense } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Outlet, RouterProvider, createMemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useRoutePending } from './useRoutePending';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Probe() {
  const pending = useRoutePending();
  const { pathname } = useLocation();
  return <output data-testid="probe">{`${pathname} pending=${pending}`}</output>;
}

function Shell() {
  return (
    <>
      <Probe />
      <Suspense fallback={<p data-testid="fallback">loading</p>}>
        <Outlet />
      </Suspense>
    </>
  );
}

describe('useRoutePending', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
  });

  const probe = () => container.querySelector('[data-testid="probe"]')?.textContent;
  const flush = () => act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });

  it('is true while a data-router navigation is held on a loading chunk, and false once it paints', async () => {
    let release: () => void = () => undefined;
    const chunk = new Promise<{ default: () => React.ReactElement }>((resolve) => {
      release = () => resolve({ default: () => <h1>Glossary</h1> });
    });
    const Held = lazy(() => chunk);
    const router = createMemoryRouter(
      [{ path: '/', element: <Shell />, children: [{ index: true, element: <h1>Home</h1> }, { path: 'glossary', element: <Held /> }] }],
      { initialEntries: ['/'] },
    );
    await act(async () => {
      root.render(<RouterProvider router={router} />);
    });
    expect(probe()).toBe('/ pending=false');

    await act(async () => {
      void router.navigate('/glossary');
    });
    await flush();
    // Held: the committed location is still Home, and the hook says pending.
    expect(container.querySelector('h1')?.textContent).toBe('Home');
    expect(container.querySelector('[data-testid="fallback"]')).toBeNull();
    expect(probe()).toBe('/ pending=true');

    await act(async () => {
      release();
      await chunk;
    });
    await flush();
    expect(container.querySelector('h1')?.textContent).toBe('Glossary');
    expect(probe()).toBe('/glossary pending=false');
  });

  it('is always false without a data router', async () => {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={['/lead-queue']}>
          <Probe />
        </MemoryRouter>,
      );
    });
    expect(probe()).toBe('/lead-queue pending=false');
  });
});
