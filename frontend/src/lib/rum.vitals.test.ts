/**
 * @vitest-environment happy-dom
 *
 * Core Web Vitals through the web-vitals attribution build (D-platform-process-d2;
 * audit 2026-09-21 stack-08, runtime-09, quality-01). web-vitals is mocked:
 * each test drives the callbacks lib/rum registered and reads the beacon's
 * body. Pinned: one report per metric instance, generateTarget never null or
 * undefined, the INP route is the template at interactionTime even after a
 * later navigation, the LCP element bucket, the navigationType map, and the
 * client re-validation that drops only the invalid event.
 */
import { createMemoryRouter } from 'react-router';
import type { CLSMetricWithAttribution, INPMetricWithAttribution, LCPMetricWithAttribution } from 'web-vitals/attribution';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiPath } from './apiPaths';
import { RUM_DETAIL_KEYS } from './rumVocabulary';

type Generate = (el: Node | null) => string | undefined;
interface Registered<M> {
  callback: (metric: M) => void;
  opts: { reportSoftNavs?: boolean; reportAllChanges?: boolean; generateTarget?: Generate };
}

const registered = vi.hoisted(() => ({
  inp: [] as Array<Registered<INPMetricWithAttribution>>,
  lcp: [] as Array<Registered<LCPMetricWithAttribution>>,
  cls: [] as Array<Registered<CLSMetricWithAttribution>>,
}));

vi.mock('web-vitals/attribution', () => ({
  onINP: (callback: Registered<INPMetricWithAttribution>['callback'], opts: Registered<INPMetricWithAttribution>['opts']) =>
    registered.inp.push({ callback, opts }),
  onLCP: (callback: Registered<LCPMetricWithAttribution>['callback'], opts: Registered<LCPMetricWithAttribution>['opts']) =>
    registered.lcp.push({ callback, opts }),
  onCLS: (callback: Registered<CLSMetricWithAttribution>['callback'], opts: Registered<CLSMetricWithAttribution>['opts']) =>
    registered.cls.push({ callback, opts }),
}));

type RumModule = typeof import('./rum');
type Bridge = typeof import('./rumBridge');
interface WireEvent {
  metric: string;
  value: number;
  rating: string;
  route: string;
  navigation_type?: string | null;
  details?: Record<string, unknown>;
}

const bodies: string[] = [];
const targets: string[] = [];

function wire(): WireEvent[] {
  return bodies.flatMap((body) => (JSON.parse(body) as { events: WireEvent[] }).events);
}

async function freshRum(): Promise<{ rum: RumModule; bridge: Bridge }> {
  vi.resetModules();
  delete window.__mipRumInstalled;
  registered.inp.length = 0;
  registered.lcp.length = 0;
  registered.cls.length = 0;
  const bridge = await import('./rumBridge');
  const rum = await import('./rum');
  return { rum, bridge };
}

/** main.tsx's registration: the router's committed location is the RUM route source. */
function registerRouter(bridge: Bridge, path = '/lead-queue'): ReturnType<typeof createMemoryRouter> {
  window.history.replaceState(null, '', path);
  const router = createMemoryRouter([{ path: '*', element: null }], { initialEntries: [path] });
  bridge.setRumRouteSource((listener) => router.subscribe((state) => listener(state.location.pathname)));
  return router;
}

async function installed(rum: RumModule, bridge?: Bridge): Promise<void> {
  if (bridge) registerRouter(bridge);
  rum.installRum((task) => task());
  await vi.waitFor(() => expect(registered.cls).toHaveLength(1));
}

async function flush(rum: RumModule): Promise<WireEvent[]> {
  const before = bodies.length;
  rum.flushRum();
  await vi.waitFor(() => expect(bodies.length).toBeGreaterThan(before));
  return wire();
}

const BASE = {
  delta: 0,
  entries: [],
  navigationId: 1,
  navigationType: 'navigate' as const,
};

function inp(id: string, value: number, interactionTime: number | undefined, target = 'filter'): INPMetricWithAttribution {
  return {
    ...BASE,
    name: 'INP',
    id,
    value,
    rating: value <= 200 ? 'good' : value <= 500 ? 'needs-improvement' : 'poor',
    attribution: {
      interactionTarget: target,
      interactionTime,
      interactionType: 'pointer',
      processedEventEntries: [],
      inputDelay: 12.4,
      processingDuration: value - 60.6,
      presentationDelay: 48.2,
      loadState: 'complete',
      longAnimationFrameEntries: [],
    },
  };
}

