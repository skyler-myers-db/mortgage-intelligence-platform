import { apiPath } from './apiPaths';
import { apiCallRoute, isApiCallSampled, serverTimingDetails } from './rumApiRoute';
import { attachClientErrorSink, getRumRouteSource, type QueuedClientError } from './rumBridge';

export type RumMetric =
  | 'navigation_load'
  | 'route_change'
  | 'lcp'
  | 'cls'
  | 'inp'
  | 'long_task'
  | 'api_call'
  | 'client_error';

export type RumRating = 'good' | 'needs_improvement' | 'poor' | 'info';

export interface RumEvent {
  metric: RumMetric;
  value: number;
  rating: RumRating;
  route: string;
  navigation_type?: string | null;
  details?: Record<string, string | number | boolean | null>;
}

declare global {
  interface Window {
    __mipRumInstalled?: boolean;
  }
}

const BORROWER_ID_RE = /\/B-[A-Za-z0-9][A-Za-z0-9_-]{0,126}(?=\/|$)/g;
const CLIP_ID_RE = /\/CL-[A-Za-z0-9][A-Za-z0-9_-]{1,126}(?=\/|$)/g;
const NUMERIC_ID_RE = /\/\d{5,}(?=\/|$)/g;
const UUID_RE = /\/[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}(?=\/|$)/g;
const MAX_BATCH = 20;
const FLUSH_DELAY_MS = 2000;

let queue: RumEvent[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;

export function sanitizeRumRoute(pathname: string): string {
  const pathOnly = pathname.split(/[?#]/, 1)[0] || '/';
  return pathOnly
    .replace(BORROWER_ID_RE, '/:borrower_id')
    .replace(CLIP_ID_RE, '/:clip_id')
    .replace(NUMERIC_ID_RE, '/:numeric_id')
    .replace(UUID_RE, '/:uuid')
    .slice(0, 160);
}

function currentRoute(): string {
  if (typeof window === 'undefined') return '/';
  return sanitizeRumRoute(window.location.pathname);
}

function rate(metric: RumMetric, value: number): RumRating {
  if (metric === 'lcp') {
    if (value <= 2500) return 'good';
    if (value <= 4000) return 'needs_improvement';
    return 'poor';
  }
  if (metric === 'cls') {
    if (value <= 0.1) return 'good';
    if (value <= 0.25) return 'needs_improvement';
    return 'poor';
  }
  if (metric === 'inp') {
    if (value <= 200) return 'good';
    if (value <= 500) return 'needs_improvement';
    return 'poor';
  }
  if (metric === 'navigation_load' || metric === 'route_change') {
    if (value <= 1000) return 'good';
    if (value <= 2500) return 'needs_improvement';
    return 'poor';
  }
  if (metric === 'long_task') {
    if (value <= 100) return 'good';
    if (value <= 250) return 'needs_improvement';
    return 'poor';
  }
  return 'info';
}

export function enqueueRumEvent(event: RumEvent): void {
  if (!Number.isFinite(event.value) || event.value < 0) return;
  queue.push(event);
  if (queue.length >= MAX_BATCH) {
    flushRum();
    return;
  }
  if (flushTimer === null) {
    flushTimer = setTimeout(flushRum, FLUSH_DELAY_MS);
  }
}

export function flushRum(): void {
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  if (queue.length === 0 || typeof window === 'undefined') return;
  const batch = queue.slice(0, MAX_BATCH);
  queue = queue.slice(MAX_BATCH);
  const body = JSON.stringify({ events: batch });
  const blob = new Blob([body], { type: 'application/json' });
  if (navigator.sendBeacon && navigator.sendBeacon(apiPath('/telemetry/rum'), blob)) {
    if (queue.length > 0) flushRum();
    return;
  }
  void fetch(apiPath('/telemetry/rum'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => {
    // RUM must never affect app behavior. Dropped telemetry is acceptable.
  });
  if (queue.length > 0) flushRum();
}

function observeNavigation(): void {
  window.addEventListener('load', () => {
    const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
    if (!nav) return;
    enqueueRumEvent({
      metric: 'navigation_load',
      value: nav.loadEventEnd || nav.duration,
      rating: rate('navigation_load', nav.loadEventEnd || nav.duration),
      route: currentRoute(),
      navigation_type: nav.type,
      details: {
        dom_content_loaded_ms: Math.round(nav.domContentLoadedEventEnd),
        ttfb_ms: Math.round(nav.responseStart),
        transfer_size: nav.transferSize || 0,
      },
    });
  }, { once: true });
}

function observeLcp(): void {
  if (!('PerformanceObserver' in window)) return;
  try {
    const observer = new PerformanceObserver((list) => {
      const entries = list.getEntries();
      const last = entries[entries.length - 1];
      if (!last) return;
      enqueueRumEvent({
        metric: 'lcp',
        value: last.startTime,
        rating: rate('lcp', last.startTime),
        route: currentRoute(),
      });
    });
    observer.observe({ type: 'largest-contentful-paint', buffered: true });
  } catch {
    // Unsupported browser, no-op.
  }
}

function observeCls(): void {
  if (!('PerformanceObserver' in window)) return;
  let cls = 0;
  try {
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const shift = entry as PerformanceEntry & { hadRecentInput?: boolean; value?: number };
        if (!shift.hadRecentInput) cls += shift.value ?? 0;
      }
    });
    observer.observe({ type: 'layout-shift', buffered: true });
    const report = () => {
      enqueueRumEvent({
        metric: 'cls',
        value: cls,
        rating: rate('cls', cls),
        route: currentRoute(),
      });
    };
    window.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') report();
    });
    window.addEventListener('pagehide', report);
  } catch {
    // Unsupported browser, no-op.
  }
}

