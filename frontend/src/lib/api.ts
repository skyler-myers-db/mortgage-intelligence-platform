import type {
  Borrower360,
  BorrowerLifecycle,
  CallDisposition,
  CampaignListResponse,
  CampaignSummary,
  ConfigOptions,
  CountyRollupResponse,
  DataEstateResponse,
  EconomicsAnalyticsResponse,
  ExecutiveAnalyticsResponse,
  GeographyAnalyticsResponse,
  LeadSummary,
  LeadAssignment,
  OfferRecommendation,
  PortfolioCreateResponse,
  PortfolioPreview,
  SegmentCode,
  SegmentSummary,
  SalesTeamMember,
  SalesAgingLead,
  SalesConversionResponse,
  SalesStandupResponse,
  SegmentAnalyticsResponse,
  StateRollupResponse,
  SignalAnalyticsResponse,
  SavedDraft,
  SavedDraftInput,
  SavedLead,
  SavedLeadInput,
  GenieActionResult,
  GenieActionSuggestion,
  GenieStartResult,
  ZipRollupResponse,
  WorkspaceMutationResult,
  WorkspaceState,
} from '../types';
import { apiPath } from './apiPaths';

/**
 * API client — calls the FastAPI backend and surfaces errors honestly.
 *
 * Per CLAUDE.md: the app runs on real Unity Catalog data or it fails
 * visibly. This module does NOT silently fall back to mock fixtures on
 * error. Every failure throws a structured `ApiError` that callers
 * render as an explicit empty/error state (e.g. "Couldn't load
 * segments"). Transient 503s are handled by the built-in retry loop
 * that mirrors the backend's Resilient wrapper cadence; the
 * <DegradedBanner> polls /api/health in parallel and surfaces the
 * "backend is warming up" messaging while retries are in flight.
 *
 * The only method that tolerates failure is `health()`: an unreachable
 * /api/health returns `{ status: 'unreachable', mode: 'unknown',
 * dependencies: {} }`. That's honest status, not synthetic data.
 *
 * Cancellation (round-2 hole-finder #10/#11, 2026-04-23): every method
 * accepts an optional `AbortSignal`. Callers pass a controller's
 * signal in the effect body and call `controller.abort()` from the
 * cleanup function so an unmount / rapid-refilter actually cancels
 * the in-flight request. An aborted fetch throws a DOMException with
 * name === 'AbortError'; we re-throw it as an ApiError with
 * `retryable: false` so call sites can `if (err.name === 'AbortError')`
 * cheaply, or inspect `.aborted` on the ApiError.
 */

export interface HealthPayload {
  status: string;
  mode: string;
  warehouse_id?: string | null;
  app_env?: string;
  dependencies?: Record<string, string>;
  circuit_breakers?: Record<string, string>;
  actor_cache_key?: string | null;
}

export interface ApproveResult {
  approved: boolean;
  approval_id?: string | null;
  audit_event_id?: string | null;
}

export interface RejectResult {
  rejected: boolean;
  approval_id?: string | null;
  audit_event_id?: string | null;
}

export interface OutreachDraftResult {
  borrower_id: string;
  offer_code: string;
  channel: 'email' | 'sms' | 'direct_mail';
  subject?: string | null;
  body: string;
  status: 'draft';
  disclosure_version: string;
  disclosure_state: string;
  marketing_eligible: boolean;
}

export interface GenieResult {
  answer: string;
  source?: string;
  trusted_assets?: string[];
  conversation_id?: string;
  message_id?: string | null;
  elapsed_ms?: number | null;
  question_hash?: string | null;
  sql_query?: string | null;
  row_count?: number | null;
  proof?: Record<string, unknown> | null;
  visualization?: Record<string, unknown> | null;
  actions?: GenieActionSuggestion[];
  metric_value?: string | null;
  table_rows?: Record<string, unknown>[] | null;
  follow_up_questions?: string[];
}

export interface AuditEventRow {
  event_id: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string;
  payload_json: Record<string, unknown>;
  evidence_ids: string[];
  created_at: string;
  event_type?: string | null;
  subject_clip?: string | null;
  subject_segment?: string | null;
  request_id?: string | null;
}

/**
 * Segment multi-select semantics forwarded to /api/leads and geo rollups.
 * `any` = OR, `all` = AND. Segment Intelligence uses `all` so each
 * additional selected card narrows the ranked borrowers and map.
 */
export type SegmentFilterMode = 'any' | 'all';
export type LeadFunnelStage =
  | 'addressable'
  | 'in_the_money'
  | 'high_opportunity'
  | 'offer_recommended'
  | 'approved'
  | 'actioned';

export interface LeadQueryOptions {
  segmentCodes?: SegmentCode[];
  /** `all` means borrowers must match every code in `segmentCodes`. */
  segmentMode?: SegmentFilterMode;
  /** Exact native Analytics Lead Funnel drilldown stage. */
  funnelStage?: LeadFunnelStage | null;
  targetLenderRef?: string | null;
  cohortId?: string | null;
  portfolioCriteria?: GeoQueryCriteria;
  approvalStatus?: 'pending' | 'approved' | 'rejected' | 'hold' | 'any';
  outreachStatus?: 'none' | 'queued' | 'actioned' | 'sent' | 'bounced' | 'replied' | 'any';
  assignedTo?: string | null;
  agedDays?: number | null;
  limit?: number;
}