function lcp(id: string, value: number, target: string | undefined, renderTime: number): LCPMetricWithAttribution {
  return {
    ...BASE,
    name: 'LCP',
    id,
    value,
    rating: value <= 2500 ? 'good' : value <= 4000 ? 'needs-improvement' : 'poor',
    attribution: {
      target,
      timeToFirstByte: 100,
      resourceLoadDelay: 0,
      resourceLoadDuration: 0,
      elementRenderDelay: 0,
      lcpEntry: { renderTime, startTime: renderTime } as LargestContentfulPaint,
    },
  };
}

function cls(id: string, value: number, navigationType: CLSMetricWithAttribution['navigationType']): CLSMetricWithAttribution {
  return { ...BASE, name: 'CLS', id, value, rating: value <= 0.1 ? 'good' : 'poor', navigationType, attribution: {} };
}

vi.setConfig({ testTimeout: 30_000 });
beforeAll(async () => {
  await import('./rum');
}, 60_000);

beforeEach(() => {
  bodies.length = 0;
  targets.length = 0;
  window.history.replaceState(null, '', '/lead-queue');
  window.sessionStorage.setItem('mip.rumApiSample', '0');
  Object.defineProperty(navigator, 'sendBeacon', {
    configurable: true,
    value: vi.fn((url: string | URL, data?: BodyInit | null) => {
      targets.push(String(url));
      if (data instanceof Blob) void data.text().then((text) => bodies.push(text));
      return true;
    }),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('registration', () => {
  it('registers INP, LCP and CLS with reportSoftNavs and without reportAllChanges', async () => {
    const { rum, bridge } = await freshRum();
    await installed(rum, bridge);
    for (const list of [registered.inp, registered.lcp, registered.cls]) {
      expect(list).toHaveLength(1);
      expect(list[0].opts.reportSoftNavs).toBe(true);
      expect(list[0].opts.reportAllChanges).toBeUndefined();
    }
  });

  it('generateTarget never returns null or undefined', async () => {
    const { rum } = await freshRum();
    await installed(rum);
    const inpTarget = registered.inp[0].opts.generateTarget as Generate;
    const lcpTarget = registered.lcp[0].opts.generateTarget as Generate;
    const clsTarget = registered.cls[0].opts.generateTarget as Generate;

    const host = document.createElement('div');
    host.innerHTML =
      '<div data-rum-target="filter"><button id="in-filter"><span id="deep">x</span></button></div>' +
      '<div data-rum-target="#lead-row td"><button id="unknown-target">y</button></div>' +
      '<p id="plain">z</p><section id="section">s</section><img id="img" alt="">';
    document.body.append(host);
    try {
      const byId = (id: string) => host.querySelector(`#${id}`);
      const text = byId('plain')?.firstChild ?? null;
      for (const generate of [inpTarget, lcpTarget, clsTarget]) {
        for (const node of [null, text, byId('plain'), byId('unknown-target'), byId('deep'), document]) {
          const value = generate(node);
          expect(value, String(node)).toEqual(expect.any(String));
        }
      }
      expect(inpTarget(byId('deep'))).toBe('filter');
      expect(inpTarget(byId('unknown-target'))).toBe('other');
      expect(inpTarget(text)).toBe('other');
      expect(inpTarget(null)).toBe('other');
      expect(lcpTarget(byId('img'))).toBe('img');
      expect(lcpTarget(byId('plain'))).toBe('p');
      expect(lcpTarget(byId('section'))).toBe('other');
      expect(clsTarget(byId('img'))).toBe('other');
    } finally {
      host.remove();
    }
  });
});

describe('reports', () => {
  it('reports each metric once per instance, and again for a new instance (bfcache, soft navigation)', async () => {
    const { rum, bridge } = await freshRum();
    await installed(rum, bridge);
    const report = registered.inp[0].callback;

    report(inp('v6-inp-1', 180, undefined));
    report(inp('v6-inp-1', 240, undefined));
    report(inp('v6-inp-2', 90, undefined));

    const events = (await flush(rum)).filter((event) => event.metric === 'inp');
    expect(events.map((event) => event.value)).toEqual([180, 90]);
  });

  it('attributes INP to the template at interactionTime, even after a later navigation', async () => {
    const { rum, bridge } = await freshRum();
    const clock = vi.spyOn(performance, 'now').mockReturnValue(1000);
    const router = registerRouter(bridge);
    await installed(rum);
    clock.mockReturnValue(2000);
    await router.navigate('/borrower-360/B-0123456789ABC');
    clock.mockReturnValue(5000);
    await router.navigate('/glossary');
    clock.mockRestore();

    registered.inp[0].callback(inp('inp-a', 320, 2500, 'lead-row'));
    registered.inp[0].callback(inp('inp-b', 120, 500));
    registered.inp[0].callback(inp('inp-c', 140, 6000));

    const events = (await flush(rum)).filter((event) => event.metric === 'inp');
    expect(events.map((event) => event.route)).toEqual(['/borrower-360/:id', '/lead-queue', '/glossary']);
    expect(events[0]).toEqual({
      metric: 'inp',
      value: 320,
      rating: 'needs_improvement',
      route: '/borrower-360/:id',
      navigation_type: 'navigate',
      details: { interaction_target: 'lead-row', input_delay_ms: 12, processing_ms: 259, presentation_ms: 48 },
    });
    expect(JSON.stringify(events)).not.toContain('B-0123456789ABC');
  });

  it('falls back to the navigation URL template, never window.location at report time', async () => {
    const { rum } = await freshRum();
    await installed(rum);
    window.history.replaceState(null, '', '/glossary');
    const metric = { ...inp('inp-url', 150, undefined), navigationURL: 'https://app.example/offer-orchestrator/B-0123456789ABC?x=1' };

    registered.inp[0].callback(metric);

    const [event] = (await flush(rum)).filter((entry) => entry.metric === 'inp');
    expect(event.route).toBe('/offer-orchestrator/:id');
  });

  it('buckets the LCP element and folds a selector-shaped target to other', async () => {
    const { rum, bridge } = await freshRum();
    await installed(rum, bridge);
    registered.lcp[0].callback(lcp('lcp-1', 1800, 'img', 900));
    registered.lcp[0].callback(lcp('lcp-2', 4200, 'body > div.lead-table', 950));
    registered.lcp[0].callback(lcp('lcp-3', 3000, undefined, 990));

    const events = (await flush(rum)).filter((event) => event.metric === 'lcp');
    expect(events.map((event) => [event.details?.lcp_element, event.rating])).toEqual([
      ['img', 'good'],
      ['other', 'poor'],
      ['other', 'needs_improvement'],
    ]);
  });

  it('maps every web-vitals navigationType onto the wire vocabulary', async () => {
    const { rum, bridge } = await freshRum();
    await installed(rum, bridge);
    const types = ['navigate', 'reload', 'restore', 'back-forward', 'back-forward-cache', 'prerender', 'soft-navigation'] as const;
    types.forEach((type, index) => registered.cls[0].callback(cls(`cls-${index}`, 0.01, type)));

    const events = (await flush(rum)).filter((event) => event.metric === 'cls');
    expect(events.map((event) => event.navigation_type)).toEqual([
      'navigate', 'reload', 'reload', 'back_forward', 'back_forward', 'prerender', 'soft_navigation',
    ]);
    expect(events.every((event) => Object.keys(event.details ?? {}).length === 0)).toBe(true);
  });

  it('drops only the invalid event, never the batch, and sends only closed keys to the one target', async () => {
    const { rum, bridge } = await freshRum();
    await installed(rum, bridge);
    registered.lcp[0].callback(lcp('lcp-huge', 700_000, 'img', 900));
    registered.cls[0].callback(cls('cls-ok', 0.04, 'navigate'));
    rum.enqueueRumEvent({ metric: 'cls', value: 0.01, rating: 'good', route: '/borrower-360/B-0123456789ABC' });
    rum.enqueueRumEvent({ metric: 'lcp', value: 900, rating: 'good', route: '/', details: { interaction_target: 'nav' } });
    rum.enqueueRumEvent({ metric: 'inp', value: 90, rating: 'good', route: '/', details: { interaction_target: '#x' } });

    const events = await flush(rum);
    expect(events.map((event) => [event.metric, event.value])).toEqual([['cls', 0.04]]);
    const allowed = new Set<string>(RUM_DETAIL_KEYS);
    for (const event of events) {
      for (const key of Object.keys(event.details ?? {})) expect(allowed.has(key), key).toBe(true);
    }
    expect(new Set(targets)).toEqual(new Set([apiPath('/telemetry/rum')]));
  });
});
