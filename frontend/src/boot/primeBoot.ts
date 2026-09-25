/**
 * The boot module (audit bundle-02): a second build entry, emitted as
 * `assets/boot-<hash>.js` and placed in the built HTML immediately before the
 * entry script (lib/bootModulePlugin). It starts the four boot reads that do
 * not write audit rows while the entry chunk and its vendor chunks are still
 * downloading, instead of after they have downloaded, parsed and mounted:
 *
 *   session    GET /api/v1/session                 redirect: manual (like apiTransport)
 *   options    GET /api/v1/config/options          redirect: manual (like apiTransport)
 *   footprint  GET /api/v1/config/footprint        plain fetch (like FootprintProvider)
 *   health     GET /api/v1/health?idle_s=0         redirect: manual; idle_s=0 is the
 *                                                  first poll's own hint
 *
 * The app side (lib/bootPrime) consumes each primed Response once, and only a
 * 2xx JSON one; anything else falls through to the real request, so the error
 * mapping is the app's. Audit-writing reads (leads, borrower and proof reads,
 * outreach drafts, offer recommendations) are never primed.
 *
 * Rules this file keeps (lib/bootModulePlugin and the build manifest check
 * enforce them):
 *   - it IMPORTS NOTHING and exports only types. The shared `_apiPaths-*.js`
 *     chunk would become a dependency the HTML has to fetch first, so the
 *     paths are literals, pinned to `apiPath(...)` by primeBoot.test.ts;
 *     lib/bootPrime imports the types below with `import type`, which is
 *     erased, so no chunk ever imports this one.
 *   - the health prime is skipped while the tab is hidden or offline, as
 *     HealthProvider's own first probe would be;
 *   - every stored promise has a no-op catch, so a failed prime never
 *     reaches the client error log as an unhandled rejection;
 *   - everything is exposed on ONE frozen global, `window.__MIP_BOOT__`;
 *   - the whole module is inside try/catch: a boot failure only means the
 *     app makes the requests itself.
 */

export type BootReadName = 'session' | 'options' | 'footprint' | 'health';

export interface BootRead {
  /** The primed fetch; a no-op catch is attached, so awaiting it may reject. */
  readonly response: Promise<Response>;
  /** `performance.now()` when the fetch started. */
  readonly startedAt: number;
  /** `performance.now()` when it settled (either way); null while in flight. */
  readonly settledAt: number | null;
}

export interface BootPrime {
  readonly reads: Readonly<Partial<Record<BootReadName, BootRead>>>;
}

/** The one global the boot module writes; lib/bootPrime reads the same literal. */
const BOOT_GLOBAL = '__MIP_BOOT__';

const BOOT_READS = {
  session: { url: '/api/v1/session', init: { redirect: 'manual' } },
  options: { url: '/api/v1/config/options', init: { redirect: 'manual' } },
  footprint: { url: '/api/v1/config/footprint', init: {} },
  health: { url: '/api/v1/health?idle_s=0', init: { redirect: 'manual' } },
} as const satisfies Record<BootReadName, { url: string; init: RequestInit }>;

function start(url: string, init: RequestInit): BootRead {
  const startedAt = performance.now();
  let settledAt: number | null = null;
  const response = fetch(url, init);
  const settle = () => {
    settledAt = performance.now();
  };
  response.then(settle, settle);
  response.catch(() => undefined);
  return Object.freeze({
    response,
    startedAt,
    get settledAt() {
      return settledAt;
    },
  });
}

function mayPrimeHealth(): boolean {
  return document.visibilityState !== 'hidden' && navigator.onLine !== false;
}

try {
  const reads: Partial<Record<BootReadName, BootRead>> = {};
  for (const name of Object.keys(BOOT_READS) as BootReadName[]) {
    if (name === 'health' && !mayPrimeHealth()) continue;
    reads[name] = start(BOOT_READS[name].url, BOOT_READS[name].init);
  }
  const prime: BootPrime = Object.freeze({ reads: Object.freeze(reads) });
  Object.defineProperty(window, BOOT_GLOBAL, { value: prime, writable: false, configurable: true, enumerable: false });
} catch {
  // No prime: the app makes the four requests itself.
}
