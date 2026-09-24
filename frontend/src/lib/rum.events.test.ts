/**
 * @vitest-environment happy-dom
 *
 * The events lib/rum builds and hands the beacon (audit 2026-09-21 stack-01,
 * states-01, quality-01, delivery-v3): the client_error a queued report
 * becomes, and the api_call a resource timing becomes. Each test installs RUM
 * on fresh modules (vi.resetModules) and reads the body the beacon was
 * handed, which is the wire payload. PerformanceObserver is replaced by a
 * fake that lets a test deliver `resource` entries.
 */
import { act, createElement, Fragment, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, Outlet, RouterProvider, useBlocker } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type RumModule = typeof import('./rum');
type ClientErrorLog = typeof import('./clientErrorLog');

interface WireEvent {
  metric: string;
  value: number;
  rating: string;
  route: string;
  details?: Record<string, unknown>;
}

const bodies: string[] = [];

type ObserverCallback = (list: { getEntries: () => PerformanceEntry[] }) => void;
const observed = new Map<string, FakePerformanceObserver>();

class FakePerformanceObserver {
  disconnected = false;
  constructor(private readonly callback: ObserverCallback) {}
  observe(options: PerformanceObserverInit): void {
    if (options.type) observed.set(options.type, this);
  }
  disconnect(): void {
    this.disconnected = true;
  }
  deliver(entries: readonly object[]): void {
    if (!this.disconnected) this.callback({ getEntries: () => entries as PerformanceEntry[] });
  }
}

interface ResourceShape {
  path: string;
  duration?: number;
  initiatorType?: string;
  transferSize?: number;
  serverTiming?: Array<{ name: string; duration: number; description: string }>;
  origin?: string;
}

function resource({ path, duration = 120.4, initiatorType = 'fetch', transferSize = 1200, serverTiming = [], origin }: ResourceShape) {
  return {
    name: `${origin ?? window.location.origin}${path}`,
    entryType: 'resource',
    initiatorType,
    duration,
    transferSize,
    serverTiming,
  };
}

function deliverResources(entries: readonly ResourceShape[]): void {
  const observer = observed.get('resource');
  if (!observer) throw new Error('no resource observer was registered');
  observer.deliver(entries.map(resource));
}

function apiCalls(): WireEvent[] {
  return wireEvents().filter((event) => event.metric === 'api_call');
}

function wireEvents(): WireEvent[] {
  return bodies.flatMap((body) => (JSON.parse(body) as { events: WireEvent[] }).events);
}

async function freshRum(): Promise<{ rum: RumModule; log: ClientErrorLog }> {
  vi.resetModules();
  delete window.__mipRumInstalled;
  const log = await import('./clientErrorLog');
  const rum = await import('./rum');
  return { rum, log };
}

async function flush(rum: RumModule): Promise<WireEvent[]> {
  const before = bodies.length;
  rum.flushRum();
  await vi.waitFor(() => expect(bodies.length).toBeGreaterThan(before));
  return wireEvents();
}

