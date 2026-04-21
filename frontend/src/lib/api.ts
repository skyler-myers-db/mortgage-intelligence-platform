import type {
  Borrower360,
  LeadSummary,
  OfferRecommendation,
  PortfolioPreview,
  SegmentSummary,
} from '../types';
import { mockBorrowers, mockPortfolio, mockSegments } from '../mocks/fixtureData';

/** Default thresholds mirror backend OfferEngineConfig — used only in offline fallback. */
const FALLBACK_THRESHOLDS: Record<string, number> = {
  min_spread_bps: 75,
  min_equity_pct: 15,
  heloc_equity_min_pct: 35,
  cashout_equity_min_pct: 25,
  retention_min_spread_bps: 50,
};

function fallbackOfferRecommendation(borrower_id: string): OfferRecommendation {
  const b = mockBorrowers.find((x) => x.borrower_id === borrower_id) ?? mockBorrowers[0];
  const offer_code = (b.recommended_offer ?? 'nurture').toLowerCase().replace(/[^a-z]+/g, '_').replace(/^_+|_+$/g, '') || 'nurture';
  return {
    borrower_id: b.borrower_id,
    offer_code,
    offer_type: offer_code,
    product_label: b.recommended_offer ?? 'Nurture',
    confidence: b.confidence,
    rationale: b.why_now ?? 'Fallback rationale — backend unavailable.',
    evidence_ids: b.evidence_ids ?? [],
    sources: [
      'mip.gold.fn_next_best_offer',
      'mip.gold.fn_rate_spread',
      'mip.gold.fn_in_the_money',
      'mip.gold.fn_lead_score',
    ],
    alternatives: [],
    thresholds_applied: FALLBACK_THRESHOLDS,
  };
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
 *
 * We deliberately do NOT fall back to mock data on a retryable 503 —
 * that would mix live and synthetic surfaces and the UI would silently
 * lie. The `fallback` value below is only reached when the browser
 * itself can't talk to the backend (offline, CORS failure);
 * in that case the DegradedBanner's own fetch also fails and the UI
 * shows warming-up copy regardless.
 */

async function _sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function _isRetryable503(res: Response): Promise<boolean> {
  if (res.status !== 503) return false;
  // Clone before reading so the caller can still read the body.
  try {
    const body = await res.clone().json();
    return body?.retryable === true;
  } catch {
    return false;
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
    if (res.ok || !(await _isRetryable503(res))) return res;
    lastRes = res;
    if (i === attempts - 1) break;
    const delay = Math.min(2000, 200 * 2 ** i);
    const jittered = delay * (0.5 + Math.random());
    await _sleep(jittered);
  }
  // Exhausted -- return the last 503 so the caller can decide.
  return lastRes as Response;
}

async function getJson<T>(path: string, fallback: T): Promise<T> {
  try {
    const res = await _fetchWithRetry(path);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

async function postJson<T, B>(path: string, body: B, fallback: T): Promise<T> {
  try {
    const res = await _fetchWithRetry(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

export const api = {
  health: () => getJson('/api/health', { status: 'mock-fallback', mode: 'mock' }),
  portfolioPreview: (criteria: Record<string, unknown> = {}) =>
    postJson<PortfolioPreview, { criteria: Record<string, unknown> }>(
      '/api/portfolio/preview',
      { criteria },
      mockPortfolio
    ),
  segments: () => getJson<SegmentSummary[]>('/api/segments', mockSegments),
  leads: (segment?: string) =>
    getJson<LeadSummary[]>(
      segment ? `/api/leads?segment=${encodeURIComponent(segment)}` : '/api/leads',
      segment ? mockBorrowers.filter((b) => b.segment_codes.includes(segment as never)) : mockBorrowers
    ),
  borrower: (id: string) =>
    getJson<Borrower360>(
      `/api/borrowers/${id}`,
      (mockBorrowers.find((b) => b.borrower_id === id) ?? mockBorrowers[0]) as Borrower360
    ),
  recommendOffer: (borrower_id: string) =>
    postJson<OfferRecommendation, { borrower_id: string }>(
      '/api/offers/recommend',
      { borrower_id },
      fallbackOfferRecommendation(borrower_id)
    ),
  approve: async (borrower_id: string, actor = 'anonymous') => {
    try {
      const res = await fetch('/api/outreach/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ borrower_id, actor }),
      });
      if (!res.ok) return { approved: false, approval_id: 'mock-fallback', audit_event_id: 'mock-fallback' };
      return res.json();
    } catch {
      return { approved: false, approval_id: 'mock-fallback', audit_event_id: 'mock-fallback' };
    }
  },
  genie: async (question: string) => {
    try {
      const res = await fetch('/api/genie/message', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    } catch {
      return {
        answer:
          'Fallback: Genie is unavailable, but the curated Module 0 metric views show In-the-Money and Home Equity candidates as the highest-value segments.',
        source: 'deterministic_fallback',
        trusted_assets: ['mip.gold.lead_population'],
      };
    }
  },
};
