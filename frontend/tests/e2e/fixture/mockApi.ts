/**
 * Credential-free mock API for the fixture harness.
 *
 * One `page.route('**\/api/**')` handler answers every API call from a
 * registry keyed by `METHOD /api/path/:param`. Nothing is proxied to a real
 * backend, and this module is test-only: it lives under `frontend/tests/` and
 * is never imported from `frontend/src` (CLAUDE.md: no mock fallback in the
 * running app).
 *
 * Two rules keep fixtures honest:
 *
 *  1. An API call with NO registered fixture is recorded in `unregistered`
 *     and the hygiene fixture fails the test with the method and path. It is
 *     answered 501 (not a retryable 503) so the app neither retries nor
 *     shows a plausible "warming up" state that would hide fixture drift.
 *  2. Degraded states are opt-in per test through `degrade()`. A route never
 *     renders degraded by accident.
 */
import type { Page, Request, Route } from '@playwright/test';

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface FixtureRequest {
  method: HttpMethod;
  /** Version-normalized path: `/api/v1/leads` is matched as `/api/leads`. */
  path: string;
  url: URL;
  query: URLSearchParams;
  /** Named `:param` captures from the registered pattern, URI-decoded. */
  params: Record<string, string>;
  /** Parsed JSON request body, or null when absent / not JSON. */
  body: unknown;
}

export interface FixtureReply<T = unknown> {
  status?: number;
  headers?: Record<string, string>;
  body: T;
}

export type FixtureHandler = (request: FixtureRequest) => FixtureReply | Promise<FixtureReply>;

export interface FixtureEntry {
  method: HttpMethod;
  /** Express-style pattern, e.g. `/api/borrowers/:id/lifecycle`. */
  pattern: string;
  handler: FixtureHandler;
}

export interface DegradeOptions {
  status: number;
  body: unknown;
  headers?: Record<string, string>;
  /** Restrict the degraded state to one method. Default: every method. */
  method?: HttpMethod;
}

export interface ApiCall {
  method: string;
  path: string;
  search: string;
  status: number;
  outcome: 'fixture' | 'degraded' | 'unregistered';
}

/**
 * Typed reply helper. The explicit type argument is the point: fixture
 * modules write `json<PortfolioPreview>({...})` so `tsc` checks the payload
 * against the frontend's own response type.
 */
export function json<T>(body: T, init: Omit<FixtureReply<T>, 'body'> = {}): FixtureReply<T> {
  return { ...init, body };
}

/** Registry entry helper that keeps the payload type attached to the handler. */
export function fixture<T>(
  method: HttpMethod,
  pattern: string,
  handler: (request: FixtureRequest) => FixtureReply<T> | Promise<FixtureReply<T>>,
): FixtureEntry {
  return { method, pattern, handler };
}

/** The backend's retryable dependency-down body (`_dependency_down_handler`). */
export const WAREHOUSE_WARMING_UP: DegradeOptions = {
  status: 503,
  headers: { 'Retry-After': '1' },
  body: {
    retryable: true,
    dependency: 'warehouse',
    reason: 'warming_up',
    detail: 'SQL warehouse is warming up after idle auto-suspend (fixture degraded state).',
    correlation_id: 'fixture-correlation-0001',
  },
};

export function normalizeApiPath(pathname: string): string {
  return pathname.replace(/^\/api\/v\d+(?=\/|$)/, '/api');
}

interface CompiledPattern {
  regex: RegExp;
  paramNames: string[];
  /** Static segment count; higher wins so `/borrowers/search` beats `/borrowers/:id`. */
  specificity: number;
}

function compilePattern(pattern: string): CompiledPattern {
  if (!pattern.startsWith('/api/')) {
    throw new Error(`Fixture pattern must start with /api/ (got "${pattern}")`);
  }
  const paramNames: string[] = [];
  let specificity = 0;
  const source = pattern
    .split('/')
    .map((segment) => {
      if (segment.startsWith(':')) {
        paramNames.push(segment.slice(1));
        return '([^/]+)';
      }
      specificity += 1;
      return segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    })
    .join('/');
  return { regex: new RegExp(`^${source}$`), paramNames, specificity };
}

function registryKey(method: HttpMethod, pattern: string): string {
  return `${method} ${pattern}`;
}

function parseBody(request: Request): unknown {
  try {
    return request.postDataJSON() ?? null;
  } catch {
    return null;
  }
}

interface DegradeRule {
  matches: (path: string) => boolean;
  options: DegradeOptions;
}

export class MockApi {
  readonly calls: ApiCall[] = [];
  readonly unregistered: ApiCall[] = [];
  /** Full URLs answered by `degrade()`; hygiene allows their console noise. */
  readonly degradedUrls = new Set<string>();
  /** Full URLs answered 501; hygiene reports them as `unregistered-api` instead. */
  readonly unregisteredUrls = new Set<string>();

  private readonly entries = new Map<string, { entry: FixtureEntry; compiled: CompiledPattern }>();
  private readonly degradeRules: DegradeRule[] = [];
  private inflightCount = 0;
  private lastActivityAt = Date.now();

