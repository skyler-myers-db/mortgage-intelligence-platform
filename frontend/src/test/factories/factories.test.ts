/**
 * The src/test factories (audit quality-04 D-api-types-a3 P3, W5e
 * w5-wire-types B3): least-privileged and pending by default, synthetic in
 * every identity-shaped field, and complete (every key of the wire base).
 */
import { describe, expect, it } from 'vitest';
import {
  APPROVAL_REQUEST_ROW_BASE,
  BORROWER_360_BASE,
  LEAD_SUMMARY_BASE,
  SESSION_BASE,
} from '../../../tests/e2e/fixture/data/wireBases';
import { makeApprovalRequestRow, makeApproveResult, makeRejectResult } from './approvals';
import { makeAuditRollupResponse } from './audit';
import { makeGrowthAgentRunResponse } from './growthAgent';
import { makeBorrower360, makeLeadSummary } from './leads';
import { makeSessionResponse } from './session';

const MASKED_ID = /^B-[0-9A-Z]{13}$/;
const DISPLAY_NAME = /^Owner [0-9A-Z]{6}$/;
const SYNTHETIC_EMAIL = /@summit\.example$/;
const ACTOR_KEY = /^actor_[a-z]{16}$/;

/** Every string anywhere in a value, with its dotted path. */
function strings(value: unknown, path = ''): Array<[string, string]> {
  if (typeof value === 'string') return [[path, value]];
  if (Array.isArray(value)) return value.flatMap((item, index) => strings(item, `${path}[${index}]`));
  if (value && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, item]) => strings(item, path ? `${path}.${key}` : key));
  }
  return [];
}

describe('factories: least-privileged and pending defaults', () => {
  it('a session can do nothing privileged unless the test says so, even over a privileged base', () => {
    expect([SESSION_BASE.can_approve, SESSION_BASE.can_access_admin, SESSION_BASE.can_read_audit]).toEqual([true, true, true]);
    const session = makeSessionResponse();

    expect(session.can_approve).toBe(false);
    expect(session.can_access_admin).toBe(false);
    expect(session.can_read_audit).toBe(false);
    expect(session.presenter_mode).toBe(false);
    expect(makeSessionResponse({ can_approve: true }).can_approve).toBe(true);
  });

  it('leads and dossiers are pending and undecided', () => {
    for (const row of [makeLeadSummary(), makeBorrower360()]) {
      expect(row.approval_status).toBe('pending');
      expect(row.approved_at).toBeNull();
    }
    expect(makeLeadSummary({ approval_status: 'approved' }).approval_status).toBe('approved');
  });

  it('a decision result records nothing by default', () => {
    expect(makeApproveResult().approved).toBe(false);
    expect(makeRejectResult().rejected).toBe(false);
    expect(makeApprovalRequestRow()).toMatchObject({ state: 'open', approval_id: null });
    expect(makeGrowthAgentRunResponse()).toMatchObject({ monitor: null, audit_event_id: null });
  });

  it('keeps every key of its wire base (complete, not partial)', () => {
    expect(Object.keys(makeLeadSummary()).sort()).toEqual(Object.keys(LEAD_SUMMARY_BASE).sort());
    expect(Object.keys(makeBorrower360()).sort()).toEqual(Object.keys(BORROWER_360_BASE).sort());
    expect(Object.keys(makeApprovalRequestRow()).sort()).toEqual(Object.keys(APPROVAL_REQUEST_ROW_BASE).sort());
    expect(Object.keys(makeAuditRollupResponse()).length).toBeGreaterThan(0);
  });
});

describe('factories: synthetic shapes only', () => {
  it('uses masked borrower ids, demo CLIP / Owner Link keys and Owner display names', () => {
    for (const row of [makeLeadSummary(), makeBorrower360(), makeApprovalRequestRow()]) {
      expect(row.borrower_id).toMatch(MASKED_ID);
    }
    for (const row of [makeLeadSummary(), makeBorrower360()]) {
      expect(row.display_name).toMatch(DISPLAY_NAME);
      expect(row.clip).toMatch(/^clip_demo_/);
    }
    const ownerLinks = strings(makeBorrower360()).filter(([path]) => /(^|\.)owner_link(_id)?$/.test(path));
    expect(ownerLinks.length).toBeGreaterThan(0);
    for (const [path, value] of ownerLinks) expect(value, path).toMatch(/^ol_demo_/);
  });

  it('names only synthetic identities, the sample lender and a FIXTURE_DEFAULT_ACTOR-shaped key', () => {
    const session = makeSessionResponse();

    expect(session.actor_email).toMatch(SYNTHETIC_EMAIL);
    expect(session.lender_name).toBe('Summit Mortgage');
    expect(session.actor_cache_key).toMatch(ACTOR_KEY);
    const everything = [makeLeadSummary(), makeBorrower360(), session, makeApproveResult(), makeRejectResult(), makeGrowthAgentRunResponse(), makeAuditRollupResponse()];
    for (const [path, value] of everything.flatMap((body) => strings(body))) {
      if (value.includes('@')) expect(value, path).toMatch(SYNTHETIC_EMAIL);
      if (/^B-/.test(value)) expect(value, path).toMatch(MASKED_ID);
      expect(value, path).not.toMatch(/sk-|ghp_|AKIA|BEGIN [A-Z ]*PRIVATE KEY/);
    }
  });
});
