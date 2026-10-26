/**
 * Error-telemetry scenarios (lane w2-error-telemetry: audit stack-01,
 * shell-01, states-01, quality-01, delivery-v3, shell-05). Nothing here is
 * registered by default (no registry.ts line): each test applies the scenario
 * it needs with `mockApi.register()` through the helpers below.
 */
import type { Page, Route } from '@playwright/test';
import type { ConfigOptions, SessionResponse } from '../../../../src/types';
import type { LineageManifestResponse } from '../../../../src/types/lineage';
import { json, type FixtureEntry, type MockApi } from '../mockApi';
import { dataEstateFixtures } from './dataEstate';
import { CONFIG_OPTIONS, SESSION } from './shell';

/**
 * The evidence-drawer crash hook. `families` is a required array in the
 * server contract (backend lineage manifest schema), so `{families: null}`
 * is a payload the server CANNOT produce. It is used deliberately: the
 * EvidenceDrawer reads `lineageQuery.data?.families.find(...)` (about
 * EvidenceDrawer.tsx:255), so this payload throws a TypeError inside the
 * drawer once the manifest arrives, which is what the drawer's panel
 * boundary must contain. If that line ever gains a null-guard, replace this
 * hook with another drawer-only render throw; do not delete cases A and E.
 * The lineage manifest read is not audited.
 *
 * Returns `heal`, which puts the registry's own manifest fixture
 * (data/dataEstate.ts) back, so a test can prove the drawer recovers once
 * the payload is fixed.
 */
export function crashTheEvidenceDrawer(mockApi: MockApi): () => void {
  const healthy = registeredManifestFixture();
  const unproducible = { schema_version: 1, manifest_path: 'fixture', families: null };
  mockApi.register<LineageManifestResponse>('GET', '/api/lineage/manifest', () =>
    json(unproducible as unknown as LineageManifestResponse),
  );
  return () => mockApi.register('GET', healthy.pattern, healthy.handler);
}

/** The default-registry fixture for GET /api/lineage/manifest: the healthy payload. */
function registeredManifestFixture(): FixtureEntry {
  const entry = dataEstateFixtures.find(
    (candidate) => candidate.method === 'GET' && candidate.pattern === '/api/lineage/manifest',
  );
  if (!entry) throw new Error('data/dataEstate.ts no longer registers GET /api/lineage/manifest');
  return entry;
}

/** The Server-Timing header w2-warehouse-delivery emits (its grammar, faked here). */
export const CONFIG_SERVER_TIMING = 'cache;desc=hit, warehouse;dur=0, total;dur=4.2';

export interface WireRumEvent {
  metric: string;
  value: number;
  rating: string;
  route: string;
  navigation_type?: string | null;
  details?: Record<string, string | number | boolean | null>;
}

export interface RumCapture {
  /** Raw request bodies of every POST /api/telemetry/rum, in arrival order. */
  bodies: string[];
  events: () => WireRumEvent[];
}

/**
 * Turn RUM on (it is off by default: `rum_enabled: false`) with the
 * Server-Timing header on the config read, and capture every telemetry POST.
 * The sink replies 202 like the real, log-only endpoint.
 */
export function enableRum(mockApi: MockApi): RumCapture {
  const bodies: string[] = [];
  mockApi.register<ConfigOptions>('GET', '/api/config/options', () =>
    json({ ...CONFIG_OPTIONS, rum_enabled: true }, { headers: { 'Server-Timing': CONFIG_SERVER_TIMING } }),
  );
  // The session carries the same setting (delivery-07), and the app reads it first.
  mockApi.register<SessionResponse>('GET', '/api/session', () => json({ ...SESSION, rum_enabled: true }));
  mockApi.register<{ accepted: number; enabled: boolean }>('POST', '/api/telemetry/rum', ({ body }) => {
    bodies.push(JSON.stringify(body));
    const events = (body as { events?: unknown[] } | null)?.events ?? [];
    return json({ accepted: events.length, enabled: true }, { status: 202 });
  });
  return {
    bodies,
    events: () => bodies.flatMap((text) => (JSON.parse(text) as { events: WireRumEvent[] }).events),
  };
}

/**
 * Make the keepalive fetch carry every RUM flush (sendBeacon answers false),
 * and sample this tab for api_call (`mip.rumApiSample` = '1'). Call before
 * the first navigation.
 */
export async function routeRumThroughFetch(page: Page): Promise<void> {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'sendBeacon', { configurable: true, value: () => false });
    try {
      window.sessionStorage.setItem('mip.rumApiSample', '1');
    } catch {
      // about:blank has no storage; the app document seeds it.
    }
  });
}

/** Answer every request for one hashed chunk 404, as a server does after a deploy retired it. */
export async function retireChunk(page: Page, chunkPrefix: string): Promise<void> {
  await page.route(new RegExp(`/assets/${chunkPrefix}-[^/]+\\.js$`), (route: Route) =>
    route.fulfill({ status: 404, contentType: 'text/plain', body: 'retired chunk (fixture)' }),
  );
}

const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * Fail every request for one hashed chunk until `restore()`. A lazy chunk is
 * fetched twice: Vite's preload helper adds a `<link rel=modulepreload>` for
 * it, then `import()` loads it. Chromium 153 (Playwright 1.63) and the WebKit
 * build here keep a failed module fetch, so failing only the FIRST request
 * failed the import too; Chromium 156 (Playwright 1.64) does not, and the
 * import fetched its own copy (W5e CI run 40311237233). Keep the chunk down
 * until the test has seen the failure it causes.
 */
export async function takeChunkDown(page: Page, chunkPrefix: string): Promise<{ aborted: () => number; restore: () => void }> {
  let down = true;
  let aborted = 0;
  await page.route(new RegExp(`/assets/${escapeRegExp(chunkPrefix)}-[^/]+\\.js$`), async (route: Route) => {
    if (!down) {
      await route.continue();
      return;
    }
    aborted += 1;
    await route.abort();
  });
  return {
    aborted: () => aborted,
    restore: () => {
      down = false;
    },
  };
}

/**
 * Give one chunk's `<link rel=modulepreload>` a module-map key of its own (a
 * URL fragment: the request is the same), so a failed preload never fails
 * the import: Chromium 156's behaviour, emulated on every engine and
 * version. Install before the navigation.
 */
export async function fetchModulePreloadApart(page: Page, chunkPrefix: string): Promise<void> {
  await page.addInitScript((prefix) => {
    const append = Node.prototype.appendChild;
    Node.prototype.appendChild = function appendChild<T extends Node>(this: Node, node: T): T {
      if (node instanceof HTMLLinkElement && node.rel === 'modulepreload' && node.href.includes(`/assets/${prefix}-`)) {
        node.href = `${node.href}#modulepreload`;
      }
      return append.call(this, node) as T;
    };
  }, chunkPrefix);
}

/** Hold one hashed chunk until `release()`; returns the release function. */
export async function holdChunk(page: Page, chunkPrefix: string): Promise<() => void> {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(new RegExp(`/assets/${chunkPrefix}-[^/]+\\.js$`), async (route: Route) => {
    await gate;
    await route.continue();
  });
  return release;
}

/**
 * Spend the one guarded stale-chunk reload for this tab (src/lib/
 * staleChunkRecovery.ts), so a retired chunk renders its surface instead of
 * reloading. Uses the page's own (frozen) clock.
 */
export async function spendStaleChunkReload(page: Page): Promise<void> {
  await page.evaluate(() => window.sessionStorage.setItem('mip.staleChunkReloadAt', String(Date.now())));
}
