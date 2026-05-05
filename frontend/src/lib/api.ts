import type {
  Borrower360,
  CountyRollupResponse,
  LeadSummary,
  OfferRecommendation,
  PortfolioPreview,
  SegmentCode,
  SegmentSummary,
  StateRollupResponse,
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
  channel: 'email' | 'sms';
  subject?: string | null;
  body: string;
  status: 'draft';
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
}

export type SegmentFilterMode = 'any' | 'all';

export interface LeadQueryOptions {
  segmentCodes?: SegmentCode[];
  segmentMode?: SegmentFilterMode;
  limit?: number;
}

/**
 * Resilience reason codes surfaced in 503 bodies by the backend's
 * `_dependency_down_handler` (cycle 13, 2026-04-23). Used by
 * `useWarmingUpRetry` to choose a cadence:
 *
 *   - "warming_up"        — dependency is initialising after idle; poll 5s.
 *   - "breaker_open"      — circuit breaker tripped; respect the 30s
 *                           cooldown before probing again.
 *   - "retries_exhausted" — backend already burned its retry budget;
 *                           further client retries will not help.
 */
export type ApiErrorReason = 'warming_up' | 'breaker_open' | 'retries_exhausted';

/** Structured error thrown by every api.* method on non-2xx or network failure. */
export class ApiError extends Error {
  readonly path: string;
  readonly status: number | null;
  readonly retryable: boolean;
  readonly dependency: string | null;
  readonly correlationId: string | null;
  readonly aborted: boolean;
  /**
   * 503 classification from the backend resilience layer, or `null`
   * when the body did not include one. See `ApiErrorReason`.
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
  // 2026-04-25 incident: an expired SDK-minted OAuth token caused the
  // warehouse path to fail with HTTP 403 forever; the resilience layer
  // wraps that as DependencyDownError(kind="retries_exhausted") and
  // the breaker eventually opens (kind="breaker_open"). Both reasons
  // arrive on the wire as 503+retryable=true, so the prior "any 503
  // counts as warming-up" rule retried forever and never surfaced the
  // real problem to the user. Only the explicit "warming_up" classifi-
  // cation should drive the warming-up UX. Unknown/null reason still
  // counts as warming-up so older backends keep working.
  if (err.reason === 'breaker_open' || err.reason === 'retries_exhausted') {
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
 * Slice-6 retry protocol for `retryable: true` 503 responses.
 *
 * The backend's resilience layer (CircuitBreaker + Resilient wrapper)
 * returns HTTP 503 with `{detail, retryable: true, dependency}` when a
 * dependency's circuit is open or all retries exhausted. We treat
 * that as a transient signal and re-fetch with exponential backoff
 * (mirroring the backend's 0.2s/0.4s/0.8s cadence). The DegradedBanner
 * is powered by `/api/health` polling in parallel so the user sees
 * "backend is warming up" while these retries run.
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
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export interface Retryable503Parsed {
  retryable: boolean;
  dependency: string | null;
  detail: string | null;
  correlationId: string | null;
  /**
   * Cycle-13 resilience classification: "warming_up" | "breaker_open" |
   * "retries_exhausted", or null if the backend didn't include one.
   * The frontend retry hook branches on this field.
   */
  reason: ApiErrorReason | string | null;
}

/**
 * Parse a 503 body emitted by `_dependency_down_handler`. Exported so
 * tests can exercise the classification logic without mocking `fetch`.
 * Returns a fully-null parse for any non-503 response.
 */
export async function _parseRetryableBody(res: Response): Promise<Retryable503Parsed> {
  const empty: Retryable503Parsed = {
    retryable: false,
    dependency: null,
    detail: null,
    correlationId: null,
    reason: null,
  };
  if (res.status !== 503) return empty;
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
      reason: body?.reason ?? null,
    };
  } catch {
    return empty;
  }
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
    const delay = Math.min(2000, 200 * 2 ** i);
    const jittered = delay * (0.5 + Math.random());
    await _sleep(jittered, signal);
  }
  return lastRes as Response;
}

