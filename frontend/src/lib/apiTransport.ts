/**
 * HTTP transport for the api client: the structured `ApiError`, the transient
 * failure classification the backend's resilience layer returns, the retry /
 * abort / request-id plumbing, the growth-agent cohort proof verification, and
 * the typed get/post/put/patch/delete helpers every endpoint client calls.
 *
 * Split out of `api.ts` verbatim. Per CLAUDE.md there is no mock fallback
 * here: every failure throws an `ApiError` the caller renders as an explicit
 * degraded state.
 */
import type {
  GrowthAgentCohortProof,
  GrowthAgentCohortVerification,
  AnalyticsQueryOptions,
  GeoQueryCriteria,
} from './apiTypes';
import { apiPath } from './apiPaths';
import {
  CLIENT_FAILURE_MESSAGES,
  isNetworkFailure,
  networkFailureReason,
  probeSession,
  reportNetworkFailure,
  type ClientFailureReason,
  type SessionProbeVerdict,
} from './apiFailure';
import { isSessionExpired, markSessionExpired } from './sessionStatus';

export function appendPortfolioCriteria(params: URLSearchParams, criteria?: GeoQueryCriteria | null) {
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

export function analyticsPath(
  scope: string,
  opts: AnalyticsQueryOptions = {},
  extraParams: Record<string, number> = {},
): string {
  const params = new URLSearchParams();
  Object.entries(extraParams).forEach(([key, value]) => params.set(key, String(value)));
  const states = opts.states?.length ? opts.states : opts.state ? [opts.state] : [];
  if (states.length > 0) params.set('states', states.map((state) => state.toUpperCase()).join(','));
  if (opts.segmentCodes && opts.segmentCodes.length > 0) {
    params.set('segment_codes', opts.segmentCodes.join(','));
    params.set('segment_mode', opts.segmentMode ?? 'any');
  }
  if (opts.lenderRelationship && opts.lenderRelationship !== 'All') {
    params.set('lender_relationship', opts.lenderRelationship);
  }
  if (opts.targetLenderRef && opts.targetLenderRef !== 'All') {
    params.set('target_lender_ref', opts.targetLenderRef);
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
 *   - "permission_denied" — a 503 with `retryable: false`: the dependency
 *                           refused the app a required object (a missing
 *                           grant). Never retried; an administrator fixes it.
 *
 * The client adds its own reasons (`ClientFailureReason`, see apiFailure.ts):
 * "session_expired", "offline", "unreachable" and "unreadable_response", plus
 * "cohort_proof" for a Growth Agent handoff whose cohort proof failed to
 * verify (a client-side 409). None of them is ever retried.
 */
export type ApiErrorReason =
  | 'warming_up'
  | 'breaker_open'
  | 'retries_exhausted'
  | 'rate_limited'
  | 'dependency_saturated'
  | 'permission_denied'
  | 'cohort_proof'
  | ClientFailureReason;

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
  /**
   * How long the server asked the client to wait before another attempt
   * (Retry-After, or the backpressure body's `retry_after_seconds`), in ms;
   * null when it named no wait. A 429 surfaces with it so the screen counts
   * the wait down instead of sleeping through it (audit states-08).
   */
  readonly retryAfterMs: number | null;

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
      retryAfterMs?: number | null;
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
    this.retryAfterMs = opts.retryAfterMs ?? null;
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

const CLIENT_FAILURE_REASONS: ReadonlySet<string> = new Set<ClientFailureReason>([
  'session_expired',
  'offline',
  'unreachable',
  'unreadable_response',
]);

/** The client-assigned reason of an ApiError (apiFailure.ts), or null. */
export function clientFailureReason(err: unknown): ClientFailureReason | null {
  if (!(err instanceof ApiError) || err.aborted || typeof err.reason !== 'string') return null;
  return CLIENT_FAILURE_REASONS.has(err.reason) ? (err.reason as ClientFailureReason) : null;
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

export function _newRequestId(): string {
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

const SHA256_HEX_RE = /^[0-9a-f]{64}$/;
const GROWTH_AGENT_RUN_ID_RE = /^[A-Za-z0-9_-]{8,128}$/;

/**
 * Every sentence a failed Growth Agent cohort proof can carry. A screen may
 * show `error.message` for a `cohort_proof` error ONLY when it is one of
 * these (lib/describeApiError.ts), so no other text can ride that path.
 */
export const COHORT_PROOF_MESSAGES = {
  incomplete: 'Growth Agent cohort proof is incomplete.',
  incompleteRerun: 'Growth Agent cohort proof is incomplete. Run the workflow again before reviewing leads.',
  unverifiable: 'Growth Agent cohort proof cannot be verified in this browser.',
  stale: 'Growth Agent cohort is stale. Run the workflow again before reviewing or approving leads.',
} as const;

function _growthAgentProofError(message: string): ApiError {
  return new ApiError(message, {
    path: apiPath('/api/leads'),
    status: 409,
    retryable: false,
    reason: 'cohort_proof',
  });
}

export function _growthAgentProofFromLocation(): GrowthAgentCohortProof | null {
  if (typeof window === 'undefined') return null;
  const params = new URLSearchParams(window.location.search);
  const runId = params.get('growth_agent_run_id')?.trim() ?? '';
  const rawTotal = params.get('actionable_total')?.trim() ?? '';
  const cohortFingerprint = params.get('actionable_cohort_fingerprint')?.trim().toLowerCase() ?? '';
  const snapshotId = params.get('actionable_snapshot_id')?.trim() ?? '';
  const toolResultHash = params.get('tool_result_hash')?.trim().toLowerCase() ?? '';
  const growthHandoff = params.get('growth_handoff')?.trim() ?? '';
  if (!runId && !rawTotal && !cohortFingerprint && !snapshotId && !toolResultHash && !growthHandoff) {
    return null;
  }

  const actionableTotal = Number(rawTotal);
  if (
    !GROWTH_AGENT_RUN_ID_RE.test(runId)
    || !rawTotal
    || !Number.isSafeInteger(actionableTotal)
    || actionableTotal < 0
    || !SHA256_HEX_RE.test(cohortFingerprint)
    || !snapshotId
    || !SHA256_HEX_RE.test(toolResultHash)
    || !growthHandoff
    || growthHandoff[4096]
  ) {
    throw _growthAgentProofError(COHORT_PROOF_MESSAGES.incomplete);
  }
  return { runId, actionableTotal, cohortFingerprint, snapshotId, toolResultHash, growthHandoff };
}

export async function growthAgentCohortFingerprint(
  cohortDigest: string,
  toolResultHash: string,
): Promise<string> {
  const normalizedDigest = cohortDigest.trim().toLowerCase();
  const normalizedToolHash = toolResultHash.trim().toLowerCase();
  if (!SHA256_HEX_RE.test(normalizedDigest) || !SHA256_HEX_RE.test(normalizedToolHash)) {
    throw _growthAgentProofError(COHORT_PROOF_MESSAGES.incompleteRerun);
  }
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) {
    throw _growthAgentProofError(COHORT_PROOF_MESSAGES.unverifiable);
  }
  const canonical = JSON.stringify({
    cohort_digest: normalizedDigest,
    tool_result_hash: normalizedToolHash,
    version: 1,
  });
  const digest = await subtle.digest('SHA-256', new TextEncoder().encode(canonical));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export async function _verifyGrowthAgentCohort(
  headers: Headers,
  proof: GrowthAgentCohortProof,
): Promise<GrowthAgentCohortVerification> {
  const rawTotal = headers.get('X-Total-Matching');
  const cohortDigest = headers.get('X-Cohort-Digest')?.trim().toLowerCase() ?? '';
  const cohortFingerprint = headers.get('X-Cohort-Fingerprint')?.trim().toLowerCase() ?? '';
  const snapshotId = headers.get('X-Cohort-Snapshot-ID')?.trim() ?? '';
  const runId = headers.get('X-Growth-Agent-Run-ID')?.trim() ?? '';
  const total = Number(rawTotal);
  if (
    rawTotal === null
    || !Number.isSafeInteger(total)
    || total < 0
    || !SHA256_HEX_RE.test(cohortDigest)
    || !SHA256_HEX_RE.test(cohortFingerprint)
    || !snapshotId
    || !GROWTH_AGENT_RUN_ID_RE.test(runId)
  ) {
    throw _growthAgentProofError(COHORT_PROOF_MESSAGES.stale);
  }
  const destinationFingerprint = await growthAgentCohortFingerprint(
    cohortDigest,
    proof.toolResultHash,
  );
  if (
    total !== proof.actionableTotal
    || destinationFingerprint !== proof.cohortFingerprint
    || cohortFingerprint !== proof.cohortFingerprint
    || snapshotId !== proof.snapshotId
    || runId !== proof.runId
  ) {
    throw _growthAgentProofError(COHORT_PROOF_MESSAGES.stale);
  }
  return {
    status: 'verified',
    runId,
    total,
    cohortFingerprint,
    snapshotId,
  };
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
      retry_after_seconds?: unknown;
    };
    return {
      retryable: body?.retryable === true,
      dependency: body?.dependency ?? null,
      detail: body?.detail ?? null,
      correlationId: body?.correlation_id ?? null,
      // The header is the source of truth; backpressure.py also puts the
      // wait in the body, which is all that survives a header-stripping proxy.
      retryAfterMs: _parseRetryAfterMs(res.headers.get('Retry-After')) ?? _bodyRetryAfterMs(body?.retry_after_seconds),
      reason: body?.reason ?? null,
    };
  } catch {
    return empty;
  }
}

function _bodyRetryAfterMs(seconds: unknown): number | null {
  return typeof seconds === 'number' && Number.isFinite(seconds) && seconds >= 0 ? seconds * 1000 : null;
}

/** A correlation id a screen may show: the backend's sanitized shape only. */
const CORRELATION_ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;

function _correlationId(...candidates: Array<string | null | undefined>): string | null {
  return candidates.find((value): value is string => typeof value === 'string' && CORRELATION_ID_RE.test(value)) ?? null;
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
    // Non-JSON error bodies fall through to SERVER_FAILURE_MESSAGE.
  }
  return { message: null, validationIssues: [] };
}

/**
 * One request, redirects NOT followed (audit 2026-09-21 `states-02`). An
 * expired Databricks Apps session can surface as a redirect to the
 * workspace's sign-in page; following it goes cross-origin and fails as an
 * unexplained TypeError. An opaque redirect is ambiguous (FastAPI's
 * trailing-slash 307s look the same), so one health probe decides: an ended
 * session throws `session_expired`; anything else re-issues the request with
 * redirects followed, exactly as the browser would have done. A 307 is issued
 * before any handler runs, so the re-issue never repeats a write.
 */
async function _fetchOnce(
  path: string,
  method: string,
  init: RequestInit | undefined,
  signal: AbortSignal | undefined,
): Promise<Response> {
  const res = await fetch(path, { ...init, signal, redirect: 'manual' });
  if (res.type !== 'opaqueredirect') return res;
  if ((await probeSession(signal)) === 'expired') throw _sessionExpiredError(path, method);
  return fetch(path, { ...init, signal, redirect: 'follow' });
}

/**
 * Which transient failures a request may be re-sent after.
 *
 *   - 'default'       every retryable 503 / 429 (idempotent calls).
 *   - 'rejected-only' only a 429 the backpressure middleware returned BEFORE
 *                     the handler ran (reason rate_limited or
 *                     dependency_saturated). Never a 503: a 503 can come from
 *                     a handler that already acted, and a request without an
 *                     Idempotency-Key (the Genie submit, which creates a
 *                     Genie message) must not act twice.
 */
export type RetryPolicy = 'default' | 'rejected-only';

export interface RequestOptions {
  retry?: RetryPolicy;
}

const REJECTED_BEFORE_HANDLER: ReadonlySet<string> = new Set(['rate_limited', 'dependency_saturated']);

function _mayResend(res: Response, parsed: Retryable503Parsed, retry: RetryPolicy): boolean {
  if (!parsed.retryable) return false;
  if (retry === 'default') return true;
  return res.status === 429 && typeof parsed.reason === 'string' && REJECTED_BEFORE_HANDLER.has(parsed.reason);
}

async function _fetchWithRetry(
  path: string,
  init?: RequestInit,
  attempts = 3,
  signal?: AbortSignal,
  retry: RetryPolicy = 'default',
): Promise<Response> {
  const method = (init?.method ?? 'GET').toUpperCase();
  let lastRes: Response | null = null;
  for (let i = 0; i < attempts; i++) {
    if (signal?.aborted) throw _abortError();
    const res = await _fetchOnce(path, method, init, signal);
    if (res.ok) return res;
    const parsed = await _parseRetryableBody(res);
    if (!_mayResend(res, parsed, retry)) return res;
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

/** Last-resort messages: neither ever carries a status line or a browser string. */
export const SERVER_FAILURE_MESSAGE = 'The server could not complete this request.';
export const REQUEST_FAILURE_MESSAGE = 'The request failed before the server answered.';

function _clientFailure(path: string, reason: ClientFailureReason, status: number | null = null): ApiError {
  return new ApiError(CLIENT_FAILURE_MESSAGES[reason], { path, status, retryable: false, reason });
}

/** Record the ended session (the dialog reads it) and build the error. */
function _sessionExpiredError(path: string, method: string, status: number | null = null): ApiError {
  markSessionExpired({ method, path });
  return _clientFailure(path, 'session_expired', status);
}

async function _throwFromResponse(res: Response, path: string, method: string): Promise<never> {
  // The proxy's 401 carries an empty `{}` body and the backend's says only
  // "authenticated identity required": neither is copy for a buyer.
  if (res.status === 401) throw _sessionExpiredError(path, method, 401);
  const parsed = await _parseRetryableBody(res);
  const parsedBody = await _parseHttpErrorBody(res);
  // Never the status line: "500 Internal Server Error" is not copy for a
  // buyer (audit states-04). Screens map status and reason to their own words
  // (lib/describeApiError.ts); this constant is only the last resort.
  const msg = parsed.detail ?? parsedBody.message ?? SERVER_FAILURE_MESSAGE;
  throw new ApiError(msg, {
    path,
    status: res.status,
    retryable: parsed.retryable,
    dependency: parsed.dependency,
    // The correlation middleware (main.py) sets the header on every
    // response; the 503/429 bodies repeat it.
    correlationId: _correlationId(parsed.correlationId, res.headers.get('X-Correlation-ID')),
    reason: parsed.reason,
    validationIssues: parsedBody.validationIssues,
    retryAfterMs: parsed.retryAfterMs,
  });
}

function _wrapFetchError(err: unknown, path: string): ApiError {
  // Already classified (session expiry confirmed by the redirect probe).
  if (err instanceof ApiError) return err;
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
  // fetch() rejects with a TypeError when the request never got an answer.
  if (isNetworkFailure(err)) {
    const reason = networkFailureReason();
    if (reason === 'unreachable') reportNetworkFailure();
    return _clientFailure(path, reason);
  }
  return new ApiError(REQUEST_FAILURE_MESSAGE, { path, status: null, retryable: false });
}

/**
 * Parse a 2xx body. A body that is not JSON is ambiguous: the proxy can serve
 * its HTML sign-in page with a 200. One health probe decides between an
 * ended session and a genuinely unreadable response; either way the caller
 * gets a classified ApiError, never a raw SyntaxError.
 */
async function _readJson<T>(res: Response, path: string, method: string, signal?: AbortSignal): Promise<T> {
  try {
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') throw _wrapFetchError(err, path);
    if (isNetworkFailure(err)) throw _clientFailure(path, networkFailureReason(), res.status);
    let verdict: SessionProbeVerdict;
    try {
      verdict = await probeSession(signal);
    } catch (probeErr) {
      throw _wrapFetchError(probeErr, path);
    }
    if (verdict === 'expired') throw _sessionExpiredError(path, method, res.status);
    throw _clientFailure(path, 'unreadable_response', res.status);
  }
}

/** The shared request path of every typed helper below. */
async function _requestJson<T>(
  path: string,
  init: RequestInit | undefined,
  signal: AbortSignal | undefined,
  retry: RetryPolicy = 'default',
): Promise<{ data: T; headers: Headers }> {
  const requestPath = apiPath(path);
  const method = (init?.method ?? 'GET').toUpperCase();
  // After the session ended every request would 401 again: fail fast, and
  // let a write attempted anyway reach the dialog's "not recorded" line.
  if (isSessionExpired()) throw _sessionExpiredError(requestPath, method);
  let res: Response;
  try {
    res = await _fetchWithRetry(requestPath, init, 3, signal, retry);
  } catch (err) {
    throw _wrapFetchError(err, requestPath);
  }
  if (!res.ok) await _throwFromResponse(res, requestPath, method);
  const data = await _readJson<T>(res, requestPath, method, signal);
  return { data, headers: res.headers };
}

function _jsonBody(method: string, body: unknown, extraHeaders?: Record<string, string>): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json', ...extraHeaders },
    body: JSON.stringify(body ?? {}),
  };
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return (await _requestJson<T>(path, undefined, signal)).data;
}

export async function getJsonWithHeaders<T>(path: string, signal?: AbortSignal): Promise<{ data: T; headers: Headers }> {
  return _requestJson<T>(path, undefined, signal);
}

export async function postJson<T, B>(
  path: string,
  body: B,
  signal?: AbortSignal,
  extraHeaders?: Record<string, string>,
  options?: RequestOptions,
): Promise<T> {
  return (await _requestJson<T>(path, _jsonBody('POST', body, extraHeaders), signal, options?.retry)).data;
}

export async function putJson<T, B>(path: string, body: B, signal?: AbortSignal): Promise<T> {
  return (await _requestJson<T>(path, _jsonBody('PUT', body), signal)).data;
}

export async function patchJson<T, B>(path: string, body: B, signal?: AbortSignal): Promise<T> {
  return (await _requestJson<T>(path, _jsonBody('PATCH', body), signal)).data;
}

export async function deleteJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return (await _requestJson<T>(path, { method: 'DELETE' }, signal)).data;
}
