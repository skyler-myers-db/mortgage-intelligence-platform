/**
 * @vitest-environment happy-dom
 *
 * The client error log (audit 2026-09-21 stack-01 / shell-01 / states-01 /
 * quality-01): every report is message-free, and what is queued for
 * telemetry (lib/rumBridge, drained by lib/rum's sink) is closed, deduped and
 * capped. Each test imports fresh modules (vi.resetModules) so the per-document
 * queue starts empty; the sink is the real `attachClientErrorSink`.
 */
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import type { QueuedClientError } from './rumBridge';

const BORROWER_ID = 'B-0TESTBORROWER';
const BORROWER_BEARING_MESSAGE = `Cannot read score of borrower ${BORROWER_ID} at /borrower-360/${BORROWER_ID}`;
const STACK_LINE = '    at EvidenceDrawer (https://host/assets/index-a1.js:1:2345)';

type ClientErrorLog = typeof import('./clientErrorLog');
type RumBridge = typeof import('./rumBridge');

async function freshModules(): Promise<{ log: ClientErrorLog; bridge: RumBridge }> {
  vi.resetModules();
  const bridge = await import('./rumBridge');
  const log = await import('./clientErrorLog');
  return { log, bridge };
}

function drain(bridge: RumBridge): QueuedClientError[] {
  const sent: QueuedClientError[] = [];
  bridge.attachClientErrorSink((report) => sent.push(report));
  return sent;
}

/** What the sink hands lib/rum must never carry a message, an id, a query or an email. */
function expectMessageFree(payload: unknown): void {
  const json = JSON.stringify(payload);
  expect(json).not.toContain('B-');
  expect(json).not.toContain('Cannot read');
  expect(json).not.toContain('    at ');
  expect(json).not.toContain('?');
  expect(json).not.toContain('@');
}

