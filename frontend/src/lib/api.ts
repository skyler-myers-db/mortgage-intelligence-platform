import type { Borrower360, LeadSummary, PortfolioPreview, SegmentSummary } from '../types';
import { mockBorrowers, mockPortfolio, mockSegments } from '../mocks/demoData';

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
