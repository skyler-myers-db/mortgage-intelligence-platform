import type { ClientErrorKind } from './chunkLoadError';
import { ROUTES } from './routeMeta';
import type { ClientErrorBoundary, ClientErrorName, ClientErrorSource } from './rumBridge';

/**
 * rumVocabulary — the closed RUM vocabularies and the event validator
 * (D-platform-process-d2; audit 2026-09-21 stack-08, runtime-09, quality-07).
 *
 * Lazy: only lib/rum imports it, so none of this reaches the initial chunk.
 * An interaction target is the nearest `[data-rum-target]` value, never a
 * CSS selector; an LCP element is a tag bucket. Both helpers always return a
 * member of their set ('other' for anything else), which is what web-vitals'
 * `generateTarget` needs: a null or undefined return would make it fall back
 * to a CSS selector, which the server refuses.
 *
 * Parity pin: tests/unit/test_rum_client_error.py parses the two `as const`
 * arrays below and asserts set equality with RUM_INTERACTION_TARGETS and
 * RUM_LCP_ELEMENTS in backend/schemas/telemetry.py, and every `pattern:` in
 * lib/routeMeta.ts against RUM_ROUTE_TEMPLATES. Change both sides together.
 */

export const RUM_INTERACTION_TARGETS = [
  'lead-row',
  'lead-expand',
  'lead-approve',
  'filter',
  'sort',
  'segment-card',
  'map',
  'genie-composer',
  'genie-panel',
  'nav',
  'palette',
  'drawer',
  'pager',
  'other',
] as const;

export const RUM_LCP_ELEMENTS = ['img', 'svg', 'h1', 'h2', 'h3', 'p', 'div', 'table', 'canvas', 'other'] as const;

export const RUM_METRICS = ['navigation_load', 'route_change', 'lcp', 'cls', 'inp', 'api_call', 'client_error'] as const;
export const RUM_RATINGS = ['good', 'needs_improvement', 'poor', 'info'] as const;
export const RUM_NAVIGATION_TYPES = ['navigate', 'reload', 'back_forward', 'prerender', 'soft_navigation'] as const;

/** The wire's closed detail keys (backend RumDetailKey). */
export const RUM_DETAIL_KEYS = [
  'dom_content_loaded_ms', 'ttfb_ms', 'transfer_size', 'from_route', 'duration_ms', 'attempt', 'retryable',
  'dependency', 'error_name', 'error_kind', 'error_source', 'boundary', 'api_route', 'cache', 'warehouse_ms',
  'lakebase_ms', 'total_ms', 'interaction_target', 'lcp_element', 'input_delay_ms', 'processing_ms',
  'presentation_ms',
] as const;

export type RumInteractionTarget = (typeof RUM_INTERACTION_TARGETS)[number];
export type RumLcpElement = (typeof RUM_LCP_ELEMENTS)[number];
export type RumMetric = (typeof RUM_METRICS)[number];
export type RumRating = (typeof RUM_RATINGS)[number];
export type RumNavigationType = (typeof RUM_NAVIGATION_TYPES)[number];
export type RumDetailKey = (typeof RUM_DETAIL_KEYS)[number];
export type RumDetails = { [K in RumDetailKey]?: string | number | boolean | null };

/** The keys each metric this client sends may carry. */
const DETAIL_KEYS_BY_METRIC: Readonly<Record<RumMetric, ReadonlySet<RumDetailKey>>> = {
  navigation_load: new Set(['dom_content_loaded_ms', 'ttfb_ms', 'transfer_size']),
  route_change: new Set(['from_route']),
  lcp: new Set(['lcp_element']),
  cls: new Set(),
  inp: new Set(['interaction_target', 'input_delay_ms', 'processing_ms', 'presentation_ms']),
  api_call: new Set(['api_route', 'transfer_size', 'cache', 'warehouse_ms', 'lakebase_ms', 'total_ms']),
  client_error: new Set(['error_name', 'error_kind', 'error_source', 'boundary']),
};

/** Every numeric key a metric above may carry: the *_ms timings and transfer_size. */
const isNumericKey = (key: string): boolean => key.endsWith('_ms') || key === 'transfer_size';
const MAX_RUM_NUMBER = 600_000;
const MAX_DETAIL_KEYS = 8;

const TARGETS: ReadonlySet<string> = new Set(RUM_INTERACTION_TARGETS);
const LCP_ELEMENTS: ReadonlySet<string> = new Set(RUM_LCP_ELEMENTS);
const RATINGS: ReadonlySet<string> = new Set(RUM_RATINGS);
const NAVIGATION_TYPES: ReadonlySet<string> = new Set(RUM_NAVIGATION_TYPES);
/** Every route registry pattern plus the not-found fallback. */
export const RUM_ROUTE_TEMPLATES: ReadonlySet<string> = new Set([...Object.values(ROUTES).map((route) => route.pattern), '/*']);

/**
 * The client-error vocabularies, spelled here rather than imported: an import
 * keeps lib/rumBridge's arrays exported from the initial chunk for this lazy
 * module (measured +0.10 KiB br initial JS). `satisfies` plus the
 * exhaustiveness check fail tsc when a union of lib/rumBridge (pinned to the
 * server) or lib/chunkLoadError gains or loses a member.
 */