describe('reportClientError', () => {
  let consoleError: MockInstance<typeof console.error>;

  beforeEach(() => {
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    window.history.replaceState(null, '', '/');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('builds a telemetry-safe report: error name, kind and route template, never the message or stack', async () => {
    const { log } = await freshModules();
    const error = new RangeError(BORROWER_BEARING_MESSAGE);
    error.stack = `RangeError: ${BORROWER_BEARING_MESSAGE}\n${STACK_LINE}`;

    const report = log.reportClientError('caught', error, {
      boundary: 'route',
      componentStack: `\n    at LeadTable (${BORROWER_ID})`,
    });

    expect(report).toEqual({
      source: 'caught',
      kind: 'render',
      errorName: 'RangeError',
      boundary: 'route',
      route: '/',
    });
    expectMessageFree(report);
  });

  it('reports the route registry pattern, never the pathname or its query string', async () => {
    const { log } = await freshModules();
    window.history.replaceState(null, '', `/borrower-360/B-0123456789ABC?from=x`);
    expect(log.reportClientError('uncaught', new Error('x')).route).toBe('/borrower-360/:id');
    window.history.replaceState(null, '', '/nope/B-0123456789ABC');
    expect(log.reportClientError('uncaught', new Error('x')).route).toBe('/*');
  });

  it('maps an unknown or missing error name to Other and keeps the closed names', async () => {
    const { log } = await freshModules();
    const custom = new Error('x');
    custom.name = `Custom ${BORROWER_ID}`;
    expect(log.reportClientError('uncaught', custom).errorName).toBe('Other');
    expect(log.reportClientError('uncaught', 'a thrown string').errorName).toBe('Other');
    expect(log.reportClientError('uncaught', null).errorName).toBe('Other');
    expect(log.reportClientError('uncaught', { name: 42 }).errorName).toBe('Other');
    expect(log.reportClientError('uncaught', new TypeError('x')).errorName).toBe('TypeError');
    expect(log.reportClientError('uncaught', new DOMException('x', 'QuotaExceededError')).errorName).toBe(
      'QuotaExceededError',
    );
  });

  it('keeps a boundary only when it is a product boundary', async () => {
    const { log } = await freshModules();
    expect(log.reportClientError('caught', new Error('x'), { boundary: 'drawer' }).boundary).toBe('drawer');
    expect(log.reportClientError('caught', new Error('x'), { boundary: BORROWER_ID }).boundary).toBeNull();
    expect(log.reportClientError('caught', new Error('x'), { boundary: null }).boundary).toBeNull();
  });

  it('classifies a dynamic-import failure as a chunk error', async () => {
    const { log } = await freshModules();
    const report = log.reportClientError(
      'preload',
      new TypeError('Failed to fetch dynamically imported module: /assets/x.js'),
    );
    expect(report).toMatchObject({ source: 'preload', kind: 'chunk', boundary: null });
  });

  it('every source, and non-Error throwables, produce a message-free queued report', async () => {
    const { log, bridge } = await freshModules();
    const sources = ['uncaught', 'caught', 'recoverable', 'preload', 'window', 'rejection'] as const;
    const throwables: unknown[] = [
      new Error(BORROWER_BEARING_MESSAGE),
      BORROWER_BEARING_MESSAGE,
      { message: BORROWER_BEARING_MESSAGE, stack: STACK_LINE },
      null,
    ];
    window.history.replaceState(null, '', `/borrower-360/${BORROWER_ID}?q=jane@summit.example`);
    const reports = sources.flatMap((source) =>
      throwables.map((error) => log.reportClientError(source, error, { boundary: 'route', componentStack: STACK_LINE })),
    );
    for (const report of reports) expectMessageFree(report);
    const sent = drain(bridge);
    expect(sent.length, 'non-vacuity: reports were queued').toBeGreaterThan(0);
    expectMessageFree(sent);
  });

  it('dedupes by kind, name and boundary: six identical errors queue one', async () => {
    const { log, bridge } = await freshModules();
    for (let i = 0; i < 6; i += 1) log.reportClientError('window', new TypeError(`boom ${i}`));
    expect(drain(bridge)).toHaveLength(1);
  });

  it('caps a document at five reports: six distinct errors queue five', async () => {
    const { log, bridge } = await freshModules();
    const names = ['Error', 'TypeError', 'RangeError', 'ReferenceError', 'SyntaxError', 'EvalError'];
    for (const name of names) {
      const error = new Error('boom');
      error.name = name;
      log.reportClientError('window', error);
    }
    const sent = drain(bridge);
    expect(sent.map((report) => report.errorName)).toEqual(names.slice(0, 5));
  });

  it('holds reports until a sink attaches, then drains them; later reports go straight through', async () => {
    const { log, bridge } = await freshModules();
    log.reportClientError('uncaught', new TypeError('before'));
    const sent = drain(bridge);
    expect(sent).toEqual([
      { source: 'uncaught', kind: 'render', errorName: 'TypeError', boundary: null, route: '/' },
    ]);
    log.reportClientError('caught', new RangeError('after'), { boundary: 'console' });
    expect(sent).toHaveLength(2);
    expect(sent[1]).toMatchObject({ errorName: 'RangeError', boundary: 'console' });
  });

  it('never queues an AbortError or a catch without a product boundary', async () => {
    const { log, bridge } = await freshModules();
    log.reportClientError('rejection', new DOMException('The operation was aborted.', 'AbortError'));
    // React Router's own boundary (no `boundary` prop): RethrowToRootBoundary
    // re-throws it and the root catch is what gets reported.
    log.reportClientError('caught', new TypeError('router boundary'), { boundary: null });
    expect(drain(bridge)).toEqual([]);
    // Both still reach the local console for whoever is debugging.
    expect(consoleError).toHaveBeenCalledTimes(1);
  });

  it('logs uncaught / caught / recoverable / preload locally, but not window or rejection (the browser already did)', async () => {
    const { log } = await freshModules();
    for (const source of ['window', 'rejection'] as const) log.reportClientError(source, new Error('x'));
    expect(consoleError).not.toHaveBeenCalled();
    for (const source of ['uncaught', 'caught', 'recoverable', 'preload'] as const) {
      log.reportClientError(source, new Error('x'));
    }
    expect(consoleError).toHaveBeenCalledTimes(4);
  });

  it('makes no network call', async () => {
    const { log, bridge } = await freshModules();
    const fetchSpy = vi.fn();
    const beaconSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    Object.defineProperty(navigator, 'sendBeacon', { configurable: true, value: beaconSpy });

    log.reportClientError('uncaught', new Error(BORROWER_BEARING_MESSAGE));
    drain(bridge);

    expect(consoleError).toHaveBeenCalledTimes(1);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(beaconSpy).not.toHaveBeenCalled();
  });
});

describe('installClientErrorListeners', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('reports window errors and unhandled rejections message-free, and uninstalls', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { log, bridge } = await freshModules();
    const sent = drain(bridge);
    const uninstall = log.installClientErrorListeners();

    window.dispatchEvent(
      new ErrorEvent('error', { error: new TypeError(BORROWER_BEARING_MESSAGE), message: BORROWER_BEARING_MESSAGE }),
    );
    const rejection = new Event('unhandledrejection') as PromiseRejectionEvent;
    Object.defineProperty(rejection, 'reason', { value: new RangeError(BORROWER_BEARING_MESSAGE) });
    window.dispatchEvent(rejection);

    expect(sent.map((report) => [report.source, report.errorName])).toEqual([
      ['window', 'TypeError'],
      ['rejection', 'RangeError'],
    ]);
    expectMessageFree(sent);
    expect(consoleError).not.toHaveBeenCalled();

    uninstall();
    window.dispatchEvent(new ErrorEvent('error', { error: new SyntaxError('after') }));
    expect(sent).toHaveLength(2);
  });
});

describe('rootErrorOptions', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('routes all three React root callbacks through the one log function', async () => {
    const { log } = await freshModules();
    const consoleError = vi.mocked(console.error);
    const options = log.rootErrorOptions();
    const boundaryInstance = { props: { boundary: 'root' } };

    options.onUncaughtError?.(new Error('a'), { componentStack: '' });
    options.onCaughtError?.(new Error('b'), {
      componentStack: '',
      errorBoundary: boundaryInstance as never,
    });
    options.onRecoverableError?.(new Error('c'), { componentStack: '' });

    const reports = consoleError.mock.calls.map((call) => call[1] as { source: string; boundary: string | null });
    expect(reports.map((r) => r.source)).toEqual(['uncaught', 'caught', 'recoverable']);
    expect(reports[1].boundary).toBe('root');
  });
});
