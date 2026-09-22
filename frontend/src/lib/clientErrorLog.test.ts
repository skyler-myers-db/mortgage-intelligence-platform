import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { reportClientError, rootErrorOptions } from './clientErrorLog';

const BORROWER_BEARING_MESSAGE = 'Cannot read score of borrower B-0TESTBORROWER at /borrower-360/B-0TESTBORROWER';

describe('reportClientError', () => {
  let consoleError: MockInstance<typeof console.error>;

  beforeEach(() => {
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('builds a telemetry-safe report: error name and kind, never the message or stack', () => {
    const error = new RangeError(BORROWER_BEARING_MESSAGE);

    const report = reportClientError('caught', error, {
      boundary: 'route',
      componentStack: '\n    at LeadTable (B-0TESTBORROWER)',
    });

    expect(report).toEqual({
      source: 'caught',
      kind: 'render',
      errorName: 'RangeError',
      boundary: 'route',
    });
    expect(JSON.stringify(report)).not.toContain('B-0TESTBORROWER');
  });

  it('classifies a dynamic-import failure as a chunk error', () => {
    const report = reportClientError(
      'preload',
      new TypeError('Failed to fetch dynamically imported module: /assets/x.js'),
    );

    expect(report).toMatchObject({ source: 'preload', kind: 'chunk', boundary: null });
  });

  it('survives non-Error throwables', () => {
    expect(reportClientError('uncaught', 'a thrown string').errorName).toBe('string');
    expect(reportClientError('uncaught', null).errorName).toBe('object');
  });

  it('logs locally and makes no network call', () => {
    const fetchSpy = vi.fn();
    const beaconSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    vi.stubGlobal('navigator', { sendBeacon: beaconSpy });

    reportClientError('uncaught', new Error(BORROWER_BEARING_MESSAGE));

    expect(consoleError).toHaveBeenCalledTimes(1);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(beaconSpy).not.toHaveBeenCalled();
  });
});

describe('rootErrorOptions', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('routes all three React root callbacks through the one log function', () => {
    const consoleError = vi.mocked(console.error);
    const options = rootErrorOptions();
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
