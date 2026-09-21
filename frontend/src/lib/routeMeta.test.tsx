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
import { COMMAND_ACTIONS } from '../components/command/commandActions';
import { MASKED_BORROWER_ID_RE as GENIE_MASKED_BORROWER_ID_RE } from './genieCellLinks';
import {
  MASKED_BORROWER_ID_RE,
  NOT_FOUND_ROUTE_META,
  ROUTE_META,
  documentTitleFor,
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
  it('names every <Route path> declared in app.tsx', () => {
    // Resolved from the Vitest root (the `frontend` package), like src/test/designCss.ts.
    const appSource: string = readFileSync(join(process.cwd(), 'src', 'app.tsx'), 'utf8');
    const declared = [...appSource.matchAll(/<Route\s+path="([^"]+)"/g)]
      .map((match) => match[1])
      .filter((path) => path !== '*');
    expect(declared.length).toBeGreaterThanOrEqual(13);

    const named = new Set(ROUTE_META.map((meta) => meta.pattern));
    expect(declared.filter((path) => !named.has(path))).toEqual([]);
    // ...and routeMeta names nothing the router does not serve.
    expect([...named].filter((pattern) => !declared.includes(pattern))).toEqual([]);
  });

  it('command palette route labels are the routeMeta page names', () => {
    const routeActions = COMMAND_ACTIONS.flatMap((action) =>
      action.target.kind === 'route' ? [{ label: action.label, to: action.target.to }] : [],
    );
    expect(routeActions.length).toBeGreaterThanOrEqual(10);
    for (const { label, to } of routeActions) {
      expect({ to, label }).toEqual({ to, label: resolveRouteMeta(to).name });
    }
  });

  it('route-nav chip labels are the routeMeta nav labels', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    queryClient.setQueryData<SessionResponse>(['session', 'access'], { can_access_admin: true });
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/']}>
          <RouteNav />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const chips = [...html.matchAll(/<a[^>]*href="([^"]+)"[^>]*>.*?<span class="filter__value">([^<]+)<\/span><\/a>/g)]
      .map((match) => ({ to: match[1], label: match[2] }));
    expect(chips.length).toBe(10);
    for (const { to, label } of chips) {
      expect({ to, label }).toEqual({ to, label: resolveRouteMeta(to).navLabel });
    }
  });
});