export interface AnalyticsQueryOptions {
  states?: string[] | null;
  state?: string | null;
  segmentCodes?: SegmentCode[] | null;
  segmentMode?: SegmentFilterMode;
  signalTypes?: string[] | null;
  signalType?: string | null;
  days?: number | null;
}

export interface AssignmentResponse {
  assignment: LeadAssignment;
  audit_event_id?: string | null;
}

export interface DispositionResponse {
  disposition: CallDisposition;
  audit_event_id?: string | null;
}

export interface LeadsPageResult {
  leads: LeadSummary[];
  totalMatching: number | null;
  returnedRows: number | null;
  truncatedAt: number | null;
}

export type GeoQueryCriteria = Record<string, string | number | readonly string[] | null | undefined>;

function appendPortfolioCriteria(params: URLSearchParams, criteria?: GeoQueryCriteria | null) {
  if (!criteria) return;
  Object.entries(criteria).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    if (Array.isArray(value)) {
      if (value.length > 0) params.set(key, value.join(','));
      return;
    }
    params.set(key, String(value));
  });
}

function analyticsPath(scope: string, opts: AnalyticsQueryOptions = {}): string {
  const params = new URLSearchParams();
  const states = opts.states?.length ? opts.states : opts.state ? [opts.state] : [];
  if (states.length > 0) params.set('states', states.map((state) => state.toUpperCase()).join(','));
  if (opts.segmentCodes && opts.segmentCodes.length > 0) {
    params.set('segment_codes', opts.segmentCodes.join(','));
    params.set('segment_mode', opts.segmentMode ?? 'any');
  }
  const signalTypes = opts.signalTypes?.length ? opts.signalTypes : opts.signalType ? [opts.signalType] : [];
  if (signalTypes.length > 0) params.set('signal_types', signalTypes.join(','));
  if (opts.days) params.set('days', String(opts.days));
  const qs = params.toString();
  return qs ? `/api/analytics/${scope}?${qs}` : `/api/analytics/${scope}`;
}

/**
 * Resilience/backpressure reason codes surfaced by the backend's transient
 * failure contracts. Dependency-down responses use HTTP 503; backpressure
 * responses use HTTP 429 plus Retry-After.
 *
 *   - "warming_up"        — dependency is initialising after idle; poll 5s.
 *   - "breaker_open"      — circuit breaker tripped; respect the 30s
 *                           cooldown before probing again.
 *   - "retries_exhausted" — backend already burned its retry budget;
 *                           further client retries will not help.
 *   - "rate_limited"      — request budget exhausted; Retry-After is the
 *                           source of truth when present.
 *   - "dependency_saturated" — concurrency guard is full for a dependency.
 */
export type ApiErrorReason =
  | 'warming_up'
  | 'breaker_open'
  | 'retries_exhausted'
  | 'rate_limited'
  | 'dependency_saturated';

export interface ApiValidationIssue {
  field: string;
  message: string;
  location: string[];
}

/** Structured error thrown by every api.* method on non-2xx or network failure. */
export class ApiError extends Error {
  readonly path: string;
  readonly status: number | null;
  readonly retryable: boolean;
  readonly dependency: string | null;
  readonly correlationId: string | null;
  readonly aborted: boolean;
  readonly validationIssues: ApiValidationIssue[];
  /**
   * Transient classification from the backend resilience/backpressure layer,
   * or `null` when the body did not include one. See `ApiErrorReason`.
   */
  readonly reason: ApiErrorReason | string | null;

  constructor(
    message: string,
    opts: {
      path: string;
      status?: number | null;
      retryable?: boolean;
      dependency?: string | null;
      correlationId?: string | null;
      aborted?: boolean;
      reason?: ApiErrorReason | string | null;
      validationIssues?: ApiValidationIssue[];
    } = { path: '' },
  ) {
    super(message);
    this.name = opts.aborted ? 'AbortError' : 'ApiError';
    this.path = opts.path;
    this.status = opts.status ?? null;
    this.retryable = Boolean(opts.retryable);
    this.dependency = opts.dependency ?? null;
    this.correlationId = opts.correlationId ?? null;
    this.aborted = Boolean(opts.aborted);
    this.reason = opts.reason ?? null;
    this.validationIssues = opts.validationIssues ?? [];
  }
}

/**
 * Helper for cold-start UX: true when the error is a 503 with
 * `retryable: true` and a named dependency (warehouse or lakebase
 * warming up after idle auto-suspend). Call sites use this to switch
 * to "warming up — attempt N of M" messaging instead of the red
 * "Backend unavailable" banner. The backend ships these fields in the
 * 503 body via `_dependency_down_handler`; mirrored into `ApiError`
 * here so UI code doesn't need to parse the error message string.
 */
export function isWarmingUpError(err: unknown): err is ApiError {
  if (!(err instanceof ApiError)) return false;
  if (err.aborted) return false;
  if (err.status !== 503) return false;
  if (!err.retryable) return false;
  // `retries_exhausted` means the backend already spent its retry budget;
  // another client-side warming loop hides a real failure. `breaker_open`
  // is still a transient dependency state, but `planForReason` slows it to
  // the backend breaker cooldown rather than hammering every few seconds.
  if (err.reason === 'retries_exhausted') {
    return false;
  }
  return true;
}

