import { apiPath } from './apiPaths';

type RumMetric =
  | 'navigation_load'
  | 'route_change'
  | 'lcp'
  | 'cls'
  | 'inp'
  | 'long_task'
  | 'api_call';

type RumRating = 'good' | 'needs_improvement' | 'poor' | 'info';

interface RumEvent {
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

function enqueue(event: RumEvent): void {
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
    enqueue({
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
      enqueue({
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
      enqueue({
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
      enqueue({
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
        enqueue({
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

function observeRouteChanges(): void {
  let route = currentRoute();
  const reportRoute = () => {
    const next = currentRoute();
    if (next === route) return;
    const previous = route;
    const start = performance.now();
    route = next;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const duration = performance.now() - start;
        enqueue({
          metric: 'route_change',
          value: duration,
          rating: rate('route_change', duration),
          route: next,
          details: { from_route: previous },
        });
      });
    });
  };
  const pushState = history.pushState;
  const replaceState = history.replaceState;
  history.pushState = function patchedPushState(...args) {
    pushState.apply(this, args);
    reportRoute();
  };
  history.replaceState = function patchedReplaceState(...args) {
    replaceState.apply(this, args);
    reportRoute();
  };
  window.addEventListener('popstate', reportRoute);
}

export function installRum(): void {
  if (typeof window === 'undefined') return;
  if (window.__mipRumInstalled) return;
  window.__mipRumInstalled = true;
  observeNavigation();
  observeLcp();
  observeCls();
  observeInp();
  observeLongTasks();
  observeRouteChanges();
  window.addEventListener('pagehide', flushRum);
}
