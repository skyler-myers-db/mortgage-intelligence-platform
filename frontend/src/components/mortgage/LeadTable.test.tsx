import { describe, expect, it } from 'vitest';
import {
  buildLeadCsv,
  computeLeadVirtualRange,
  isEditableTarget,
  isLeadApprovalEligible,
  isLeadSelectableForSalesOps,
} from './LeadTable';
import type { LeadSummary } from '../../types';

/**
 * LeadTable hotkey-bailout guard (R5-12, 2026-04-23).
 *
 * The window-level keydown handler must NEVER swallow letters typed
 * into text inputs, textareas, selects, or contenteditable regions —
 * otherwise typing "a" into the Genie chat fires the approve hotkey.
 *
 * The runtime DOM listener lives inside a `useEffect` closure so we
 * can't unit-test it in a node environment. Instead the helper is
 * factored out as `isEditableTarget` and covered exhaustively here;
 * the handler itself also checks `document.activeElement` through
 * the same helper as a belt-and-suspenders defense.
 *
 * This matters because vitest runs under `environment: 'node'` — we
 * can't mount the component and drive real keyboard events. Covering
 * the pure predicate keeps the guard from regressing silently.
 */

describe('isEditableTarget (LeadTable hotkey bailout)', () => {
  const fakeElement = (tag: string, contentEditable = false): Element => {
    return {
      tagName: tag,
      isContentEditable: contentEditable,
    } as unknown as Element;
  };

  it('returns false for null / undefined (no focused element)', () => {
    expect(isEditableTarget(null)).toBe(false);
    expect(isEditableTarget(undefined)).toBe(false);
  });

  it('returns true for INPUT, TEXTAREA, SELECT', () => {
    expect(isEditableTarget(fakeElement('INPUT'))).toBe(true);
    expect(isEditableTarget(fakeElement('TEXTAREA'))).toBe(true);
    expect(isEditableTarget(fakeElement('SELECT'))).toBe(true);
  });

  it('returns true for contenteditable DIV (Genie rich-text fallback)', () => {
    expect(isEditableTarget(fakeElement('DIV', true))).toBe(true);
  });

  it('returns false for BODY, DIV (not contenteditable), BUTTON, TR', () => {
    expect(isEditableTarget(fakeElement('BODY'))).toBe(false);
    expect(isEditableTarget(fakeElement('DIV', false))).toBe(false);
    expect(isEditableTarget(fakeElement('BUTTON'))).toBe(false);
    expect(isEditableTarget(fakeElement('TR'))).toBe(false);
  });
});

/**
 * useRef in-flight latch (R5-04, 2026-04-23).
 *
 * The component uses `useRef<boolean>` for the approve/bulk-approve
 * in-flight latch because `setState` is async — two clicks in the
 * same frame both see `bulkApproving=false` and spawn parallel
 * loops, each writing an audit row per borrower.
 *
 * The ref is a primitive boolean — the test below pins the
 * invariant: a synchronous flip-and-check gate excludes a second
 * caller, and reset on completion allows a later retry.
 */

function makeLatch(): {
  tryEnter: () => boolean;
  release: () => void;
  snapshot: () => boolean;
} {
  const ref = { current: false };
  return {
    tryEnter: () => {
      if (ref.current) return false;
      ref.current = true;
      return true;
    },
    release: () => {
      ref.current = false;
    },
    snapshot: () => ref.current,
  };
}

describe('synchronous in-flight latch shape', () => {
  it('lets the first caller enter and blocks a concurrent caller', () => {
    const latch = makeLatch();
    expect(latch.tryEnter()).toBe(true);
    expect(latch.tryEnter()).toBe(false);
    expect(latch.snapshot()).toBe(true);
  });

  it('releases cleanly so a retry after completion is allowed', () => {
    const latch = makeLatch();
    expect(latch.tryEnter()).toBe(true);
    latch.release();
    expect(latch.snapshot()).toBe(false);
    expect(latch.tryEnter()).toBe(true);
  });
});