/** Human label for the dependency name in the warming-up copy. */
export function dependencyLabel(dep: string | null | undefined): string {
  if (!dep) return 'Backend';
  const normalized = dep.toLowerCase();
  if (normalized === 'warehouse' || normalized.includes('warehouse')) return 'Warehouse';
  if (normalized === 'lakebase' || normalized.includes('lakebase')) return 'Lakebase';
  if (normalized === 'genie') return 'Genie';
  // Fallback: title-case the dependency string.
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

/** True when the error was caused by a caller-driven AbortController abort. */
export function isAbortError(err: unknown): boolean {
  if (!err) return false;
  if (err instanceof ApiError) return err.aborted;
  if (err instanceof Error && err.name === 'AbortError') return true;
  return false;
}

/**
 * Slice-6 retry protocol for `retryable: true` transient responses.
 *
 * The backend's resilience layer (CircuitBreaker + Resilient wrapper)
 * returns HTTP 503 with `{detail, retryable: true, dependency}` when a
 * dependency's circuit is open or all retries exhausted. Backpressure
 * returns HTTP 429 with `{retryable: true}` plus `Retry-After`. We treat
 * both as transient signals and re-fetch with bounded backoff.
 */

function _sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(_abortError());
      return;
    }
    const t = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(t);
      reject(_abortError());
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

function _abortError(): DOMException {
  // DOMException exists in browsers + jsdom + recent Node. Fall back to
  // a plain Error with name='AbortError' in ancient runtimes.
  try {
    return new DOMException('The operation was aborted.', 'AbortError');
  } catch {
    const err = new Error('The operation was aborted.');
    err.name = 'AbortError';
    return err as unknown as DOMException;
  }
}

function _newRequestId(): string {
  // Prefer the standard crypto.randomUUID() (available in all modern
  // browsers + jsdom >= 22). Fall back to a time-based random for test
  // environments where crypto is stubbed. The backend only requires
  // uniqueness within its own unique-index window, not RFC4122
  // compliance — uniqueness is what matters for idempotency.
  const c: Crypto | undefined =
    typeof globalThis !== 'undefined' ? globalThis.crypto : undefined;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const n = ch === 'x' ? Math.floor(Math.random() * 16) : 8 + Math.floor(Math.random() * 4);
    return n.toString(16);
  });
}

export interface Retryable503Parsed {
  retryable: boolean;
  dependency: string | null;
  detail: string | null;
  correlationId: string | null;
  retryAfterMs: number | null;
  /**
   * Cycle-13 resilience classification: "warming_up" | "breaker_open" |
   * "retries_exhausted", or null if the backend didn't include one.
   * The frontend retry hook branches on this field.
   */
  reason: ApiErrorReason | string | null;
}

/**
 * Parse a transient 503/429 body emitted by `_dependency_down_handler`
 * or `BackpressureMiddleware`. Exported so
 * tests can exercise the classification logic without mocking `fetch`.
 * Returns a fully-null parse for any non-transient response.
 */
export async function _parseRetryableBody(res: Response): Promise<Retryable503Parsed> {
  const empty: Retryable503Parsed = {
    retryable: false,
    dependency: null,
    detail: null,
    correlationId: null,
    retryAfterMs: null,
    reason: null,
  };
  if (res.status !== 503 && res.status !== 429) return empty;
  try {
    const body = (await res.clone().json()) as {
      retryable?: boolean;
      dependency?: string;
      detail?: string;
      correlation_id?: string;
      reason?: string;
    };
    return {
      retryable: body?.retryable === true,
      dependency: body?.dependency ?? null,
      detail: body?.detail ?? null,
      correlationId: body?.correlation_id ?? null,
      retryAfterMs: _parseRetryAfterMs(res.headers.get('Retry-After')),
      reason: body?.reason ?? null,
    };
  } catch {
    return empty;
  }
}

function _parseRetryAfterMs(headerValue: string | null): number | null {
  if (!headerValue) return null;
  const seconds = Number.parseFloat(headerValue);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
  const dateMs = Date.parse(headerValue);
  if (!Number.isFinite(dateMs)) return null;
  return Math.max(0, dateMs - Date.now());
}

function _fieldFromLoc(loc: unknown): { field: string; location: string[] } {
  const location = Array.isArray(loc)
    ? loc.map((part) => String(part))
    : [];
  const publicParts = location.filter((part) => !['query', 'body', 'path'].includes(part));
  return {
    field: publicParts.length > 0 ? publicParts.join('.') : location.join('.') || 'request',
    location,
  };
}

export async function _parseHttpErrorBody(res: Response): Promise<{
  message: string | null;
  validationIssues: ApiValidationIssue[];
}> {
  try {
    const body = (await res.clone().json()) as {
      detail?: string | Array<{ loc?: unknown; msg?: string; message?: string }>;
      message?: string;
      error?: string;
    };
    const detail = body?.detail;
    if (Array.isArray(detail)) {
      const validationIssues = detail.map((item) => {
        const loc = _fieldFromLoc(item?.loc);
        return {
          field: loc.field,
          location: loc.location,
          message: item?.msg ?? item?.message ?? 'Invalid value.',
        };
      });
      return {
        message: validationIssues
          .map((issue) => `${issue.field}: ${issue.message}`)
          .join('; '),
        validationIssues,
      };
    }
    if (typeof detail === 'string' && detail.trim().length > 0) {
      return { message: detail, validationIssues: [] };
    }
    if (typeof body?.message === 'string' && body.message.trim().length > 0) {
      return { message: body.message, validationIssues: [] };
    }
    if (typeof body?.error === 'string' && body.error.trim().length > 0) {
      return { message: body.error, validationIssues: [] };
    }
  } catch {
    // Non-JSON error bodies fall through to the HTTP status text below.
  }
  return { message: null, validationIssues: [] };
}