function observeInp(): void {
  if (!('PerformanceObserver' in window)) return;
  let maxDuration = 0;
  try {
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const event = entry as PerformanceEntry & { duration?: number; interactionId?: number };
        if ((event.interactionId ?? 0) > 0 && (event.duration ?? 0) > maxDuration) {
          maxDuration = event.duration ?? 0;
        }
      }
    });
    observer.observe({
      type: 'event',
      buffered: true,
      durationThreshold: 40,
    } as PerformanceObserverInit & { durationThreshold: number });
    const report = () => {
      if (maxDuration <= 0) return;
      enqueueRumEvent({
        metric: 'inp',
        value: maxDuration,
        rating: rate('inp', maxDuration),
        route: currentRoute(),
      });
    };
    window.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') report();
    });
    window.addEventListener('pagehide', report);
  } catch {
    // Unsupported browser, no-op.
  }
}

function observeLongTasks(): void {
  if (!('PerformanceObserver' in window)) return;
  try {
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        enqueueRumEvent({
          metric: 'long_task',
          value: entry.duration,
          rating: rate('long_task', entry.duration),
          route: currentRoute(),
        });
      }
    });
    observer.observe({ type: 'longtask', buffered: true });
  } catch {
    // Unsupported browser, no-op.
  }
}

/** A tab reports at most this many api_call events per document. */
const MAX_API_CALLS_PER_DOCUMENT = 100;
const MAX_RUM_NUMBER = 600_000;
const API_INITIATORS: ReadonlySet<string> = new Set(['fetch', 'xmlhttprequest']);

function sessionStorageOrNull(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/**
 * api_call (audit delivery-v3): same-origin fetch / XHR resource timings under
 * /api, as a templated `api_route` (lib/rumApiRoute) plus the Server-Timing
 * fields, on the page route. Sampled per tab session and capped per document;
 * the server rejects a whole batch over one out-of-range number, so a call
 * longer than the schema's 600 s is skipped and an oversized transfer is left
 * out rather than clamped. `transferSize` is the encoded (compressed) size on
 * the wire, so the cap binds rarely: the default 500-row GET /api/leads page
 * is about 45 KB gzipped (628 KB decoded).
 */
function observeApiCalls(): void {
  if (!('PerformanceObserver' in window)) return;
  if (!isApiCallSampled(sessionStorageOrNull, Math.random)) return;
  let reported = 0;
  try {
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as PerformanceResourceTiming[]) {
        if (reported >= MAX_API_CALLS_PER_DOCUMENT) {
          observer.disconnect();
          return;
        }
        if (!API_INITIATORS.has(entry.initiatorType)) continue;
        const apiRoute = apiCallRoute(entry.name, window.location.origin);
        const value = Math.round(entry.duration);
        if (apiRoute === null || !(value >= 0 && value <= MAX_RUM_NUMBER)) continue;
        const details: Record<string, string | number> = { api_route: apiRoute };
        const transferSize = entry.transferSize || 0;
        if (transferSize <= MAX_RUM_NUMBER) details.transfer_size = transferSize;
        Object.assign(details, serverTimingDetails(entry.serverTiming ?? []));
        reported += 1;
        enqueueRumEvent({ metric: 'api_call', value, rating: 'info', route: currentRoute(), details });
      }
    });
    observer.observe({ type: 'resource', buffered: true });
  } catch {
    // Unsupported browser, no-op.
  }
}

/**
 * route_change from the router's COMMITTED location (lib/rumBridge's route
 * source, registered by main.tsx), never from window.history: a Back the
 * unsaved-changes guard blocks moves the URL there and back again (two
 * popstates) while the router's location never changes, which the old
 * pushState / replaceState patch and popstate listener recorded as two
 * phantom route changes. Without a registered source nothing is recorded.
 */
function observeRouteChanges(): void {
  const source = getRumRouteSource();
  if (!source) return;
  let route = currentRoute();
  source((pathname) => {
    const next = sanitizeRumRoute(pathname);
    if (next === route) return;
    const previous = route;
    const start = performance.now();
    route = next;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const duration = performance.now() - start;
        enqueueRumEvent({
          metric: 'route_change',
          value: duration,
          rating: rate('route_change', duration),
          route: next,
          details: { from_route: previous },
        });
      });
    });
  });
}

/**
 * A queued client error (lib/rumBridge) as a RUM event: the closed name,
 * kind, source and boundary, on the route registry's pattern. Never a
 * message, a stack or a pathname.
 */
function clientErrorEvent(report: QueuedClientError): RumEvent {
  const details: Record<string, string> = {
    error_name: report.errorName,
    error_kind: report.kind,
    error_source: report.source,
  };
  if (report.boundary) details.boundary = report.boundary;
  return { metric: 'client_error', value: 1, rating: 'info', route: report.route, details };
}

export function installRum(): void {
  if (typeof window === 'undefined') return;
  if (window.__mipRumInstalled) return;
  window.__mipRumInstalled = true;
  attachClientErrorSink((report) => enqueueRumEvent(clientErrorEvent(report)));
  observeNavigation();
  observeLcp();
  observeCls();
  observeInp();
  observeLongTasks();
  observeApiCalls();
  observeRouteChanges();
  window.addEventListener('pagehide', flushRum);
}