async function _throwFromResponse(res: Response, path: string): Promise<never> {
  const parsed = await _parseRetryableBody(res);
  const msg = parsed.detail ?? `${res.status} ${res.statusText}`;
  throw new ApiError(msg, {
    path,
    status: res.status,
    retryable: parsed.retryable,
    dependency: parsed.dependency,
    correlationId: parsed.correlationId,
    reason: parsed.reason,
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
  let res: Response;
  try {
    res = await _fetchWithRetry(path, undefined, 3, signal);
  } catch (err) {
    throw _wrapFetchError(err, path);
  }
  if (!res.ok) await _throwFromResponse(res, path);
  return (await res.json()) as T;
}

async function postJson<T, B>(path: string, body: B, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await _fetchWithRetry(
      path,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body ?? {}),
      },
      3,
      signal,
    );
  } catch (err) {
    throw _wrapFetchError(err, path);
  }
  if (!res.ok) await _throwFromResponse(res, path);
  return (await res.json()) as T;
}

async function putJson<T, B>(path: string, body: B, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await _fetchWithRetry(
      path,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body ?? {}),
      },
      3,
      signal,
    );
  } catch (err) {
    throw _wrapFetchError(err, path);
  }
  if (!res.ok) await _throwFromResponse(res, path);
  return (await res.json()) as T;
}

async function deleteJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await _fetchWithRetry(
      path,
      { method: 'DELETE' },
      3,
      signal,
    );
  } catch (err) {
    throw _wrapFetchError(err, path);
  }
  if (!res.ok) await _throwFromResponse(res, path);
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

  portfolioPreview: (
    criteria: Record<string, unknown> = {},
    signal?: AbortSignal,
  ) =>
    postJson<PortfolioPreview, { criteria: Record<string, unknown> }>(
      '/api/portfolio/preview',
      { criteria },
      signal,
    ),

  segments: (signal?: AbortSignal) =>
    getJson<SegmentSummary[]>('/api/segments', signal),

  stateRollups: (
    segmentCodes?: string[] | null,
    signal?: AbortSignal,
    segmentMode: SegmentFilterMode = 'any',
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
    const qs = params.toString();
    return getJson<StateRollupResponse>(
      qs ? `/api/geo/state-rollups?${qs}` : '/api/geo/state-rollups',
      signal,
    );
  },

  countyRollups: (state: string, signal?: AbortSignal) =>
    getJson<CountyRollupResponse>(
      `/api/geo/county-rollups?state=${encodeURIComponent(state.toUpperCase())}`,
      signal,
    ),

  zipRollups: (fips: string, signal?: AbortSignal) =>
    getJson<ZipRollupResponse>(
      `/api/geo/zip-rollups?fips=${encodeURIComponent(fips)}`,
      signal,
    ),

  leads: (
    segment?: string,
    signal?: AbortSignal,
    geo?: { state?: string; zip?: string },
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
    if (opts.limit) params.set('limit', String(opts.limit));
    if (geo?.state) params.set('state', geo.state);
    if (geo?.zip) params.set('zip', geo.zip);
    const qs = params.toString();
    return getJson<LeadSummary[]>(
      qs ? `/api/leads?${qs}` : '/api/leads',
      signal,
    );
  },

  borrower: (id: string, signal?: AbortSignal) =>
    getJson<Borrower360>(`/api/borrowers/${id}`, signal),

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
      rationale?: string | null;
      request_id?: string;
    } = {},
    signal?: AbortSignal,
  ) =>
    postJson<
      RejectResult,
      {
        borrower_id: string;
        actor: string;
        offer_code?: string | null;
        evidence_ids?: string[];
        rationale?: string | null;
        request_id: string;
      }
    >(
      '/api/outreach/reject',
      {
        borrower_id,
        actor: opts.actor ?? 'anonymous',
        offer_code: opts.offer_code ?? null,
        evidence_ids: opts.evidence_ids ?? [],
        rationale: opts.rationale ?? null,
        request_id: opts.request_id ?? _newRequestId(),
      },
      signal,
    ),

  /**
   * Fetch the backend-generated outreach draft for a borrower. The
   * backend emits a DRAFT_OUTREACH audit row as a side effect so we
   * know which draft copy was shown to the approver. Callers should
   * fall back to a local template string if this rejects so the
   * Offer Orchestrator stays usable when the endpoint is degraded.
   */
  draftOutreach: (
    borrower_id: string,
    channel: 'email' | 'sms' = 'email',
    signal?: AbortSignal,
  ) =>
    postJson<OutreachDraftResult, { borrower_id: string; channel: 'email' | 'sms' }>(
      '/api/outreach/draft',
      { borrower_id, channel },
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
  auditEvents: (limit = 12, signal?: AbortSignal) =>
    getJson<AuditEventRow[]>(`/api/audit/events?limit=${limit}`, signal),

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
    channel: 'email' | 'sms' = 'email',
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
};
