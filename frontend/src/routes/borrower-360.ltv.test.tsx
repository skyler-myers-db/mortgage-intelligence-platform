/**
 * @vitest-environment happy-dom
 *
 * Route-level proof for the 2026-09-21 audit finding quality-04 (S part):
 * `Borrower360.ltv` is `int | None` on the wire, and the dossier interpolated
 * it raw, so a borrower whose LTV the API withheld read "null% · $0". The
 * defect lived in the route's JSX, so this mounts the ROUTE and reads the
 * rendered "LTV / Equity" field rather than a helper below it.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { Borrower360 as Borrower360Type } from '../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const state = vi.hoisted(() => ({ borrower: null as unknown }));

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => ({
    data: state.borrower,
    warmingUp: null,
    error: null,
    manualRetry: vi.fn(),
  }),
}));

vi.mock('../lib/api', () => {
  class ApiError extends Error {
    status = 500;
  }
  return { api: { borrower: vi.fn() }, ApiError };
});

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    lastBorrowerId: null,
    setLastBorrowerId: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
  }),
}));

// Shell + siblings that are not under test and drag in providers of their own.
vi.mock('../components/layout/PageShell', () => ({
  PageShell: ({ children }: { children?: ReactNode }) => <main>{children}</main>,
}));
vi.mock('../components/mortgage/BorrowerStoryCard', () => ({ BorrowerStoryCard: () => null }));
vi.mock('../components/mortgage/TriggerTimeline', () => ({ TriggerTimeline: () => null }));
vi.mock('../components/mortgage/BorrowerProofDrawer', () => ({ BorrowerProofDrawer: () => null }));
vi.mock('../components/mortgage/BorrowerTruthFlags', () => ({ BorrowerTruthFlags: () => null }));
vi.mock('../components/mortgage/TopLeadsQuickPick', () => ({ TopLeadsQuickPick: () => null }));
vi.mock('../components/mortgage/ScoreBadge', () => ({ ScoreBadge: () => null }));
vi.mock('../components/mortgage/ConfidenceMeter', () => ({ ConfidenceMeter: () => null }));
vi.mock('../components/fx/Reveal', () => ({
  Reveal: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));
vi.mock('../components/GlossaryTerm', () => ({
  GlossaryTerm: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));
vi.mock('../components/Primitives', async (orig) => {
  const actual = await orig<typeof import('../components/Primitives')>();
  return {
    ...actual,
    EvidenceChip: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
  };
});

import Borrower360 from './borrower-360';

const BORROWER_ID = 'B-1ABCDEFGHIJK2';

function borrower(overrides: Partial<Borrower360Type> = {}): Borrower360Type {
  return {
    borrower_id: BORROWER_ID,
    city: 'Chicago', state: 'IL', zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 285_000,
    rate_spread_bps: 88,
    opportunity_score: 82,
    confidence: 80,
    recommended_offer: 'Refinance',
    recommended_offer_code: 'refi',
    why_now: 'Rate spread clears the bar.',
    is_investor: false,
    related_property_count: 1,
    clip_id: 'clip_demo_48291',
    owner_link_id: 'ol_demo_48291',
    subject_property: 'Chicago, IL 60611',
    avm_value: 625_000,
    current_lien_balance: 340_000,
    current_rate: 5.75,
    ltv: 54,
    ltv_basis_is_unreliable: false,
    trigger_timeline: [],
    evidence_events: [],
    why_panel: {
      in_the_money: true,
      in_the_money_reason: 'Spread and equity both clear the screen.',
      rate_spread_bps: 88,
      market_rate: 0.0487,
      sources: [],
    },
    ...overrides,
  } as unknown as Borrower360Type;
}

describe('Borrower 360 LTV / Equity field', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function mount(b: Borrower360Type) {
    state.borrower = b;
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[`/borrower-360/${BORROWER_ID}`]}>
          <Routes>
            <Route path="/borrower-360/:id" element={<Borrower360 />} />
          </Routes>
        </MemoryRouter>,
      );
    });
  }

  /** The dossier field whose label reads "LTV / Equity". */
  function ltvField(): HTMLElement {
    const label = Array.from(container.querySelectorAll<HTMLElement>('.field__label'))
      .find((el) => el.textContent?.replace(/\s+/g, ' ').trim() === 'LTV / Equity');
    expect(label).toBeTruthy();
    return label!.parentElement as HTMLElement;
  }

  it('renders the reported LTV beside the equity estimate', () => {
    mount(borrower());
    expect(ltvField().querySelector('.field__value')?.textContent).toBe('54% · $285,000');
    expect(ltvField().textContent).not.toContain('LTV basis unreliable');
  });

  it('never prints "null%": a withheld LTV is an em dash with an accessible label', () => {
    // Exactly what the API sends for an unreliable basis: ltv None, flag True,
    // and gold's floored equity_estimate of 0.
    mount(borrower({ ltv: null, ltv_basis_is_unreliable: true, equity_estimate: 0 }));
    const field = ltvField();
    expect(container.textContent).not.toContain('null');
    const value = field.querySelector('.field__value');
    expect(value?.querySelector('[aria-hidden="true"]')?.textContent).toBe('—');
    expect(value?.querySelector('.sr-only')?.textContent).toBe('LTV and equity unknown');
    // "$0" beside a withheld ratio would read as no equity / free and clear.
    expect(field.textContent).not.toContain('$0');
  });

  it('surfaces the unreliable-basis caveat next to the figure, in chip + tooltip vocabulary', () => {
    mount(borrower({ ltv: null, ltv_basis_is_unreliable: true, equity_estimate: 0 }));
    const chip = ltvField().querySelector('.chip.chip--warning.chip--compact');
    expect(chip?.textContent).toBe('LTV basis unreliable');
    expect(chip?.getAttribute('title')).toContain('no trustworthy basis');
    expect(ltvField().textContent).toContain('Withheld, not zero');
    // The raw facts the API keeps publishing stay on the dossier.
    expect(container.textContent).toContain('$625,000');
    expect(container.textContent).toContain('$340,000');
  });

  it('treats a flagged basis as withheld even if a number rides along', () => {
    mount(borrower({ ltv: 0, ltv_basis_is_unreliable: true, equity_estimate: 0 }));
    expect(ltvField().querySelector('.field__value')?.textContent).toBe('—LTV and equity unknown');
    expect(ltvField().textContent).not.toContain('0%');
  });

  it('renders an unknown without the caveat when LTV is simply absent', () => {
    mount(borrower({ ltv: null, ltv_basis_is_unreliable: false }));
    const field = ltvField();
    expect(container.textContent).not.toContain('null');
    expect(field.querySelector('.chip--warning')).toBeNull();
    expect(field.textContent).toContain('LTV was not reported');
  });

  it('keeps the no-AVM state: unknown, and explicitly not a zero-equity signal', () => {
    mount(borrower({ avm_value: 0, ltv: 0, equity_estimate: 0 }));
    const field = ltvField();
    expect(field.querySelector('.field__value [aria-hidden="true"]')?.textContent).toBe('—');
    expect(field.textContent).toContain('Not a zero-equity signal; AVM was unavailable.');
    expect(field.textContent).not.toContain('0%');
  });
});
