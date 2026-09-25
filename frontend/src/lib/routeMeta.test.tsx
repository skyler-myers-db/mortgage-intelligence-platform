// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the app.tsx route table as text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import type { SessionResponse } from '../types';

declare const process: { cwd(): string };

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ lastBorrowerId: null }),
}));

vi.mock('./api', () => ({
  api: { session: vi.fn() },
}));

import { RouteNav } from '../components/layout/RouteNav';
import { Icon } from '../components/Icon';
import { COMMAND_ACTIONS } from '../components/command/commandActions';
import { routePreloaders } from './routePreloaders';
import { MASKED_BORROWER_ID_RE as GENIE_MASKED_BORROWER_ID_RE } from './genieCellLinks';
import {
  MASKED_BORROWER_ID_RE,
  NAV_ROUTE_IDS,
  NOT_FOUND_ROUTE_META,
  PALETTE_ROUTE_IDS,
  ROUTES,
  ROUTE_IDS,
  ROUTE_META,
  documentTitleFor,
  indexPathOf,
  resolveRouteMeta,
  routePageLabel,
} from './routeMeta';

/**
 * routeMeta is the one table the document title and the route announcer read.
 * Audit 2026-09-21 `shell-08` found five hand-kept route tables drifting. This
 * file pins the other tables TO routeMeta, so adding a route or renaming a
 * page fails here instead of shipping a tab titled "Page not found" or a nav
 * chip that disagrees with the page it opens.
 */

describe('documentTitleFor / routePageLabel', () => {
  it.each([
    ['/', 'Home'],
    ['/analytics', 'Analytics'],
    ['/portfolio-builder', 'Portfolio Builder'],
    ['/segment-intelligence', 'Segment Intelligence'],
    ['/lead-queue', 'Lead Queue'],
    ['/borrower-360', 'Borrower 360'],
    ['/offer-orchestrator', 'Offer Orchestrator'],
    ['/ask-genie', 'Ask Genie'],
    ['/glossary', 'Glossary'],
    ['/admin-config', 'Admin'],
    ['/data-estate/assets/gold.lead_population', 'Governed asset'],
  ])('%s is titled "%s · Mortgage Intelligence Platform"', (pathname, name) => {
    expect(routePageLabel(pathname)).toBe(name);
    expect(documentTitleFor(pathname)).toBe(`${name} · Mortgage Intelligence Platform`);
  });

  it('adds the masked borrower id on Borrower 360 and Offer detail routes', () => {
    expect(documentTitleFor('/borrower-360/B-0123456789ABC')).toBe(
      'Borrower 360 · B-0123456789ABC · Mortgage Intelligence Platform',
    );
    expect(documentTitleFor('/offer-orchestrator/B-0123456789ABC')).toBe(
      'Offer Orchestrator · B-0123456789ABC · Mortgage Intelligence Platform',
    );
  });

  it('drops a param that is not a masked borrower id rather than echoing URL text', () => {
    expect(routePageLabel('/borrower-360/jane%20doe')).toBe('Borrower 360');
    expect(routePageLabel('/borrower-360/b-0123456789abc')).toBe('Borrower 360');
    expect(routePageLabel('/offer-orchestrator/B-012')).toBe('Offer Orchestrator');
  });

  it('resolves unknown paths to the not-found meta', () => {
    expect(resolveRouteMeta('/nope')).toBe(NOT_FOUND_ROUTE_META);
    expect(resolveRouteMeta('/lead-queue/extra')).toBe(NOT_FOUND_ROUTE_META);
    expect(documentTitleFor('/nope')).toBe('Page not found · Mortgage Intelligence Platform');
  });

  it('names the legacy outreach redirects after their destination', () => {
    expect(routePageLabel('/outreach-composer')).toBe('Lead Queue');
    expect(routePageLabel('/outreach-composer/B-0123456789ABC')).toBe('Lead Queue');
  });

  it('shares the canonical masked-id shape with the Genie cell links', () => {
    expect(MASKED_BORROWER_ID_RE.source).toBe(GENIE_MASKED_BORROWER_ID_RE.source);
    expect(MASKED_BORROWER_ID_RE.flags).toBe(GENIE_MASKED_BORROWER_ID_RE.flags);
  });
});

