import type {
  Borrower360,
  LeadSummary,
  OfferRecommendation,
  PortfolioPreview,
  SegmentSummary,
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

export interface GenieResult {
  answer: string;
  source?: string;
  trusted_assets?: string[];
  metric_value?: string | null;
  table_rows?: Record<string, unknown>[] | null;
  follow_up_questions?: string[];
}

/** Structured error thrown by every api.* method on non-2xx or network failure. */
export class ApiError extends Error {
  readonly path: string;
  readonly status: number | null;
  readonly retryable: boolean;
  readonly dependency: string | null;

  constructor(
    message: string,
    opts: { path: string; status?: number | null; retryable?: boolean; dependency?: string | null } = { path: '' },
  ) {
    super(message);
    this.name = 'ApiError';
    this.path = opts.path;
    this.status = opts.status ?? null;
    this.retryable = Boolean(opts.retryable);
    this.dependency = opts.dependency ?? null;
  }
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

async function _sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

interface Retryable503Parsed {
  retryable: boolean;
  dependency: string | null;
  detail: string | null;
}

async function _parseRetryableBody(res: Response): Promise<Retryable503Parsed> {
  if (res.status !== 503) return { retryable: false, dependency: null, detail: null };
  try {
    const body = (await res.clone().json()) as {
      retryable?: boolean;
      dependency?: string;
      detail?: string;
    };
    return {
      retryable: body?.retryable === true,
      dependency: body?.dependency ?? null,
      detail: body?.detail ?? null,
    };
  } catch {
    return { retryable: false, dependency: null, detail: null };
  }
}

async function _fetchWithRetry(
  path: string,
  init?: RequestInit,
  attempts = 3,
): Promise<Response> {
  let lastRes: Response | null = null;
  for (let i = 0; i < attempts; i++) {
    const res = await fetch(path, init);
    if (res.ok) return res;
    const parsed = await _parseRetryableBody(res);
    if (!parsed.retryable) return res;
    lastRes = res;
    if (i === attempts - 1) break;
    const delay = Math.min(2000, 200 * 2 ** i);
    const jittered = delay * (0.5 + Math.random());
    await _sleep(jittered);
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
  });
}

async function getJson<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await _fetchWithRetry(path);
  } catch (err) {
    // Network-level failure (offline, DNS, CORS preflight, etc.)
    const message = err instanceof Error ? err.message : 'network error';
    throw new ApiError(message, { path, status: null, retryable: false });
  }
  if (!res.ok) await _throwFromResponse(res, path);
  return (await res.json()) as T;
}

async function postJson<T, B>(path: string, body: B): Promise<T> {
  let res: Response;
  try {
    res = await _fetchWithRetry(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'network error';
    throw new ApiError(message, { path, status: null, retryable: false });
  }
  if (!res.ok) await _throwFromResponse(res, path);
  return (await res.json()) as T;
}

export const api = {
  /**
   * Honest health probe. A dead backend returns an "unreachable" status
   * object instead of throwing — callers render dependency state as
   * `unknown`, never as synthesized "up".
   */
  health: async (): Promise<HealthPayload> => {
    try {
      return await getJson<HealthPayload>('/api/health');
    } catch {
      return { status: 'unreachable', mode: 'unknown', dependencies: {} };
    }
  },

  portfolioPreview: (criteria: Record<string, unknown> = {}) =>
    postJson<PortfolioPreview, { criteria: Record<string, unknown> }>(
      '/api/portfolio/preview',
      { criteria },
    ),

  segments: () => getJson<SegmentSummary[]>('/api/segments'),

  leads: (segment?: string) =>
    getJson<LeadSummary[]>(
      segment ? `/api/leads?segment=${encodeURIComponent(segment)}` : '/api/leads',
    ),

  borrower: (id: string) => getJson<Borrower360>(`/api/borrowers/${id}`),

  recommendOffer: (borrower_id: string) =>
    postJson<OfferRecommendation, { borrower_id: string }>(
      '/api/offers/recommend',
      { borrower_id },
    ),

  approve: (
    borrower_id: string,
    opts: { actor?: string; offer_code?: string | null; evidence_ids?: string[] } = {},
  ) =>
    postJson<
      ApproveResult,
      {
        borrower_id: string;
        actor: string;
        offer_code?: string | null;
        evidence_ids?: string[];
      }
    >('/api/outreach/approve', {
      borrower_id,
      actor: opts.actor ?? 'anonymous',
      // Forward the chosen offer_code + evidence_ids so the audit row
      // captures what the approver actually saw. Default to [] / null
      // so callers that don't have the recommendation hydrated still work.
      offer_code: opts.offer_code ?? null,
      evidence_ids: opts.evidence_ids ?? [],
    }),

  genie: (question: string) =>
    postJson<GenieResult, { question: string }>('/api/genie/message', { question }),
};
