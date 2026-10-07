import type { ClientErrorKind } from './chunkLoadError';
import { resolveRouteMeta } from './routeMeta';

/**
 * rumBridge — the initial-chunk half of browser RUM (2026-09-21 UI/UX audit
 * stack-01, shell-01, states-01, quality-01).
 *
 * lib/rum.ts is lazy: it loads only once the session says RUM is on
 * (`mip_rum_enabled`: on by default through the deploy payload, off in the
 * code default; D-platform-process-d1). Client errors and router location
 * changes happen long before that, in code that is in the initial chunk
 * (main.tsx, lib/clientErrorLog). This module is what they talk to, so the
 * initial chunk carries only these vocabularies, a five-slot queue and the
 * committed route TEMPLATE with its short timeline, never the RUM observers.
 * It must never import lib/rum.
 *
 * Nothing leaves the browser from here. A report waits in the buffer until
 * rum.ts attaches its sink; while RUM is not installed it simply stays.
 *
 * Parity pin: tests/unit/test_rum_client_error.py parses the three `as const`
 * arrays below and asserts set equality with CLIENT_ERROR_NAMES,
 * CLIENT_ERROR_SOURCES and CLIENT_ERROR_BOUNDARIES in
 * backend/schemas/telemetry.py, and the ClientErrorKind union
 * (lib/chunkLoadError) against CLIENT_ERROR_KINDS. Change both sides together.
 */

export const CLIENT_ERROR_NAMES = [
  'Error',
  'TypeError',
  'RangeError',
  'ReferenceError',
  'SyntaxError',
  'EvalError',
  'URIError',
  'AggregateError',
  'ChunkLoadError',
  'ApiError',
  'GenieLiveError',
  'NotFoundError',
  'InvalidStateError',
  'QuotaExceededError',
  'SecurityError',
  'Other',
] as const;

export const CLIENT_ERROR_SOURCES = [
  'uncaught',
  'caught',
  'recoverable',
  'preload',
  'window',
  'rejection',
] as const;

export const CLIENT_ERROR_BOUNDARIES = ['root', 'route', 'console', 'genie', 'drawer'] as const;

export type ClientErrorName = (typeof CLIENT_ERROR_NAMES)[number];
export type ClientErrorSource = (typeof CLIENT_ERROR_SOURCES)[number];
export type ClientErrorBoundary = (typeof CLIENT_ERROR_BOUNDARIES)[number];

/** A telemetry-safe client error: four closed values and a route template. */
export interface QueuedClientError {
  source: ClientErrorSource;
  kind: ClientErrorKind;
  errorName: ClientErrorName;
  boundary: ClientErrorBoundary | null;
  /** The route registry's pattern (`/borrower-360/:id`), never a pathname. */
  route: string;
}

export type ClientErrorSink = (report: QueuedClientError) => void;

/** At most this many reports per document, before or after a sink attaches. */
const MAX_REPORTS_PER_DOCUMENT = 5;

const reportedKeys = new Set<string>();
let buffered: QueuedClientError[] = [];
let sink: ClientErrorSink | null = null;

/**
 * Queue one report. Deduped per document by kind + name + boundary, and
 * capped at five, so a render loop or a noisy listener can never flood the
 * telemetry endpoint.
 */
export function queueClientError(report: QueuedClientError): void {
  const key = `${report.kind}|${report.errorName}|${report.boundary ?? ''}`;
  if (reportedKeys.has(key) || reportedKeys.size >= MAX_REPORTS_PER_DOCUMENT) return;
  reportedKeys.add(key);
  if (sink) sink(report);
  else buffered.push(report);
}

/** rum.ts attaches its sink on install; it receives the buffered reports first. */
export function attachClientErrorSink(next: ClientErrorSink): void {
  sink = next;
  const pending = buffered;
  buffered = [];
  for (const report of pending) next(report);
}

export type RumRouteListener = (pathname: string) => void;
/** Subscribe to the router's COMMITTED location; returns the unsubscribe. */
export type RumRouteSource = (listener: RumRouteListener) => () => void;

let routeSource: RumRouteSource | null = null;
let committedTemplate: string | null = null;
/** (performance.now(), registry template) per committed change; templates only, never pathnames. */
const timeline: Array<[number, string]> = [];

function commitTemplate(pathname: string, at: number): void {
  const template = resolveRouteMeta(pathname).pattern;
  if (template === committedTemplate) return;
  committedTemplate = template;
  timeline.push([at, template]);
  if (timeline.length > 64) timeline.shift();
}

/**
 * main.tsx registers the data router here, synchronously right after
 * creating it and before any navigation, so the initial template is the
 * router's committed location and holds from time 0. rum.ts can then report
 * a route_change only when the router really committed a new location (a
 * Back the unsaved-changes guard blocks never changes it), and both RUM and
 * client errors read the template in effect at a given time (quality-01).
 */
export function setRumRouteSource(source: RumRouteSource): void {
  routeSource = source;
  commitTemplate(window.location.pathname, 0);
  source((pathname) => commitTemplate(pathname, performance.now()));
}

/** The router's committed route template, or null before main.tsx registers the router. */
export function getCommittedRouteTemplate(): string | null {
  return committedTemplate;
}

/** The template in effect at `t` (performance.now()), or null when `t` predates the kept timeline. */
export function routeTemplateAt(t: number): string | null {
  for (let index = timeline.length - 1; index >= 0; index -= 1) {
    if (timeline[index][0] <= t) return timeline[index][1];
  }
  return null;
}

export function getRumRouteSource(): RumRouteSource | null {
  return routeSource;
}
