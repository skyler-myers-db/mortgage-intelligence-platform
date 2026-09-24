import { matchPath } from 'react-router';
import type { IconName } from '../components/Icon';

/**
 * Route registry — the ONE typed table of what the app serves.
 *
 * Audit 2026-09-21 (`shell-08`, `critic-v1`, `a11y-03`): five hand-kept route
 * tables (app.tsx, RouteNav, Topbar, commandActions, routePreloaders) each
 * spelled the routes their own way, so the nav and the palette disagreed on
 * icons (`flow` was Analytics in the nav and Lead Queue in the palette; `user`
 * and `doc` swapped the same way) and every tab read "Mortgage Intelligence
 * Platform". Every one of those tables now DERIVES from `ROUTES`:
 *
 *   - app.tsx maps `ROUTE_IDS` to `<Route path={ROUTES[id].pattern}>`, with an
 *     element table typed `satisfies Record<RouteId, ...>`, so a route with no
 *     element is a type error;
 *   - RouteNav renders `NAV_ROUTE_IDS`; the command palette builds its route
 *     actions from `PALETTE_ROUTE_IDS`; both take `name` / `navLabel` / `icon`
 *     from here, so one route has one icon everywhere;
 *   - routePreloaders keys its preload map by each route's `IndexPath`;
 *   - the Topbar breadcrumbs, the document title and the route announcer
 *     resolve a pathname through `resolveRouteMeta`.
 *
 * `routeMeta.test.tsx` pins the derived tables against this one.
 */

export const PRODUCT_NAME = 'Mortgage Intelligence Platform';

/**
 * Canonical masked borrower id (CLAUDE.md naming rules). Kept here rather than
 * imported from `genieCellLinks` so the shell does not pull a lazy Genie
 * module into the initial chunk; `routeMeta.test.ts` asserts the two patterns
 * stay identical.
 */
export const MASKED_BORROWER_ID_RE = /^B-[0-9A-Z]{13}$/;

/** An absolute in-app path or route pattern: `/` or `/<segment>...`. */
export type AppPath = '/' | `/${string}`;

/** The index path of a route pattern: everything before its first `/:param`. */
export type IndexPath<P extends string> = P extends `${infer Base}/:${string}` ? Base : P;

/** The lazy route module that renders a route (see routePreloaders.ts). */
export type RouteChunk =
  | 'home' | 'analytics' | 'asset' | 'portfolio' | 'segments' | 'leads'
  | 'borrower' | 'glossary' | 'offer' | 'askGenie' | 'admin';

export interface RouteDefinition {
  /** Route pattern, served verbatim as the `<Route path>` in `app.tsx`. */
  pattern: AppPath;
  /** Full page name: document title, announcer, command palette label. */
  name: string;
  /** Short label used by the route-nav chip strip. */
  navLabel: string;
  /** The one icon this route shows in the nav chips and the command palette. */
  icon: IconName;
  /**
   * Detail routes whose `:id` param is a masked borrower id. The id is added
   * to the title only when it matches `MASKED_BORROWER_ID_RE`, so arbitrary
   * URL text never reaches the tab title or the live region.
   */
  detail?: 'borrower';
  /** The lazy module that renders the route. Absent on a redirect. */
  chunk?: RouteChunk;
  /** Legacy path: redirects to this registered pattern. */
  redirectTo?: AppPath;
}

export const ROUTES = {
  home: { pattern: '/', name: 'Home', navLabel: 'Home', icon: 'home', chunk: 'home' },
  analytics: { pattern: '/analytics', name: 'Analytics', navLabel: 'Analytics', icon: 'flow', chunk: 'analytics' },
  asset: {
    pattern: '/data-estate/assets/:assetKey', name: 'Governed asset', navLabel: 'Governed asset', icon: 'db', chunk: 'asset',
  },
  portfolio: {
    pattern: '/portfolio-builder', name: 'Portfolio Builder', navLabel: 'Portfolio', icon: 'target', chunk: 'portfolio',
  },
  segments: {
    pattern: '/segment-intelligence', name: 'Segment Intelligence', navLabel: 'Segments', icon: 'layers', chunk: 'segments',
  },
  leads: { pattern: '/lead-queue', name: 'Lead Queue', navLabel: 'Leads', icon: 'user', chunk: 'leads' },
  borrowerIndex: { pattern: '/borrower-360', name: 'Borrower 360', navLabel: 'Borrower 360', icon: 'doc', chunk: 'borrower' },
  borrower: {
    pattern: '/borrower-360/:id', name: 'Borrower 360', navLabel: 'Borrower 360', icon: 'doc', detail: 'borrower', chunk: 'borrower',
  },
  glossary: { pattern: '/glossary', name: 'Glossary', navLabel: 'Glossary', icon: 'info', chunk: 'glossary' },
  offerIndex: { pattern: '/offer-orchestrator', name: 'Offer Orchestrator', navLabel: 'Offer', icon: 'bolt', chunk: 'offer' },
  offer: {
    pattern: '/offer-orchestrator/:id', name: 'Offer Orchestrator', navLabel: 'Offer', icon: 'bolt', detail: 'borrower', chunk: 'offer',
  },
  askGenie: { pattern: '/ask-genie', name: 'Ask Genie', navLabel: 'Ask Genie', icon: 'sparkle', chunk: 'askGenie' },
  admin: { pattern: '/admin-config', name: 'Admin', navLabel: 'Admin', icon: 'settings', chunk: 'admin' },
  // Legacy outreach paths redirect to the Lead Queue (outreach drafting lives
  // inside the Offer Orchestrator). Naming them after their destination keeps
  // the title from flashing "Page not found" for the commit before the
  // redirect lands.
  legacyOutreach: { pattern: '/outreach-composer', name: 'Lead Queue', navLabel: 'Leads', icon: 'user', redirectTo: '/lead-queue' },
  legacyOutreachDetail: {
    pattern: '/outreach-composer/:id', name: 'Lead Queue', navLabel: 'Leads', icon: 'user', redirectTo: '/lead-queue',
  },
} as const satisfies Record<string, RouteDefinition>;

