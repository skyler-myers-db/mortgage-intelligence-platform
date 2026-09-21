import { matchPath } from 'react-router';

/**
 * Route meta — the one table that says what each route is CALLED.
 *
 * Audit 2026-09-21 (`shell-08`, `critic-v1`, `a11y-03`): every tab, history
 * entry and bookmark read "Mortgage Intelligence Platform" because nothing
 * mapped a path to a page name, while five hand-kept route tables (app.tsx,
 * RouteNav, Topbar, commandActions, routePreloaders) each spelled the names
 * their own way. The document title and the route announcer read THIS table;
 * `routeMeta.test.ts` pins the nav-chip and command-palette labels to it and
 * pins the patterns to the `<Route path>` list in `app.tsx`, so a new route or
 * a renamed page fails a test instead of drifting silently.
 *
 * `name` is the full page name (document title, screen-reader announcement,
 * command palette). `navLabel` is the short chip label in the route nav.
 */

export const PRODUCT_NAME = 'Mortgage Intelligence Platform';

/**
 * Canonical masked borrower id (CLAUDE.md naming rules). Kept here rather than
 * imported from `genieCellLinks` so the shell does not pull a lazy Genie
 * module into the initial chunk; `routeMeta.test.ts` asserts the two patterns
 * stay identical.
 */
export const MASKED_BORROWER_ID_RE = /^B-[0-9A-Z]{13}$/;

export interface RouteMeta {
  /** Route pattern, spelled exactly as the `<Route path>` in `app.tsx`. */
  pattern: string;
  /** Full page name: document title, announcer, command palette label. */
  name: string;
  /** Short label used by the route-nav chip strip. */
  navLabel: string;
  /**
   * Detail routes whose `:id` param is a masked borrower id. The id is added
   * to the title only when it matches `MASKED_BORROWER_ID_RE`, so arbitrary
   * URL text never reaches the tab title or the live region.
   */
  detail?: 'borrower';
}

export const ROUTE_META: readonly RouteMeta[] = [
  { pattern: '/', name: 'Home', navLabel: 'Home' },
  { pattern: '/analytics', name: 'Analytics', navLabel: 'Analytics' },
  { pattern: '/data-estate/assets/:assetKey', name: 'Governed asset', navLabel: 'Governed asset' },
  { pattern: '/portfolio-builder', name: 'Portfolio Builder', navLabel: 'Portfolio' },
  { pattern: '/segment-intelligence', name: 'Segment Intelligence', navLabel: 'Segments' },
  { pattern: '/lead-queue', name: 'Lead Queue', navLabel: 'Leads' },
  { pattern: '/borrower-360', name: 'Borrower 360', navLabel: 'Borrower 360' },
  { pattern: '/borrower-360/:id', name: 'Borrower 360', navLabel: 'Borrower 360', detail: 'borrower' },
  { pattern: '/glossary', name: 'Glossary', navLabel: 'Glossary' },
  { pattern: '/offer-orchestrator', name: 'Offer Orchestrator', navLabel: 'Offer' },
  { pattern: '/offer-orchestrator/:id', name: 'Offer Orchestrator', navLabel: 'Offer', detail: 'borrower' },
  { pattern: '/ask-genie', name: 'Ask Genie', navLabel: 'Ask Genie' },
  { pattern: '/admin-config', name: 'Admin', navLabel: 'Admin' },
  // Legacy outreach paths redirect to the Lead Queue (see app.tsx). Naming
  // them keeps the title from flashing "Page not found" for the one commit
  // before the redirect lands.
  { pattern: '/outreach-composer', name: 'Lead Queue', navLabel: 'Leads' },
  { pattern: '/outreach-composer/:id', name: 'Lead Queue', navLabel: 'Leads' },
];

export const NOT_FOUND_ROUTE_META: RouteMeta = {
  pattern: '*',
  name: 'Page not found',
  navLabel: 'Not found',
};

interface ResolvedRoute {
  meta: RouteMeta;
  params: Readonly<Record<string, string | undefined>>;
}

function resolveRoute(pathname: string): ResolvedRoute {
  for (const meta of ROUTE_META) {
    const match = matchPath({ path: meta.pattern, end: true }, pathname);
    if (match) return { meta, params: match.params };
  }
  return { meta: NOT_FOUND_ROUTE_META, params: {} };
}

/** Meta for a concrete pathname; unknown paths resolve to the not-found meta. */
export function resolveRouteMeta(pathname: string): RouteMeta {
  return resolveRoute(pathname).meta;
}

/**
 * Page label for a concrete pathname: the page name, plus the masked borrower
 * id on detail routes ("Borrower 360 · B-0123456789ABC"). This is what the
 * route announcer reads out and what prefixes the document title.
 */
export function routePageLabel(pathname: string): string {
  const { meta, params } = resolveRoute(pathname);
  if (meta.detail === 'borrower') {
    const id = (params.id ?? '').trim();
    if (MASKED_BORROWER_ID_RE.test(id)) return `${meta.name} · ${id}`;
  }
  return meta.name;
}

/** `<Page> · Mortgage Intelligence Platform`. */
export function documentTitleFor(pathname: string): string {
  return `${routePageLabel(pathname)} · ${PRODUCT_NAME}`;
}