async function _fetchWithRetry(
  path: string,
  init?: RequestInit,
  attempts = 3,
  signal?: AbortSignal,
): Promise<Response> {
  let lastRes: Response | null = null;
  for (let i = 0; i < attempts; i++) {
    if (signal?.aborted) throw _abortError();
    const res = await fetch(path, { ...init, signal });
    if (res.ok) return res;
    const parsed = await _parseRetryableBody(res);
    if (!parsed.retryable) return res;
    lastRes = res;
    if (i === attempts - 1) break;
    const delay = parsed.retryAfterMs ?? Math.min(2000, 200 * 2 ** i);
    const jittered = parsed.retryAfterMs === null
      ? delay * (0.5 + Math.random())
      : delay;
    await _sleep(jittered, signal);
  }
  return lastRes as Response;
}

async function _throwFromResponse(res: Response, path: string): Promise<never> {
  const parsed = await _parseRetryableBody(res);
  const parsedBody = await _parseHttpErrorBody(res);
  const msg = parsed.detail ?? parsedBody.message ?? `${res.status} ${res.statusText}`;
  throw new ApiError(msg, {
    path,
    status: res.status,
    retryable: parsed.retryable,
    dependency: parsed.dependency,
    correlationId: parsed.correlationId,
    reason: parsed.reason,
    validationIssues: parsedBody.validationIssues,
  });
}

function _wrapFetchError(err: unknown, path: string): ApiError {
  // A caller-triggered abort surfaces as a DOMException with name ===
  // 'AbortError'. Preserve that signal so consumers can short-circuit
  // without spamming the user with a red error banner.
  if (err instanceof Error && err.name === 'AbortError') {
    return new ApiError('request aborted', {
      path,
      status: null,
      retryable: false,
      aborted: true,
    });
  }
  const message = err instanceof Error ? err.message : 'network error';
  return new ApiError(message, { path, status: null, retryable: false });
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const requestPath = apiPath(path);
  let res: Response;
  try {
    res = await _fetchWithRetry(requestPath, undefined, 3, signal);
  } catch (err) {
    throw _wrapFetchError(err, requestPath);
  }
  if (!res.ok) await _throwFromResponse(res, requestPath);
  return (await res.json()) as T;
}

async function getJsonWithHeaders<T>(path: string, signal?: AbortSignal): Promise<{ data: T; headers: Headers }> {
  const requestPath = apiPath(path);
  let res: Response;
  try {
    res = await _fetchWithRetry(requestPath, undefined, 3, signal);
  } catch (err) {
    throw _wrapFetchError(err, requestPath);
  }
  if (!res.ok) await _throwFromResponse(res, requestPath);
  return { data: (await res.json()) as T, headers: res.headers };
}

async function postJson<T, B>(path: string, body: B, signal?: AbortSignal): Promise<T> {
  const requestPath = apiPath(path);
  let res: Response;
  try {
    res = await _fetchWithRetry(
      requestPath,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body ?? {}),
      },
      3,
      signal,
    );
  } catch (err) {
    throw _wrapFetchError(err, requestPath);
  }
  if (!res.ok) await _throwFromResponse(res, requestPath);
  return (await res.json()) as T;
}

async function putJson<T, B>(path: string, body: B, signal?: AbortSignal): Promise<T> {
  const requestPath = apiPath(path);
  let res: Response;
  try {
    res = await _fetchWithRetry(
      requestPath,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body ?? {}),
      },
      3,
      signal,
    );
  } catch (err) {
    throw _wrapFetchError(err, requestPath);
  }
  if (!res.ok) await _throwFromResponse(res, requestPath);
  return (await res.json()) as T;
}

async function patchJson<T, B>(path: string, body: B, signal?: AbortSignal): Promise<T> {
  const requestPath = apiPath(path);
  let res: Response;
  try {
    res = await _fetchWithRetry(
      requestPath,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body ?? {}),
      },
      3,
      signal,
    );
  } catch (err) {
    throw _wrapFetchError(err, requestPath);
  }
  if (!res.ok) await _throwFromResponse(res, requestPath);
  return (await res.json()) as T;
}

async function deleteJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const requestPath = apiPath(path);
  let res: Response;
  try {
    res = await _fetchWithRetry(
      requestPath,
      { method: 'DELETE' },
      3,
      signal,
    );
  } catch (err) {
    throw _wrapFetchError(err, requestPath);
  }
  if (!res.ok) await _throwFromResponse(res, requestPath);
  return (await res.json()) as T;
}