export type RouteId = keyof typeof ROUTES;
export type RoutePattern = (typeof ROUTES)[RouteId]['pattern'];

/** Registry order: the order app.tsx declares its `<Route>`s. */
export const ROUTE_IDS = Object.keys(ROUTES) as RouteId[];

/**
 * Route-nav chip order (the product flow: portfolio, segments, leads,
 * borrower, offer). Borrower 360 and Offer link to the last borrower's detail
 * route when there is one; Admin is shown only to an admitted actor.
 */
export const NAV_ROUTE_IDS = [
  'home', 'analytics', 'portfolio', 'segments', 'leads',
  'borrowerIndex', 'offerIndex', 'askGenie', 'glossary', 'admin',
] as const satisfies readonly RouteId[];

/** Command-palette "Navigate" order. */
export const PALETTE_ROUTE_IDS = [
  'home', 'portfolio', 'segments', 'leads', 'borrowerIndex',
  'offerIndex', 'analytics', 'askGenie', 'glossary', 'admin',
] as const satisfies readonly RouteId[];

export type NavRouteId = (typeof NAV_ROUTE_IDS)[number];
export type PaletteRouteId = (typeof PALETTE_ROUTE_IDS)[number];

/** `/borrower-360/<id>`: the dossier of one masked borrower. */
export function borrowerPath(borrowerId: string): `/borrower-360/${string}` {
  return `/borrower-360/${borrowerId}`;
}

/** `/offer-orchestrator/<id>`: the offer and outreach workspace of one borrower. */
export function offerPath(borrowerId: string): `/offer-orchestrator/${string}` {
  return `/offer-orchestrator/${borrowerId}`;
}

/** The index path of a pattern at runtime; `IndexPath` is its type. */
export function indexPathOf<P extends string>(pattern: P): IndexPath<P> {
  const param = pattern.indexOf('/:');
  return (param === -1 ? pattern : pattern.slice(0, param)) as IndexPath<P>;
}

/**
 * A registry entry as the shell resolves it from a pathname. `pattern` widens
 * to `string` here so callers can key plain lookups (genieContext's starter
 * table) by it; `ROUTES` keeps the literal pattern types.
 */
export interface RouteMeta extends Omit<RouteDefinition, 'pattern'> {
  id: RouteId | 'notFound';
  pattern: string;
}

export const ROUTE_META: readonly RouteMeta[] = ROUTE_IDS.map((id) => ({ id, ...ROUTES[id] }));

export const NOT_FOUND_ROUTE_META: RouteMeta = {
  id: 'notFound',
  pattern: '/*',
  name: 'Page not found',
  navLabel: 'Not found',
  icon: 'info',
};

interface ResolvedRoute {
  meta: RouteMeta;
  params: Readonly<Record<string, string | undefined>>;
}

export function resolveRoute(pathname: string): ResolvedRoute {
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

/** The masked borrower id of a borrower detail pathname, or null. */
export function maskedBorrowerIdFor(pathname: string): string | null {
  const { meta, params } = resolveRoute(pathname);
  if (meta.detail !== 'borrower') return null;
  const id = (params.id ?? '').trim();
  return MASKED_BORROWER_ID_RE.test(id) ? id : null;
}

/**
 * Page label for a concrete pathname: the page name, plus the masked borrower
 * id on detail routes ("Borrower 360 · B-0123456789ABC"). This is what the
 * route announcer reads out and what prefixes the document title.
 */
export function routePageLabel(pathname: string): string {
  const { name } = resolveRouteMeta(pathname);
  const id = maskedBorrowerIdFor(pathname);
  return id ? `${name} · ${id}` : name;
}

/** `<Page> · Mortgage Intelligence Platform`. */
export function documentTitleFor(pathname: string): string {
  return `${routePageLabel(pathname)} · ${PRODUCT_NAME}`;
}
