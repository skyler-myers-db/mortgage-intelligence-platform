/**
 * @vitest-environment happy-dom
 *
 * lib/rum's install and transport contract (D-platform-process-d1 / d2;
 * audit 2026-09-21 stack-08, quality-07): installRum waits for an idle
 * callback after first paint (a timer where requestIdleCallback is missing),
 * the payload keys are a subset of the closed wire set, the only network
 * target is apiPath('/telemetry/rum'), and a flush happens on pagehide and
 * on visibilitychange to hidden, idempotently on an empty queue.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiPath } from './apiPaths';
import { RUM_DETAIL_KEYS } from './rumVocabulary';

type RumModule = typeof import('./rum');

const WIRE_EVENT_KEYS = new Set(['metric', 'value', 'rating', 'route', 'navigation_type', 'details']);
const WIRE_DETAIL_KEYS: ReadonlySet<string> = new Set(RUM_DETAIL_KEYS);

const beaconTargets: string[] = [];
const fetchTargets: string[] = [];
const bodies: string[] = [];

async function freshRum(): Promise<RumModule> {
  vi.resetModules();
  delete window.__mipRumInstalled;
  return import('./rum');
}

vi.setConfig({ testTimeout: 30_000 });
beforeAll(async () => {
  await import('./rum');
}, 60_000);

beforeEach(() => {
  beaconTargets.length = 0;
  fetchTargets.length = 0;
  bodies.length = 0;
  window.history.replaceState(null, '', '/lead-queue');
  window.sessionStorage.setItem('mip.rumApiSample', '0');
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  Object.defineProperty(navigator, 'sendBeacon', {
    configurable: true,
    value: vi.fn((url: string | URL, data?: BodyInit | null) => {
      beaconTargets.push(String(url));
      if (data instanceof Blob) void data.text().then((text) => bodies.push(text));
      return true;
    }),
  });
  vi.stubGlobal('fetch', vi.fn((url: string | URL) => {
    fetchTargets.push(String(url));
    return Promise.resolve(new Response(null, { status: 202 }));
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('installRum', () => {
  it('starts RUM from an idle callback with a 3 s timeout, never synchronously', async () => {
    const rum = await freshRum();
    const tasks: Array<() => void> = [];
    const idle = vi.fn((callback: IdleRequestCallback, options?: IdleRequestOptions) => {
      tasks.push(() => callback({ didTimeout: false, timeRemaining: () => 10 }));
      expect(options).toEqual({ timeout: 3000 });
      return 1;
    });
    vi.stubGlobal('requestIdleCallback', idle);
    const log = await import('./clientErrorLog');

    rum.installRum();
    log.reportClientError('uncaught', new TypeError('x'));
    rum.flushRum();
    expect(idle).toHaveBeenCalledTimes(1);
    expect(beaconTargets, 'nothing is wired before the idle callback').toEqual([]);

    tasks.forEach((task) => task());
    rum.flushRum();
    await vi.waitFor(() => expect(bodies).toHaveLength(1));
    expect(JSON.parse(bodies[0]).events[0]).toMatchObject({ metric: 'client_error', route: '/lead-queue' });
  });

  it('falls back to a timer where requestIdleCallback is missing (Safari)', async () => {
    const rum = await freshRum();
    const log = await import('./clientErrorLog');
    vi.stubGlobal('requestIdleCallback', undefined);
    vi.useFakeTimers();
    try {
      rum.installRum();
      log.reportClientError('uncaught', new TypeError('x'));
      rum.flushRum();
      expect(beaconTargets, 'nothing is wired before the timer fires').toEqual([]);

      vi.advanceTimersByTime(1);
      rum.flushRum();
      expect(beaconTargets).toEqual([apiPath('/telemetry/rum')]);
    } finally {
      vi.useRealTimers();
    }
  });

  it('installs once per document', async () => {
    const rum = await freshRum();
    const schedule = vi.fn();
    rum.installRum(schedule);
    rum.installRum(schedule);
    expect(schedule).toHaveBeenCalledTimes(1);
  });
});

describe('the wire', () => {
  it('sends only closed keys, and only to apiPath("/telemetry/rum")', async () => {
    const rum = await freshRum();
    rum.enqueueRumEvent({
      metric: 'inp', value: 180, rating: 'good', route: '/lead-queue', navigation_type: 'soft_navigation',
      details: { interaction_target: 'filter', input_delay_ms: 3, processing_ms: 120, presentation_ms: 57 },
    });
    rum.enqueueRumEvent({ metric: 'cls', value: 0.02, rating: 'good', route: '/' });
    rum.flushRum();
    vi.mocked(navigator.sendBeacon).mockReturnValue(false);
    rum.enqueueRumEvent({ metric: 'lcp', value: 1200, rating: 'good', route: '/glossary', details: { lcp_element: 'h1' } });
    rum.flushRum();

    await vi.waitFor(() => expect(bodies.length).toBeGreaterThan(0));
    const target = apiPath('/telemetry/rum');
    expect(new Set([...beaconTargets, ...fetchTargets])).toEqual(new Set([target]));
    expect(fetchTargets).toEqual([target]);
    for (const body of bodies) {
      for (const event of (JSON.parse(body) as { events: Array<Record<string, unknown>> }).events) {
        for (const key of Object.keys(event)) expect(WIRE_EVENT_KEYS.has(key), key).toBe(true);
        for (const key of Object.keys((event.details as object | undefined) ?? {})) {
          expect(WIRE_DETAIL_KEYS.has(key), key).toBe(true);
        }
      }
    }
  });

  it('flushes on visibilitychange to hidden and on pagehide; an empty flush sends nothing', async () => {
    const rum = await freshRum();
    rum.installRum((task) => task());
    await vi.waitFor(() => expect(rum).toBeDefined());
    // Let the dynamic web-vitals import settle so its flush listener is in place.
    await new Promise((resolve) => setTimeout(resolve, 50));

    rum.enqueueRumEvent({ metric: 'cls', value: 0.01, rating: 'good', route: '/' });
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    try {
      document.dispatchEvent(new Event('visibilitychange'));
    } finally {
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    }
    expect(beaconTargets).toHaveLength(1);

    rum.enqueueRumEvent({ metric: 'cls', value: 0.03, rating: 'good', route: '/' });
    window.dispatchEvent(new Event('pagehide'));
    expect(beaconTargets).toHaveLength(2);

    rum.flushRum();
    window.dispatchEvent(new Event('pagehide'));
    expect(beaconTargets, 'an empty queue sends nothing').toHaveLength(2);
  });
});