export const api = {
  /**
   * Honest health probe. A dead backend returns an "unreachable" status
   * object instead of throwing — callers render dependency state as
   * `unknown`, never as synthesized "up". A caller-triggered abort
   * re-throws so `HealthProvider` can cancel in-flight polls on
   * unmount.
   */
  health: async (signal?: AbortSignal): Promise<HealthPayload> => {
    try {
      return await getJson<HealthPayload>('/api/health', signal);
    } catch (err) {
      if (isAbortError(err)) throw err;
      return { status: 'unreachable', mode: 'unknown', dependencies: {} };
    }
  },

  analyticsExecutive: (signal?: AbortSignal, opts: AnalyticsQueryOptions = {}) =>
    getJson<ExecutiveAnalyticsResponse>(analyticsPath('executive', opts), signal),

  analyticsGeography: (signal?: AbortSignal, opts: AnalyticsQueryOptions = {}) =>
    getJson<GeographyAnalyticsResponse>(analyticsPath('geography', opts), signal),

  analyticsEconomics: (signal?: AbortSignal, opts: AnalyticsQueryOptions = {}) =>
    getJson<EconomicsAnalyticsResponse>(analyticsPath('economics', opts), signal),

  analyticsSegments: (signal?: AbortSignal, opts: AnalyticsQueryOptions = {}) =>
    getJson<SegmentAnalyticsResponse>(analyticsPath('segments', opts), signal),

  analyticsSignals: (signal?: AbortSignal, opts: AnalyticsQueryOptions = {}) =>
    getJson<SignalAnalyticsResponse>(analyticsPath('signals', opts), signal),

  portfolioPreview: (
    criteria: Record<string, unknown> = {},
    signal?: AbortSignal,
  ) =>
    postJson<PortfolioPreview, { criteria: Record<string, unknown> }>(
      '/api/portfolio/preview',
      { criteria },
      signal,
    ),

  portfolioCreate: (
    name: string,
    criteria: Record<string, unknown> = {},
    config: Partial<{
      suppression_policy: Record<string, unknown>;
      message_variants: Record<string, unknown>[];
      channel_cascade: Record<string, unknown>[];
      send_window: Record<string, unknown>;
      holdout: Record<string, unknown>;
      roi_assumptions: Record<string, unknown>;
    }> = {},
    signal?: AbortSignal,
  ) =>
    postJson<PortfolioCreateResponse, {
      name: string;
      criteria: Record<string, unknown>;
      suppression_policy: Record<string, unknown>;
      message_variants: Record<string, unknown>[];
      channel_cascade: Record<string, unknown>[];
      send_window: Record<string, unknown>;
      holdout: Record<string, unknown>;
      roi_assumptions: Record<string, unknown>;
    }>(
      '/api/portfolio/create',
      {
        name,
        criteria,
        suppression_policy: config.suppression_policy ?? { default: 'eligible_only', frequency_cap_days: 30 },
        message_variants: config.message_variants ?? [],
        channel_cascade: config.channel_cascade ?? [
          { channel: 'email', step: 1 },
          { channel: 'sms', step: 2, after_days: 3 },
          { channel: 'direct_mail', step: 3, after_days: 10 },
        ],
        send_window: config.send_window ?? { days: ['Tuesday', 'Wednesday', 'Thursday'], start_local: '09:00', end_local: '16:00' },
        holdout: config.holdout ?? { method: 'hash_modulo', size_pct: 10 },
        roi_assumptions: config.roi_assumptions ?? { source: 'operator_required_before_live_send' },
      },
      signal,
    ),

  segments: (
    signal?: AbortSignal,
    segmentCodes?: SegmentCode[] | null,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => {
    const params = new URLSearchParams();
    if (segmentCodes && segmentCodes.length > 0) {
      params.set('segment_codes', segmentCodes.join(','));
      params.set('segment_mode', segmentMode);
    }
    appendPortfolioCriteria(params, portfolioCriteria);
    const qs = params.toString();
    return getJson<SegmentSummary[]>(qs ? `/api/segments?${qs}` : '/api/segments', signal);
  },

  stateRollups: (
    segmentCodes?: string[] | null,
    signal?: AbortSignal,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => {
    // 2026-05-04 (FIX G): when a segment filter is active, fetch the
    // segment-aware per-state counts so the choropleth tooltip + the
    // shading reflect "marketable in <segment>" instead of the cross-
    // segment total. No filter ⇒ the original cross-segment query.
    const params = new URLSearchParams();
    if (segmentCodes && segmentCodes.length > 0) {
      params.set('segment_codes', segmentCodes.join(','));
      params.set('segment_mode', segmentMode);
    }
    appendPortfolioCriteria(params, portfolioCriteria);
    const qs = params.toString();
    return getJson<StateRollupResponse>(
      qs ? `/api/geo/state-rollups?${qs}` : '/api/geo/state-rollups',
      signal,
    );
  },

  countyRollups: (
    state: string,
    signal?: AbortSignal,
    segmentCodes?: string[] | null,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => {
    const params = new URLSearchParams();
    params.set('state', state.toUpperCase());
    if (segmentCodes && segmentCodes.length > 0) {
      params.set('segment_codes', segmentCodes.join(','));
      params.set('segment_mode', segmentMode);
    }
    appendPortfolioCriteria(params, portfolioCriteria);
    return getJson<CountyRollupResponse>(
      `/api/geo/county-rollups?${params.toString()}`,
      signal,
    );
  },

  zipRollups: (
    countyFips: string,
    signal?: AbortSignal,
    segmentCodes?: string[] | null,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => {
    const params = new URLSearchParams();
    params.set('county_fips', countyFips);
    if (segmentCodes && segmentCodes.length > 0) {
      params.set('segment_codes', segmentCodes.join(','));
      params.set('segment_mode', segmentMode);
    }
    appendPortfolioCriteria(params, portfolioCriteria);
    return getJson<ZipRollupResponse>(
      `/api/geo/zip-rollups?${params.toString()}`,
      signal,
    );
  },

  leadsPage: (
    segment?: string,
    signal?: AbortSignal,
    geo?: { state?: string; zip?: string; county?: string; states?: string[]; zips?: string[]; borrowerIds?: string[] },
    opts: LeadQueryOptions = {},
  ) => {
    // 2026-05-04 FIX β: forward state + zip to the API so the backend
    // queries borrower_360 directly (no score floor) when a geo
    // narrowing is requested. Without this, narrow ZIP/state filters
    // returned 0 rows because the unfiltered top-500 from
    // lead_population didn't include the geo's borrowers.
    const params = new URLSearchParams();
    if (segment) params.set('segment', segment);
    if (opts.segmentCodes && opts.segmentCodes.length > 0) {
      params.set('segment_codes', opts.segmentCodes.join(','));
      params.set('segment_mode', opts.segmentMode ?? 'any');
    }
    if (opts.funnelStage) params.set('funnel_stage', opts.funnelStage);
    if (opts.limit) params.set('limit', String(opts.limit));
    if (geo?.state) params.set('state', geo.state);
    if (geo?.zip) params.set('zip', geo.zip);
    if (geo?.county) params.set('county', geo.county);
    if (geo?.states && geo.states.length > 0) params.set('states', geo.states.join(','));
    if (geo?.zips && geo.zips.length > 0) params.set('zips', geo.zips.join(','));
    if (geo?.borrowerIds && geo.borrowerIds.length > 0) params.set('borrower_ids', geo.borrowerIds.join(','));
    if (opts.targetLenderRef) params.set('target_lender_ref', opts.targetLenderRef);
    if (opts.cohortId) params.set('cohort_id', opts.cohortId);
    if (opts.approvalStatus && opts.approvalStatus !== 'any') params.set('approval_status', opts.approvalStatus);
    if (opts.outreachStatus && opts.outreachStatus !== 'any') params.set('outreach_status', opts.outreachStatus);
    if (opts.assignedTo) params.set('assigned_to', opts.assignedTo);
    if (opts.agedDays) params.set('aged_days', String(opts.agedDays));
    if (opts.portfolioCriteria) {
      for (const [key, value] of Object.entries(opts.portfolioCriteria)) {
        if (value !== null && value !== undefined && String(value).length > 0) {
          params.set(key, String(value));
        }
      }
    }
    const qs = params.toString();
    return getJsonWithHeaders<LeadSummary[]>(
      qs ? `/api/leads?${qs}` : '/api/leads',
      signal,
    ).then(({ data, headers }) => ({
      leads: data,
      totalMatching: headers.get('X-Total-Matching') ? Number(headers.get('X-Total-Matching')) : null,
      returnedRows: headers.get('X-Returned-Rows') ? Number(headers.get('X-Returned-Rows')) : null,
      truncatedAt: headers.get('X-Truncated-At') ? Number(headers.get('X-Truncated-At')) : null,
    }));
  },

  leads: (
    segment?: string,
    signal?: AbortSignal,
    geo?: { state?: string; zip?: string; county?: string; states?: string[]; zips?: string[]; borrowerIds?: string[] },
    opts: LeadQueryOptions = {},
  ) => api.leadsPage(segment, signal, geo, opts).then((page) => page.leads),

  borrower: (id: string, signal?: AbortSignal) =>
    getJson<Borrower360>(`/api/borrowers/${id}`, signal),

  borrowerLifecycle: (id: string, signal?: AbortSignal) =>
    getJson<BorrowerLifecycle>(`/api/borrowers/${id}/lifecycle`, signal),

  borrowerSearch: (q: string, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    params.set('q', q);
    return getJson<LeadSummary[]>(`/api/borrowers/search?${params.toString()}`, signal);
  },

  recommendOffer: (borrower_id: string, signal?: AbortSignal) =>
    postJson<OfferRecommendation, { borrower_id: string }>(
      '/api/offers/recommend',
      { borrower_id },
      signal,
    ),

  approve: (
    borrower_id: string,
    opts: {
      actor?: string;
      offer_code?: string | null;
      evidence_ids?: string[];
      draft_body?: string | null;
      rationale?: string | null;
      bulk_id?: string | null;
      bulk_rationale?: string | null;
      channel?: 'email' | 'sms' | 'direct_mail';
      campaign_id?: string | null;
      variant_name?: string | null;
      request_id?: string;
    } = {},
    signal?: AbortSignal,
  ) =>
    postJson<
      ApproveResult,
      {
        borrower_id: string;
        actor: string;
        offer_code?: string | null;
        evidence_ids?: string[];
        draft_body?: string | null;
        rationale?: string | null;
        bulk_id?: string | null;
        bulk_rationale?: string | null;
        channel?: 'email' | 'sms' | 'direct_mail';
        campaign_id?: string | null;
        variant_name?: string | null;
        request_id: string;
      }
    >(
      '/api/outreach/approve',
      {
        borrower_id,
        actor: opts.actor ?? 'anonymous',
        offer_code: opts.offer_code ?? null,
        evidence_ids: opts.evidence_ids ?? [],
        draft_body: opts.draft_body ?? null,
        rationale: opts.rationale ?? null,
        bulk_id: opts.bulk_id ?? null,
        bulk_rationale: opts.bulk_rationale ?? null,
        channel: opts.channel ?? 'email',
        campaign_id: opts.campaign_id ?? null,
        variant_name: opts.variant_name ?? null,
        // R5-01 idempotency: generate one UUID per user action and reuse
        // across any transparent retries inside _fetchWithRetry. The
        // backend has a unique index on mip_app.approvals(request_id)
        // and ON CONFLICT DO NOTHING so a duplicate POST (e.g. after a
        // 503 that the server actually committed before losing the
        // response) does not write a second audit row.
        request_id: opts.request_id ?? _newRequestId(),
      },
      signal,
    ),

  /**
   * Reject a borrower from outreach. Structural twin of `approve` —
   * writes one row to `mip_app.approvals` (action='reject') + one to
   * `mip_app.action_audit` (event_type='OUTREACH_REJECT'). Fires the
   * same debounced lifecycle-sync trigger so the funnel snapshot
   * reflects rejected counts without waiting on the daily cron.
   */
  reject: (
    borrower_id: string,
    opts: {
      actor?: string;
      offer_code?: string | null;
      evidence_ids?: string[];
      rationale_code: string;
      rationale?: string | null;
      channel?: 'email' | 'sms' | 'direct_mail';
      campaign_id?: string | null;
      variant_name?: string | null;
      request_id?: string;
    },
    signal?: AbortSignal,
  ) =>
    postJson<
      RejectResult,
      {
        borrower_id: string;
        actor: string;
        offer_code?: string | null;
        evidence_ids?: string[];
        rationale_code: string;
        rationale?: string | null;
        channel?: 'email' | 'sms' | 'direct_mail';
        campaign_id?: string | null;
        variant_name?: string | null;
        request_id: string;
      }
    >(
      '/api/outreach/reject',
      {
        borrower_id,
        actor: opts.actor ?? 'anonymous',
        offer_code: opts.offer_code ?? null,
        evidence_ids: opts.evidence_ids ?? [],
        rationale_code: opts.rationale_code,
        rationale: opts.rationale ?? null,
        channel: opts.channel ?? 'email',
        campaign_id: opts.campaign_id ?? null,
        variant_name: opts.variant_name ?? null,
        request_id: opts.request_id ?? _newRequestId(),
      },
      signal,
    ),

  /**
   * Fetch the backend-generated outreach draft for a borrower. The
   * backend emits a DRAFT_OUTREACH audit row as a side effect so we
   * know which draft copy was shown to the approver. Callers must fail
   * closed if this rejects; outreach copy is never generated locally.
   */
  draftOutreach: (
    borrower_id: string,
    channel: 'email' | 'sms' | 'direct_mail' = 'email',
    signal?: AbortSignal,
  ) =>
    postJson<OutreachDraftResult, { borrower_id: string; channel: 'email' | 'sms' | 'direct_mail' }>(
      '/api/outreach/draft',
      { borrower_id, channel },
      signal,
    ),

  salesTeam: (signal?: AbortSignal) =>
    getJson<SalesTeamMember[]>('/api/sales/team', signal),

  assignLead: (
    borrowerId: string,
    assignedToEmail: string,
    strategy: 'manual' | 'round_robin' | 'score_balanced' = 'manual',
    signal?: AbortSignal,
  ) =>
    postJson<AssignmentResponse, {
      assigned_to_email: string;
      strategy: 'manual' | 'round_robin' | 'score_balanced';
      expires_in_hours: number;
      request_id: string;
    }>(
      `/api/leads/${encodeURIComponent(borrowerId)}/assign`,
      {
        assigned_to_email: assignedToEmail,
        strategy,
        expires_in_hours: 24,
        request_id: _newRequestId(),
      },
      signal,
    ),

  distributeLeads: (
    borrowerIds: string[],
    loEmails: string[],
    strategy: 'round_robin' | 'score_balanced' = 'round_robin',
    signal?: AbortSignal,
  ) =>
    postJson<{
      assigned_count: number;
      strategy: string;
      assignments: LeadAssignment[];
      per_lo_counts: Record<string, number>;
      audit_event_id?: string | null;
    }, {
      borrower_ids: string[];
      lo_emails: string[];
      strategy: 'round_robin' | 'score_balanced';
      expires_in_hours: number;
      request_id: string;
    }>(
      '/api/sales/distribute',
      {
        borrower_ids: borrowerIds,
        lo_emails: loEmails,
        strategy,
        expires_in_hours: 24,
        request_id: _newRequestId(),
      },
      signal,
    ),

  logDisposition: (
    borrowerId: string,
    payload: {
      lo_email: string;
      outcome: CallDisposition['outcome'];
      callback_at?: string | null;
      notes?: string | null;
    },
    signal?: AbortSignal,
  ) =>
    postJson<DispositionResponse, {
      lo_email: string;
      outcome: CallDisposition['outcome'];
      callback_at?: string | null;
      notes?: string | null;
      request_id: string;
    }>(
      `/api/leads/${encodeURIComponent(borrowerId)}/disposition`,
      {
        ...payload,
        request_id: _newRequestId(),
      },
      signal,
    ),

  salesAging: (olderThanDays = 7, limit = 100, signal?: AbortSignal) =>
    getJson<SalesAgingLead[]>(`/api/sales/aging?older_than_days=${olderThanDays}&limit=${limit}`, signal),

  salesStandup: (date?: string, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    if (date) params.set('date', date);
    const qs = params.toString();
    return getJson<SalesStandupResponse>(qs ? `/api/sales/standup?${qs}` : '/api/sales/standup', signal);
  },

  salesConversion: (
    fromDate: string,
    toDate: string,
    groupBy: 'lo' | 'cohort' = 'lo',
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams();
    params.set('from', fromDate);
    params.set('to', toDate);
    params.set('groupBy', groupBy);
    return getJson<SalesConversionResponse>(`/api/sales/conversion?${params.toString()}`, signal);
  },

  campaigns: (signal?: AbortSignal) =>
    getJson<CampaignListResponse>('/api/campaigns', signal),

  campaign: (campaignId: string, signal?: AbortSignal) =>
    getJson<CampaignSummary>(`/api/campaigns/${campaignId}`, signal),

  campaignStatus: (
    campaignId: string,
    status: CampaignSummary['status'],
    rationale?: string | null,
    signal?: AbortSignal,
  ) =>
    patchJson<CampaignSummary, { status: CampaignSummary['status']; rationale?: string | null }>(
      `/api/campaigns/${campaignId}`,
      { status, rationale: rationale ?? null },
      signal,
    ),

  genie: (question: string, conversationId?: string | null, signal?: AbortSignal) =>
    postJson<GenieResult, { question: string; conversation_id?: string | null }>(
      '/api/genie/message',
      { question, conversation_id: conversationId ?? null },
      signal,
    ),

  genieStart: (signal?: AbortSignal) =>
    postJson<GenieStartResult, { context: Record<string, never> }>(
      '/api/genie/start',
      { context: {} },
      signal,
    ),

  genieAction: (
    action: GenieActionSuggestion & {
      conversation_id?: string | null;
      message_id?: string | null;
      question_hash?: string | null;
    },
    signal?: AbortSignal,
  ) =>
    postJson<GenieActionResult, Record<string, unknown>>(
      '/api/genie/actions',
      {
        action_type: action.action_type,
        conversation_id: action.conversation_id ?? null,
        message_id: action.message_id ?? null,
        question_hash: action.question_hash ?? null,
        borrower_ids: action.borrower_ids ?? [],
        criteria: action.criteria ?? {},
        route: action.route ?? null,
        request_id: action.request_id ?? _newRequestId(),
        confirmed: true,
        confirmation_token: action.confirmation_token ?? null,
      },
      signal,
    ),

  /**
   * Recent audit events for the Agent Activity Log. Routes through the
   * same retry/backoff loop as every other read so a transient 503 on
   * Lakebase doesn't immediately render "feed unavailable" — callers
   * get the same cadence the backend's Resilient wrapper runs at.
   * Hole-finder finding #4, 2026-04-23.
   */
  auditEvents: (
    limit = 12,
    signal?: AbortSignal,
    filters: {
      actor?: string | null;
      action?: string | null;
      entity_id?: string | null;
      borrower_id?: string | null;
      subject_clip?: string | null;
      event_type?: string | null;
      since?: string | null;
      until?: string | null;
    } = {},
  ) => {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    Object.entries(filters).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    return getJson<AuditEventRow[]>(`/api/audit/events?${params.toString()}`, signal);
  },

  auditRollups: (period: 'day' | 'week' | 'month' = 'week', signal?: AbortSignal) =>
    getJson<Array<{ bucket_start: string; event_type: string; event_count: number }>>(
      `/api/audit/rollups?period=${period}`,
      signal,
    ),

  workspace: (signal?: AbortSignal) =>
    getJson<WorkspaceState>('/api/workspace', signal),

  saveWorkspaceLead: (lead: SavedLeadInput, signal?: AbortSignal) =>
    putJson<SavedLead, SavedLeadInput>(
      `/api/workspace/leads/${encodeURIComponent(lead.borrower_id)}`,
      lead,
      signal,
    ),

  deleteWorkspaceLead: (borrowerId: string, signal?: AbortSignal) =>
    deleteJson<WorkspaceMutationResult>(
      `/api/workspace/leads/${encodeURIComponent(borrowerId)}`,
      signal,
    ),

  saveWorkspaceDraft: (draft: SavedDraftInput, signal?: AbortSignal) =>
    putJson<SavedDraft, SavedDraftInput>(
      `/api/workspace/drafts/${encodeURIComponent(draft.borrower_id)}`,
      draft,
      signal,
    ),

  deleteWorkspaceDraft: (
    borrowerId: string,
    channel: 'email' | 'sms' | 'direct_mail' = 'email',
    signal?: AbortSignal,
  ) =>
    deleteJson<WorkspaceMutationResult>(
      `/api/workspace/drafts/${encodeURIComponent(borrowerId)}?channel=${encodeURIComponent(channel)}`,
      signal,
    ),

  /**
   * Admin rules probe — used by the Administration route's "Offer rules"
   * tile. Routes through getJson so a 503 retryable turns into an
   * ApiError the useWarmingUpRetry hook can detect, matching every
   * other route's cold-start UX.
   */
  adminRules: <T>(signal?: AbortSignal) =>
    getJson<T>('/api/admin/rules', signal),

  /**
   * Admin data-source readiness probe — per-source rows with status,
   * row counts, and DESCRIBE DETAIL lastModified stamps. Same warming-up
   * semantics as adminRules.
   */
  adminSources: <T>(signal?: AbortSignal) =>
    getJson<T>('/api/admin/sources', signal),

  dataEstate: (signal?: AbortSignal) =>
    getJson<DataEstateResponse>('/api/data-estate', signal),

  configOptions: (signal?: AbortSignal) =>
    getJson<ConfigOptions>('/api/config/options', signal),
};
