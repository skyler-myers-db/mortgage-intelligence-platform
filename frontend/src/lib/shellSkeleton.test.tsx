// @vitest-environment happy-dom

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppShell } from '../components/layout/AppShell';
import { RouteFallback } from '../components/layout/RouteFallback';
import { RouteNav } from '../components/layout/RouteNav';
import { installLocalStorage } from '../test/installLocalStorage';
import { SHELL_SKELETON_HTML, injectShellSkeleton, shellSkeleton } from './shellSkeleton';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * The pre-JS shell skeleton (audit bundle-10) is a copy of the shell's own
 * markup, so it is pinned to the REAL AppShell and RouteFallback: every grid
 * child it draws must be a direct `.app-shell` child in the real markup, the
 * route-nav band must be `.main`'s first element child, and the hero must use
 * RouteFallback's classes. A rename on either side fails here.
 */

function skeletonRoot(): HTMLElement {
  const host = document.createElement('div');
  host.innerHTML = SHELL_SKELETON_HTML;
  return host.firstElementChild as HTMLElement;
}

const classOf = (element: Element) => element.getAttribute('class') ?? '';

/** Class lists of every element under `root`, in document order. */
function classTree(root: Element): string[] {
  return [root, ...root.querySelectorAll('*')].map(classOf);
}

describe('shell skeleton', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    installLocalStorage();
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"not found"}', { status: 404 })));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    queryClient.clear();
    vi.unstubAllGlobals();
  });

  async function render(node: React.ReactNode): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/']}>{node}</MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it("draws the real shell's grid children, each a direct .app-shell child, with the route nav first in .main", async () => {
    await render(
      <AppShell>
        <RouteNav />
        <h1>Home</h1>
      </AppShell>,
    );
    const real = container.querySelector('.app-shell') as HTMLElement;
    const skeleton = skeletonRoot();

    expect(classOf(skeleton)).toBe('app-shell');
    const drawn = [...skeleton.children].map(classOf);
    expect(drawn).toEqual(['rail', 'topbar', 'main']);
    for (const name of drawn) {
      expect(real.querySelector(`:scope > .${name}`), `.app-shell > .${name} in the real AppShell`).not.toBeNull();
    }
    expect(classOf(skeleton.querySelector(':scope > .main')?.firstElementChild as Element)).toBe('route-nav');
    expect(classOf(real.querySelector(':scope > .main')?.firstElementChild as Element)).toBe('route-nav');
  });

  it("draws RouteFallback's text-free hero inside .main__content > .main__inner", async () => {
    await render(<RouteFallback />);
    const realHero = container.querySelector('.main__content > .main__inner > .proto-hero') as HTMLElement;
    const skeletonHero = skeletonRoot().querySelector(':scope > .main > .main__content > .main__inner > .proto-hero') as HTMLElement;

    expect(realHero).not.toBeNull();
    expect(skeletonHero).not.toBeNull();
    expect(classTree(skeletonHero)).toEqual(classTree(realHero));
    expect(classTree(skeletonHero)).toEqual([
      'proto-hero',
      '',
      'skeleton skeleton--eyebrow',
      'skeleton skeleton--title',
      'skeleton skeleton--lede',
    ]);
  });

  it('is aria-hidden, text-free, divs only, with no ids, roles, labels or style attributes', () => {
    const skeleton = skeletonRoot();
    const elements = [skeleton, ...skeleton.querySelectorAll('*')];

    expect(skeleton.getAttribute('aria-hidden')).toBe('true');
    expect(skeleton.textContent).toBe('');
    expect(new Set(elements.map((element) => element.tagName))).toEqual(new Set(['DIV']));
    for (const element of elements) {
      const attributes = [...element.attributes].map((attribute) => attribute.name);
      expect(attributes.filter((name) => name !== 'class' && !(element === skeleton && name === 'aria-hidden'))).toEqual([]);
    }
  });

  it('replaces exactly the empty #root, in dev and build alike', () => {
    const html = '<body>\n    <div id="root"></div>\n    <script type="module" src="/src/main.tsx"></script>\n  </body>';
    const out = injectShellSkeleton(html);

    expect(out).toBe(html.replace('<div id="root"></div>', `<div id="root">${SHELL_SKELETON_HTML}</div>`));
    expect(shellSkeleton().transformIndexHtml(html)).toBe(out);
    expect(() => injectShellSkeleton('<div id="root"><p>x</p></div>')).toThrow(/exactly once/);
    expect(() => injectShellSkeleton(`${html}${html}`)).toThrow(/exactly once/);
  });
});