describe('route tables stay pinned to routeMeta', () => {
  it('app.tsx serves exactly the registry: no hand-written route path, one element per route id', () => {
    // Resolved from the Vitest root (the `frontend` package), like src/test/designCss.ts.
    const appSource: string = readFileSync(join(process.cwd(), 'src', 'app.tsx'), 'utf8');
    // The only literal path left is the catch-all; every other <Route> is
    // generated from ROUTE_IDS with its pattern read from ROUTES.
    const literalPaths = [...appSource.matchAll(/<Route\s[^>]*path="([^"]+)"/g)].map((match) => match[1]);
    expect(literalPaths).toEqual(['*']);
    expect(appSource).toMatch(/ROUTE_IDS\.map\(\(id\) => \(\s*<Route key=\{id\} path=\{ROUTES\[id\]\.pattern\} element=\{ROUTE_ELEMENTS\[id\]\} \/>/);
    const elementTable = appSource.match(/const ROUTE_ELEMENTS = \{([\s\S]*?)\} satisfies Record<RouteId, ReactElement>;/);
    expect(elementTable, 'app.tsx keeps one typed element table').not.toBeNull();
    const elementIds = [...(elementTable?.[1] ?? '').matchAll(/^\s{2}(\w+):/gm)].map((match) => match[1]);
    expect(elementIds).toEqual(ROUTE_IDS);
    expect(ROUTE_IDS.length).toBeGreaterThanOrEqual(13);
  });

  it('every registered pattern is unique and every redirect lands on a served route', () => {
    const patterns = ROUTE_META.map((meta) => meta.pattern);
    expect(new Set(patterns).size).toBe(patterns.length);
    for (const meta of ROUTE_META) {
      if (!meta.redirectTo) {
        expect(meta.chunk, `${meta.pattern} renders a route module`).toBeDefined();
        continue;
      }
      const destination = resolveRouteMeta(meta.redirectTo);
      expect(destination.chunk, `${meta.pattern} redirects to a served route`).toBeDefined();
      expect(meta.name, 'a redirect is named after its destination').toBe(destination.name);
    }
  });

  it('every route module is preloadable by its index path', () => {
    const served = ROUTE_META.filter((meta) => meta.chunk).map((meta) => indexPathOf(meta.pattern));
    expect(Object.keys(routePreloaders).sort()).toEqual([...new Set(served)].sort());
    expect(indexPathOf('/borrower-360/:id')).toBe('/borrower-360');
    expect(indexPathOf('/data-estate/assets/:assetKey')).toBe('/data-estate/assets');
  });

  it('command palette route labels are the routeMeta page names', () => {
    const routeActions = COMMAND_ACTIONS.flatMap((action) =>
      action.target.kind === 'route' ? [{ label: action.label, to: action.target.to }] : [],
    );
    expect(routeActions.length).toBeGreaterThanOrEqual(10);
    for (const { label, to } of routeActions) {
      expect({ to, label }).toEqual({ to, label: resolveRouteMeta(to).name });
    }
    expect(routeActions.map((action) => action.to)).toEqual(PALETTE_ROUTE_IDS.map((id) => ROUTES[id].pattern));
  });

  it('route-nav chip labels are the routeMeta nav labels', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    queryClient.setQueryData<SessionResponse>(['session', 'access'], {
      can_access_admin: true,
      can_approve: true,
      actor_email: null,
    });
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/']}>
          <RouteNav />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const chips = [...html.matchAll(/<a[^>]*href="([^"]+)"[^>]*>.*?<span class="route-nav__label">([^<]+)<\/span><\/a>/g)]
      .map((match) => ({ to: match[1], label: match[2] }));
    expect(chips.length).toBe(10);
    expect(chips.length).toBe(NAV_ROUTE_IDS.length);
    for (const { to, label } of chips) {
      expect({ to, label }).toEqual({ to, label: resolveRouteMeta(to).navLabel });
    }
  });

  /**
   * Audit shell-08 icon drift: `flow` was Analytics in the nav but Lead Queue
   * in the palette, and `user` / `doc` swapped between Leads, Borrower 360 and
   * Glossary. Each route now has ONE icon: the one the nav chip draws is the
   * one the palette row names.
   */
  it('a route shows the same icon in the nav chip and the command palette', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    queryClient.setQueryData<SessionResponse>(['session', 'access'], {
      can_access_admin: true,
      can_approve: true,
      actor_email: null,
    });
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/']}>
          <RouteNav />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const chipIcons = new Map(
      [...html.matchAll(/<a[^>]*href="([^"]+)"[^>]*>(<svg[\s\S]*?<\/svg>)<span class="route-nav__label">/g)]
        .map((match) => [match[1], match[2]] as const),
    );
    const paletteRoutes = COMMAND_ACTIONS.flatMap((action) =>
      action.target.kind === 'route' ? [{ to: action.target.to, icon: action.icon }] : [],
    );
    expect(paletteRoutes.length).toBe(10);
    for (const { to, icon } of paletteRoutes) {
      const drawn = chipIcons.get(to);
      expect(drawn, `the nav has a chip for ${to}`).toBeDefined();
      expect(drawn, `${to} draws the palette's "${icon}" icon`).toBe(renderToStaticMarkup(<Icon name={icon} size={12} />));
    }
    const icons = paletteRoutes.map((route) => route.icon);
    expect(new Set(icons).size, 'no two destinations share an icon').toBe(icons.length);
  });
});
