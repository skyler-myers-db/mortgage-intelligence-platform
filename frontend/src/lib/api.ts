import type {
  Borrower360,
  LeadSummary,
  OfferRecommendation,
  PortfolioPreview,
  SegmentSummary,
} from '../types';
import { mockBorrowers, mockPortfolio, mockSegments } from '../mocks/demoData';

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
      'mip_demo.gold.fn_next_best_offer',
      'mip_demo.gold.fn_rate_spread',
      'mip_demo.gold.fn_in_the_money',
      'mip_demo.gold.fn_lead_score',
    ],
    alternatives: [],
    thresholds_applied: FALLBACK_THRESHOLDS,
  };
}

async function getJson<T>(path: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(path);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

async function postJson<T, B>(path: string, body: B, fallback: T): Promise<T> {
  try {
    const res = await fetch(path, {
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
  approve: async (borrower_id: string, actor = 'demo-user') => {
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
        trusted_assets: ['mip_demo.gold.lead_population'],
      };
    }
  },
};
