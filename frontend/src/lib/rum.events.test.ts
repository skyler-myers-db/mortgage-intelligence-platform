/**
 * @vitest-environment happy-dom
 *
 * The events lib/rum builds and hands the beacon (audit 2026-09-21 stack-01,
 * states-01, quality-01): the client_error a queued report becomes. Each test
 * installs RUM on fresh modules (vi.resetModules) and reads the body the
 * beacon was handed, which is the wire payload.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
  window.history.replaceState(null, '', '/');
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  const beacon = vi.fn((_url: string | URL, data?: BodyInit | null) => {
    if (data instanceof Blob) void data.text().then((text) => bodies.push(text));
    return true;
  });
  Object.defineProperty(navigator, 'sendBeacon', { configurable: true, value: beacon });
});

afterEach(() => {
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
