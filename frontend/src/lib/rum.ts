import type { CLSMetricWithAttribution, INPMetricWithAttribution, LCPMetricWithAttribution, Metric } from 'web-vitals/attribution';
import { apiPath } from './apiPaths';
import { resolveRouteMeta } from './routeMeta';
import { apiCallRoute, isApiCallSampled, serverTimingDetails } from './rumApiRoute';
import {
  attachClientErrorSink,
  getCommittedRouteTemplate,
  getRumRouteSource,
  routeTemplateAt,
  type QueuedClientError,
} from './rumBridge';
import {
  closedInteractionTarget,
  closedLcpElement,
  isValidRumEvent,
  lcpElementBucket,
  nearestRumTarget,
  type RumDetails,
  type RumMetric,
  type RumNavigationType,
  type RumRating,
} from './rumVocabulary';

/**
 * Browser RUM (D-platform-process-d1 / d2; audit 2026-09-21 stack-08,
 * runtime-09, quality-07, quality-01). Lazy: AppContext imports it only when
 * the session says RUM is on (on by default through the deploy payload).
 *
 * Core Web Vitals come from the web-vitals 6.2.2 attribution build, imported
 * inside this chunk: one LCP, CLS and INP report per page lifecycle (and per
 * soft navigation where Chromium supports it), each attributed to the route
 * TEMPLATE in effect at the interaction or render time (lib/rumBridge's
 * timeline of the router's committed location), never window.location at
 * report time. INP carries its phases and the closed data-rum-target; LCP a
 * closed tag bucket. navigation_load, route_change and the sampled api_call
 * stay. Every event is re-validated against the closed sets before it is
 * queued (lib/rumVocabulary); an invalid one is dropped alone.
 */

export type { RumDetailKey, RumDetails, RumMetric, RumNavigationType, RumRating } from './rumVocabulary';

/** One event on the wire (POST /api/v1/telemetry/rum): closed keys and values only. */
export interface RumEvent {
  metric: RumMetric;
  value: number;
  rating: RumRating;
  route: string;
  navigation_type?: RumNavigationType | null;
  details?: RumDetails;
}

declare global {
  interface Window {
    __mipRumInstalled?: boolean;
  }
}

const MAX_BATCH = 20;
const FLUSH_DELAY_MS = 2000;
const IDLE_TIMEOUT_MS = 3000;