const ERROR_NAMES = [
  'Error', 'TypeError', 'RangeError', 'ReferenceError', 'SyntaxError', 'EvalError', 'URIError', 'AggregateError',
  'ChunkLoadError', 'ApiError', 'GenieLiveError', 'NotFoundError', 'InvalidStateError', 'QuotaExceededError',
  'SecurityError', 'Other',
] as const satisfies readonly ClientErrorName[];
const ERROR_SOURCES = [
  'uncaught', 'caught', 'recoverable', 'preload', 'window', 'rejection',
] as const satisfies readonly ClientErrorSource[];
const ERROR_BOUNDARIES = ['root', 'route', 'console', 'genie', 'drawer'] as const satisfies readonly ClientErrorBoundary[];
const ERROR_KINDS = ['chunk', 'render'] as const satisfies readonly ClientErrorKind[];
type Exhaustive<Union, Listed> = [Exclude<Union, Listed>] extends [never] ? true : never;
export type ClientErrorVocabulariesComplete = [
  Exhaustive<ClientErrorName, (typeof ERROR_NAMES)[number]>,
  Exhaustive<ClientErrorSource, (typeof ERROR_SOURCES)[number]>,
  Exhaustive<ClientErrorBoundary, (typeof ERROR_BOUNDARIES)[number]>,
  Exhaustive<ClientErrorKind, (typeof ERROR_KINDS)[number]>,
] extends [true, true, true, true] ? true : never;
/** Compiles only while the four lists above match lib/rumBridge and lib/chunkLoadError exactly. */
export const CLIENT_ERROR_VOCABULARIES_COMPLETE: ClientErrorVocabulariesComplete = true;

/** The closed values of the string keys (the api_route template is checked by lib/rumApiRoute). */
const VALUE_SETS: Readonly<Partial<Record<RumDetailKey, ReadonlySet<string>>>> = {
  from_route: RUM_ROUTE_TEMPLATES,
  interaction_target: TARGETS,
  lcp_element: LCP_ELEMENTS,
  cache: new Set(['hit', 'miss', 'stale']),
  error_name: new Set(ERROR_NAMES),
  error_kind: new Set(ERROR_KINDS),
  error_source: new Set(ERROR_SOURCES),
  boundary: new Set(ERROR_BOUNDARIES),
};

function isElement(node: Node | null | undefined): node is Element {
  return node !== null && node !== undefined && node.nodeType === 1;
}

/** The nearest `[data-rum-target]` value when it is in the closed set, else 'other'. Never null. */
export function nearestRumTarget(node: Node | null | undefined): RumInteractionTarget {
  if (!isElement(node)) return 'other';
  const value = node.closest('[data-rum-target]')?.getAttribute('data-rum-target');
  return value && TARGETS.has(value) ? (value as RumInteractionTarget) : 'other';
}

/** The lowercase tag name when it is in the closed set, else 'other'. Never null. */
export function lcpElementBucket(node: Node | null | undefined): RumLcpElement {
  if (!isElement(node)) return 'other';
  const tag = node.tagName.toLowerCase();
  return LCP_ELEMENTS.has(tag) ? (tag as RumLcpElement) : 'other';
}

/** A value web-vitals reported as a target, folded onto the closed set (a selector becomes 'other'). */
export function closedInteractionTarget(value: string | undefined): RumInteractionTarget {
  return value !== undefined && TARGETS.has(value) ? (value as RumInteractionTarget) : 'other';
}

export function closedLcpElement(value: string | undefined): RumLcpElement {
  return value !== undefined && LCP_ELEMENTS.has(value) ? (value as RumLcpElement) : 'other';
}

function isRumNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= MAX_RUM_NUMBER;
}

function isValidDetail(key: RumDetailKey, value: unknown): boolean {
  if (isNumericKey(key)) return isRumNumber(value);
  if (key === 'api_route') return typeof value === 'string' && value.length <= 160 && value.startsWith('/api/');
  const allowed = VALUE_SETS[key];
  return allowed !== undefined && typeof value === 'string' && allowed.has(value);
}

/** Re-validation of one event against the closed sets the server enforces. */
export interface RumEventShape {
  metric: string;
  value: number;
  rating: string;
  route: string;
  navigation_type?: string | null;
  details?: Partial<Record<string, string | number | boolean | null>>;
}

export function isValidRumEvent(event: RumEventShape): boolean {
  const allowed: ReadonlySet<RumDetailKey> | undefined = Object.prototype.hasOwnProperty.call(DETAIL_KEYS_BY_METRIC, event.metric)
    ? DETAIL_KEYS_BY_METRIC[event.metric as RumMetric]
    : undefined;
  if (!allowed || !isRumNumber(event.value) || !RATINGS.has(event.rating)) return false;
  if (!RUM_ROUTE_TEMPLATES.has(event.route)) return false;
  const navigation = event.navigation_type;
  if (navigation !== undefined && navigation !== null && !NAVIGATION_TYPES.has(navigation)) return false;
  const details = event.details ?? {};
  const keys = Object.keys(details);
  if (keys.length > MAX_DETAIL_KEYS) return false;
  return keys.every((key) => allowed.has(key as RumDetailKey) && isValidDetail(key as RumDetailKey, details[key]));
}