describe('Sales Manager approval-state guards', () => {
  it('keeps hold rows out of approval and sales-work selections', () => {
    expect(isLeadApprovalEligible('hold')).toBe(false);
    expect(isLeadSelectableForSalesOps('hold')).toBe(false);
  });

  it('allows approved rows to be assigned but not approved again', () => {
    expect(isLeadApprovalEligible('approved')).toBe(false);
    expect(isLeadSelectableForSalesOps('approved')).toBe(true);
  });

  it('keeps rejected rows locked out of sales work', () => {
    expect(isLeadApprovalEligible('rejected')).toBe(false);
    expect(isLeadSelectableForSalesOps('rejected')).toBe(false);
  });
});

describe('computeLeadVirtualRange', () => {
  it('returns the full range when virtualization is disabled', () => {
    expect(computeLeadVirtualRange(80, 500, 400, false)).toEqual({
      start: 0,
      end: 80,
      top: 0,
      bottom: 0,
    });
  });

  it('windows a large table with overscan and spacer heights', () => {
    const range = computeLeadVirtualRange(500, 860, 430, true, 86, 4);
    expect(range.start).toBe(6);
    expect(range.end).toBe(19);
    expect(range.top).toBe(516);
    expect(range.bottom).toBe(41366);
  });

  it('clamps the bottom range instead of over-rendering past the dataset', () => {
    const range = computeLeadVirtualRange(500, 42_600, 430, true, 86, 4);
    expect(range.end).toBe(500);
    expect(range.bottom).toBe(0);
  });
});

const sampleLead = (overrides: Partial<LeadSummary> = {}): LeadSummary => ({
  borrower_id: 'B-TEST1',
  display_name: 'Owner masked',
  city: 'Chicago',
  state: 'IL',
  zip: '60617',
  clip: 'clip_ref_public',
  segment_codes: ['itm', 'equity'],
  equity_estimate: 350000,
  rate_spread_bps: 325,
  opportunity_score: 82,
  confidence: 77,
  recommended_offer: 'Refinance + HELOC',
  why_now: 'Market rate comparison',
  evidence_ids: ['EV1'],
  approval_status: 'pending',
  is_owner_occupied: true,
  is_investor: false,
  is_current_customer: false,
  is_former_customer: true,
  is_competitor_lien: true,
  current_lender_ref: 'Competitor A',
  current_lien_balance: 120000,
  second_pos_amount: 0,
  has_permit: false,
  listed_for_sale: false,
  related_property_count: 1,
  marketing_eligible: true,
  consent_status: 'opt_in',
  suppression_reason: null,
  last_touch_at: null,
  eligible_recontact_at: null,
  ...overrides,
});

describe('buildLeadCsv', () => {
  it('exports context comments and public truth-flag columns', () => {
    const csv = buildLeadCsv(
      [sampleLead()],
      { 'B-TEST1': 'approved' },
      {
        generatedAt: '2026-05-10T12:00:00.000Z',
        filters: 'states=IL&product=Cash-out',
        refreshedAt: '2026-05-09T21:54:00Z',
        rulesVersion: 'rules.itm_abc123',
      },
    );

    expect(csv).toContain('# generated_at=2026-05-10T12:00:00.000Z');
    expect(csv).toContain('# filters=states=IL&product=Cash-out');
    expect(csv).toContain('# refreshed_at=2026-05-09T21:54:00Z');
    expect(csv).toContain('# rules_version=rules.itm_abc123');
    expect(csv).toContain(
      'approval_status,outreach_status,approved_at,outreach_at,assigned_to_email,assigned_at,latest_disposition_outcome',
    );
    expect(csv).toContain(
      'aging_days,is_owner_occupied,is_investor,is_current_customer,is_former_customer,is_competitor_lien,current_lender_ref',
    );
    expect(csv).toContain('approved,none,,,,,,,,,true,false,false,true,true,Competitor A');
    expect(csv).not.toContain('owner_name');
    expect(csv).not.toContain('street_address');
  });

  it('escapes comma quote newline and formula-leading values', () => {
    const csv = buildLeadCsv([
      sampleLead({
        borrower_id: 'B-TEST2',
        city: 'North, "West"\nSide',
        current_lender_ref: '=unsafe',
      }),
    ], {}, { generatedAt: '2026-05-10T12:00:00.000Z' });

    expect(csv).toContain('"North, ""West""\nSide"');
    expect(csv).toContain(",'=unsafe,");
  });
});