beforeEach(() => {
  bodies.length = 0;
  observed.clear();
  vi.stubGlobal('PerformanceObserver', FakePerformanceObserver);
  window.sessionStorage.clear();
  window.history.replaceState(null, '', '/');
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  const beacon = vi.fn((_url: string | URL, data?: BodyInit | null) => {
    if (data instanceof Blob) void data.text().then((text) => bodies.push(text));
    return true;
  });
  Object.defineProperty(navigator, 'sendBeacon', { configurable: true, value: beacon });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('client_error events', () => {
  it('turns a report queued before install into the closed client_error event', async () => {
    const { rum, log } = await freshRum();
    const message = "Cannot read properties of null (reading 'find') for B-0123456789ABC";
    log.reportClientError('caught', new TypeError(message), { boundary: 'drawer', componentStack: message });

    rum.installRum();
    const events = await flush(rum);

    expect(events).toEqual([
      {
        metric: 'client_error',
        value: 1,
        rating: 'info',
        route: '/',
        details: { error_name: 'TypeError', error_kind: 'render', error_source: 'caught', boundary: 'drawer' },
      },
    ]);
    const payload = bodies.join('\n');
    expect(payload).not.toContain('B-');
    expect(payload).not.toContain('Cannot read');
  });

  it('omits the boundary key for a report no boundary caught', async () => {
    const { rum, log } = await freshRum();
    rum.installRum();
    window.history.replaceState(null, '', '/borrower-360/B-0123456789ABC?from=queue');
    log.reportClientError('uncaught', new RangeError('x'));

    const [event] = await flush(rum);
    expect(event).toEqual({
      metric: 'client_error',
      value: 1,
      rating: 'info',
      route: '/borrower-360/:id',
      details: { error_name: 'RangeError', error_kind: 'render', error_source: 'uncaught' },
    });
  });
});

describe('api_call events', () => {
  it('reports a sampled tab\'s API calls as templated routes with their Server-Timing fields', async () => {
    window.sessionStorage.setItem('mip.rumApiSample', '1');
    const { rum } = await freshRum();
    rum.installRum();
    window.history.replaceState(null, '', '/borrower-360/B-0123456789ABC');
    deliverResources([
      {
        path: '/api/v1/borrowers/B-0123456789ABC/proof',
        duration: 912.4,
        serverTiming: [
          { name: 'cache', duration: 0, description: 'miss' },
          { name: 'warehouse', duration: 812.4, description: '' },
          { name: 'lakebase', duration: 2.6, description: '' },
          { name: 'total', duration: 840.2, description: '' },
        ],
      },
      { path: '/api/v1/analytics/funnel/loan-officers/lo-jdoe', transferSize: 0 },
      { path: '/api/v1/leads?state=TX&owner=jane@summit.example' },
    ]);

    const events = await flush(rum);
    expect(events).toEqual([
      {
        metric: 'api_call',
        value: 912,
        rating: 'info',
        route: '/borrower-360/:borrower_id',
        details: {
          api_route: '/api/borrowers/:id/proof',
          transfer_size: 1200,
          cache: 'miss',
          warehouse_ms: 812,
          lakebase_ms: 3,
          total_ms: 840,
        },
      },
      {
        metric: 'api_call',
        value: 120,
        rating: 'info',
        route: '/borrower-360/:borrower_id',
        details: { api_route: '/api/analytics/funnel/loan-officers/:id', transfer_size: 0 },
      },
      {
        metric: 'api_call',
        value: 120,
        rating: 'info',
        route: '/borrower-360/:borrower_id',
        details: { api_route: '/api/leads', transfer_size: 1200 },
      },
    ]);
    const payload = bodies.join('\n');
    expect(payload).not.toContain('B-0123456789ABC');
    expect(payload).not.toContain('lo-jdoe');
    expect(payload).not.toContain('?');
    expect(payload).not.toContain('@');
  });

  it('never reports the telemetry POST, the health probes, the Genie progress poll, another origin or a non-fetch load', async () => {
    window.sessionStorage.setItem('mip.rumApiSample', '1');
    const { rum } = await freshRum();
    rum.installRum();
    deliverResources([
      { path: '/api/v1/telemetry/rum' },
      { path: '/api/v1/health' },
      { path: '/api/v1/admin/health' },
      { path: '/api/v1/genie/message/progress' },
      { path: '/api/v1/leads', origin: 'https://elsewhere.example' },
      { path: '/api/v1/leads', initiatorType: 'beacon' },
      { path: '/assets/index-a1.js', initiatorType: 'script' },
      { path: '/api/v1/session' },
    ]);
    await flush(rum);
    expect(apiCalls().map((event) => event.details?.api_route)).toEqual(['/api/session']);
  });

  it('skips a call longer than the schema allows and leaves an oversized transfer out', async () => {
    window.sessionStorage.setItem('mip.rumApiSample', '1');
    const { rum } = await freshRum();
    rum.installRum();
    deliverResources([
      { path: '/api/v1/genie/message/submit', duration: 600_001 },
      { path: '/api/v1/leads', transferSize: 600_001 },
    ]);
    await flush(rum);
    expect(apiCalls()).toEqual([
      { metric: 'api_call', value: 120, rating: 'info', route: '/', details: { api_route: '/api/leads' } },
    ]);
  });

  it("an unsampled tab ('0') reports no API call", async () => {
    window.sessionStorage.setItem('mip.rumApiSample', '0');
    const { rum, log } = await freshRum();
    rum.installRum();
    expect(observed.has('resource')).toBe(false);
    // Non-vacuity: the tab's other RUM still flows.
    log.reportClientError('uncaught', new TypeError('x'));
    await flush(rum);
    expect(apiCalls()).toEqual([]);
    expect(wireEvents().map((event) => event.metric)).toEqual(['client_error']);
  });

  it('draws the tab sample once when the key is absent and keeps it', async () => {
    const random = vi.spyOn(Math, 'random').mockReturnValue(0.05);
    const { rum } = await freshRum();
    rum.installRum();
    expect(window.sessionStorage.getItem('mip.rumApiSample')).toBe('1');
    expect(random).toHaveBeenCalledTimes(1);
    deliverResources([{ path: '/api/v1/leads' }]);
    await flush(rum);
    expect(apiCalls()).toHaveLength(1);
  });

  it('caps a document at 100 api_call events, in batches of at most 20', async () => {
    window.sessionStorage.setItem('mip.rumApiSample', '1');
    const { rum } = await freshRum();
    rum.installRum();
    const leads = Array.from({ length: 60 }, () => ({ path: '/api/v1/leads' }));
    deliverResources(leads);
    deliverResources(leads);
    deliverResources(leads);
    await flush(rum);
    await vi.waitFor(() => expect(apiCalls()).toHaveLength(100));
    for (const body of bodies) {
      expect((JSON.parse(body) as { events: unknown[] }).events.length).toBeLessThanOrEqual(20);
    }
  });
});

describe('route_change events', () => {
  function routeChanges(): WireEvent[] {
    return wireEvents().filter((event) => event.metric === 'route_change');
  }

  async function frames(count: number): Promise<void> {
    for (let frame = 0; frame < count; frame += 1) {
      await new Promise((resolve) => requestAnimationFrame(() => resolve(undefined)));
    }
  }

  /** Blocks every navigation that leaves /portfolio-builder, like a dirty page's guard. */
  function Guard(): ReactNode {
    const blocker = useBlocker(
      ({ currentLocation, nextLocation }) =>
        currentLocation.pathname === '/portfolio-builder' && nextLocation.pathname !== '/portfolio-builder',
    );
    return createElement('output', { 'data-testid': 'blocker' }, blocker.state);
  }

  it('leaves the browser\'s own history methods alone', async () => {
    const nativePushState = window.history.pushState;
    const nativeReplaceState = window.history.replaceState;
    const { rum } = await freshRum();
    rum.installRum();
    expect(window.history.pushState).toBe(nativePushState);
    expect(window.history.replaceState).toBe(nativeReplaceState);
  });

  it('records the committed navigation once, and nothing for a Back the blocker holds', async () => {
    const { rum, log } = await freshRum();
    const { setRumRouteSource } = await import('./rumBridge');
    const router = createBrowserRouter([
      { path: '*', element: createElement(Fragment, null, createElement(Guard), createElement(Outlet)) },
    ]);
    setRumRouteSource((listener) => router.subscribe((state) => listener(state.location.pathname)));
    rum.installRum();

    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => root.render(createElement(RouterProvider, { router })));
    await act(async () => {
      await router.navigate('/portfolio-builder');
    });
    await frames(3);
    await flush(rum);
    expect(routeChanges().map((event) => [event.details?.from_route, event.route])).toEqual([
      ['/', '/portfolio-builder'],
    ]);

    // Back: the browser pops to /, the router reverts the URL (two popstates)
    // and keeps its committed location on /portfolio-builder. A browser
    // queues the traversal history.go() starts, so the URL really reads /
    // while the first popstate is handled; happy-dom traverses synchronously
    // inside go(), so queue it here the way a browser does.
    const nativeGo = window.history.go.bind(window.history);
    vi.spyOn(window.history, 'go').mockImplementation((delta?: number) => {
      window.setTimeout(() => nativeGo(delta), 0);
    });
    const before = routeChanges().length;
    await act(async () => {
      window.history.back();
      // The router's own blocker state (React renders it once act settles).
      await vi.waitFor(() =>
        expect([...router.state.blockers.values()].map((blocker) => blocker.state)).toContain('blocked'),
      );
      await vi.waitFor(() => expect(window.location.pathname).toBe('/portfolio-builder'));
    });
    expect(container.textContent).toBe('blocked');
    await frames(3);
    // A sentinel event makes this flush always carry a body, so "no
    // route_change in it" is read from a batch that really was sent.
    log.reportClientError('uncaught', new TypeError('sentinel'));
    await flush(rum);
    expect(wireEvents().some((event) => event.metric === 'client_error')).toBe(true);
    expect(router.state.location.pathname).toBe('/portfolio-builder');
    expect(routeChanges().length - before, 'a blocked Back records no route_change').toBe(0);

    await act(async () => root.unmount());
    container.remove();
  });

  it('records nothing when no route source is registered', async () => {
    const { rum, log } = await freshRum();
    rum.installRum();
    window.history.pushState(null, '', '/lead-queue');
    window.history.pushState(null, '', '/glossary');
    await frames(3);
    log.reportClientError('uncaught', new TypeError('x'));
    await flush(rum);
    expect(routeChanges()).toEqual([]);
    expect(wireEvents().map((event) => event.metric)).toEqual(['client_error']);
  });
});