  constructor(entries: FixtureEntry[]) {
    for (const entry of entries) this.add(entry, false);
  }

  /** API requests the handler has received but not yet fulfilled. */
  get inflight(): number {
    return this.inflightCount;
  }

  /** Milliseconds since the mock API last received or fulfilled a request. */
  get idleMs(): number {
    return Date.now() - this.lastActivityAt;
  }

  /** Register or replace one fixture for the current test. */
  register<T>(
    method: HttpMethod,
    pattern: string,
    handler: (request: FixtureRequest) => FixtureReply<T> | Promise<FixtureReply<T>>,
  ): void {
    this.add({ method, pattern, handler }, true);
  }

  /** Remove one fixture so the endpoint becomes unregistered (harness self-tests). */
  unregister(method: HttpMethod, pattern: string): void {
    if (!this.entries.delete(registryKey(method, pattern))) {
      throw new Error(`No fixture registered for ${method} ${pattern}`);
    }
  }

  /**
   * Opt one endpoint into an explicit degraded state. `endpointPattern` is a
   * registry-style pattern (`/api/leads`, `/api/borrowers/:id`) or a RegExp
   * tested against the version-normalized path.
   */
  degrade(endpointPattern: string | RegExp, options: DegradeOptions): void {
    const regex = typeof endpointPattern === 'string' ? compilePattern(endpointPattern).regex : endpointPattern;
    this.degradeRules.push({ matches: (path) => regex.test(path), options });
  }

  registeredKeys(): string[] {
    return [...this.entries.keys()].sort();
  }

  async install(page: Page): Promise<void> {
    await page.route('**/api/**', (route) => this.handle(route));
  }

  private add(entry: FixtureEntry, replace: boolean): void {
    const key = registryKey(entry.method, entry.pattern);
    if (!replace && this.entries.has(key)) {
      throw new Error(`Duplicate fixture registration: ${key}`);
    }
    this.entries.set(key, { entry, compiled: compilePattern(entry.pattern) });
  }

  private match(method: string, path: string): { entry: FixtureEntry; params: Record<string, string> } | null {
    let best: { entry: FixtureEntry; params: Record<string, string>; specificity: number } | null = null;
    for (const { entry, compiled } of this.entries.values()) {
      if (entry.method !== method) continue;
      const hit = compiled.regex.exec(path);
      if (!hit) continue;
      if (best && best.specificity >= compiled.specificity) continue;
      const params: Record<string, string> = {};
      compiled.paramNames.forEach((name, index) => {
        params[name] = decodeURIComponent(hit[index + 1] ?? '');
      });
      best = { entry, params, specificity: compiled.specificity };
    }
    return best;
  }

  private async handle(route: Route): Promise<void> {
    const request = route.request();
    const url = new URL(request.url());
    const path = normalizeApiPath(url.pathname);
    if (!/^\/api(\/|$)/.test(path)) {
      await route.continue();
      return;
    }
    const method = request.method();
    this.inflightCount += 1;
    this.lastActivityAt = Date.now();
    try {
      const call = await this.respond(route, request, url, method, path);
      this.calls.push(call);
      if (call.outcome === 'unregistered') this.unregistered.push(call);
    } finally {
      this.inflightCount -= 1;
      this.lastActivityAt = Date.now();
    }
  }

  private async respond(route: Route, request: Request, url: URL, method: string, path: string): Promise<ApiCall> {
    const search = url.search.replace(/^\?/, '');
    const degraded = this.degradeRules.find(
      (rule) => rule.matches(path) && (!rule.options.method || rule.options.method === method),
    );
    if (degraded) {
      this.degradedUrls.add(request.url());
      await this.fulfill(route, degraded.options.status, degraded.options.headers, degraded.options.body);
      return { method, path, search, status: degraded.options.status, outcome: 'degraded' };
    }

    const hit = this.match(method, path);
    if (!hit) {
      this.unregisteredUrls.add(request.url());
      await this.fulfill(route, 501, undefined, {
        detail: `No fixture registered for ${method} ${path} (fixture harness).`,
      });
      return { method, path, search, status: 501, outcome: 'unregistered' };
    }

    const reply = await hit.entry.handler({
      method: hit.entry.method,
      path,
      url,
      query: url.searchParams,
      params: hit.params,
      body: parseBody(request),
    });
    const status = reply.status ?? 200;
    await this.fulfill(route, status, reply.headers, reply.body);
    return { method, path, search, status, outcome: 'fixture' };
  }

  private async fulfill(
    route: Route,
    status: number,
    headers: Record<string, string> | undefined,
    body: unknown,
  ): Promise<void> {
    try {
      await route.fulfill({
        status,
        contentType: 'application/json',
        headers: headers ?? {},
        body: JSON.stringify(body),
      });
    } catch {
      // The page navigated or closed while the request was in flight; the
      // browser already dropped it, so there is nothing left to answer.
    }
  }
}