let queue: RumEvent[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;

/** The template at `t` (performance.now()); the committed one before the timeline, the URL only before the router. */
function templateAt(t: number): string {
  return routeTemplateAt(t) ?? getCommittedRouteTemplate() ?? resolveRouteMeta(window.location.pathname).pattern;
}

/** navigation_load and route_change bands (the server derives the stored rating itself). */
function rate(value: number): RumRating {
  if (value <= 1000) return 'good';
  if (value <= 2500) return 'needs_improvement';
  return 'poor';
}

export function enqueueRumEvent(event: RumEvent): void {
  if (!isValidRumEvent(event)) return;
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
  // wire: 'POST /api/v1/telemetry/rum'
  if (navigator.sendBeacon && navigator.sendBeacon(apiPath('/telemetry/rum'), blob)) {
    if (queue.length > 0) flushRum();
    return;
  }
  // wire: 'POST /api/v1/telemetry/rum'
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

function reportNavigation(): void {
  const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
  if (!nav) return;
  const value = nav.loadEventEnd || nav.duration;
  enqueueRumEvent({
    metric: 'navigation_load',
    value,
    rating: rate(value),
    route: templateAt(0),
    navigation_type: nav.type,
    details: {
      dom_content_loaded_ms: Math.round(nav.domContentLoadedEventEnd),
      ttfb_ms: Math.round(nav.responseStart),
      transfer_size: nav.transferSize || 0,
    },
  });
}

/** RUM installs at idle, usually after `load`: report at once then, else after `load` settles. */
function observeNavigation(): void {
  if (document.readyState === 'complete') {
    reportNavigation();
    return;
  }
  window.addEventListener('load', () => setTimeout(reportNavigation, 0), { once: true });
}

const NAVIGATION_TYPES: Readonly<Record<Metric['navigationType'], RumNavigationType>> = {
  navigate: 'navigate',
  reload: 'reload',
  restore: 'reload',
  'back-forward': 'back_forward',
  'back-forward-cache': 'back_forward',
  prerender: 'prerender',
  'soft-navigation': 'soft_navigation',
};

/** The route of a vital: the template at its attribution time, else its navigation URL's template. */
function vitalRoute(attributionTime: number | undefined, navigationURL: string | undefined): string | null {
  const atTime = attributionTime === undefined ? null : routeTemplateAt(attributionTime);
  if (atTime !== null) return atTime;
  if (navigationURL) {
    try {
      return resolveRouteMeta(new URL(navigationURL).pathname).pattern;
    } catch {
      // A malformed URL: fall through to the committed template.
    }
  }
  return getCommittedRouteTemplate();
}

/** Metric ids already reported: one report per metric instance (page lifecycle or soft navigation). */
const reportedVitals = new Set<string>();

function reportVital(
  metric: Metric,
  name: 'lcp' | 'cls' | 'inp',
  attributionTime: number | undefined,
  details: RumDetails,
): void {
  if (reportedVitals.has(metric.id)) return;
  reportedVitals.add(metric.id);
  const route = vitalRoute(attributionTime, metric.navigationURL);
  if (route === null) return;
  enqueueRumEvent({
    metric: name,
    value: metric.value,
    rating: metric.rating === 'needs-improvement' ? 'needs_improvement' : metric.rating,
    route,
    navigation_type: NAVIGATION_TYPES[metric.navigationType] ?? null,
    details,
  });
}

function reportInp(metric: INPMetricWithAttribution): void {
  const { attribution } = metric;
  reportVital(metric, 'inp', attribution.interactionTime, {
    interaction_target: closedInteractionTarget(attribution.interactionTarget),
    input_delay_ms: Math.round(attribution.inputDelay),
    processing_ms: Math.round(attribution.processingDuration),
    presentation_ms: Math.round(attribution.presentationDelay),
  });
}

function reportLcp(metric: LCPMetricWithAttribution): void {
  const entry = metric.attribution.lcpEntry;
  const renderedAt = entry ? entry.renderTime || entry.startTime : undefined;
  reportVital(metric, 'lcp', renderedAt, { lcp_element: closedLcpElement(metric.attribution.target) });
}

function reportCls(metric: CLSMetricWithAttribution): void {
  reportVital(metric, 'cls', metric.attribution.largestShiftTime, {});
}

function flushWhenHidden(): void {
  if (document.visibilityState === 'hidden') flushRum();
}

/**
 * The web-vitals attribution build, loaded inside this lazy chunk. Its
 * hidden-page reports run in its own capture listeners, so the flush on
 * `visibilitychange` is added after them and carries what they queued.
 */
async function observeVitals(): Promise<void> {
  try {
    const vitals = await import('web-vitals/attribution');
    vitals.onINP(reportInp, { reportSoftNavs: true, generateTarget: (el) => nearestRumTarget(el) });
    vitals.onLCP(reportLcp, { reportSoftNavs: true, generateTarget: (el) => lcpElementBucket(el) });
    vitals.onCLS(reportCls, { reportSoftNavs: true, generateTarget: () => 'other' });
  } catch {
    // A chunk that cannot load costs only the vitals; the other events still flow.
  } finally {
    document.addEventListener('visibilitychange', flushWhenHidden);
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
 * fields, on the route template in effect when the call started. Sampled per
 * tab session and capped per document; a call longer than the schema's 600 s
 * is skipped and an oversized transfer is left out rather than clamped.
 * `transferSize` is the encoded (compressed) size on the wire, so the cap
 * binds rarely: the default 500-row GET /api/leads page is about 45 KB
 * gzipped (628 KB decoded).
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
        const details: RumDetails = { api_route: apiRoute };
        const transferSize = entry.transferSize || 0;
        if (transferSize <= MAX_RUM_NUMBER) details.transfer_size = transferSize;
        Object.assign(details, serverTimingDetails(entry.serverTiming ?? []));
        reported += 1;
        enqueueRumEvent({ metric: 'api_call', value, rating: 'info', route: templateAt(entry.startTime), details });
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
 * popstates) while the router's location never changes. Route and from_route
 * are registry templates, so a change inside one template (one borrower to
 * the next) is not a route change. Without a registered source nothing is
 * recorded.
 */
function observeRouteChanges(): void {
  const source = getRumRouteSource();
  if (!source) return;
  let route = templateAt(performance.now());
  source((pathname) => {
    const next = resolveRouteMeta(pathname).pattern;
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
          rating: rate(duration),
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
  const details: RumDetails = {
    error_name: report.errorName,
    error_kind: report.kind,
    error_source: report.source,
  };
  if (report.boundary) details.boundary = report.boundary;
  return { metric: 'client_error', value: 1, rating: 'info', route: report.route, details };
}

function startRum(): void {
  attachClientErrorSink((report) => enqueueRumEvent(clientErrorEvent(report)));
  observeNavigation();
  void observeVitals();
  observeApiCalls();
  observeRouteChanges();
  window.addEventListener('pagehide', flushRum);
}

export type RumScheduler = (task: () => void) => void;

/** After first paint, when the main thread is idle (setTimeout where requestIdleCallback is missing: Safari). */
export function scheduleIdle(task: () => void): void {
  if (typeof window.requestIdleCallback === 'function') {
    window.requestIdleCallback(() => task(), { timeout: IDLE_TIMEOUT_MS });
    return;
  }
  setTimeout(task, 0);
}

export function installRum(schedule: RumScheduler = scheduleIdle): void {
  if (typeof window === 'undefined') return;
  if (window.__mipRumInstalled) return;
  window.__mipRumInstalled = true;
  schedule(startRum);
}
